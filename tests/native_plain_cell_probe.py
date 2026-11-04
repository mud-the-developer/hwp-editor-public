"""ONE opt-in owned TOY current-list scan. No insertion or candidate export path."""

import argparse
import ctypes
from ctypes import wintypes
from datetime import datetime, timezone
import gc
import importlib
import json
from pathlib import Path
import secrets
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
r = importlib.import_module("hwp_review")
mapper = importlib.import_module("hwp_native_cells")
form = importlib.import_module("hwp_form_draft")
cell = importlib.import_module("native_cell_position_probe")
fixture = importlib.import_module("native_fill_metadata_probe")
full = importlib.import_module("native_fill_smoke")
from native_blank_metadata_probe import hwp_inventory
from hwp_security import registered_security_module, register_security_module


class ScanAudit:
    """Own-TOY instrumentation only; delegates the mapper's read-only operations."""

    def __init__(self, app, events):
        self.app = app
        self.events = events

    def __getattr__(self, name):
        return getattr(self.app, name)

    def InitScan(self, *args):
        result = self.app.InitScan(*args)
        self.events.append(
            {"api": "InitScan", "args": list(args), "return": cell.scalar(result)}
        )
        return result

    def GetText(self):
        result = self.app.GetText()
        if (
            isinstance(result, (tuple, list))
            and len(result) == 2
            and isinstance(result[1], str)
        ):
            import hashlib

            self.events.append(
                {
                    "api": "GetText",
                    "state": cell.scalar(result[0]),
                    "return_type": type(result).__name__,
                    "text_units": len(result[1].encode("utf-16-le")) // 2,
                    "text_sha256": hashlib.sha256(
                        result[1].encode("utf-8")
                    ).hexdigest(),
                }
            )
        else:
            self.events.append({"api": "GetText", "return_type": type(result).__name__})
        return result

    def ReleaseScan(self):
        result = self.app.ReleaseScan()
        self.events.append({"api": "ReleaseScan", "return": cell.scalar(result)})
        return result


def observe(source, baseline, selected, report):
    import pythoncom
    import win32com.client
    from win32com.client import gencache

    if hwp_inventory() != []:
        raise RuntimeError("Existing Hwp process; no scan start")
    pythoncom.CoInitialize()
    app = None
    window = None
    owned = False
    handle = None
    kernel = None
    try:
        report["stage"] = "dispatch_owned_scan_instance"
        started = int(datetime.now(timezone.utc).timestamp() * 1000)
        app = gencache.EnsureDispatch(win32com.client.DispatchEx("HWPFrame.HwpObject"))
        window = fixture.verify_owned_blank(app, [], started, report)
        owned = True
        window.Visible = False
        kernel = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
        kernel.OpenProcess.restype = wintypes.HANDLE
        kernel.WaitForSingleObject.argtypes = [wintypes.HANDLE, wintypes.DWORD]
        kernel.WaitForSingleObject.restype = wintypes.DWORD
        kernel.CloseHandle.argtypes = [wintypes.HANDLE]
        kernel.CloseHandle.restype = wintypes.BOOL
        handle = kernel.OpenProcess(0x00100000, False, report["owned_hwp_pid"])
        if not handle:
            raise RuntimeError("Owned scan exit monitor unavailable")
        register_security_module(app)
        if not app.Open(str(source.resolve()), "HWP", "versionwarning:false"):
            raise RuntimeError("Own scan source Open failed")
        report["stage"] = "readonly_native_cell_scan"
        report["scan_events"] = []
        report["binding"] = mapper.capture(
            ScanAudit(app, report["scan_events"]),
            source,
            r.file_hash(source),
            baseline,
            selected,
        )
        report["status"] = "readonly_scan_observed_not_writer_qualified"
    finally:
        try:
            if app is not None and owned:
                if cell.own_saved_document(app, source):
                    try:
                        app.Clear(1)
                    finally:
                        app.Quit()
                else:
                    report.update(
                        status="blocked",
                        cleanup="source_identity_unknown_owner_inspection_required",
                    )
        finally:
            window = None
            app = None
            gc.collect()
            pythoncom.CoUninitialize()
            if owned:
                exited = False
                if handle and kernel is not None:
                    try:
                        exited = kernel.WaitForSingleObject(handle, 5000) == 0
                    finally:
                        kernel.CloseHandle(handle)
                report["after_scan_hwp_processes"] = hwp_inventory()
                report["owned_scan_exit_confirmed"] = exited and not any(
                    p["pid"] == report["owned_hwp_pid"]
                    and p["creation_ms"] == report["owned_process_creation_ms"]
                    for p in report["after_scan_hwp_processes"]
                )
                if not report["owned_scan_exit_confirmed"]:
                    raise RuntimeError("Owned scan process exit unconfirmed; no retry")


