"""Read-only empty-cell mapping and source-bound fill drafts; NOT an apply API.
Original implementation informed by the references in THIRD_PARTY_REFERENCES.md.
No COM, template mutation, approval writer, cache clearing or automatic fitting.
"""
from __future__ import annotations

import argparse
from collections import defaultdict
from datetime import date
import hashlib
import html
import json
from pathlib import Path
import re
import sys
import zipfile

import hwp_review as r

MAP_SCHEMA = "hwp-template-map/v1"
DRAFT_SCHEMA = "hwp-fill-draft/v1"
KINDS = {"text", "multiline", "date", "integer", "decimal", "checkbox"}
SECTION = re.compile(r"Contents/section[0-9]+\.xml\Z")


def canonical_hash(value: dict) -> str:
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True,
                                   separators=(",", ":"), allow_nan=False).encode()).hexdigest()


def read_data(path: Path) -> dict:
    """Reject duplicate keys/NaN rather than silently changing a user's mapping."""
    def pairs(items):
        result = {}
        for key, value in items:
            if key in result:
                raise ValueError("Duplicate JSON key: " + key)
            result[key] = value
        return result
    def invalid(value):
        raise ValueError("Non-JSON number: " + value)
    with path.open("rb") as stream:
        raw = stream.read(4 * 1024 * 1024 + 1)
    if len(raw) > 4 * 1024 * 1024:
        raise ValueError("Draft input exceeds 4 MiB")
    result = json.loads(raw, object_pairs_hook=pairs, parse_constant=invalid)
    if not isinstance(result, dict):
        raise ValueError("Expected a JSON object")
    return result


def _cell_text(cell) -> str:
    # Only the cell's own paragraphs; nested table contents are separate targets.
    return "\n".join("".join(t.text or "" for run in p.findall(r.HP + "run")
                            for t in run.findall(r.HP + "t"))
                     for p in cell.findall(r.HP + "subList/" + r.HP + "p"))


