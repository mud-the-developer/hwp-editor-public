"""Unqualified read-only plain-table binding. No CLI, COM creation, save or insertion.
The fixed caller must establish owned COM/source-copy identity and its own authority.
"""

import hashlib
import json
from pathlib import Path

MAX_CELLS = 256
MAX_CELL_UNITS = 4096
MAX_SCAN_CALLS = 8
PROFILE = "single-plain-table-readonly-v1"
MINIMA_PROFILE = "single-original-minima-table-readonly-v1"


def _hash(value):
    return hashlib.sha256(
        json.dumps(
            value, sort_keys=True, separators=(",", ":"), ensure_ascii=False
        ).encode("utf-8")
    ).hexdigest()


def position(value):
    if (
        not isinstance(value, (list, tuple))
        or len(value) != 3
        or any(type(v) is not int or not 0 <= v < 2**31 for v in value)
    ):
        raise ValueError("Typed bounded Native List/Para/Pos required")
    return list(value)


def _plain_table(baseline):
    import hwp_review as r
    import hwp_native_plan as native

    baseline = Path(baseline)
    before = r.file_hash(baseline)
    parts = r.archive(baseline)
    native.require_yes_spine(parts.get("Contents/content.hpf"))
    sections = [name for name in parts if r.SECTIONS.fullmatch(name)]
    if len(sections) != 1:
        raise ValueError("Native binding currently requires one section")
    part = sections[0]
    root = r.xml_root(parts[part])
    tables = []

    def walk(node, path):
        if node.tag == r.HP + "tbl":
            tables.append((node, path))
        for index, child in enumerate(node):
            walk(child, path + [index])

    walk(root, [])
    if len(tables) != 1:
        raise ValueError("One unambiguous non-nested table required")
    table, table_path = tables[0]
    rows = table.get("rowCnt", "")
    cols = table.get("colCnt", "")
    if (
        not rows.isascii()
        or not rows.isdigit()
        or not cols.isascii()
        or not cols.isdigit()
    ):
        raise ValueError("Explicit bounded table dimensions required")
    rows, cols = int(rows), int(cols)
    if not 1 <= rows <= 32 or not 1 <= cols <= 16 or rows * cols > MAX_CELLS:
        raise ValueError("Plain table exceeds bounded binding profile")
    native_rows = table.findall(r.HP + "tr")
    if len(native_rows) != rows or len(list(table.iter(r.HP + "tc"))) != rows * cols:
        raise ValueError("Declared table row/cell count differs")
    cells = []
    for row, row_node in enumerate(native_rows):
        row_cells = row_node.findall(r.HP + "tc")
        if len(row_cells) != cols:
            raise ValueError("Declared table column count differs")
        for col, node in enumerate(row_cells):
            address = node.find(r.HP + "cellAddr")
            span = node.find(r.HP + "cellSpan")
            sublists = node.findall(r.HP + "subList")
            if (
                address is None
                or len(node.findall(r.HP + "cellAddr")) != 1
                or len(node.findall(r.HP + "cellSpan")) != 1
                or address.attrib != {"rowAddr": str(row), "colAddr": str(col)}
                or span is None
                or span.attrib != {"rowSpan": "1", "colSpan": "1"}
            ):
                raise ValueError(
                    "Merged/reordered/unknown cell coordinate is unsupported"
                )
            if (
                len(sublists) != 1
                or len(sublists[0]) != 1
                or sublists[0][0].tag != r.HP + "p"
            ):
                raise ValueError("One plain paragraph per cell required")
            para = sublists[0][0]
            runs = para.findall(r.HP + "run")
            if (
                (para.text and para.text.strip())
                or not runs
                or any(
                    child.tag not in (r.HP + "run", r.HP + "linesegarray")
                    or child.tail
                    and child.tail.strip()
                    for child in para
                )
            ):
                raise ValueError("Cell paragraph has inline controls")
            chunks = []
            for run in runs:
                if any(
                    child.tag != r.HP + "t"
                    or len(child)
                    or child.tail
                    and child.tail.strip()
                    for child in run
                ):
                    raise ValueError("Cell run has inline controls")
                if run.text and run.text.strip():
                    raise ValueError("Mixed run content is unsupported")
                chunks.extend(child.text or "" for child in run)
            text = "".join(chunks)
            if (
                any(c in text for c in "\r\n\t\x00")
                or len(text.encode("utf-16-le")) // 2 > MAX_CELL_UNITS
            ):
                raise ValueError("Cell text is not a bounded single paragraph")
            cells.append({"row": row, "col": col, "text": text})
    if r.file_hash(baseline) != before:
        raise ValueError("Baseline changed during table inspection")
    return (
        {
            "baseline_hwpx_sha256": before,
            "part": part,
            "table_path": table_path,
            "rows": rows,
            "cols": cols,
            "cells": cells,
        },
        table.get("id"),
    )


