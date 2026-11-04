"""Schema3 read-only minima bindings: TEMP synthetic XML + fake Native APIs only."""

from copy import deepcopy
import importlib
import unittest
import xml.etree.ElementTree as ET
import zipfile

import test_native_cells as blank_fixture
import test_hwp_fixed_layout as layout_fixture

m = importlib.import_module("hwp_native_cells")
r = layout_fixture.r
fixed = r.hwp_fixed_layout


class MinimaCellBindingTests(unittest.TestCase):
    def setUp(self):
        self.native = blank_fixture.NativeCellBindingTests()
        self.native.setUp()
        self.addCleanup(self.native.doCleanups)
        layout = layout_fixture.FixedLayoutTests()
        layout.setUp()
        self.addCleanup(layout.doCleanups)
        self.baseline = layout.base
        self.manifest = deepcopy(layout.manifest)
        parts = r.archive(self.baseline)
        root = r.xml_root(parts["Contents/section0.xml"])
        for col, cell in enumerate(root.iter(r.HP + "tc")):
            cell.find(r.HP + "cellAddr").attrib = {"rowAddr": "0", "colAddr": str(col)}
        parts["Contents/section0.xml"] = ET.tostring(root)
        self.rebuild(parts)
        self.native.positions = self.native.positions[:2]
        self.native.texts = ["Unique synthetic slot", "Protected synthetic label"]
        self.app = self.native.app

    def rebuild(self, parts):
        with zipfile.ZipFile(self.baseline, "w") as package:
            for name, data in parts.items():
                package.writestr(name, data)
        anchors, _ = r.index_hwpx(self.baseline)
        for anchor in anchors:
            anchor.update(page_hint=1, boxes=[[10, 10, 90, 20]])
        self.manifest.update(
            baseline_hwpx_sha256=r.file_hash(self.baseline), anchors=anchors
        )
        old = {
            "schema": 1,
            "workspace_id": self.manifest["workspace_id"],
            "baseline_hwpx_sha256": r.file_hash(self.baseline),
            "edits": [
                {
                    "anchor_id": anchors[0]["anchor_id"],
                    "old_text": anchors[0]["old_text"],
                    "new_text": "short",
                }
            ],
        }
        self.plan = fixed.proposal(
            r.hwp_native_plan.proposal(old, self.manifest, self.baseline),
            self.manifest,
            self.baseline,
        )

    def capture(self, plan=None):
        return m.capture_minima(
            self.app,
            self.native.source,
            r.file_hash(self.native.source),
            self.baseline,
            self.plan if plan is None else plan,
            self.manifest,
        )

    def assert_no_native_methods(self):
        for method in (
            self.app.GetTextFile,
            self.app.SetPos,
            self.app.Run,
            self.app.InitScan,
            self.app.SaveAs,
            self.app.Clear,
            self.app.Quit,
        ):
            method.assert_not_called()

    def test_distinct_profile_binds_every_nonempty_cell_and_original_minimum(self):
        profile = m.minima_profile(self.baseline, self.plan, self.manifest)
        self.assertEqual(profile["policy"], m.MINIMA_PROFILE)
        self.assertEqual(profile["table_id"], "synthetic-table")
        self.assertEqual(profile["plan_sha256"], r.plan_hash(self.plan))
        self.assertEqual(
            profile["targets"],
            [{"row": 0, "col": 0, "height": 300}, {"row": 0, "col": 1, "height": 300}],
        )
        self.assertEqual(
            profile, m.minima_profile(self.baseline, self.plan, self.manifest)
        )
        with self.assertRaises(ValueError):
            m.plain_profile(self.baseline, [{"field_id": "not-an-empty-field"}])
        self.assert_no_native_methods()

    def test_capture_verifies_all_cells_restores_caret_and_never_grants_writer(self):
        original = self.baseline.read_bytes(), self.native.source.read_bytes()
        result = self.capture()
        self.assertEqual(result["schema"], "hwp-native-original-minima-binding/v1")
        self.assertEqual(result["policy"], m.MINIMA_PROFILE)
        self.assertEqual(result["plan_sha256"], r.plan_hash(self.plan))
        self.assertEqual(
            [item["position"] for item in result["targets"]], self.native.positions
        )
        self.assertEqual([item["height"] for item in result["targets"]], [300, 300])
        self.assertFalse(result["writer_qualified"])
        self.assertTrue(result["original_caret_restored"])
        self.assertEqual(list(self.app.GetPos()), [0, 0, 0])
        self.assertEqual(
            original, (self.baseline.read_bytes(), self.native.source.read_bytes())
        )
        for method in (self.app.SaveAs, self.app.Clear, self.app.Quit):
            method.assert_not_called()

    def test_invalid_plan_or_minimum_rejects_before_native(self):
        variants = []
        for key, value in (
            ("schema", 4),
            ("schema", True),
            ("native_policy", "foreign"),
            ("baseline_hwpx_sha256", "0" * 64),
        ):
            plan = deepcopy(self.plan)
            plan[key] = value
            variants.append(plan)
        for value in (301, True, 300.0):
            plan = deepcopy(self.plan)
            plan["row_minima"][0]["row_heights"][0] = value
            variants.append(plan)
        plan = deepcopy(self.plan)
        plan["row_minima"][0]["table_id"] = "foreign"
        variants.append(plan)
        for plan in variants:
            with self.subTest(plan=plan), self.assertRaises(ValueError):
                self.capture(plan)
        self.assert_no_native_methods()

    def test_no_or_unknown_spine_rejects_before_native(self):
        original = r.archive(self.baseline)
        for value in (b"no", b"unknown", b"YES"):
            parts = dict(original)
            parts["Contents/content.hpf"] = parts["Contents/content.hpf"].replace(
                b'linear="yes"', b'linear="' + value + b'"'
            )
            self.rebuild(parts)
            with self.assertRaisesRegex(ValueError, "Reading-order"):
                self.capture()
        self.assert_no_native_methods()

    def test_missing_or_reordered_coordinates_do_not_relax_profile(self):
        original = r.archive(self.baseline)
        for operation in ("missing", "reordered"):
            parts = dict(original)
            root = r.xml_root(parts["Contents/section0.xml"])
            cell = next(root.iter(r.HP + "tc"))
            address = cell.find(r.HP + "cellAddr")
            if operation == "missing":
                cell.remove(address)
            else:
                address.set("colAddr", "1")
            parts["Contents/section0.xml"] = ET.tostring(root)
            self.rebuild(parts)
            with self.assertRaisesRegex(ValueError, "coordinate"):
                self.capture()
        self.assert_no_native_methods()

    def test_contract_changed_during_scan_never_returns_minima_proof(self):
        def changed(*args):
            self.plan["edits"][0]["new_text"] = "other"
            return self.app.buffer

        self.app.GetTextFile.side_effect = changed
        with self.assertRaisesRegex(ValueError, "contract changed"):
            self.capture()
        self.assertEqual(list(self.app.GetPos()), [0, 0, 0])
        self.app.SaveAs.assert_not_called()

    def test_modified_source_document_rejects_without_scan_or_proof(self):
        self.native.doc.Modified = True
        with self.assertRaisesRegex(ValueError, "identity"):
            self.capture()
        self.app.InitScan.assert_not_called()
        self.app.SaveAs.assert_not_called()

    def test_wrong_nonempty_cell_text_never_produces_binding(self):
        self.native.texts[1] = "FOREIGN CELL"
        with self.assertRaisesRegex(ValueError, "cell text"):
            self.capture()
        self.assertEqual(list(self.app.GetPos()), [0, 0, 0])
        self.app.SaveAs.assert_not_called()


if __name__ == "__main__":
    unittest.main()
