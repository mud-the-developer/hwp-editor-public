"""OPT-IN exact frozen contract qualification: NEW owned unsigned TOY only.

NEVER writes approval directly. Actual local UI handler provides TEST consent for
this synthetic fixture; actual fixed EXE exports/engine/hash/render gates apply.
One family/run, failures STOP and preserve evidence/lease. No original reports.
"""

import argparse
from datetime import datetime, timezone
import http.client
import importlib
import json
from pathlib import Path
import re
import shutil
import sys
import threading
import time
from types import SimpleNamespace
from typing import Any
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
r = importlib.import_module("hwp_review")
p = importlib.import_module("hwp_project")
n = importlib.import_module("hwp_contract_apply")
security = importlib.import_module("hwp_security")
from hwp_native_owner import owned_session
from native_blank_metadata_probe import hwp_inventory

MARKER = "TOY ONLY - UNSIGNED CONTRACT QUALIFICATION - NOT FOR DELIVERY"
OLD = "Fixture code: AAAAAAAA"
NEW = "Fixture code: BBBBBBBB"
# Schema3 applies only to an originally multiline simple-table paragraph.
TABLE_OLD = "Fixture original long code: AAAAAAAA AAAAAAAA AAAAAAAA."
# Original, seal/image-free synthetic sources from completed schema3/4 test cases.
REFERENCE_TOY_SHA256 = {
    3: "2f1f92c1cf68970a8060bb87605dc60311b35ab201416fd20bf15f6a9cf5c399",
    4: "0125d779730aea9e072193e53f851b4cd3d800988030c1a0b85d2e09d1f2817f",
}
REFERENCE_FILL_MARKER = "TOY ONLY - UNSIGNED HWPX METADATA STAGE A - NOT FOR DELIVERY"


def reference_toy(path, schema):
    """Read-only exact synthetic source check; never Native or execution authority."""
    if (
        getattr(sys, "frozen", False)
        or type(schema) is not int
        or schema not in REFERENCE_TOY_SHA256
    ):
        raise ValueError("Reference TOY requires SOURCE schema3/4")
    source = n.real_path(path).resolve(strict=True)
    if (
        not source.is_file()
        or source.suffix.lower() != ".hwp"
        or source.stat().st_size > 1024 * 1024
        or r.file_hash(source) != REFERENCE_TOY_SHA256[schema]
    ):
        raise ValueError("Only the fixed prior-success synthetic source SHA is allowed")
    return source


def copy_reference_toy(path, directory, schema):
    source = reference_toy(path, schema)
    directory = n.real_path(directory)
    lab = directory.parent.resolve(strict=True)
    if (
        directory.name != "source"
        or lab.parent != ROOT / "work"
        or not lab.name.startswith(("toy-contract-", "toy-gui-native-"))
        or source.is_relative_to(lab)
    ):
        raise ValueError("New direct owned TOY source directory only")
    directory.mkdir()
    target = directory / "TOY-ONLY-reference-source.hwp"
    with source.open("rb") as original, target.open("xb") as copied:
        shutil.copyfileobj(original, copied, length=1024 * 1024)
    if (
        r.file_hash(target) != REFERENCE_TOY_SHA256[schema]
        or reference_toy(source, schema) != source
    ):
        raise ValueError("Reference TOY changed during exclusive copy; no repair/retry")
    return target


def fill_input(workspace):
    """Fixed TEST data only; real template IDs/regions, never plan/consent/positions."""
    mapped = importlib.import_module("hwp_form_draft").inspect_template(
        workspace / "baseline.hwpx"
    )
    targets = [field for field in mapped["fields"] if field["supported"]]
    if (
        len(targets) != 3
        or len(r.hwp_fill_plan.empty_regions(workspace / "baseline.pdf")) != 3
    ):
        raise ValueError("Exact three TOY blanks required")
    return {
        "bindings": {
            "schema": "hwp-fill-bindings/v1",
            "fields": [
                {
                    "field_id": field["field_id"],
                    "key": key,
                    "kind": kind,
                    "required": True,
                    "max_utf16_units": 30,
                    "max_lines": 1,
                }
                for field, key, kind in zip(
                    targets, ("Name", "Code", "Date"), ("text", "integer", "date")
                )
            ],
        },
        "record": {
            "values": {"Name": "Fixture User", "Code": "7", "Date": "2026-10-13"},
            "sources": {key: "FICTIONAL TEST ONLY" for key in ("Name", "Code", "Date")},
        },
    }


