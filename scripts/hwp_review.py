"""Local, fail-closed HWP page review. See README.md for scope and limitations."""

from __future__ import annotations
import argparse
from collections import Counter, OrderedDict
from datetime import datetime, timezone
import hashlib
import html
from http.server import BaseHTTPRequestHandler, HTTPServer
import json
import math
import os
from pathlib import Path
import re
import secrets
import subprocess
import sys
import time
from typing import BinaryIO, cast
import unicodedata
import urllib.parse
import webbrowser
import xml.etree.ElementTree as ET
import zipfile
import zlib

import fitz
import olefile

# Source test hosts may import by filename. A frozen app must NEVER add a
# working-directory/TEMP scripts path: use only modules embedded by the builder.
if not getattr(sys, "frozen", False):
    scripts_dir = str(Path(__file__).resolve().parent)
    if scripts_dir not in sys.path:
        sys.path.insert(0, scripts_dir)
from hwp_resources import frozen, legal_notice, resource_root

ROOT = resource_root()
import hwp_native_plan
import hwp_fixed_layout
import hwp_fill_plan

HP = "{http://www.hancom.co.kr/hwpml/2011/paragraph}"
SECTIONS = re.compile(r"Contents/section\d+\.xml\Z")
TEXT = re.compile(
    rb"<(?P<tag>(?:[\w.-]+:)?t)\b[^<>]*?(?:\s*/>|>(?P<body>.*?)</(?P=tag)\s*>)", re.S
)
DPI = 144
APPLY_LOCK = ".apply-lock.json"
RUN_ID = re.compile(r"\d{8}T\d{6}-[a-f0-9]{8}\Z")
RESULT_POLICY = "apply-result-sha256-v1"
RESULT_FILE = "result-integrity.json"
RESULT_ARTIFACTS = (
    "plan.json",
    "verification.json",
    "candidate.hwpx",
    "candidate.hwp",
    "candidate-buffer.pdf",
    "candidate.pdf",
)
RESULT_OPTIONAL_ARTIFACTS = ("candidate-reopened.hwpx",)
RESULT_BAD_STATES = {"missing", "invalid", "mismatch"}
DOWNLOAD_LIMIT = 128 * 1024 * 1024
DOWNLOAD_FILES = {"hwp": "candidate.hwp", "pdf": "candidate.pdf"}


def digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def file_hash(path: Path) -> str:
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def normalized(text: str) -> str:
    return "".join(unicodedata.normalize("NFKC", text).split())


def parse_json(blob):
    """Reject ambiguous keys and nonfinite numbers, including 1e309 overflow."""

    def pairs(items):
        result = {}
        for key, value in items:
            if key in result:
                raise ValueError("Duplicate JSON key: " + key)
            result[key] = value
        return result

    def finite(value):
        result = float(value)
        if not math.isfinite(result):
            raise ValueError("JSON numbers must be finite")
        return result

    def constant(value):
        raise ValueError("Unsupported JSON constant: " + value)

    try:
        return json.loads(
            blob, object_pairs_hook=pairs, parse_float=finite, parse_constant=constant
        )
    except json.JSONDecodeError as error:
        raise ValueError("Malformed JSON: " + str(error)) from error


def read_json(path: Path):
    return parse_json(path.read_text(encoding="utf-8"))


def file_revision(path: Path) -> str:
    return file_hash(path) if path.exists() else "absent"


def write_json(path: Path, data) -> None:
    blob = json.dumps(data, ensure_ascii=False, indent=2, allow_nan=False)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + "." + secrets.token_hex(8) + ".tmp")
    owned = False
    try:
        with tmp.open("x", encoding="utf-8") as stream:
            owned = True
            stream.write(blob)
            stream.flush()
            os.fsync(stream.fileno())
        tmp.replace(path)
    finally:
        if owned:
            tmp.unlink(missing_ok=True)


def require_apply_unlocked(workspace: Path) -> None:
    lock = workspace / APPLY_LOCK
    if lock.exists() or lock.is_symlink():
        raise ValueError(
            "Apply is locked or interrupted. Owner must inspect the prior run and owned Hancom processes; no automatic unlock/retry"
        )


def acquire_apply_lock(
    workspace: Path, manifest: dict, plan: dict, run_id: str
) -> dict:
    """Cooperating applies in THIS workspace only; not a global COM/OS sandbox."""
    if not RUN_ID.fullmatch(run_id):
        raise ValueError("Invalid apply run id")
    lease = {
        "schema": 1,
        "workspace_id": manifest["workspace_id"],
        "baseline_hwpx_sha256": manifest["baseline_hwpx_sha256"],
        "plan_sha256": plan_hash(plan),
        "run_id": run_id,
        "pid": os.getpid(),
        "owner_nonce": secrets.token_hex(16),
    }
    with (workspace / APPLY_LOCK).open("x", encoding="utf-8") as stream:
        stream.write(json.dumps(lease, allow_nan=False))
        stream.flush()
        os.fsync(stream.fileno())
    return lease


def assert_apply_lock_owned(workspace: Path, lease: dict) -> None:
    path = workspace / APPLY_LOCK
    if (
        path.is_symlink()
        or not path.is_file()
        or path.stat().st_size > 4096
        or plan_hash(read_json(path)) != plan_hash(lease)
    ):
        raise ValueError("Apply lock ownership changed; stop for owner inspection")


def apply_state(workspace: Path, manifest: dict) -> dict:
    """Never infer that a stored PID/old timestamp makes a lock safe to clear."""
    path = workspace / APPLY_LOCK
    if not path.exists() and not path.is_symlink():
        return {"locked": False}
    state = {"locked": True, "record_valid": False, "run_id": None, "plan_sha256": None}
    try:
        if path.is_symlink() or not path.is_file() or path.stat().st_size > 4096:
            return state
        data = read_json(path)
        if (
            not isinstance(data, dict)
            or set(data)
            != {
                "schema",
                "workspace_id",
                "baseline_hwpx_sha256",
                "plan_sha256",
                "run_id",
                "pid",
                "owner_nonce",
            }
            or type(data["schema"]) is not int
            or data["schema"] != 1
            or data["workspace_id"] != manifest["workspace_id"]
            or data["baseline_hwpx_sha256"] != manifest["baseline_hwpx_sha256"]
            or type(data["pid"]) is not int
            or data["pid"] < 1
            or not isinstance(data["run_id"], str)
            or not RUN_ID.fullmatch(data["run_id"])
            or not isinstance(data["plan_sha256"], str)
            or not re.fullmatch(r"[a-f0-9]{64}", data["plan_sha256"])
            or not isinstance(data["owner_nonce"], str)
            or not re.fullmatch(r"[a-f0-9]{32}", data["owner_nonce"])
        ):
            return state
        state.update(
            record_valid=True, run_id=data["run_id"], plan_sha256=data["plan_sha256"]
        )
    except (OSError, ValueError, TypeError):
        pass
    return state


def plan_hash(plan: dict) -> str:
    return digest(
        json.dumps(
            plan,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        ).encode()
    )


def archive(path: Path | BinaryIO) -> dict[str, bytes]:
    with zipfile.ZipFile(path) as z:
        entries = z.infolist()
        if (
            len(entries) > 10000
            or sum(i.file_size for i in entries) > 512 * 1024 * 1024
        ):
            raise ValueError("HWPX archive exceeds the supported size limit")
        names = [i.filename for i in entries]
        if len(names) != len(set(names)):
            raise ValueError("Duplicate archive entries")
        for i in entries:
            if (
                "\\" in i.filename
                or i.filename.startswith("/")
                or ".." in i.filename.split("/")
            ):
                raise ValueError("Unsafe archive member")
            if i.filename.endswith(".xml") and i.file_size > 50 * 1024 * 1024:
                raise ValueError("XML part exceeds the supported size limit")
        return {i.filename: z.read(i) for i in entries}


def xml_root(raw: bytes) -> ET.Element:
    if b"<!DOCTYPE" in raw.upper() or b"<!ENTITY" in raw.upper():
        raise ValueError("DTD/entity declarations are not supported")
    return ET.fromstring(raw)


def text_nodes(part: str, raw: bytes) -> list[dict]:
    """Index by child-index path, NOT paragraph id (Hancom repeats paragraph ids)."""
    root = xml_root(raw)
    matches = list(TEXT.finditer(raw))
    elements = []

    def walk(node, path, parents):
        if node.tag == HP + "t":
            elements.append((node, path, parents))
        for i, child in enumerate(node):
            walk(child, path + [i], parents + [node])

    walk(root, [], [])
    if len(matches) != len(elements):
        raise ValueError(
            "Unsupported text XML syntax; refusing approximate node addressing"
        )
    result = []
    for (node, path, parents), match in zip(elements, matches):
        text = node.text or ""
        simple = len(node) == 0 and b"<![CDATA[" not in match.group(0)
        if simple and match.group("body") is not None:
            # Validate lexical and parsed node alignment before permitting byte splicing.
            decoded = ET.fromstring(b"<x>" + match.group("body") + b"</x>").text or ""
            if decoded != text:
                raise ValueError("Parsed and lexical text-node order disagree")
        para = next((p for p in reversed(parents) if p.tag == HP + "p"), None)
        run = next((p for p in reversed(parents) if p.tag == HP + "run"), None)
        cell = next((p for p in reversed(parents) if p.tag == HP + "tc"), None)
        cell_addr = cell.find(HP + "cellAddr") if cell is not None else None
        key = part + ":" + "/".join(map(str, path))
        result.append(
            {
                "anchor_id": "a-" + digest(key.encode())[:20],
                "part": part,
                "path": path,
                "old_text": text,
                "text_sha256": digest(text.encode()),
                "simple": simple,
                "paragraph_id": para.get("id") if para is not None else None,
                "para_style": para.get("paraPrIDRef") if para is not None else None,
                "char_style": run.get("charPrIDRef") if run is not None else None,
                "cell": dict(cell_addr.attrib) if cell_addr is not None else None,
                "start": match.start(),
                "end": match.end(),
                "raw": match.group(0),
            }
        )
    return result


def index_hwpx(path: Path) -> tuple[list[dict], dict]:
    contents = archive(path)
    anchors, counts = [], Counter()
    for name, raw in contents.items():
        if SECTIONS.fullmatch(name):
            anchors.extend(text_nodes(name, raw))
            counts.update(e.tag.split("}")[-1] for e in xml_root(raw).iter())
    if not anchors:
        raise ValueError("No supported HWPX section text nodes")
    return anchors, {k: counts[k] for k in ["tbl", "pic", "p", "t", "secPr"]}


def hwp_audit(path: Path) -> dict:
    with olefile.OleFileIO(str(path)) as o:
        header = o.openstream("FileHeader").read()
        if not header.startswith(b"HWP Document File"):
            raise ValueError("Not an HWP5 document")
        flags = int.from_bytes(header[36:40], "little")
        if flags & ((1 << 1) | (1 << 2) | (1 << 4)):
            raise ValueError(
                "Encrypted, distribution or digitally signed HWP is unsupported"
            )
        binaries = []
        for member in o.listdir():
            if member[0] != "BinData":
                continue
            raw = o.openstream(member).read()
            # BinData may be compressed independently of the document compression flag.
            try:
                raw = zlib.decompress(raw, -15)
            except zlib.error:
                pass
            binaries.append(digest(raw))
        return {
            "sha256": file_hash(path),
            "bytes": path.stat().st_size,
            "flags": flags,
            "embedded_binary_hashes": sorted(binaries),
            "embedded_binary_count": len(binaries),
        }


def native_export(
    source: Path,
    pdf: Path,
    *,
    hwpx=None,
    hwp=None,
    native_workspace=None,
    native_plan=None,
) -> dict:
    if frozen():
        raise RuntimeError(
            "EXE의 일반 Native 내보내기·실제 수정 경로는 차단됩니다. 기준 자료 준비는 화면의 별도 확인과 제한된 무수정 worker만 사용하며, 실제 적용은 원본 도구와 정확한 승인으로 실행하세요."
        )
    if (native_workspace is None) != (native_plan is None):
        raise ValueError("Native editing requires both workspace and exact plan")
    args = [
        sys.executable,
        str(ROOT / "scripts/native_hwp.py"),
        str(source),
        "--pdf",
        str(pdf),
    ]
    if native_plan is not None:
        args += [
            "--native-workspace",
            str(native_workspace),
            "--native-plan",
            str(native_plan),
        ]
    for key, value in [("--hwpx", hwpx), ("--hwp", hwp)]:
        if value is not None:
            args += [key, str(value)]
    try:
        run = subprocess.run(
            args,
            stdin=subprocess.DEVNULL,
            capture_output=True,
            encoding="utf-8",
            timeout=240,
        )
    except subprocess.TimeoutExpired as exc:
        raise RuntimeError(
            "Hancom timed out (possibly an access dialog). Stop and inspect the automation window; no global process kill or retry was attempted."
        ) from exc
    if run.returncode:
        raise RuntimeError("Hancom export failed:\n" + run.stderr[-6000:])
    try:
        return json.loads(run.stdout.strip())
    except json.JSONDecodeError as error:
        raise RuntimeError("Native export returned malformed JSON; no retry") from error


def validate_bbox(box: list, page: fitz.Page) -> fitz.Rect:
    if (
        not isinstance(box, list)
        or len(box) != 4
        or not all(isinstance(v, (int, float)) for v in box)
    ):
        raise ValueError("Invalid edit bounding box")
    rect = fitz.Rect(box)
    if rect.is_empty or rect.is_infinite or not page.rect.contains(rect):
        raise ValueError("Edit bounding box is outside the page")
    return rect


