"""Exact-region template REVIEW drafts. Never a Native qualification.

Only the local review UI persists bounded draft permission and exact artifact
acceptance. Inputs, leases and failed attempts remain immutable; no Native route.
"""

from collections import Counter
from copy import deepcopy
from datetime import datetime, timezone
from io import BytesIO
from pathlib import Path
import json
import re
import secrets
import xml.etree.ElementTree as ET
import zipfile

import hwp_review as review
import hwp_object_captions as captions

HP = review.HP
HH = "{http://www.hancom.co.kr/hwpml/2011/head}"
HC = "{http://www.hancom.co.kr/hwpml/2011/core}"
OPF = "{http://www.idpf.org/2007/opf/}"
PART = "Contents/section0.xml"
CONTRACT = "template-review-draft-v2"
POLICY = "append-resources-preserve-outside-xml-review-reflow-v1"
CONFIRMATIONS = (
    "replace_exact_region",
    "institution_scope",
    "resources_reviewed",
    "review_reflow",
    "caption_policy",
    "not_submission_or_native_qualified",
)
CATEGORIES = (
    "borderFills",
    "charProperties",
    "tabProperties",
    "numberings",
    "bullets",
    "paraProperties",
    "styles",
)
REFS = {
    "borderFillIDRef": "borderFills",
    "charPrIDRef": "charProperties",
    "paraPrIDRef": "paraProperties",
    "tabPrIDRef": "tabProperties",
    "styleIDRef": "styles",
    "nextStyleIDRef": "styles",
}
LANGS = {
    key.lower(): key
    for key in (
        "HANGUL",
        "LATIN",
        "HANJA",
        "JAPANESE",
        "OTHER",
        "SYMBOL",
        "USER",
    )
}


def node_hash(nodes):
    return review.plan_hash(
        {"nodes": [ET.tostring(node, encoding="unicode") for node in nodes]}
    )


def texts(nodes):
    result = []
    for node in nodes:
        for paragraph in node.iter(HP + "p"):
            chunks = []
            for run in paragraph.findall(HP + "run"):
                for child in run:
                    if child.tag == HP + "t":
                        chunks.append("".join(child.itertext()))
                    elif child.tag == HP + "ctrl":
                        chunks.extend(
                            number.get("num", "")
                            for number in child.iter(HP + "autoNum")
                        )
            value = "".join(chunks).strip()
            if value:
                result.append(value)
    return result


def parts_from(raw):
    parts = review.archive(BytesIO(raw))
    if {name for name in parts if review.SECTIONS.fullmatch(name)} != {PART}:
        raise ValueError("Only one explicitly selected HWPX section is supported")
    if any("signat" in name.lower() or "encrypt" in name.lower() for name in parts):
        raise ValueError("Signed or encrypted input is unsupported")
    for name in (PART, "Contents/header.xml", "Contents/content.hpf"):
        blob = parts[name]
        body = re.sub(rb"^\s*<\?xml[^?]*\?>", b"", blob, count=1)
        if any(token in body for token in (b"<!DOCTYPE", b"<!ENTITY", b"<!--", b"<?")):
            raise ValueError(
                "DTD, comments or processing instructions are outside this contract"
            )
    return parts


def selected(root, ranges):
    if not isinstance(ranges, list) or not 1 <= len(ranges) <= 4:
        raise ValueError("Explicit ordered paragraph ranges are required")
    children = list(root)
    result = []
    last = -1
    for bounds in ranges:
        if (
            not isinstance(bounds, list)
            or len(bounds) != 2
            or any(type(value) is not int for value in bounds)
        ):
            raise ValueError("Range bounds must be integers")
        start, end = bounds
        if not 0 <= start < end <= len(children) or start < last:
            raise ValueError("Range is empty, overlapping or outside the section")
        group = children[start:end]
        if any(node.tag != HP + "p" for node in group):
            raise ValueError("Only explicit top-level paragraphs may be selected")
        result.extend(group)
        last = end
    return result


def _manifest(parts):
    root = review.xml_root(parts["Contents/content.hpf"])
    manifest = root.find(OPF + "manifest")
    if manifest is None:
        raise ValueError("HWPX package manifest missing")
    return root, manifest


def _resource_ids(container):
    result = {}
    if container is not None:
        for node in container:
            identifier = node.get("id")
            if identifier is None or not identifier.isdecimal() or identifier in result:
                raise ValueError("Resource IDs must be unambiguous numeric IDs")
            result[identifier] = node
    return result


def _allocate(existing, incoming):
    used = {int(value) for value in existing}
    value = max(used, default=-1) + 1
    result = {}
    for identifier in incoming:
        while value in used:
            value += 1
        if value > 4294967295:
            raise ValueError("No supported object/resource ID remains")
        result[identifier] = str(value)
        used.add(value)
        value += 1
    return result


def _rewrite(node, maps, fonts, images, *, allow_crossrefs=False):
    for item in node.iter():
        if item.tag == HH + "fontRef":
            for attribute, value in list(item.attrib.items()):
                language = LANGS.get(attribute)
                if language is None or value not in fonts.get(language, {}):
                    raise ValueError("Unknown font reference")
                item.set(attribute, fonts[language][value])
        for key, value in list(item.attrib.items()):
            if key in REFS:
                category = REFS[key]
                if value == "0" and category == "borderFills":
                    continue
                if value not in maps[category]:
                    raise ValueError("Unresolved " + key + ": " + value)
                item.set(key, maps[category][value])
            elif key == "binaryItemIDRef":
                if (
                    item.tag == HH + "substFont"
                    and item.get("isEmbedded") == "0"
                    and value == ""
                ):
                    continue
                if value not in images:
                    raise ValueError("Unresolved binary reference")
                item.set(key, images[value])
            elif key == "idRef":
                kind = item.get("type")
                if item.tag != HH + "heading":
                    raise ValueError("Unsupported idRef")
                if kind in ("NONE", "OUTLINE") and value == "0":
                    continue
                category = {"BULLET": "bullets", "NUMBER": "numberings"}.get(kind)
                if category is None or value not in maps[category]:
                    raise ValueError("Unsupported heading reference")
                item.set(key, maps[category][value])
            elif key.endswith("IDRef") and key not in REFS:
                if (
                    allow_crossrefs
                    and item.tag == HP + "fieldEnd"
                    and key == "beginIDRef"
                ):
                    continue
                if key not in ("linkListIDRef", "linkListNextIDRef") or value != "0":
                    raise ValueError("Unsupported reference " + key)


