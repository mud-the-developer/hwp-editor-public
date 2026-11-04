"""Exact selection/Cell-minimum primitives; NO CLI, COM creation, save or generic edits.

Caller MUST verify the immutable baseline, exact UI policy/region approval and an
owned lease before invoking. Text/selection/full-buffer checks are additional,
not a replacement for the schema-specific structural and rendered gates.
"""


def replace_plain_selection(app, edit: dict) -> dict:
    old = edit["old_text"]
    if app.GetTextFile("TEXT", "").count(old) != 1:
        raise ValueError("Native literal is missing or ambiguous")
    if not app.SetPos(0, 0, 0) or tuple(app.GetPos())[:2] != (0, 0):
        raise ValueError("Native first body paragraph unavailable")
    find = app.HParameterSet.HFindReplace
    app.HAction.GetDefault("RepeatFind", find.HSet)
    flags = {
        "FindString": old,
        "ReplaceString": "",
        "IgnoreFindString": 0,
        "IgnoreReplaceString": 0,
        "Direction": app.FindDir("Forward"),
        "WholeWordOnly": 0,
        "UseWildCards": 0,
        "SeveralWords": 0,
        "AllWordForms": 0,
        "MatchCase": 1,
        "ReplaceMode": 0,
        "FindRegExp": 0,
        "FindJaso": 0,
        "HanjaFromHangul": 0,
        "AutoSpell": 0,
        "IgnoreMessage": 1,
        "FindType": 1,
    }
    for key, value in flags.items():
        setattr(find, key, value)
    if not app.HAction.Execute("RepeatFind", find.HSet):
        raise ValueError("Native exact literal was not selected")
    pos = tuple(app.GetSelectedPos())
    if (
        len(pos) != 7
        or not pos[0]
        or pos[1] != pos[4]
        or pos[2] != pos[5]
        or abs(pos[6] - pos[3]) != len(old.encode("utf-16-le")) // 2
    ):
        raise ValueError("Native selection differs from the approved single paragraph")
    text = app.GetTextFile("TEXT", "saveblock")
    if text not in (old, old + "\r\n", "\ufeff" + old, "\ufeff" + old + "\r\n"):
        raise ValueError("Native selected text is not the approved old text")
    insert = app.HParameterSet.HInsertText
    app.HAction.GetDefault("InsertText", insert.HSet)
    insert.Text = edit["new_text"]
    if not app.HAction.Execute("InsertText", insert.HSet):
        raise ValueError("Native selected-text insertion failed")
    return {
        "anchor_id": edit["anchor_id"],
        "selection_position": list(pos),
        "old_text_exact": True,
    }


def edit_exact_buffer(app, edits: list[dict]) -> tuple[list[dict], str]:
    expected = app.GetTextFile("TEXT", "")
    if not isinstance(expected, str):
        raise ValueError("Native full-text snapshot unavailable")
    evidence = []
    for edit in edits:
        if app.GetTextFile("TEXT", "") != expected:
            raise ValueError(
                "Unexpected native input before replacement; no candidate saved"
            )
        proof = replace_plain_selection(app, edit)
        expected = expected.replace(edit["old_text"], edit["new_text"], 1)
        if app.GetTextFile("TEXT", "") != expected:
            raise ValueError(
                "Unexpected native text delta after replacement; no candidate saved"
            )
        evidence.append(proof)
    return evidence, expected


def require_unselected_caret(app, pos: list[int]) -> dict:
    from hwp_native_cells import position

    pos = position(pos)
    if position(app.GetPos()) != pos:
        raise ValueError("Native caret differs from content-verified cell")
    raw = app.GetSelectedPos()
    if (
        not isinstance(raw, (list, tuple))
        or len(raw) != 7
        or type(raw[0]) is not bool
        or any(type(v) is not int or not 0 <= v < 2**31 for v in raw[1:])
    ):
        raise ValueError("Unclassified Native selection metadata; no insertion")
    if position(app.GetPos()) != pos:
        raise ValueError("Selection metadata moved the Native caret")
    values = list(raw)
    no_block = values[0] is False and values[1:] == [0] * 6
    collapsed = values[0] is True and values[1:4] == pos and values[4:7] == pos
    if not (no_block or collapsed):
        raise ValueError(
            "Native selection is not calibrated no-block/collapsed; no insertion"
        )
    return {
        "caret": pos,
        "return_python_type": type(raw).__name__,
        "sequence_length": 7,
        "typed_items": [{"python_type": type(v).__name__, "value": v} for v in raw],
        "diagnostic_classification": "calibrated_no_block"
        if no_block
        else "collapsed_range",
    }


