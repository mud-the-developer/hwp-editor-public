"""Schema 4: separately UI-approved plain empty-cell filling; no old gate weakening."""

from copy import deepcopy
from pathlib import Path
from typing import cast
import math
import tempfile
import xml.etree.ElementTree as ET
import zipfile

MODE = "blank-cell-native-v1"
POLICY = "selected-empty-cells-lines-caret-date-preview-header-spine-v1"
KEYS = {
    "schema",
    "workspace_id",
    "baseline_hwpx_sha256",
    "edit_mode",
    "native_policy",
    "bindings",
    "record",
    "placements",
    "draft_sha256",
    "header_spine",
}
RENDER_POLICY = "exact-cell-text-and-glyph-geometry-v1"
RENDER_ISSUE_LIMIT = 20
RENDER_CODES = {
    "page_count_changed",
    "baseline_cell_not_empty",
    "glyph_outside_cell",
    "outside_text_changed",
    "rendered_text_mismatch",
    "glyph_overlap",
}


def header_spine(baseline):
    """Exact ORIGINAL header resource binding, not a broad OPF normalization."""
    import hwp_review as r
    import hwp_native_plan as native

    raw = r.archive(baseline).get("Contents/content.hpf")
    if raw is None:
        return None
    root = native.strict_root(raw)
    headers = root.findall(
        f'{native.OPF}manifest/{native.OPF}item[@href="Contents/header.xml"]'
    )
    if len(headers) != 1 or not headers[0].get("id"):
        raise ValueError("Cannot bind a unique original header resource")
    identifier = headers[0].get("id")
    refs = [
        node
        for node in root.findall(native.OPF + "spine/" + native.OPF + "itemref")
        if node.get("idref") == identifier
    ]
    if len(refs) != 1 or refs[0].get("linear") not in (None, "yes", "no"):
        raise ValueError("Unsupported header reading-order metadata")
    return {
        "part": "Contents/content.hpf",
        "idref": identifier,
        "before": refs[0].get("linear"),
        "after": "no",
    }


def _node(root, path):
    for index in path:
        root = [n for n in root if isinstance(n.tag, str)][index]
    return root


def target_paths(root, cell_path):
    import hwp_review as r

    cell = _node(root, cell_path)
    sub = next(i for i, n in enumerate(cell) if n.tag == r.HP + "subList")
    para = next(i for i, n in enumerate(cell[sub]) if n.tag == r.HP + "p")
    paragraph = cell_path + [sub, para]
    run = next(i for i, n in enumerate(_node(root, paragraph)) if n.tag == r.HP + "run")
    return paragraph, paragraph + [run]


def empty_regions(pdf: Path, protected_pages=()) -> list[dict]:
    """Minimal closed PDF grid cells, inset to keep borders OUTSIDE edit masks."""
    import fitz

    result = []
    with fitz.open(pdf) as doc:
        for index in range(len(doc)):
            number, page = index + 1, doc[index]
            if number in protected_pages:
                continue
            horizontal, vertical = [], []
            for drawing in page.get_drawings():
                for item in drawing["items"]:
                    lines = []
                    if item[0] == "l":
                        lines = [(item[1], item[2])]
                    elif item[0] == "re":
                        rect = item[1]
                        lines = [
                            (rect.tl, rect.tr),
                            (rect.tr, rect.br),
                            (rect.br, rect.bl),
                            (rect.bl, rect.tl),
                        ]
                    for start, end in lines:
                        x0, y0, x1, y1 = [
                            round(v, 2) for v in (start.x, start.y, end.x, end.y)
                        ]
                        if y0 == y1 and x0 != x1:
                            horizontal.append((y0, min(x0, x1), max(x0, x1)))
                        if x0 == x1 and y0 != y1:
                            vertical.append((x0, min(y0, y1), max(y0, y1)))
            xs = sorted({x for x, _, _ in vertical})
            ys = sorted({y for y, _, _ in horizontal})
            if len(xs) * len(ys) > 50000:
                raise ValueError("PDF grid is too complex; no guessed regions")
            chars = _characters(page)
            images = [fitz.Rect(info["bbox"]) for info in page.get_image_info()]
            for x0, x1 in zip(xs, xs[1:]):
                for y0, y1 in zip(ys, ys[1:]):
                    if x1 - x0 < 5 or y1 - y0 < 5:
                        continue
                    if not all(
                        any(
                            abs(y - edge) <= 0.05 and a <= x0 + 0.05 and b >= x1 - 0.05
                            for y, a, b in horizontal
                        )
                        for edge in (y0, y1)
                    ):
                        continue
                    if not all(
                        any(
                            abs(x - edge) <= 0.05 and a <= y0 + 0.05 and b >= y1 - 0.05
                            for x, a, b in vertical
                        )
                        for edge in (x0, x1)
                    ):
                        continue
                    rect = fitz.Rect(x0 + 1, y0 + 1, x1 - 1, y1 - 1)
                    if (
                        not page.rect.contains(rect)
                        or any(
                            c.strip() and rect.intersects(fitz.Rect(box))
                            for c, box in chars
                        )
                        or any(rect.intersects(image) for image in images)
                    ):
                        continue
                    result.append(
                        {"page": number, "region": [rect.x0, rect.y0, rect.x1, rect.y1]}
                    )
                    if len(result) > 5000:
                        raise ValueError(
                            "Too many blank PDF cells; explicit smaller template required"
                        )
    return result


