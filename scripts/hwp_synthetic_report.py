"""Fresh unsigned HWPX protocol fixtures for explicit report smoke only.

Not a Hancom layout/native qualification or a real research report. Ordinary app
startup never calls this module or creates these files.
"""

import base64
import hashlib
from io import BytesIO
from pathlib import Path
import xml.etree.ElementTree as ET
import zipfile

HP = "{http://www.hancom.co.kr/hwpml/2011/paragraph}"
HS = "{http://www.hancom.co.kr/hwpml/2011/section}"
HH = "{http://www.hancom.co.kr/hwpml/2011/head}"
HC = "{http://www.hancom.co.kr/hwpml/2011/core}"
OPF = "{http://www.idpf.org/2007/opf/}"
PART = "Contents/section0.xml"
PNG = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAMAAAACCAIAAAASFvFNAAAACXBIWXMAAA7EAAAOxAGVKw4b"
    "AAAAEElEQVR4nGPgK5kAQQxwFgA9QAZtzKsBwQAAAABJRU5ErkJggg=="
)


def _paragraph(text, identifier):
    paragraph = ET.Element(
        HP + "p", {"id": str(identifier), "paraPrIDRef": "0", "styleIDRef": "0"}
    )
    run = ET.SubElement(paragraph, HP + "run", {"charPrIDRef": "0"})
    ET.SubElement(run, HP + "t").text = text
    return paragraph


