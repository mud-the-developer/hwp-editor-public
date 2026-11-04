"""Offline schema-4 synthetic fixtures; no Hancom, real document or real consent."""

from copy import deepcopy
from pathlib import Path
import sys
import tempfile
import unittest
import zipfile

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import hwp_review as r
import hwp_fill_plan as fill
import hwp_form_draft as f
from test_hwp_form_draft import package


class FillTests(unittest.TestCase):
    plain_native = False

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.w = Path(self.temp.name)
        self.baseline = self.w / "baseline.hwpx"
        package(self.baseline, plain_native=self.plain_native)
        self.pdf(self.w / "baseline.pdf")
        self.source = self.w / "fixture.hwp"
        self.source.write_bytes(b"SYNTHETIC-ONLY-NOT-HWP")
        mapped = f.inspect_template(self.baseline)
        self.targets = [item for item in mapped["fields"] if item["supported"]]
        self.bindings = {
            "schema": "hwp-fill-bindings/v1",
            "fields": [
                {
                    "field_id": item["field_id"],
                    "key": "name",
                    "kind": "text",
                    "required": True,
                    "max_utf16_units": 30,
                    "max_lines": 1,
                }
                for item in self.targets
            ],
        }
        self.record = {
            "values": {"name": ["Value A", "Value B"]},
            "sources": {"name": ["synthetic A", "synthetic B"]},
        }
        self.manifest = {
            "workspace_id": "fill-test-only",
            "source": str(self.source),
            "source_audit": {"sha256": r.file_hash(self.source)},
            "baseline_hwpx_sha256": r.file_hash(self.baseline),
            "baseline_pdf_sha256": r.file_hash(self.w / "baseline.pdf"),
            "roundtrip_passed": True,
            "protected_pages": [],
            "pages": [{"page": 1, "width": 300, "height": 400}],
            "anchors": [],
        }
        r.write_json(self.w / "manifest.json", self.manifest)
        regions = fill.empty_regions(self.w / "baseline.pdf")
        self.assertEqual(len(regions), 2)
        self.placements = [
            {"field_id": item["field_id"], **region}
            for item, region in zip(self.targets, regions)
        ]
        self.plan = fill.proposal(self.w, self.bindings, self.record, self.placements)

    def pdf(self, path, values=None, extra=None, misplaced=False):
        with r.fitz.open() as doc:
            page = doc.new_page(width=300, height=400)
            page.insert_text((20, 30), "SYNTHETIC FIXTURE ONLY")
            for index, y in enumerate([60, 90]):
                for x0, x1 in [(20, 80), (80, 220)]:
                    page.draw_rect(r.fitz.Rect(x0, y, x1, y + 30), color=(0, 0, 0))
                page.insert_text((25, y + 20), "Name " + str(index + 1), fontsize=10)
                if values:
                    page.insert_text(
                        (88 if not misplaced else 23, y + 20),
                        values[index],
                        fontsize=10,
                    )
            if extra:
                page.insert_text((20, 180), extra, fontsize=10)
            doc.save(path)

    def selected(self):
        return fill.validate(self.plan, self.manifest, self.baseline)

    def candidate(self):
        path = self.w / "candidate.hwpx"
        fill.write_input(self.baseline, path, self.selected())
        return path

    def change(self, path, transform, part="Contents/section0.xml"):
        parts = r.archive(path)
        parts[part] = transform(parts[part])
        output = self.w / (
            "changed-" + str(len(list(self.w.glob("changed-*")))) + ".hwpx"
        )
        with zipfile.ZipFile(output, "x") as z:
            for name, raw in parts.items():
                z.writestr(name, raw)
        return output

    def test_exact_replay_regions_and_unsigned_policy_are_new_contract(self):
        self.assertEqual(
            len(r.validate_plan(self.plan, self.manifest, self.baseline)), 2
        )
        self.assertTrue(fill.approval_scope(self.plan)["unsigned_template_confirmed"])
        self.assertNotIn("native_policy_confirmed", fill.approval_scope(self.plan))
        self.assertEqual(self.plan["schema"], 4)

    def test_no_approval_wrong_hash_missing_scope_and_old_scope_are_rejected(self):
        with self.assertRaises(OSError):
            r.approved_plan(self.w, self.plan, self.manifest)
        approval = {
            "approved_via": "local-review-ui",
            "plan_sha256": r.plan_hash(self.plan),
            "plan": self.plan,
        }
        for scope in [
            None,
            {"native_policy_confirmed": True},
            {**fill.approval_scope(self.plan), "unsigned_template_confirmed": False},
        ]:
            approval["approved_scope"] = scope
            r.write_json(self.w / "approved-plan.json", approval)
            with self.assertRaises(ValueError):
                r.approved_plan(self.w, self.plan, self.manifest)
        approval["approved_scope"] = fill.approval_scope(self.plan)
        r.write_json(self.w / "approved-plan.json", approval)
        self.assertEqual(len(r.approved_plan(self.w, self.plan, self.manifest)), 2)
        approval["plan_sha256"] = "0" * 64
        r.write_json(self.w / "approved-plan.json", approval)
        with self.assertRaises(ValueError):
            r.approved_plan(self.w, self.plan, self.manifest)

    def test_changed_input_requires_fresh_proposal_and_approval(self):
        record = deepcopy(self.record)
        record["values"]["name"][0] = "Changed"
        r.write_json(
            self.w / "fill-input.json", {"bindings": self.bindings, "record": record}
        )
        with self.assertRaises(ValueError):
            self.selected()

    def test_whole_page_enlarged_overlapping_unknown_reordered_nan_regions_rejected(
        self,
    ):
        for placements in [
            [
                {
                    "field_id": self.targets[0]["field_id"],
                    "page": 1,
                    "region": [0, 0, 300, 400],
                },
                self.placements[1],
            ],
            [self.placements[1], self.placements[0]],
            [
                self.placements[0],
                {**self.placements[1], "region": self.placements[0]["region"]},
            ],
            [
                {**self.placements[0], "region": [float("nan"), 0, 10, 10]},
                self.placements[1],
            ],
            [{**self.placements[0], "page": True}, self.placements[1]],
        ]:
            plan = {**self.plan, "placements": placements}
            with self.subTest(placements=placements), self.assertRaises(ValueError):
                fill.validate(plan, self.manifest, self.baseline)
        manifest = {**self.manifest, "protected_pages": [1]}
        with self.assertRaises(ValueError):
            fill.validate(self.plan, manifest, self.baseline)

    def test_values_sources_budgets_and_unknown_properties_cannot_change(self):
        plan = deepcopy(self.plan)
        plan["record"]["values"]["name"][0] = "Changed"
        with self.assertRaises(ValueError):
            fill.validate(plan, self.manifest, self.baseline)
        plan = {**self.plan, "approved": True}
        with self.assertRaises(ValueError):
            fill.validate(plan, self.manifest, self.baseline)
        for values in [[], ["one"], ["", "b"], ["a\nb", "b"]]:
            record = {**self.record, "values": {"name": values}}
            with self.assertRaises(ValueError):
                fill.proposal(self.w, self.bindings, record, self.placements)

    def test_input_is_exact_insertion_and_gate_locks_assets_styles_table_and_unselected_cache(
        self,
    ):
        candidate = self.candidate()
        self.assertEqual(
            fill.structural_gate(self.baseline, candidate, self.selected())[
                "exact_empty_cells_filled"
            ],
            2,
        )
        changes = [
            ("Contents/header.xml", lambda _: b"CHANGED-STYLE"),
            ("BinData/locked.png", lambda _: b"CHANGED-IMAGE"),
            (
                "Contents/section0.xml",
                lambda raw: raw.replace(b'charPrIDRef="3"', b'charPrIDRef="4"', 1),
            ),
            (
                "Contents/section0.xml",
                lambda raw: raw.replace(b'rowSpan="1"', b'rowSpan="2"', 1),
            ),
            (
                "Contents/section0.xml",
                lambda raw: raw.replace(b'textpos="0"', b'textpos="1"', 1),
            ),
            ("Contents/section0.xml", lambda raw: raw.replace(b"Value A", b"Value AX")),
        ]
        for part, change in changes:
            with self.subTest(part=part), self.assertRaises(ValueError):
                fill.structural_gate(
                    self.baseline, self.change(candidate, change, part), self.selected()
                )
        with self.assertRaises(FileExistsError):
            fill.write_input(self.baseline, candidate, self.selected())

    def test_actual_empty_run_without_text_node_has_one_declared_new_text_node(self):
        parts = r.archive(self.baseline)
        parts["Contents/section0.xml"] = parts["Contents/section0.xml"].replace(
            b"<hp:t></hp:t>", b""
        )
        path = self.w / "empty-run.hwpx"
        with zipfile.ZipFile(path, "x") as z:
            for name, raw in parts.items():
                z.writestr(name, raw)
        mapped = f.inspect_template(path)
        self.assertEqual(sum(item["supported"] for item in mapped["fields"]), 2)
        self.assertTrue(
            all(
                item["insertion_kind"] == "empty_run_add_text"
                for item in mapped["fields"]
                if item["supported"]
            )
        )

    def test_header_reading_order_is_explicit_exact_and_does_not_unlock_other_metadata(
        self,
    ):
        parts = r.archive(self.baseline)
        parts[
            "Contents/content.hpf"
        ] = b"""<opf:package xmlns:opf="http://www.idpf.org/2007/opf/">
          <opf:metadata><opf:title>LOCKED TITLE</opf:title><opf:meta name="ModifiedDate">2026-10-10T00:00:00Z</opf:meta></opf:metadata>
          <opf:manifest><opf:item id="header" href="Contents/header.xml"/></opf:manifest>
          <opf:spine><opf:itemref idref="header" linear="yes"/><opf:itemref idref="section0" linear="yes"/></opf:spine></opf:package>"""
        base = self.w / "metadata.hwpx"
        with zipfile.ZipFile(base, "x") as z:
            for name, raw in parts.items():
                z.writestr(name, raw)
        declared = fill.header_spine(base)
        self.assertEqual(
            declared,
            {
                "part": "Contents/content.hpf",
                "idref": "header",
                "before": "yes",
                "after": "no",
            },
        )
        target = self.w / "metadata-input.hwpx"
        fill.write_input(base, target, self.selected())
        after = self.change(
            target,
            lambda raw: raw.replace(
                b'idref="header" linear="yes"', b'idref="header" linear="no"'
            ),
            "Contents/content.hpf",
        )
        with self.assertRaises(ValueError):
            fill.structural_gate(base, after, self.selected())
        fill.structural_gate(base, after, self.selected(), declared)
        for old, new in [
            (b"LOCKED TITLE", b"CHANGED TITLE"),
            (b'idref="section0" linear="yes"', b'idref="section0" linear="no"'),
        ]:
            bad = self.change(
                after, lambda raw: raw.replace(old, new), "Contents/content.hpf"
            )
            with self.assertRaises(ValueError):
                fill.structural_gate(base, bad, self.selected(), declared)

    def test_header_permission_is_optional_and_yes_omission_is_equivalent_not_no(self):
        raw = b"""<opf:package xmlns:opf="http://www.idpf.org/2007/opf/">
          <opf:metadata><opf:meta name="ModifiedDate">2026-10-10T00:00:00Z</opf:meta></opf:metadata>
          <opf:manifest><opf:item id="header" href="Contents/header.xml"/></opf:manifest>
          <opf:spine><opf:itemref idref="header"/><opf:itemref idref="section0"/></opf:spine></opf:package>"""
        for original in (None, "yes", "no"):
            parts = r.archive(self.baseline)
            original_bytes = (
                b"" if original is None else (' linear="' + original + '"').encode()
            )
            parts["Contents/content.hpf"] = raw.replace(
                b'idref="header"', b'idref="header"' + original_bytes
            )
            base = self.w / ("header-base-" + str(original) + ".hwpx")
            with zipfile.ZipFile(base, "x") as z:
                for name, data in parts.items():
                    z.writestr(name, data)
            declared = fill.header_spine(base)
            target = self.w / ("header-input-" + str(original) + ".hwpx")
            fill.write_input(base, target, self.selected())
            for actual in (None, "yes", "no", "unknown"):
                actual_bytes = (
                    b"" if actual is None else (' linear="' + actual + '"').encode()
                )
                after = self.change(
                    target,
                    lambda data: data.replace(
                        b'idref="header"' + original_bytes,
                        b'idref="header"' + actual_bytes,
                    ),
                    "Contents/content.hpf",
                )
                rejected = actual == "unknown" or original == "no" and actual != "no"
                with self.subTest(original=original, actual=actual):
                    if rejected:
                        with self.assertRaises(ValueError):
                            fill.structural_gate(base, after, self.selected(), declared)
                    else:
                        result = fill.structural_gate(
                            base, after, self.selected(), declared
                        )
                        self.assertEqual(
                            result["header_spine_transition_applied"],
                            actual == "no" and original != "no",
                        )

    def test_pdf_requires_exact_inside_text_and_unchanged_outside_and_no_missing_fields(
        self,
    ):
        selected = self.selected()
        for name, values, extra, misplaced, expected in [
            ("valid", ["Value A", "Value B"], None, False, True),
            ("extra", ["Value AX", "Value B"], None, False, False),
            ("outside", ["Value A", "Value B"], "unapproved", False, False),
            ("missing", ["Value A", ""], None, False, False),
            ("wrong-cell", ["Value A", "Value B"], None, True, False),
        ]:
            path = self.w / (name + ".pdf")
            self.pdf(path, values, extra, misplaced)
            with self.subTest(name=name):
                self.assertEqual(
                    fill.rendered_text_gate(self.w / "baseline.pdf", path, selected),
                    expected,
                )
        valid = self.w / "valid.pdf"
        masks = {1: [p["region"] for p in self.placements]}
        self.assertTrue(r.compare_pdfs(self.w / "baseline.pdf", valid, masks)["passed"])