def create_source(directory, schema):
    directory = n.real_path(directory)
    if directory.exists():
        raise FileExistsError("New TOY source directory required")
    if hwp_inventory() != []:
        raise RuntimeError("Existing Hwp state prevents new TOY source creation")
    module = security.registered_security_module()  # READ ONLY; never installs.
    directory.mkdir()
    seed = directory / "TOY-ONLY-authoring-seed.hwp"
    source = directory / "TOY-ONLY-unsigned-contract.hwp"
    proof: dict[str, Any] = {"status": "blocked", "qualification": False, "events": []}
    try:
        with owned_session((seed, source)) as app:
            proof["ownership"] = app.ownership
            if not app.RegisterModule(security.MODULE_TYPE, security.MODULE_ALIAS):
                raise RuntimeError("Existing module rejected")
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

            def text(value):
                param = app.HParameterSet.HInsertText
                try:
                    app.HAction.GetDefault("InsertText", param.HSet)
                    param.Text = value
                    if not app.HAction.Execute("InsertText", param.HSet):
                        raise RuntimeError("TOY literal creation failed")
                finally:
                    param = None

            char = table = None
            try:
                char = app.HParameterSet.HCharShape
                app.HAction.GetDefault("CharShape", char.HSet)
                char.Height = 1000
                char.FaceNameLatin = "Courier New"
                if not app.HAction.Execute("CharShape", char.HSet):
                    raise RuntimeError("TOY font creation failed")
                text(MARKER)
                app.Run("BreakPara")
                text("FICTIONAL ONLY; own source; no seals/signatures/images.")
                app.Run("BreakPara")
                if schema == 2:
                    text(OLD)
                else:
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
                        raise RuntimeError("TOY table creation failed")
                    for index, label in enumerate(
                        ("TEST LABEL", "Name", "Code", "Date")
                    ):
                        text(label)
                        app.Run("TableRightCell")
                        if index == 0:
                            text("TEST INPUT CELLS BELOW")
                        if schema == 3 and index == 1:
                            text(TABLE_OLD)
                        if index < 3:
                            app.Run("TableLeftCell")
                            app.Run("TableLowerCell")
            finally:
                char = table = (
                    None  # Release child COM references before owned exit wait.
                )
            if source.exists():
                raise FileExistsError("TOY source exists; no overwrite")
            if (
                not app.SaveAs(str(source), "HWP", "")
                or not source.is_file()
                or not source.stat().st_size
            ):
                raise RuntimeError("TOY source creation failed")
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


def qualification_preflight(executable, schema):
    """Read-only exact candidate check; NEVER execution/consent/qualification authority."""
    if (
        getattr(sys, "frozen", False)
        or type(schema) is not int
        or schema not in (2, 3, 4)
    ):
        raise ValueError("SOURCE-only supported contract schema required")
    n.require_qualified({"schema": schema})
    build = importlib.import_module("build_exe")
    executable = n.real_path(executable).resolve(strict=True)
    metadata = r.read_json(executable.parent / "BUILD.json")
    if (
        executable.name != "HWPReview.exe"
        or r.file_hash(executable) != metadata["executable_sha256"]
    ):
        raise ValueError("Exact candidate EXE required")
    sources = metadata.get("source_sha256")
    assets = metadata.get("assets_sha256")
    if (
        not isinstance(sources, dict)
        or set(sources) != set(build.SOURCE_FILES)
        or not isinstance(assets, dict)
        or set(assets) != set(build.ASSETS)
    ):
        raise ValueError(
            "Candidate snapshot must contain the exact allowed source/asset set"
        )
    for name, sha in {**sources, **assets}.items():
        if (
            not isinstance(sha, str)
            or re.fullmatch("[a-f0-9]{64}", sha) is None
            or r.file_hash(n.real_path(ROOT / name)) != sha
        ):
            raise ValueError("Parent differs from candidate snapshot: " + name)
    return {
        "schema": schema,
        "executable_sha256": r.file_hash(executable),
        "source_and_assets_match": True,
        "production_schema_qualified": True,
        "native_started": False,
        "trial_authorized": False,
    }