def _package(payload):
    header = ET.Element(HH + "head", {"version": "1.4", "secCnt": "1"})
    ET.SubElement(header, HH + "beginNum", {"pic": "1"})
    refs = ET.SubElement(header, HH + "refList")
    languages = ("HANGUL", "LATIN", "HANJA", "JAPANESE", "OTHER", "SYMBOL", "USER")
    faces = ET.SubElement(refs, HH + "fontfaces", {"itemCnt": str(len(languages))})
    for language in languages:
        face = ET.SubElement(faces, HH + "fontface", {"lang": language, "fontCnt": "1"})
        ET.SubElement(face, HH + "font", {"id": "0", "face": "SYNTHETIC Report Font"})
    borders = ET.SubElement(refs, HH + "borderFills", {"itemCnt": "1"})
    ET.SubElement(borders, HH + "borderFill", {"id": "1"})
    chars = ET.SubElement(refs, HH + "charProperties", {"itemCnt": "1"})
    char = ET.SubElement(chars, HH + "charPr", {"id": "0", "borderFillIDRef": "0"})
    ET.SubElement(char, HH + "fontRef", {language.lower(): "0" for language in languages})
    tabs = ET.SubElement(refs, HH + "tabProperties", {"itemCnt": "1"})
    ET.SubElement(tabs, HH + "tabPr", {"id": "0"})
    paragraphs = ET.SubElement(refs, HH + "paraProperties", {"itemCnt": "1"})
    paragraph = ET.SubElement(paragraphs, HH + "paraPr", {"id": "0", "tabPrIDRef": "0"})
    ET.SubElement(paragraph, HH + "heading", {"type": "NONE", "idRef": "0"})
    styles = ET.SubElement(refs, HH + "styles", {"itemCnt": "1"})
    ET.SubElement(
        styles,
        HH + "style",
        {"id": "0", "name": "SYNTHETIC", "paraPrIDRef": "0", "charPrIDRef": "0", "nextStyleIDRef": "0"},
    )
    section = ET.Element(HS + "sec")
    if payload:
        section.extend((_paragraph("SYNTHETIC REPORT BODY AFTER", 1), _paragraph("", 2)))
        picture = ET.SubElement(section[1][0], HP + "pic", {"id": "7", "instanceID": "8", "zOrder": "0"})
        ET.SubElement(picture, HP + "sz", {"width": "1000", "height": "1000"})
        ET.SubElement(picture, HC + "img", {"binaryItemIDRef": "image"})
        caption = ET.SubElement(picture, HP + "caption", {"side": "BOTTOM", "lastWidth": "1000"})
        content = ET.SubElement(caption, HP + "subList")
        label = _paragraph("SYNTHETIC CAPTION", 3)
        content.append(label)
        control = ET.SubElement(label[0], HP + "ctrl")
        number = ET.SubElement(control, HP + "autoNum", {"numType": "PICTURE", "num": "1"})
        ET.SubElement(number, HP + "autoNumFormat", {"type": "DIGIT"})
    else:
        first = _paragraph("SYNTHETIC SOURCE PREFIX", 10)
        settings = ET.SubElement(first[0], HP + "secPr")
        page = ET.SubElement(settings, HP + "pagePr", {"width": "60000", "height": "84000"})
        ET.SubElement(page, HP + "margin", {"left": "5000", "right": "5000", "gutter": "0"})
        section.extend((first, _paragraph("SYNTHETIC BEFORE BOUNDARY", 11), _paragraph("SYNTHETIC REPORT BODY BEFORE", 12), _paragraph("SYNTHETIC AFTER BOUNDARY", 13)))
    package = ET.Element(OPF + "package")
    ET.SubElement(package, OPF + "metadata").text = "SYNTHETIC LOCKED METADATA"
    manifest = ET.SubElement(package, OPF + "manifest")
    for identifier, href in (("header", "Contents/header.xml"), ("section0", PART)):
        ET.SubElement(manifest, OPF + "item", {"id": identifier, "href": href, "media-type": "application/xml"})
    spine = ET.SubElement(package, OPF + "spine")
    ET.SubElement(spine, OPF + "itemref", {"idref": "section0"})
    if payload:
        ET.SubElement(manifest, OPF + "item", {"id": "image", "href": "BinData/image.png", "media-type": "image/png"})
    parts = {
        "mimetype": b"application/hwp+zip",
        PART: ET.tostring(section, encoding="utf-8", xml_declaration=True),
        "Contents/header.xml": ET.tostring(header, encoding="utf-8", xml_declaration=True),
        "Contents/content.hpf": ET.tostring(package, encoding="utf-8", xml_declaration=True),
        "settings.xml": b"SYNTHETIC LOCKED SETTINGS",
        "Preview/PrvImage.png": PNG,
        "Preview/PrvText.txt": b"SYNTHETIC ONLY",
    }
    if payload:
        parts["BinData/image.png"] = PNG
    buffer = BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        for name, raw in parts.items():
            compression = zipfile.ZIP_STORED if name == "mimetype" else zipfile.ZIP_DEFLATED
            archive.writestr(name, raw, compress_type=compression)
    return buffer.getvalue()


def write_inputs(directory):
    """Create only NEW fixture files and return real profile/feedback data."""
    directory = Path(directory)
    directory.mkdir(parents=True, exist_ok=False)
    source, payload = directory / "unsigned-source.hwpx", directory / "unsigned-draft.hwpx"
    for path, raw in ((source, _package(False)), (payload, _package(True))):
        with path.open("xb") as output:
            output.write(raw)
    profile = {
        "contract": "template-review-draft-v2",
        "jobs": [{
            "label": "SYNTHETIC UNSIGNED REPORT REVIEW ONLY",
            "source": str(source),
            "source_sha256": hashlib.sha256(source.read_bytes()).hexdigest(),
            "payload": str(payload),
            "payload_sha256": hashlib.sha256(payload.read_bytes()).hexdigest(),
            "target_range": [2, 3],
            "payload_ranges": [[0, 2]],
        }],
    }
    feedback = {
        "schema": "hwp-report-feedback-v1",
        "goal": "Prepare a synthetic review draft; no real report, model, Native or submission.",
        "mode": "authoring",
        "decisions": ["Preserve source boundaries and original resources.", "Create only a new unapproved draft; artifact acceptance and submission are separate."],
    }
    return profile, feedback