def _dependencies(nodes, resources, *, existing_context=False):
    """Resolve body references; import dependency closures only for foreign context."""
    definitions = {
        name: _resource_ids(resources.find(HH + name)) for name in CATEGORIES
    }
    required = {name: set() for name in CATEGORIES}
    fonts = {language: set() for language in LANGS.values()}
    pending = []

    def collect(root):
        for node in root.iter():
            if node.tag == HH + "fontRef":
                for key, value in node.attrib.items():
                    if key not in LANGS:
                        raise ValueError("Unknown font language reference")
                    fonts[LANGS[key]].add(value)
            for key, value in node.attrib.items():
                if key in REFS:
                    category = REFS[key]
                    if category == "borderFills" and value == "0":
                        continue
                    pending.append((category, value))
                elif key == "idRef" and node.tag == HH + "heading":
                    kind = node.get("type")
                    if kind in ("NONE", "OUTLINE") and value == "0":
                        continue
                    category = {"BULLET": "bullets", "NUMBER": "numberings"}.get(kind)
                    if category is None:
                        raise ValueError("Unsupported heading dependency")
                    pending.append((category, value))

    for node in nodes:
        collect(node)
    while pending:
        category, identifier = pending.pop()
        if identifier in required[category]:
            continue
        definition = definitions[category].get(identifier)
        if definition is None:
            raise ValueError("Unresolved reachable " + category + ": " + identifier)
        required[category].add(identifier)
        if not existing_context:
            collect(definition)
    return definitions, required, fonts


def _merge_header(target, donor, image_map, prefix, incoming, *, existing_context=False):
    result = target if existing_context else deepcopy(target)
    destination = result.find(HH + "refList")
    source = donor.find(HH + "refList")
    if destination is None or source is None:
        raise ValueError("Header reference list missing")
    supported = {HH + "fontfaces", *(HH + name for name in CATEGORIES)}
    if any(node.tag not in supported and len(node) for node in source):
        raise ValueError("Donor uses unsupported header resources")
    definitions, required, required_fonts = _dependencies(
        incoming, source, existing_context=existing_context
    )
    maps = {}
    fonts = {}
    for name in CATEGORIES:
        maps[name] = (
            {key: key for key in required[name]}
            if existing_context
            else _allocate(
                _resource_ids(destination.find(HH + name)),
                [key for key in definitions[name] if key in required[name]],
            )
        )
    source_faces = source.find(HH + "fontfaces")
    target_faces = destination.find(HH + "fontfaces")
    if source_faces is None or target_faces is None:
        raise ValueError("Font language tables missing")
    by_language = {node.get("lang"): node for node in target_faces}
    if len(by_language) != len(target_faces):
        raise ValueError("Ambiguous font language")
    for face in source_faces:
        language = face.get("lang")
        if language not in by_language or language not in LANGS.values():
            raise ValueError("Unsupported font language")
        dest = by_language[language]
        available = _resource_ids(face)
        if not required_fonts[language] <= set(available):
            raise ValueError("Reachable font definition missing")
        fonts[language] = (
            {key: key for key in required_fonts[language]}
            if existing_context
            else _allocate(
                _resource_ids(dest),
                [key for key in available if key in required_fonts[language]],
            )
        )
        if existing_context:
            continue
        for font in face:
            if font.get("id") not in required_fonts[language]:
                continue
            copied = deepcopy(font)
            copied.set("id", fonts[language][font.get("id")])
            _rewrite(copied, maps, fonts, image_map)
            dest.append(copied)
        dest.set("fontCnt", str(len(dest)))
    if existing_context:
        return result, maps, fonts
    order = ["fontfaces", *CATEGORIES]
    for name in CATEGORIES:
        source_container = source.find(HH + name)
        if source_container is None or not required[name]:
            continue
        dest = destination.find(HH + name)
        if dest is None:
            dest = ET.Element(HH + name, dict(source_container.attrib))
            position = next(
                (
                    i
                    for i, item in enumerate(destination)
                    if item.tag in supported
                    and order.index(item.tag.rsplit("}", 1)[-1]) > order.index(name)
                ),
                len(destination),
            )
            destination.insert(position, dest)
        for original in source_container:
            if original.get("id") not in required[name]:
                continue
            copied = deepcopy(original)
            copied.set("id", maps[name][original.get("id")])
            _rewrite(copied, maps, fonts, image_map)
            if name == "styles":
                for attribute in ("name", "engName"):
                    if attribute in copied.attrib:
                        copied.set(attribute, prefix + copied.get(attribute, ""))
            dest.append(copied)
        dest.set("itemCnt", str(len(dest)))
    return result, maps, fonts


def _xml(root):
    return ET.tostring(root, encoding="utf-8", xml_declaration=True)