def insert_bound_empty_cell(
    app, edit: dict, pos: list[int], expected: str
) -> tuple[dict, str]:
    """Caller owns a fresh content-verified empty-cell map and exact approved lease."""
    from hwp_native_cells import position, scan_current_cell

    pos = position(pos)
    value = edit["new_text"]
    if (
        pos[0] == 0
        or pos[1:] != [0, 0]
        or not isinstance(expected, str)
        or not isinstance(value, str)
        or not value
        or value in expected
        or any(c in value for c in "\r\n\t\x00")
    ):
        raise ValueError("Bound cell needs an exact unique single-paragraph literal")
    if app.GetTextFile("TEXT", "") != expected:
        raise ValueError("Native source buffer changed before empty-cell insertion")
    if not app.SetPos(*pos) or position(app.GetPos()) != pos:
        raise ValueError("Bound empty-cell position did not roundtrip")
    metadata = require_unselected_caret(app, pos)
    scan_current_cell(app, "")
    require_unselected_caret(app, pos)
    if app.GetTextFile("TEXT", "") != expected:
        raise ValueError("Native buffer changed during empty-cell navigation")
    insert = app.HParameterSet.HInsertText
    app.HAction.GetDefault("InsertText", insert.HSet)
    insert.Text = value
    if not app.HAction.Execute("InsertText", insert.HSet):
        raise ValueError("Native bound empty-cell insertion failed")
    after = app.GetTextFile("TEXT", "")
    if (
        not isinstance(after, str)
        or after.count(value) != 1
        or after.replace(value, "", 1) != expected
    ):
        raise ValueError(
            "Unexpected Native text delta after empty-cell insertion; no candidate saved"
        )
    return {
        "field_id": edit["field_id"],
        "verified_position": pos,
        "exact_literal_addition_only": True,
        "selection_metadata": metadata,
    }, after


def source_buffer_identity(app, source, digest, full):
    """Known source-copy invariants; caller owns authorization and lease checks."""
    from pathlib import Path
    import hwp_review as r

    doc = None
    try:
        if type(app.XHwpDocuments.Count) is not int or app.XHwpDocuments.Count != 1:
            raise ValueError("One known calibration document required")
        doc = app.XHwpDocuments.Item(0)
        if (
            not isinstance(doc.FullName, str)
            or Path(doc.FullName).resolve() != source
            or not isinstance(doc.Path, str)
            or Path(doc.Path).resolve() != source.parent
            or type(doc.Modified) not in (bool, int)
            or doc.Modified not in (0, 1)
            or r.file_hash(source) != digest
        ):
            raise ValueError("Calibration document/source identity changed")
        if (
            app.GetTextFile("TEXT", "") != full
            or type(app.PageCount) is not int
            or app.PageCount != 1
        ):
            raise ValueError("Calibration changed text/page count")
    finally:
        doc = None


def parameter_snapshot(param):
    """Bounded primitive diagnostics only; never used as write/cleanup authority."""
    try:
        sid, count = param.SetID, param.Count
        if (
            not isinstance(sid, str)
            or len(sid) > 64
            or type(count) is not int
            or not 0 <= count <= 256
        ):
            raise ValueError("Unknown SetID/Count metadata")
        result = {"SetID": sid, "Count": count}
        for name in ("Height", "Width"):
            exists = param.ItemExist(name)
            if type(exists) not in (bool, int) or exists not in (0, 1):
                raise ValueError("Unknown ItemExist metadata")
            value = param.Item(name) if exists else None
            if value is not None and (type(value) is not int or not 0 <= value < 2**31):
                raise ValueError("Unknown size metadata")
            result[name] = {"exists": bool(exists), "value": value}
        return result
    except BaseException as error:
        return {"diagnostic_error": (type(error).__name__ + ": " + str(error))[:160]}


def raw_comparison(value):
    return {
        "python_type": type(value).__name__[:64],
        "value": value if type(value) in (bool, int) and value in (0, 1) else None,
    }


def _cell_parameter(table):
    if table.SetID != "Table" or not table.ItemExist("Cell"):
        raise ValueError("Exact Table.Cell parameter path required")
    child = table.Item("Cell")
    if child.SetID != "Cell":
        raise ValueError("Exact Cell child type required")
    return child


