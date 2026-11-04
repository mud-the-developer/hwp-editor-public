"""Explicit authoring lab for the generated, unsigned TOY ONLY fixture.
Offline: exact slot edits + structural gate. Native checks are opt-in and require
human HWP file-access confirmations. Never writes approvals or real work/review.
"""
from __future__ import annotations
import argparse
import importlib.util
import json
import re
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("hwp_review", ROOT / "scripts/hwp_review.py")
r = importlib.util.module_from_spec(spec)
spec.loader.exec_module(r)


def inspect_toy(source_directory: Path) -> tuple[dict, dict, list]:
    directory = source_directory.resolve()
    if ROOT / "work" not in directory.parents or not re.fullmatch(r"toy-source(?:-v\d+)?", directory.name):
        raise ValueError("This opt-in lab accepts only work/toy-source, never the real report")
    metadata = r.read_json(directory / "toy-fixture.json")
    if metadata.get("purpose") != "TOY_ONLY_NOT_DELIVERABLE":
        raise ValueError("Missing synthetic fixture declaration")
    source = (directory / "TOY-ONLY-template.hwp").resolve()
    if source.parent != directory or str(source) != metadata["source"] or r.file_hash(source) != metadata["source_sha256"]:
        raise ValueError("Toy source identity changed")
    hwpx, pdf = directory / "TOY-ONLY-template.hwpx", directory / "TOY-ONLY-template.pdf"
    anchors, objects = r.index_hwpx(hwpx)
    anchors, pages = r.map_anchors(anchors, pdf)
    with r.fitz.open(pdf) as doc:
        if len(doc) != 5 or any(metadata["marker"] not in page.get_text() for page in doc):
            raise ValueError("Not the generated five-page TOY fixture")
    indexed = {a["old_text"]: a for a in anchors if a["simple"] and a["page_hint"] and a["boxes"]}
    selected = []
    for key, (old, new) in metadata["slots"].items():
        if old not in indexed:
            raise ValueError("Unmapped toy slot: " + key)
        selected.append(indexed[old])
    manifest = {"workspace_id": "TOY-ONLY-" + metadata["source_sha256"][:16],
                "baseline_hwpx_sha256": r.file_hash(hwpx), "anchors": anchors,
                "pages": pages, "protected_pages": [p["page"] for p in pages if p["image_only"]]}
    return metadata, manifest, selected