def _zip(source_raw, parts, added):
    output = BytesIO()
    with (
        zipfile.ZipFile(BytesIO(source_raw)) as original,
        zipfile.ZipFile(output, "w") as archive,
    ):
        for info in original.infolist():
            archive.writestr(deepcopy(info), parts[info.filename])
        for name in sorted(added):
            info = zipfile.ZipInfo(name, (2000, 1, 1, 0, 0, 0))
            info.compress_type = zipfile.ZIP_DEFLATED
            archive.writestr(info, parts[name])
    return output.getvalue()


def compose(source_raw, payload_raw, specification):
    """Pure dry-run/compile. No approval, filesystem write or Native activation."""
    source = parts_from(source_raw)
    donor = parts_from(payload_raw)
    # A source-derived text donor already has this exact resource context.
    # Reuse it only when every non-body member is byte-identical, not inferred.
    existing_context = source.keys() == donor.keys() and all(
        value == donor[name] for name, value in source.items() if name != PART
    )
    old_section = review.xml_root(source[PART])
    donor_section = review.xml_root(donor[PART])
    target_range = specification["target_range"]
    removed = selected(old_section, [target_range])
    incoming = deepcopy(selected(donor_section, specification["payload_ranges"]))
    if len(incoming) > 2000:
        raise ValueError("Payload is too large for review")
    modern_captions = captions.enabled(specification)
    start, end = target_range
    outside = list(old_section)[:start] + list(old_section)[end:]
    if not modern_captions and any(
        node.tag == HP + "autoNum" and node.get("numType") == "PICTURE"
        for root in outside
        for node in root.iter()
    ):
        raise ValueError(
            "Existing automatic picture counters outside the region require a separate contract"
        )
    allowed_controls = {HP + "autoNum"}
    if modern_captions:
        allowed_controls.update((HP + "fieldBegin", HP + "fieldEnd"))
    for root in incoming:
        for node in root.iter():
            if node.tag in {
                HP + key
                for key in (
                    "secPr",
                    "colPr",
                    "ole",
                    "equation",
                    "video",
                    "chart",
                    "script",
                    "fieldBegin",
                    "fieldEnd",
                )
            } and not (
                modern_captions and node.tag in (HP + "fieldBegin", HP + "fieldEnd")
            ):
                raise ValueError("Unsupported payload control " + node.tag)
            if node.tag == HP + "ctrl" and any(
                child.tag not in allowed_controls for child in node
            ):
                raise ValueError("Only picture-number controls are supported")
    counters = [node for root in incoming for node in root.iter(HP + "autoNum")]
    pictures = [node for root in incoming for node in root.iter(HP + "pic")]
    caption_model = None
    caption_offsets = None
    if modern_captions:
        caption_offsets = captions.bases(
            old_section,
            target_range,
            review.xml_root(source["Contents/header.xml"]),
            incoming,
        )
        caption_model = captions.inspect(incoming)
    else:
        if any(
            node.get("numType") != "PICTURE" or node.get("num") != str(index)
            for index, node in enumerate(counters, 1)
        ) or len(counters) != len(pictures):
            raise ValueError(
                "Payload picture/caption counters must be sequential from1"
            )
        for pic in pictures:
            cap = pic.find(HP + "caption")
            if (
                cap is None
                or len(list(cap.iter(HP + "autoNum"))) != 1
                or cap.get("side") != "BOTTOM"
            ):
                raise ValueError("Each imported picture needs one owned bottom caption")
    page = old_section.find(".//" + HP + "pagePr")
    if page is None:
        raise ValueError("Source page geometry missing")
    margin = page.find(HP + "margin")
    if margin is None:
        raise ValueError("Source page margins missing")
    try:
        usable = (
            int(page.attrib["width"])
            - int(margin.attrib["left"])
            - int(margin.attrib["right"])
            - int(margin.get("gutter", "0"))
        )
    except (KeyError, ValueError) as error:
        raise ValueError("Invalid source page geometry") from error
    if usable <= 0:
        raise ValueError("Source column width must be positive")
    if any(
        int(node.get("width", "0")) > usable
        for root in incoming
        for node in root.iter(HP + "sz")
    ):
        raise ValueError("Payload exceeds source column width; no automatic shrinking")
    source_hpf, source_manifest = _manifest(source)
    _, donor_manifest = _manifest(donor)
    donor_items = {node.get("id"): node for node in donor_manifest}
    if len(donor_items) != len(donor_manifest):
        raise ValueError("Ambiguous donor manifest")
    prefix = "td_" + review.digest(payload_raw)[:12] + "_"
    image_map = {}
    added = []
    referenced = sorted(
        {
            node.get("binaryItemIDRef")
            for root in incoming
            for node in root.iter(HC + "img")
        }
    )
    existing_ids = {node.get("id") for node in source_manifest}
    if None in existing_ids or len(existing_ids) != len(source_manifest):
        raise ValueError("Source package IDs are ambiguous")
    for ordinal, identifier in enumerate(referenced):
        item = donor_items.get(identifier)
        if item is None or item.get("media-type") != "image/png":
            raise ValueError("Only declared PNG assets may be imported")
        href = item.get("href", "")
        if (
            not href.startswith("BinData/")
            or href not in donor
            or not donor[href].startswith(b"\x89PNG\r\n\x1a\n")
        ):
            raise ValueError("Invalid donor PNG")
        new_id = prefix + str(ordinal)
        path = "BinData/" + new_id + ".png"
        if new_id in existing_ids or path in source:
            raise ValueError("Imported asset namespace collision")
        image_map[identifier] = new_id
        source[path] = donor[href]
        added.append(path)
        ET.SubElement(
            source_manifest,
            OPF + "item",
            {"id": new_id, "href": path, "media-type": "image/png", "isEmbeded": "1"},
        )
    header = review.xml_root(source["Contents/header.xml"])
    begin = header.find(HH + "beginNum")
    if pictures and (begin is None or begin.get("pic") != "1"):
        raise ValueError("Source picture numbering does not start at1")
    new_header, maps, fonts = _merge_header(
        header,
        review.xml_root(donor["Contents/header.xml"]),
        image_map,
        prefix,
        incoming,
        existing_context=existing_context,
    )
    used = {
        int(node.get(key, "0"))
        for node in old_section.iter()
        for key in (
            ("id", "instanceID", "instid") if modern_captions else ("id", "instanceID")
        )
        if node.get(key, "").isdecimal()
    }
    object_ids = {}
    field_ids = {}
    next_id = 1
    z_order = (
        max(
            (
                int(node.get("zOrder", "0"))
                for node in old_section.iter()
                if node.get("zOrder", "").isdecimal()
            ),
            default=0,
        )
        + 1
    )
    for root in incoming:
        _rewrite(root, maps, fonts, image_map, allow_crossrefs=modern_captions)
        for node in root.iter():
            old_identity = node.get("id")
            for key in ("id", "instanceID"):
                if key in node.attrib:
                    while next_id in used:
                        next_id += 1
                    if next_id > 4294967295:
                        raise ValueError("No supported object ID remains")
                    node.set(key, str(next_id))
                    if modern_captions and key == "id":
                        if node.tag in captions.KINDS:
                            object_ids[old_identity] = str(next_id)
                        elif node.tag == HP + "fieldBegin":
                            field_ids[old_identity] = str(next_id)
                    used.add(next_id)
                    next_id += 1
            # Object identity and drawing stack order are different namespaces.
            if "zOrder" in node.attrib:
                if z_order > 2147483647:
                    raise ValueError("Drawing stack order exceeds the supported range")
                node.set("zOrder", str(z_order))
                z_order += 1
        for parent in root.iter():
            for child in list(parent):
                if child.tag == HP + "linesegarray":
                    parent.remove(child)
    if modern_captions:
        if caption_model is None or caption_offsets is None:
            raise ValueError("Caption model was not validated")
        captions.remap(caption_model, object_ids, field_ids, caption_offsets)
        captions.inspect(incoming, caption_offsets)
    section = deepcopy(old_section)
    for node in list(section)[start:end]:
        section.remove(node)
    for offset, node in enumerate(incoming):
        section.insert(start + offset, node)
    source[PART] = _xml(section)
    if not existing_context:
        source["Contents/header.xml"] = _xml(new_header)
    if not existing_context or referenced:
        source["Contents/content.hpf"] = _xml(source_hpf)
    source["Preview/PrvText.txt"] = "\r\n".join(texts(list(section))).encode("utf-8")
    if "Preview/PrvText.txt" not in review.archive(BytesIO(source_raw)):
        added.append("Preview/PrvText.txt")
    raw = _zip(source_raw, source, added)
    report = validate(
        source_raw,
        raw,
        target_range,
        len(incoming),
        added,
        caption_policy=specification.get("caption_policy"),
    )
    report.update(
        {
            "old_text": texts(removed),
            "new_text": texts(incoming),
            "removed_paragraphs": len(removed),
            "removed_pictures": sum(
                len(list(node.iter(HP + "pic"))) for node in removed
            ),
            "inserted_paragraphs": len(incoming),
            "inserted_pictures": len(pictures),
            "inserted_tables": sum(
                len(list(node.iter(HP + "tbl"))) for node in incoming
            ),
            "candidate_sha256": review.digest(raw),
            "resource_imports": {key: 0 if existing_context else len(value) for key, value in maps.items()},
            "font_imports": {key: 0 if existing_context else len(value) for key, value in fonts.items()},
        }
    )
    return raw, report