def set_bound_cell_minimum(
    app, source, digest, full, position, expected, height, diagnostic
):
    """One bound Cell.Height only. Caller MUST recheck exact plan/approval/lease."""
    from hwp_native_owner import NativeReference
    import hwp_native_cells as cells

    if (
        not isinstance(app, NativeReference)
        or app.target is None
        or type(height) is not int
        or not 0 < height < 2**31
    ):
        raise ValueError("Typed bounded height and owned reference required")
    pos = cells.position(position)
    if pos[0] == 0 or pos[1:] != [0, 0] or not isinstance(expected, str):
        raise ValueError("Exact authored cell-start binding required")
    source_buffer_identity(app, source, digest, full)
    if not app.SetPos(*pos) or cells.position(app.GetPos()) != pos:
        raise ValueError("Authored cell position did not roundtrip")
    if type(app.SelectionMode) is not int or app.SelectionMode != 0:
        raise ValueError("Single caret cell without selection required")
    cells.scan_current_cell(app, expected)
    before = changed = current = normalized = original_cell = changed_cell = (
        current_cell
    ) = normalized_cell = None
    try:
        diagnostic.update(stage="parameter_scope", height_put_attempted=False)
        before = app.CellShape
        diagnostic["original_table"] = parameter_snapshot(before)
        original_cell = _cell_parameter(before)
        diagnostic["original"] = parameter_snapshot(original_cell)
        old, width = original_cell.Item("Height"), original_cell.Item("Width")
        if (
            type(old) is not int
            or not 0 < old < 2**31
            or type(width) is not int
            or not 0 < width < 2**31
        ):
            raise ValueError("Cell size is not a typed positive integer")
        changed = before.Clone()
        changed_cell = _cell_parameter(changed)
        diagnostic["original_vs_untouched_clone"] = raw_comparison(
            before.IsEquivalent(changed)
        )
        changed_cell.SetItem("Height", height)
        diagnostic["changed_copy"] = parameter_snapshot(changed_cell)
        if (
            original_cell.Item("Height") != old
            or original_cell.Item("Width") != width
            or changed_cell.Item("Height") != height
            or changed_cell.Item("Width") != width
        ):
            raise ValueError("Child clone aliased original or changed width")
        source_buffer_identity(app, source, digest, full)
        normalized = changed.Clone()
        normalized_cell = _cell_parameter(normalized)
        normalized_cell.SetItem("Height", old)
        diagnostic["normalized_comparison_cell"] = parameter_snapshot(normalized_cell)
        diagnostic["changed_after_normalization"] = parameter_snapshot(changed_cell)
        if (
            original_cell.Item("Height") != old
            or changed_cell.Item("Height") != height
            or normalized_cell.Item("Height") != old
            or normalized_cell.Item("Width") != width
        ):
            raise ValueError("Comparison clone aliased original or pending child copy")
        comparison = before.IsEquivalent(normalized)
        diagnostic["comparison_raw"] = raw_comparison(comparison)
        diagnostic["stage"] = "non_height_equivalence"
        if comparison != True:
            raise ValueError("Parameter copy changed more than Height")
        if cells.position(app.GetPos()) != pos or app.SelectionMode != 0:
            raise ValueError("Cell selection changed before height put")
        source_buffer_identity(app, source, digest, full)
        # Fixed property on the owned target; no parent Height assignment or fallback.
        diagnostic.update(height_put_attempted=True, stage="height_property_put")
        app.target.CellShape = changed
        source_buffer_identity(app, source, digest, full)
        if cells.position(app.GetPos()) != pos:
            raise ValueError("Height write changed the selected cell")
        current = app.CellShape
        current_cell = _cell_parameter(current)
        if (
            current_cell.Item("Height") != height
            or type(current_cell.Item("Height")) is not int
            or current_cell.Item("Width") != width
        ):
            raise ValueError("Native height/width readback mismatch; no retry")
        normalized = current.Clone()
        normalized_cell = _cell_parameter(normalized)
        normalized_cell.SetItem("Height", old)
        if current_cell.Item("Height") != height:
            raise ValueError("Readback comparison clone aliased native parameter")
        diagnostic["native_readback"] = parameter_snapshot(current_cell)
        comparison = before.IsEquivalent(normalized)
        diagnostic["readback_comparison_raw"] = raw_comparison(comparison)
        diagnostic["stage"] = "native_readback_equivalence"
        if comparison != True:
            raise ValueError("Native readback changed non-height properties")
        cells.scan_current_cell(app, expected)
        source_buffer_identity(app, source, digest, full)
        return {
            "position": pos,
            "old_height_readback": old,
            "height_written_and_read_back": height,
            "width_unchanged": width,
            "text_unchanged": True,
            "non_height_parameters_equivalent": True,
        }
    finally:
        before = changed = current = normalized = original_cell = changed_cell = (
            current_cell
        ) = normalized_cell = None