def compare_pdfs(
    before: Path, after: Path, masks: dict[int, list[list]] | None = None
) -> dict:
    """Exact raster equality OUTSIDE tight approved target rectangles; no tolerances."""
    masks = masks or {}
    with fitz.open(before) as a, fitz.open(after) as b:
        result = {
            "dpi": DPI,
            "before_pages": len(a),
            "after_pages": len(b),
            "page_count_equal": len(a) == len(b),
            "size_changed_pages": [],
            "outside_region_changed_pages": [],
            "any_pixel_changed_pages": [],
        }
        for i in range(min(len(a), len(b))):
            x, y = a[i], b[i]
            if x.rect != y.rect:
                result["size_changed_pages"].append(i + 1)
                continue
            px = x.get_pixmap(dpi=DPI, alpha=False, colorspace=fitz.csRGB)
            py = y.get_pixmap(dpi=DPI, alpha=False, colorspace=fitz.csRGB)
            if px.samples == py.samples:
                continue
            result["any_pixel_changed_pages"].append(i + 1)
            for box in masks.get(i + 1, []):
                rect = validate_bbox(box, x)
                pixel_box = (rect * fitz.Matrix(DPI / 72, DPI / 72)).irect
                px.set_rect(pixel_box, (255, 255, 255))
                py.set_rect(pixel_box, (255, 255, 255))
            if px.samples != py.samples:
                result["outside_region_changed_pages"].append(i + 1)
        result["passed"] = (
            result["page_count_equal"]
            and not result["size_changed_pages"]
            and not result["outside_region_changed_pages"]
        )
        return result


def map_anchors(anchors: list[dict], pdf: Path) -> tuple[list[dict], list[dict]]:
    """Conservative PDF hints, not authoritative HWP pagination. Ambiguity stays locked."""
    node_counts = Counter(normalized(a["old_text"]) for a in anchors)
    with fitz.open(pdf) as doc:
        texts = [normalized(cast(str, doc[i].get_text())) for i in range(len(doc))]
        pages = []
        for i in range(len(doc)):
            page = doc[i]
            pages.append(
                {
                    "page": i + 1,
                    "width": page.rect.width,
                    "height": page.rect.height,
                    "text_chars": len(page.get_text()),
                    "image_only": not bool(texts[i]),
                    "preview": f"pages/page-{i + 1:03d}.png",
                }
            )
        for anchor in anchors:
            needle = normalized(anchor["old_text"])
            hits = (
                [i for i, text in enumerate(texts) if needle and needle in text]
                if len(needle) >= 8
                else []
            )
            anchor["page_hint"], anchor["boxes"] = None, []
            if (
                len(hits) == 1
                and texts[hits[0]].count(needle) == 1
                and node_counts[needle] == 1
                and anchor["simple"]
            ):
                boxes = doc[hits[0]].search_for(anchor["old_text"].strip())
                if not boxes:
                    # Long unspaced slot IDs may wrap inside a table cell. Match the
                    # exact unique normalized character stream and retain ONLY its
                    # original glyph boxes, not a guessed cell or enlarged region.
                    chars, positions = [], []
                    for block_index, block in enumerate(
                        cast(
                            dict,
                            doc[hits[0]].get_text(
                                "rawdict",
                                flags=fitz.TEXTFLAGS_RAWDICT
                                & ~fitz.TEXT_PRESERVE_IMAGES,
                            ),
                        )["blocks"]
                    ):
                        for line_index, line in enumerate(block.get("lines", [])):
                            for span in line["spans"]:
                                for char in span["chars"]:
                                    folded = normalized(char["c"])
                                    chars.append(folded)
                                    positions.extend(
                                        [(block_index, line_index, char["bbox"])]
                                        * len(folded)
                                    )
                    stream = "".join(chars)
                    if stream.count(needle) == 1:
                        start = stream.index(needle)
                        lines = {}
                        for block_index, line_index, bbox in positions[
                            start : start + len(needle)
                        ]:
                            key = (block_index, line_index)
                            rect = fitz.Rect(bbox)
                            lines[key] = lines[key] | rect if key in lines else rect
                        boxes = list(lines.values())
                if boxes:
                    anchor["page_hint"] = hits[0] + 1
                    # Only original glyph rectangles + 1pt safety margin, not the whole cell/page.
                    anchor["boxes"] = [
                        list((r + (-1, -1, 1, 1)) & doc[hits[0]].rect) for r in boxes
                    ]
            for key in ["start", "end", "raw"]:
                anchor.pop(key, None)
        return anchors, pages


def prepare(
    source: Path, workspace: Path, *, exporter=None, progress=None, cancelled=None
) -> dict:
    # Desktop preparation supplies a THREE-PHASE, NO-EDIT exporter. The default
    # Native apply/export entry point remains blocked in frozen apps.
    export = native_export if exporter is None else exporter

    def checkpoint(step, message):
        if cancelled is not None and cancelled():
            from hwp_project import PreparationCancelled

            raise PreparationCancelled(
                "준비를 중단했습니다. 원본과 생성된 자료를 보존했습니다. 한글을 강제 종료하거나 준비를 자동 재시도하지 않습니다."
            )
        if progress is not None:
            progress(step, message)

    checkpoint(0, "원본과 저장 위치 확인")
    source, workspace = source.resolve(), workspace.resolve()
    if workspace.exists():
        raise FileExistsError(
            "Use a NEW workspace; existing baselines are never overwritten"
        )
    source_audit = hwp_audit(source)
    workspace.mkdir(parents=True)
    write_json(
        workspace / "preparing.json",
        {"source": str(source), "source_audit": source_audit},
    )
    checkpoint(1, "한글로 원본 미리보기 만들기")
    native = export(
        source, workspace / "baseline.pdf", hwpx=workspace / "baseline.hwpx"
    )
    if file_hash(source) != source_audit["sha256"]:
        raise ValueError("Source changed during conversion")
    checkpoint(2, "수정 없는 저장·재개방 확인")
    export(
        workspace / "baseline.hwpx",
        workspace / "roundtrip.pdf",
        hwp=workspace / "roundtrip.hwp",
    )
    # Check the actual persisted HWP, not merely the exporting application's buffer.
    checkpoint(3, "저장된 한글 파일 다시 열기")
    export(workspace / "roundtrip.hwp", workspace / "roundtrip-reopened.pdf")
    checkpoint(4, "페이지·글자·그림 보존 검사")
    check = compare_pdfs(
        workspace / "baseline.pdf", workspace / "roundtrip-reopened.pdf"
    )
    check["embedded_binaries_equal"] = (
        source_audit["embedded_binary_hashes"]
        == hwp_audit(workspace / "roundtrip.hwp")["embedded_binary_hashes"]
    )
    check["native_page_count_equal"] = native["native_pages"] == check["before_pages"]
    check["text_changed_pages"] = []
    with (
        fitz.open(workspace / "baseline.pdf") as a,
        fitz.open(workspace / "roundtrip-reopened.pdf") as b,
    ):
        check["text_changed_pages"] = [
            i + 1
            for i in range(min(len(a), len(b)))
            if a[i].get_text() != b[i].get_text()
        ]
    write_json(workspace / "roundtrip-verification.json", check)
    if (
        not check["passed"]
        or not check["embedded_binaries_equal"]
        or not check["native_page_count_equal"]
        or check["text_changed_pages"]
    ):
        raise ValueError("No-edit roundtrip failed; editing this document is BLOCKED")
    checkpoint(5, "검토할 페이지와 위치 연결")
    anchors, counts = index_hwpx(workspace / "baseline.hwpx")
    anchors, pages = map_anchors(anchors, workspace / "baseline.pdf")
    (workspace / "pages").mkdir()
    with fitz.open(workspace / "baseline.pdf") as doc:
        for i in range(len(doc)):
            page = doc[i]
            checkpoint(5, f"페이지 미리보기 만들기 · {i + 1}/{len(doc)}쪽")
            page.get_pixmap(dpi=100).save(workspace / pages[i]["preview"])
    manifest = {
        "schema": 1,
        "workspace_id": secrets.token_hex(16),
        "source": str(source),
        "source_audit": source_audit,
        "baseline_hwpx_sha256": file_hash(workspace / "baseline.hwpx"),
        "baseline_pdf_sha256": file_hash(workspace / "baseline.pdf"),
        "hancom": native,
        "objects": counts,
        "pages": pages,
        "anchors": anchors,
        "protected_pages": [p["page"] for p in pages if p["image_only"]],
        "roundtrip_passed": True,
    }
    checkpoint(5, "검토 자료 마무리")
    # Publish the ready marker LAST; interrupted preparation never looks ready.
    write_json(
        workspace / "comments.json",
        {"workspace_id": manifest["workspace_id"], "comments": []},
    )
    write_json(workspace / "manifest.json", manifest)
    if progress is not None:
        progress(6, "검토 준비 완료")
    return {
        "workspace": str(workspace),
        "pages": len(pages),
        "objects": counts,
        "mapped_editable_anchors": sum(bool(a["page_hint"]) for a in anchors),
        "protected_pages": manifest["protected_pages"],
        "roundtrip": check,
    }


def verify_baseline(workspace: Path) -> dict:
    manifest = read_json(workspace / "manifest.json")
    checks = [
        (Path(manifest["source"]), manifest["source_audit"]["sha256"]),
        (workspace / "baseline.hwpx", manifest["baseline_hwpx_sha256"]),
        (workspace / "baseline.pdf", manifest["baseline_pdf_sha256"]),
    ]
    if not manifest.get("roundtrip_passed") or any(
        file_hash(p) != sha for p, sha in checks
    ):
        raise ValueError(
            "Stale or unverified source/baseline. Start a new review workspace"
        )
    return manifest


def validate_plan(
    plan: dict, manifest: dict, baseline: Path | None = None
) -> list[dict]:
    if isinstance(plan, dict) and plan.get("schema") == 4:
        return hwp_fill_plan.validate(plan, manifest, baseline)
    if isinstance(plan, dict) and plan.get("schema") == 3:
        return hwp_fixed_layout.validate(plan, manifest, baseline)
    if isinstance(plan, dict) and plan.get("schema") == 2:
        return hwp_native_plan.validate(plan, manifest, baseline)
    if (
        set(plan) != {"schema", "workspace_id", "baseline_hwpx_sha256", "edits"}
        or type(plan["schema"]) is not int
        or plan["schema"] != 1
    ):
        raise ValueError("Invalid plan schema")
    if (
        plan["workspace_id"] != manifest["workspace_id"]
        or plan["baseline_hwpx_sha256"] != manifest["baseline_hwpx_sha256"]
    ):
        raise ValueError("Plan belongs to another baseline")
    if not isinstance(plan["edits"], list) or not 1 <= len(plan["edits"]) <= 100:
        raise ValueError("A plan needs 1..100 explicitly reviewed text edits")
    anchors = {a["anchor_id"]: a for a in manifest["anchors"]}
    seen, selected = set(), []
    for edit in plan["edits"]:
        if not isinstance(edit, dict) or set(edit) != {
            "anchor_id",
            "old_text",
            "new_text",
        }:
            raise ValueError("Edits may contain only anchor_id, old_text and new_text")
        key = edit["anchor_id"]
        if key in seen or key not in anchors:
            raise ValueError("Duplicate or unknown anchor")
        seen.add(key)
        anchor = anchors[key]
        new = edit["new_text"]
        if not isinstance(new, str) or not isinstance(edit["old_text"], str):
            raise ValueError("Text must be a string")
        if (
            not anchor["simple"]
            or not anchor["page_hint"]
            or not anchor["boxes"]
            or anchor["page_hint"] in manifest["protected_pages"]
        ):
            raise ValueError("Unmapped, complex or protected anchor: " + key)
        if edit["old_text"] != anchor["old_text"] or new == edit["old_text"]:
            raise ValueError("Stale text or no-op edit")
        if len(new) > 20000 or any(
            ord(c) < 32 or 0xD800 <= ord(c) <= 0xDFFF or ord(c) in (0xFFFE, 0xFFFF)
            for c in new
        ):
            raise ValueError(
                "Only bounded single-line XML text replacements are supported"
            )
        selected.append(anchor)
    if baseline is not None:
        check_text_layout_cache(baseline, plan["edits"])
    return selected


def check_text_layout_cache(before: Path, edits: list[dict]) -> None:
    """Reject invalid cached line offsets without rewriting locked layout XML.

    Hancom 11 rejects shortened paragraphs when lineseg.textpos exceeds the
    replacement's UTF-16 length. Offsets equal to the length are valid. Mixed
    controls have their own HWP positions, so length-changing edits there are
    conservatively unsupported when nonzero cached offsets are present.
    """
    selected = {e["anchor_id"]: e for e in edits}
    if len(selected) != len(edits):
        raise ValueError("Duplicate edit")
    errors = []
    for part, raw in archive(before).items():
        if not SECTIONS.fullmatch(part):
            continue
        anchors = [a for a in text_nodes(part, raw) if a["anchor_id"] in selected]
        if not anchors:
            continue
        root = xml_root(raw)
        paragraphs = {}
        for anchor in anchors:
            node, parents = root, []
            for index in anchor["path"]:
                parents.append(node)
                node = node[index]
            para = next((p for p in reversed(parents) if p.tag == HP + "p"), None)
            if para is None:
                raise ValueError("Edited text has no paragraph: " + anchor["anchor_id"])
            paragraphs.setdefault(para, {})[node] = selected[anchor["anchor_id"]]
        for para, changes in paragraphs.items():
            cache = para.find(HP + "linesegarray")
            if cache is None:
                continue
            positions = [int(segment.attrib["textpos"]) for segment in cache]
            if any(position < 0 for position in positions):
                raise ValueError("Negative cached line textpos")
            if not positions or max(positions) == 0:
                continue
            runs = para.findall(HP + "run")
            plain = all(
                child.tag == HP + "t" and not len(child)
                for run in runs
                for child in run
            )
            keys = ", ".join(edit["anchor_id"] for edit in changes.values())
            if not plain:
                if any(
                    len(edit["old_text"].encode("utf-16-le"))
                    != len(edit["new_text"].encode("utf-16-le"))
                    for edit in changes.values()
                ):
                    errors.append(
                        keys
                        + ": length-changing mixed/control paragraph cache is unsupported"
                    )
                continue
            new_text = "".join(
                changes[t]["new_text"] if t in changes else (t.text or "")
                for run in runs
                for t in run
            )
            length = len(new_text.encode("utf-16-le")) // 2
            if max(positions) > length:
                errors.append(
                    f"{keys}: cached textpos {max(positions)} exceeds new UTF-16 length {length}"
                )
    if errors:
        raise ValueError(
            "HWPX cached line positions would be invalid; Hancom may reject Open. "
            "No layout cache, font, table or edit mask was changed. "
            + "; ".join(errors)
        )


