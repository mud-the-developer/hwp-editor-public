"""Closed table/picture caption + same-selection CROSSREF support (source only)."""

import re
import xml.etree.ElementTree as ET

HP = "{http://www.hancom.co.kr/hwpml/2011/paragraph}"
HH = "{http://www.hancom.co.kr/hwpml/2011/head}"
POLICY = "owned-table-picture-crossref-v1"
FIELD_ID = "628650598"  # Hancom's %xrf control identifier, not an instance identity.
KINDS = {HP + "pic": "PICTURE", HP + "tbl": "TABLE"}
REF_TYPES = {"PICTURE": "TARGET_FIGURE", "TABLE": "TARGET_TABLE"}
PARAMETERS = (
    ("booleanParam", "Fiexde", "1"),
    ("integerParam", "Prop", "0"),
    ("stringParam", "Command", None),
    ("stringParam", "RefPath", None),
    ("stringParam", "RefType", None),
    ("stringParam", "RefContentType", "OBJECT_TYPE_NUMBER"),
    ("booleanParam", "RefHyperLink", "false"),
    ("stringParam", "RefOpenType", "HWPHYPERLINK_JUMP_CURRENTTAB"),
)


def enabled(specification):
    if "caption_policy" not in specification:
        return False
    if specification["caption_policy"] != POLICY:
        raise ValueError("Unsupported explicit caption policy")
    return True


def _identifier(value, maximum=4294967295):
    if not isinstance(value, str) or re.fullmatch(r"[1-9][0-9]*|0", value) is None:
        raise ValueError("Invalid caption/reference identity")
    try:
        number = int(value)
    except ValueError as error:
        raise ValueError("Invalid caption/reference identity") from error
    if number > maximum:
        raise ValueError("Caption/reference identity exceeds its supported range")
    return value


def _counts(nodes):
    counts = dict.fromkeys(REF_TYPES, 0)
    for root in nodes:
        for node in root.iter():
            kind = node.get("numberingType")
            if kind in counts:
                counts[kind] += 1
    return counts


def bases(source_section, bounds, header, incoming):
    start, end = bounds
    prefix = list(source_section)[:start]
    suffix = list(source_section)[end:]
    outside = prefix + suffix
    begin = header.find(HH + "beginNum")
    if begin is None or any(begin.get(key) != "1" for key in ("pic", "tbl")):
        raise ValueError("Caption policy requires source picture/table starts of1")
    for root in list(source_section) + list(incoming):
        for node in root.iter(HP + "newNum"):
            if node.get("numType") in REF_TYPES:
                raise ValueError("Caption numbering resets need a separate contract")
    for root in outside:
        if any(node.get("numType") in REF_TYPES for node in root.iter(HP + "autoNum")):
            raise ValueError("Outside automatic captions need a separate contract")
        if any(node.get("type") == "CROSSREF" for node in root.iter(HP + "fieldBegin")):
            raise ValueError("Outside cross-references need a separate contract")
    old_counts = _counts(list(source_section)[start:end])
    new_counts = _counts(incoming)
    suffix_counts = _counts(suffix)
    if any(
        suffix_counts[kind] and old_counts[kind] != new_counts[kind]
        for kind in REF_TYPES
    ):
        raise ValueError("Numbered suffix objects would change their numbering")
    return _counts(prefix)