def _characters(page):
    return [
        (char["c"], char["bbox"])
        for block in cast(dict, page.get_text("rawdict"))["blocks"]
        if block.get("type") == 0
        for line in block["lines"]
        for span in line["spans"]
        for char in span["chars"]
    ]


def replay(baseline, bindings, record):
    import hwp_form_draft as f

    mapped = f.inspect_template(baseline)
    draft = f.draft(baseline, mapped, bindings, record)
    if draft["status"] != "needs_review" or draft["issues"]:
        raise ValueError(
            "Fill draft has missing/invalid source values or exceeded budgets"
        )
    active = [item for item in draft["assignments"] if item["value"] is not None]
    if not 1 <= len(active) <= 100:
        raise ValueError("Fill requires 1..100 supplied values")
    for item in active:
        if not item["value"] or "\n" in item["value"] or item["kind"] == "checkbox":
            raise ValueError(
                "Initial fill supports nonempty single-paragraph text, dates and numbers only"
            )
    return mapped, draft, active


def proposal(workspace, bindings, record, placements):
    import hwp_review as r

    manifest = r.verify_baseline(workspace)
    _, draft, _ = replay(workspace / "baseline.hwpx", bindings, record)
    plan = {
        "schema": 4,
        "workspace_id": manifest["workspace_id"],
        "baseline_hwpx_sha256": manifest["baseline_hwpx_sha256"],
        "edit_mode": MODE,
        "native_policy": POLICY,
        "bindings": bindings,
        "record": record,
        "placements": placements,
        "draft_sha256": draft["draft_sha256"],
        "header_spine": header_spine(workspace / "baseline.hwpx"),
    }
    validate(plan, manifest, workspace / "baseline.hwpx")
    return plan


