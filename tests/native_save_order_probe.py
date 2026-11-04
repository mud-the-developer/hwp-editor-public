"""SOURCE-only, opt-in no-edit Open-format / SaveAs-order provenance.
One newly authored unsigned 4x2 TOY; observations are NEVER qualification.
No caller source, JSON admission, imports, filling, approval or production apply.
"""

import argparse
import base64
import ctypes
from ctypes import wintypes
from datetime import datetime, timezone
import gc
import json
import os
from pathlib import Path
import re
import secrets
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
import native_cell_position_probe as cell
import native_direct_fill_probe as direct
import native_fill_metadata_probe as fixture
from native_blank_metadata_probe import hwp_inventory
import hwp_security as security
import hwp_native_owner as owner
import hwp_process_identity as process_identity

r = fixture.r
# These are the entire experiment, not a caller-supplied operation language.
ARMS = (
    ("", ("HWPX",)),
    ("HWP", ("HWPX",)),
    ("HWP", ("HWP", "HWPX")),
    ("HWP", ("HWPX",)),
    ("", ("HWPX",)),  # Match arm1, deferring document TEXT reads until export.
    ("", ("HWPX",)),  # Match arm5, using DispatchEx without EnsureDispatch.
)


def local_path(lab, path, *, fresh=False):
    path = fixture.paths.real_path(path)
    if path != path.resolve() or not path.is_relative_to(lab) or path == lab:
        raise ValueError("Only canonical paths inside this new lab are permitted")
    if fresh:
        if path.exists():
            raise ValueError("Never overwrite an existing diagnostic artifact")
    elif not path.is_file() or path.stat().st_nlink != 1 or path.stat().st_size == 0:
        raise ValueError("Missing, empty or nonexclusive diagnostic artifact")
    return path


def new_json(lab, name, data):
    with local_path(lab, lab / name, fresh=True).open("x", encoding="utf-8") as stream:
        json.dump(data, stream, ensure_ascii=False, allow_nan=False, indent=2)
        stream.flush()
        os.fsync(stream.fileno())


def unchanged(lab, hashes):
    for path, digest in hashes.items():
        if r.file_hash(local_path(lab, path)) != digest:
            raise ValueError("Immutable source/copy/output/evidence bytes changed")


def code_hashes():
    modules = (
        cell,
        direct,
        fixture,
        fixture.native,
        fixture.paths,
        r,
        security,
        owner,
        process_identity,
    )
    paths = {Path(__file__)}
    for module in modules:
        source = module.__file__
        if source is None:
            raise ValueError("Diagnostic module source file unavailable")
        paths.add(Path(source))
    paths.add(Path(cell.__file__).with_name("native_blank_metadata_probe.py"))
    return {
        p.relative_to(Path(__file__).resolve().parents[1]).as_posix(): r.file_hash(p)
        for p in sorted(paths)
    }


def register_existing(app):
    info = security.registered_security_module()  # Read-only; NEVER ensure/install.
    if not app.RegisterModule(security.MODULE_TYPE, security.MODULE_ALIAS):
        raise RuntimeError("Already registered security module rejected; no fallback")
    return {
        "alias": info["alias"],
        "dll_sha256": info["dll_sha256"],
        "auto_installed": False,
        "registry_updated": False,
    }


def identity(app, expected):
    """One exact saved identity, or the independently owned pristine blank only."""
    documents = doc = None
    try:
        documents = app.XHwpDocuments
        if type(documents.Count) is not int or documents.Count != 1:
            raise ValueError("Document count is not the typed singleton")
        doc = documents.Item(0)
        name, modified = doc.FullName, doc.Modified
        if type(name) is not str or type(modified) not in (bool, int) or modified != 0:
            raise ValueError("Document identity/modification state is unknown")
        if expected is None:
            if name or type(doc.Path) is not str or doc.Path:
                raise ValueError("Owned blank identity changed")
        elif (
            not name
            or fixture.paths.real_path(name) != expected
            or not cell.own_saved_document(app, expected)
        ):
            raise ValueError("Known document identity changed")
    finally:
        doc = documents = None


