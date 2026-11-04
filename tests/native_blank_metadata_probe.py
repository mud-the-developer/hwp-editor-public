"""OPT-IN one new-instance metadata read. No document content output/edit/Open/Save/Clear."""

import argparse
import ctypes
from ctypes import wintypes
from datetime import datetime, timezone
import hashlib
import gc
import json
import os
from pathlib import Path
import secrets
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
from hwp_text_apply import real_path


def describe_value(value):
    # Never repr/log the value, decode bytes, or emit individual character codes.
    if value is None:
        return {"python_type": "NoneType", "is_none": True}
    if isinstance(value, str):
        raw = value.encode("utf-8", "surrogatepass")
        bom = value.startswith("\ufeff")
        rest = value[1:] if bom else value
        return {
            "python_type": "str",
            "length_chars": len(value),
            "length_utf8_bytes": len(raw),
            "literal_empty": value == "",
            "strip_empty": not value.strip(),
            "starts_unicode_bom": bom,
            "only_whitespace": all(c.isspace() for c in value),
            "single_bom_then_whitespace_only": bom and all(c.isspace() for c in rest),
            "has_non_bom_non_whitespace": any(
                c != "\ufeff" and not c.isspace() for c in value
            ),
            "sha256_utf8_surrogatepass": hashlib.sha256(raw).hexdigest(),
        }
    if isinstance(value, bytes):
        return {
            "python_type": "bytes",
            "length_bytes": len(value),
            "utf8_bom": value.startswith(b"\xef\xbb\xbf"),
            "utf16_le_bom": value.startswith(b"\xff\xfe"),
            "utf16_be_bom": value.startswith(b"\xfe\xff"),
            "sha256": hashlib.sha256(value).hexdigest(),
        }
    return {
        "python_type": type(value).__name__
        if type(value) in (bool, int, float)
        else "other",
        "is_none": False,
    }


def hwp_inventory():
    command = "$ErrorActionPreference='Stop'; $items=@(Get-CimInstance Win32_Process -Filter \"Name='Hwp.exe'\"); @($items | ForEach-Object { [pscustomobject]@{pid=[int]$_.ProcessId;creation_ms=([DateTimeOffset]$_.CreationDate.ToUniversalTime()).ToUnixTimeMilliseconds()} }) | ConvertTo-Json -Compress"
    result = subprocess.run(
        ["powershell.exe", "-NoProfile", "-NonInteractive", "-Command", command],
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        timeout=20,
    )
    if result.returncode or len(result.stdout) > 16384 or len(result.stderr) > 16384:
        raise RuntimeError("Process metadata unavailable; no Native start")
    data = json.loads(result.stdout) if result.stdout.strip() else []
    rows = [data] if isinstance(data, dict) else data
    if not isinstance(rows, list) or any(
        not isinstance(row, dict)
        or set(row) != {"pid", "creation_ms"}
        or type(row["pid"]) is not int
        or type(row["creation_ms"]) is not int
        for row in rows
    ):
        raise ValueError("Unexpected process metadata")
    return rows


