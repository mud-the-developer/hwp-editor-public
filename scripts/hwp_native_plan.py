"""Explicit schema-2 native paragraph contracts. Schema-1 locks are not weakened."""
from collections import Counter
import math
from pathlib import Path
import re
import xml.etree.ElementTree as ET

MODE = "native-paragraph-v1"
POLICY = "selected-lines-caret-modified-date-preview-v1"
OPF = "{http://www.idpf.org/2007/opf/}"
APP = "{http://www.hancom.co.kr/hwpml/2011/app}"
LINE_ATTRIBUTES = {"textpos", "vertpos", "vertsize", "textheight", "baseline", "spacing", "horzpos", "horzsize", "flags"}


def envelope(anchor):
    boxes = anchor["boxes"]
    return [min(b[0] for b in boxes), min(b[1] for b in boxes),
            max(b[2] for b in boxes), max(b[3] for b in boxes)]


def paragraph(root, path, hp):
    node, parents = root, []
    for index in path:
        parents.append(node)
        # Anchor paths count elements, not comments/PIs; preserve them for the
        # final lock comparison without letting them shift paragraph addresses.
        node = [child for child in node if isinstance(child.tag, str)][index]
    para = next((p for p in reversed(parents) if p.tag == hp + "p"), None)
    if para is None:
        raise ValueError("Native text has no paragraph")
    return para


def strict_root(raw):
    import hwp_review as r
    r.xml_root(raw)  # Reject DTD/entities before the comment/PI-preserving parse.
    return ET.fromstring(raw, parser=ET.XMLParser(target=ET.TreeBuilder(insert_comments=True, insert_pis=True)))


def require_yes_spine(raw):
    """Read-only execution eligibility, not approval or a preservation attestation."""
    if raw is None:
        raise ValueError("Reading-order preservation unverified: missing OPF; no Native binding")
    package = strict_root(raw)
    spines = package.findall(OPF + "spine")
    if package.tag != OPF + "package" or len(spines) != 1 or not len(spines[0]):
        raise ValueError("Reading-order preservation unverified: ambiguous OPF spine; no Native binding")
    refs = list(spines[0])
    if any(node.tag != OPF + "itemref" or not node.get("idref") or node.get("linear") not in (None, "yes") for node in refs) or len({node.get("idref") for node in refs}) != len(refs):
        raise ValueError("Reading-order preservation unverified for NO/unknown spine metadata; no Native binding")


def validate(plan, manifest, baseline):
    import hwp_review as r
    if set(plan) != {"schema", "workspace_id", "baseline_hwpx_sha256", "edit_mode", "native_policy", "edits"} or type(plan["schema"]) is not int or plan["schema"] != 2:
        raise ValueError("Invalid native plan schema")
    if plan["edit_mode"] != MODE or plan["native_policy"] != POLICY:
        raise ValueError("Unsupported native policy")
    if manifest.get("source") and Path(manifest["source"]).suffix.lower() != ".hwp":
        raise ValueError("Native paragraph editing requires an HWP source")
    if baseline is None or r.file_hash(baseline) != manifest["baseline_hwpx_sha256"]:
        raise ValueError("Native plan requires the immutable baseline")
    if not isinstance(plan["edits"], list):
        raise ValueError("Native edits must be a list")
    legacy_edits = []
    for edit in plan["edits"]:
        if not isinstance(edit, dict) or set(edit) != {"anchor_id", "old_text", "new_text", "region"}:
            raise ValueError("Native edits require exact text and an explicit region")
        legacy_edits.append({key: edit[key] for key in ("anchor_id", "old_text", "new_text")})
    legacy = {key: plan[key] for key in ("workspace_id", "baseline_hwpx_sha256")}
    legacy.update(schema=1, edits=legacy_edits)
    # Address/text validation only; native mode never opens the invalid patched XML.
    selected = r.validate_plan(legacy, manifest)
    parts = r.archive(baseline)
    nodes, _ = r.index_hwpx(baseline)
    by_id = {a["anchor_id"]: a for a in nodes}
    counts = Counter(a["old_text"] for a in nodes)
    for edit, anchor in zip(plan["edits"], selected):
        region = edit["region"]
        if (not isinstance(region, list) or len(region) != 4 or
                any(type(v) not in (int, float) or not math.isfinite(v) for v in region) or
                region != envelope(anchor)):
            raise ValueError("Region must equal the proposed original paragraph envelope; no cell-wide enlargement")
        if not anchor["old_text"] or counts[anchor["old_text"]] != 1:
            raise ValueError("Native literal must be unique and nonempty")
        node = by_id.get(anchor["anchor_id"])
        if node is None or node["old_text"] != edit["old_text"]:
            raise ValueError("Stale native address")
        para = paragraph(r.xml_root(parts[node["part"]]), node["path"], r.HP)
        runs = para.findall(r.HP + "run")
        if (not edit["new_text"] or len(list(para.iter(r.HP + "t"))) != 1 or not runs or
                any(child.tag not in (r.HP + "run", r.HP + "linesegarray") for child in para) or
                any(child.tag != r.HP + "t" or len(child) for run in runs for child in run)):
            raise ValueError("Native editing supports a single plain text node per paragraph only")
    return selected


def proposal(legacy, manifest, baseline):
    import hwp_review as r
    anchors = r.validate_plan(legacy, manifest)
    plan = {key: legacy[key] for key in ("workspace_id", "baseline_hwpx_sha256")}
    plan.update(schema=2, edit_mode=MODE, native_policy=POLICY,
                edits=[{**edit, "region": envelope(anchor)} for edit, anchor in zip(legacy["edits"], anchors)])
    validate(plan, manifest, baseline)
    return plan


