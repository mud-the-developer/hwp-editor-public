"""OPT-IN Stage A only: NEW owned unsigned HWPX authoring/import/re-export.

No HWP/PDF SaveAs, no real report/approval/apply, no policy or failed evidence repair.
A missing precondition or any failure stops this one lab; NEVER retries/continues.
"""

import argparse
import ctypes
from ctypes import wintypes
import gc
from datetime import datetime, timezone
import importlib
import json
import os
from pathlib import Path
import secrets
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
from native_blank_metadata_probe import hwp_inventory

r = importlib.import_module("hwp_review")
f = importlib.import_module("hwp_form_draft")
fill = importlib.import_module("hwp_fill_plan")
native = importlib.import_module("hwp_native_plan")
security = importlib.import_module("hwp_security")
paths = importlib.import_module("hwp_text_apply")
MARKER = "TOY ONLY - UNSIGNED HWPX METADATA STAGE A - NOT FOR DELIVERY"
VALUE = "TOY NAME ONLY"
PURPOSE_PREFIXES = {
    "reproduce-legacy-no": "toy-fill-metadata-",
    "preserve-generated-yes": "toy-fill-compatible-",
}


def spine(path):
    raw = r.archive(path)["Contents/content.hpf"]
    root = native.strict_root(raw)
    refs = root.findall(native.OPF + "spine/" + native.OPF + "itemref")
    result = []
    for node in refs:
        if not node.get("idref") or node.get("linear") not in (None, "yes", "no"):
            raise ValueError("Unsupported generated reading-order metadata")
        result.append(
            {
                "idref": node.get("idref"),
                "linear": node.get("linear"),
                "semantic_linear": node.get("linear", "yes"),
            }
        )
    return raw, result


def has_precondition(items, purpose="reproduce-legacy-no"):
    if not isinstance(purpose, str) or purpose not in PURPOSE_PREFIXES:
        raise ValueError("Unknown diagnostic purpose")
    bodies = [row for row in items if row["idref"] == "section0"]
    return len(bodies) == 1 and (
        bodies[0]["linear"] == "no"
        if purpose == "reproduce-legacy-no"
        else bodies[0]["linear"] in (None, "yes")
    )


def selected_cell(source):
    mapped = f.inspect_template(source)
    if len({(row["part"], tuple(row["table_path"])) for row in mapped["fields"]}) != 1:
        raise ValueError("Expected exactly the one table authored by this probe")
    targets = [
        row
        for row in mapped["fields"]
        if row["cell_address"] == {"rowAddr": "1", "colAddr": "1"}
        and row["supported"]
        and row["expected_text"] == ""
        and "Name" in row["label_hints"]
    ]
    if len(targets) != 1:
        raise ValueError("Own explicit Name cell missing/ambiguous; no guessing")
    field = targets[0]
    root = native.strict_root(r.archive(source)[field["part"]])
    _, run_path = fill.target_paths(root, field["cell_path"])
    return [
        {
            "field_id": field["field_id"],
            "part": field["part"],
            "run_path": run_path,
            "new_text": VALUE,
            "cell_address": field["cell_address"],
        }
    ]


def save_hwpx(app, target, directory):
    if (
        target.parent != directory
        or target.suffix.lower() != ".hwpx"
        or target.exists()
        or target.is_symlink()
    ):
        raise ValueError("Stage A saves NEW fixed HWPX outputs only")
    if (
        not app.SaveAs(str(target.resolve()), "HWPX", "")
        or not target.is_file()
        or target.stat().st_size == 0
    ):
        raise RuntimeError("Owned HWPX export failed; no retry")


def action(app, name):
    if name not in ("BreakPara", "TableRightCell", "TableLeftCell", "TableLowerCell"):
        raise ValueError("Unknown authoring action")
    if app.Run(name) is False:
        raise RuntimeError("Owned TOY navigation rejected")


