"""Source-only in-memory OWN-TOY adapter for the identical fixed worker core.
Not packaged, not a general executor, never a production qualification grant.
"""

import argparse
from contextlib import contextmanager
from datetime import datetime, timezone
import http.client
import importlib
import json
import os
from pathlib import Path
import re
import secrets
import sys
import threading
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
n = importlib.import_module("hwp_contract_apply")
r = importlib.import_module("hwp_review")
fixture = importlib.import_module("native_fill_metadata_probe")
cell = importlib.import_module("native_cell_position_probe")
direct = importlib.import_module("native_direct_fill_probe")
fill = importlib.import_module("hwp_fill_plan")
form = importlib.import_module("hwp_form_draft")
project = importlib.import_module("hwp_project")
from native_blank_metadata_probe import hwp_inventory
from hwp_security import registered_security_module


class OwnToyExporter(n.NativeContractExporter):
    def __init__(self, workspace, source, proof, plan, report):
        self.source = source.resolve(strict=True)
        self.proof = proof
        self.plan = plan
        self.report = report
        self.source_sha = proof["reopened_observation"]["source_sha256"]
        self.baseline_sha = r.file_hash(workspace / "baseline.hwpx")
        super().__init__(workspace, r.plan_hash(plan))
        self._require_qualified(plan)

    def _require_qualified(self, plan):
        if (
            getattr(sys, "frozen", False)
            or self.workspace.parent.parent.parent != ROOT / "work"
            or not self.workspace.parent.parent.name.startswith("toy-fixed-fill-")
            or self.workspace.parent.name != "source"
            or self.workspace.name != project.REVIEW_DIR
            or self.source != self.workspace.parent / "TOY-ONLY-source.hwp"
        ):
            raise ValueError("Only this newly authored source-only fixture is admitted")
        direct.require_binding(self.source, self.proof)
        if (
            self.phase not in (0, 1)
            or plan != self.plan
            or plan["schema"] != 4
            or r.plan_hash(plan) != self.expected_hash
            or r.file_hash(self.source) != self.source_sha
            or r.file_hash(self.workspace / "baseline.hwpx") != self.baseline_sha
        ):
            raise ValueError("Immutable own source/plan/profile changed")
        manifest = r.verify_baseline(self.workspace)
        if (
            n.real_path(Path(manifest["source"])) != self.source
            or manifest["source_audit"]["sha256"] != self.source_sha
            or plan["record"]["values"] != direct.VALUES
        ):
            raise ValueError("Fixture source or fixed TEST values differ")
        selected = fill.validate(plan, manifest, self.workspace / "baseline.hwpx")
        if len(selected) != 3 or {
            tuple(sorted(i["cell_address"].items())) for i in selected
        } != {
            tuple(sorted({"rowAddr": str(row), "colAddr": "1"}.items()))
            for row in (1, 2, 3)
        }:
            raise ValueError("Only the own three empty fields are admitted")
        profile = importlib.import_module("hwp_native_cells").plain_profile(
            self.workspace / "baseline.hwpx", selected
        )
        if (profile["rows"], profile["cols"]) != (4, 2):
            raise ValueError("Own source must remain exactly4x2")

    @contextmanager
    def _session(self, workspace, run_id, phase):
        if workspace != self.workspace or phase != self.phase or hwp_inventory() != []:
            raise ValueError("Validation phase/owned idle context changed")
        event = {
            "phase": phase,
            "cleanup": "ownership_unconfirmed_owner_inspection_required",
            "owned_exit_confirmed": False,
        }
        self.report.setdefault("worker_sessions", []).append(event)
        completed = False
        try:
            with n._native_session(workspace, run_id, phase) as reference:
                event["ownership"] = reference.ownership
                yield reference
            completed = True
        except BaseException as error:
            event.update(error_type=type(error).__name__, error=str(error)[:1000])
            raise
        finally:
            try:
                event["after_hwp_processes"] = hwp_inventory()
            except BaseException as error:
                event["inventory_error"] = (
                    type(error).__name__ + ": " + str(error)[:500]
                )
            ownership = event.get("ownership", {})
            event["owned_exit_confirmed"] = (
                ownership.get("status") == "owned_exit_confirmed"
                and ownership.get("exit_confirmed") is True
                and event.get("after_hwp_processes") == []
            )
            event["cleanup"] = (
                "owned_exit_confirmed"
                if event["owned_exit_confirmed"]
                else "owned_exit_unconfirmed_owner_inspection_required"
            )
            if completed and not event["owned_exit_confirmed"]:
                raise RuntimeError("Validation owned exit unconfirmed; no retry")

    def _launch_worker(self, command, run, request):
        self._require_qualified(n.read_plan(run / "plan.json"))
        _, _, _, _, reply, _, _, bound, plan, _ = n.step_paths(
            self.workspace, run.name, self.phase
        )
        if bound != request or plan != self.plan:
            raise ValueError("Validation request differs from exact parent request")
        try:
            native = n._export_phase(
                self.workspace,
                run.name,
                self.phase,
                self._require_qualified,
                self._session,
            )
            data, code = {"ok": True, "native": native}, 0
        except Exception as error:
            data, code = (
                {"ok": False, "error": type(error).__name__ + ": " + str(error)},
                1,
            )
        data.update(
            schema=4,
            phase=self.phase,
            plan_sha256=request["plan_sha256"],
            approval_scope_sha256=request["approval_scope_sha256"],
        )
        with reply.open("x", encoding="utf-8") as stream:
            json.dump(data, stream, ensure_ascii=False, indent=2)
            stream.flush()
            os.fsync(stream.fileno())
        return SimpleNamespace(returncode=code)