def inspect_template(source: Path) -> dict:
    """Addresses are child-index paths + source hash, NOT repeated paragraph IDs."""
    before = r.file_hash(source)
    parts = r.archive(source)
    if parts.get("mimetype", b"").strip() != b"application/hwp+zip":
        raise ValueError("Expected an HWPX package, not a renamed HWP")
    fields = []
    sections = sorted(name for name in parts if SECTION.fullmatch(name))
    if not sections:
        raise ValueError("No HWPX sections")
    for part in sections:
        raw = parts[part]
        # UTF-8 only: don't let UTF-16 hide DTD/entity syntax from the XML guard.
        raw.decode("utf-8-sig", errors="strict")
        root = r.xml_root(raw)
        if root.tag != "{http://www.hancom.co.kr/hwpml/2011/section}sec":
            raise ValueError("Unexpected section root/namespace")
        texts = {tuple(a["path"]): a for a in r.text_nodes(part, raw)}
        cells = []
        def walk(node, path, table_path=None):
            if node.tag == r.HP + "tbl":
                table_path = path
            if node.tag == r.HP + "tc":
                cells.append((node, path, table_path))
            for index, child in enumerate(node):
                walk(child, path + [index], table_path)
        walk(root, [])
        for cell, path, table_path in cells:
            issues = []
            addr, span = cell.find(r.HP + "cellAddr"), cell.find(r.HP + "cellSpan")
            address = dict(addr.attrib) if addr is not None else {}
            spans = dict(span.attrib) if span is not None else {}
            if (table_path is None or set(address) != {"rowAddr", "colAddr"}
                    or any(not re.fullmatch(r"[0-9]+", v) for v in address.values())):
                issues.append("missing_or_invalid_cell_address")
            if spans != {"rowSpan": "1", "colSpan": "1"}:
                issues.append("merged_or_unknown_span_needs_separate_contract")
            text = _cell_text(cell)
            if text:
                issues.append("not_empty")
            subs = cell.findall(r.HP + "subList")
            paragraphs = subs[0].findall(r.HP + "p") if len(subs) == 1 else []
            runs = paragraphs[0].findall(r.HP + "run") if len(paragraphs) == 1 else []
            empty_run = len(runs) == 1 and not len(runs[0]) and not (runs[0].text or "").strip()
            plain_paragraph = (len(paragraphs) == 1 and not (paragraphs[0].text or "").strip()
                               and all(child.tag in (r.HP + "run", r.HP + "linesegarray") for child in paragraphs[0]))
            simple = (len(subs) == len(paragraphs) == len(runs) == 1 and plain_paragraph
                      and len(list(subs[0])) == 1 and (empty_run or (len(runs[0]) == 1
                      and runs[0][0].tag == r.HP + "t" and len(runs[0][0]) == 0
                      and not (runs[0].text or "").strip() and not (runs[0][0].tail or "").strip())))
            # Paragraph properties/cache are fine; all inline controls are blocked.
            if not simple or any(n.tag not in {r.HP + "run", r.HP + "linesegarray"}
                                 for p in paragraphs for n in p):
                issues.append("not_single_plain_text_paragraph")
            matches = [a for p, a in texts.items() if p[:len(path)] == tuple(path)]
            anchor = matches[0] if len(matches) == 1 and matches[0]["simple"] else None
            if (anchor is None and not empty_run) or (anchor is not None and anchor["old_text"]):
                issues.append("no_exact_empty_text_anchor")
            fields.append({
                "field_id": "f-" + canonical_hash({"part": part, "path": path})[:24],
                "part": part, "cell_path": path, "table_path": table_path,
                "cell_address": address, "cell_span": spans,
                "expected_text": text, "anchor_id": anchor["anchor_id"] if anchor else None,
                "paragraph_style": paragraphs[0].get("paraPrIDRef") if len(paragraphs) == 1 else None,
                "character_style": runs[0].get("charPrIDRef") if len(runs) == 1 else None,
                "insertion_kind": "empty_run_add_text" if empty_run else "existing_empty_text",
                "label_hints": [], "supported": not issues, "issues": sorted(set(issues)),
            })
        # Label suggestions only; never treat adjacency as approval or guess a target.
        part_fields = [f for f in fields if f["part"] == part]
        coordinates = defaultdict(list)
        for field in part_fields:
            a = field["cell_address"]
            if set(a) == {"rowAddr", "colAddr"} and all(v.isdigit() for v in a.values()):
                coordinates[(tuple(field["table_path"] or []), int(a["rowAddr"]), int(a["colAddr"]))].append(field)
        for group in coordinates.values():
            if len(group) > 1:
                for field in group:
                    field["issues"].append("ambiguous_cell_coordinate")
                    field["supported"] = False
        for field in part_fields:
            a = field["cell_address"]
            if set(a) != {"rowAddr", "colAddr"} or not all(v.isdigit() for v in a.values()):
                continue
            row, col = int(a["rowAddr"]), int(a["colAddr"])
            for other in part_fields:
                b = other["cell_address"]
                if other["table_path"] != field["table_path"] or not other["expected_text"]:
                    continue
                if b in ({"rowAddr": str(row), "colAddr": str(col - 1)},
                         {"rowAddr": str(row - 1), "colAddr": str(col)}):
                    field["label_hints"].append(other["expected_text"])
    if r.file_hash(source) != before:
        raise ValueError("Source changed during inspection")
    return {"schema": MAP_SCHEMA, "source_sha256": before,
            "render_mapping": "not_performed", "fields": fields}