def validate(
    source_raw, candidate_raw, bounds, inserted_count, added, *, caption_policy=None
):
    source = parts_from(source_raw)
    candidate = parts_from(candidate_raw)
    allowed = {
        PART,
        "Contents/header.xml",
        "Contents/content.hpf",
        "Preview/PrvText.txt",
    }
    if set(candidate) != set(source) | set(added):
        raise ValueError("Package membership changed outside the declared additions")
    for name in source:
        if name not in allowed and source[name] != candidate[name]:
            raise ValueError("Locked package member changed: " + name)
    before = review.xml_root(source[PART])
    after = review.xml_root(candidate[PART])
    start, end = bounds
    if before.attrib != after.attrib or before.text != after.text:
        raise ValueError("Section properties changed")
    if node_hash(list(before)[:start]) != node_hash(list(after)[:start]) or node_hash(
        list(before)[end:]
    ) != node_hash(list(after)[start + inserted_count :]):
        raise ValueError("Paragraphs outside the permitted region changed")
    old_head = review.xml_root(source["Contents/header.xml"])
    new_head = review.xml_root(candidate["Contents/header.xml"])
    if (
        old_head.attrib != new_head.attrib
        or old_head.text != new_head.text
        or [node.tag for node in old_head] != [node.tag for node in new_head]
    ):
        raise ValueError("Source header properties changed")
    for old in old_head:
        new = new_head.find(old.tag)
        if new is None:
            raise ValueError("Source header resource removed")
        if old.tag != HH + "refList":
            if ET.tostring(old) != ET.tostring(new):
                raise ValueError("Source header data changed")
            continue
        if old.attrib != new.attrib:
            raise ValueError("Source reference-list properties changed")
        if len({item.tag for item in new}) != len(new):
            raise ValueError("Duplicate resource containers")
        for container in old:
            dest = new.find(container.tag)
            if dest is None:
                raise ValueError("Source resource container removed")
            if {k: v for k, v in container.attrib.items() if k != "itemCnt"} != {
                k: v for k, v in dest.attrib.items() if k != "itemCnt"
            }:
                raise ValueError("Source resource properties changed")
            if "itemCnt" in dest.attrib and dest.get("itemCnt") != str(len(dest)):
                raise ValueError("Resource count mismatch")
            if container.tag == HH + "fontfaces":
                if len(container) != len(dest):
                    raise ValueError("Font language tables changed")
                for face in container:
                    matches = [
                        item for item in dest if item.get("lang") == face.get("lang")
                    ]
                    if len(matches) != 1 or node_hash(list(face)) != node_hash(
                        list(matches[0])[: len(face)]
                    ):
                        raise ValueError("Source font definitions changed")
                    target = matches[0]
                    if {k: v for k, v in face.attrib.items() if k != "fontCnt"} != {
                        k: v for k, v in target.attrib.items() if k != "fontCnt"
                    } or target.get("fontCnt") != str(len(target)):
                        raise ValueError("Source font language properties changed")
            elif node_hash(list(container)) != node_hash(list(dest)[: len(container)]):
                raise ValueError("Source resource definitions changed")
    old_hpf, old_items = _manifest(source)
    new_hpf, new_items = _manifest(candidate)
    if (
        old_hpf.attrib != new_hpf.attrib
        or old_items.attrib != new_items.attrib
        or [node.tag for node in old_hpf] != [node.tag for node in new_hpf]
    ):
        raise ValueError("Package structure changed")
    if node_hash(list(old_items)) != node_hash(list(new_items)[: len(old_items)]):
        raise ValueError("Original package items changed")
    for node in old_hpf:
        if node.tag != OPF + "manifest":
            updated = new_hpf.find(node.tag)
            if updated is None or ET.tostring(node) != ET.tostring(updated):
                raise ValueError("Package metadata/spine changed")
    report = {
        "status": "checks_passed_needs_human_visual_review",
        "outside_region_xml_unchanged": True,
        "old_binary_members_byte_unchanged": True,
        "old_resource_definitions_unchanged": True,
        "native_qualification": False,
        "native_runs": 0,
        "source_sha256": review.digest(source_raw),
    }
    if caption_policy is not None:
        captions.enabled({"caption_policy": caption_policy})
        imported = list(after)[start : start + inserted_count]
        offsets = captions.bases(before, bounds, old_head, imported)
        model = captions.inspect(imported, offsets)
        old_ids = {
            node.get(key)
            for node in before.iter()
            for key in ("id", "instanceID", "instid")
            if node.get(key, "").isdecimal()
        }
        if old_ids.intersection(model["targets"]) or old_ids.intersection(
            reference["identity"] for reference in model["references"]
        ):
            raise ValueError("Imported caption/reference identity collides with source")
        report.update(captions.summary(model, offsets))
    return report