def validate(plan, manifest, baseline):
    import hwp_review as r

    if (
        not isinstance(plan, dict)
        or set(plan) != KEYS
        or type(plan["schema"]) is not int
        or plan["schema"] != 4
        or plan["edit_mode"] != MODE
        or plan["native_policy"] != POLICY
    ):
        raise ValueError("Invalid blank-cell fill contract")
    if (
        baseline is None
        or plan["workspace_id"] != manifest["workspace_id"]
        or plan["baseline_hwpx_sha256"] != manifest["baseline_hwpx_sha256"]
        or r.file_hash(baseline) != plan["baseline_hwpx_sha256"]
    ):
        raise ValueError("Fill contract belongs to another/stale baseline")
    if plan["header_spine"] != header_spine(baseline):
        raise ValueError(
            "Header reading-order scope must equal the independently derived original binding"
        )
    data_path = baseline.parent / "fill-input.json"
    if data_path.exists():
        data = r.read_json(data_path)
        if plan["bindings"] != data["bindings"] or plan["record"] != data["record"]:
            raise ValueError(
                "Fill input changed; a new exact proposal and UI approval are required"
            )
    mapped, draft, active = replay(baseline, plan["bindings"], plan["record"])
    if plan["draft_sha256"] != draft["draft_sha256"]:
        raise ValueError("Fill values/source/budget changed")
    placements = plan["placements"]
    if not isinstance(placements, list) or len(placements) != len(active):
        raise ValueError(
            "Each supplied field needs an explicitly reviewed blank region"
        )
    regions = empty_regions(baseline.with_suffix(".pdf"), manifest["protected_pages"])
    by_id = {item["field_id"]: item for item in mapped["fields"]}
    selected, seen, used = [], set(), set()
    parts = r.archive(baseline)
    roots = {
        part: r.xml_root(raw)
        for part, raw in parts.items()
        if r.SECTIONS.fullmatch(part)
    }
    for placement, item in zip(placements, active):
        if (
            not isinstance(placement, dict)
            or set(placement) != {"field_id", "page", "region"}
            or placement["field_id"] != item["field_id"]
            or placement["field_id"] in seen
            or type(placement["page"]) is not int
        ):
            raise ValueError("Unknown/reordered/duplicate fill placement")
        box = placement["region"]
        if (
            not isinstance(box, list)
            or len(box) != 4
            or any(type(v) not in (int, float) or not math.isfinite(v) for v in box)
            or placement
            not in [{"field_id": item["field_id"], **region} for region in regions]
        ):
            raise ValueError(
                "Region must be an original empty PDF cell interior; no enlargement"
            )
        key = (placement["page"], tuple(box))
        if key in used:
            raise ValueError("Multiple values cannot occupy one PDF cell")
        seen.add(item["field_id"])
        used.add(key)
        target = by_id[item["field_id"]]
        para_path, run_path = target_paths(roots[target["part"]], target["cell_path"])
        # The current native gate requires a source line cache; do not invent one.
        cache = _node(roots[target["part"]], para_path).find(r.HP + "linesegarray")
        if cache is None or not len(cache):
            raise ValueError("Blank paragraph needs a native baseline line cache")
        import hwp_native_plan as native

        native._cache(deepcopy(_node(roots[target["part"]], para_path)), r.HP)
        selected.append(
            {
                **target,
                "paragraph_path": para_path,
                "run_path": run_path,
                "new_text": item["value"],
                "page_hint": placement["page"],
                "boxes": [box],
            }
        )
    return selected


def approval_scope(plan):
    return {
        "fill_policy_confirmed": True,
        "unsigned_template_confirmed": True,
        "header_spine_confirmed": True,
        "header_spine": plan["header_spine"],
        "fields_reviewed": [p["field_id"] for p in plan["placements"]],
        "edit_mode": MODE,
        "native_policy": POLICY,
    }


def _input_parts(before, selected):
    import hwp_review as r

    parts = r.archive(before)
    for part in {item["part"] for item in selected}:
        import hwp_native_plan as native

        root = native.strict_root(parts[part])
        for item in selected:
            if item["part"] != part:
                continue
            run = _node(root, item["run_path"])
            if (
                (run.text or "").strip()
                or len(run) > 1
                or any(n.tag != r.HP + "t" or len(n) or (n.text or "") for n in run)
            ):
                raise ValueError("Target run no longer empty/plain")
            if not len(run):
                ET.SubElement(run, r.HP + "t")
            run[0].text = item["new_text"]
        parts[part] = ET.tostring(root, encoding="utf-8", xml_declaration=True)
    return parts


def write_input(before, target, selected):
    if target.exists():
        raise FileExistsError(target)
    parts = _input_parts(before, selected)
    with zipfile.ZipFile(target, "x", compression=zipfile.ZIP_DEFLATED) as archive:
        for name, raw in parts.items():
            archive.writestr(
                name,
                raw,
                compress_type=zipfile.ZIP_STORED
                if name == "mimetype"
                else zipfile.ZIP_DEFLATED,
            )


