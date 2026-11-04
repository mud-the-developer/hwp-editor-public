"""PREPARATION ONLY owned Hancom worker, no edit/approval/apply entry point.

Inputs/outputs derive from existing engine preparing.json + one fixed phase.
Requires an already registered, exact approved security module. No DLL install,
registry write, generic COM action, source Save(), file overwrite or PID kill.
"""

import json
import os
import shutil

from hwp_project import step_paths
from hwp_security import MODULE_ALIAS, MODULE_TYPE, registered_security_module
from hwp_native_owner import owned_session


def export_phase(workspace, phase, trace=None):
    event = trace.event if trace is not None else lambda stage: None
    event("step_paths_begin")
    source, outputs, reply = step_paths(workspace, phase)
    event("step_paths_done")
    event("security_preflight_begin")
    security = registered_security_module()
    event("security_preflight_done")
    if phase == 0:
        # NEVER open the user's input in COM, even during no-edit preparation.
        # A fresh verified copy confines possible format/macro side effects.
        import hwp_review as review

        copied = workspace / "native-import-copy.hwp"
        event("input_copy_begin")
        with source.open("rb") as original, copied.open("xb") as target:
            shutil.copyfileobj(original, target, length=1024 * 1024)
        expected = review.read_json(workspace / "preparing.json")["source_audit"][
            "sha256"
        ]
        if review.file_hash(copied) != expected or review.file_hash(source) != expected:
            raise ValueError("Native preparation copy differs from the captured source")
        source = copied
        event("input_copy_done")
    event("com_import_begin")
    # Source/path/security checks precede the fixed fresh owned COM session.
    with owned_session((source, *outputs), event) as app:
        if not app.RegisterModule(MODULE_TYPE, MODULE_ALIAS):
            raise RuntimeError(
                "Hancom rejected the approved security module; no document opened"
            )
        event("open_begin")
        if not app.Open(str(source.resolve()), "", "versionwarning:false"):
            raise RuntimeError(
                "한글이 문서를 열지 못했습니다. 접근 대화상자/문서 상태를 확인하세요."
            )
        event("open_done")
        pages = int(app.PageCount)
        # Exact fixed NEW paths only. Never call Save()/SaveAs on the input.
        for path, format in (
            (outputs[1], "HWPX"),
            (outputs[2], "HWP"),
            (outputs[0], "PDF"),
        ):
            if path is None:
                continue
            if path.exists():
                raise FileExistsError("Existing preparation output: " + str(path))
            event("export_" + format + "_begin")
            if (
                not app.SaveAs(str(path.resolve()), format, "")
                or not path.is_file()
                or not path.stat().st_size
            ):
                raise RuntimeError("한글 준비 출력이 생성되지 않았습니다: " + format)
            event("export_" + format + "_done")
        return {
            "native_pages": pages,
            "hancom_version": str(app.Version),
            "security_module": {**security, "registered": True},
            "owned_session": app.ownership,
        }


def probe(workspace, phase, trace):
    """Same import/path/security preflight ONLY. Never creates COM or Native output."""
    trace.event("probe_step_paths_begin")
    step_paths(workspace, phase)
    trace.event("probe_step_paths_done")
    registered_security_module()
    trace.event("probe_security_done")
    trace.event("probe_exit")
    return 0


def run(workspace, phase, trace=None):
    event = trace.event if trace is not None else lambda stage: None
    # Invalid/unprepared folders do not get a report, let alone native execution.
    event("run_validation_begin")
    _, _, reply = step_paths(workspace, phase)
    event("run_validation_done")
    try:
        native = export_phase(workspace, phase, trace=trace)
        data, code = {"ok": True, "phase": phase, "native": native}, 0
    except Exception as error:
        data, code = (
            {
                "ok": False,
                "phase": phase,
                "error": type(error).__name__ + ": " + str(error),
            },
            1,
        )
    event("reply_write_begin")
    with reply.open("x", encoding="utf-8") as stream:
        json.dump(data, stream, ensure_ascii=False, indent=2)
        stream.flush()
        os.fsync(stream.fileno())
    event("reply_written")
    return code