def insert(app, text):
    parameter = app.HParameterSet.HInsertText
    app.HAction.GetDefault("InsertText", parameter.HSet)
    parameter.Text = text
    if not app.HAction.Execute("InsertText", parameter.HSet):
        raise RuntimeError("Owned TOY text creation failed")


def author(app, observe_cell=None):
    char = app.HParameterSet.HCharShape
    app.HAction.GetDefault("CharShape", char.HSet)
    char.Height = 1000
    char.FaceNameLatin = "Courier New"
    if not app.HAction.Execute("CharShape", char.HSet):
        raise RuntimeError("Owned TOY font creation failed")
    insert(app, MARKER)
    action(app, "BreakPara")
    insert(app, "FICTIONAL TEST ONLY; no source report, seal, signature or pictures.")
    action(app, "BreakPara")
    table = app.HParameterSet.HTableCreation
    app.HAction.GetDefault("TableCreate", table.HSet)
    table.Rows = 4
    table.Cols = 2
    table.WidthType = 1
    table.HeightType = 1
    table.WidthValue = app.MiliToHwpUnit(140)
    table.HeightValue = app.MiliToHwpUnit(48)
    table.TableProperties.TreatAsChar = 1
    table.CreateItemArray("ColWidth", 2)
    table.ColWidth.SetItem(0, app.MiliToHwpUnit(45))
    table.ColWidth.SetItem(1, app.MiliToHwpUnit(95))
    if not app.HAction.Execute("TableCreate", table.HSet):
        raise RuntimeError("Owned TOY table creation failed")
    for index, label in enumerate(("TEST LABEL", "Name", "Code", "Date")):
        if observe_cell is not None:
            observe_cell(index, 0, app)
        insert(app, label)
        action(app, "TableRightCell")
        if observe_cell is not None:
            observe_cell(index, 1, app)
        if index == 0:
            insert(app, "TEST INPUT CELLS BELOW")
        if index < 3:
            action(app, "TableLeftCell")
            action(app, "TableLowerCell")


def window_pid(window):
    handle = int(window.WindowHandle)
    user = ctypes.WinDLL("user32", use_last_error=True)
    user.GetWindowThreadProcessId.argtypes = [
        wintypes.HWND,
        ctypes.POINTER(wintypes.DWORD),
    ]
    user.GetWindowThreadProcessId.restype = wintypes.DWORD
    pid = wintypes.DWORD()
    if (
        handle <= 0
        or not user.GetWindowThreadProcessId(handle, ctypes.byref(pid))
        or not pid.value
    ):
        raise RuntimeError("New window PID is unavailable; no editing")
    return pid.value


def verify_owned_blank(app, before, started_ms, report):
    report["cleanup"] = "ownership_unconfirmed_owner_inspection_required"
    if before != []:
        raise RuntimeError("A verified empty preexisting Hwp inventory is required")
    window = app.XHwpWindows.Item(0)
    pid = window_pid(window)
    after = hwp_inventory()
    report["after_dispatch_hwp_processes"] = after
    now = int(datetime.now(timezone.utc).timestamp() * 1000)
    if (
        len(after) != 1
        or after[0]["pid"] != pid
        or not started_ms <= after[0]["creation_ms"] <= now
    ):
        raise RuntimeError("Window is not bound to the unique fresh Hwp process")
    report.update(
        owned_hwp_pid=pid,
        owned_process_creation_ms=after[0]["creation_ms"],
        window_pid_binding_verified=True,
    )

    def unchanged_unsaved():
        count = app.XHwpDocuments.Count
        if type(count) is not int or count != 1:
            return False
        document = app.XHwpDocuments.Item(0)
        path, name, modified = document.Path, document.FullName, document.Modified
        return (
            isinstance(path, str)
            and not path
            and isinstance(name, str)
            and not name
            and type(modified) in (bool, int)
            and modified == 0
        )

    if not unchanged_unsaved():
        raise RuntimeError(
            "Not a single verified unsaved/unmodified document; no text read"
        )
    blank = app.GetTextFile("TEXT", "")
    # None is calibrated ONLY for this independently owned fresh blank document.
    if blank is not None and (not isinstance(blank, str) or blank.strip()):
        raise RuntimeError("New document has unsupported/nonempty text; no editing")
    if not unchanged_unsaved():
        raise RuntimeError("Document changed during blank-state read")
    report.update(
        owned_blank_verified=True,
        blank_text_return_type="NoneType" if blank is None else "str",
    )
    return window