def approve_fixture(workspace, plan, report):
    """Existing UI HTTP handler is the ONLY approval writer; case is newly owned."""
    server = r.create_review_server(workspace, text_apply=False, contract_apply=False)
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
            return response.status, response.read()
        finally:
            connection.close()

    try:
        token = re.search(r"const token='([a-f0-9]+)'", request("GET", "/")[1].decode())
        if token is None:
            raise ValueError("Fixture UI token missing")
        headers = {
            "Origin": origin,
            "Content-Type": "application/json",
            "X-Review-Token": token.group(1),
            "If-Match": '"' + r.file_hash(workspace / "proposal-fill.json") + '"',
        }
        payload = {
            "plan_sha256": r.plan_hash(plan),
            "proposal_kind": "fill",
            "fill_policy_confirmed": True,
            "unsigned_template_confirmed": True,
            "header_spine_confirmed": True,
            "fields_reviewed": [item["field_id"] for item in plan["placements"]],
        }
        status, body = request("POST", "/api/approve", payload, headers)
        if status != 200:
            raise ValueError(
                "Existing fixture approval handler rejected: " + body.decode()
            )
        report["approval_writer"] = "existing_local_review_http_handler"
        report["ui_approval_sha256"] = r.file_hash(workspace / "approved-plan.json")
    finally:
        server.shutdown()
        thread.join()
        server.server_close()