def structural_gate(before, after, selected, declared_header_spine=None):
    """Compare to EXACT declared insertion, then retain all existing native locks."""
    import hwp_review as r
    import hwp_native_plan as native
    import fitz

    parts, output = _input_parts(before, selected), r.archive(after)
    if set(parts) != set(output):
        raise ValueError("Fill changed archive membership")
    expected_spine = header_spine(before)
    if declared_header_spine != expected_spine:
        raise ValueError("Explicit exact header reading-order declaration required")
    header_transition_applied = False
    if expected_spine is not None:
        part = expected_spine["part"]
        root = native.strict_root(parts[part])
        changed = native.strict_root(output[part])
        refs = [
            node
            for node in changed.findall(native.OPF + "spine/" + native.OPF + "itemref")
            if node.get("idref") == expected_spine["idref"]
        ]
        if len(refs) != 1 or refs[0].get("linear") not in (None, "yes", "no"):
            raise ValueError("Invalid candidate header reading-order binding")
        original = expected_spine["before"] or "yes"
        actual = refs[0].get("linear", "yes")
        # Permission to change to 'no' is NOT a requirement to change it.
        # Omitted/explicit 'yes' share OPF semantics and the existing native lock.
        if actual != original:
            if actual != expected_spine["after"]:
                raise ValueError("Unapproved header reading-order transition")
            ref = next(
                node
                for node in root.findall(native.OPF + "spine/" + native.OPF + "itemref")
                if node.get("idref") == expected_spine["idref"]
            )
            ref.set("linear", actual)
            parts[part] = ET.tostring(root, encoding="utf-8", xml_declaration=True)
            header_transition_applied = True
    preview = "Preview/PrvImage.png"
    if preview in parts and parts[preview] != output[preview]:
        if (
            not output[preview].startswith(b"\x89PNG\r\n\x1a\n")
            or len(output[preview]) > 1024 * 1024
        ):
            raise ValueError("Invalid generated fill preview")
        a, b = fitz.Pixmap(parts[preview]), fitz.Pixmap(output[preview])
        if (a.width, a.height, a.n, a.alpha) != (b.width, b.height, b.n, b.alpha):
            raise ValueError("Fill preview dimensions/format changed")
        parts[preview] = output[preview]
    # This reference represents declared text insertion only, NEVER a repaired candidate.
    with tempfile.TemporaryDirectory(prefix="hwp-fill-lock-") as directory:
        reference = Path(directory) / "declared-fill.hwpx"
        with zipfile.ZipFile(reference, "x") as archive:
            for name, raw in parts.items():
                archive.writestr(name, raw)
        nodes, _ = r.index_hwpx(reference)
        edits = []
        for item in selected:
            match = [
                n
                for n in nodes
                if n["part"] == item["part"]
                and n["path"][: len(item["run_path"])] == item["run_path"]
            ]
            if len(match) != 1 or match[0]["old_text"] != item["new_text"]:
                raise ValueError("Declared fill target cannot be rebound exactly")
            edits.append(
                {
                    "anchor_id": match[0]["anchor_id"],
                    "old_text": item["new_text"],
                    "new_text": item["new_text"],
                }
            )
        result = native.structural_gate(
            reference,
            after,
            {
                "schema": 2,
                "edit_mode": native.MODE,
                "native_policy": native.POLICY,
                "edits": edits,
            },
        )
    return {
        **result,
        "policy": POLICY,
        "exact_empty_cells_filled": len(selected),
        "generated_preview_declared": True,
        "declared_header_spine": expected_spine,
        "header_spine_transition_applied": header_transition_applied,
    }


def _glyphs_readable(chars):
    """Conservative geometry guard: reject severe overprinting, not normal kerning.
    Combining marks intentionally share a base glyph; exclude those from pairs.
    Containment/text extraction alone can accept hundreds of overlapping letters.
    """
    import fitz
    import unicodedata

    def area(box):
        return max(0, box.width) * max(0, box.height)

    boxes = [
        fitz.Rect(box)
        for char, box in chars
        if char.strip()
        and not unicodedata.category(char).startswith("M")
        and area(fitz.Rect(box)) > 0
    ]
    active = []
    for box in sorted(boxes, key=lambda value: value.x0):
        active = [other for other in active if other.x1 > box.x0]
        for other in active:
            overlap = area(box & other)
            if overlap > 0.5 * min(area(box), area(other)):
                return False
        active.append(box)
    return True