def patch_hwpx(before: Path, after: Path, edits: list[dict]) -> None:
    if after.exists():
        raise FileExistsError(after)
    check_text_layout_cache(before, edits)
    parts = archive(before)
    selected = {e["anchor_id"]: e for e in edits}
    if len(selected) != len(edits):
        raise ValueError("Duplicate edit")
    found = set()
    for part, raw in list(parts.items()):
        if not SECTIONS.fullmatch(part):
            continue
        replacements = []
        for anchor in text_nodes(part, raw):
            edit = selected.get(anchor["anchor_id"])
            if edit is None:
                continue
            if not anchor["simple"] or edit["old_text"] != anchor["old_text"]:
                raise ValueError("Stale/complex lexical anchor")
            new = html.escape(edit["new_text"], quote=False).encode("utf-8")
            match = TEXT.fullmatch(anchor["raw"])
            if match is None:
                raise ValueError("Invalid exact lexical text node")
            tag = match.group("tag")
            if anchor["raw"].rstrip().endswith(b"/>"):
                replacement = (
                    anchor["raw"].rstrip()[:-2].rstrip()
                    + b">"
                    + new
                    + b"</"
                    + tag
                    + b">"
                )
            else:
                replacement = (
                    anchor["raw"][: match.start("body")]
                    + new
                    + anchor["raw"][match.end("body") :]
                )
            replacements.append((anchor["start"], anchor["end"], replacement))
            found.add(anchor["anchor_id"])
        for start, end, replacement in sorted(replacements, reverse=True):
            raw = raw[:start] + replacement + raw[end:]
        parts[part] = raw
    if found != set(selected):
        raise ValueError("Some anchors were not found")
    # Preserve member metadata/order and original namespaces/bytes outside the chosen t nodes.
    with zipfile.ZipFile(before) as src, zipfile.ZipFile(after, "w") as dst:
        for info in src.infolist():
            dst.writestr(info, parts[info.filename])


def structural_gate(before: Path, after: Path, edits: list[dict]) -> None:
    a, b = archive(before), archive(after)
    if set(a) != set(b):
        raise ValueError("Archive membership changed")
    selected = {e["anchor_id"]: e for e in edits}
    if len(selected) != len(edits):
        raise ValueError("Duplicate edits")
    seen = set()
    for part in a:
        if not SECTIONS.fullmatch(part):
            if a[part] != b[part]:
                raise ValueError("Locked asset/style/metadata changed: " + part)
            continue
        ar, br = xml_root(a[part]), xml_root(b[part])
        an, bn = text_nodes(part, a[part]), text_nodes(part, b[part])
        if len(an) != len(bn):
            raise ValueError("Text node count changed")
        for old, new in zip(an, bn):
            if old["anchor_id"] != new["anchor_id"]:
                raise ValueError("Node addressing changed")
            edit = selected.get(old["anchor_id"])
            expected = edit["new_text"] if edit else old["old_text"]
            if new["old_text"] != expected or (
                edit and old["old_text"] != edit["old_text"]
            ):
                raise ValueError("Unapproved text changed")
            if edit:
                seen.add(old["anchor_id"])
        for root in [ar, br]:
            for node in root.iter(HP + "t"):
                node.text = "__LOCKED_TEXT__"
        if ET.tostring(ar) != ET.tostring(br):
            raise ValueError(
                "Locked structure, geometry, formatting or controls changed"
            )
        # Also lock lexical XML outside selected nodes (ET discards comments/PIs).
        locked_a, locked_b = a[part], b[part]
        for nodes, which in [(an, "a"), (bn, "b")]:
            raw = a[part] if which == "a" else b[part]
            for node in reversed(nodes):
                if node["anchor_id"] in selected:
                    raw = (
                        raw[: node["start"]]
                        + b"__APPROVED_TEXT_NODE__"
                        + raw[node["end"] :]
                    )
            if which == "a":
                locked_a = raw
            else:
                locked_b = raw
        if locked_a != locked_b:
            raise ValueError(
                "Unapproved XML bytes changed outside the selected text nodes"
            )
    if seen != set(selected):
        raise ValueError("Missing edited anchors")


def rendered_text_gate(
    before: Path, after: Path, edits: list[dict], selected: list[dict]
) -> bool:
    """Require the exact expected per-page text, including deletions; no ignored edits."""
    per_page = {}
    for edit, anchor in zip(edits, selected):
        per_page.setdefault(anchor["page_hint"], {})[normalized(edit["old_text"])] = (
            normalized(edit["new_text"])
        )
    with fitz.open(before) as a, fitz.open(after) as b:
        if len(a) != len(b):
            return False
        for i in range(len(a)):
            expected = normalized(cast(str, a[i].get_text()))
            replacements = per_page.get(i + 1, {})
            if replacements:
                if any(not old or expected.count(old) != 1 for old in replacements):
                    return False
                pattern = "|".join(
                    re.escape(old)
                    for old in sorted(replacements, key=len, reverse=True)
                )
                expected = re.sub(pattern, lambda m: replacements[m.group(0)], expected)
            if expected != normalized(cast(str, b[i].get_text())):
                return False
    return True


def approved_plan(workspace: Path, plan: dict, manifest: dict) -> list[dict]:
    selected = validate_plan(plan, manifest, workspace / "baseline.hwpx")
    approval = read_json(workspace / "approved-plan.json")
    if (
        approval.get("approved_via") != "local-review-ui"
        or approval.get("plan_sha256") != plan_hash(plan)
        or plan_hash(approval.get("plan")) != plan_hash(plan)
    ):
        raise ValueError(
            "The EXACT proposed diff has not been approved in the local review UI"
        )
    if plan["schema"] == 4 and plan_hash(approval.get("approved_scope")) != plan_hash(
        hwp_fill_plan.approval_scope(plan)
    ):
        raise ValueError(
            "Fresh UI confirmation of exact blank fields, policy and unsigned template is required"
        )
    if plan["schema"] in (2, 3) and (
        plan_hash(approval.get("approved_scope"))
        != plan_hash(native_approval_scope(plan))
        or approval.get("approved_scope", {}).get("native_policy_confirmed") is not True
    ):
        raise ValueError(
            "Explicit UI confirmation of native policy and every paragraph region is required"
        )
    return selected


def native_approval_scope(plan: dict) -> dict:
    if plan["schema"] == 4:
        return hwp_fill_plan.approval_scope(plan)
    scope = {
        "native_policy_confirmed": True,
        "regions_reviewed": [e["anchor_id"] for e in plan["edits"]],
        "edit_mode": plan["edit_mode"],
        "native_policy": plan["native_policy"],
    }
    if plan["schema"] == 3:
        scope.update(row_minima_confirmed=True, row_minima=plan["row_minima"])
    return scope


def approved_digest(workspace: Path, plan: dict | None, manifest: dict):
    if plan is None:
        return None
    try:
        approved_plan(workspace, plan, manifest)
        return plan_hash(plan)
    except (OSError, ValueError, KeyError):
        return None


def apply(
    workspace: Path,
    plan_path: Path,
    *,
    text_exporter=None,
    contract_exporter=None,
    expected_plan_sha256=None,
) -> dict:
    workspace = workspace.resolve()
    require_apply_unlocked(workspace)
    manifest = verify_baseline(workspace)
    plan = read_json(plan_path)
    if expected_plan_sha256 is not None and plan_hash(plan) != expected_plan_sha256:
        raise ValueError("Requested exact apply plan changed before acquisition")
    if text_exporter is not None and contract_exporter is not None:
        raise ValueError("Text and Native contract workers cannot be mixed")
    if contract_exporter is not None:
        import importlib

        contract_module = importlib.import_module("hwp_contract_apply")
        if (
            not isinstance(contract_exporter, contract_module.NativeContractExporter)
            or type(plan.get("schema")) is not int
            or plan["schema"] not in contract_module.PROPOSALS
            or contract_exporter.workspace != workspace
            or plan_path != workspace / contract_module.PROPOSALS[plan["schema"]]
            or contract_exporter.expected_hash != plan_hash(plan)
            or expected_plan_sha256 != plan_hash(plan)
        ):
            raise ValueError(
                "Only an exact bounded Native contract exporter is accepted"
            )
    if text_exporter is not None:
        from hwp_text_apply import TextExporter

        if (
            not isinstance(text_exporter, TextExporter)
            or plan.get("schema") != 1
            or text_exporter.workspace != workspace
            or plan_path != workspace / "proposal.json"
            or text_exporter.expected_hash != plan_hash(plan)
            or expected_plan_sha256 != plan_hash(plan)
        ):
            raise ValueError("Only an exact bounded schema-1 text exporter is accepted")
    export = (
        contract_exporter
        if contract_exporter is not None
        else (native_export if text_exporter is None else text_exporter)
    )
    selected = approved_plan(workspace, plan, manifest)
    root = workspace / "runs"
    if root.is_symlink() or root.resolve().parent != workspace:
        raise ValueError("Apply runs directory escapes workspace")
    run = root / (
        datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S")
        + "-"
        + secrets.token_hex(4)
    )
    lease = acquire_apply_lock(workspace, manifest, plan, run.name)
    run.mkdir(parents=True)
    write_json(run / "plan.json", plan)
    report = {
        "status": "in_progress",
        "stage": "input_preparation",
        "source_unchanged": None,
        "run": str(run),
        "result_binding_policy": RESULT_POLICY,
    }
    write_json(run / "verification.json", report)
    immutable = {"plan.json": file_hash(run / "plan.json")}
    finalization_stage = "result_integrity"
    try:
        # Recheck after exclusive acquisition; consent can never be inferred from a lock.
        selected = approved_plan(workspace, plan, verify_baseline(workspace))
        candidate = run / "candidate.hwpx"
        if plan["schema"] in (2, 3, 4):
            # The child rechecks the exact UI approval, edits only a source COPY,
            # and lets Hancom generate caches. Never feed it invalid patched XML.
            report["stage"] = "native_edit_export"
            write_json(run / "verification.json", report)
            edit_exporter = (
                native_export if contract_exporter is None else contract_exporter
            )
            report["native_edit_export"] = edit_exporter(
                Path(manifest["source"]),
                run / "candidate-buffer.pdf",
                hwpx=candidate,
                hwp=run / "candidate.hwp",
                native_workspace=workspace,
                native_plan=run / "plan.json",
            )
            for name, sha in result_artifact_hashes(run).items():
                if name != "verification.json":
                    immutable.setdefault(name, sha)
            report["stage"] = "structural_verification"
            write_json(run / "verification.json", report)
            if plan["schema"] == 4:
                report["native_contract"] = hwp_fill_plan.structural_gate(
                    workspace / "baseline.hwpx",
                    candidate,
                    selected,
                    plan["header_spine"],
                )
            else:
                gate = (
                    hwp_fixed_layout.structural_gate
                    if plan["schema"] == 3
                    else hwp_native_plan.structural_gate
                )
                report["native_contract"] = gate(
                    workspace / "baseline.hwpx", candidate, plan
                )
        else:
            patch_hwpx(workspace / "baseline.hwpx", candidate, plan["edits"])
            immutable["candidate.hwpx"] = file_hash(candidate)
            report["stage"] = "structural_verification"
            structural_gate(workspace / "baseline.hwpx", candidate, plan["edits"])
            report["structural_gate"] = True
            report["stage"] = "native_edit_export"
            write_json(run / "verification.json", report)
            export(candidate, run / "candidate-buffer.pdf", hwp=run / "candidate.hwp")
            for name, sha in result_artifact_hashes(run).items():
                if name != "verification.json":
                    immutable.setdefault(name, sha)
        report["structural_gate"] = True
        report["stage"] = "saved_hwp_reopen"
        write_json(run / "verification.json", report)
        reopened_native = export(run / "candidate.hwp", run / "candidate.pdf")
        if contract_exporter is not None:
            report["saved_hwp_reopen_export"] = reopened_native
            immutable["candidate-reopened.hwpx"] = reopened_native[
                "reopened_hwpx_sha256"
            ]
        for name, sha in result_artifact_hashes(run).items():
            if name != "verification.json":
                immutable.setdefault(name, sha)
        report["stage"] = "rendered_verification"
        write_json(run / "verification.json", report)
        masks = {}
        if plan["schema"] == 4:
            for field in selected:
                masks.setdefault(field["page_hint"], []).extend(field["boxes"])
        else:
            for edit, anchor in zip(plan["edits"], selected):
                masks.setdefault(anchor["page_hint"], []).extend(
                    [edit["region"]] if plan["schema"] in (2, 3) else anchor["boxes"]
                )
        report["layout_gate"] = compare_pdfs(
            workspace / "baseline.pdf", run / "candidate.pdf", masks
        )
        report["embedded_binaries_equal"] = (
            hwp_audit(run / "candidate.hwp")["embedded_binary_hashes"]
            == manifest["source_audit"]["embedded_binary_hashes"]
        )
        if plan["schema"] == 4:
            report["rendered_text_verification"] = (
                hwp_fill_plan.rendered_text_verification(
                    workspace / "baseline.pdf", run / "candidate.pdf", selected
                )
            )
            report["rendered_page_text_exact"] = report["rendered_text_verification"][
                "passed"
            ]
        else:
            report["rendered_page_text_exact"] = rendered_text_gate(
                workspace / "baseline.pdf",
                run / "candidate.pdf",
                plan["edits"],
                selected,
            )
        report["source_unchanged"] = (
            file_hash(Path(manifest["source"])) == manifest["source_audit"]["sha256"]
        )
        report["stage"] = "final_source_and_approval_check"
        write_json(run / "verification.json", report)
        approved_plan(workspace, plan, verify_baseline(workspace))
        if (
            expected_plan_sha256 is not None
            and plan_hash(read_json(plan_path)) != expected_plan_sha256
        ):
            raise ValueError("Requested exact proposal changed during apply")
        if all(
            [
                report["layout_gate"]["passed"],
                report["embedded_binaries_equal"],
                report["rendered_page_text_exact"],
                report["source_unchanged"],
            ]
        ):
            assert_apply_lock_owned(workspace, lease)
            report["status"] = "checks_passed_needs_human_visual_review"
            report["stage"] = "checks_passed"
        else:
            report["stage"] = "rendered_verification"
            raise ValueError(
                "Strict verification failed; candidate is NOT a deliverable"
            )
        write_json(run / "verification.json", report)
        seal_result(run, manifest, plan, immutable)
        finalization_stage = "lock_finalization"
        assert_apply_lock_owned(workspace, lease)
        (workspace / APPLY_LOCK).unlink()
        return report
    except BaseException as exc:
        if report["status"] == "checks_passed_needs_human_visual_review":
            report["stage"] = finalization_stage
        report["status"] = "blocked"
        if isinstance(exc, (KeyboardInterrupt, SystemExit)):
            report["interrupted_at_stage"] = report["stage"]
            report["stage"] = "interrupted"
            report["error"] = (
                "Apply interrupted; lock retained. Owner must inspect the owned worker/Hancom state before any new attempt"
            )
        else:
            report["error"] = str(exc)
        # Every non-success retains the lock: no inferred stale-lock recovery/retry.
        # A failed export never reached the success-path hash check. Record the
        # actual source state, not the initial unknown placeholder.
        try:
            report["source_unchanged"] = (
                file_hash(Path(manifest["source"]))
                == manifest["source_audit"]["sha256"]
            )
        except OSError as source_error:
            report["source_check_error"] = str(source_error)
        write_json(run / "verification.json", report)
        try:
            seal_result(
                run, manifest, plan
            )  # Bind blocked bytes only, NEVER turn a failure into a pass.
        except (OSError, ValueError, TypeError):
            pass  # Missing/invalid binding is itself blocked by read-only consumers.
        raise