def _input(workspace, job, which):
    relative = job[which + "_snapshot"]
    path = workspace / relative
    if (
        Path(relative).is_absolute()
        or ".." in Path(relative).parts
        or path.resolve().parent != (workspace / "inputs").resolve()
        or path.is_symlink()
    ):
        raise ValueError("Invalid immutable snapshot path")
    raw = path.read_bytes()
    if review.digest(raw) != job[which + "_sha256"]:
        raise ValueError("Immutable input changed")
    original = Path(job[which + "_path"])
    if original.is_symlink() or review.file_hash(original) != job[which + "_sha256"]:
        raise ValueError("Original input changed; prepare a NEW review workspace")
    return raw


def prepare(profile_path, workspace):
    profile = review.read_json(profile_path)
    if (
        profile.get("contract") != CONTRACT
        or not isinstance(profile.get("jobs"), list)
        or not 1 <= len(profile["jobs"]) <= 2
    ):
        raise ValueError(
            "An explicit template review profile with one or two jobs is required"
        )
    workspace = Path(workspace).resolve()
    if workspace.exists():
        raise FileExistsError(
            "Review workspace must be NEW; never reuse approval or failure evidence"
        )
    jobs = []
    snapshots = {}
    for index, spec in enumerate(profile["jobs"]):
        job = {
            "label": str(spec["label"]),
            "target_range": spec["target_range"],
            "payload_ranges": spec["payload_ranges"],
        }
        if "caption_policy" in spec:
            captions.enabled(spec)
            job["caption_policy"] = spec["caption_policy"]
        for which in ("source", "payload"):
            path = Path(spec[which]).resolve(strict=True)
            raw = path.read_bytes()
            if review.digest(raw) != spec[which + "_sha256"]:
                raise ValueError("Profile input hash is stale")
            relative = "inputs/" + str(index) + "-" + which + ".hwpx"
            job.update(
                {
                    which + "_path": str(path),
                    which + "_snapshot": relative,
                    which + "_sha256": review.digest(raw),
                }
            )
            snapshots[relative] = raw
        source_root = review.xml_root(
            parts_from(snapshots[job["source_snapshot"]])[PART]
        )
        nodes = selected(source_root, [job["target_range"]])
        start, end = job["target_range"]
        if start == 0 or end == len(source_root):
            raise ValueError("Both exact boundary paragraphs are required")
        job["before_anchor"] = texts([source_root[start - 1]])
        job["after_anchor"] = texts([source_root[end]])
        job["selection_sha256"] = node_hash(nodes)
        _, report = compose(
            snapshots[job["source_snapshot"]], snapshots[job["payload_snapshot"]], job
        )
        job["preview"] = report
        jobs.append(job)
    plan = {
        "contract": CONTRACT,
        "policy": POLICY,
        "jobs": jobs,
        "confirmations": list(CONFIRMATIONS),
        "native_qualification": False,
    }
    workspace.mkdir(parents=True, exist_ok=False)
    (workspace / "inputs").mkdir()
    for relative, raw in snapshots.items():
        with (workspace / relative).open("xb") as stream:
            stream.write(raw)
    manifest = {
        "kind": CONTRACT,
        "workspace_id": secrets.token_hex(16),
        "baseline_hwpx_sha256": review.plan_hash(
            {"sources": [job["source_sha256"] for job in jobs]}
        ),
        "profile_sha256": review.file_hash(Path(profile_path)),
        "prepared_at": datetime.now(timezone.utc).isoformat(),
    }
    review.write_json(workspace / "template-manifest.json", manifest)
    review.write_json(workspace / "template-plan.json", plan)
    return {
        "workspace": str(workspace),
        "plan_sha256": review.plan_hash(plan),
        "jobs": len(jobs),
        "draft_permission": "not_granted",
        "artifact_acceptance": "not_generated",
        "candidate_files_written": 0,
    }


