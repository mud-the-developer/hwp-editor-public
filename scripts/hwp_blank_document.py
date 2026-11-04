"""Build a small unsigned OWPML package, not a report/protocol fixture.

The element/attribute vocabulary follows HWPX 2011, as documented by the
rhwp 0.8.7 serializer and its empty-document examples. This is an independent
serializer: no upstream template, document, font or preview binary is copied.
Only font family names are recorded; installed-font rendering remains local.
"""

from io import BytesIO
import xml.etree.ElementTree as ET
import zipfile

from hwp_resources import APP_VERSION


_BASE = "http://www.hancom.co.kr/hwpml/2011/"
_LANGUAGES = ("hangul", "latin", "hanja", "japanese", "other", "symbol", "user")


def _node(parent, tag, **attrs):
    return ET.SubElement(parent, tag, {key: str(value) for key, value in attrs.items()})


def _xml(root):
    return ET.tostring(root, encoding="utf-8", xml_declaration=True)


def _header():
    root = ET.Element("hh:head", {
        "xmlns:hh": _BASE + "head", "xmlns:hc": _BASE + "core",
        "version": "1.2", "secCnt": "1",
    })
    _node(root, "hh:beginNum", page=1, footnote=1, endnote=1, pic=1, tbl=1, equation=1)
    refs = _node(root, "hh:refList")
    faces = _node(refs, "hh:fontfaces", itemCnt=7)
    for language in _LANGUAGES:
        face = _node(faces, "hh:fontface", lang=language.upper(), fontCnt=1)
        # A Windows font family reference is not an embedded font binary.
        font = _node(face, "hh:font", id=0, face="맑은 고딕", type="TTF", isEmbedded=0)
        _node(font, "hh:typeInfo", familyType="FCAT_GOTHIC", weight=5, proportion=0,
              contrast=0, strokeVariation=0, armStyle=0, letterform=0, midline=0, xHeight=0)
    fills = _node(refs, "hh:borderFills", itemCnt=1)
    fill = _node(fills, "hh:borderFill", id=1, threeD=0, shadow=0,
                 centerLine="NONE", breakCellSeparateLine=0)
    for tag in ("slash", "backSlash"):
        _node(fill, "hh:" + tag, type="NONE", Crooked=0, isCounter=0)
    for tag in ("leftBorder", "rightBorder", "topBorder", "bottomBorder", "diagonal"):
        _node(fill, "hh:" + tag, type="NONE", width="0.1 mm", color="#000000")
    chars = _node(refs, "hh:charProperties", itemCnt=1)
    char = _node(chars, "hh:charPr", id=0, height=1000, textColor="#000000",
                 shadeColor="none", useFontSpace=0, useKerning=0, symMark="NONE",
                 borderFillIDRef=1)
    for tag, value in (("fontRef", 0), ("ratio", 100), ("spacing", 0),
                       ("relSz", 100), ("offset", 0)):
        _node(char, "hh:" + tag, **dict.fromkeys(_LANGUAGES, value))
    tabs = _node(refs, "hh:tabProperties", itemCnt=1)
    _node(tabs, "hh:tabPr", id=0, autoTabLeft=0, autoTabRight=0)
    numberings = _node(refs, "hh:numberings", itemCnt=1)
    numbering = _node(numberings, "hh:numbering", id=1, start=1)
    for level in range(1, 8):
        head = _node(numbering, "hh:paraHead", start=1, level=level, align="LEFT",
                     useInstWidth=1, autoIndent=1, widthAdjust=0, textOffsetType="PERCENT",
                     textOffset=50, numFormat="DIGIT", charPrIDRef=0, checkable=0)
        head.text = "^" + str(level) + "."
    paras = _node(refs, "hh:paraProperties", itemCnt=1)
    para = _node(paras, "hh:paraPr", id=0, tabPrIDRef=0, condense=0,
                 fontLineHeight=0, snapToGrid=1, suppressLineNumbers=0, checked=0)
    _node(para, "hh:align", horizontal="LEFT", vertical="BASELINE")
    _node(para, "hh:heading", type="NONE", idRef=0, level=0)
    _node(para, "hh:breakSetting", breakLatinWord="KEEP_WORD", breakNonLatinWord="KEEP_WORD",
          widowOrphan=0, keepWithNext=0, keepLines=0, pageBreakBefore=0, lineWrap="BREAK")
    _node(para, "hh:autoSpacing", eAsianEng=0, eAsianNum=0)
    margin = _node(para, "hh:margin")
    for tag in ("intent", "left", "right", "prev", "next"):
        _node(margin, "hc:" + tag, value=0, unit="HWPUNIT")
    _node(para, "hh:lineSpacing", type="PERCENT", value=160, unit="HWPUNIT")
    _node(para, "hh:border", borderFillIDRef=1, offsetLeft=0, offsetRight=0,
          offsetTop=0, offsetBottom=0, connect=0, ignoreMargin=0)
    styles = _node(refs, "hh:styles", itemCnt=1)
    _node(styles, "hh:style", id=0, type="PARA", name="바탕글", engName="Normal",
          paraPrIDRef=0, charPrIDRef=0, nextStyleIDRef=0, langID=1042, lockForm=0)
    compatibility = _node(root, "hh:compatibleDocument", targetProgram="HWP201X")
    _node(compatibility, "hh:layoutCompatibility")
    option = _node(root, "hh:docOption")
    _node(option, "hh:linkinfo", path="", pageInherit=0, footnoteInherit=0)
    _node(root, "hh:trackchageConfig", flags=56)
    return _xml(root)