def _bindings(bindings: dict, mapped: dict) -> list[dict]:
    if set(bindings) != {"schema", "fields"} or bindings["schema"] != "hwp-fill-bindings/v1":
        raise ValueError("Invalid binding schema")
    fields = bindings["fields"]
    if not isinstance(fields, list) or not 0 < len(fields) <= 1000:
        raise ValueError("Expected 1..1000 explicit field bindings")
    indexed = {f["field_id"]: f for f in mapped["fields"]}
    occupied = set()
    for item in fields:
        if not isinstance(item, dict) or set(item) != {"field_id", "key", "kind", "required", "max_utf16_units", "max_lines"}:
            raise ValueError("Unknown/missing binding properties")
        identifier, key = item["field_id"], item["key"]
        if not isinstance(identifier, str) or identifier not in indexed or identifier in occupied:
            raise ValueError("Unknown/duplicate target field")
        occupied.add(identifier)
        if not indexed[identifier]["supported"]:
            raise ValueError("Unsupported or nonempty cell: " + identifier)
        if not isinstance(key, str) or not key.strip() or len(key) > 120:
            raise ValueError("An explicit record key is required")
        if not isinstance(item["kind"], str) or item["kind"] not in KINDS or type(item["required"]) is not bool:
            raise ValueError("Invalid field type/required flag")
        for name, maximum in [("max_utf16_units", 20000), ("max_lines", 200)]:
            if type(item[name]) is not int or not 1 <= item[name] <= maximum:
                raise ValueError("Invalid budget: " + name)
        if item["kind"] != "multiline" and item["max_lines"] != 1:
            raise ValueError("Only multiline fields allow multiple lines")
    return fields


def _value_issues(value, evidence, binding: dict) -> list[str]:
    if value is None:
        return ["required_value_missing"] if binding["required"] else []
    if not isinstance(value, str):
        return ["value_must_be_string"]
    issues = []
    if not value.strip() and binding["required"]:
        issues.append("required_value_empty")
    if not isinstance(evidence, str) or not evidence.strip() or len(evidence) > 2000:
        issues.append("source_required")
    try:
        units = len(value.encode("utf-16-le")) // 2
    except UnicodeEncodeError as exc:
        raise ValueError("Invalid Unicode cannot be serialized into a draft") from exc
    if units > binding["max_utf16_units"] or value.count("\n") + 1 > binding["max_lines"]:
        issues.append("content_budget_exceeded")
    if any(ord(c) < 32 and c != "\n" for c in value) or any(ord(c) in (0xFFFE, 0xFFFF) for c in value):
        issues.append("unsupported_control_character")
    kind = binding["kind"]
    if kind == "date":
        try:
            if not re.fullmatch(r"[0-9]{4}-[0-9]{2}-[0-9]{2}", value):
                raise ValueError("date")
            date.fromisoformat(value)
        except ValueError:
            issues.append("invalid_date")
    if kind in ("integer", "decimal") and not re.fullmatch(
            r"-?(0|[1-9][0-9]*)" + (r"(\.[0-9]+)?" if kind == "decimal" else ""), value):
        issues.append("invalid_number")
    if kind == "checkbox" and value not in ("checked", "unchecked"):
        issues.append("invalid_checkbox")
    return issues


def draft(source: Path, mapped: dict, bindings: dict, record: dict) -> dict:
    # Recompute the ENTIRE map, not just a caller-provided source hash/supported flag.
    if canonical_hash(mapped) != canonical_hash(inspect_template(source)):
        raise ValueError("Stale or tampered template map")
    fields = _bindings(bindings, mapped)
    if set(record) != {"values", "sources"} or not all(isinstance(record[k], dict) for k in record):
        raise ValueError("Record must contain only values/sources objects")
    grouped = defaultdict(list)
    for item in fields:
        grouped[item["key"]].append(item)
    if (set(record["values"]) | set(record["sources"])) - set(grouped):
        raise ValueError("Unknown record/source key; nothing is silently ignored")
    assignments, issues = [], []
    for key, group in grouped.items():
        value, evidence = record["values"].get(key), record["sources"].get(key)
        if len(group) > 1:
            if value is None:
                values, sources = [None] * len(group), [None] * len(group)
            elif (not isinstance(value, list) or not isinstance(evidence, list)
                  or len(value) != len(group) or len(evidence) != len(group)):
                raise ValueError("Repeated fields need exact value/source arrays: " + key)
            else:
                values, sources = value, evidence
        else:
            values, sources = [value], [evidence]
        for binding, new, origin in zip(group, values, sources):
            problems = _value_issues(new, origin, binding)
            row = {**binding, "expected_text": "", "value": new, "source": origin,
                   "issues": problems, "status": "blocked" if problems else "unfilled" if new is None else "needs_review"}
            assignments.append(row)
            issues.extend({"field_id": binding["field_id"], "code": p} for p in problems)
    result = {"schema": DRAFT_SCHEMA, "source_sha256": mapped["source_sha256"],
              "template_map_sha256": canonical_hash(mapped), "bindings_sha256": canonical_hash(bindings),
              "status": "blocked" if issues else "needs_review", "approval_status": "not_approved",
              "execution_enabled": False, "native_verification": "not_run",
              "layout_budget_verified": False, "assignments": assignments, "issues": issues}
    result["draft_sha256"] = canonical_hash(result)
    return result