def state(workspace):
    workspace = Path(workspace)
    manifest = review.read_json(workspace / "template-manifest.json")
    plan = review.read_json(workspace / "template-plan.json")
    if (
        manifest.get("kind") != CONTRACT
        or plan.get("contract") != CONTRACT
        or plan.get("policy") != POLICY
        or plan.get("confirmations") != list(CONFIRMATIONS)
    ):
        raise ValueError("Invalid template review contract")
    if not isinstance(plan.get("jobs"), list) or not 1 <= len(plan["jobs"]) <= 2:
        raise ValueError("Invalid template job list")
    if manifest["baseline_hwpx_sha256"] != review.plan_hash(
        {"sources": [job["source_sha256"] for job in plan["jobs"]]}
    ):
        raise ValueError("Template input set changed")
    for job in plan["jobs"]:
        source = _input(workspace, job, "source")
        payload = _input(workspace, job, "payload")
        root = review.xml_root(parts_from(source)[PART])
        start, end = job["target_range"]
        if (
            start == 0
            or end >= len(root)
            or node_hash(selected(root, [job["target_range"]]))
            != job["selection_sha256"]
            or texts([root[start - 1]]) != job["before_anchor"]
            or texts([root[end]]) != job["after_anchor"]
        ):
            raise ValueError("Exact region or boundary anchors changed")
        _, report = compose(source, payload, job)
        if review.plan_hash(report) != review.plan_hash(job["preview"]):
            raise ValueError("Proposal content or import policy changed")
    permission_revision = review.file_revision(workspace / "draft-permission.json")
    revision = review.plan_hash({"manifest": manifest, "plan": plan})
    return {
        "manifest": manifest,
        "plan": plan,
        "plan_sha256": review.plan_hash(plan),
        "revision": revision,
        "permission_revision": permission_revision,
        "locked": (workspace / review.APPLY_LOCK).exists(),
    }