def _section_properties(run):
    section = _node(run, "hp:secPr", id="", textDirection="HORIZONTAL", spaceColumns=1134,
                    tabStop=8000, tabStopVal=4000, tabStopUnit="HWPUNIT",
                    outlineShapeIDRef=1, memoShapeIDRef=0, textVerticalWidthHead=0, masterPageCnt=0)
    _node(section, "hp:grid", lineGrid=0, charGrid=0, wonggojiFormat=0)
    _node(section, "hp:startNum", pageStartsOn="BOTH", page=0, pic=0, tbl=0, equation=0)
    _node(section, "hp:visibility", hideFirstHeader=0, hideFirstFooter=0, hideFirstMasterPage=0,
          border="SHOW_ALL", fill="SHOW_ALL", hideFirstPageNum=0, hideFirstEmptyLine=0, showLineNumber=0)
    _node(section, "hp:lineNumberShape", restartType=0, countBy=0, distance=0, startNumber=0)
    page = _node(section, "hp:pagePr", landscape="WIDELY", width=59528, height=84186, gutterType="LEFT_ONLY")
    _node(page, "hp:margin", header=4252, footer=4252, gutter=0, left=8504,
          right=8504, top=5668, bottom=4252)
    for tag, place in (("footNotePr", "EACH_COLUMN"), ("endNotePr", "END_OF_DOCUMENT")):
        note = _node(section, "hp:" + tag)
        _node(note, "hp:autoNumFormat", type="DIGIT", userChar="", prefixChar="", suffixChar=")", supscript=0)
        _node(note, "hp:noteLine", length=-1, type="SOLID", width="0.12 mm", color="#000000")
        _node(note, "hp:noteSpacing", betweenNotes=0, belowLine=567, aboveLine=850)
        _node(note, "hp:numbering", type="CONTINUOUS", newNum=1)
        _node(note, "hp:placement", place=place, beneathText=0)
    for kind in ("BOTH", "EVEN", "ODD"):
        border = _node(section, "hp:pageBorderFill", type=kind, borderFillIDRef=1,
                       textBorder="PAPER", headerInside=0, footerInside=0, fillArea="PAPER")
        _node(border, "hp:offset", left=1417, right=1417, top=1417, bottom=1417)
    control = _node(run, "hp:ctrl")
    _node(control, "hp:colPr", id="", type="NEWSPAPER", layout="LEFT", colCount=1, sameSz=1, sameGap=0)