def blank(app):
    identity(app, None)
    text = app.GetTextFile("TEXT", "")
    if text is not None and (type(text) is not str or text.strip()):
        raise ValueError("Owned blank TEXT changed")
    identity(app, None)


def snapshot(app, path, text_sha, pages=None, text=None):
    identity(app, path)
    value = app.GetTextFile("TEXT", "")
    if (
        type(value) is not str
        or value.count(fixture.MARKER) != 1
        or r.digest(value.encode("utf-8")) != text_sha
        or text is not None
        and value != text
    ):
        raise ValueError("Missing/changed typed source TEXT snapshot")
    count = fixture.native_pages(app)
    again = app.GetTextFile("TEXT", "")
    if type(again) is not str or again != value or pages is not None and count != pages:
        raise ValueError("TEXT/page count drift during no-edit observation")
    identity(app, path)
    return value, {
        "text_type": "str",
        "text_sha256": text_sha,
        "text_characters": len(value),
        "page_count": count,
        "document": path.name,
        "document_count": 1,
        "modified": False,
    }


def deferred_snapshot(app, path, pages):
    """Fixed export-first TOY control only; no claimed pre-save TEXT evidence."""
    identity(app, path)
    count = fixture.native_pages(app)
    if pages is None or count != pages:
        raise ValueError("Export-first control page count differs from the own source")
    identity(app, path)
    return {
        "text_snapshot": "deferred_until_after_first_save",
        "page_count": count,
        "document": path.name,
        "document_count": 1,
        "modified": False,
    }


def _create_source(directory, proof):
    """Use the shared owned creator; never patch live cache or registration APIs."""
    local_path(directory.parent, directory / "TOY-ONLY-source.hwp", fresh=True)
    local_path(directory.parent, directory / "TOY-ONLY-authoring-seed.hwp", fresh=True)
    cell.native_once(directory, proof)


def exit_monitor(pid):
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
    kernel.OpenProcess.restype = wintypes.HANDLE
    kernel.WaitForSingleObject.argtypes = [wintypes.HANDLE, wintypes.DWORD]
    kernel.WaitForSingleObject.restype = wintypes.DWORD
    kernel.CloseHandle.argtypes = [wintypes.HANDLE]
    kernel.CloseHandle.restype = wintypes.BOOL
    handle = kernel.OpenProcess(0x00100000, False, pid)  # SYNCHRONIZE, cannot kill.
    if not handle:
        raise RuntimeError("Exact owned exit handle unavailable")
    return kernel, handle


def observe_spine(path, baseline):
    raw, items = fixture.spine(path)  # Existing archive and strict XML parser.
    ids = [item["idref"] for item in items]
    if len(ids) != len(set(ids)) or not {"header", "section0"}.issubset(ids):
        raise ValueError("Missing/ambiguous required HPF spine snapshot")
    observation = {
        "raw_hpf_base64": base64.b64encode(raw).decode("ascii"),
        "raw_hpf_sha256": r.digest(raw),
        "spine": items,
    }
    baseline = baseline or observation
    semantic = lambda rows: [(v["idref"], v["semantic_linear"]) for v in rows]
    observation["baseline_comparison"] = {
        "baseline_arm": 1,
        "baseline_raw_hpf_sha256": baseline["raw_hpf_sha256"],
        "raw_hpf_equal": observation["raw_hpf_sha256"] == baseline["raw_hpf_sha256"],
        "literal_spine_equal": items == baseline["spine"],
        "semantic_spine_equal": semantic(items) == semantic(baseline["spine"]),
    }
    return observation