def validate_comments(payload: dict, manifest: dict) -> dict:
    if (
        not isinstance(payload, dict)
        or set(payload) != {"workspace_id", "comments"}
        or payload["workspace_id"] != manifest["workspace_id"]
    ):
        raise ValueError("Invalid comments envelope")
    if not isinstance(payload["comments"], list) or len(payload["comments"]) > 500:
        raise ValueError("Invalid comment count")
    anchors = {a["anchor_id"]: a for a in manifest["anchors"]}
    seen_ids = set()
    for c in payload["comments"]:
        if not isinstance(c, dict) or set(c) != {
            "id",
            "page",
            "anchor_id",
            "region",
            "comment",
        }:
            raise ValueError("Comments are instructions, never approval/edits")
        if type(c["page"]) is not int or not 1 <= c["page"] <= len(manifest["pages"]):
            raise ValueError("Invalid physical page number")
        if (
            not isinstance(c["id"], str)
            or not isinstance(c["comment"], str)
            or not 1 <= len(c["comment"].strip()) <= 10000
        ):
            raise ValueError("Invalid comment text")
        if not c["id"].strip() or len(c["id"]) > 128 or c["id"] in seen_ids:
            raise ValueError("Comment ids must be nonempty and unique")
        seen_ids.add(c["id"])
        if c["anchor_id"] is not None:
            anchor = anchors.get(c["anchor_id"])
            if not anchor or anchor["page_hint"] != c["page"]:
                raise ValueError("Comment anchor does not match its page")
        if c["region"] is not None:
            r = c["region"]
            if (
                not isinstance(r, list)
                or len(r) != 2
                or any(not isinstance(v, (int, float)) or not 0 <= v <= 1 for v in r)
            ):
                raise ValueError("Region must be a normalized [x, y] point")
    return payload


def result_artifact_hashes(directory: Path) -> dict:
    """Fixed basenames only; no paths supplied by documents/reports."""
    hashes = {}
    for name in RESULT_ARTIFACTS + RESULT_OPTIONAL_ARTIFACTS:
        path = directory / name
        if (
            path.is_symlink()
            or path.exists()
            and (not path.is_file() or path.resolve().parent != directory.resolve())
        ):
            raise ValueError("Unsafe result artifact: " + name)
        if path.is_file():
            hashes[name] = file_hash(path)
    return hashes


def seal_result(
    directory: Path, manifest: dict, plan: dict, expected: dict | None = None
) -> None:
    """NEW apply output only. Never backfill/relabel an owner's historical run."""
    report = read_json(directory / "verification.json")
    if report.get("result_binding_policy") != RESULT_POLICY or report.get(
        "status"
    ) not in {"blocked", "checks_passed_needs_human_visual_review"}:
        raise ValueError("Cannot bind an unfinished or legacy result")
    hashes = result_artifact_hashes(directory)
    required: set[str] = (
        set(RESULT_ARTIFACTS)
        if report["status"] == "checks_passed_needs_human_visual_review"
        else {"plan.json", "verification.json"}
    )
    if "saved_hwp_reopen_export" in report:
        required.add("candidate-reopened.hwpx")
        native = report["saved_hwp_reopen_export"]
        if not isinstance(native, dict) or native.get(
            "reopened_hwpx_sha256"
        ) != hashes.get("candidate-reopened.hwpx"):
            raise ValueError("Saved-HWP structure proof differs from bound result")
    if not required.issubset(hashes):
        raise ValueError("Incomplete result artifacts")
    if expected is not None and any(
        hashes.get(name) != sha for name, sha in expected.items()
    ):
        raise ValueError("Result artifacts changed during verification")
    write_json(
        directory / RESULT_FILE,
        {
            "schema": 1,
            "policy": RESULT_POLICY,
            "workspace_id": manifest["workspace_id"],
            "baseline_hwpx_sha256": manifest["baseline_hwpx_sha256"],
            "run_id": directory.name,
            "plan_sha256": plan_hash(plan),
            "artifact_sha256": hashes,
        },
    )


def inspect_result(
    directory: Path, manifest: dict, plan: dict | None, report: dict | None
) -> dict:
    """Detect drift, not signatures/OS trust. Always read-only; never auto-reseal."""
    state = {
        "state": "invalid",
        "plan_sha256": plan_hash(plan) if isinstance(plan, dict) else None,
        "bound_metadata": False,
        "issues": [],
    }
    binding = directory / RESULT_FILE
    if isinstance(report, dict) and report.get("status") == "in_progress":
        state["state"] = "in_progress"
        return state
    if not binding.exists() and not binding.is_symlink():
        state["state"] = (
            "unbound_legacy"
            if report is None or "result_binding_policy" not in report
            else "missing"
        )
        return state
    try:
        if (
            binding.is_symlink()
            or not binding.is_file()
            or binding.stat().st_size > 16384
        ):
            return state
        data = read_json(binding)
        if (
            not isinstance(data, dict)
            or set(data)
            != {
                "schema",
                "policy",
                "workspace_id",
                "baseline_hwpx_sha256",
                "run_id",
                "plan_sha256",
                "artifact_sha256",
            }
            or type(data["schema"]) is not int
            or data["schema"] != 1
            or data["policy"] != RESULT_POLICY
            or data["workspace_id"] != manifest["workspace_id"]
            or data["baseline_hwpx_sha256"] != manifest["baseline_hwpx_sha256"]
            or data["run_id"] != directory.name
            or not RUN_ID.fullmatch(directory.name)
            or not isinstance(data["plan_sha256"], str)
            or not re.fullmatch(r"[a-f0-9]{64}", data["plan_sha256"])
        ):
            return state
        state["plan_sha256"] = data["plan_sha256"]
        state["bound_metadata"] = True
        hashes = data["artifact_sha256"]
        if (
            not isinstance(hashes, dict)
            or not {"plan.json", "verification.json"}.issubset(hashes)
            or not set(hashes).issubset(RESULT_ARTIFACTS + RESULT_OPTIONAL_ARTIFACTS)
            or any(
                not isinstance(sha, str) or not re.fullmatch(r"[a-f0-9]{64}", sha)
                for sha in hashes.values()
            )
        ):
            return state
        state["state"] = "mismatch"
        for name in RESULT_ARTIFACTS + RESULT_OPTIONAL_ARTIFACTS:
            path = directory / name
            present = path.exists() or path.is_symlink()
            if name not in hashes:
                if present:
                    state["issues"].append(
                        {"code": "unexpected_artifact", "artifact": name}
                    )
            elif (
                path.is_symlink()
                or not path.is_file()
                or path.resolve().parent != directory.resolve()
                or file_hash(path) != hashes[name]
            ):
                state["issues"].append(
                    {"code": "artifact_changed_or_missing", "artifact": name}
                )
        if (
            not isinstance(plan, dict)
            or plan_hash(plan) != data["plan_sha256"]
            or not isinstance(report, dict)
            or report.get("result_binding_policy") != RESULT_POLICY
            or report.get("status") == "checks_passed_needs_human_visual_review"
            and not set(RESULT_ARTIFACTS).issubset(hashes)
            or isinstance(report, dict)
            and "saved_hwp_reopen_export" in report
            and (
                not isinstance(report["saved_hwp_reopen_export"], dict)
                or "candidate-reopened.hwpx" not in hashes
                or report["saved_hwp_reopen_export"].get("reopened_hwpx_sha256")
                != hashes["candidate-reopened.hwpx"]
            )
        ):
            state["issues"].append(
                {"code": "binding_contract_mismatch", "artifact": None}
            )
        if not state["issues"]:
            state["state"] = "verified"
            state["artifact_sha256"] = dict(hashes)
    except (OSError, ValueError, TypeError):
        state["state"] = "invalid"
    return state


def result_json(directory: Path, name: str) -> dict | None:
    path = directory / name
    try:
        if (
            path.is_symlink()
            or not path.is_file()
            or path.resolve().parent != directory.resolve()
            or path.stat().st_size > 1024 * 1024
        ):
            return None
        data = read_json(path)
        return data if isinstance(data, dict) else None
    except (OSError, ValueError, TypeError):
        return None


def review_version(workspace: Path, manifest: dict, run_id: str) -> tuple[Path, dict]:
    """Read-only comparison artifacts; never follow paths from a verification report."""
    if not re.fullmatch(r"\d{8}T\d{6}-[a-f0-9]{8}", run_id):
        raise ValueError("Invalid result id")
    root = (workspace / "runs").resolve()
    directory = (root / run_id).resolve()
    if (
        root.parent != workspace.resolve()
        or directory.parent != root
        or directory.name != run_id
    ):
        raise ValueError("Result path escapes the review workspace")
    for name in ["plan.json", "verification.json", "candidate.pdf"]:
        path = directory / name
        if not path.is_file() or path.resolve().parent != directory:
            raise ValueError("Incomplete or unsafe comparison artifact")
    plan = result_json(directory, "plan.json")
    if (
        not isinstance(plan, dict)
        or plan.get("workspace_id") != manifest["workspace_id"]
        or plan.get("baseline_hwpx_sha256") != manifest["baseline_hwpx_sha256"]
    ):
        raise ValueError("Result belongs to another baseline")
    report = result_json(directory, "verification.json")
    if not isinstance(report, dict) or report.get("status") not in {
        "blocked",
        "checks_passed_needs_human_visual_review",
    }:
        raise ValueError("Result has no supported verification record")
    integrity = inspect_result(directory, manifest, plan, report)
    if integrity["state"] in RESULT_BAD_STATES:
        raise ValueError("Result artifact integrity is not confirmed; preview blocked")
    with fitz.open(directory / "candidate.pdf") as doc:
        after_pages = len(doc)
    before_pages = len(manifest["pages"])
    layout = report.get("layout_gate", {})
    if not isinstance(layout, dict) or any(
        not isinstance(layout.get(key, []), list)
        for key in ("any_pixel_changed_pages", "size_changed_pages")
    ):
        raise ValueError("Invalid comparison verification metadata")
    changed = set(
        p for p in layout.get("any_pixel_changed_pages", []) if type(p) is int
    )
    changed.update(p for p in layout.get("size_changed_pages", []) if type(p) is int)
    changed.update(
        range(min(before_pages, after_pages) + 1, max(before_pages, after_pages) + 1)
    )
    changed = sorted(
        p
        for p in changed
        if type(p) is int and 1 <= p <= max(before_pages, after_pages)
    )
    downloads = {}
    if (
        report["status"] == "checks_passed_needs_human_visual_review"
        and integrity["state"] == "verified"
    ):
        for kind, name in DOWNLOAD_FILES.items():
            size = (directory / name).stat().st_size
            if 0 < size <= DOWNLOAD_LIMIT:
                downloads[kind] = {
                    "sha256": integrity["artifact_sha256"][name],
                    "bytes": size,
                }
    return directory, {
        "downloads": downloads,
        "id": run_id,
        "status": report["status"],
        "artifact_integrity": integrity["state"],
        "before_pages": before_pages,
        "after_pages": after_pages,
        "changed_pages": changed,
        "page_count_equal": before_pages == after_pages,
    }