def rendered_text_verification(before, after, selected):
    """Same strict gate, with bounded codes/addresses only, never source values."""
    import fitz
    import hwp_review as r

    report = {
        "policy": RENDER_POLICY,
        "passed": False,
        "issues": [],
        "issue_count": 0,
        "truncated": False,
    }
    seen = set()

    def issue(code, page=None, field=None):
        field_id = field["field_id"] if field else None
        identity = (code, page, field_id)
        if identity in seen:
            return
        seen.add(identity)
        report["issue_count"] += 1
        if len(report["issues"]) < RENDER_ISSUE_LIMIT:
            report["issues"].append({"code": code, "page": page, "field_id": field_id})
        else:
            report["truncated"] = True

    with fitz.open(before) as a, fitz.open(after) as b:
        if len(a) != len(b):
            issue("page_count_changed")
            return report
        for number in range(len(a)):
            fields = [item for item in selected if item["page_hint"] == number + 1]
            regions = [fitz.Rect(item["boxes"][0]) for item in fields]
            before_chars, after_chars = _characters(a[number]), _characters(b[number])
            for field, rect in zip(fields, regions):
                if any(
                    c.strip() and rect.intersects(fitz.Rect(box))
                    for c, box in before_chars
                ):
                    issue("baseline_cell_not_empty", number + 1, field)
            outside = []
            texts = ["" for _ in fields]
            glyphs = [[] for _ in fields]
            for char, box in after_chars:
                overlaps = [
                    i
                    for i, rect in enumerate(regions)
                    if rect.intersects(fitz.Rect(box))
                ]
                if overlaps and char.strip():
                    if len(overlaps) != 1 or not regions[overlaps[0]].contains(
                        fitz.Rect(box)
                    ):
                        for index in overlaps:
                            issue("glyph_outside_cell", number + 1, fields[index])
                        continue
                    texts[overlaps[0]] += char
                    glyphs[overlaps[0]].append((char, box))
                else:
                    outside.append(char)
            if r.normalized("".join(outside)) != r.normalized(
                "".join(c for c, _ in before_chars)
            ):
                issue("outside_text_changed", number + 1)
            for text, chars, field in zip(texts, glyphs, fields):
                if r.normalized(text) != r.normalized(field["new_text"]):
                    issue("rendered_text_mismatch", number + 1, field)
                if not _glyphs_readable(chars):
                    issue("glyph_overlap", number + 1, field)
    report["passed"] = report["issue_count"] == 0
    return report


def rendered_text_gate(before, after, selected):
    return rendered_text_verification(before, after, selected)["passed"]


def review_render_issues(report, plan, page_count):
    """Treat stored diagnostic metadata as data, not trusted page/field navigation."""
    if (
        type(plan.get("schema")) is not int
        or plan["schema"] != 4
        or report.get("status") != "blocked"
        or report.get("rendered_page_text_exact") is not False
    ):
        return []
    verification = report.get("rendered_text_verification")
    if (
        not isinstance(verification, dict)
        or verification.get("policy") != RENDER_POLICY
        or verification.get("passed") is not False
        or not isinstance(verification.get("issues"), list)
        or len(verification["issues"]) > RENDER_ISSUE_LIMIT
    ):
        return []
    placements = {
        item["field_id"]: item["page"]
        for item in plan.get("placements", [])
        if isinstance(item, dict)
        and isinstance(item.get("field_id"), str)
        and type(item.get("page")) is int
    }
    result = []
    for item in verification["issues"]:
        if (
            not isinstance(item, dict)
            or set(item) != {"code", "page", "field_id"}
            or not isinstance(item["code"], str)
            or item["code"] not in RENDER_CODES
        ):
            continue
        page, field_id = item["page"], item["field_id"]
        if item["code"] == "page_count_changed":
            if page is not None or field_id is not None:
                continue
        else:
            if type(page) is not int or not 1 <= page <= page_count:
                continue
            if item["code"] == "outside_text_changed":
                if field_id is not None:
                    continue
            elif not isinstance(field_id, str) or placements.get(field_id) != page:
                continue
        safe = {"code": item["code"], "page": page, "field_id": field_id}
        if safe not in result:
            result.append(safe)
    return result