def native_pages(app):
    count = app.PageCount
    if type(count) is not int or count <= 0:
        raise ValueError("Native layout read did not provide a positive page count")
    return count


def require_source_cache(source, selected):
    parts = r.archive(source)
    for item in selected:
        root = native.strict_root(parts[item["part"]])
        paragraph = fill._node(root, item["run_path"][:-1])
        try:
            native._cache(
                paragraph, r.HP
            )  # Validate a parsed copy only; never write/reuse a fabricated cache.
        except ValueError as error:
            raise ValueError(
                "Original selected paragraph has no valid native layout cache; stop before input/import"
            ) from error


def native_stage(directory, event, report):
    import pythoncom
    import win32com.client
    from win32com.client import gencache

    module = security.registered_security_module()  # READ ONLY; no install/HKCU write.
    event("require_no_preexisting_hwp")
    before = hwp_inventory()
    report["before_hwp_processes"] = before
    if before != []:
        raise RuntimeError(
            "Existing or unverified Hwp inventory requires owner inspection; no COM start"
        )
    pythoncom.CoInitialize()
    app = None
    window = None
    owned = False
    kernel = None
    process_handle = None
    source = directory / "TOY-ONLY-source.hwpx"
    prepared = directory / "TOY-ONLY-filled-input.hwpx"
    output = directory / "TOY-ONLY-reexport.hwpx"
    immutable = {}
    try:
        event("dispatch_new")
        started_ms = int(datetime.now(timezone.utc).timestamp() * 1000)
        app = gencache.EnsureDispatch(win32com.client.DispatchEx("HWPFrame.HwpObject"))
        event("verify_blank_ownership")
        window = verify_owned_blank(app, before, started_ms, report)
        owned = True
        report["cleanup"] = "owned_instance_active"
        kernel = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
        kernel.OpenProcess.restype = wintypes.HANDLE
        kernel.WaitForSingleObject.argtypes = [wintypes.HANDLE, wintypes.DWORD]
        kernel.WaitForSingleObject.restype = wintypes.DWORD
        kernel.CloseHandle.argtypes = [wintypes.HANDLE]
        kernel.CloseHandle.restype = wintypes.BOOL
        process_handle = kernel.OpenProcess(
            0x00100000, False, report["owned_hwp_pid"]
        )  # SYNCHRONIZE only; cannot terminate.
        if not process_handle:
            raise RuntimeError("Cannot monitor the exact owned process exit")
        window.Visible = False
        report["hancom_version"] = str(app.Version)
        report["security_module"] = {
            "alias": module["alias"],
            "dll_sha256": module["dll_sha256"],
            "auto_installed": False,
            "registry_updated": False,
        }
        event("register_existing_module")
        if not app.RegisterModule(security.MODULE_TYPE, security.MODULE_ALIAS):
            raise RuntimeError("Existing approved module rejected")
        event("author_new_unsigned_toy")
        author(app)
        event("read_source_native_layout")
        report["source_native_pages"] = native_pages(app)
        event("save_new_source_hwpx_only")
        save_hwpx(app, source, directory)
        immutable[source.name] = r.file_hash(source)
        raw, items = spine(source)
        report["source_spine"] = items
        purpose = report.get("diagnostic_purpose", "reproduce-legacy-no")
        report["legacy_no_condition_present"] = has_precondition(items)
        if not has_precondition(items, purpose):
            report["status"] = "not_reproduced"
            report["reason"] = (
                "Generated source does not meet the explicitly selected purpose: "
                + purpose
                + "; no source metadata modified"
            )
            return
        selected = selected_cell(source)
        event("original_cache_preflight")
        require_source_cache(source, selected)
        report["source_selected_cache_valid"] = True
        event("write_exact_own_empty_cell_input")
        fill.write_input(source, prepared, selected)
        immutable[prepared.name] = r.file_hash(prepared)
        input_raw, input_items = spine(prepared)
        if raw != input_raw:
            raise ValueError("Pure input writer changed OPF; no Native import")
        report["input_hpf_exact_source"] = True
        report["input_spine"] = input_items
        event("clear_owned_authoring_buffer")
        app.Clear(1)
        event("open_own_filled_hwpx_once")
        if not app.Open(str(prepared.resolve()), "HWPX", "versionwarning:false"):
            raise RuntimeError("Owned HWPX input rejected; no retry")
        expected = app.GetTextFile("TEXT", "")
        if (
            not isinstance(expected, str)
            or expected.count(MARKER) != 1
            or expected.count(VALUE) != 1
        ):
            raise ValueError("Owned source marker/exact inserted value mismatch")
        event("read_filled_native_layout")
        report["filled_native_pages"] = native_pages(app)
        if report["filled_native_pages"] != report["source_native_pages"]:
            raise ValueError("Native page count changed; no resizing or retry")
        if app.GetTextFile("TEXT", "") != expected:
            raise ValueError("Native buffer text changed during layout read")
        event("save_new_hwpx_only_once")
        save_hwpx(app, output, directory)
        if app.GetTextFile("TEXT", "") != expected:
            raise ValueError("Unexpected buffer text during HWPX export")
        _, after = spine(output)
        report["reexport_spine"] = after
        report["pre_hwp_saveas"] = True
        event("existing_fill_structural_gate")
        report["structural_gate"] = fill.structural_gate(
            source, output, selected, fill.header_spine(source)
        )
        report["status"] = "stage_a_metadata_preserved_not_qualified"
    except BaseException as error:
        report["failure_stage"] = report["stage"]
        report["failure_type"] = type(error).__name__
        report["failure_error"] = str(error)[:1000]
        raise
    finally:
        try:
            if app is not None and owned:
                event("clear_quit_owned_instance")
                try:
                    try:
                        app.Clear(1)
                    finally:
                        app.Quit()
                    report["cleanup"] = "owned_clear_quit_completed"
                except BaseException as error:
                    report["status"] = "blocked"
                    report["cleanup"] = "failed_owner_inspection_required"
                    report["cleanup_error_type"] = type(error).__name__
        finally:
            window = None
            app = None
            gc.collect()
            pythoncom.CoUninitialize()
            if owned:
                try:
                    waited_exit = False
                    if process_handle and kernel is not None:
                        try:
                            waited_exit = (
                                kernel.WaitForSingleObject(process_handle, 5000) == 0
                            )
                            report["owned_exit_handle_signaled"] = waited_exit
                        finally:
                            kernel.CloseHandle(process_handle)
                            process_handle = None
                    remaining = hwp_inventory()
                    report["after_cleanup_hwp_processes"] = remaining
                    exited = waited_exit and not any(
                        row["pid"] == report["owned_hwp_pid"]
                        and row["creation_ms"] == report["owned_process_creation_ms"]
                        for row in remaining
                    )
                    report["owned_process_exit_confirmed"] = exited
                    if not exited:
                        report["status"] = "blocked"
                        report["cleanup"] = (
                            "owned_exit_unconfirmed_owner_inspection_required"
                        )
                except Exception as error:
                    report["status"] = "blocked"
                    report["cleanup"] = "owned_exit_unknown_owner_inspection_required"
                    report["cleanup_error_type"] = type(error).__name__
            for name, sha in immutable.items():
                if r.file_hash(directory / name) != sha:
                    raise ValueError("Own source/input mutated during probe")
            report["immutable_inputs_unchanged"] = True