def inspect(nodes, offsets=None):
    """Validate the complete closed model; return handles for deterministic remapping."""
    offsets = offsets or dict.fromkeys(REF_TYPES, 0)
    all_nodes = [node for root in nodes for node in root.iter()]
    forbidden = {
        HP + name
        for name in ("secPr", "colPr", "ole", "equation", "video", "chart", "script")
    }
    controls = {HP + "autoNum", HP + "fieldBegin", HP + "fieldEnd"}
    if any(
        node.tag in forbidden
        or (
            node.tag == HP + "ctrl"
            and (len(node) != 1 or any(child.tag not in controls for child in node))
        )
        for node in all_nodes
    ):
        raise ValueError("Unsupported control in the closed caption/reference model")
    parents = {
        child: parent for root in nodes for parent in root.iter() for child in parent
    }
    targets = {}
    counters = []
    ordinals = dict.fromkeys(REF_TYPES, 0)
    for node in all_nodes:
        if node.tag not in KINDS:
            if node.get("numberingType") in REF_TYPES:
                raise ValueError(
                    "Imported numbered shapes other than tables/pictures are unsupported"
                )
            continue
        kind = KINDS[node.tag]
        if node.get("numberingType") != kind:
            raise ValueError(
                "Imported caption object must have its explicit numbering type"
            )
        identity = _identifier(node.get("id"))
        if identity in targets or node.get("instid", identity) != identity:
            raise ValueError("Ambiguous caption object identity or instid alias")
        ancestor = parents.get(node)
        while ancestor is not None:
            if ancestor.tag in KINDS:
                raise ValueError("Nested caption objects need a separate contract")
            ancestor = parents.get(ancestor)
        caps = node.findall(HP + "caption")
        if len(caps) != 1 or caps[0].get("side") != (
            "TOP" if kind == "TABLE" else "BOTTOM"
        ):
            raise ValueError(
                "Each table/picture needs one directly owned TOP/BOTTOM caption"
            )
        caption = caps[0]
        numbers = list(caption.iter(HP + "autoNum"))
        ordinals[kind] += 1
        expected = ordinals[kind] + offsets[kind]
        if (
            len(numbers) != 1
            or numbers[0].get("numType") != kind
            or numbers[0].get("num") != str(expected)
        ):
            raise ValueError("Caption counter type, ownership or sequence is invalid")
        ancestor = numbers[0]
        for tag in ("ctrl", "run", "p", "subList", "caption"):
            ancestor = parents.get(ancestor)
            if ancestor is None or ancestor.tag != HP + tag:
                raise ValueError("Caption counter is outside its native control chain")
        if ancestor is not caption:
            raise ValueError("Caption counter belongs to a different caption")
        counters.extend(numbers)
        targets[identity] = {
            "node": node,
            "kind": kind,
            "counter": numbers[0],
            "ordinal": ordinals[kind],
            "number": expected,
        }
    if {id(node) for node in counters} != {
        id(node) for node in all_nodes if node.tag == HP + "autoNum"
    }:
        raise ValueError("Orphan automatic counter outside an owned caption")
    references = []
    begins = set()
    ends = set()
    begin_ids = set()
    for run in (node for node in all_nodes if node.tag == HP + "run"):
        if not any(
            child.tag in (HP + "fieldBegin", HP + "fieldEnd")
            for ctrl in run.findall(HP + "ctrl")
            for child in ctrl
        ):
            continue
        if (
            [node.tag for node in run] != [HP + "ctrl", HP + "t", HP + "ctrl"]
            or len(run[0]) != 1
            or len(run[2]) != 1
        ):
            raise ValueError("CROSSREF must be a single isolated begin/text/end run")
        begin, display, end = run[0][0], run[1], run[2][0]
        if begin.tag != HP + "fieldBegin" or end.tag != HP + "fieldEnd" or len(display):
            raise ValueError("Malformed CROSSREF boundaries or cached text")
        identity = _identifier(begin.get("id"), 2147483647)
        expected_attrs = {
            "id": identity,
            "type": "CROSSREF",
            "name": "",
            "editable": "0",
            "dirty": "0",
            "zorder": "-1",
            "fieldid": FIELD_ID,
            "metaTag": "",
        }
        if (
            begin.attrib != expected_attrs
            or end.attrib != {"beginIDRef": identity, "fieldid": FIELD_ID}
            or len(end)
            or identity in begin_ids
        ):
            raise ValueError("Invalid or duplicate CROSSREF identity/pair")
        begin_ids.add(identity)
        if (
            len(begin) != 1
            or begin[0].tag != HP + "parameters"
            or begin[0].attrib != {"cnt": "8", "name": ""}
            or len(begin[0]) != 8
        ):
            raise ValueError(
                "Only the canonical eight-parameter CROSSREF model is supported"
            )
        params = {}
        for parameter, (tag, name, value) in zip(begin[0], PARAMETERS, strict=True):
            if (
                parameter.tag != HP + tag
                or parameter.attrib != {"name": name}
                or len(parameter)
                or (value is not None and parameter.text != value)
            ):
                raise ValueError("Unsupported CROSSREF parameter")
            params[name] = parameter
        match = re.fullmatch(r"\?#([0-9]+);([01]);1;0;0;", params["Command"].text or "")
        if match is None:
            raise ValueError("Unsupported CROSSREF command or external path")
        target_id, wire_kind = match.groups()
        target = targets.get(target_id)
        if (
            target is None
            or params["RefPath"].text != "?#" + target_id + ";"
            or params["RefType"].text != REF_TYPES[target["kind"]]
            or wire_kind != ("0" if target["kind"] == "TABLE" else "1")
        ):
            raise ValueError("CROSSREF target is missing, external, or inconsistent")
        if display.text != str(target["number"]):
            raise ValueError("CROSSREF cached number disagrees with its caption")
        ancestor = parents.get(run)
        while ancestor is not None:
            if ancestor.tag == HP + "caption":
                raise ValueError("Cross-references inside captions are unsupported")
            ancestor = parents.get(ancestor)
        begins.add(id(begin))
        ends.add(id(end))
        references.append(
            {
                "begin": begin,
                "end": end,
                "display": display,
                "params": params,
                "identity": identity,
                "target": target_id,
                "wire_kind": wire_kind,
            }
        )
    if begins != {
        id(node) for node in all_nodes if node.tag == HP + "fieldBegin"
    } or ends != {id(node) for node in all_nodes if node.tag == HP + "fieldEnd"}:
        raise ValueError("Orphan, nested or cross-boundary field")
    return {"targets": targets, "references": references}


