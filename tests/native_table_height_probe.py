"""OPT-IN SOURCE-only height API calibration on ONE new unsigned literal TOY.

No production path/policy changes, HWPX input, cache repair, retry or process kill.
CellShape is the single fixed probe API; a mismatch aborts without fallback.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import shutil
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
import hwp_fixed_layout as fixed
import hwp_native_cells as cells
from hwp_native_buffer import (
    source_buffer_identity,
    parameter_snapshot as parameter_snapshot,
    raw_comparison as raw_comparison,
    set_bound_cell_minimum as set_height,
)
from hwp_native_owner import NativeReference as NativeReference, owned_session
import hwp_review as r
import hwp_security as security
import native_fill_metadata_probe as fixture
from native_blank_metadata_probe import hwp_inventory
from native_contract_smoke import TABLE_OLD
from hwp_text_apply import real_path


identity = source_buffer_identity

EXPECTED = (
    "TEST LABEL",
    "TEST INPUT CELLS BELOW",
    "Name",
    TABLE_OLD,
    "Code",
    "",
    "Date",
    "",
)


def read_only_security(app):
    info = security.registered_security_module()
    if not app.RegisterModule(security.MODULE_TYPE, security.MODULE_ALIAS):
        raise RuntimeError("Existing approved module rejected; no installation")
    return {**info, "registered": True}


def export_copy(source, pdf, hwpx):
    if pdf.exists() or hwpx.exists():
        raise FileExistsError("New calibration export outputs required")
    with owned_session((source, pdf, hwpx)) as app:
        module = read_only_security(app)
        if not app.Open(str(source), "HWP", "versionwarning:false"):
            raise RuntimeError("Known calibration HWP copy open failed")
        if type(app.PageCount) is not int or app.PageCount != 1:
            raise ValueError("One authored page required")
        for target, format in ((hwpx, "HWPX"), (pdf, "PDF")):
            if (
                not app.SaveAs(str(target), format, "")
                or not target.is_file()
                or target.stat().st_size == 0
            ):
                raise RuntimeError("Calibration export failed: " + format)
        proof = {
            "native_pages": 1,
            "security_module": module,
            "owned_session": app.ownership,
        }
    return proof


def create_source(directory, grid):
    directory.mkdir()
    seed, source = directory / "TOY-SEED.hwp", directory / "TOY-HEIGHT-SOURCE.hwp"
    with owned_session((seed, source)) as app:
        read_only_security(app)
        if not app.SaveAs(str(seed), "HWP", "") or not seed.is_file():
            raise RuntimeError("New calibration seed save failed")

        def insert(value):
            param = None
            try:
                param = app.HParameterSet.HInsertText
                app.HAction.GetDefault("InsertText", param.HSet)
                param.Text = value
                if not app.HAction.Execute("InsertText", param.HSet):
                    raise RuntimeError("TOY literal insertion failed")
            finally:
                param = None

        char = table = None
        try:
            char = app.HParameterSet.HCharShape
            app.HAction.GetDefault("CharShape", char.HSet)
            char.Height, char.FaceNameLatin = 1000, "Courier New"
            if not app.HAction.Execute("CharShape", char.HSet):
                raise RuntimeError("TOY font creation failed")
            insert(fixture.MARKER)
            if app.Run("BreakPara") is False:
                raise RuntimeError("TOY paragraph creation failed")
            table = app.HParameterSet.HTableCreation
            app.HAction.GetDefault("TableCreate", table.HSet)
            table.Rows, table.Cols = 4, 2
            table.WidthType, table.HeightType = 1, 1
            table.WidthValue, table.HeightValue = (
                app.MiliToHwpUnit(140),
                app.MiliToHwpUnit(48),
            )
            table.TableProperties.TreatAsChar = 1
            table.CreateItemArray("ColWidth", 2)
            table.ColWidth.SetItem(0, app.MiliToHwpUnit(45))
            table.ColWidth.SetItem(1, app.MiliToHwpUnit(95))
            if not app.HAction.Execute("TableCreate", table.HSet):
                raise RuntimeError("TOY table creation failed")
            for row in range(4):
                for col in range(2):
                    if col and app.Run("TableRightCell") is False:
                        raise RuntimeError("TOY next cell unavailable")
                    pos = cells.position(app.GetPos())
                    if pos[0] == 0 or pos[1:] != [0, 0] or pos in grid.values():
                        raise ValueError("Ambiguous authored cell")
                    grid[f"{row},{col}"] = pos
                    if EXPECTED[2 * row + col]:
                        insert(EXPECTED[2 * row + col])
                if row < 3:
                    for action in ("TableLeftCell", "TableLowerCell"):
                        if app.Run(action) is False:
                            raise RuntimeError("TOY row navigation failed")
        finally:
            char = table = None
        if (
            len(grid) != 8
            or not app.SaveAs(str(source), "HWP", "")
            or not source.is_file()
        ):
            raise RuntimeError("Complete source save failed")
        proof = app.ownership
    return source, proof


def pixel_gate(before, after):
    with r.fitz.open(before) as a, r.fitz.open(after) as b:
        if (
            len(a) != 1
            or len(b) != 1
            or a[0].rect != b[0].rect
            or a[0].get_text() != b[0].get_text()
        ):
            raise ValueError("Height-only calibration changed page/text")
        x = a[0].get_pixmap(dpi=144, alpha=False)
        y = b[0].get_pixmap(dpi=144, alpha=False)
        if (x.width, x.height, x.n) != (
            y.width,
            y.height,
            y.n,
        ) or x.samples != y.samples:
            raise ValueError("Height-only calibration changed rendered pixels")
    return {"dpi": 144, "pages": 1, "all_pixels_and_text_equal": True}


def run(output, *, run_native=False, toy_only=False):
    if run_native is not True or toy_only is not True or getattr(sys, "frozen", False):
        raise ValueError("Explicit SOURCE-only height calibration opt-in required")
    output = real_path(output)
    if (
        output.parent != ROOT / "work"
        or not output.name.startswith("toy-native-height-")
        or output.exists()
        or hwp_inventory()
    ):
        raise ValueError("New direct owned work lab and empty Hwp inventory required")
    security.registered_security_module()
    output.mkdir()
    report = {
        "status": "blocked",
        "test_only_not_deliverable": True,
        "purpose": "height-only API calibration, NOT text/prod/schema3 qualification",
        "production_changed": False,
        "automatic_retries": 0,
        "process_termination": False,
        "height_evidence": [],
        "height_attempt_diagnostics": [],
    }
    try:
        grid = {}
        source, report["author_owned_session"] = create_source(output / "source", grid)
        report["authored_positions"] = grid
        digest = r.file_hash(source)
        report["source_sha256_before"] = digest
        copy = output / "baseline-source-copy.hwp"
        shutil.copyfile(source, copy)
        baseline, pdf = output / "baseline.hwpx", output / "baseline.pdf"
        report["baseline_export"] = export_copy(copy, pdf, baseline)
        anchors, _ = r.index_hwpx(baseline)
        targets = [a for a in anchors if a["old_text"] == TABLE_OLD]
        if len(targets) != 1:
            raise ValueError("One exact multiline authored selection required")
        # Height-only selection, explicitly NO text edit or approval/admission artifact.
        selected = [
            {
                "anchor_id": targets[0]["anchor_id"],
                "old_text": TABLE_OLD,
                "new_text": TABLE_OLD,
            }
        ]
        minima = fixed.row_minima(baseline, selected)
        if len(minima) != 1 or len(minima[0]["row_heights"]) != 4:
            raise ValueError("Exact authored 4x2 minimum contract required")
        plan = {
            "schema": 3,
            "edit_mode": fixed.MODE,
            "native_policy": fixed.POLICY,
            "workspace_id": "TOY-HEIGHT-API-CALIBRATION",
            "baseline_hwpx_sha256": r.file_hash(baseline),
            "edits": selected,
            "row_minima": minima,
        }
        report["height_only_contract_not_approval"] = plan
        edit, candidate = output / "edit-source-copy.hwp", output / "candidate.hwp"
        shutil.copyfile(source, edit)
        edit_sha = r.file_hash(edit)
        with owned_session((edit, candidate)) as app:
            read_only_security(app)
            if not app.Open(str(edit), "HWP", "versionwarning:false"):
                raise RuntimeError("Original HWP copy open failed")
            full = app.GetTextFile("TEXT", "")
            if not isinstance(full, str) or full.count(fixture.MARKER) != 1:
                raise ValueError("Authored full text unavailable")
            for row, height in enumerate(minima[0]["row_heights"]):
                for col in range(2):
                    diagnostic = {"row": row, "col": col}
                    report["height_attempt_diagnostics"].append(diagnostic)
                    result = set_height(
                        app,
                        edit,
                        edit_sha,
                        full,
                        grid[f"{row},{col}"],
                        EXPECTED[2 * row + col],
                        height,
                        diagnostic,
                    )
                    report["height_evidence"].append({"row": row, "col": col, **result})
            if (
                candidate.exists()
                or not app.SaveAs(str(candidate), "HWP", "")
                or not candidate.is_file()
            ):
                raise RuntimeError("Height candidate HWP save failed")
            report["height_owned_session"] = app.ownership
        reopened = output / "saved-HWP-reopen-copy.hwp"
        shutil.copyfile(candidate, reopened)
        after, after_pdf = (
            output / "candidate-reopened.hwpx",
            output / "candidate-reopened.pdf",
        )
        report["saved_HWP_export"] = export_copy(reopened, after_pdf, after)
        report["structural_gate"] = fixed.structural_gate(baseline, after, plan)
        if (
            r.archive(baseline)["Contents/header.xml"]
            != r.archive(after)["Contents/header.xml"]
        ):
            raise ValueError("Height calibration changed header bytes")
        report["header_bytes_unchanged"] = True
        report["pixel_gate"] = pixel_gate(pdf, after_pdf)
        if (
            r.file_hash(source) != digest
            or r.file_hash(edit) != edit_sha
            or r.file_hash(candidate) != r.file_hash(reopened)
        ):
            raise ValueError("Height source/copy bytes changed")
        report["status"] = (
            "height_API_calibration_passed_needs_human_review_NOT_production_qualified"
        )
    except BaseException as error:
        report.update(error_type=type(error).__name__, error=str(error)[:2000])
    finally:
        report["after_hwp"] = hwp_inventory()
        if report["after_hwp"]:
            report["status"] = "blocked"
        report["artifact_sha256"] = {
            str(p.relative_to(output)): r.file_hash(p)
            for p in output.rglob("*")
            if p.is_file()
        }
        with (output / "TOY-ONLY-HEIGHT-CALIBRATION.json").open(
            "x", encoding="utf-8"
        ) as stream:
            json.dump(report, stream, ensure_ascii=False, indent=2)
    return report


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("output", type=Path)
    parser.add_argument("--run-native-test", action="store_true")
    parser.add_argument("--toy-only", action="store_true")
    args = parser.parse_args()
    result = run(args.output, run_native=args.run_native_test, toy_only=args.toy_only)
    print(
        json.dumps(
            {
                "status": result["status"],
                "error": result.get("error"),
                "height_cells": len(result["height_evidence"]),
            }
        )
    )
    raise SystemExit(
        0 if result["status"].startswith("height_API_calibration_passed") else 1
    )
