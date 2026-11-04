"""Opt-in source-only direct insertion into ONE newly authored 4x2 TOY.
Never a general cell API, approval writer or production schema4 qualification.
"""

import argparse
import importlib
import ctypes
from ctypes import wintypes
from datetime import datetime, timezone
import gc
import json
from pathlib import Path
import secrets
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
import hwp_review as r
import hwp_fill_plan as fill
import hwp_form_draft as form
from hwp_security import register_security_module, registered_security_module
import native_cell_position_probe as cell
import native_fill_metadata_probe as fixture
import native_fill_smoke as full
from native_blank_metadata_probe import hwp_inventory

VALUES = {"Name": "TOY DIRECT NAME", "Code": "7", "Date": "2026-10-10"}


def require_binding(source, proof):
    if (
        proof.get("status") != "metadata_observed_not_writer_qualified"
        or proof.get("owned_process_exit_confirmed") is not True
        or proof.get("source_reopened") is not True
        or proof.get("anchor_navigation", {}).get("all_authored_positions_match")
        is not True
    ):
        raise ValueError("Current own-source metadata proof is incomplete")
    if r.file_hash(source) != proof["reopened_observation"]["source_sha256"]:
        raise ValueError("Own source hash differs from its recorded grid proof")
    grid = proof["authored_grid_before_source_save"]
    if (
        set(grid) != {f"{row},{col}" for row in range(4) for col in range(2)}
        or len({tuple(cell.position(pos)) for pos in grid.values()}) != 8
    ):
        raise ValueError("Own authored grid is not complete and unique")
    return grid


def require_unselected_caret(app, pos):
    return importlib.import_module("hwp_native_buffer").require_unselected_caret(
        app, pos
    )


def insert_own_values(app, source, grid, selected, evidence):
    """Known-source diagnostic only; structural/render gates must still follow."""
    if len(selected) != 3 or {
        tuple(sorted(item["cell_address"].items())) for item in selected
    } != {
        tuple(sorted({"rowAddr": str(row), "colAddr": "1"}.items()))
        for row in (1, 2, 3)
    }:
        raise ValueError("Only the three explicitly authored empty cells are permitted")
    if not cell.own_saved_document(app, source):
        raise ValueError("Own original source identity unavailable before insertion")
    original_sha = r.file_hash(source)
    expected = app.GetTextFile("TEXT", "")
    if not isinstance(expected, str) or expected.count(fixture.MARKER) != 1:
        raise ValueError("Own original text snapshot unavailable")
    for item in selected:
        address = item["cell_address"]
        key = address["rowAddr"] + "," + address["colAddr"]
        pos = cell.position(grid[key])
        value = item["new_text"]
        if not isinstance(value, str) or not value or expected.count(value):
            raise ValueError("Test value is empty or already occurs in source")
        doc = app.XHwpDocuments.Item(0)
        if (
            app.XHwpDocuments.Count != 1
            or Path(doc.FullName).resolve() != source.resolve()
            or r.file_hash(source) != original_sha
            or app.GetTextFile("TEXT", "") != expected
        ):
            raise ValueError("Own source/buffer changed before direct insertion")
        proof, expected = importlib.import_module(
            "hwp_native_buffer"
        ).insert_bound_empty_cell(app, item, pos, expected)
        evidence.append({**proof, "authored_cell_address": key})
    return expected