def run(output, *, run_native=False, toy_only=False):
    if run_native is not True or toy_only is not True or getattr(sys, "frozen", False):
        raise ValueError("Explicit SOURCE-only --run-native-test --toy-only required")
    folder = fixture.paths.real_path(output)
    if (
        folder.parent != ROOT / "work"
        or not folder.name.startswith("toy-fixed-fill-")
        or folder.exists()
    ):
        raise ValueError("Only a fresh direct owned toy-fixed-fill-* lab")
    if hwp_inventory() != []:
        raise RuntimeError("Existing Hwp process; no validation start")
    registered_security_module()
    folder.mkdir()
    report = {
        "kind": "own-TOY-source-fixed-worker-fill-validation",
        "status": "blocked",
        "qualification": False,
        "frozen_exe_used": False,
        "production_controller_enabled": False,
        "actual_report_used": False,
        "automatic_retries": 0,
        "owner_nonce": secrets.token_hex(16),
    }
    with (folder / "OWNER-TEST-ONLY.json").open("x", encoding="utf-8") as stream:
        json.dump(
            {
                "test_only": True,
                "purpose": report["kind"],
                "nonce": report["owner_nonce"],
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
        direct.require_binding(source, proof)
        workspace = source_dir / project.REVIEW_DIR
        prepare_exporter = project.BaselineExporter(workspace)
        r.prepare(source, workspace, exporter=prepare_exporter)
        if prepare_exporter.phase != 3:
            raise RuntimeError(
                "Incomplete owned baseline preparation; no proposal/approval/worker"
            )
        report.update(
            preparation_exporter="app_readonly_owned_baseline_exporter",
            preparation_phases=prepare_exporter.phase,
        )
        manifest = r.verify_baseline(workspace)
        regions = fill.empty_regions(workspace / "baseline.pdf")
        targets = direct.full.own_targets(
            form.inspect_template(workspace / "baseline.hwpx"), regions
        )
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
                for item, key, kind in zip(
                    targets, direct.VALUES, ("text", "integer", "date")
                )
            ],
        }
        record = {
            "values": direct.VALUES,
            "sources": {
                key: "FICTIONAL FIXED WORKER TEST ONLY" for key in direct.VALUES
            },
        }
        r.write_json(
            workspace / "fill-input.json", {"bindings": bindings, "record": record}
        )
        plan = fill.proposal(
            workspace,
            bindings,
            record,
            [
                {"field_id": item["field_id"], **region}
                for item, region in zip(targets, regions)
            ],
        )
        r.write_json(workspace / "proposal-fill.json", plan)
        exporter = OwnToyExporter(workspace, source, proof, plan, report)
        try:
            r.apply(
                workspace,
                workspace / "proposal-fill.json",
                expected_plan_sha256=r.plan_hash(plan),
                contract_exporter=exporter,
            )
        except FileNotFoundError as error:
            if (
                not isinstance(error.filename, str)
                or Path(error.filename).resolve() != workspace / "approved-plan.json"
            ):
                raise
        except ValueError:
            pass
        else:
            raise ValueError("Unapproved own fixture unexpectedly applied")
        if (workspace / r.APPLY_LOCK).exists() or exporter.phase != 0:
            raise ValueError("Unapproved case started Native or retained a lease")
        report["unapproved_blocked_before_worker"] = True
        approve_fixture(workspace, plan, report)
        hashes = {
            name: r.file_hash(workspace / name)
            for name in (
                "manifest.json",
                "baseline.hwpx",
                "baseline.pdf",
                "comments.json",
                "fill-input.json",
                "proposal-fill.json",
                "approved-plan.json",
            )
        }
        result = r.apply(
            workspace,
            workspace / "proposal-fill.json",
            expected_plan_sha256=r.plan_hash(plan),
            contract_exporter=exporter,
        )
        report["verification"] = result
        attempts = r.review_attempts(workspace, manifest)
        attempt = next(
            (a for a in attempts if a["plan_sha256"] == r.plan_hash(plan)), None
        )
        report["attempt"] = attempt
        if (
            exporter.phase != 2
            or not attempt
            or attempt["status"] != "checks_passed_needs_human_visual_review"
            or attempt["artifact_integrity"] != "verified"
            or (workspace / r.APPLY_LOCK).exists()
            or hwp_inventory() != []
        ):
            raise ValueError("Fixed source worker validation failed; no retry")
        if (
            hashes != {name: r.file_hash(workspace / name) for name in hashes}
            or r.file_hash(source) != proof["reopened_observation"]["source_sha256"]
        ):
            raise ValueError("Immutable inputs or UI approval changed")
        report.update(
            status="checks_passed_needs_human_visual_review",
            source_unchanged=True,
            immutable_inputs_unchanged=True,
            owned_processes_stopped=True,
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
        with (folder / "FIXED-FILL-VALIDATION.json").open(
            "x", encoding="utf-8"
        ) as stream:
            json.dump(report, stream, ensure_ascii=False, indent=2)
    return report


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run-native-test", action="store_true", required=True)
    parser.add_argument("--toy-only", action="store_true", required=True)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    folder = args.output or ROOT / "work" / (
        "toy-fixed-fill-"
        + datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        + "-"
        + secrets.token_hex(3)
    )
    report = run(folder, run_native=args.run_native_test, toy_only=args.toy_only)
    print(json.dumps(report, ensure_ascii=False, indent=2))
    sys.exit(1 if report["status"] == "blocked" else 0)
