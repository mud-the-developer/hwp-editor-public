"""Explicit NEW unsigned TOY-only bounded text apply lab. NOT real-report approval.

Creates its own fictional source; only synthetic test consent via the real local
handler. Actual structure/pixel/text/binary/source/reopen gates are NEVER mocked.
Any unexpected failure stops and retains evidence/lease. No retry/cleanup/kill.
"""

import argparse
from datetime import datetime, timezone
import http.client
import importlib
import json
from pathlib import Path
import re
import sys
import threading
import time
from types import SimpleNamespace
from typing import Any, TYPE_CHECKING, cast
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
if TYPE_CHECKING:
    from scripts import hwp_project as p, hwp_review as r, hwp_text_apply as a
else:
    # Keep canonical runtime identities: runner-only patches must affect the
    # SAME modules used by the real engine, never a namespaced duplicate.
    p = importlib.import_module("hwp_project")
    r = importlib.import_module("hwp_review")
    a = importlib.import_module("hwp_text_apply")
security = importlib.import_module("hwp_security")
MODULE_ALIAS, MODULE_TYPE = security.MODULE_ALIAS, security.MODULE_TYPE
registered_security_module = security.registered_security_module
from hwp_native_owner import owned_session
from native_blank_metadata_probe import hwp_inventory

MARKER = "TOY ONLY - UNSIGNED TEXT APPLY TEST - NOT FOR DELIVERY"
OLD = "Fixture code: AAAAAAAA"
NEW = "Fixture code: BBBBBBBB"


def create_source(directory):
    directory = a.real_path(directory)
    if directory.exists():
        raise FileExistsError("New TOY source directory required")
    if hwp_inventory() != []:
        raise RuntimeError("Existing Hwp state prevents new TOY source creation")
    module = registered_security_module()
    directory.mkdir()
    seed = directory / "TOY-ONLY-authoring-seed.hwp"
    source = directory / "TOY-ONLY-unsigned-text.hwp"
    proof: dict[str, Any] = {"status": "blocked", "qualification": False, "events": []}
    try:
        with owned_session((seed, source)) as app:
            proof["ownership"] = app.ownership
            if not app.RegisterModule(MODULE_TYPE, MODULE_ALIAS):
                raise RuntimeError("Existing approved module rejected")
            proof["security_module"] = {**module, "registered": True}
            if seed.exists():
                raise FileExistsError("TOY seed exists; no overwrite")
            if (
                not app.SaveAs(str(seed), "HWP", "")
                or not seed.is_file()
                or not seed.stat().st_size
            ):
                raise RuntimeError("TOY seed save failed")
            proof["events"].append("blank_seed_saved")
            shape = text = None
            try:
                shape = app.HParameterSet.HCharShape
                app.HAction.GetDefault("CharShape", shape.HSet)
                shape.Height = 1000
                shape.FaceNameLatin = "Courier New"
                if not app.HAction.Execute("CharShape", shape.HSet):
                    raise RuntimeError("TOY font creation failed")
                for index, value in enumerate(
                    (
                        MARKER,
                        "Fictional equal-length monospace code. No seals, signatures, tables or images.",
                        OLD,
                    )
                ):
                    if index:
                        app.Run("BreakPara")
                    text = app.HParameterSet.HInsertText
                    app.HAction.GetDefault("InsertText", text.HSet)
                    text.Text = value
                    if not app.HAction.Execute("InsertText", text.HSet):
                        raise RuntimeError("TOY text creation failed")
            finally:
                shape = text = (
                    None  # Release child COM references before owned exit wait.
                )
            if source.exists():
                raise FileExistsError("TOY source exists; no overwrite")
            if (
                not app.SaveAs(str(source), "HWP", "")
                or not source.is_file()
                or not source.stat().st_size
            ):
                raise RuntimeError("TOY save failed")
            proof["events"].append("source_saved")
            proof["source_sha256"] = r.file_hash(source)
        proof["status"] = "owned_source_created_not_document_qualified"
        return source
    except BaseException as error:
        proof["error"] = type(error).__name__ + ": " + str(error)[:1000]
        raise
    finally:
        with (directory / "TOY-SOURCE-CREATION.json").open(
            "x", encoding="utf-8"
        ) as stream:
            json.dump(proof, stream, ensure_ascii=False, indent=2)


def qualification_preflight(executable):
    """Read-only exact candidate binding, never execution or consent authority."""
    if getattr(sys, "frozen", False):
        raise ValueError("SOURCE-only text qualification preflight required")
    build = importlib.import_module("build_exe")
    executable = a.real_path(executable).resolve(strict=True)
    metadata = r.read_json(executable.parent / "BUILD.json")
    if (
        executable.name != "HWPReview.exe"
        or r.file_hash(executable) != metadata["executable_sha256"]
    ):
        raise ValueError("Only the exact audited candidate EXE may be tested")
    for field, allowed in (
        ("source_sha256", build.SOURCE_FILES),
        ("assets_sha256", build.ASSETS),
    ):
        captured = metadata.get(field)
        if not isinstance(captured, dict) or set(captured) != set(allowed):
            raise ValueError("Candidate must bind the exact allowed source/asset set")
        for name, digest in captured.items():
            if (
                not isinstance(digest, str)
                or re.fullmatch("[a-f0-9]{64}", digest) is None
                or r.file_hash(a.real_path(ROOT / name)) != digest
            ):
                raise ValueError(
                    "Current text parent source/assets differ from candidate"
                )
    return {
        "executable_sha256": r.file_hash(executable),
        "source_and_assets_match": True,
        "native_started": False,
        "trial_authorized": False,
    }


