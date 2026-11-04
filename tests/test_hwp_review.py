from copy import deepcopy
import importlib.util
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
import zipfile

import fitz

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("hwp_review", ROOT / "scripts/hwp_review.py")
r = importlib.util.module_from_spec(spec)
spec.loader.exec_module(r)

XML = b'''<?xml version="1.0" encoding="UTF-8"?>
<hs:sec xmlns:hs="http://www.hancom.co.kr/hwpml/2011/section" xmlns:hp="http://www.hancom.co.kr/hwpml/2011/paragraph">
<hp:p id="2147483648" paraPrIDRef="1"><hp:run charPrIDRef="2"><hp:t>Original unique phrase</hp:t></hp:run></hp:p>
<hp:p id="2147483648" paraPrIDRef="1"><hp:run charPrIDRef="2"><hp:t>Other protected phrase</hp:t><hp:t/></hp:run></hp:p>
</hs:sec>'''


def make_hwpx(path, xml=XML, image=b"locked-image", header=b"styles"):
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("mimetype", b"application/hwp+zip")
        z.writestr("Contents/section0.xml", xml)
        z.writestr("Contents/header.xml", header)
        z.writestr("BinData/image1.png", image)


class Tests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name)
        self.base = self.path / "baseline.hwpx"
        make_hwpx(self.base)
        self.anchors, _ = r.index_hwpx(self.base)
        for a in self.anchors:
            a["page_hint"] = 1
            a["boxes"] = [[10, 10, 180, 40]]
        self.manifest = {"workspace_id": "test-only", "baseline_hwpx_sha256": r.file_hash(self.base),
                         "anchors": self.anchors, "protected_pages": [], "pages": [{"page": 1}]}
        self.plan = {"schema": 1, "workspace_id": "test-only",
                     "baseline_hwpx_sha256": r.file_hash(self.base), "edits": [
                         {"anchor_id": self.anchors[0]["anchor_id"], "old_text": self.anchors[0]["old_text"],
                          "new_text": "Updated unique phrase"}]}

    def changed(self, xml=XML, image=b"locked-image", header=b"styles"):
        target = self.path / "changed.hwpx"
        make_hwpx(target, xml, image, header)
        return target

    def test_paragraph_ids_are_not_unique_but_anchors_are(self):
        self.assertEqual(self.anchors[0]["paragraph_id"], self.anchors[1]["paragraph_id"])
        self.assertEqual(len(set(a["anchor_id"] for a in self.anchors)), 3)

    def test_plain_text_patch_preserves_namespace_styles_and_assets(self):
        target = self.path / "candidate.hwpx"
        r.patch_hwpx(self.base, target, self.plan["edits"])
        r.structural_gate(self.base, target, self.plan["edits"])
        a, b = r.archive(self.base), r.archive(target)
        self.assertEqual(a["BinData/image1.png"], b["BinData/image1.png"])
        self.assertEqual(b["Contents/section0.xml"], XML.replace(b"Original unique phrase", b"Updated unique phrase"))

    def test_xml_escaping_and_korean_text(self):
        self.plan["edits"][0]["new_text"] = "한글 <확인> & 연구"
        target = self.path / "candidate.hwpx"
        r.patch_hwpx(self.base, target, self.plan["edits"])
        r.structural_gate(self.base, target, self.plan["edits"])
        self.assertEqual(r.index_hwpx(target)[0][0]["old_text"], "한글 <확인> & 연구")

    def cached_fixture(self, position=16, inline=b""):
        before = self.path / "cached.hwpx"
        xml = XML.replace(b"</hp:run></hp:p>", inline + b'</hp:run><hp:linesegarray><hp:lineseg textpos="0"/>'
                          + f'<hp:lineseg textpos="{position}"/></hp:linesegarray></hp:p>'.encode(), 1)
        make_hwpx(before, xml)
        anchor = r.index_hwpx(before)[0][0]
        edit = {"anchor_id": anchor["anchor_id"], "old_text": anchor["old_text"], "new_text": "short"}
        return before, edit

    def test_shortening_beyond_cached_line_position_is_rejected_before_write(self):
        before, edit = self.cached_fixture()
        original = before.read_bytes()
        target = self.path / "invalid-candidate.hwpx"
        with self.assertRaisesRegex(ValueError, "textpos 16 exceeds new UTF-16 length 5"):
            r.patch_hwpx(before, target, [edit])
        self.assertFalse(target.exists())
        self.assertEqual(before.read_bytes(), original)

    def test_cache_position_equal_to_new_length_is_allowed_and_unchanged(self):
        before, edit = self.cached_fixture()
        edit["new_text"] = "x" * 16
        target = self.path / "valid-candidate.hwpx"
        r.patch_hwpx(before, target, [edit])
        r.structural_gate(before, target, [edit])
        self.assertIn(b'textpos="16"', r.archive(target)["Contents/section0.xml"])

    def test_cache_offsets_use_utf16_not_python_character_count(self):
        before, edit = self.cached_fixture()
        edit["new_text"] = "😀" * 8  # Eight codepoints, sixteen HWP UTF-16 units.
        r.check_text_layout_cache(before, [edit])
        edit["new_text"] = "😀" * 7
        with self.assertRaisesRegex(ValueError, "UTF-16 length 14"):
            r.check_text_layout_cache(before, [edit])

    def test_single_cached_line_can_shorten(self):
        before, edit = self.cached_fixture(position=0)
        target = self.path / "short-valid.hwpx"
        r.patch_hwpx(before, target, [edit])
        r.structural_gate(before, target, [edit])

    def test_cache_check_counts_all_text_runs_in_the_paragraph(self):
        before, edit = self.cached_fixture(inline=b"<hp:t>unchanged remainder</hp:t>")
        r.check_text_layout_cache(before, [edit])

    def test_length_changing_mixed_control_cache_is_blocked(self):
        before, edit = self.cached_fixture(inline=b"<hp:tab/>")
        with self.assertRaisesRegex(ValueError, "mixed/control"):
            r.check_text_layout_cache(before, [edit])
        edit["new_text"] = "x" * len(edit["old_text"])
        r.check_text_layout_cache(before, [edit])

    def test_cache_repair_cannot_bypass_locked_structure(self):
        before, edit = self.cached_fixture()
        xml = r.archive(before)["Contents/section0.xml"].replace(edit["old_text"].encode(), b"short")
        changed = self.changed(xml.replace(b'textpos="16"', b'textpos="5"'))
        with self.assertRaisesRegex(ValueError, "Locked structure"):
            r.structural_gate(before, changed, [edit])

    def test_full_plan_preflight_checks_cached_positions(self):
        before, edit = self.cached_fixture()
        anchor = r.index_hwpx(before)[0][0]
        anchor.update(page_hint=1, boxes=[[10, 10, 180, 40]])
        manifest = {**self.manifest, "anchors": [anchor], "baseline_hwpx_sha256": r.file_hash(before)}
        plan = {**self.plan, "baseline_hwpx_sha256": r.file_hash(before), "edits": [edit]}
        with self.assertRaisesRegex(ValueError, "cached line positions"):
            r.validate_plan(plan, manifest, before)

    def test_failed_native_export_reports_actual_unchanged_source(self):
        source = self.path / "SYNTHETIC-ONLY.hwp"
        source.write_bytes(b"synthetic test source, not an HWP document")
        manifest = {**self.manifest, "source": str(source), "source_audit": {"sha256": r.file_hash(source)}}
        plan_path = self.path / "plan.json"
        r.write_json(plan_path, self.plan)
        r.write_json(self.path / "approved-plan.json", {"approved_via": "local-review-ui",
                     "plan_sha256": r.plan_hash(self.plan), "plan": self.plan})
        with patch.object(r, "verify_baseline", return_value=manifest), \
                patch.object(r, "native_export", side_effect=RuntimeError("synthetic export rejected")):
            with self.assertRaisesRegex(RuntimeError, "synthetic export rejected"):
                r.apply(self.path, plan_path)
        report = r.read_json(next((self.path / "runs").glob("*/verification.json")))
        self.assertEqual(report["status"], "blocked")
        self.assertTrue(report["source_unchanged"])
        self.assertNotIn("layout_gate", report)

    def test_empty_text_node_patch(self):
        edit = {"anchor_id": self.anchors[2]["anchor_id"], "old_text": "", "new_text": "filled"}
        target = self.path / "candidate.hwpx"
        r.patch_hwpx(self.base, target, [edit])
        r.structural_gate(self.base, target, [edit])

    def test_rejects_unapproved_text(self):
        changed = self.changed(XML.replace(b"Other protected phrase", b"unauthorized"))
        with self.assertRaisesRegex(ValueError, "Unapproved text"):
            r.structural_gate(self.base, changed, [])

    def test_rejects_style_change(self):
        changed = self.changed(XML.replace(b'charPrIDRef="2"', b'charPrIDRef="9"'))
        with self.assertRaisesRegex(ValueError, "structure"):
            r.structural_gate(self.base, changed, [])

    def test_rejects_table_geometry_change(self):
        before = self.path / "table.hwpx"
        table = XML.replace(b"</hs:sec>", b'<hp:tbl rowCnt="1" colCnt="2"><hp:sz width="900" height="700"/></hp:tbl></hs:sec>')
        make_hwpx(before, table)
        after = self.changed(table.replace(b'height="700"', b'height="800"'))
        with self.assertRaisesRegex(ValueError, "geometry"):
            r.structural_gate(before, after, [])

    def test_rejects_hidden_xml_changes(self):
        changed = self.changed(XML.replace(b"</hs:sec>", b"<!-- unapproved --></hs:sec>"))
        with self.assertRaisesRegex(ValueError, "Unapproved XML bytes"):
            r.structural_gate(self.base, changed, [])

    def test_rejects_image_and_header_changes(self):
        with self.assertRaisesRegex(ValueError, "Locked asset"):
            r.structural_gate(self.base, self.changed(image=b"tampered"), [])
        with self.assertRaisesRegex(ValueError, "Locked asset"):
            r.structural_gate(self.base, self.changed(header=b"new styles"), [])

    def test_rejects_stale_text(self):
        self.plan["edits"][0]["old_text"] = "stale"
        with self.assertRaisesRegex(ValueError, "Stale"):
            r.validate_plan(self.plan, self.manifest)

    def test_rejects_stale_baseline(self):
        self.plan["baseline_hwpx_sha256"] = "wrong"
        with self.assertRaisesRegex(ValueError, "another baseline"):
            r.validate_plan(self.plan, self.manifest)

    def test_rejects_duplicate_anchor(self):
        self.plan["edits"].append(deepcopy(self.plan["edits"][0]))
        with self.assertRaisesRegex(ValueError, "Duplicate"):
            r.validate_plan(self.plan, self.manifest)

    def test_rejects_unmapped_or_protected_page(self):
        self.anchors[0]["page_hint"] = None
        with self.assertRaisesRegex(ValueError, "Unmapped"):
            r.validate_plan(self.plan, self.manifest)
        self.anchors[0]["page_hint"] = 1
        self.manifest["protected_pages"] = [1]
        with self.assertRaisesRegex(ValueError, "protected"):
            r.validate_plan(self.plan, self.manifest)

    def test_rejects_multiline_or_control_text(self):
        for text in ["two\nlines", "tab\t", "invalid\x00", "surrogate\ud800", "bad\uffff"]:
            self.plan["edits"][0]["new_text"] = text
            with self.assertRaisesRegex(ValueError, "single-line"):
                r.validate_plan(self.plan, self.manifest)

    def test_comments_are_not_approval(self):
        body = {"workspace_id": "test-only", "comments": [
            {"id": "c1", "page": 1, "anchor_id": None, "region": [.2, .3], "comment": "Please improve."}]}
        r.validate_comments(body, self.manifest)
        body["comments"][0]["approved"] = True
        with self.assertRaisesRegex(ValueError, "never approval"):
            r.validate_comments(body, self.manifest)

    def test_apply_requires_separate_exact_approval(self):
        with self.assertRaises(FileNotFoundError):
            r.approved_plan(self.path, self.plan, self.manifest)
        # Artificial test fixture ONLY. No real document approvals are written by tests.
        r.write_json(self.path / "approved-plan.json", {"approved_via": "local-review-ui",
                     "plan_sha256": r.plan_hash(self.plan), "plan": self.plan})
        r.approved_plan(self.path, self.plan, self.manifest)
        changed = deepcopy(self.plan)
        changed["edits"][0]["new_text"] = "Unreviewed proposal"
        with self.assertRaisesRegex(ValueError, "EXACT"):
            r.approved_plan(self.path, changed, self.manifest)

    def test_unsafe_zip_paths_and_entities_are_rejected(self):
        path = self.path / "unsafe.hwpx"
        with zipfile.ZipFile(path, "w") as z:
            z.writestr("../escape.xml", b"x")
        with self.assertRaisesRegex(ValueError, "Unsafe"):
            r.archive(path)
        with self.assertRaisesRegex(ValueError, "DTD"):
            r.xml_root(b'<!DOCTYPE x [<!ENTITY y "bad">]><x>&y;</x>')

    def pdf(self, name, changed=False, outside=False, extra_page=False, wide=False):
        path = self.path / name
        doc = fitz.open()
        p = doc.new_page(width=310 if wide else 300, height=400)
        p.insert_text((25, 25), "Updated" if changed else "Original", fontsize=12)
        p.insert_text((25, 120), "Protected", fontsize=12)
        if outside:
            p.draw_rect(fitz.Rect(250, 250, 270, 270), fill=(0, 0, 0))
        if extra_page:
            doc.new_page(width=300, height=400)
        doc.save(path)
        doc.close()
        return path

    def test_layout_allows_only_target_pixels(self):
        a, b = self.pdf("a.pdf"), self.pdf("b.pdf", changed=True)
        self.assertFalse(r.compare_pdfs(a, b)["passed"])
        self.assertTrue(r.compare_pdfs(a, b, {1: [[20, 5, 110, 32]]})["passed"])

    def test_layout_rejects_outside_changes(self):
        a, b = self.pdf("a.pdf"), self.pdf("b.pdf", changed=True, outside=True)
        self.assertFalse(r.compare_pdfs(a, b, {1: [[20, 5, 110, 32]]})["passed"])

    def test_layout_rejects_pagination_and_size_changes(self):
        a = self.pdf("a.pdf")
        self.assertFalse(r.compare_pdfs(a, self.pdf("b.pdf", extra_page=True))["passed"])
        self.assertFalse(r.compare_pdfs(a, self.pdf("c.pdf", wide=True))["passed"])

    def test_rendered_text_requires_actual_replacement(self):
        a, b = self.pdf("a.pdf"), self.pdf("b.pdf", changed=True)
        edits = [{"old_text": "Original", "new_text": "Updated"}]
        selected = [{"page_hint": 1}]
        self.assertTrue(r.rendered_text_gate(a, b, edits, selected))
        self.assertFalse(r.rendered_text_gate(a, a, edits, selected))
        # A renderer ignoring a deletion must not be reported as successful.
        self.assertFalse(r.rendered_text_gate(a, a, [{"old_text": "Original", "new_text": ""}], selected))

    def test_wrapped_unique_slot_maps_exact_glyph_lines(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "wrapped.pdf"
            with r.fitz.open() as doc:
                page = doc.new_page(width=300, height=200)
                page.insert_text((20, 40), "TOY_SLOT_", fontsize=12)
                page.insert_text((20, 70), "EXACT_PLACEHOLDER", fontsize=12)
                page.insert_text((180, 100), "NEIGHBOR", fontsize=12)
                self.assertEqual(page.search_for("TOY_SLOT_EXACT_PLACEHOLDER"), [])
                doc.save(path)
            anchors = [{"anchor_id": "slot", "old_text": "TOY_SLOT_EXACT_PLACEHOLDER", "simple": True}]
            mapped, _ = r.map_anchors(anchors, path)
            self.assertEqual(mapped[0]["page_hint"], 1)
            self.assertEqual(len(mapped[0]["boxes"]), 2)
            self.assertLess(mapped[0]["boxes"][0][3], 50)
            self.assertGreater(mapped[0]["boxes"][1][1], 50)
            self.assertLess(mapped[0]["boxes"][1][3], 90)

    def test_wrapped_duplicate_slots_stay_unmapped(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "duplicates.pdf"
            with r.fitz.open() as doc:
                page = doc.new_page()
                page.insert_text((20, 40), "TOY_SLOT_\nEXACT_PLACEHOLDER", fontsize=12)
                page.insert_text((20, 140), "TOY_SLOT_\nEXACT_PLACEHOLDER", fontsize=12)
                doc.save(path)
            mapped, _ = r.map_anchors([{"old_text": "TOY_SLOT_EXACT_PLACEHOLDER", "simple": True}], path)
            self.assertIsNone(mapped[0]["page_hint"])
            self.assertEqual(mapped[0]["boxes"], [])

    def test_repeated_text_is_never_auto_mapped(self):
        a = self.pdf("a.pdf")
        anchors = [{"old_text": "Protected", "simple": True}, {"old_text": "Protected", "simple": True}]
        mapped, _ = r.map_anchors(anchors, a)
        self.assertTrue(all(x["page_hint"] is None for x in mapped))


if __name__ == "__main__":
    unittest.main()
