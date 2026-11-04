"""Opt-in native integration TEST, not an approval path or a report-writing task.
Uses only temporary document copies. It never creates approved-plan.json or calls apply.
"""
import argparse
import importlib.util
from pathlib import Path
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("hwp_review", ROOT / "scripts/hwp_review.py")
r = importlib.util.module_from_spec(spec)
spec.loader.exec_module(r)


def run(workspace: Path, output: Path):
    manifest = r.verify_baseline(workspace)
    source = Path(manifest["source"])
    anchor = next(a for a in manifest["anchors"]
                  if a["old_text"] == "Development of 5G-A vRAN Research Platform" and a["page_hint"] == 2)
    if output.exists():
        raise FileExistsError("Native test evidence is never overwritten")
    output.mkdir(parents=True)
    evidence = {"test_only_not_deliverable": True, "source_sha256_before": r.file_hash(source), "cases": {}}
    cases = {"small-text": "Development of 5G-A vRAN research Platform",
             "overflow": (anchor["old_text"] + " ") * 80}
    for name, replacement in cases.items():
        edit = {"anchor_id": anchor["anchor_id"], "old_text": anchor["old_text"], "new_text": replacement}
        plan = {"schema": 1, "workspace_id": manifest["workspace_id"],
                "baseline_hwpx_sha256": manifest["baseline_hwpx_sha256"], "edits": [edit]}
        r.validate_plan(plan, manifest)
        with tempfile.TemporaryDirectory(prefix="hwp-test-") as directory:
            temp = Path(directory)
            candidate = temp / "TEST-ONLY.hwpx"
            r.patch_hwpx(workspace / "baseline.hwpx", candidate, [edit])
            r.structural_gate(workspace / "baseline.hwpx", candidate, [edit])
            r.native_export(candidate, temp / "buffer.pdf", hwp=temp / "TEST-ONLY.hwp")
            r.native_export(temp / "TEST-ONLY.hwp", output / (name + "-TEST-ONLY.pdf"))
            pdf = output / (name + "-TEST-ONLY.pdf")
            gate = r.compare_pdfs(workspace / "baseline.pdf", pdf, {2: anchor["boxes"]})
            text_ok = r.rendered_text_gate(workspace / "baseline.pdf", pdf, [edit], [anchor])
            binaries_ok = (r.hwp_audit(temp / "TEST-ONLY.hwp")["embedded_binary_hashes"] ==
                           manifest["source_audit"]["embedded_binary_hashes"])
            evidence["cases"][name] = {"structural_gate": True, "layout_gate": gate,
                                      "rendered_page_text_exact": text_ok, "embedded_binaries_equal": binaries_ok,
                                      "combined_passed": gate["passed"] and text_ok and binaries_ok}
    evidence["source_sha256_after"] = r.file_hash(source)
    evidence["source_unchanged"] = evidence["source_sha256_before"] == evidence["source_sha256_after"]
    evidence["test_passed"] = (evidence["cases"]["small-text"]["combined_passed"] and
                               not evidence["cases"]["overflow"]["combined_passed"] and evidence["source_unchanged"])
    r.write_json(output / "native-test-verification.json", evidence)
    print(__import__("json").dumps(evidence, ensure_ascii=False, indent=2))
    if not evidence["test_passed"]:
        raise SystemExit(1)


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser()
    parser.add_argument("workspace", type=Path)
    parser.add_argument("output", type=Path)
    parser.add_argument("--run-native-test", action="store_true", required=True)
    args = parser.parse_args()
    run(args.workspace.resolve(), args.output.resolve())