def result_download(workspace: Path, run_id: str, kind: str) -> tuple[bytes, str]:
    """Snapshot ONLY an integrity-bound passed result. No export, repair or consent."""
    from hwp_text_apply import real_path

    if kind not in DOWNLOAD_FILES:
        raise ValueError("Unsupported result file kind")
    workspace = real_path(workspace).resolve(strict=True)
    manifest = verify_baseline(workspace)
    directory, version = review_version(workspace, manifest, run_id)
    info = version["downloads"].get(kind)
    if info is None:
        raise ValueError(
            "검증된 내려받기 결과가 아닙니다. 실패/과거 미확인/빈 파일/128 MiB 초과는 차단합니다."
        )
    path = real_path(directory / DOWNLOAD_FILES[kind])
    with path.open("rb") as stream:
        blob = stream.read(DOWNLOAD_LIMIT + 1)
    if (
        not 0 < len(blob) <= DOWNLOAD_LIMIT
        or len(blob) != info["bytes"]
        or digest(blob) != info["sha256"]
    ):
        raise ValueError(
            "결과 파일이 바뀌었습니다. 내려받기 전에 상태를 확인하세요. 자동 복구하지 않습니다."
        )
    # Recheck all bound artifacts AND source after reading, then serve this byte
    # snapshot (not another filesystem read vulnerable to a replace between checks).
    _, current = review_version(workspace, verify_baseline(workspace), run_id)
    if current["downloads"].get(kind) != info:
        raise ValueError("Result metadata changed during download")
    return blob, info["sha256"]


def review_versions(workspace: Path, manifest: dict) -> list[dict]:
    root = workspace / "runs"
    if not root.is_dir():
        return []
    results = []
    for directory in sorted(root.iterdir(), key=lambda p: p.name, reverse=True)[:50]:
        try:
            _, metadata = review_version(workspace, manifest, directory.name)
            results.append(metadata)
        except (ValueError, TypeError, OSError, json.JSONDecodeError, RuntimeError):
            continue
    return results


def review_attempts(workspace: Path, manifest: dict) -> list[dict]:
    """Read-only status even when a gate stopped before a reopened candidate PDF."""
    root = (workspace / "runs").resolve()
    if not root.is_dir() or root.parent != workspace.resolve():
        return []
    results = []
    for path in sorted(root.iterdir(), key=lambda p: p.name, reverse=True)[:50]:
        try:
            if not re.fullmatch(r"\d{8}T\d{6}-[a-f0-9]{8}", path.name):
                continue
            directory = path.resolve()
            if directory.parent != root or directory.name != path.name:
                continue
            plan, report = (
                result_json(directory, "plan.json"),
                result_json(directory, "verification.json"),
            )
            integrity = inspect_result(directory, manifest, plan, report)
            valid_plan = (
                isinstance(plan, dict)
                and plan.get("workspace_id") == manifest["workspace_id"]
                and plan.get("baseline_hwpx_sha256") == manifest["baseline_hwpx_sha256"]
            )
            if not valid_plan and not integrity["bound_metadata"]:
                continue
            if integrity["state"] in RESULT_BAD_STATES:
                results.append(
                    {
                        "id": path.name,
                        "status": "blocked",
                        "plan_sha256": integrity["plan_sha256"],
                        "error": "Result artifact integrity is not confirmed; preview blocked. No automatic reseal/retry.",
                        "stage": "result_integrity",
                        "rendered_issues": [],
                        "source_unchanged": False,
                        "artifact_integrity": integrity["state"],
                        "artifact_issues": integrity["issues"],
                    }
                )
                continue
            if (
                not valid_plan
                or not isinstance(plan, dict)
                or not isinstance(report, dict)
                or report.get("status")
                not in {
                    "blocked",
                    "in_progress",
                    "checks_passed_needs_human_visual_review",
                }
            ):
                continue
            if report["status"] == "checks_passed_needs_human_visual_review":
                review_version(
                    workspace, manifest, path.name
                )  # A missing reopened PDF cannot look like success.
            stages = {
                "input_preparation",
                "native_edit_export",
                "structural_verification",
                "saved_hwp_reopen",
                "rendered_verification",
                "final_source_and_approval_check",
                "result_integrity",
                "lock_finalization",
                "interrupted",
                "checks_passed",
            }
            stage = report.get("stage")
            results.append(
                {
                    "id": path.name,
                    "status": report["status"],
                    "plan_sha256": plan_hash(plan),
                    "error": str(report.get("error", ""))[:500],
                    "stage": stage
                    if isinstance(stage, str) and stage in stages
                    else None,
                    "rendered_issues": hwp_fill_plan.review_render_issues(
                        report, plan, len(manifest.get("pages", []))
                    ),
                    "source_unchanged": report.get("source_unchanged") is True,
                    "artifact_integrity": integrity["state"],
                    "artifact_issues": integrity["issues"],
                }
            )
        except (ValueError, TypeError, OSError, RuntimeError):
            continue
    return results


def comparison_image(
    workspace: Path,
    directory: Path,
    side: str,
    number: int,
    dpi: int,
    *,
    before_pdf: Path | None = None,
) -> bytes:
    if side not in {"before", "after", "diff"} or dpi not in (100, 144) or number < 1:
        raise ValueError("Invalid comparison image request")
    with (
        fitz.open(
            workspace / "baseline.pdf" if before_pdf is None else before_pdf
        ) as before,
        fitz.open(directory / "candidate.pdf") as after,
    ):
        if number > max(len(before), len(after)):
            raise ValueError("Invalid comparison page")
        if side != "diff":
            doc = before if side == "before" else after
            if number > len(doc):
                raise ValueError("This version has no page at this number")
            return (
                doc[number - 1]
                .get_pixmap(dpi=dpi, alpha=False, colorspace=fitz.csRGB)
                .tobytes("png")
            )
        a = (
            before[number - 1].get_pixmap(dpi=dpi, alpha=False, colorspace=fitz.csRGB)
            if number <= len(before)
            else None
        )
        b = (
            after[number - 1].get_pixmap(dpi=dpi, alpha=False, colorspace=fitz.csRGB)
            if number <= len(after)
            else None
        )
        width, height = (
            max(p.width for p in (a, b) if p),
            max(p.height for p in (a, b) if p),
        )
        if width * height > 6000000:
            raise ValueError("Comparison canvas exceeds the supported size limit")
        base = a or b
        if base is None:
            raise ValueError("Comparison has no rendered page")
        light = fitz.Pixmap(fitz.csRGB, base.width, base.height, base.samples, False)
        light.tint_with(0xDDE3EB, 0xFFFFFF)
        light.set_origin(0, 0)
        result = fitz.Pixmap(fitz.csRGB, fitz.IRect(0, 0, width, height), False)
        result.clear_with(255)
        result.copy(light, fitz.IRect(0, 0, light.width, light.height))
        samples_a, samples_b = a.samples if a else b"", b.samples if b else b""
        for y in range(height):
            row_a = (
                samples_a[y * a.stride : (y + 1) * a.stride]
                if a and y < a.height
                else b""
            )
            row_b = (
                samples_b[y * b.stride : (y + 1) * b.stride]
                if b and y < b.height
                else b""
            )
            if row_a == row_b:
                continue
            for x in range(width):
                left, right = row_a[x * 3 : x * 3 + 3], row_b[x * 3 : x * 3 + 3]
                if left != right:
                    result.set_pixel(x, y, (239, 68, 86))
        return result.tobytes("png")


class _ReviewRequestHandler(BaseHTTPRequestHandler):
    def discard_rejected_body(self):
        """Drain only a bounded, declared body without parsing it or doing work.

        Closing with unread bytes can cause a Windows TCP reset instead of the
        403 response. Limit size AND total time so an unauthenticated slow
        sender cannot hold the serialized review server indefinitely.
        """
        try:
            remaining = int(self.headers.get("Content-Length", "0"))
        except ValueError:
            return
        if not 0 < remaining <= 1024 * 1024 or self.headers.get(
            "Transfer-Encoding"
        ):
            return
        deadline = time.monotonic() + 0.5
        previous = self.connection.gettimeout()
        try:
            while remaining and time.monotonic() < deadline:
                self.connection.settimeout(max(0.001, deadline - time.monotonic()))
                chunk = self.rfile.read1(min(65536, remaining))
                if not chunk:
                    break
                remaining -= len(chunk)
        except OSError:
            pass
        finally:
            self.connection.settimeout(previous)