def export_direct(source, proof, selected, output, report):
    import pythoncom
    import win32com.client
    from win32com.client import gencache

    grid = require_binding(source, proof)
    if hwp_inventory() != []:
        raise RuntimeError("Existing Hwp state prevents direct diagnostic")
    pythoncom.CoInitialize()
    app = None
    window = None
    owned = False
    kernel = None
    handle = None
    try:
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
            raise RuntimeError("Owned direct writer exit monitor unavailable")
        register_security_module(app)
        if not app.Open(str(source.resolve()), "HWP", "versionwarning:false"):
            raise RuntimeError("Own HWP reopen failed")
        observed = cell.observe_saved(
            app, source, {key: grid[key] for key in ("1,1", "2,1", "3,1")}
        )
        if (
            observed["source_text_sha256"]
            != proof["reopened_observation"]["source_text_sha256"]
        ):
            raise ValueError("Source text differs from authoring proof")
        report["prewrite_anchor_navigation"] = {}
        cell.observe_anchor_walk(
            app,
            source,
            grid,
            observed["one_table"]["anchor"],
            report["prewrite_anchor_navigation"],
        )
        report["stage"] = "read_only_cell_content_binding"
        native_cells = importlib.import_module("hwp_native_cells")

        report["cell_content_binding"] = native_cells.capture(
            app,
            source,
            proof["reopened_observation"]["source_sha256"],
            output / "review/baseline.hwpx",
            selected,
        )
        if any(
            item["position"] != grid[str(item["row"]) + "," + str(item["col"])]
            for item in report["cell_content_binding"]["cells"]
        ):
            raise ValueError(
                "Content-verified Native map differs from own authoring evidence"
            )
        report["stage"] = "direct_test_insertions"
        report["insertions"] = []
        expected = insert_own_values(app, source, grid, selected, report["insertions"])
        report["stage"] = "native_candidate_exports"
        report["candidate_native_pages"] = fixture.native_pages(app)
        for name, fmt in (
            ("TOY-ONLY-filled.hwp", "HWP"),
            ("TOY-ONLY-native-filled.hwpx", "HWPX"),
            ("TOY-ONLY-buffer.pdf", "PDF"),
        ):
            target = output / name
            if (
                target.exists()
                or not app.SaveAs(str(target.resolve()), fmt, "")
                or not target.is_file()
                or target.stat().st_size == 0
            ):
                raise RuntimeError("Direct candidate export failed: " + fmt)
            if app.GetTextFile("TEXT", "") != expected:
                raise ValueError("Direct buffer changed during candidate export")
        report["input_hwpx_imported"] = False
    finally:
        try:
            if app is not None and owned:
                try:
                    app.Clear(1)
                finally:
                    app.Quit()
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
                report["after_direct_hwp_processes"] = hwp_inventory()
                report["direct_owned_exit_confirmed"] = exited and not any(
                    p["pid"] == report["owned_hwp_pid"]
                    and p["creation_ms"] == report["owned_process_creation_ms"]
                    for p in report["after_direct_hwp_processes"]
                )
                report["cleanup"] = (
                    "owned_exit_confirmed"
                    if report["direct_owned_exit_confirmed"]
                    else "owned_exit_unconfirmed_owner_inspection_required"
                )
                if not report["direct_owned_exit_confirmed"]:
                    raise RuntimeError(
                        "Direct owned process exit unconfirmed; no follow-up"
                    )