def review_html(value: dict) -> str:
    """Static, escaped, no JavaScript/approval controls; local review only."""
    rows = []
    for row in value["assignments"]:
        cols = [row["key"], row["field_id"], row["expected_text"], row["value"], row["source"],
                ", ".join(row["issues"]) or row["status"]]
        rows.append("<tr>" + "".join("<td>" + html.escape("" if v is None else str(v)) + "</td>" for v in cols) + "</tr>")
    return ('<!doctype html><html lang="ko"><meta charset="utf-8">'
            '<meta http-equiv="Content-Security-Policy" content="default-src \'none\'; style-src \'unsafe-inline\'">'
            '<title>빈칸 입력 초안 — 미승인</title><style>body{font:16px sans-serif;padding:24px}table{border-collapse:collapse;width:100%}'
            'td,th{border:1px solid #aaa;padding:8px;white-space:pre-wrap;overflow-wrap:anywhere}</style>'
            '<h1>빈칸 입력 초안 — 미승인 / 문서 적용 안 됨</h1>'
            '<p>출처는 사용자가 제시한 참조이며 진위를 자동 확인한 것이 아닙니다. 문자·줄 수 예산은 실제 양식 맞춤 검증이 아닙니다.</p>'
            '<p>새 승인 UI와 한글 재개방 검증이 연결될 때까지 실행 불가합니다.</p>'
            '<table><thead><tr><th>항목</th><th>칸 ID</th><th>현재 값</th><th>입력값</th><th>근거</th><th>상태</th></tr></thead><tbody>'
            + "".join(rows) + '</tbody></table></html>')


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    p = sub.add_parser("map")
    p.add_argument("source", type=Path);p.add_argument("output", type=Path)
    p = sub.add_parser("draft")
    for name in ("source", "map", "bindings", "record", "output"):
        p.add_argument(name, type=Path)
    args = parser.parse_args()
    mapped = inspect_template(args.source)
    value = None
    if args.command == "draft":
        value = draft(args.source, read_data(args.map), read_data(args.bindings), read_data(args.record))
    # All validation BEFORE creating output; never overwrite any existing directory/file.
    args.output.mkdir(parents=True, exist_ok=False)
    if value is None:
        r.write_json(args.output / "template-map.json", mapped)
        print(json.dumps({"supported_empty_cells": sum(f["supported"] for f in mapped["fields"]),
                          "map": str(args.output / "template-map.json"), "execution_enabled": False}))
    else:
        r.write_json(args.output / "fill-draft.json", value)
        (args.output / "review.html").write_text(review_html(value), encoding="utf-8")
        print(json.dumps({"status": value["status"], "execution_enabled": False,
                          "review": str(args.output / "review.html")}, ensure_ascii=False))
        if value["issues"]:
            raise SystemExit(1)


if __name__ == "__main__":
    try:
        main()
    except (ValueError, OSError, zipfile.BadZipFile, r.ET.ParseError) as exc:
        print(json.dumps({"status": "blocked", "execution_enabled": False, "error": str(exc)},
                         ensure_ascii=True), file=sys.stderr)
        raise SystemExit(1) from None