def _native_arm(lab, number, source_copy, hashes, text_sha, pages, baseline, evidence):
    """One fixed no-edit arm. All failures, including cleanup, block the caller."""
    import pythoncom
    import win32com.client
    from win32com.client import gencache

    app = window = kernel = handle = None
    owned = initialized = False
    current = None
    expected_text = None
    expected_pages = pages
    evidence.update(
        status="blocked",
        operations=[],
        cleanup="not_started",
        owned_process_exit_confirmed=False,
    )
    try:
        before = hwp_inventory()
        evidence["before_hwp_processes"] = before
        if before != []:
            raise RuntimeError("Nonempty/unverified Hwp inventory; no start")
        unchanged(lab, hashes)
        pythoncom.CoInitialize()
        initialized = True
        evidence["cleanup"] = "ownership_unconfirmed_owner_inspection_required"
        started = int(datetime.now(timezone.utc).timestamp() * 1000)
        evidence["python_process_id"] = os.getpid()
        evidence["dispatch_path"] = (
            "DispatchEx" if number == 6 else "DispatchEx+EnsureDispatch"
        )
        app = win32com.client.DispatchEx("HWPFrame.HwpObject")
        evidence["dispatch_wrapper"] = (
            f"{type(app).__module__}.{type(app).__qualname__}"
        )
        if number != 6:
            # Keep no second COM reference; it would prevent owned process exit.
            app = gencache.EnsureDispatch(app)
        evidence["active_wrapper"] = f"{type(app).__module__}.{type(app).__qualname__}"
        window = fixture.verify_owned_blank(app, before, started, evidence)
        owned = True
        kernel, handle = exit_monitor(evidence["owned_hwp_pid"])
        expected_process = {
            "pid": evidence["owned_hwp_pid"],
            "creation_ms": evidence["owned_process_creation_ms"],
        }
        if hwp_inventory() != [expected_process]:
            raise ValueError("Owned process changed while acquiring exact exit handle")
        window.Visible = False
        evidence["security_module"] = register_existing(app)
        blank(app)
        fmt, saves = ARMS[number - 1]
        op = {"operation": "Open", "format": fmt, "path": source_copy.name}
        evidence["operations"].append(op)
        unchanged(lab, hashes)
        if not app.Open(str(source_copy), fmt, "versionwarning:false"):
            raise RuntimeError("Own source-copy Open failed; no retry")
        current = source_copy
        if number in (5, 6):
            op["after"] = deferred_snapshot(app, current, pages)
        else:
            expected_text, op["after"] = snapshot(app, current, text_sha, pages)
        expected_pages = op["after"]["page_count"]
        unchanged(lab, hashes)
        for fmt in saves:
            target = local_path(lab, lab / f"arm{number}.{fmt.lower()}", fresh=True)
            op = {"operation": "SaveAs", "format": fmt, "path": target.name}
            evidence["operations"].append(op)
            if number in (5, 6) and expected_text is None:
                op["before"] = deferred_snapshot(app, current, expected_pages)
            else:
                _, op["before"] = snapshot(
                    app, current, text_sha, expected_pages, expected_text
                )
            unchanged(lab, hashes)
            local_path(lab, target, fresh=True)
            op["attempted"] = True
            if not app.SaveAs(str(target), fmt, ""):
                raise RuntimeError("No-edit SaveAs failed; no retry or fallback")
            current = (
                target  # SaveAs must establish exactly this identity, not an allowlist.
            )
            op["output_sha256"] = r.file_hash(local_path(lab, target))
            hashes[target] = op["output_sha256"]
            expected_text, op["after"] = snapshot(
                app, current, text_sha, expected_pages, expected_text
            )
            unchanged(lab, hashes)
            if fmt == "HWPX":
                op["hpf"] = observe_spine(target, baseline)
                evidence["hpf"] = op["hpf"]
            else:
                op["hpf"] = None  # Binary HWP has no directly observed HPF snapshot.
        evidence["status"] = "observed_not_qualified"
    except BaseException as error:
        evidence.update(
            status="blocked", error_type=type(error).__name__, error=str(error)[:1000]
        )
    finally:
        # Do not keep a live exception/traceback or document COM reference at exit wait.
        try:
            if owned:
                if app is None:
                    raise RuntimeError("Owned Native reference unavailable")
                unchanged(lab, hashes)
                if current is None:
                    blank(app)
                else:
                    snapshot(app, current, text_sha, expected_pages, expected_text)
                evidence["cleanup"] = "owned_known_document_verified"
                if app.Clear(1) is False:
                    raise RuntimeError("Owned Clear failed")
                blank(app)  # Quit is forbidden if Clear left an unknown document.
                app.Quit()
                evidence["cleanup"] = "owned_clear_quit_returned"
        except BaseException as error:
            evidence.update(
                status="blocked",
                cleanup="unknown_owner_inspection_required",
                cleanup_error_type=type(error).__name__,
            )
        finally:
            window = app = None
            gc.collect()
            try:
                if initialized:
                    pythoncom.CoUninitialize()
            except BaseException as error:
                evidence.update(
                    status="blocked", cleanup_error_type=type(error).__name__
                )
            try:
                if handle is not None:
                    if kernel is None:
                        raise RuntimeError("Owned exit monitor unavailable")
                    waited = kernel.WaitForSingleObject(handle, 5000)
                    evidence["exit_wait_result"] = waited
                    after = hwp_inventory()
                    evidence["after_hwp_processes"] = after
                    evidence["owned_process_exit_confirmed"] = (
                        type(waited) is int
                        and waited == 0
                        and not any(
                            row["pid"] == evidence["owned_hwp_pid"]
                            and row["creation_ms"]
                            == evidence["owned_process_creation_ms"]
                            for row in after
                        )
                    )
                    if after != []:
                        evidence.update(
                            status="blocked",
                            remaining_hwp_requires_owner_inspection=True,
                        )
            except BaseException as error:
                evidence.update(status="blocked", exit_error_type=type(error).__name__)
            finally:
                if handle is not None:
                    try:
                        if kernel is None:
                            raise RuntimeError("Owned exit monitor unavailable")
                        evidence["exit_handle_closed"] = bool(
                            kernel.CloseHandle(handle)
                        )
                    except BaseException as error:
                        evidence["exit_handle_closed"] = False
                        evidence["exit_error_type"] = type(error).__name__
                if (
                    evidence["owned_process_exit_confirmed"] is not True
                    or evidence.get("exit_handle_closed") is not True
                ):
                    evidence["status"] = "blocked"
                    evidence["cleanup"] = "exit_unconfirmed_owner_inspection_required"
                elif evidence["cleanup"] == "owned_clear_quit_returned":
                    evidence["cleanup"] = "owned_exit_confirmed"