def run(output, *, run_native=False, toy_only=False):
    if run_native is not True or toy_only is not True or getattr(sys, "frozen", False):
        raise ValueError("Explicit SOURCE-only --run-native-test --toy-only required")
    folder = fixture.paths.real_path(output)
    if (
        folder.parent != ROOT / "work"
        or not folder.name.startswith("toy-direct-fill-")
        or folder.exists()
    ):
        raise ValueError("Only a fresh direct owned work/toy-direct-fill-* folder")
    if hwp_inventory() != []:
        raise RuntimeError("Existing Hwp process; no direct trial start")
    registered_security_module()
    folder.mkdir()
    report = {
        "kind": "own-grid-direct-native-fill-diagnostic",
        "status": "blocked",
        "stage": "source_creation",
        "qualification": False,
        "general_template_binding_proven": False,
        "actual_report_used": False,
        "approval_record_used": False,
        "automatic_retries": 0,
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
        source = source_dir / "TOY-ONLY-source.hwp"
        require_binding(source, proof)
        source_sha = r.file_hash(source)
        report["stage"] = "no_edit_baseline"
        workspace = folder / "review"
        r.prepare(source, workspace)
        manifest = r.verify_baseline(workspace)
        if len(manifest["pages"]) != 1:
            raise ValueError("Own baseline is not one page; no adjustment")
        targets = full.own_targets(
            form.inspect_template(workspace / "baseline.hwpx"),
            fill.empty_regions(workspace / "baseline.pdf"),
        )
        regions = fill.empty_regions(workspace / "baseline.pdf")
        bindings = {
            "schema": "hwp-fill-bindings/v1",
            "fields": [
                {
                    "field_id": item["field_id"],
                    "key": key,
                    "kind": kind,
                    "required": True,
                    "max_utf16_units": 30,
                    "max_lines": 1,
                }
                for item, key, kind in zip(targets, VALUES, ("text", "integer", "date"))
            ],
        }
        record = {
            "values": VALUES,
            "sources": {key: "FICTIONAL DIRECT TEST ONLY" for key in VALUES},
        }
        placements = [
            {"field_id": item["field_id"], **region}
            for item, region in zip(targets, regions)
        ]
        plan = fill.proposal(workspace, bindings, record, placements)
        selected = fill.validate(plan, manifest, workspace / "baseline.hwpx")
        fixture.require_source_cache(workspace / "baseline.hwpx", selected)
        r.write_json(workspace / "proposal-fill.json", plan)
        report["stage"] = "direct_original_hwp_edit"
        export_direct(source, proof, selected, folder, report)
        hwp = folder / "TOY-ONLY-filled.hwp"
        hwpx = folder / "TOY-ONLY-native-filled.hwpx"
        pdf = folder / "TOY-ONLY-reopened.pdf"
        reopened = folder / "TOY-ONLY-reopened.hwpx"
        report["stage"] = "initial_structural_gate"
        report["structural_gate"] = fill.structural_gate(
            workspace / "baseline.hwpx", hwpx, selected, plan["header_spine"]
        )
        original_outputs = {
            p.name: r.file_hash(p) for p in (hwp, hwpx, folder / "TOY-ONLY-buffer.pdf")
        }
        report["stage"] = "saved_hwp_reopen"
        r.native_export(hwp, pdf, hwpx=reopened)
        report["stage"] = "saved_hwp_structural_gate"
        report["saved_hwp_structural_gate"] = fill.structural_gate(
            workspace / "baseline.hwpx", reopened, selected, plan["header_spine"]
        )
        report["stage"] = "rendered_and_original_gates"
        report["layout_gate"] = r.compare_pdfs(
            workspace / "baseline.pdf",
            pdf,
            {1: [item["region"] for item in placements]},
        )
        report["rendered_page_text_exact"] = fill.rendered_text_gate(
            workspace / "baseline.pdf", pdf, selected
        )
        report["embedded_binaries_equal"] = (
            r.hwp_audit(source)["embedded_binary_hashes"]
            == r.hwp_audit(hwp)["embedded_binary_hashes"]
        )
        report["source_unchanged"] = r.file_hash(source) == source_sha
        report["initial_outputs_unchanged"] = all(
            r.file_hash(folder / name) == sha for name, sha in original_outputs.items()
        )
        report["baseline_verified_again"] = (
            r.verify_baseline(workspace)["workspace_id"] == manifest["workspace_id"]
        )
        report["owned_processes_stopped"] = hwp_inventory() == []
        if (
            not all(
                (
                    report["layout_gate"]["passed"],
                    report["rendered_page_text_exact"],
                    report["embedded_binaries_equal"],
                    report["source_unchanged"],
                    report["initial_outputs_unchanged"],
                    report["baseline_verified_again"],
                    report["owned_processes_stopped"],
                )
            )
            or (workspace / "approved-plan.json").exists()
        ):
            raise ValueError("Direct trial failed strict gates; no retry or repair")
        report.update(
            status="checks_passed_needs_human_visual_review", stage="checks_passed"
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
        with (folder / "DIRECT-FILL-RESULT.json").open("x", encoding="utf-8") as stream:
            json.dump(report, stream, ensure_ascii=False, indent=2)
    return report


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run-native-test", action="store_true", required=True)
    parser.add_argument("--toy-only", action="store_true", required=True)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    target = args.output or ROOT / "work" / (
        "toy-direct-fill-"
        + datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        + "-"
        + secrets.token_hex(3)
    )
    result = run(target, run_native=args.run_native_test, toy_only=args.toy_only)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    sys.exit(1 if result["status"] == "blocked" else 0)
