"""Explicit preparation-only Native lab: NEW unsigned TOY, no real reports/approval.

Uses existing minimal toy authoring with READ-ONLY security registration instead
of installation. Existing full engine gates are never mocked/relaxed. A failure
stops this invocation and retains evidence; no retry, lease cleanup or PID kill.
"""

import argparse
from datetime import datetime, timezone
from pathlib import Path
import re
import sys
from types import SimpleNamespace
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
import hwp_project as p
import hwp_review as r
from hwp_security import registered_security_module
from hwp_text_apply import real_path
from native_blank_metadata_probe import hwp_inventory
from native_fill_smoke import create_source


def run(output, executable=None, *, run_native=False, toy_only=False):
    if run_native is not True or toy_only is not True or getattr(sys, "frozen", False):
        raise ValueError(
            "Literal SOURCE-only --run-native-test --toy-only opt-ins required"
        )
    output = real_path(output)
    if (
        output.parent != ROOT / "work"
        or output.exists()
        or not output.name.startswith("toy-desktop-")
    ):
        raise ValueError("Use a NEW work/toy-desktop-* folder only")
    registered_security_module()  # no COM/outputs until read-only check succeeds
    if executable is not None:
        executable = real_path(executable).resolve(strict=True)
        metadata = r.read_json(executable.parent / "BUILD.json")
        if (
            executable.name != "HWPReview.exe"
            or r.file_hash(executable) != metadata["executable_sha256"]
        ):
            raise ValueError("Only the exact audited candidate EXE may be tested")
        import build_exe

        for field, allowed in (
            ("source_sha256", build_exe.SOURCE_FILES),
            ("assets_sha256", build_exe.ASSETS),
        ):
            captured = metadata.get(field)
            if not isinstance(captured, dict) or set(captured) != set(allowed):
                raise ValueError(
                    "Candidate must bind the exact allowed source/asset set"
                )
            for name, digest in captured.items():
                if (
                    not isinstance(digest, str)
                    or re.fullmatch("[a-f0-9]{64}", digest) is None
                    or r.file_hash(real_path(ROOT / name)) != digest
                ):
                    raise ValueError(
                        "Current preparation source/assets differ from candidate"
                    )
    if hwp_inventory() != []:
        raise RuntimeError("Existing or unverified Hwp processes; no preparation trial")
    output.mkdir()
    report = {
        "test_only_not_deliverable": True,
        "status": "blocked",
        "human_visual_review_required": True,
        "executable": str(executable) if executable else "source",
        "events": [],
        "executable_sha256": r.file_hash(executable) if executable else None,
    }
    try:
        # Authoring creates an entirely NEW owned unsigned test source. It does
        # not open/derive from any real report, and never installs/registers HKCU.
        source = create_source(output / "source")
        before = r.file_hash(source)
        report["source_sha256_before"] = before
        folder = output / "작업 폴더"
        folder.mkdir()
        imported = p.import_hwp(folder, source)
        if imported.source is None:
            raise ValueError(
                "Fresh imported TOY source is missing; no Native preparation"
            )

        def progress(step, message):
            report["events"].append({"step": step, "message": message})
            print(step, message, flush=True)

        if executable is None:
            project = p.prepare_project(folder, progress=progress)
        else:
            # ONLY select the frozen worker executable; all source, Native COM,
            # roundtrip/format/text/binary gates and replies remain REAL.
            with (
                patch.object(p, "frozen", return_value=True),
                patch.object(p, "sys", SimpleNamespace(executable=str(executable))),
            ):
                project = p.prepare_project(folder, progress=progress)
        manifest = r.verify_baseline(project.workspace)
        verification = r.read_json(project.workspace / "roundtrip-verification.json")
        source_unchanged = r.file_hash(source) == before == r.file_hash(imported.source)
        if (
            not source_unchanged
            or not verification["passed"]
            or not manifest["roundtrip_passed"]
        ):
            raise ValueError("Actual preparation gates failed; no retry or relaxation")
        if (project.workspace / "approved-plan.json").exists() or r.apply_state(
            project.workspace, manifest
        )["locked"]:
            raise ValueError(
                "Preparation lab must not approve/apply or acquire an edit lease"
            )
        report.update(
            status="checks_passed_needs_human_visual_review",
            source_unchanged=True,
            pages=len(manifest["pages"]),
            objects=manifest["objects"],
            workspace=str(project.workspace),
            verification=verification,
            no_approval_or_apply=True,
        )
    except Exception as error:
        report["error"] = type(error).__name__ + ": " + str(error)
        raise
    finally:
        r.write_json(output / "TOY-ONLY-project-verification.json", report)
    return report


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run-native-test", action="store_true", required=True)
    parser.add_argument("--toy-only", action="store_true", required=True)
    parser.add_argument(
        "--output",
        type=Path,
        default=ROOT
        / "work"
        / ("toy-desktop-" + datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S")),
    )
    parser.add_argument("--executable", type=Path)
    args = parser.parse_args()
    result = run(
        args.output,
        args.executable,
        run_native=args.run_native_test,
        toy_only=args.toy_only,
    )
    print(
        "PASS: actual preparation ONLY, no edits/approvals; needs human visual review;",
        result["workspace"],
    )