def create_review_server(
    workspace: Path,
    port: int = 0,
    *,
    text_apply: bool = False,
    contract_apply: bool = False,
    template_review: bool = False,
    report_binding: str | None = None,
    document_project: bool = False,
) -> HTTPServer:
    """Bind a verified loopback server; the caller owns its lifetime, not Hancom.

    No document conversion, approval, or apply occurs at construction. The desktop
    shell and CLI share the SAME handler, token, origin, revision and approval gates.
    """
    if document_project and (not template_review or report_binding is not None):
        raise ValueError("Document projects use their own pinned report root")
    if report_binding is not None and not template_review:
        raise ValueError(
            "Report harness binding belongs to template review metadata"
        )
    if template_review:
        if text_apply or contract_apply:
            raise ValueError("Template REVIEW cannot enable Native apply")
        if frozen() and report_binding is None and not document_project:
            raise ValueError("Packaged report review requires a pinned report harness")
        import hwp_template_draft as template

        workspace = workspace.resolve(strict=True)
        report_control = None
        if document_project:
            from hwp_report_control import ReportControl

            report_control = ReportControl(workspace)
        else:
            template.state(workspace)
        token = secrets.token_hex(32)

        def harness_check():
            if report_binding is not None:
                if workspace.name != "review":
                    raise ValueError(
                        "Report harness must use its exact owned review directory"
                    )
                from hwp_report_harness import inspect as inspect_report

                assessment = inspect_report(workspace.parent, report_binding)
                if not assessment["gate_passed"]:
                    raise ValueError(
                        "Report harness blocked: " + "; ".join(assessment["issues"])
                    )
                return assessment
            return None

        harness_check()

        def current_state():
            if report_control is not None:
                return report_control.state()
            current = template.state(workspace)
            try:
                template.candidate_permission(workspace, current)
                current["draft_authorized"] = True
            except (FileNotFoundError, ValueError):
                current["draft_authorized"] = False
            current.update(template.result_state(workspace, current))
            assessment = harness_check()
            if assessment is not None:
                current["report_harness"] = {
                    key: assessment[key]
                    for key in (
                        "goal",
                        "decisions",
                        "binding_sha256",
                        "phase",
                        "warnings",
                    )
                }
            return current

        class TemplateHandler(_ReviewRequestHandler):
            def log_message(self, format, *args):
                pass

            def host_valid(self):
                return (
                    self.headers.get("Host")
                    == f"127.0.0.1:{cast(HTTPServer, self.server).server_port}"
                )

            def reply(
                self,
                code,
                data,
                content_type="application/json; charset=utf-8",
                extra=None,
            ):
                blob = (
                    data
                    if isinstance(data, bytes)
                    else json.dumps(data, ensure_ascii=False, allow_nan=False).encode(
                        "utf-8"
                    )
                )
                self.send_response(code)
                self.send_header("Content-Type", content_type)
                self.send_header("Content-Length", str(len(blob)))
                self.send_header("Cache-Control", "no-store")
                self.send_header("X-Content-Type-Options", "nosniff")
                self.send_header("Referrer-Policy", "no-referrer")
                self.send_header(
                    "Content-Security-Policy",
                    f"default-src 'none'; script-src 'nonce-{token}'; style-src 'nonce-{token}'; connect-src 'self'; img-src blob:; frame-ancestors 'none'; base-uri 'none'; form-action 'none'",
                )
                for key, value in (extra or {}).items():
                    self.send_header(key, value)
                self.end_headers()
                self.wfile.write(blob)

            def persist_authority(self, path, record):
                """Only this existing local UI persists task/acceptance authority."""
                path = Path(path).absolute()
                if not path.is_relative_to(workspace):
                    raise ValueError("Authority record escaped the owned workspace")
                for part in (path, *path.parents):
                    if part.is_symlink() or (
                        hasattr(part, "is_junction") and part.is_junction()
                    ):
                        raise ValueError("Linked authority paths are not allowed")
                blob = json.dumps(
                    record, ensure_ascii=False, allow_nan=False, indent=2
                ).encode("utf-8")
                if path.exists():
                    if not path.is_file() or path.stat().st_nlink != 1 or path.stat().st_size > 64 * 1024 * 1024:
                        raise ValueError("Invalid existing authority record")
                    if parse_json(path.read_bytes()) != record:
                        raise ValueError("Existing authority differs; preserve it")
                    return
                path.parent.mkdir(parents=True, exist_ok=True)
                with path.open("xb") as stream:
                    stream.write(blob)

            def do_GET(self):
                if not self.host_valid():
                    self.reply(403, {"error": "Invalid Host"})
                    return
                try:
                    if self.path == "/":
                        blob = (
                            (ROOT / ("web/report-workspace.html" if document_project else "web/template-review.html"))
                            .read_bytes()
                            .replace(b"__REVIEW_TOKEN__", token.encode("ascii"))
                        )
                        self.reply(200, blob, "text/html; charset=utf-8")
                        return
                    if urllib.parse.urlsplit(self.path).path == "/legal":
                        self.reply(
                            200,
                            legal_notice(ROOT).encode("utf-8"),
                            "text/plain; charset=utf-8",
                        )
                        return
                    if self.headers.get("X-Review-Token") != token:
                        self.reply(403, {"error": "Local review token required"})
                        return
                    if report_control is not None:
                        if self.path == "/api/report-state":
                            self.reply(200, current_state())
                        elif self.path == "/api/report-source":
                            item = report_control.source()
                            self.reply(
                                200, item["data"], "application/octet-stream",
                                {"Content-Disposition": 'attachment; filename="report-source.hwpx"',
                                 "X-Artifact-Sha256": item["sha256"]},
                            )
                        elif match := re.fullmatch(r"/api/report-artifact/([A-Za-z0-9_-]{1,100})", self.path):
                            item = report_control.candidate(match[1])
                            blob = Path(item["path"]).read_bytes()
                            if digest(blob) != item["sha256"]:
                                raise ValueError("Selected report bytes changed")
                            self.reply(
                                200, blob, "application/octet-stream",
                                {"Content-Disposition": 'attachment; filename="report-draft.hwpx"',
                                 "X-Artifact-Sha256": item["sha256"]},
                            )
                        else:
                            self.reply(404, {"error": "Not found"})
                        return
                    if self.path == "/api/template-state":
                        self.reply(200, current_state())
                    elif match := re.fullmatch(
                        r"/api/template-images/([a-f0-9]{64})/([01])(?:/(before|after)/(0|[1-9][0-9]{0,3}))?",
                        self.path,
                    ):
                        harness_check()
                        images = template.image_review(
                            workspace, match[1], int(match[2])
                        )
                        if match[3] is None:
                            for side in ("before", "after"):
                                images[side] = [
                                    {
                                        key: value
                                        for key, value in item.items()
                                        if key != "_blob"
                                    }
                                    for item in images[side]
                                ]
                            self.reply(200, images)
                        else:
                            item = next(
                                (
                                    item
                                    for item in images[match[3]]
                                    if item["index"] == int(match[4])
                                ),
                                None,
                            )
                            if item is None or not item["available"]:
                                raise LookupError("No selected PNG preview")
                            self.reply(
                                200,
                                item["_blob"],
                                "image/png",
                                {"X-Image-Sha256": item["sha256"]},
                            )
                    elif re.fullmatch(r"/artifact/[01]", self.path):
                        current = current_state()
                        index = int(self.path.rsplit("/", 1)[1])
                        if index >= len(current["artifacts"]):
                            raise ValueError("No completed review artifact")
                        item = current["artifacts"][index]
                        blob = template.artifact_bytes(workspace, current, index, item)
                        expected = current["plan"]["jobs"][index]["preview"][
                            "candidate_sha256"
                        ]
                        self.reply(
                            200,
                            blob,
                            "application/octet-stream",
                            {
                                "Content-Disposition": f'attachment; filename="document-{index + 1}-template-review.hwpx"',
                                "X-Artifact-Sha256": expected,
                            },
                        )
                    else:
                        self.reply(404, {"error": "Not found"})
                except LookupError as exc:
                    self.reply(404, {"error": str(exc)})
                except Exception as exc:
                    self.reply(409, {"error": str(exc)})

            def do_POST(self):
                origin = f"http://127.0.0.1:{cast(HTTPServer, self.server).server_port}"
                if (
                    not self.host_valid()
                    or self.headers.get("Origin") != origin
                    or self.headers.get("X-Review-Token") != token
                ):
                    self.discard_rejected_body()
                    self.reply(
                        403, {"error": "Same-origin local review token required"}
                    )
                    return
                try:
                    if report_control is not None and (
                        self.path == "/api/report-material"
                        or self.path.startswith("/api/report-import/")
                    ):
                        current = current_state()
                        if self.headers.get("If-Match") != current["revision"]:
                            raise ValueError("작업 상태가 달라졌습니다. 현재 상태를 확인하세요.")
                        length = int(self.headers.get("Content-Length", "0"))
                        name = urllib.parse.unquote(self.headers.get("X-Report-Name", ""))
                        maximum = (512 * 1024 if self.path == "/api/report-material"
                                   and Path(name).suffix.lower() != ".hwpx" else 64 * 1024 * 1024)
                        if not 0 < length <= maximum:
                            raise ValueError("Selected file exceeds the upload bound")
                        raw = self.rfile.read(length)
                        if len(raw) != length:
                            raise ValueError("Selected file transfer was incomplete")
                        if self.path == "/api/report-material":
                            self.reply(201, report_control.upload(raw, name))
                        else:
                            match = re.fullmatch(r"/api/report-import/([A-Za-z0-9_-]{1,100})", self.path)
                            if match is None:
                                raise ValueError("Invalid source revision")
                            self.reply(201, report_control.import_revision(match[1], raw, name))
                        return
                    if self.headers.get("Content-Type") != "application/json":
                        raise ValueError("JSON request required")
                    length = int(self.headers.get("Content-Length", "0"))
                    if not 0 < length <= (1024 * 1024 if document_project else 16384):
                        raise ValueError("Invalid request size")
                    payload = parse_json(self.rfile.read(length))
                    current = current_state()
                    if self.headers.get("If-Match") != current["revision"]:
                        self.reply(409, {"error": "Stale review revision"})
                        return
                    if report_control is not None:
                        actions = {
                            "/api/report-model/discover": report_control.discover,
                            "/api/report-model/prepare": report_control.prepare_model,
                            "/api/report-model/connect": report_control.connect_model,
                            "/api/report-preview": report_control.preview,
                            "/api/report-omp-context": report_control.omp_context,
                            "/api/report-memo-target": report_control.memo_target,
                            "/api/report-omp-proposal": report_control.omp_proposal,
                            "/api/report-open": report_control.open,
                        }
                        if self.path in actions:
                            self.reply(200, actions[self.path](payload))
                        elif self.path == "/api/report-start":
                            path, record = report_control.task_record(payload)
                            self.persist_authority(path, record)
                            self.reply(202, report_control.start(payload))
                        elif self.path == "/api/report-omp-create":
                            path, record = report_control.omp_task_record(payload)
                            self.persist_authority(path, record)
                            self.reply(202, report_control.omp_start(payload))
                        elif self.path == "/api/report-accept":
                            path, record = report_control.acceptance_record(payload)
                            self.persist_authority(path, record)
                            self.reply(200, {"accepted": True, "creation_performed": False})
                        elif self.path == "/api/report-import-opened":
                            self.reply(201, report_control.import_opened(payload))
                        else:
                            self.reply(404, {"error": "Not found"})
                        return
                    if self.path == "/api/template-draft":
                        if payload != {
                            "plan_sha256": current["plan_sha256"],
                            "draft_only_confirmed": True,
                        } or payload.get("draft_only_confirmed") is not True:
                            raise ValueError("Explicit bounded unapproved draft permission required")
                        completed = template.result_state(workspace, current)
                        if completed["artifacts"]:
                            self.reply(200, completed)
                            return
                        require_apply_unlocked(workspace)
                        path, record = template.draft_permission_record(workspace, current["revision"])
                        self.persist_authority(path, record)
                        ready = template.state(workspace)
                        self.reply(200, template.apply_candidate(workspace, ready["revision"], file_hash(path)))
                    elif self.path == "/api/template-accept":
                        if (
                            not isinstance(payload, dict)
                            or set(payload) != {"index", "sha256"}
                            or type(payload["index"]) is not int
                        ):
                            raise ValueError("Exact generated artifact selection required")
                        path, record = template.artifact_acceptance_record(
                            workspace, payload["index"], payload["sha256"]
                        )
                        self.persist_authority(path, record)
                        self.reply(200, {"accepted": True, "creation_performed": False})
                    else:
                        self.reply(404, {"error": "Not found"})
                except Exception as exc:
                    self.reply(409, {"error": str(exc)})

        class TemplateServer(HTTPServer):
            def server_close(self):
                if report_control is not None:
                    report_control.close()
                super().server_close()

        server = TemplateServer(("127.0.0.1", port), TemplateHandler)
        server.report_control = report_control
        server.template_review_token = token
        return server

    manifest = verify_baseline(workspace)
    from hwp_text_apply import TextApplyController

    controller = TextApplyController(workspace, enabled=text_apply)
    import importlib

    native_controller = importlib.import_module(
        "hwp_contract_control"
    ).NativeApplyController(
        workspace, enabled=contract_apply, guard=controller.guard, peers=(controller,)
    )
    controller.peers = (native_controller,)
    agents = importlib.import_module("hwp_agents").Discovery()
    pi_probe = importlib.import_module("hwp_pi_probe").PiProbe(agents)

    def read_agent_jobs(current):
        return {
            "text_apply": controller.snapshot(),
            "contract_apply": native_controller.snapshot(),
            "apply_state": apply_state(workspace, current),
            "attempts": review_attempts(workspace, current),
        }

    review_context = importlib.import_module("hwp_pi_context").ReviewContext(
        workspace, read_agent_jobs
    )
    pi_chat = importlib.import_module("hwp_pi_chat").Chat(
        agents, context_provider=review_context
    )
    token = secrets.token_hex(32)
    template_path = ROOT / "web/review.html"
    search_index = None
    preview_cache = OrderedDict()

    def search_pages(query: str) -> list[dict]:
        nonlocal search_index
        if not query.strip() or len(query) > 200:
            raise ValueError("Search requires 1..200 characters")
        if search_index is None:
            search_index = []
            with fitz.open(workspace / "baseline.pdf") as doc:
                for page in doc:
                    display = " ".join(
                        unicodedata.normalize(
                            "NFKC", cast(str, page.get_text())
                        ).split()
                    )
                    folded, positions = [], []
                    for pos, char in enumerate(display):
                        if not char.isspace():
                            chars = char.casefold()
                            folded.append(chars)
                            positions.extend([pos] * len(chars))
                    search_index.append((display, "".join(folded), positions))
        needle = normalized(query).casefold()
        matches = []
        for i, (display, folded, positions) in enumerate(search_index):
            found = folded.find(needle)
            if found >= 0:
                start = max(0, positions[found] - 24)
                end = min(len(display), positions[found + len(needle) - 1] + 80)
                matches.append(
                    {
                        "page": i + 1,
                        "snippet": ("…" if start else "")
                        + display[start:end]
                        + ("…" if end < len(display) else ""),
                    }
                )
        return matches

    class Handler(_ReviewRequestHandler):
        # Per-read timeout. Desktop shutdown also bounds its own wait and keeps
        # ownership if an active request has not finished (no forced PID kill).
        timeout = 15

        def handle(self):
            try:
                super().handle()
            except ConnectionError:
                # A browser/client may disappear mid-response. This is not a
                # reason to crash a windowed server or conceal other exceptions.
                self.close_connection = True

        def log_message(self, format, *args):
            # pythonw has no stderr. Logging must not break a valid request.
            if sys.stderr is not None:
                super().log_message(format, *args)

        def reply(
            self,
            code,
            body,
            content_type="application/json; charset=utf-8",
            *,
            headers=None,
        ):
            if isinstance(body, (dict, list)):
                body = json.dumps(body, ensure_ascii=False).encode()
            elif isinstance(body, str):
                body = body.encode()
            self.send_response(code)
            self.send_header("Content-Type", content_type)
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header(
                "Content-Security-Policy",
                "default-src 'self'; script-src 'unsafe-inline'; style-src 'unsafe-inline'; img-src 'self'; connect-src 'self'; frame-ancestors 'none'; base-uri 'none'; form-action 'none'",
            )
            if headers is not None:
                for name, value in headers.items():
                    self.send_header(name, value)
            self.end_headers()
            self.wfile.write(body)

        def host_ok(self):
            return (
                self.headers.get("Host")
                == f"127.0.0.1:{cast(HTTPServer, self.server).server_port}"
            )

        def do_GET(self):
            if not self.host_ok():
                self.reply(403, {"error": "Invalid Host"})
                return
            url = urllib.parse.urlsplit(self.path)
            route = url.path
            params = urllib.parse.parse_qs(url.query)
            try:
                if route == "/legal":
                    self.reply(
                        200,
                        legal_notice(ROOT),
                        "text/plain; charset=utf-8",
                    )
                elif route == "/fill":
                    template = (ROOT / "web/fill-review.html").read_text(
                        encoding="utf-8"
                    )
                    self.reply(
                        200,
                        template.replace("__TOKEN__", token),
                        "text/html; charset=utf-8",
                    )
                elif route == "/api/fill-state":
                    current = verify_baseline(workspace)
                    data = read_json(workspace / "fill-input.json")
                    mapped, draft, active = hwp_fill_plan.replay(
                        workspace / "baseline.hwpx", data["bindings"], data["record"]
                    )
                    proposal_path = workspace / "proposal-fill.json"
                    proposal_blob = (
                        proposal_path.read_bytes() if proposal_path.exists() else None
                    )
                    plan = (
                        parse_json(proposal_blob) if proposal_blob is not None else None
                    )
                    proposal_revision = (
                        digest(proposal_blob) if proposal_blob is not None else "absent"
                    )
                    error = None
                    if plan is not None:
                        try:
                            if (
                                plan["bindings"] != data["bindings"]
                                or plan["record"] != data["record"]
                            ):
                                raise ValueError(
                                    "Fill input changed; compile a new proposal"
                                )
                            validate_plan(plan, current, workspace / "baseline.hwpx")
                        except Exception as exc:
                            error, plan = str(exc), None
                    self.reply(
                        200,
                        {
                            "manifest": current,
                            "input_sha256": plan_hash(data),
                            "proposal_revision": proposal_revision,
                            "draft": draft,
                            "fields": [
                                f
                                for f in mapped["fields"]
                                if f["field_id"] in {i["field_id"] for i in active}
                            ],
                            "regions": hwp_fill_plan.empty_regions(
                                workspace / "baseline.pdf", current["protected_pages"]
                            ),
                            "header_spine": hwp_fill_plan.header_spine(
                                workspace / "baseline.hwpx"
                            ),
                            "proposal": plan,
                            "proposal_error": error,
                            "proposal_sha256": plan_hash(plan) if plan else None,
                            "approved_sha256": approved_digest(
                                workspace, plan, current
                            ),
                            "contract_apply": native_controller.snapshot(),
                            "approval_scope_sha256": plan_hash(
                                native_approval_scope(plan)
                            )
                            if plan
                            else None,
                            "versions": review_versions(workspace, current),
                            "apply_state": apply_state(workspace, current),
                            "latest_attempt": next(
                                (
                                    a
                                    for a in review_attempts(workspace, current)
                                    if plan and a["plan_sha256"] == plan_hash(plan)
                                ),
                                None,
                            ),
                        },
                    )
                elif route == "/":
                    # UI-only changes take effect on refresh without restarting the server.
                    template = template_path.read_text(encoding="utf-8")
                    self.reply(
                        200,
                        template.replace("__TOKEN__", token),
                        "text/html; charset=utf-8",
                    )
                elif route == "/api/state":
                    kind = params.get("proposal", ["text"])[0]
                    if kind not in ("text", "native", "layout"):
                        raise ValueError("Unknown proposal kind")
                    path = (
                        workspace
                        / {
                            "text": "proposal.json",
                            "native": "proposal-native.json",
                            "layout": "proposal-layout.json",
                        }[kind]
                    )
                    current = verify_baseline(workspace)
                    proposal_blob = path.read_bytes() if path.exists() else None
                    proposal_revision = (
                        digest(proposal_blob) if proposal_blob is not None else "absent"
                    )
                    proposals, proposal_error = None, None
                    if proposal_blob is not None:
                        try:
                            proposals = parse_json(proposal_blob)
                            if (
                                not isinstance(proposals, dict)
                                or proposals.get("schema")
                                != {"text": 1, "native": 2, "layout": 3}[kind]
                            ):
                                raise ValueError(
                                    "Proposal does not match the selected review contract"
                                )
                            validate_plan(
                                proposals, current, workspace / "baseline.hwpx"
                            )
                        except Exception as exc:
                            proposal_error, proposals = str(exc), None
                    comments_blob = (workspace / "comments.json").read_bytes()
                    comments = validate_comments(parse_json(comments_blob), current)
                    self.reply(
                        200,
                        {
                            "manifest": current,
                            "comments": comments,
                            "comments_revision": digest(comments_blob),
                            "proposal_revision": proposal_revision,
                            "proposal": proposals,
                            "proposal_sha256": plan_hash(proposals)
                            if proposals
                            else None,
                            "proposal_error": proposal_error,
                            "proposal_kind": kind,
                            "text_apply": controller.snapshot(),
                            "contract_apply": native_controller.snapshot(),
                            "approval_scope_sha256": plan_hash(
                                native_approval_scope(proposals)
                            )
                            if proposals and proposals.get("schema") in (2, 3, 4)
                            else None,
                            "approved_sha256": approved_digest(
                                workspace, proposals, current
                            ),
                            "versions": review_versions(workspace, current),
                            "apply_state": apply_state(workspace, current),
                            "latest_attempt": next(
                                (
                                    a
                                    for a in review_attempts(workspace, current)
                                    if proposals
                                    and a["plan_sha256"] == plan_hash(proposals)
                                ),
                                None,
                            ),
                        },
                    )
                elif route in ("/api/agents", "/api/pi", "/api/pi/chat"):
                    expected_origin = f"http://{self.headers.get('Host')}"
                    if self.headers.get("X-Review-Token") != token or self.headers.get(
                        "Origin"
                    ) not in (None, expected_origin):
                        self.reply(
                            403, {"error": "Local agent presence token required"}
                        )
                        return
                    if params:
                        raise ValueError(
                            "Agent presence does not accept paths or commands"
                        )
                    self.reply(
                        200,
                        {
                            "/api/agents": agents,
                            "/api/pi": pi_probe,
                            "/api/pi/chat": pi_chat,
                        }[route].snapshot(),
                    )
                elif route == "/api/workflow":
                    import importlib

                    workflow = importlib.import_module("hwp_workflow")
                    current = verify_baseline(workspace)
                    saved_workflow = workflow.read(workspace, current)
                    comments = validate_comments(
                        read_json(workspace / "comments.json"), current
                    )["comments"]
                    saved_workflow["comment_hashes"] = {
                        c["id"]: plan_hash(c) for c in comments
                    }
                    self.reply(200, saved_workflow)
                elif re.fullmatch(r"/evidence/[a-f0-9]{64}", route):
                    if self.headers.get("X-Review-Token") != token:
                        self.reply(403, {"error": "Local evidence token required"})
                        return
                    import importlib

                    workflow = importlib.import_module("hwp_workflow")
                    sha = route.rsplit("/", 1)[1]
                    blob = workflow.evidence_bytes(
                        workspace, verify_baseline(workspace), sha
                    )
                    self.reply(
                        200,
                        blob,
                        "application/octet-stream",
                        headers={
                            "Content-Disposition": 'attachment; filename="evidence-'
                            + sha
                            + '.bin"',
                            "X-Artifact-Sha256": sha,
                            "Content-Length": str(len(blob)),
                        },
                    )
                elif route == "/api/session-history":
                    import importlib

                    recovery = importlib.import_module("hwp_review_session")
                    current = verify_baseline(workspace)
                    self.reply(
                        200,
                        {
                            "history": recovery.list_history(workspace),
                            "revision": recovery.read_snapshot(workspace, current)[
                                "revision"
                            ],
                        },
                    )
                elif route == "/api/session":
                    import importlib

                    recovery = importlib.import_module("hwp_review_session")
                    self.reply(
                        200,
                        recovery.read_snapshot(workspace, verify_baseline(workspace)),
                    )
                elif route == "/api/versions":
                    current = verify_baseline(workspace)
                    self.reply(
                        200,
                        {
                            "versions": review_versions(workspace, current),
                            "attempts": review_attempts(workspace, current),
                            "text_apply": controller.snapshot(),
                            "contract_apply": native_controller.snapshot(),
                            "apply_state": apply_state(workspace, current),
                        },
                    )
                elif re.fullmatch(
                    r"/compare-pair/\d{8}T\d{6}-[a-f0-9]{8}/\d{8}T\d{6}-[a-f0-9]{8}/(?:before|after|diff)/\d+\.png",
                    route,
                ):
                    current = verify_baseline(workspace)
                    _, _, first_id, second_id, side, filename = route.split("/")
                    first, first_meta = review_version(workspace, current, first_id)
                    second, second_meta = review_version(workspace, current, second_id)
                    if any(
                        meta["artifact_integrity"] != "verified"
                        for meta in (first_meta, second_meta)
                    ):
                        raise ValueError(
                            "Unbound history cannot be used as a comparison reference"
                        )
                    self.reply(
                        200,
                        comparison_image(
                            workspace,
                            second,
                            side,
                            int(filename[:-4]),
                            int(params.get("dpi", ["100"])[0]),
                            before_pdf=first / "candidate.pdf",
                        ),
                        "image/png",
                    )
                elif re.fullmatch(
                    r"/comparison/[0-9T\-a-f]+/(?:before|after|diff)/\d+\.png", route
                ):
                    _, _, run_id, side, filename = route.split("/")
                    directory, _ = review_version(workspace, manifest, run_id)
                    number = int(filename.split(".")[0])
                    dpi = int(params.get("dpi", ["100"])[0])
                    pdf = directory / "candidate.pdf"
                    key = (
                        run_id,
                        side,
                        number,
                        dpi,
                        pdf.stat().st_mtime_ns,
                        pdf.stat().st_size,
                    )
                    if key not in preview_cache:
                        preview_cache[key] = comparison_image(
                            workspace, directory, side, number, dpi
                        )
                        if len(preview_cache) > 6:
                            preview_cache.popitem(last=False)
                    preview_cache.move_to_end(key)
                    self.reply(200, preview_cache[key], "image/png")
                elif re.fullmatch(
                    r"/result/\d{8}T\d{6}-[a-f0-9]{8}/(?:hwp|pdf)", route
                ):
                    origin = (
                        f"http://127.0.0.1:{cast(HTTPServer, self.server).server_port}"
                    )
                    if self.headers.get("X-Review-Token") != token or self.headers.get(
                        "Origin"
                    ) not in (None, origin):
                        self.reply(
                            403,
                            {"error": "Local token-protected result download required"},
                        )
                        return
                    _, _, run_id, kind = route.split("/")
                    blob, sha = result_download(workspace, run_id, kind)
                    filename = urllib.parse.quote(f"검토용-{run_id}.{kind}", safe="")
                    self.reply(
                        200,
                        blob,
                        "application/pdf"
                        if kind == "pdf"
                        else "application/octet-stream",
                        headers={
                            "Content-Disposition": "attachment; filename*=UTF-8''"
                            + filename,
                            "Content-Length": str(len(blob)),
                            "X-Artifact-Sha256": sha,
                            "ETag": '"' + sha + '"',
                        },
                    )
                elif route == "/api/search":
                    query = params.get("q", [""])[0]
                    self.reply(
                        200,
                        {"query": query, "matches": search_pages(query), "ocr": False},
                    )
                elif re.fullmatch(r"/page/\d+\.png", route):
                    number = int(route.split("/")[-1].split(".")[0])
                    if not 1 <= number <= len(manifest["pages"]):
                        raise ValueError("Invalid page")
                    dpi = int(params.get("dpi", ["100"])[0])
                    if dpi not in (100, 144):
                        raise ValueError("Preview DPI must be 100 or 144")
                    if dpi == 100:
                        image = (
                            workspace / f"pages/page-{number:03d}.png"
                        ).read_bytes()
                    else:
                        key = (number, dpi)
                        if key not in preview_cache:
                            with fitz.open(workspace / "baseline.pdf") as doc:
                                preview_cache[key] = (
                                    doc[number - 1]
                                    .get_pixmap(dpi=dpi, alpha=False)
                                    .tobytes("png")
                                )
                            if len(preview_cache) > 6:
                                preview_cache.popitem(last=False)
                        preview_cache.move_to_end(key)
                        image = preview_cache[key]
                    self.reply(200, image, "image/png")
                else:
                    self.reply(404, {"error": "Not found"})
            except Exception as exc:
                self.reply(400, {"error": str(exc)})

        def do_POST(self):
            origin = f"http://127.0.0.1:{cast(HTTPServer, self.server).server_port}"
            if (
                not self.host_ok()
                or self.headers.get("Origin") != origin
                or self.headers.get("X-Review-Token") != token
                or self.headers.get("Content-Type") != "application/json"
            ):
                self.discard_rejected_body()
                self.reply(403, {"error": "Local same-origin review required"})
                return
            try:
                length = int(self.headers.get("Content-Length", "0"))
                if not 0 < length <= 1024 * 1024:
                    raise ValueError("Request exceeds size limit")
                payload = parse_json(self.rfile.read(length))
                if self.path == "/api/agents/discover":
                    if type(payload) is not dict or payload:
                        raise ValueError(
                            "Agent presence accepts an empty object only; no paths/commands"
                        )
                    self.reply(202, agents.start())
                    return
                if self.path == "/api/pi/chat/prepare":
                    if (
                        payload != {"prepare_confirmed": True}
                        or payload.get("prepare_confirmed") is not True
                    ):
                        raise ValueError("Explicit Pi chat program check required")
                    self.reply(202, pi_chat.prepare())
                    return
                chat_actions = {
                    "/api/pi/chat/connect": pi_chat.connect,
                    "/api/pi/chat/send": pi_chat.send,
                    "/api/pi/chat/context": pi_chat.preview_context,
                    "/api/pi/chat/artifact-review": pi_chat.review_artifact,
                    "/api/pi/chat/cancel": pi_chat.cancel_turn,
                    "/api/pi/chat/disconnect": pi_chat.disconnect,
                }
                if self.path in chat_actions:
                    self.reply(202, chat_actions[self.path](payload))
                    return
                if self.path == "/api/pi/prepare":
                    if (
                        payload != {"prepare_confirmed": True}
                        or payload.get("prepare_confirmed") is not True
                    ):
                        raise ValueError(
                            "Explicit Pi installation check required; no prompt/path arguments"
                        )
                    self.reply(202, pi_probe.prepare())
                    return
                if self.path == "/api/pi/probe":
                    self.reply(202, pi_probe.start(payload))
                    return
                current = verify_baseline(workspace)
                if self.path in {
                    "/api/text-proposal",
                    "/api/fill-proposal",
                    "/api/approve",
                    "/api/apply-text",
                    "/api/apply-contract",
                } and (
                    apply_state(workspace, current)["locked"]
                    or controller.snapshot()["active"]
                    or native_controller.snapshot()["active"]
                ):
                    self.reply(
                        423,
                        {
                            "error": "적용 중이거나 중단된 잠금이 있습니다. 기존 작업·한글 상태를 소유자가 확인해야 합니다. 자료/승인을 변경하거나 자동 재시도하지 않습니다.",
                            "apply_locked": True,
                        },
                    )
                    return
                if self.path == "/api/comments":
                    payload = validate_comments(payload, current)
                    revision = self.headers.get("If-Match")
                    if revision is None:
                        self.reply(
                            428,
                            {
                                "error": "코멘트를 다시 읽은 뒤 저장해주세요. 새로고침 전에 초안을 보존하세요."
                            },
                        )
                        return
                    if revision != '"' + file_hash(workspace / "comments.json") + '"':
                        self.reply(
                            409,
                            {
                                "error": "다른 창에서 코멘트가 변경되었습니다. 초안을 유지한 채 새로 읽기를 눌러주세요.",
                                "conflict": True,
                            },
                        )
                        return
                    write_json(workspace / "comments.json", payload)
                    self.reply(
                        200,
                        {
                            "saved": True,
                            "comments_revision": file_hash(workspace / "comments.json"),
                        },
                    )
                elif self.path == "/api/apply-text":
                    revision = self.headers.get("If-Match")
                    if revision is None:
                        self.reply(
                            428,
                            {
                                "error": "적용 전에 최신 승인된 수정안을 다시 읽어주세요."
                            },
                        )
                        return
                    if (
                        revision
                        != '"' + file_revision(workspace / "proposal.json") + '"'
                    ):
                        self.reply(
                            409,
                            {
                                "error": "적용할 수정안이 바뀌었습니다. 새로 읽고 다시 검토하세요.",
                                "conflict": True,
                            },
                        )
                        return
                    self.reply(202, controller.start(payload, revision))
                elif self.path in {
                    "/api/workflow/note",
                    "/api/workflow/evidence",
                    "/api/workflow/visual-review",
                }:
                    import importlib

                    workflow = importlib.import_module("hwp_workflow")
                    saved = workflow.read(workspace, current)
                    revision = self.headers.get("If-Match")
                    if revision is None:
                        self.reply(428, {"error": "작업 기록을 먼저 다시 읽으세요."})
                        return
                    if revision != '"' + saved["revision"] + '"':
                        self.reply(
                            409,
                            {
                                "error": "작업 기록이 다른 창에서 바뀌었습니다. 자동 재시도하지 않습니다.",
                                "conflict": True,
                            },
                        )
                        return
                    operation = {
                        "/api/workflow/note": workflow.note,
                        "/api/workflow/evidence": workflow.evidence,
                        "/api/workflow/visual-review": workflow.visual_review,
                    }[self.path]
                    self.reply(200, operation(workspace, current, payload, revision))
                elif self.path in {
                    "/api/session",
                    "/api/session-clear",
                    "/api/session-history-delete",
                }:
                    import importlib

                    recovery = importlib.import_module("hwp_review_session")
                    raw = recovery.read_raw(workspace)
                    session_revision = digest(raw) if raw is not None else "absent"
                    revision = self.headers.get("If-Match")
                    if revision is None:
                        self.reply(
                            428,
                            {
                                "error": "복구용 초안을 먼저 읽고 저장하세요. 현재 입력은 보존합니다."
                            },
                        )
                        return
                    if revision != '"' + session_revision + '"':
                        self.reply(
                            409,
                            {
                                "error": "다른 창에서 복구용 초안이 바뀌었습니다. 자동 교체하지 않습니다.",
                                "conflict": True,
                            },
                        )
                        return
                    if self.path == "/api/session-history-delete":
                        self.reply(
                            200, recovery.delete_history(workspace, payload, revision)
                        )
                        return
                    if self.path == "/api/session-clear":
                        if (
                            payload != {"clear_confirmed": True}
                            or payload.get("clear_confirmed") is not True
                        ):
                            raise ValueError(
                                "Separate recovery-clear confirmation required"
                            )
                        result = recovery.clear_snapshot(workspace, revision)
                    else:
                        result = recovery.save_snapshot(
                            workspace, current, payload, revision
                        )
                    self.reply(200, result)
                elif self.path == "/api/apply-contract":
                    revision = self.headers.get("If-Match")
                    if revision is None:
                        self.reply(
                            428,
                            {
                                "error": "Native 계약 수정안을 새로 읽고 별도 적용 범위를 확인하세요."
                            },
                        )
                        return
                    self.reply(202, native_controller.start(payload, revision))
                elif self.path == "/api/text-proposal":
                    from hwp_text_proposal import (
                        compile_text,
                        archive_previous,
                        write_proposal,
                    )

                    path = workspace / "proposal.json"
                    if path.is_symlink() or (
                        hasattr(path, "is_junction") and path.is_junction()
                    ):
                        raise ValueError("연결된 수정안 파일을 바꾸지 않습니다.")
                    if path.exists() and (
                        not path.is_file() or path.stat().st_size > 8 * 1024 * 1024
                    ):
                        raise ValueError(
                            "기존 수정안 파일을 소유자가 확인하세요. 자동 복구하지 않습니다."
                        )
                    previous = path.read_bytes() if path.exists() else None
                    expected_revision = (
                        digest(previous) if previous is not None else "absent"
                    )
                    revision = self.headers.get("If-Match")
                    if revision is None:
                        self.reply(
                            428,
                            {
                                "error": "수정안을 새로 읽고 교체 범위를 확인하세요. 화면 초안을 보존합니다."
                            },
                        )
                        return
                    if revision != '"' + expected_revision + '"':
                        self.reply(
                            409,
                            {
                                "error": "다른 창에서 수정안이 바뀌었습니다. 내 초안을 유지하고 최신안을 확인하세요.",
                                "conflict": True,
                            },
                        )
                        return
                    plan = compile_text(payload, current, workspace / "baseline.hwpx")
                    if previous is not None:
                        try:
                            unchanged = plan_hash(parse_json(previous)) == plan_hash(
                                plan
                            )
                        except (ValueError, TypeError):
                            unchanged = False
                        if unchanged:
                            raise ValueError(
                                "서버와 동일한 수정안입니다. 바뀐 항목을 확인하세요."
                            )
                    archive_previous(workspace, previous)
                    write_proposal(workspace, plan, expected_revision)
                    self.reply(
                        200,
                        {
                            "proposal": plan,
                            "proposal_sha256": plan_hash(plan),
                            "proposal_revision": file_revision(path),
                            "approved": False,
                        },
                    )
                elif self.path == "/api/fill-proposal":
                    revision = self.headers.get("If-Match")
                    if revision is None:
                        self.reply(
                            428,
                            {
                                "error": "수정안을 새로 읽고 저장해주세요. 위치 선택은 화면에 보존됩니다."
                            },
                        )
                        return
                    if (
                        revision
                        != '"' + file_revision(workspace / "proposal-fill.json") + '"'
                    ):
                        self.reply(
                            409,
                            {
                                "error": "다른 창에서 수정안이 변경됐습니다. 현재 위치 선택을 유지한 채 최신안을 확인해주세요.",
                                "conflict": True,
                            },
                        )
                        return
                    data = read_json(workspace / "fill-input.json")
                    if (
                        not isinstance(payload, dict)
                        or set(payload) != {"input_sha256", "placements"}
                        or payload["input_sha256"] != plan_hash(data)
                    ):
                        raise ValueError(
                            "Fill data changed; refresh and review before compiling"
                        )
                    plan = hwp_fill_plan.proposal(
                        workspace,
                        data["bindings"],
                        data["record"],
                        payload["placements"],
                    )
                    write_json(workspace / "proposal-fill.json", plan)
                    self.reply(
                        200, {"proposal_sha256": plan_hash(plan), "approved": False}
                    )
                elif self.path == "/api/approve":
                    kind = (
                        payload.get("proposal_kind", "text")
                        if isinstance(payload, dict)
                        else None
                    )
                    if kind not in ("text", "native", "layout", "fill"):
                        raise ValueError("Unknown proposal kind")
                    plan = read_json(
                        workspace
                        / {
                            "text": "proposal.json",
                            "native": "proposal-native.json",
                            "layout": "proposal-layout.json",
                            "fill": "proposal-fill.json",
                        }[kind]
                    )
                    validate_plan(plan, current, workspace / "baseline.hwpx")
                    expected: dict[str, object] = {"plan_sha256": plan_hash(plan)}
                    if kind == "fill":
                        if plan["schema"] != 4:
                            raise ValueError(
                                "Fill needs the dedicated schema-4 contract"
                            )
                        data = read_json(workspace / "fill-input.json")
                        if (
                            plan["bindings"] != data["bindings"]
                            or plan["record"] != data["record"]
                        ):
                            raise ValueError(
                                "Fill input changed; new proposal required"
                            )
                        expected.update(
                            proposal_kind="fill",
                            fill_policy_confirmed=True,
                            unsigned_template_confirmed=True,
                            header_spine_confirmed=True,
                            fields_reviewed=[p["field_id"] for p in plan["placements"]],
                        )
                    elif kind in ("native", "layout"):
                        if (
                            plan["schema"] != {"native": 2, "layout": 3}[kind]
                            or payload.get("native_policy_confirmed") is not True
                        ):
                            raise ValueError(
                                "Native review requires the exact selected contract and explicit policy confirmation"
                            )
                        expected.update(
                            proposal_kind=kind,
                            native_policy_confirmed=True,
                            regions_reviewed=[e["anchor_id"] for e in plan["edits"]],
                        )
                        if kind == "layout":
                            expected.update(row_minima_confirmed=True)
                    elif plan["schema"] != 1:
                        raise ValueError(
                            "Native policy cannot be approved through text-only review"
                        )
                    if payload != expected or any(
                        payload.get(key) is not True
                        for key, value in expected.items()
                        if value is True
                    ):
                        raise ValueError(
                            "Proposal changed or native regions/policy were not explicitly reviewed"
                        )
                    # Only this human UI action archives/replaces an approval.
                    previous = workspace / "approved-plan.json"
                    if previous.exists():
                        blob = previous.read_bytes()
                        history = (
                            workspace / "approval-history" / (digest(blob) + ".json")
                        )
                        history.parent.mkdir(exist_ok=True)
                        if history.exists():
                            if history.read_bytes() != blob:
                                raise ValueError(
                                    "Existing approval history is inconsistent"
                                )
                        else:
                            with history.open("xb") as stream:
                                stream.write(blob)
                    approval = {
                        "approved_via": "local-review-ui",
                        "approved_at": datetime.now(timezone.utc).isoformat(),
                        "plan_sha256": plan_hash(plan),
                        "plan": plan,
                    }
                    if plan["schema"] in (2, 3, 4):
                        approval["approved_scope"] = native_approval_scope(plan)
                    write_json(workspace / "approved-plan.json", approval)
                    self.reply(
                        200,
                        {
                            "approved": True,
                            "note": "Apply is separate; final visual review still required",
                        },
                    )
                else:
                    self.reply(404, {"error": "Not found"})
            except Exception as exc:
                self.reply(400, {"error": str(exc)})

    class ReviewServer(HTTPServer):
        text_apply_controller = controller
        contract_apply_controller = native_controller
        agent_discovery = agents
        pi_probe_controller = pi_probe
        pi_chat_controller = pi_chat

        def server_close(self):
            controller.require_idle()
            pi_chat.close()
            pi_probe.close()
            agents.close()
            super().server_close()

    server = ReviewServer(("127.0.0.1", port), Handler)
    return server