def _review_pictures(raw, bounds):
    """Inspect selected picture occurrences only; never render/transform their PNGs."""
    parts = parts_from(raw)
    _, manifest = _manifest(parts)
    items = {item.get("id"): item for item in manifest}
    nodes = selected(review.xml_root(parts[PART]), [bounds])
    result = []
    for pic in (pic for node in nodes for pic in node.iter(HP + "pic")):
        images = list(pic.iter(HC + "img"))
        if len(images) != 1:
            raise ValueError("Picture preview needs one unambiguous embedded image")
        image = images[0]
        item = items.get(image.get("binaryItemIDRef"))
        if item is None:
            raise ValueError("Picture preview image reference is missing")
        href = item.get("href", "")
        if not href.startswith("BinData/") or href not in parts:
            raise ValueError("Picture preview requires an embedded package image")
        blob = parts[href]
        available = (
            item.get("media-type") == "image/png"
            and blob.startswith(b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR")
            and 33 <= len(blob) <= 8 * 1024 * 1024
            and 0
            < int.from_bytes(blob[16:20]) * int.from_bytes(blob[20:24])
            <= 16000000
        )
        shape = deepcopy(pic)
        caption = shape.find(HP + "caption")
        label = " / ".join(texts([caption])) if caption is not None else ""
        if caption is not None:
            shape.remove(caption)
        for key in ("id", "instanceID", "instid", "zOrder"):
            shape.attrib.pop(key, None)
        for image_node in shape.iter(HC + "img"):
            image_node.attrib.pop("binaryItemIDRef", None)
        geometry = ET.canonicalize(ET.tostring(shape, encoding="unicode"))
        size = pic.find(HP + "sz")
        result.append(
            {
                "index": len(result),
                "caption": label or "범위 내 그림 " + str(len(result) + 1),
                "sha256": review.digest(blob),
                "properties_sha256": review.digest(geometry.encode("utf-8")),
                "width": size.get("width") if size is not None else None,
                "height": size.get("height") if size is not None else None,
                "available": available,
                "reason": ""
                if available
                else "내장 PNG 형식·치수 또는 미리보기 한도 밖입니다. 변환하거나 대체하지 않습니다.",
                "_blob": blob if available else None,
            }
        )
    return result


def image_review(workspace, expected_plan, job_index):
    """Transient image evidence; saved plans/reports/output bytes stay unchanged."""
    current = state(workspace)
    if current["plan_sha256"] != expected_plan:
        raise ValueError("Stale image review plan")
    jobs = current["plan"]["jobs"]
    if type(job_index) is not int or not 0 <= job_index < len(jobs):
        raise LookupError("No image review job")
    job = jobs[job_index]
    source = _input(Path(workspace), job, "source")
    candidate, report = compose(source, _input(Path(workspace), job, "payload"), job)
    if review.plan_hash(report) != review.plan_hash(job["preview"]):
        raise ValueError("Image review content changed")
    start = job["target_range"][0]
    before = _review_pictures(source, job["target_range"])
    after = _review_pictures(candidate, [start, start + report["inserted_paragraphs"]])

    def key(item):
        return item["sha256"], item["properties_sha256"]

    old_counts, new_counts = Counter(map(key, before)), Counter(map(key, after))
    changed = {}
    for side, entries, remaining in (
        ("before", before, new_counts.copy()),
        ("after", after, old_counts.copy()),
    ):
        changed[side] = []
        for item in entries:
            identity = key(item)
            if remaining[identity]:
                remaining[identity] -= 1
            else:
                changed[side].append(item)
    return {
        "plan_sha256": expected_plan,
        "matched_count": sum((old_counts & new_counts).values()),
        "before": changed["before"],
        "after": changed["after"],
    }


def artifact_bytes(workspace, current, index, item):
    """Read only an exact, pinned result; shared by the UI and offline harness."""
    workspace = Path(workspace).resolve(strict=True)
    if type(index) is not int or not 0 <= index < len(current["plan"]["jobs"]):
        raise ValueError("Invalid artifact index")
    relative = item["file"]
    if not re.fullmatch(
        rf"attempts/\d{{8}}T\d{{6}}-[a-f0-9]{{8}}/document-{index + 1}-template-review\.hwpx",
        relative,
    ):
        raise ValueError("Invalid artifact path")
    path = workspace / relative
    if path.is_symlink() or path.resolve().parent.parent != workspace / "attempts":
        raise ValueError("Artifact escaped the owned attempt")
    blob = path.read_bytes()
    expected = current["plan"]["jobs"][index]["preview"]["candidate_sha256"]
    if review.digest(blob) != item["sha256"] or review.digest(blob) != expected:
        raise ValueError("Review artifact changed")
    return blob


def result_state(workspace, current):
    """Observe all outputs without approving, creating, retrying or clearing locks."""
    workspace = Path(workspace).resolve(strict=True)
    path = workspace / "template-result.json"
    result = {
        "artifacts": [],
        "result_error": "",
        "accepted_indices": [],
        "execution_recorded": current["locked"] or path.exists(),
    }
    if path.exists():
        try:
            if path.is_symlink():
                raise ValueError("Result metadata must not be a symlink")
            record = review.read_json(path)
            if (
                not isinstance(record, dict)
                or record.get("contract") != CONTRACT
                or record.get("plan_sha256") != current["plan_sha256"]
                or record.get("status") != "checks_passed_needs_human_visual_review"
                or not isinstance(record.get("artifacts"), list)
                or len(record["artifacts"]) != len(current["plan"]["jobs"])
            ):
                raise ValueError(
                    "완료된 검토본을 확인할 수 없습니다. 실행 기록을 보존하고 새 작업에서 확인하세요."
                )
            authority = record.get("authority")
            if not isinstance(authority, dict) or set(authority) != {"kind", "sha256"}:
                raise ValueError("완료 결과에 정확한 초안 권한 결합이 없습니다.")
            if authority["kind"] == "template-ui":
                candidate_permission(workspace, current)
                if review.file_hash(workspace / "draft-permission.json") != authority["sha256"]:
                    raise ValueError("완료 후 UI 초안 권한 바이트가 변경되었습니다.")
            elif authority["kind"] != "bound-report-task":
                raise ValueError("알 수 없는 초안 생성 권한입니다.")
            artifacts, accepted_indices = [], []
            for index, item in enumerate(record["artifacts"]):
                artifact_bytes(workspace, current, index, item)
                acceptance_path, expected_acceptance = _artifact_acceptance_data(workspace, current, index, item)
                accepted = False
                if acceptance_path.exists() or acceptance_path.is_symlink():
                    if acceptance_path.is_symlink() or review.read_json(acceptance_path) != expected_acceptance:
                        raise ValueError("현재 수정본과 다른 정확한 파일 선택 기록입니다.")
                    accepted = True
                    accepted_indices.append(index)
                artifacts.append({**item, "accepted": accepted})
            result["artifacts"] = artifacts
            result["accepted_indices"] = accepted_indices
        except (OSError, ValueError, KeyError, TypeError) as exc:
            result["result_error"] = str(exc)
    return result


def _assert_review_current(workspace, current, authorize=None):
    if (
        review.plan_hash(review.read_json(workspace / "template-manifest.json"))
        != review.plan_hash(current["manifest"])
        or review.plan_hash(review.read_json(workspace / "template-plan.json"))
        != current["plan_sha256"]
    ):
        raise ValueError("Review inputs changed during creation")
    if authorize is None:
        candidate_permission(workspace, current)
    else:
        authorize()


def apply(workspace, expected_revision, *, authorize=None, authority=None):
    workspace = Path(workspace)
    current = state(workspace)
    if expected_revision != current["revision"]:
        raise ValueError("Stale review revision")
    if authorize is None:
        candidate_permission(workspace, current)
        permission_sha256 = review.file_hash(workspace / "draft-permission.json")
        authority = {"kind": "template-ui", "sha256": permission_sha256}

        def authorize():
            candidate_permission(workspace, current)
            if review.file_hash(workspace / "draft-permission.json") != permission_sha256:
                raise ValueError("생성 중 UI 초안 권한 바이트가 변경되었습니다.")
    elif authority is None:
        raise ValueError("외부 UI 권한의 정확한 결합이 필요합니다.")
    review.require_apply_unlocked(workspace)
    if (workspace / "template-result.json").exists() or (workspace / "template-result.json").is_symlink():
        raise ValueError("실행 결과/실패 기록이 이미 있습니다. 다시 생성하지 않습니다.")
    _assert_review_current(workspace, current, authorize)
    run_id = (
        datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S")
        + "-"
        + secrets.token_hex(4)
    )
    lease = review.acquire_apply_lock(
        workspace, current["manifest"], current["plan"], run_id
    )
    attempt = workspace / "attempts" / run_id
    attempt.mkdir(parents=True, exist_ok=False)
    result = {
        "contract": CONTRACT,
        "run_id": run_id,
        "plan_sha256": current["plan_sha256"],
        "authority": authority,
        "status": "failed",
        "native_runs": 0,
        "native_qualification": False,
        "artifacts": [],
    }
    try:
        for index, job in enumerate(current["plan"]["jobs"]):
            review.assert_apply_lock_owned(workspace, lease)
            source = _input(workspace, job, "source")
            payload = _input(workspace, job, "payload")
            raw, report = compose(source, payload, job)
            if report != job["preview"]:
                raise ValueError("Candidate differs from the exact pinned proposal")
            _input(workspace, job, "source")
            _input(workspace, job, "payload")
            _assert_review_current(workspace, current, authorize)
            path = attempt / ("document-" + str(index + 1) + "-template-review.hwpx")
            with path.open("xb") as stream:
                stream.write(raw)
            validate(
                source,
                path.read_bytes(),
                job["target_range"],
                report["inserted_paragraphs"],
                [name for name in parts_from(raw) if name not in parts_from(source)],
                caption_policy=job.get("caption_policy"),
            )
            result["artifacts"].append(
                {
                    "file": path.relative_to(workspace).as_posix(),
                    "sha256": review.file_hash(path),
                    "label": job["label"],
                }
            )
        review.assert_apply_lock_owned(workspace, lease)
        _assert_review_current(workspace, current, authorize)
        for job in current["plan"]["jobs"]:
            _input(workspace, job, "source")
            _input(workspace, job, "payload")
        result["status"] = "checks_passed_needs_human_visual_review"
        return result
    except Exception as error:
        result["error"] = str(error)
        raise
    finally:
        _write_new_json(attempt / "verification.json", result)
        _write_new_json(workspace / "template-result.json", result)


def _write_new_json(path, data):
    raw = json.dumps(data, ensure_ascii=False, indent=2, allow_nan=False).encode("utf-8")
    with path.open("xb") as stream:
        stream.write(raw)


def draft_permission_record(workspace, expected_revision):
    """Pure UI record preparation for an advanced rich prepared-HWPX task."""
    import hwp_report_runtime as runtime

    workspace = Path(workspace).absolute()
    current = state(workspace)
    if current["revision"] != expected_revision or current["locked"]:
        raise ValueError("검토 범위가 바뀌었거나 실행 기록이 이미 있습니다.")
    return workspace / "draft-permission.json", {
        "contract": "hwp-template-draft-permission-v1",
        "granted_via": "local-review-ui",
        "authority": "create_unapproved_candidate_only",
        "workspace": str(workspace),
        "runtime_sha256": review.plan_hash(runtime.identity()),
        "manifest_sha256": review.plan_hash(current["manifest"]),
        "plan_sha256": current["plan_sha256"],
        "scope": {key: True for key in CONFIRMATIONS},
    }


def candidate_permission(workspace, current):
    import hwp_report_runtime as runtime

    path = Path(workspace) / "draft-permission.json"
    if path.is_symlink():
        raise ValueError("UI 초안 권한은 실제 파일이어야 합니다.")
    record = review.read_json(path)
    expected = {
        "contract": "hwp-template-draft-permission-v1",
        "granted_via": "local-review-ui",
        "authority": "create_unapproved_candidate_only",
        "workspace": str(Path(workspace).absolute()),
        "runtime_sha256": review.plan_hash(runtime.identity()),
        "manifest_sha256": review.plan_hash(current["manifest"]),
        "plan_sha256": current["plan_sha256"],
        "scope": {key: True for key in CONFIRMATIONS},
    }
    if record != expected:
        raise ValueError("현재 범위/실행 환경에 결합된 UI 초안 권한이 없습니다.")
    return record


def apply_candidate(workspace, expected_revision, expected_permission_sha256):
    workspace = Path(workspace)

    def authorize():
        current = state(workspace)
        candidate_permission(workspace, current)
        if review.file_hash(workspace / "draft-permission.json") != expected_permission_sha256:
            raise ValueError("UI 초안 권한이 생성 중 변경되었습니다.")

    authorize()
    current = state(workspace)
    if current["revision"] != expected_revision:
        raise ValueError("Stale review revision")
    result = result_state(workspace, current)
    if result["artifacts"] and not result["result_error"]:
        return review.read_json(workspace / "template-result.json")
    return apply(workspace, expected_revision, authorize=authorize,
                 authority={"kind": "template-ui", "sha256": expected_permission_sha256})


def artifact_acceptance_record(workspace, index, expected_sha256):
    """Return an exact existing artifact acceptance record; never write/create."""
    workspace = Path(workspace)
    current = state(workspace)
    result = result_state(workspace, current)
    if result["result_error"] or type(index) is not int or not 0 <= index < len(result["artifacts"]):
        raise ValueError("정확한 생성 결과를 확인할 수 없습니다.")
    item = result["artifacts"][index]
    artifact_bytes(workspace, current, index, item)
    if expected_sha256 != item["sha256"]:
        raise ValueError("확인한 파일 바이트가 현재 수정본과 다릅니다.")
    return _artifact_acceptance_data(workspace, current, index, item)


def _artifact_acceptance_data(workspace, current, index, item):
    return workspace / ("artifact-" + str(index + 1) + "-acceptance.json"), {
        "contract": "hwp-template-artifact-acceptance-v1",
        "accepted_via": "local-review-ui", "authority": "use_exact_artifact_only",
        "plan_sha256": current["plan_sha256"], "file": item["file"],
        "sha256": item["sha256"],
    }
