"""Schema 3: separately approved original row minima; schema 1/2 stay locked."""

from copy import deepcopy
from pathlib import Path
import tempfile
import xml.etree.ElementTree as ET
import zipfile

import hwp_native_plan as native

MODE = "native-original-row-minima-v1"
POLICY = "selected-lines-original-row-minima-generated-preview-v1"
KEYS = {
    "schema",
    "workspace_id",
    "baseline_hwpx_sha256",
    "edit_mode",
    "native_policy",
    "row_minima",
    "edits",
}


def text_contract(plan):
    result = {key: deepcopy(plan[key]) for key in KEYS - {"row_minima"}}
    result.update(schema=2, edit_mode=native.MODE, native_policy=native.POLICY)
    return result


def require_native_profile(baseline):
    """Known execution profile only; read-only proposals keep their existing rules."""
    import hwp_review as r
    from hwp_text_apply import real_path

    baseline = real_path(baseline)
    before = r.file_hash(baseline)
    native.require_yes_spine(r.archive(baseline).get("Contents/content.hpf"))
    if r.file_hash(baseline) != before:
        raise ValueError("Reading-order baseline changed during Native preflight")


def row_minima(baseline: Path, edits: list) -> list:
    """Derive ONLY original simple table row heights from immutable native caches."""
    import hwp_review as r

    parts = r.archive(baseline)
    anchors, _ = r.index_hwpx(baseline)
    selected = {edit["anchor_id"] for edit in edits}
    tables = {}
    for anchor in anchors:
        if anchor["anchor_id"] not in selected:
            continue
        root = r.xml_root(parts[anchor["part"]])
        ids = [table.get("id") for table in root.iter(r.HP + "tbl")]
        if len(ids) != len(set(ids)):
            raise ValueError(
                "Duplicate table identifiers cannot bind a fixed-row contract"
            )
        node, parents = root, []
        for index in anchor["path"]:
            parents.append(node)
            node = node[index]
        table = next((p for p in reversed(parents) if p.tag == r.HP + "tbl"), None)
        para = next((p for p in reversed(parents) if p.tag == r.HP + "p"), None)
        cache = para.find(r.HP + "linesegarray") if para is not None else None
        # Do not alter minima in tables whose selected text was already one line.
        if table is not None and cache is not None and len(cache) > 1:
            key = (anchor["part"], table.get("id"))
            tables[key] = table
    result = []
    for (part, table_id), table in sorted(tables.items()):
        rows = table.findall(r.HP + "tr")
        heights = []
        for row in rows:
            cells = row.findall(r.HP + "tc")
            if not cells or len(cells) != int(table.get("colCnt", "0")):
                raise ValueError("Fixed rows require a rectangular, unmerged table")
            sizes = []
            for cell in cells:
                span = cell.find(r.HP + "cellSpan")
                paras = list(cell.iter(r.HP + "p"))
                margin = cell.find(r.HP + "cellMargin")
                size = cell.find(r.HP + "cellSz")
                if (
                    span is None
                    or span.attrib != {"colSpan": "1", "rowSpan": "1"}
                    or len(paras) != 1
                    or margin is None
                    or size is None
                ):
                    raise ValueError(
                        "Fixed rows support one plain paragraph per unmerged cell"
                    )
                if any(
                    child.tag != r.HP + "t" or len(child)
                    for run in paras[0].findall(r.HP + "run")
                    for child in run
                ):
                    raise ValueError("Fixed row cells cannot contain non-text controls")
                lines = paras[0].find(r.HP + "linesegarray")
                if lines is None or not len(lines):
                    raise ValueError("Original row height cache is unavailable")
                extent = max(
                    int(line.attrib["vertpos"]) + int(line.attrib["vertsize"])
                    for line in lines
                )
                height = (
                    extent + int(margin.attrib["top"]) + int(margin.attrib["bottom"])
                )
                if not 0 < int(size.attrib["height"]) <= height < 2**31:
                    raise ValueError(
                        "Original cell minima/row height cannot be safely derived"
                    )
                sizes.append(height)
            heights.append(max(sizes))
        control = table.find(r.HP + "sz")
        if (
            not table_id
            or len(rows) != int(table.get("rowCnt", "0"))
            or control is None
            or sum(heights) != int(control.attrib["height"])
        ):
            raise ValueError(
                "Derived original rows do not exactly match the original table height"
            )
        result.append({"part": part, "table_id": table_id, "row_heights": heights})
    if not result:
        raise ValueError("The fixed-row proposal needs an affected simple table")
    return result