def run(output, *, run_native=False, toy_only=False, purpose="reproduce-legacy-no"):
    if not isinstance(purpose, str) or purpose not in PURPOSE_PREFIXES:
        raise ValueError("Explicit known diagnostic purpose required")
    if run_native is not True or toy_only is not True or getattr(sys, "frozen", False):
        raise ValueError(
            "Explicit SOURCE-only --run-native-test --toy-only Stage A consent required"
        )
    directory = paths.real_path(output)
    if (
        not directory.is_absolute()
        or directory.parent != ROOT / "work"
        or not directory.name.startswith(PURPOSE_PREFIXES[purpose])
        or directory.exists()
    ):
        raise ValueError(
            "Fresh direct owned work folder matching the selected purpose only; no reuse/outside workspace"
        )
    security.registered_security_module()  # Preflight READ ONLY before creating output.
    directory.mkdir()
    nonce = secrets.token_hex(16)
    report = {
        "test_only_not_deliverable": True,
        "kind": "unsigned-HWPX-only-Stage-A-metadata",
        "diagnostic_purpose": purpose,
        "is_legacy_no_reproduction_purpose": purpose == "reproduce-legacy-no",
        "preservation_contract_changed": False,
        "status": "blocked",
        "stage": "entry",
        "python_pid": os.getpid(),
        "owner_nonce": nonce,
        "native_instances_requested": 1,
        "hwp_or_pdf_saved": False,
        "actual_report_used": False,
        "approval_record_used": False,
        "qualification": False,
        "automatic_retries": 0,
        "cleanup": "not_started",
    }
    with (directory / "OWNER-TEST-ONLY.json").open("x", encoding="utf-8") as stream:
        json.dump(
            {
                "pid": os.getpid(),
                "nonce": nonce,
                "stage_a_only": True,
                "diagnostic_purpose": purpose,
                "test_only": True,
            },
            stream,
            indent=2,
        )
    with (directory / "events.jsonl").open("x", encoding="utf-8"):
        pass

    def event(stage):
        report["stage"] = stage
        try:
            with (directory / "events.jsonl").open("a", encoding="utf-8") as stream:
                stream.write(
                    json.dumps(
                        {
                            "stage": stage,
                            "pid": os.getpid(),
                            "utc": datetime.now(timezone.utc).isoformat(),
                        }
                    )
                    + "\n"
                )
                stream.flush()
                os.fsync(stream.fileno())
        except OSError:
            report["event_log_failed"] = (
                True  # Logging never prevents owned Clear/Quit.
            )

    try:
        native_stage(directory, event, report)
    except BaseException as error:
        report["status"] = "blocked"
        report.setdefault("failure_stage", report["stage"])
        report["error_type"] = type(error).__name__
        report["error"] = str(error)[:1000]
    finally:
        report["files_sha256"] = {
            path.name: r.file_hash(path)
            for path in directory.iterdir()
            if path.is_file()
        }
        with (directory / "STAGE-A-TEST-ONLY.json").open(
            "x", encoding="utf-8"
        ) as stream:
            json.dump(report, stream, ensure_ascii=False, indent=2)
    return report


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run-native-test", action="store_true", required=True)
    parser.add_argument("--toy-only", action="store_true", required=True)
    parser.add_argument(
        "--purpose", choices=tuple(PURPOSE_PREFIXES), default="reproduce-legacy-no"
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=None,
    )
    args = parser.parse_args()
    output = args.output or ROOT / "work" / (
        PURPOSE_PREFIXES[args.purpose]
        + datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        + "-"
        + secrets.token_hex(3)
    )
    result = run(
        output,
        run_native=args.run_native_test,
        toy_only=args.toy_only,
        purpose=args.purpose,
    )
    print(json.dumps(result, ensure_ascii=False, indent=2))
    sys.exit(1 if result["status"] == "blocked" else 0)