def create_template_review_server(
    workspace: Path, port: int = 0, *, report_binding: str | None = None
):
    """Enter the existing local review UI's separate pinned draft contract."""
    return create_review_server(
        workspace, port, template_review=True, report_binding=report_binding
    )


def create_report_document_server(root: Path, port: int = 0):
    """Enter document-first authoring through the SAME existing local UI handler."""
    return create_review_server(root, port, template_review=True, document_project=True)


def serve_template_review(
    workspace: Path, port: int, open_browser: bool, *, report_binding: str | None = None
):
    server = create_template_review_server(
        workspace, port, report_binding=report_binding
    )
    url = f"http://127.0.0.1:{server.server_port}"
    print(
        f"Template REVIEW draft: {url}\nNo approval/creation is automatic. Stop with Ctrl+C.",
        flush=True,
    )
    if open_browser:
        webbrowser.open(url)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


def serve(
    workspace: Path, port: int, open_browser: bool, fill_review: bool = False
) -> None:
    server = create_review_server(workspace, port)
    url = f"http://127.0.0.1:{server.server_port}"
    print(
        f"Local review: {url}\nStop with Ctrl+C. No automatic AI call; Pi chat requires explicit profile/send consent.",
        flush=True,
    )
    if open_browser:
        webbrowser.open(url + "/fill" if fill_review else url)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