def plain_profile(baseline, selected):
    import hwp_review as r
    import hwp_form_draft as form

    baseline = Path(baseline)
    common, _ = _plain_table(baseline)
    before, part, table_path, rows, cols, cells = (
        common[key]
        for key in (
            "baseline_hwpx_sha256",
            "part",
            "table_path",
            "rows",
            "cols",
            "cells",
        )
    )
    mapped = form.inspect_template(baseline)
    fields = {item["field_id"]: item for item in mapped["fields"]}
    targets = []
    seen = set()
    if not isinstance(selected, list) or not selected or len(selected) > rows * cols:
        raise ValueError("Exact selected empty fields required")
    for selected_field in selected:
        if not isinstance(selected_field, dict):
            raise ValueError("Exact selected field record required")
        field = fields.get(selected_field.get("field_id"))
        if (
            field is None
            or not field["supported"]
            or field["part"] != part
            or field["table_path"] != table_path
            or field["field_id"] in seen
        ):
            raise ValueError("Selected field is foreign, ambiguous or unsupported")
        if any(
            selected_field.get(key) != field[key]
            for key in (
                "cell_path",
                "table_path",
                "part",
                "cell_address",
                "expected_text",
            )
        ):
            raise ValueError("Selected field address differs from baseline")
        row = int(field["cell_address"]["rowAddr"])
        col = int(field["cell_address"]["colAddr"])
        if cells[row * cols + col]["text"] != "":
            raise ValueError("Selected target must be exactly empty, not whitespace")
        seen.add(field["field_id"])
        targets.append(
            {
                "field_id": field["field_id"],
                "row": int(field["cell_address"]["rowAddr"]),
                "col": int(field["cell_address"]["colAddr"]),
                "cell_path": field["cell_path"],
            }
        )
    if r.file_hash(baseline) != before:
        raise ValueError("Baseline changed during profile inspection")
    profile = {"policy": PROFILE, **common, "targets": targets}
    return {**profile, "profile_sha256": _hash(profile)}


def minima_profile(baseline, plan, manifest):
    """Distinct schema3 read-only profile; plan validity is not UI approval."""
    import hwp_review as r
    import hwp_fixed_layout as fixed

    fixed.validate(plan, manifest, baseline)
    common, table_id = _plain_table(baseline)
    specs = plan["row_minima"]
    if (
        common["baseline_hwpx_sha256"] != plan["baseline_hwpx_sha256"]
        or len(specs) != 1
        or specs[0]["part"] != common["part"]
        or specs[0]["table_id"] != table_id
        or len(specs[0]["row_heights"]) != common["rows"]
    ):
        raise ValueError("Exact one-table original-minima binding required")
    targets = [
        {
            "row": cell["row"],
            "col": cell["col"],
            "height": specs[0]["row_heights"][cell["row"]],
        }
        for cell in common["cells"]
    ]
    profile = {
        "policy": MINIMA_PROFILE,
        **common,
        "table_id": table_id,
        "plan_sha256": r.plan_hash(plan),
        "targets": targets,
    }
    if r.file_hash(baseline) != common["baseline_hwpx_sha256"]:
        raise ValueError("Baseline changed during minima profile inspection")
    return {**profile, "profile_sha256": _hash(profile)}


def scan_current_cell(app, expected):
    caret = position(app.GetPos())
    if not app.InitScan(0, 0x55):
        raise ValueError("Native current-list scan initialization failed")
    chunks = []
    units = 0
    finished = False
    try:
        for _ in range(MAX_SCAN_CALLS):
            result = app.GetText()
            if (
                not isinstance(result, (tuple, list))
                or len(result) != 2
                or type(result[0]) is not int
                or not isinstance(result[1], str)
            ):
                raise ValueError("Unknown Native cell scan response")
            state, text = result
            if state in (0, 1):
                if text:
                    raise ValueError("Terminal Native scan returned unexpected text")
                finished = True
                break
            if state not in (2, 3):
                raise ValueError("Native cell scan entered control/error state")
            units += len(text.encode("utf-16-le")) // 2
            if units > MAX_CELL_UNITS + 2:
                raise ValueError("Native cell scan text limit exceeded")
            chunks.append(text)
        if not finished:
            raise ValueError("Native cell scan call limit exceeded")
    finally:
        app.ReleaseScan()
    if position(app.GetPos()) != caret:
        raise ValueError("Native cell scan changed caret")
    text = "".join(chunks)
    if text not in (expected, expected + "\r\n"):
        raise ValueError("Native cell text differs from baseline; no binding")


def capture(app, source_copy, source_sha256, baseline, selected):
    """Blank-field evidence only; never admission to write."""
    profile = plain_profile(baseline, selected)
    return _capture_profile(app, source_copy, source_sha256, baseline, profile)


