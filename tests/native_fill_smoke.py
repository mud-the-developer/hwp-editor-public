"""Explicit, NEW unsigned native TOY lab; never report apply or real UI approval.
This test owns its entire synthetic source. Failed native checks stop; no retries.
"""

from __future__ import annotations
import argparse
import json
from datetime import datetime, timezone
from pathlib import Path
import sys
import secrets
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
import hwp_review as r
import hwp_fill_plan as fill
import hwp_form_draft as f
from hwp_native_owner import owned_session
from hwp_security import MODULE_ALIAS, MODULE_TYPE, registered_security_module
from hwp_text_apply import real_path
import native_fill_metadata_probe as fixture
from native_blank_metadata_probe import hwp_inventory

MARKER = fixture.MARKER


def create_source(directory):
    directory = real_path(directory)
    if directory.exists():
        raise FileExistsError("New TOY source directory required")
    if hwp_inventory() != []:
        raise RuntimeError("Existing Hwp state prevents new TOY source creation")
    module = registered_security_module()  # READ ONLY; no installation/registry write.
    directory.mkdir(parents=True, exist_ok=False)
    seed = directory / "TOY-ONLY-authoring-seed.hwp"
    source = directory / "TOY-ONLY-empty-template.hwp"
    proof: dict[str, Any] = {"status": "blocked", "qualification": False, "events": []}
    try:
        with owned_session((seed, source)) as app:
            proof["ownership"] = app.ownership
            if not app.RegisterModule(MODULE_TYPE, MODULE_ALIAS):
                raise RuntimeError("Existing approved module rejected")
            proof["security_module"] = {**module, "registered": True}
            # Name the pristine blank before authoring; dirty unnamed docs stay forbidden.
            for path, authoring in ((seed, False), (source, True)):
                if authoring:
                    fixture.author(app)
                    fixture.native_pages(app)
                if path.exists():
                    raise FileExistsError("TOY output exists; no overwrite")
                if (
                    not app.SaveAs(str(path), "HWP", "")
                    or not path.is_file()
                    or not path.stat().st_size
                ):
                    raise RuntimeError("Own TOY HWP save failed; no retry")
                proof["events"].append(
                    "source_saved" if authoring else "blank_seed_saved"
                )
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


def own_targets(mapped, regions):
    fields = [item for item in mapped["fields"] if item["supported"]]
    if (
        len(fields) != 3
        or len(regions) != 3
        or any(region["page"] != 1 for region in regions)
        or len({(item["part"], tuple(item["table_path"])) for item in fields}) != 1
    ):
        raise ValueError("Expected only the one authored three-field table on one page")
    targets = []
    for row, key in enumerate(("Name", "Code", "Date"), 1):
        matches = [
            item
            for item in fields
            if item["cell_address"] == {"rowAddr": str(row), "colAddr": "1"}
            and key in item["label_hints"]
        ]
        if len(matches) != 1:
            raise ValueError("Missing/ambiguous explicitly authored field: " + key)
        targets.append(matches[0])
    return targets