def main():
    parser = argparse.ArgumentParser()
    sub = parser.add_subparsers(dest="command", required=True)
    p = sub.add_parser("prepare")
    p.add_argument("source", type=Path)
    p.add_argument("workspace", type=Path)
    p = sub.add_parser("serve")
    p.add_argument("workspace", type=Path)
    p.add_argument("--port", type=int, default=8765)
    p.add_argument("--open", action="store_true")
    p.add_argument(
        "--fill",
        action="store_true",
        help="Open the separate unsigned empty-cell review UI",
    )
    p = sub.add_parser("anchors")
    p.add_argument("workspace", type=Path)
    p.add_argument("--page", type=int)
    p.add_argument("--query", default="")
    p = sub.add_parser("check-plan")
    p.add_argument("workspace", type=Path)
    p.add_argument("plan", type=Path)
    p = sub.add_parser("apply")
    p.add_argument("workspace", type=Path)
    p.add_argument("plan", type=Path)
    p = sub.add_parser("fill-draft")
    p.add_argument("workspace", type=Path)
    p.add_argument("bindings", type=Path)
    p.add_argument("record", type=Path)
    p = sub.add_parser("template-draft-prepare")
    p.add_argument("profile", type=Path)
    p.add_argument("workspace", type=Path)
    p = sub.add_parser("template-draft-review")
    p.add_argument("workspace", type=Path)
    p.add_argument("--port", type=int, default=8766)
    p.add_argument("--open", action="store_true")
    args = parser.parse_args()
    if args.command == "prepare":
        result = prepare(args.source, args.workspace)
    elif args.command == "serve":
        serve(args.workspace.resolve(), args.port, args.open, args.fill)
        return
    elif args.command == "template-draft-review":
        serve_template_review(args.workspace, args.port, args.open)
        return
    elif args.command == "template-draft-prepare":
        if frozen():
            raise ValueError(
                "Packaged report preparation uses app-owned report projects, not raw template CLI"
            )
        import hwp_template_draft as template

        result = template.prepare(args.profile, args.workspace)
    elif args.command == "fill-draft":
        import hwp_form_draft as f

        verify_baseline(args.workspace)
        require_apply_unlocked(args.workspace)
        bindings, record = f.read_data(args.bindings), f.read_data(args.record)
        _, draft, _ = hwp_fill_plan.replay(
            args.workspace / "baseline.hwpx", bindings, record
        )
        write_json(
            args.workspace / "fill-input.json", {"bindings": bindings, "record": record}
        )
        result = {
            "draft_sha256": draft["draft_sha256"],
            "approval": "not_approved",
            "review_url": "/fill",
        }
    elif args.command == "anchors":
        m = verify_baseline(args.workspace)
        result = [
            a
            for a in m["anchors"]
            if (args.page is None or a["page_hint"] == args.page)
            and args.query.casefold() in a["old_text"].casefold()
        ]
    elif args.command == "check-plan":
        m = verify_baseline(args.workspace)
        selected = validate_plan(
            read_json(args.plan), m, args.workspace / "baseline.hwpx"
        )
        result = {"valid": True, "edits": len(selected), "approval": "not_checked"}
    else:
        result = apply(args.workspace, args.plan)
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure is not None:
            reconfigure(encoding="utf-8")
    try:
        main()
    except Exception as exc:
        print(
            json.dumps({"status": "blocked", "error": str(exc)}, ensure_ascii=False),
            file=sys.stderr,
        )
        raise SystemExit(2)