def run(output, *, run_native=False, readonly_scan=False):
    if (
        run_native is not True
        or readonly_scan is not True
        or getattr(sys, "frozen", False)
    ):
        raise ValueError(
            "Explicit SOURCE-only --run-native-test --readonly-scan required"
        )
    folder = fixture.paths.real_path(output)
    if (
        folder.parent != ROOT / "work"
        or not folder.name.startswith("toy-cell-scan-")
        or folder.exists()
    ):
        raise ValueError("Only a fresh direct owned work/toy-cell-scan-* folder")
    if hwp_inventory() != []:
        raise RuntimeError("Existing Hwp process; no scan calibration")
    registered_security_module()
    folder.mkdir()
    report = {
        "kind": "owned-TOY-readonly-cell-scan",
        "status": "blocked",
        "stage": "source_creation",
        "qualification": False,
        "field_values_inserted": 0,
        "candidate_exports": 0,
        "automatic_retries": 0,
        "actual_report_used": False,
        "approval_record_used": False,
        "owner_nonce": secrets.token_hex(16),
    }
    with (folder / "OWNER-TEST-ONLY.json").open("x", encoding="utf-8") as stream:
        json.dump(
            {
                "test_only": True,
                "nonce": report["owner_nonce"],
                "purpose": report["kind"],
            },
            stream,
            indent=2,
        )
    try:
        source_dir = folder / "source"
        source_dir.mkdir()
        proof = {}
        cell.native_once(source_dir, proof)
        report["creation_metadata"] = proof
        if (
            proof.get("status") != "metadata_observed_not_writer_qualified"
            or proof.get("owned_process_exit_confirmed") is not True
        ):
            raise ValueError("Own source creation/cleanup failed; no scan")
        source = source_dir / "TOY-ONLY-source.hwp"
        source_sha = r.file_hash(source)
        report["stage"] = "no_edit_baseline_preparation"
        workspace = folder / "review"
        r.prepare(source, workspace)
        manifest = r.verify_baseline(workspace)
        baseline = workspace / "baseline.hwpx"
        regions = r.hwp_fill_plan.empty_regions(workspace / "baseline.pdf")
        selected = full.own_targets(form.inspect_template(baseline), regions)
        report["profile"] = mapper.plain_profile(baseline, selected)
        observe(source, baseline, selected, report)
        report["source_unchanged"] = r.file_hash(source) == source_sha
        report["baseline_verified_again"] = (
            r.verify_baseline(workspace)["workspace_id"] == manifest["workspace_id"]
        )
        report["hwp_processes_stopped"] = hwp_inventory() == []
        if not (
            report["source_unchanged"]
            and report["baseline_verified_again"]
            and report["hwp_processes_stopped"]
        ):
            raise ValueError("Read-only source/baseline/process invariants failed")
        for item in report["binding"]["cells"]:
            if (
                item["position"]
                != proof["authored_grid_before_source_save"][
                    str(item["row"]) + "," + str(item["col"])
                ]
            ):
                raise ValueError(
                    "Scanned position differs from independent own source authoring evidence"
                )
    except BaseException as error:
        report.update(
            status="blocked", error_type=type(error).__name__, error=str(error)[:1000]
        )
    finally:
        report["files_sha256"] = {
            str(p.relative_to(folder)): r.file_hash(p)
            for p in folder.rglob("*")
            if p.is_file()
        }
        with (folder / "CELL-SCAN-RESULT.json").open("x", encoding="utf-8") as stream:
            json.dump(report, stream, ensure_ascii=False, indent=2)
    return report


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run-native-test", action="store_true", required=True)
    parser.add_argument("--readonly-scan", action="store_true", required=True)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    target = args.output or ROOT / "work" / (
        "toy-cell-scan-"
        + datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        + "-"
        + secrets.token_hex(3)
    )
    result = run(
        target, run_native=args.run_native_test, readonly_scan=args.readonly_scan
    )
    print(json.dumps(result, ensure_ascii=False, indent=2))
    sys.exit(1 if result["status"] == "blocked" else 0)