def run(output, *, run_native=False, toy_only=False):
    if run_native is not True or toy_only is not True or getattr(sys, "frozen", False):
        raise ValueError(
            "Literal SOURCE-only --run-native-test --toy-only opt-ins required"
        )
    lab = fixture.paths.real_path(output)
    if (
        lab.parent != ROOT / "work"
        or lab != lab.resolve()
        or not re.fullmatch(r"toy-save-order-[A-Za-z0-9_-]+", lab.name)
        or lab.exists()
    ):
        raise ValueError("Only a fresh direct ROOT/work/toy-save-order-* lab")
    # All opt-in/frozen/path guards precede security, process and lab actions.
    security.registered_security_module()
    if hwp_inventory() != []:
        raise RuntimeError("Existing Hwp state; no lab or Native start")
    lab.mkdir()
    report = {
        "kind": "own-unsigned-4x2-no-edit-save-order-provenance",
        "status": "blocked",
        "qualification": False,
        "production_enabled": False,
        "preservation_success_claimed": False,
        "automatic_retries": 0,
        "actual_documents_used": False,
        "edits_after_creation": False,
        "baseline_role": "arm1 observation only; not a preservation baseline promotion",
        "creation_metadata": {"qualification": False, "production_enabled": False},
        "arms": [],
    }
    hashes = {}
    try:
        report["code_sha256"] = code_hashes()
        new_json(
            lab,
            "OWNER-TEST-ONLY.json",
            {
                "test_only": True,
                "qualification": False,
                "production_enabled": False,
                "nonce": secrets.token_hex(16),
                "purpose": report["kind"],
            },
        )
        owner = lab / "OWNER-TEST-ONLY.json"
        hashes[owner] = r.file_hash(owner)
        source_dir = local_path(lab, lab / "source", fresh=True)
        source_dir.mkdir()
        try:
            _create_source(source_dir, report["creation_metadata"])
        finally:
            new_json(lab, "SOURCE-CREATION.json", report["creation_metadata"])
        source = local_path(lab, source_dir / "TOY-ONLY-source.hwp")
        direct.require_binding(source, report["creation_metadata"])
        source_sha = r.file_hash(source)
        hashes[source] = source_sha
        proof = report["creation_metadata"]
        text_sha = proof["reopened_observation"]["source_text_sha256"]
        if proof["observation"]["source_text_sha256"] != text_sha:
            raise ValueError("Creation/reopen TEXT evidence differs")
        report["source_sha256"] = source_sha
        pages = baseline = None
        for number in range(1, len(ARMS) + 1):
            unchanged(lab, hashes)
            direct.require_binding(source, proof)
            if code_hashes() != report["code_sha256"]:
                raise ValueError("Diagnostic code changed during invocation")
            origin = lab / "arm3.hwp" if number == 4 else source
            copy = local_path(lab, lab / f"arm{number}-source.hwp", fresh=True)
            with origin.open("rb") as src, copy.open("xb") as dst:
                dst.write(src.read())
                dst.flush()
                os.fsync(dst.fileno())
            hashes[copy] = hashes[origin]
            unchanged(lab, hashes)
            evidence = {
                "arm": number,
                "qualification": False,
                "production_enabled": False,
                "automatic_retries": 0,
                "source_sha256": source_sha,
                "copy_of": origin.relative_to(lab).as_posix(),
                "copy_sha256": hashes[copy],
                "code_sha256": report["code_sha256"],
            }
            report["arms"].append(evidence)
            try:
                _native_arm(
                    lab, number, copy, hashes, text_sha, pages, baseline, evidence
                )
                unchanged(lab, hashes)
                if evidence["status"] != "observed_not_qualified":
                    raise RuntimeError("Arm blocked; all remaining arms stopped")
                if number == 1:
                    baseline = evidence["hpf"]
                    pages = evidence["operations"][0]["after"]["page_count"]
                    report["baseline_NO_idrefs"] = [
                        item["idref"]
                        for item in baseline["spine"]
                        if item["semantic_linear"] == "no"
                    ]
            finally:
                new_json(lab, f"ARM-{number}.json", evidence)
        unchanged(lab, hashes)
        if code_hashes() != report["code_sha256"]:
            raise ValueError("Diagnostic code changed")
        report.update(
            status="diagnosis_completed_not_qualified",
            original_defect_conclusion=(
                "NO_baseline_observed_not_qualification"
                if report["baseline_NO_idrefs"]
                else "inconclusive_without_NO_baseline"
            ),
        )
    except BaseException as error:
        report.update(
            status="blocked", error_type=type(error).__name__, error=str(error)[:1000]
        )
    finally:
        report["recorded_sha256"] = {
            p.relative_to(lab).as_posix(): sha for p, sha in hashes.items()
        }
        # A failed SaveAs may leave bytes without a valid snapshot. Record, never repair.
        report["observed_files_sha256"] = {}
        names = [
            "source/TOY-ONLY-source.hwp",
            "OWNER-TEST-ONLY.json",
            "SOURCE-CREATION.json",
        ]
        names += [
            f"arm{i}{suffix}"
            for i in range(1, len(ARMS) + 1)
            for suffix in ("-source.hwp", ".hwp", ".hwpx")
        ]
        names += [f"ARM-{i}.json" for i in range(1, len(ARMS) + 1)]
        for name in names:
            try:
                path = fixture.paths.real_path(lab / name)
                if path.exists():
                    report["observed_files_sha256"][name] = r.file_hash(
                        local_path(lab, path)
                    )
            except Exception as error:
                report["status"] = "blocked"
                report.setdefault("artifact_errors", {})[name] = type(error).__name__
        try:
            unchanged(lab, hashes)
        except Exception as error:
            report.update(status="blocked", final_integrity_error=type(error).__name__)
        new_json(lab, "SAVE-ORDER-RESULT.json", report)
    return report


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run-native-test", action="store_true", required=True)
    parser.add_argument("--toy-only", action="store_true", required=True)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args(argv)
    target = args.output or ROOT / "work" / (
        "toy-save-order-"
        + datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        + "-"
        + secrets.token_hex(3)
    )
    try:
        report = run(target, run_native=args.run_native_test, toy_only=args.toy_only)
        status = report["status"]
    except Exception:
        status = "blocked"
    print(
        json.dumps(
            {"status": status, "qualification": False, "production_enabled": False}
        )
    )
    return 1 if status == "blocked" else 0


if __name__ == "__main__":
    raise SystemExit(main())