def _cache(para, hp):
    caches = para.findall(hp + "linesegarray")
    if len(caches) != 1 or caches[0].attrib or not len(caches[0]):
        raise ValueError("Native output needs one nonempty selected paragraph line cache")
    length = len("".join(t.text or "" for t in para.iter(hp + "t")).encode("utf-16-le")) // 2
    positions = []
    for line in caches[0]:
        if line.tag != hp + "lineseg" or len(line) or (line.text or "").strip() or not set(line.attrib) <= LINE_ATTRIBUTES:
            raise ValueError("Unknown content in native line cache")
        if "textpos" not in line.attrib or any(not re.fullmatch(r"-?\d{1,11}", v) for v in line.attrib.values()):
            raise ValueError("Invalid native cache numbers")
        if any(not -(2**31) <= int(v) < 2**32 for v in line.attrib.values()):
            raise ValueError("Native cache number exceeds bounds")
        positions.append(int(line.attrib["textpos"]))
    if positions[0] != 0 or positions != sorted(set(positions)) or any(p < 0 or p > length for p in positions):
        raise ValueError("Invalid native cached text positions")
    para.remove(caches[0])


def structural_gate(before: Path, after: Path, plan: dict):
    """Allow only declared derived changes; lock all other structure/assets/styles."""
    import hwp_review as r
    if plan.get("schema") != 2 or plan.get("native_policy") != POLICY or plan.get("edit_mode") != MODE:
        raise ValueError("Explicit native contract required")
    a, b = r.archive(before), r.archive(after)
    if set(a) != set(b):
        raise ValueError("Archive membership changed")
    selected = {e["anchor_id"]: e for e in plan["edits"]}
    if len(selected) != len(plan["edits"]):
        raise ValueError("Duplicate native edits")
    seen, derived = set(), []
    for part in a:
        if r.SECTIONS.fullmatch(part):
            ar, br = strict_root(a[part]), strict_root(b[part])
            an, bn = r.text_nodes(part, a[part]), r.text_nodes(part, b[part])
            if len(an) != len(bn):
                raise ValueError("Native text node count changed")
            for old, new in zip(an, bn):
                if old["anchor_id"] != new["anchor_id"]:
                    raise ValueError("Native node addressing changed")
                edit = selected.get(old["anchor_id"])
                if new["old_text"] != (edit["new_text"] if edit else old["old_text"]) or (edit and edit["old_text"] != old["old_text"]):
                    raise ValueError("Unapproved native text changed")
                if edit:
                    seen.add(old["anchor_id"])
                    # Locate using normal element paths before preserving comments/PIs.
                    # Comments/PIs are forbidden additions by the final full tree compare.
                    for root in (ar, br):
                        para = paragraph(root, old["path"], r.HP)
                        _cache(para, r.HP)
            for root in (ar, br):
                for node in root.iter(r.HP + "t"):
                    node.text = "__VERIFIED_NATIVE_TEXT__"
            if ET.tostring(ar) != ET.tostring(br):
                raise ValueError("Native changed locked structure, font, table, controls or unselected cache")
            if a[part] != b[part]:
                derived.append(part + ": selected text/line cache only")
        elif part == "settings.xml":
            ar, br = strict_root(a[part]), strict_root(b[part])
            for root in (ar, br):
                nodes = root.findall(APP + "CaretPosition")
                if len(nodes) != 1 or set(nodes[0].attrib) != {"listIDRef", "paraIDRef", "pos"} or len(nodes[0]) or (nodes[0].text or "").strip():
                    raise ValueError("Invalid caret metadata")
                if any(not re.fullmatch(r"\d{1,10}", v) for v in nodes[0].attrib.values()):
                    raise ValueError("Invalid caret positions")
                nodes[0].attrib = {k: "__CURSOR__" for k in nodes[0].attrib}
            if ET.tostring(ar) != ET.tostring(br):
                raise ValueError("Unapproved application settings changed")
            if a[part] != b[part]:
                derived.append(part + ": caret only")
        elif part == "Contents/content.hpf":
            ar, br = strict_root(a[part]), strict_root(b[part])
            for root in (ar, br):
                dates = root.findall(f'{OPF}metadata/{OPF}meta[@name="ModifiedDate"]')
                if len(dates) != 1 or len(dates[0]) or not re.fullmatch(r"\d{4}-\d\d-\d\dT\d\d:\d\d:\d\dZ", dates[0].text or ""):
                    raise ValueError("Invalid modification date metadata")
                dates[0].text = "__MODIFIED_DATE__"
                for item in root.findall(OPF + "spine/" + OPF + "itemref"):
                    # OPF omitted linear is semantically 'yes'; no other normalization.
                    if item.get("linear", "yes") == "yes":
                        item.set("linear", "yes")
            if ET.tostring(ar) != ET.tostring(br):
                raise ValueError("Unapproved package metadata/resource mapping changed")
            if a[part] != b[part]:
                derived.append(part + ": modified date/default spine linear only")
        elif part == "Preview/PrvText.txt":
            b[part].decode("utf-8", errors="strict")
            if len(b[part]) > 128 * 1024:
                raise ValueError("Native text preview exceeds bounds")
            if a[part] != b[part]:
                derived.append(part + ": native text preview only")
        elif a[part] != b[part]:
            raise ValueError("Locked native asset/style/metadata changed: " + part)
    if seen != set(selected):
        raise ValueError("Missing native edited anchors")
    return {"policy": POLICY, "allowed_derived_changes": derived, "locked_table_font_controls_assets": True}