def run(output, *, run_native=False, toy_only=False):
    if run_native is not True or toy_only is not True or getattr(sys, "frozen", False):
        raise ValueError(
            "Explicit source-only full HWP/PDF diagnostic consent required"
        )
    output = fixture.paths.real_path(output)
    if (
        output.parent != ROOT / "work"
        or output.exists()
        or not output.name.startswith("toy-fill-full-")
    ):
        raise ValueError(
            "Only a NEW direct work/toy-fill-full-* directory is permitted"
        )
    if hwp_inventory() != []:
        raise RuntimeError("Existing Hwp process; no Native test start")
    output.mkdir()
    report = {
        "test_only_not_deliverable": True,
        "native_status": "blocked",
        "human_visual_review_required": True,
        "qualification": False,
        "actual_report_used": False,
        "approval_record_used": False,
        "automatic_retries": 0,
        "stage": "source_creation",
    }
    try:
        source = create_source(output / "source")
        report["source_sha256_before"] = r.file_hash(source)
        report["stage"] = "no_edit_baseline"
        workspace = output / "review"
        r.prepare(source, workspace)
        manifest = r.verify_baseline(workspace)
        if len(manifest["pages"]) != 1:
            raise ValueError("Authored baseline is not one page; no page/font change")
        report["stage"] = "original_eligibility"
        _, spine = fixture.spine(workspace / "baseline.hwpx")
        if not fixture.has_precondition(spine, "preserve-generated-yes"):
            raise ValueError(
                "Original baseline is not eligible semantic-yes profile; no OPF repair"
            )
        mapped = f.inspect_template(workspace / "baseline.hwpx")
        regions = fill.empty_regions(workspace / "baseline.pdf")
        targets = own_targets(mapped, regions)
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
                    targets, ["Name", "Code", "Date"], ["text", "integer", "date"]
                )
            ],
        }
        record = {
            "values": {"Name": "Synthetic User", "Code": "7", "Date": "2026-10-10"},
            "sources": {
                key: "FICTIONAL TEST VALUE ONLY" for key in ["Name", "Code", "Date"]
            },
        }
        r.write_json(
            workspace / "fill-input.json", {"bindings": bindings, "record": record}
        )
        placements = [
            {"field_id": item["field_id"], **region}
            for item, region in zip(targets, regions)
        ]
        plan = fill.proposal(workspace, bindings, record, placements)
        selected = fill.validate(
            plan, r.verify_baseline(workspace), workspace / "baseline.hwpx"
        )
        r.write_json(workspace / "proposal-fill.json", plan)
        fixture.require_source_cache(workspace / "baseline.hwpx", selected)
        # Not apply/approval: the explicit lab writes ONLY its generated unsigned test input.
        report["stage"] = "exact_generated_input"
        candidate = output / "TOY-ONLY-filled-input.hwpx"
        fill.write_input(workspace / "baseline.hwpx", candidate, selected)
        hwp = output / "TOY-ONLY-filled.hwp"
        hwpx = output / "TOY-ONLY-native-filled.hwpx"
        pdf = output / "TOY-ONLY-reopened.pdf"
        report["stage"] = "initial_native_export"
        native = r.native_export(
            candidate, output / "TOY-ONLY-buffer.pdf", hwp=hwp, hwpx=hwpx
        )
        report["native_export"] = native
        report["stage"] = "initial_structural_gate"
        report["structural_gate"] = fill.structural_gate(
            workspace / "baseline.hwpx", hwpx, selected, plan["header_spine"]
        )
        immutable = {
            path.name: r.file_hash(path)
            for path in (candidate, hwp, hwpx, output / "TOY-ONLY-buffer.pdf")
        }
        report["stage"] = "saved_hwp_reopen"
        reopened = output / "TOY-ONLY-reopened.hwpx"
        report["reopen_export"] = r.native_export(hwp, pdf, hwpx=reopened)
        report["stage"] = "saved_hwp_structural_gate"
        report["saved_hwp_structural_gate"] = fill.structural_gate(
            workspace / "baseline.hwpx", reopened, selected, plan["header_spine"]
        )
        report["initial_artifacts_unchanged"] = all(
            r.file_hash(output / name) == sha for name, sha in immutable.items()
        )
        report["stage"] = "rendered_and_original_gates"
        report["layout_gate"] = r.compare_pdfs(
            workspace / "baseline.pdf", pdf, {1: [p["region"] for p in placements]}
        )
        report["rendered_page_text_exact"] = fill.rendered_text_gate(
            workspace / "baseline.pdf", pdf, selected
        )
        report["embedded_binaries_equal"] = (
            r.hwp_audit(source)["embedded_binary_hashes"]
            == r.hwp_audit(hwp)["embedded_binary_hashes"]
        )
        report["source_unchanged"] = report["source_sha256_before"] == r.file_hash(
            source
        )
        report["baseline_verified_again"] = (
            r.verify_baseline(workspace)["workspace_id"] == manifest["workspace_id"]
        )
        report["owned_processes_stopped"] = hwp_inventory() == []
        if not all(
            [
                report["layout_gate"]["passed"],
                report["rendered_page_text_exact"],
                report["embedded_binaries_equal"],
                report["source_unchanged"],
                report["initial_artifacts_unchanged"],
                report["baseline_verified_again"],
                report["owned_processes_stopped"],
            ]
        ):
            raise ValueError(
                "Native toy fill failed strict gates; no retry or layout relaxation"
            )
        if (workspace / "approved-plan.json").exists():
            raise ValueError("Toy lab must NEVER write a real approval")
        report["native_status"] = "checks_passed_needs_human_visual_review"
        report["stage"] = "checks_passed"
        report["review_workspace"] = str(workspace)
        report["reopened_pdf"] = str(pdf)
    except Exception as exc:
        report["error"] = str(exc)
        r.write_json(output / "TOY-ONLY-fill-verification.json", report)
        raise
    r.write_json(output / "TOY-ONLY-fill-verification.json", report)
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
        / (
            "toy-fill-full-"
            + datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S")
            + "-"
            + secrets.token_hex(3)
        ),
    )
    args = parser.parse_args()
    print(
        __import__("json").dumps(
            run(args.output, run_native=args.run_native_test, toy_only=args.toy_only),
            ensure_ascii=False,
            indent=2,
        )
    )