def validate(plan, manifest, baseline):
    if (
        not isinstance(plan, dict)
        or set(plan) != KEYS
        or type(plan["schema"]) is not int
        or plan["schema"] != 3
        or plan["edit_mode"] != MODE
        or plan["native_policy"] != POLICY
    ):
        raise ValueError("Invalid fixed-row native contract")
    selected = native.validate(text_contract(plan), manifest, baseline)
    expected = row_minima(baseline, plan["edits"])
    if (
        plan["row_minima"] != expected
        or not isinstance(plan["row_minima"], list)
        or any(
            type(height) is not int
            for spec in plan["row_minima"]
            for height in spec["row_heights"]
        )
    ):
        raise ValueError(
            "Row minima must exactly equal the independently derived integer original rows"
        )
    return selected


def proposal(old, manifest, baseline):
    native.validate(old, manifest, baseline)
    result = deepcopy(old)
    result.update(
        schema=3,
        edit_mode=MODE,
        native_policy=POLICY,
        row_minima=row_minima(baseline, old["edits"]),
    )
    validate(result, manifest, baseline)
    return result


def template_parts(baseline, plan):
    """Change declared minima only BEFORE editing; never repair a failed candidate."""
    import hwp_review as r

    if (
        type(plan.get("schema")) is not int
        or plan["schema"] != 3
        or plan.get("native_policy") != POLICY
        or plan.get("edit_mode") != MODE
        or plan.get("baseline_hwpx_sha256") != r.file_hash(baseline)
        or plan.get("row_minima") != row_minima(baseline, plan["edits"])
        or any(
            type(height) is not int
            for spec in plan["row_minima"]
            for height in spec["row_heights"]
        )
    ):
        raise ValueError("Exact original row minima contract required")
    parts = r.archive(baseline)
    for part in {spec["part"] for spec in plan["row_minima"]}:
        root = native.strict_root(parts[part])
        tables = {table.get("id"): table for table in root.iter(r.HP + "tbl")}
        for spec in plan["row_minima"]:
            if spec["part"] != part:
                continue
            table = tables[spec["table_id"]]
            for row, height in zip(table.findall(r.HP + "tr"), spec["row_heights"]):
                for cell in row.findall(r.HP + "tc"):
                    size = cell.find(r.HP + "cellSz")
                    if size is None:
                        raise ValueError("Original row cell size is missing")
                    size.set("height", str(height))
        parts[part] = ET.tostring(root, encoding="utf-8", xml_declaration=True)
    return parts


def write_template(baseline, target, plan):
    if target.exists():
        raise FileExistsError(target)
    parts = template_parts(baseline, plan)
    with zipfile.ZipFile(target, "x", compression=zipfile.ZIP_DEFLATED) as archive:
        for name, raw in parts.items():
            archive.writestr(
                name,
                raw,
                compress_type=zipfile.ZIP_STORED
                if name == "mimetype"
                else zipfile.ZIP_DEFLATED,
            )


def structural_gate(before, after, plan):
    """Keep every v2 lock, except exact row minima and declared generated thumbnail."""
    import hwp_review as r
    import fitz

    if plan.get("edit_mode") != MODE or plan.get("native_policy") != POLICY:
        raise ValueError("Explicit fixed-row/preview policy required")
    parts = template_parts(before, plan)
    output = r.archive(after)
    if set(parts) != set(output):
        raise ValueError("Fixed-row archive membership changed")
    preview = "Preview/PrvImage.png"
    if preview in parts and parts[preview] != output[preview]:
        if len(output[preview]) > 1024 * 1024 or not output[preview].startswith(
            b"\x89PNG\r\n\x1a\n"
        ):
            raise ValueError("Generated preview must be a bounded PNG")
        original, changed = fitz.Pixmap(parts[preview]), fitz.Pixmap(output[preview])
        if (
            (original.width, original.height, original.n, original.alpha)
            != (changed.width, changed.height, changed.n, changed.alpha)
            or not 1 <= changed.width <= 1024
            or not 1 <= changed.height <= 1024
        ):
            raise ValueError("Generated preview dimensions/pixel format changed")
        parts[preview] = output[preview]
    # Compare against an ephemeral reference, never overwrite the immutable baseline.
    with tempfile.TemporaryDirectory(prefix="hwp-fixed-lock-") as directory:
        reference = Path(directory) / "declared-minima.hwpx"
        with zipfile.ZipFile(
            reference, "x", compression=zipfile.ZIP_DEFLATED
        ) as archive:
            for name, raw in parts.items():
                archive.writestr(name, raw)
        result = native.structural_gate(reference, after, text_contract(plan))
    result.pop("locked_table_font_controls_assets", None)
    result.update(
        policy=POLICY,
        original_table_geometry_font_controls_assets_locked=True,
        declared_original_row_minima=plan["row_minima"],
        generated_thumbnail_changed=preview in parts
        and r.archive(before)[preview] != output[preview],
    )
    return result