def capture_minima(app, source_copy, source_sha256, baseline, plan, manifest):
    """Schema3 original minima evidence only; caller retains all apply authority."""
    profile = minima_profile(baseline, plan, manifest)
    result = _capture_profile(app, source_copy, source_sha256, baseline, profile)
    if minima_profile(baseline, plan, manifest) != profile:
        raise ValueError("Original-minima contract changed during binding")
    result.update(
        schema="hwp-native-original-minima-binding/v1",
        plan_sha256=profile["plan_sha256"],
    )
    return result


def _capture_profile(app, source_copy, source_sha256, baseline, profile):
    """Internal walk over a profile built by the fixed read-only constructors."""
    import hwp_review as r

    source_copy = Path(source_copy).resolve(strict=True)
    baseline = Path(baseline)
    original = position(app.GetPos())

    def identity():
        if type(app.XHwpDocuments.Count) is not int or app.XHwpDocuments.Count != 1:
            raise ValueError("Owned source document count changed")
        doc = app.XHwpDocuments.Item(0)
        if (
            not isinstance(doc.FullName, str)
            or Path(doc.FullName).resolve() != source_copy
            or type(doc.Modified) not in (bool, int)
            or doc.Modified != 0
            or r.file_hash(source_copy) != source_sha256
        ):
            raise ValueError("Owned source-copy identity or bytes changed")
        if r.file_hash(baseline) != profile["baseline_hwpx_sha256"]:
            raise ValueError("Baseline changed during Native binding")

    identity()
    full = app.GetTextFile("TEXT", "")
    if not isinstance(full, str):
        raise ValueError("Native source text snapshot unavailable")
    if type(app.PageCount) is not int or app.PageCount != 1:
        raise ValueError("Initial Native binding profile requires one page")

    def unchanged():
        identity()
        if app.GetTextFile("TEXT", "") != full:
            raise ValueError("Read-only binding changed the Native buffer")

    controls = []
    anchor = None
    ctrl = app.HeadCtrl
    while ctrl is not None:
        if len(controls) >= 32:
            raise ValueError("Control enumeration limit exceeded")
        kind = ctrl.CtrlID
        if kind not in ("secd", "cold", "tbl"):
            raise ValueError("Unsupported control in plain-table binding")
        controls.append(kind)
        if kind == "tbl":
            if anchor is not None:
                raise ValueError("Multiple Native table controls are ambiguous")
            item = ctrl.GetAnchorPos(0)
            anchor = position([item.Item(k) for k in ("List", "Para", "Pos")])
        ctrl = ctrl.Next
    if anchor is None or anchor[0] != 0:
        raise ValueError("Unique main-body table anchor required")
    records = []
    seen = set()

    def move(action):
        if action not in (
            "MoveRight",
            "TableRightCell",
            "TableLeftCell",
            "TableLowerCell",
        ):
            raise ValueError("Unknown read-only cell navigation")
        if app.Run(action) is False:
            raise ValueError("Native cell navigation failed")
        unchanged()

    try:
        if not app.SetPos(*anchor) or position(app.GetPos()) != anchor:
            raise ValueError("Native table anchor did not roundtrip")
        move("MoveRight")
        for row in range(profile["rows"]):
            for col in range(profile["cols"]):
                if col:
                    move("TableRightCell")
                pos = position(app.GetPos())
                if pos[0] == 0 or pos[1:] != [0, 0] or tuple(pos) in seen:
                    raise ValueError(
                        "Native cell position is ambiguous or not at paragraph start"
                    )
                seen.add(tuple(pos))
                expected = profile["cells"][row * profile["cols"] + col]["text"]
                scan_current_cell(app, expected)
                unchanged()
                records.append(
                    {
                        "row": row,
                        "col": col,
                        "position": pos,
                        "text_sha256": hashlib.sha256(
                            expected.encode("utf-8")
                        ).hexdigest(),
                    }
                )
            if row + 1 < profile["rows"]:
                for _ in range(profile["cols"] - 1):
                    move("TableLeftCell")
                move("TableLowerCell")
    finally:
        identity()
        if not app.SetPos(*original) or position(app.GetPos()) != original:
            raise ValueError("Original caret restoration failed")
        unchanged()
    targets = [
        {
            **target,
            "position": next(
                item["position"]
                for item in records
                if (item["row"], item["col"]) == (target["row"], target["col"])
            ),
        }
        for target in profile["targets"]
    ]
    return {
        "schema": "hwp-native-cell-binding/v1",
        "policy": profile["policy"],
        "source_sha256": source_sha256,
        "baseline_hwpx_sha256": profile["baseline_hwpx_sha256"],
        "profile_sha256": profile["profile_sha256"],
        "full_text_sha256": hashlib.sha256(full.encode("utf-8")).hexdigest(),
        "table_anchor": anchor,
        "cells": records,
        "targets": targets,
        "source_unchanged": True,
        "original_caret_restored": True,
        "writer_qualified": False,
    }