def remap(model, object_ids, field_ids, offsets):
    for identity, target in model["targets"].items():
        node = target["node"]
        if node.get("id") != object_ids[identity]:
            raise ValueError("Caption object identity allocation disagrees")
        if "instid" in node.attrib:
            node.set("instid", object_ids[identity])
        target["number"] = target["ordinal"] + offsets[target["kind"]]
        target["counter"].set("num", str(target["number"]))
    for reference in model["references"]:
        reference["end"].set("beginIDRef", field_ids[reference["identity"]])
        target_id = object_ids[reference["target"]]
        reference["params"]["RefPath"].text = "?#" + target_id + ";"
        reference["params"]["Command"].text = (
            "?#" + target_id + ";" + reference["wire_kind"] + ";1;0;0;"
        )
        reference["display"].text = str(model["targets"][reference["target"]]["number"])


def reference_run(target_id, kind, number, char_pr, begin_id):
    """Author only the closed native number-reference model validated above."""
    target_id = _identifier(target_id)
    begin_id = _identifier(begin_id, 2147483647)
    if (
        kind not in REF_TYPES
        or not isinstance(number, int)
        or isinstance(number, bool)
        or number < 1
    ):
        raise ValueError("Invalid cross-reference kind or cached number")
    run = ET.Element(HP + "run", {"charPrIDRef": str(char_pr)})
    ctrl = ET.SubElement(run, HP + "ctrl")
    begin = ET.SubElement(
        ctrl,
        HP + "fieldBegin",
        {
            "id": begin_id,
            "type": "CROSSREF",
            "name": "",
            "editable": "0",
            "dirty": "0",
            "zorder": "-1",
            "fieldid": FIELD_ID,
            "metaTag": "",
        },
    )
    params = ET.SubElement(begin, HP + "parameters", {"cnt": "8", "name": ""})
    values = {
        "Command": "?#"
        + target_id
        + ";"
        + ("0" if kind == "TABLE" else "1")
        + ";1;0;0;",
        "RefPath": "?#" + target_id + ";",
        "RefType": REF_TYPES[kind],
    }
    for tag, name, value in PARAMETERS:
        ET.SubElement(params, HP + tag, {"name": name}).text = (
            value if value is not None else values[name]
        )
    ET.SubElement(run, HP + "t").text = str(number)
    ctrl = ET.SubElement(run, HP + "ctrl")
    ET.SubElement(ctrl, HP + "fieldEnd", {"beginIDRef": begin_id, "fieldid": FIELD_ID})
    return run


def summary(model, offsets):
    return {
        "caption_policy": POLICY,
        "native_picture_captions": sum(
            target["kind"] == "PICTURE" for target in model["targets"].values()
        ),
        "native_table_captions": sum(
            target["kind"] == "TABLE" for target in model["targets"].values()
        ),
        "body_cross_references": len(model["references"]),
        "caption_number_bases": offsets,
    }