def run(source_directory: Path, output: Path, native: bool = False) -> dict:
    source_directory, output = source_directory.resolve(), output.resolve()
    if output.exists() or ROOT / "work" not in output.parents or not re.fullmatch(r"toy-(?:authoring|native-test)(?:-v\d+)?", output.name):
        raise FileExistsError("Use NEW work/toy-authoring; never overwrite evidence or real files")
    metadata, manifest, selected = inspect_toy(source_directory)
    output.mkdir(parents=True)
    source = source_directory / "TOY-ONLY-template.hwp"
    hwpx, pdf = source_directory / "TOY-ONLY-template.hwpx", source_directory / "TOY-ONLY-template.pdf"
    originals = {p.name: r.file_hash(p) for p in [source, hwpx, pdf]}
    edits = [{"anchor_id": a["anchor_id"], "old_text": old, "new_text": new}
             for a, (old, new) in zip(selected, metadata["slots"].values())]
    plan = {"schema": 1, "workspace_id": manifest["workspace_id"],
            "baseline_hwpx_sha256": manifest["baseline_hwpx_sha256"], "edits": edits}
    r.validate_plan(plan, manifest)
    written = output / "TOY-ONLY-written.hwpx"
    r.patch_hwpx(hwpx, written, edits)
    r.structural_gate(hwpx, written, edits)
    updated, after_objects = r.index_hwpx(written)
    after_text = {a["anchor_id"]: a["old_text"] for a in updated}
    exact = all(after_text[e["anchor_id"]] == e["new_text"] for e in edits)
    _, objects = r.index_hwpx(hwpx)
    assert exact and objects == after_objects
    r.write_json(output / "TOY-ONLY-write-plan.json", plan)
    evidence = {"test_only_not_deliverable": True, "scope": "generated toy fixture ONLY",
                "pages": len(manifest["pages"]), "objects": objects, "written_slots": len(edits),
                "field_pages": {key: a["page_hint"] for key, a in zip(metadata["slots"], selected)},
                "exact_text_changes": exact, "structural_gate": True,
                "images_present": objects["pic"] > 0,
                "native_reopen_and_layout": "NOT_RUN", "native_cases": {},
                "source_unchanged": all(r.file_hash(source_directory / name) == sha for name, sha in originals.items())}
    if native:
        # This is NOT apply and is NOT an approval path. Native PDFs are TEST ONLY.
        # Any blocked/timed-out native operation stops this invocation; no weakened checks/retry.
        try:
            baseline_dir = output / "native-baseline"
            baseline_dir.mkdir()
            r.native_export(source, baseline_dir / "TOY-ONLY-source.pdf", hwpx=baseline_dir / "source.hwpx")
            r.native_export(baseline_dir / "source.hwpx", baseline_dir / "buffer.pdf", hwp=baseline_dir / "source-roundtrip.hwp")
            r.native_export(baseline_dir / "source-roundtrip.hwp", baseline_dir / "TOY-ONLY-reopened.pdf")
            roundtrip = r.compare_pdfs(pdf, baseline_dir / "TOY-ONLY-reopened.pdf")
            if not roundtrip["passed"]:
                raise ValueError("Unmodified toy roundtrip failed; no edits rendered")
            evidence["native_roundtrip"] = roundtrip
            masks = {}
            for a in selected:
                masks.setdefault(a["page_hint"], []).extend(a["boxes"])
            cases = [("written", written, edits, selected, masks)]
            overflow_anchor = next(a for a in selected if a["old_text"] == metadata["slots"]["G01"][0])
            overflow_edit = {"anchor_id": overflow_anchor["anchor_id"], "old_text": overflow_anchor["old_text"],
                             "new_text": "가상 과도한 장문 입력 시험 " * 100}
            overflow = output / "TOY-ONLY-overflow.hwpx"
            overflow_plan = {**plan, "edits": [overflow_edit]}
            r.validate_plan(overflow_plan, manifest)
            r.patch_hwpx(hwpx, overflow, [overflow_edit])
            r.structural_gate(hwpx, overflow, [overflow_edit])
            cases.append(("overflow", overflow, [overflow_edit], [overflow_anchor], {overflow_anchor["page_hint"]: overflow_anchor["boxes"]}))
            for name, candidate, changes, anchors, allowed in cases:
                hwp = output / f"TOY-ONLY-{name}.hwp"
                rendered = output / f"TOY-ONLY-{name}.pdf"
                r.native_export(candidate, output / f"TOY-ONLY-{name}-buffer.pdf", hwp=hwp)
                r.native_export(hwp, rendered)
                layout = r.compare_pdfs(pdf, rendered, allowed)
                text_ok = r.rendered_text_gate(pdf, rendered, changes, anchors)
                binaries = r.hwp_audit(hwp)["embedded_binary_hashes"] == r.hwp_audit(source)["embedded_binary_hashes"]
                passed = layout["passed"] and text_ok and binaries
                evidence["native_cases"][name] = {"layout_gate": layout, "rendered_page_text_exact": text_ok,
                                                   "embedded_binaries_equal": binaries, "combined_passed": passed}
                if name == "written" and not passed:
                    raise ValueError("Toy writing failed strict checks; stop, do not change masks/font/table geometry")
            evidence["native_reopen_and_layout"] = "PASSED" if not evidence["native_cases"]["overflow"]["combined_passed"] else "FAILED"
        except Exception as exc:
            evidence["native_reopen_and_layout"] = "BLOCKED"
            evidence["native_error"] = str(exc)
            evidence["source_unchanged"] = all(r.file_hash(source_directory / name) == sha for name, sha in originals.items())
            r.write_json(output / "TOY-ONLY-authoring-test.json", evidence)
            raise
    evidence["source_unchanged"] = all(r.file_hash(source_directory / name) == sha for name, sha in originals.items())
    r.write_json(output / "TOY-ONLY-authoring-test.json", evidence)
    return evidence


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("source", type=Path)
    parser.add_argument("output", type=Path)
    parser.add_argument("--toy-only", action="store_true", required=True)
    parser.add_argument("--run-native-test", action="store_true")
    args = parser.parse_args()
    result = run(args.source, args.output, args.run_native_test)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    if not result["source_unchanged"] or (args.run_native_test and result["native_reopen_and_layout"] != "PASSED"):
        raise SystemExit(1)