def run(
    output,
    executable,
    schema,
    *,
    run_native=False,
    toy_only=False,
    reference_toy_source=None,
):
    if run_native is not True or toy_only is not True or getattr(sys, "frozen", False):
        raise ValueError("Explicit SOURCE-only --run-native-test --toy-only required")
    # Deny unqualified families BEFORE paths, security, lab creation or Native.
    preflight = qualification_preflight(executable, schema)
    output = n.real_path(output)
    executable = n.real_path(executable).resolve(strict=True)
    if (
        output.parent != ROOT / "work"
        or output.exists()
        or not output.name.startswith("toy-contract-")
        or schema not in (2, 3, 4)
    ):
        raise ValueError("Fresh explicitly owned TOY contract lab only")
    reference = (
        reference_toy(reference_toy_source, schema)
        if reference_toy_source is not None
        else None
    )
    security.registered_security_module()
    output.mkdir()
    report = {
        "test_only_not_deliverable": True,
        "status": "blocked",
        "schema": schema,
        "actual_parent_engine_and_http": True,
        "actual_worker": "frozen",
        "executable_sha256": preflight["executable_sha256"],
        "test_consent_only_new_unsigned_fixture": True,
        "human_visual_review_required": True,
        "real_report_modified": False,
        "source_reference_copy": reference is not None,
        "source_fixture_kind": "exclusive_SHA_pinned_synthetic_reference_copy"
        if reference
        else "new_native_authored_synthetic",
    }
    server = thread = None
    try:
        source = (
            copy_reference_toy(reference, output / "source", schema)
            if reference is not None
            else create_source(output / "source", schema)
        )
        report["source_sha256_before"] = r.file_hash(source)
        project_root = output / "owned-project"
        project_root.mkdir()
        project = p.import_hwp(project_root, source)
        with (
            patch.object(p, "frozen", return_value=True),
            patch.object(p, "sys", SimpleNamespace(executable=str(executable))),
        ):
            project = p.prepare_project(project_root)
        w = project.workspace
        manifest = r.verify_baseline(w)
        report["workspace"] = str(w)
        r.hwp_fixed_layout.require_native_profile(w / "baseline.hwpx")
        report["fresh_baseline_reading_order_eligible"] = True
        marker = (
            REFERENCE_FILL_MARKER if reference is not None and schema == 4 else MARKER
        )
        with r.fitz.open(w / "baseline.pdf") as pdf:
            if len(pdf) != 1 or marker not in "".join(page.get_text() for page in pdf):
                raise ValueError("Exact TOY marker/size mismatch")
        if manifest["protected_pages"] or manifest["objects"].get("pic", 0):
            raise ValueError("Only the new unsigned seal/image-free TOY allowed")
        kind = {2: "native", 3: "layout", 4: "fill"}[schema]
        if schema in (2, 3):
            old = TABLE_OLD if schema == 3 else OLD
            targets = [a for a in manifest["anchors"] if a["old_text"] == old]
            if len(targets) != 1 or not targets[0]["simple"] or not targets[0]["boxes"]:
                raise ValueError("Unique simple TOY target missing")
            legacy = {
                "schema": 1,
                "workspace_id": manifest["workspace_id"],
                "baseline_hwpx_sha256": manifest["baseline_hwpx_sha256"],
                "edits": [
                    {
                        "anchor_id": targets[0]["anchor_id"],
                        "old_text": old,
                        "new_text": NEW,
                    }
                ],
            }
            plan = r.hwp_native_plan.proposal(legacy, manifest, w / "baseline.hwpx")
            if schema == 3:
                plan = r.hwp_fixed_layout.proposal(plan, manifest, w / "baseline.hwpx")
        else:
            data = fill_input(w)
            bindings, record = data["bindings"], data["record"]
            targets = bindings["fields"]
            regions = r.hwp_fill_plan.empty_regions(w / "baseline.pdf")
            r.write_json(
                w / "fill-input.json", {"bindings": bindings, "record": record}
            )
            plan = r.hwp_fill_plan.proposal(
                w,
                bindings,
                record,
                [
                    {"field_id": f["field_id"], **region}
                    for f, region in zip(targets, regions)
                ],
            )
        r.validate_plan(plan, manifest, w / "baseline.hwpx")
        r.write_json(w / n.PROPOSALS[schema], plan)
        sha = r.plan_hash(plan)
        report["plan_sha256"] = sha
        server = r.create_review_server(w, text_apply=True, contract_apply=True)
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

        token_match = re.search(
            r"const token='([a-f0-9]+)'", request("GET", "/")[1].decode()
        )
        if token_match is None:
            raise ValueError("TOY UI token missing")
        headers = {
            "Origin": origin,
            "Content-Type": "application/json",
            "X-Review-Token": token_match.group(1),
            "If-Match": '"' + r.file_hash(w / n.PROPOSALS[schema]) + '"',
        }
        payload = {
            "workspace_id": manifest["workspace_id"],
            "baseline_hwpx_sha256": manifest["baseline_hwpx_sha256"],
            "proposal_kind": kind,
            "plan_sha256": sha,
            "approval_scope_sha256": r.plan_hash(r.native_approval_scope(plan)),
            "contract_apply_confirmed": True,
        }
        if (
            request("POST", "/api/apply-contract", payload, headers)[0] != 400
            or (w / r.APPLY_LOCK).exists()
        ):
            raise ValueError("Unapproved TOY Native accepted")
        report["unapproved_blocked_before_native"] = True
        consent = {"plan_sha256": sha, "proposal_kind": kind}
        if schema in (2, 3):
            consent.update(
                native_policy_confirmed=True,
                regions_reviewed=[e["anchor_id"] for e in plan["edits"]],
            )
            if schema == 3:
                consent.update(row_minima_confirmed=True)
        else:
            consent.update(
                fill_policy_confirmed=True,
                unsigned_template_confirmed=True,
                header_spine_confirmed=True,
                fields_reviewed=[f["field_id"] for f in plan["placements"]],
            )
        if request("POST", "/api/approve", consent, headers)[0] != 200:
            raise ValueError("Exact TOY local-handler consent rejected")
        if (
            request(
                "POST",
                "/api/apply-contract",
                {**payload, "approval_scope_sha256": "0" * 64},
                headers,
            )[0]
            != 400
        ):
            raise ValueError("Stale policy accepted")
        report["stale_plan_blocked_before_native"] = True
        hashes = {
            name: r.file_hash(w / name)
            for name in (
                "manifest.json",
                "baseline.hwpx",
                "baseline.pdf",
                "comments.json",
                n.PROPOSALS[schema],
                "approved-plan.json",
            )
        }
        with (
            patch.object(n, "frozen", return_value=True),
            patch.object(n, "sys", SimpleNamespace(executable=str(executable))),
        ):
            code, body = request("POST", "/api/apply-contract", payload, headers)
            if code != 202:
                raise ValueError("TOY separate start failed: " + body.decode())
            deadline = time.monotonic() + 600
            job = getattr(server, "contract_apply_controller")
            while job.snapshot()["active"] and time.monotonic() < deadline:
                time.sleep(0.2)
            if job.snapshot()["active"]:
                raise TimeoutError("TOY owned job active; preserve, no kill/retry")
        report["contract_apply"] = job.snapshot()
        attempts = r.review_attempts(w, manifest)
        attempt = next((a for a in attempts if a["plan_sha256"] == sha), None)
        report["latest_attempt"] = attempt
        report["apply_state"] = r.apply_state(w, manifest)
        if (
            job.snapshot()["status"] != "finished"
            or not attempt
            or attempt["status"] != "checks_passed_needs_human_visual_review"
            or attempt["artifact_integrity"] != "verified"
            or report["apply_state"]["locked"]
        ):
            raise ValueError(
                "Actual fixed Native contract failed strict gates; no retry/relaxation"
            )
        run_dir = w / "runs" / attempt["id"]
        report["run"] = str(run_dir)
        report["verification"] = r.read_json(run_dir / "verification.json")
        if (
            hashes != {name: r.file_hash(w / name) for name in hashes}
            or r.file_hash(source) != report["source_sha256_before"]
        ):
            raise ValueError("TOY immutable inputs/source/consent changed")
        if request("POST", "/api/apply-contract", payload, headers)[0] != 400:
            raise ValueError("Duplicate completed Native plan accepted")
        for file_kind, name in r.DOWNLOAD_FILES.items():
            status, blob = request(
                "GET",
                f"/result/{attempt['id']}/{file_kind}",
                headers={"X-Review-Token": headers["X-Review-Token"]},
            )
            if status != 200 or r.digest(blob) != r.file_hash(run_dir / name):
                raise ValueError("Native contract result bytes unbound")
        report.update(
            status="checks_passed_needs_human_visual_review",
            source_unchanged=True,
            approval_and_baselines_unchanged=True,
            duplicate_blocked=True,
            download_bytes_verified=True,
        )
    except BaseException as error:
        report["error"] = type(error).__name__ + ": " + str(error)
        r.write_json(output / "TOY-ONLY-contract-verification.json", report)
        raise
    finally:
        if server is not None:
            getattr(server, "contract_apply_controller").require_idle()
            server.shutdown()
            if thread is not None:
                thread.join(5)
            server.server_close()
    r.write_json(output / "TOY-ONLY-contract-verification.json", report)
    return report


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--preflight",
        action="store_true",
        help="Read-only exact schema/candidate checks; does not authorize or run Native",
    )
    parser.add_argument("--run-native-test", action="store_true")
    parser.add_argument("--toy-only", action="store_true")
    parser.add_argument("--reference-toy-source", type=Path)
    parser.add_argument("--schema", type=int, choices=(2, 3, 4), required=True)
    parser.add_argument("--executable", type=Path, required=True)
    parser.add_argument(
        "--output",
        type=Path,
        default=ROOT
        / "work"
        / ("toy-contract-" + datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S")),
    )
    args = parser.parse_args()
    if args.preflight:
        if args.reference_toy_source is not None:
            raise ValueError(
                "Reference source belongs only to explicit Native TOY mode"
            )
        result = qualification_preflight(args.executable, args.schema)
    else:
        result = run(
            args.output,
            args.executable,
            args.schema,
            run_native=args.run_native_test,
            toy_only=args.toy_only,
            reference_toy_source=args.reference_toy_source,
        )
    print(json.dumps(result, ensure_ascii=False, indent=2))