def run(output, executable=None, *, run_native=False, toy_only=False):
    if run_native is not True or toy_only is not True or getattr(sys, "frozen", False):
        raise ValueError(
            "Literal SOURCE-only --run-native-test --toy-only opt-ins required"
        )
    output = a.real_path(output)
    if (
        output.parent != ROOT / "work"
        or output.exists()
        or not output.name.startswith("toy-text-apply-")
    ):
        raise ValueError("Use a NEW direct owned work/toy-text-apply-* folder only")
    if executable is not None:
        executable = a.real_path(executable).resolve(strict=True)
        qualification_preflight(executable)
    registered_security_module()
    if hwp_inventory() != []:
        raise RuntimeError("Existing or unverified Hwp processes; no text trial")
    output.mkdir()
    report = {
        "test_only_not_deliverable": True,
        "status": "blocked",
        "human_visual_review_required": True,
        "actual_parent_engine_and_http": True,
        "actual_worker": "frozen" if executable else "source",
        "test_consent_only_new_unsigned_fixture": True,
        "real_report_modified": False,
        "executable_sha256": r.file_hash(executable) if executable else None,
        "events": [],
    }
    server = thread = None
    try:
        source = create_source(output / "source")
        report["source_sha256_before"] = r.file_hash(source)
        project_root = output / "작업 폴더"
        project_root.mkdir()
        project = p.import_hwp(project_root, source)
        copied_source = project.source
        if copied_source is None:
            raise ValueError("TOY import did not capture its exact source copy")

        def progress(stage, message):
            report["events"].append({"stage": stage, "message": message})
            print(stage, message, flush=True)

        if executable:
            with (
                patch.object(p, "frozen", return_value=True),
                patch.object(p, "sys", SimpleNamespace(executable=str(executable))),
            ):
                project = p.prepare_project(project_root, progress=progress)
        else:
            project = p.prepare_project(project_root, progress=progress)
        w = project.workspace
        m = r.verify_baseline(w)
        with r.fitz.open(w / "baseline.pdf") as pdf:
            text = "\n".join(cast(str, pdf[i].get_text()) for i in range(len(pdf)))
            if MARKER not in text or OLD not in text or len(pdf) != 1:
                raise ValueError("Unsigned lab source marker/size differs")
        if (
            m["protected_pages"]
            or m["objects"].get("pic", 0)
            or m["objects"].get("tbl", 0)
        ):
            raise ValueError("This lab must be a NEW simple seal/table/image-free TOY")
        anchors = [anchor for anchor in m["anchors"] if anchor["old_text"] == OLD]
        if len(anchors) != 1 or not anchors[0]["simple"] or not anchors[0]["boxes"]:
            raise ValueError("TOY exact target not uniquely mapped")
        anchor = anchors[0]
        server = r.create_review_server(w, text_apply=True)
        thread = threading.Thread(target=server.serve_forever)
        thread.start()
        origin = f"http://127.0.0.1:{server.server_port}"

        def request(method, path, payload=None, headers=None):
            connection = http.client.HTTPConnection(
                "127.0.0.1", server.server_port, timeout=30
            )
            try:
                connection.request(
                    method,
                    path,
                    json.dumps(payload) if payload is not None else None,
                    headers or {},
                )
                response = connection.getresponse()
                body = response.read()
                return response.status, body
            finally:
                connection.close()

        match = re.search(r"const token='([a-f0-9]+)'", request("GET", "/")[1].decode())
        if match is None:
            raise ValueError("Exact TOY UI token missing")
        token = match.group(1)
        headers = {
            "Origin": origin,
            "X-Review-Token": token,
            "Content-Type": "application/json",
            "If-Match": '"absent"',
        }
        payload = {
            "workspace_id": m["workspace_id"],
            "baseline_hwpx_sha256": m["baseline_hwpx_sha256"],
            "edits": [
                {"anchor_id": anchor["anchor_id"], "old_text": OLD, "new_text": NEW}
            ],
        }
        status, body = request("POST", "/api/text-proposal", payload, headers)
        if status != 200:
            raise ValueError("TOY strict proposal rejected: " + body.decode())
        plan = json.loads(body)["proposal"]
        sha = r.plan_hash(plan)
        headers["If-Match"] = '"' + r.file_hash(w / "proposal.json") + '"'
        apply = {
            "workspace_id": m["workspace_id"],
            "baseline_hwpx_sha256": m["baseline_hwpx_sha256"],
            "plan_sha256": sha,
            "text_apply_confirmed": True,
        }
        if (
            request("POST", "/api/apply-text", apply, headers)[0] != 400
            or (w / "runs").exists()
        ):
            raise ValueError("Unapproved TOY must not launch Native/apply")
        report["unapproved_blocked_before_native"] = True
        # Synthetic consent is EXCLUSIVELY for this newly authored toy fixture,
        # using the actual UI handler. NEVER writes/forges an approval file here.
        if request("POST", "/api/approve", {"plan_sha256": sha}, headers)[0] != 200:
            raise ValueError("Synthetic TOY local-handler test consent rejected")
        approval_before = r.file_hash(w / "approved-plan.json")
        if (
            request(
                "POST", "/api/apply-text", {**apply, "plan_sha256": "0" * 64}, headers
            )[0]
            != 400
        ):
            raise ValueError("Stale hash accepted")
        report["stale_plan_blocked_before_native"] = True
        hashes = {
            name: r.file_hash(w / name)
            for name in (
                "baseline.hwpx",
                "baseline.pdf",
                "manifest.json",
                "comments.json",
                "proposal.json",
                "approved-plan.json",
            )
        }

        def start_and_wait():
            status, body = request("POST", "/api/apply-text", apply, headers)
            if status != 202:
                raise ValueError("TOY start failed: " + body.decode())
            deadline = time.monotonic() + 600
            while time.monotonic() < deadline:
                code, raw = request("GET", "/api/state")
                if code != 200:
                    raise ValueError("TOY read-only state failed")
                state = json.loads(raw)
                if not state["text_apply"]["active"]:
                    return state
                time.sleep(0.3)
            raise TimeoutError(
                "TOY apply did not finish. Preserve ownership; no cancel/kill/retry"
            )

        if executable:
            # Runner choice only. All approval/lease/source/package/render gates
            # and parent engine are actual, and match the EXE source snapshot.
            with (
                patch.object(a, "frozen", return_value=True),
                patch.object(a, "sys", SimpleNamespace(executable=str(executable))),
            ):
                state = start_and_wait()
        else:
            state = start_and_wait()
        report.update(
            workspace=str(w),
            plan_sha256=sha,
            text_apply=state["text_apply"],
            latest_attempt=state["latest_attempt"],
            apply_state=state["apply_state"],
        )
        attempt = state["latest_attempt"]
        if (
            state["text_apply"]["status"] != "finished"
            or not attempt
            or attempt["status"] != "checks_passed_needs_human_visual_review"
            or attempt["artifact_integrity"] != "verified"
            or state["apply_state"]["locked"]
        ):
            raise ValueError(
                "Actual TOY bounded apply gates failed; no retry/relaxation"
            )
        run = w / "runs" / attempt["id"]
        report["verification"] = r.read_json(run / "verification.json")
        if hashes != {
            name: r.file_hash(w / name) for name in hashes
        } or approval_before != r.file_hash(w / "approved-plan.json"):
            raise ValueError("Immutable TOY inputs or exact test consent changed")
        if (
            r.file_hash(source) != report["source_sha256_before"]
            or r.file_hash(copied_source) != report["source_sha256_before"]
        ):
            raise ValueError("TOY source changed")
        if request("POST", "/api/apply-text", apply, headers)[0] != 400:
            raise ValueError("Duplicate completed plan accepted")
        for kind, name in r.DOWNLOAD_FILES.items():
            status, blob = request(
                "GET",
                f"/result/{attempt['id']}/{kind}",
                headers={"X-Review-Token": token},
            )
            if status != 200 or r.digest(blob) != r.file_hash(run / name):
                raise ValueError("Actual TOY read-only result download differs")
        if hashes != {name: r.file_hash(w / name) for name in hashes}:
            raise ValueError("Read-only download altered TOY inputs")
        report["download_bytes_verified"] = True
        report.update(
            status="checks_passed_needs_human_visual_review",
            source_unchanged=True,
            approval_and_baselines_unchanged=True,
            duplicate_blocked=True,
            run=str(run),
        )
        print(
            "PASS: actual strict text apply on NEW unsigned TOY ONLY", run, flush=True
        )
    except Exception as error:
        report["error"] = type(error).__name__ + ": " + str(error)
        raise
    finally:
        if server is not None:
            # A still-active worker is NOT killed/cleared to make shutdown pass.
            if not getattr(server, "text_apply_controller").snapshot()["active"]:
                server.shutdown()
                if thread is not None:
                    thread.join(10)
                server.server_close()
        r.write_json(output / "TOY-ONLY-text-apply-verification.json", report)
    return report


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run-native-test", action="store_true", required=True)
    parser.add_argument("--toy-only", action="store_true", required=True)
    parser.add_argument("--executable", type=Path)
    parser.add_argument(
        "--output",
        type=Path,
        default=ROOT
        / "work"
        / ("toy-text-apply-" + datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S")),
    )
    args = parser.parse_args()
    run(
        args.output,
        args.executable,
        run_native=args.run_native_test,
        toy_only=args.toy_only,
    )