def native_metadata(event, report):
    import pythoncom
    import win32com.client
    from win32com.client import gencache

    before = hwp_inventory()
    report["before_hwp_processes"] = before
    if before:
        raise RuntimeError("Hwp process exists; no new instance or attachment")
    pythoncom.CoInitialize()
    app = None
    window = None
    document = None
    bound = False
    process_handle = None
    kernel = None
    started_ms = int(datetime.now(timezone.utc).timestamp() * 1000)
    try:
        event("dispatch_new_metadata_only")
        app = gencache.EnsureDispatch(win32com.client.DispatchEx("HWPFrame.HwpObject"))
        event("bind_exact_new_window_pid")
        window = app.XHwpWindows.Item(0)
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
            raise RuntimeError("Owned window PID unavailable")
        after = hwp_inventory()
        report["after_dispatch_hwp_processes"] = after
        if (
            len(after) != 1
            or after[0]["pid"] != pid.value
            or after[0]["creation_ms"] < started_ms
        ):
            raise RuntimeError(
                "New process identity ambiguous; no content read/cleanup"
            )
        report["owned_hwp_pid"] = pid.value
        report["owned_process_creation_ms"] = after[0]["creation_ms"]
        report["window_pid_binding_verified"] = True
        bound = True
        kernel = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
        kernel.OpenProcess.restype = wintypes.HANDLE
        kernel.WaitForSingleObject.argtypes = [wintypes.HANDLE, wintypes.DWORD]
        kernel.WaitForSingleObject.restype = wintypes.DWORD
        kernel.CloseHandle.argtypes = [wintypes.HANDLE]
        kernel.CloseHandle.restype = wintypes.BOOL
        process_handle = kernel.OpenProcess(
            0x00100000, False, pid.value
        )  # SYNCHRONIZE only, no terminate permission.
        window.Visible = False
        event("read_unsaved_document_metadata")
        count = int(app.XHwpDocuments.Count)
        report["document_count"] = count
        if count != 1:
            raise RuntimeError("Unexpected document count; no text read")
        document = app.XHwpDocuments.Item(0)
        path = document.Path
        name = document.FullName
        modified = bool(document.Modified)
        report["document_path_is_str"] = isinstance(path, str)
        report["document_path_empty"] = isinstance(path, str) and not path
        report["document_name_is_str"] = isinstance(name, str)
        report["document_name_empty"] = isinstance(name, str) and not name
        report["document_modified_before"] = modified
        if not report["document_path_empty"]:
            raise RuntimeError("Document has a path; no text read")
        event("classify_text_return_once_no_content")
        value = app.GetTextFile("TEXT", "")
        report["text_return_metadata"] = describe_value(value)
        value = None
        report["document_modified_after"] = bool(document.Modified)
        report["hancom_version"] = str(app.Version)
        report["status"] = "metadata_observed_not_qualified"
    except BaseException as error:
        report["status"] = "blocked"
        report["failure_stage"] = report.get("stage")
        report["error_type"] = type(error).__name__
    finally:
        if app is not None and bound:
            try:
                if int(app.XHwpDocuments.Count) == 1:
                    document = app.XHwpDocuments.Item(0)
                    path = document.Path
                    if (
                        isinstance(path, str)
                        and not path
                        and not bool(document.Modified)
                    ):
                        event("quit_retained_new_unmodified_instance")
                        app.Quit()
                        report["cleanup"] = "owned_quit_returned_no_clear_or_save"
                    else:
                        report["cleanup"] = (
                            "document_state_changed_owner_inspection_required"
                        )
                else:
                    report["cleanup"] = (
                        "document_count_changed_owner_inspection_required"
                    )
            except BaseException as error:
                report["cleanup"] = "failed_owner_inspection_required"
                report["cleanup_error_type"] = type(error).__name__
        elif app is not None:
            report["cleanup"] = "ownership_unconfirmed_owner_inspection_required"
        document = None
        window = None
        app = None
        gc.collect()
        pythoncom.CoUninitialize()
        if process_handle and kernel:
            try:
                exited = kernel.WaitForSingleObject(process_handle, 5000) == 0
                report["owned_process_exit_confirmed"] = exited
                if not exited:
                    report["cleanup"] = (
                        "quit_process_exit_unconfirmed_owner_inspection_required"
                    )
                    report["status"] = "blocked"
            finally:
                kernel.CloseHandle(process_handle)
        if (
            report.get("cleanup") != "owned_quit_returned_no_clear_or_save"
            or report.get("owned_process_exit_confirmed") is not True
        ):
            report["status"] = "blocked"


def run(output, *, run_native=False, metadata_only=False):
    if (
        run_native is not True
        or metadata_only is not True
        or getattr(sys, "frozen", False)
    ):
        raise ValueError("Explicit SOURCE-only metadata diagnostic consent required")
    directory = real_path(output)
    if (
        not directory.is_absolute()
        or directory.parent != ROOT / "work"
        or not directory.name.startswith("toy-hwp-blank-metadata-")
        or directory.exists()
    ):
        raise ValueError("Fresh direct owned metadata-only lab required")
    directory.mkdir()
    report = {
        "kind": "new-Hwp-return-metadata-only",
        "status": "blocked",
        "stage": "entry",
        "python_pid": os.getpid(),
        "raw_content_recorded": False,
        "document_edit_performed": False,
        "document_open_performed": False,
        "document_save_performed": False,
        "clear_performed": False,
        "qualification": False,
        "automatic_retries": 0,
        "cleanup": "not_started",
    }
    with (directory / "OWNER-METADATA-ONLY.json").open("x", encoding="utf-8") as stream:
        json.dump(
            {"pid": os.getpid(), "nonce": secrets.token_hex(16), "metadata_only": True},
            stream,
        )
    with (directory / "events.jsonl").open("x", encoding="utf-8"):
        pass

    def event(stage):
        report["stage"] = stage
        try:
            with (directory / "events.jsonl").open("a", encoding="utf-8") as stream:
                stream.write(
                    json.dumps(
                        {"stage": stage, "utc": datetime.now(timezone.utc).isoformat()}
                    )
                    + "\n"
                )
                stream.flush()
                os.fsync(stream.fileno())
        except OSError:
            report["event_log_failed"] = True

    try:
        native_metadata(event, report)
    except BaseException as error:
        report["status"] = "blocked"
        report["error_type"] = type(error).__name__
    finally:
        with (directory / "METADATA-ONLY-RESULT.json").open(
            "x", encoding="utf-8"
        ) as stream:
            json.dump(report, stream, ensure_ascii=False, indent=2)
    return report


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run-native-test", action="store_true", required=True)
    parser.add_argument("--metadata-only", action="store_true", required=True)
    parser.add_argument(
        "--output",
        type=Path,
        default=ROOT
        / "work"
        / (
            "toy-hwp-blank-metadata-"
            + datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
            + "-"
            + secrets.token_hex(3)
        ),
    )
    args = parser.parse_args()
    result = run(
        args.output, run_native=args.run_native_test, metadata_only=args.metadata_only
    )
    print(json.dumps(result, ensure_ascii=False, indent=2))
    raise SystemExit(0 if result["status"] == "metadata_observed_not_qualified" else 1)