def _section():
    root = ET.Element("hs:sec", {"xmlns:hs": _BASE + "section", "xmlns:hp": _BASE + "paragraph"})
    # The section control stays in the protected first paragraph. A genuinely
    # empty middle paragraph is editable; the empty last paragraph stays locked.
    for index in range(3):
        para = _node(root, "hp:p", id=index, paraPrIDRef=0, styleIDRef=0,
                     pageBreak=0, columnBreak=0, merged=0)
        if index == 0:
            _section_properties(_node(para, "hp:run", charPrIDRef=0))
        _node(_node(para, "hp:run", charPrIDRef=0), "hp:t")
        lines = _node(para, "hp:linesegarray")
        _node(lines, "hp:lineseg", textpos=0, vertpos=index * 1600, vertsize=1000,
              textheight=1000, baseline=850, spacing=600, horzpos=0, horzsize=42520, flags=393216)
    return _xml(root)


def _content():
    root = ET.Element("opf:package", {"xmlns:opf": "http://www.idpf.org/2007/opf/",
                                      "version": "", "unique-identifier": "", "id": ""})
    metadata = _node(root, "opf:metadata")
    _node(metadata, "opf:title")
    _node(metadata, "opf:language").text = "ko"
    manifest = _node(root, "opf:manifest")
    for identifier, path in (("header", "Contents/header.xml"),
                             ("section0", "Contents/section0.xml"), ("settings", "settings.xml")):
        _node(manifest, "opf:item", id=identifier, href=path, **{"media-type": "application/xml"})
    spine = _node(root, "opf:spine")
    for identifier in ("header", "section0"):
        _node(spine, "opf:itemref", idref=identifier)
    return _xml(root)


def blank_document():
    """Return a complete blank .hwpx; callers exclusively own destination I/O."""
    container = ET.Element("ocf:container", {"xmlns:ocf": "urn:oasis:names:tc:opendocument:xmlns:container"})
    roots = _node(container, "ocf:rootfiles")
    for path, media in (("Contents/content.hpf", "application/hwpml-package+xml"),
                        ("Preview/PrvText.txt", "text/plain"), ("META-INF/container.rdf", "application/rdf+xml")):
        _node(roots, "ocf:rootfile", **{"full-path": path, "media-type": media})
    rdf = ET.Element("rdf:RDF", {"xmlns:rdf": "http://www.w3.org/1999/02/22-rdf-syntax-ns#",
                                 "xmlns:pkg": "http://www.hancom.co.kr/hwpml/2016/meta/pkg#"})
    for path, kind in (("", "Document"), ("Contents/header.xml", "HeaderFile"),
                       ("Contents/section0.xml", "SectionFile")):
        desc = _node(rdf, "rdf:Description", **{"rdf:about": path})
        _node(desc, "rdf:type", **{"rdf:resource": "http://www.hancom.co.kr/hwpml/2016/meta/pkg#" + kind})
        if path:
            _node(_node(rdf, "rdf:Description", **{"rdf:about": ""}), "pkg:hasPart", **{"rdf:resource": path})
    version = ET.Element("hv:HCFVersion", {
        "xmlns:hv": _BASE + "version", "tagetApplication": "WORDPROCESSOR", "major": "5",
        "minor": "1", "micro": "0", "buildNumber": "1", "os": "1", "xmlVersion": "1.5",
        "application": "HWPReview", "appVersion": APP_VERSION,
    })
    settings = ET.Element("ha:HWPApplicationSetting", {"xmlns:ha": _BASE + "app"})
    _node(settings, "ha:CaretPosition", listIDRef=0, paraIDRef=1, pos=0)
    manifest = ET.Element("odf:manifest", {"xmlns:odf": "urn:oasis:names:tc:opendocument:xmlns:manifest:1.0"})
    parts = {
        "version.xml": _xml(version), "META-INF/container.xml": _xml(container),
        "META-INF/container.rdf": _xml(rdf), "META-INF/manifest.xml": _xml(manifest),
        "Contents/content.hpf": _content(), "Contents/header.xml": _header(),
        "Contents/section0.xml": _section(), "settings.xml": _xml(settings),
        "Preview/PrvText.txt": b"\r\n\r\n\r\n",
    }
    output = BytesIO()
    with zipfile.ZipFile(output, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        # OCF requires the first entry to be the uncompressed media type.
        archive.writestr("mimetype", b"application/hwp+zip", compress_type=zipfile.ZIP_STORED)
        for path, blob in parts.items():
            archive.writestr(path, blob)
    return output.getvalue()
