"""OPT-IN one fresh TOY ParameterSet copy comparison; NEVER document put/save.

Only TOY preparation creates seed/source files. The observation session reads an
exclusive copy, mutates detached parameter copies, and never saves a candidate.
"""

from __future__ import annotations

import json
from pathlib import Path
import shutil
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
from hwp_native_owner import NativeReference, owned_session
import hwp_native_cells as cells
import hwp_review as r
import hwp_security as security
from hwp_text_apply import real_path
import native_table_height_probe as height
from native_blank_metadata_probe import hwp_inventory


def readonly_identity(app, source, digest, full, position):
    height.identity(app, source, digest, full)
    doc = None
    try:
        doc = app.XHwpDocuments.Item(0)
        if doc.Modified != 0 or cells.position(app.GetPos()) != position:
            raise ValueError("Read-only parameter observation changed document/caret")
    finally:
        doc = None


def observe(app, source, digest, full, position, expected, evidence):
    if not isinstance(app, NativeReference) or app.target is None:
        raise ValueError("Active owned reference required")
    pos = cells.position(position)
    if pos[0] == 0 or pos[1:] != [0, 0] or not isinstance(expected, str):
        raise ValueError("Exact authored cell required")
    if not app.SetPos(*pos) or cells.position(app.GetPos()) != pos:
        raise ValueError("Authored position did not roundtrip")
    readonly_identity(app, source, digest, full, pos)
    cells.scan_current_cell(app, expected)
    original = copy = restored = original_cell = copy_cell = restored_cell = None
    try:
        evidence["stage"] = "table_and_cell_scope"
        original = app.CellShape
        if original.SetID != "Table" or not original.ItemExist("Cell"):
            raise ValueError("Exact Table.Cell parameter path required")
        original_cell = original.Item("Cell")
        if original_cell.SetID != "Cell":
            raise ValueError("Exact Cell child type required")
        evidence["original_table"] = height.parameter_snapshot(original)
        evidence["original_cell"] = height.parameter_snapshot(original_cell)
        old, width = original_cell.Item("Height"), original_cell.Item("Width")
        if (
            type(old) is not int
            or not 0 < old < 2**31 - 1000
            or type(width) is not int
            or not 0 < width < 2**31
        ):
            raise ValueError("Typed bounded cell-only height/width required")
        evidence["self_comparison"] = height.raw_comparison(
            original.IsEquivalent(original)
        )
        copy = original.Clone()
        copy_cell = copy.Item("Cell")
        if copy.SetID != "Table" or copy_cell.SetID != "Cell":
            raise ValueError("Clone parameter scope changed")
        evidence["untouched_cell_clone"] = height.parameter_snapshot(copy_cell)
        evidence["original_vs_clone"] = height.raw_comparison(
            original.IsEquivalent(copy)
        )
        evidence["clone_vs_original"] = height.raw_comparison(
            copy.IsEquivalent(original)
        )
        readonly_identity(app, source, digest, full, pos)
        evidence["stage"] = "detached_cell_height_copy"
        copy_cell.SetItem("Height", old + 1000)
        evidence["copy_test_height_not_document_height"] = old + 1000
        evidence["original_after_copy_change"] = height.parameter_snapshot(
            original_cell
        )
        evidence["changed_cell_copy"] = height.parameter_snapshot(copy_cell)
        if (
            original_cell.Item("Height") != old
            or original_cell.Item("Width") != width
            or copy_cell.Item("Height") != old + 1000
            or copy_cell.Item("Width") != width
        ):
            raise ValueError("Child clone aliased original or changed width")
        readonly_identity(app, source, digest, full, pos)
        evidence["stage"] = "comparison_copy_height_restore"
        restored = copy.Clone()
        restored_cell = restored.Item("Cell")
        if restored.SetID != "Table" or restored_cell.SetID != "Cell":
            raise ValueError("Comparison copy scope changed")
        restored_cell.SetItem("Height", old)
        evidence["restored_comparison_cell"] = height.parameter_snapshot(restored_cell)
        evidence["changed_cell_after_comparison_restore"] = height.parameter_snapshot(
            copy_cell
        )
        if (
            original_cell.Item("Height") != old
            or copy_cell.Item("Height") != old + 1000
            or restored_cell.Item("Height") != old
            or restored_cell.Item("Width") != width
        ):
            raise ValueError("Comparison clone aliased original or pending child copy")
        evidence["restored_comparison"] = height.raw_comparison(
            original.IsEquivalent(restored)
        )
        evidence["restored_reverse_comparison"] = height.raw_comparison(
            restored.IsEquivalent(original)
        )
        readonly_identity(app, source, digest, full, pos)
        cells.scan_current_cell(app, expected)
        evidence["stage"] = "observed_no_document_put_or_save"
        evidence["source_text_page_caret_and_modified_unchanged"] = True
        return evidence
    finally:
        original = copy = restored = original_cell = copy_cell = restored_cell = None


def run(output, *, run_native=False, toy_only=False):
    if run_native is not True or toy_only is not True or getattr(sys, "frozen", False):
        raise ValueError("Explicit SOURCE-only parameter observation opt-in required")
    output = real_path(output)
    if (
        output.parent != ROOT / "work"
        or not output.name.startswith("toy-parameter-copy-")
        or output.exists()
        or hwp_inventory()
    ):
        raise ValueError("New direct owned lab and empty Hwp inventory required")
    security.registered_security_module()
    output.mkdir()
    report = {
        "status": "blocked",
        "test_only_not_deliverable": True,
        "document_property_put": False,
        "observation_SaveAs_calls": 0,
        "candidate_saved": False,
        "production_qualified": False,
        "automatic_retries": 0,
        "observations": {},
    }
    try:
        grid = {}
        source, report["author_owned_session"] = height.create_source(
            output / "source", grid
        )
        digest = r.file_hash(source)
        report["source_sha256"] = digest
        copy = output / "read-only-source-copy.hwp"
        shutil.copyfile(source, copy)
        with owned_session((copy,)) as app:
            report["observation_owned_session"] = app.ownership
            report["security_module"] = height.read_only_security(app)
            if not app.Open(str(copy), "HWP", "versionwarning:false"):
                raise RuntimeError("Known observation HWP copy open failed")
            full = app.GetTextFile("TEXT", "")
            if not isinstance(full, str) or full.count(height.fixture.MARKER) != 1:
                raise ValueError("Authored text identity unavailable")
            observe(
                app,
                copy,
                digest,
                full,
                grid["0,0"],
                height.EXPECTED[0],
                report["observations"],
            )
        if r.file_hash(source) != digest or r.file_hash(copy) != digest:
            raise ValueError("Observation changed source/copy bytes")
        report["status"] = (
            "parameter_copy_observation_completed_NOT_height_or_production_qualification"
        )
    except BaseException as error:
        report.update(error_type=type(error).__name__, error=str(error)[:1500])
    finally:
        report["after_hwp"] = hwp_inventory()
        if report["after_hwp"]:
            report["status"] = "blocked"
        report["artifact_sha256"] = {
            str(p.relative_to(output)): r.file_hash(p)
            for p in output.rglob("*")
            if p.is_file()
        }
        with (output / "TOY-ONLY-PARAMETER-COPY-OBSERVATION.json").open(
            "x", encoding="utf-8"
        ) as stream:
            json.dump(report, stream, ensure_ascii=False, indent=2)
    return report
