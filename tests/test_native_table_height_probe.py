"""Height-only calibration helpers: fake COM, no real Native/processes."""

import importlib
from pathlib import Path
import subprocess
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

m = importlib.import_module("native_table_height_probe")


class Params:
    def __init__(self, values, corrupt=False):
        self.values = dict(values)
        self.corrupt = corrupt

    @property
    def SetID(self) -> str:
        return "Cell"

    @property
    def Count(self):
        return len(self.values)

    def ItemExist(self, name):
        return name in self.values

    def Item(self, name):
        return self.values[name]

    def Clone(self):
        return Params(self.values, self.corrupt)

    def SetItem(self, name, value):
        if name != "Height":
            raise AssertionError("Only Height may be assigned")
        self.values[name] = value
        if self.corrupt:
            self.values["Width"] += 1

    def RemoveItem(self, name):
        if name != "Height":
            raise AssertionError("Only comparison-copy Height may be removed")
        self.values.pop(name, None)

    def IsEquivalent(self, other):
        return self.values == other.values


class TableParams(Params):
    @property
    def SetID(self):
        return "Table"

    @property
    def Count(self):
        return 37

    def Clone(self):
        return TableParams(self.values)

    def RemoveItem(self, name):
        pass  # Observed Table Height remained present; synthetic regression only.


class ParentParams(Params):
    def __init__(self, values, corrupt=False):
        super().__init__({"Height": 6728, "Width": 18000})
        self.child = Params(values, corrupt)

    @property
    def SetID(self) -> str:
        return "Table"

    def ItemExist(self, name):
        return name == "Cell" or super().ItemExist(name)

    def Item(self, name):
        return self.child if name == "Cell" else super().Item(name)

    def Clone(self):
        return ParentParams(self.child.values, self.child.corrupt)

    def IsEquivalent(self, other):
        return self.values == other.values and self.child.IsEquivalent(other.child)


class Target:
    def __init__(self, source):
        self.values = {"Height": 282, "Width": 9000, "Border": "SYNTHETIC"}
        self.writes = []
        self.pos = [5, 0, 0]
        self.full = "SYNTHETIC full"
        self.old = "SYNTHETIC CELL"
        self.bad_readback = self.mutate_text = self.corrupt_clone = False
        self.XHwpDocuments = SimpleNamespace(
            Count=1,
            Item=lambda index: SimpleNamespace(
                FullName=str(source), Path=str(source.parent), Modified=False
            ),
        )
        self.PageCount = 1
        self.SelectionMode = 0
        self.InitScan = Mock(side_effect=self.scan)
        self.ReleaseScan = Mock()
        self.GetText = Mock()

    def scan(self, *args):
        self.GetText.side_effect = [(2, self.old + "\r\n"), (0, "")]
        return True

    def SetPos(self, *pos):
        self.pos = list(pos)
        return True

    def GetPos(self):
        return tuple(self.pos)

    def GetTextFile(self, *args):
        return self.full

    @property
    def CellShape(self):
        return ParentParams(self.values, self.corrupt_clone)

    @CellShape.setter
    def CellShape(self, value):
        child = value.Item("Cell")
        self.writes.append(dict(child.values))
        self.values = dict(child.values)
        if self.bad_readback:
            self.values["Height"] += 1
        if self.mutate_text:
            self.full += "CHANGED"


class TableHeightCalibrationTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name).resolve()
        self.source = self.root / "OWNED-NOT-REAL.hwp"
        self.source.write_bytes(b"SYNTHETIC ONLY")
        self.target = Target(self.source)
        self.app = m.NativeReference(self.target)
        self.digest = m.r.file_hash(self.source)
        denied = patch.object(
            subprocess,
            "Popen",
            side_effect=AssertionError("No actual SDK/Native/process"),
        )
        self.launch = denied.start()
        self.addCleanup(denied.stop)

    def setting(self, height: object = 1282):
        self.diagnostic = {}
        return m.set_height(
            self.app,
            self.source,
            self.digest,
            "SYNTHETIC full",
            [5, 0, 0],
            "SYNTHETIC CELL",
            height,
            self.diagnostic,
        )

    def test_probe_uses_the_single_shared_buffer_implementation(self):
        buffer = importlib.import_module("hwp_native_buffer")
        self.assertIs(m.set_height, buffer.set_bound_cell_minimum)
        self.assertIs(m.identity, buffer.source_buffer_identity)
        self.assertIs(m.parameter_snapshot, buffer.parameter_snapshot)
        self.assertIs(m.raw_comparison, buffer.raw_comparison)

    def test_source_byte_drift_blocks_shared_primitive_before_put(self):
        self.source.write_bytes(b"CHANGED SYNTHETIC ONLY")
        with self.assertRaisesRegex(ValueError, "identity"):
            self.setting()
        self.assertEqual(self.target.writes, [])

    def test_only_height_changes_with_exact_readback_and_no_text_or_width_delta(self):
        evidence = self.setting()
        self.assertEqual(
            self.target.writes, [{"Height": 1282, "Width": 9000, "Border": "SYNTHETIC"}]
        )
        self.assertEqual(evidence["height_written_and_read_back"], 1282)
        self.assertTrue(evidence["non_height_parameters_equivalent"])
        self.assertEqual(self.target.full, "SYNTHETIC full")
        self.assertEqual(m.r.file_hash(self.source), self.digest)
        self.launch.assert_not_called()

    def test_bounded_parameter_snapshots_are_primitive_and_preserve_removal_state(self):
        import json

        with patch.object(
            Params, "RemoveItem", side_effect=AssertionError("No removal assumption")
        ):
            self.setting()
        diagnostic = self.diagnostic
        self.assertEqual(
            diagnostic["original"]["Height"], {"exists": True, "value": 282}
        )
        self.assertEqual(diagnostic["changed_copy"]["Height"]["value"], 1282)
        self.assertEqual(diagnostic["original_table"]["SetID"], "Table")
        self.assertEqual(
            diagnostic["normalized_comparison_cell"]["Height"]["value"], 282
        )
        self.assertEqual(
            diagnostic["changed_after_normalization"]["Height"]["value"], 1282
        )
        self.assertEqual(
            diagnostic["original_vs_untouched_clone"],
            {"python_type": "bool", "value": True},
        )
        self.assertTrue(diagnostic["height_put_attempted"])
        self.assertLess(len(json.dumps(diagnostic)), 3000)

    def test_false_raw_results_remain_typed_and_never_authorize_put(self):
        for raw in (False, 0):
            with (
                self.subTest(raw=raw),
                patch.object(Params, "IsEquivalent", return_value=raw),
            ):
                with self.assertRaisesRegex(ValueError, "more than Height"):
                    self.setting()
                self.assertEqual(
                    self.diagnostic["comparison_raw"],
                    {"python_type": type(raw).__name__, "value": raw},
                )
                self.assertEqual(
                    self.diagnostic["original_vs_untouched_clone"]["value"], raw
                )
                self.assertFalse(self.diagnostic["height_put_attempted"])
                self.assertEqual(self.target.writes, [])

    def test_table_set_is_rejected_before_clone_or_parameter_mutation(self):
        table = TableParams({"Height": 6728, "Width": 41954})
        with (
            patch.object(
                Target, "CellShape", new_callable=property, fget=lambda _: table
            ),
            patch.object(
                TableParams,
                "Clone",
                side_effect=AssertionError("Wrong-scope clone must not run"),
            ),
        ):
            with self.assertRaisesRegex(ValueError, "Table.Cell"):
                self.setting()
        self.assertEqual(self.target.writes, [])
        self.assertEqual(self.diagnostic["original_table"]["SetID"], "Table")
        self.assertEqual(self.diagnostic["stage"], "parameter_scope")
        self.assertFalse(self.diagnostic["height_put_attempted"])

    def test_observed_table_remove_noop_keeps_height_and_false_is_not_other_delta_proof(
        self,
    ):
        original = TableParams({"Height": 6728, "Width": 41954})
        changed = original.Clone()
        self.assertTrue(original.IsEquivalent(changed))
        changed.SetItem("Height", 7728)
        original.RemoveItem("Height")
        changed.RemoveItem("Height")
        self.assertTrue(original.ItemExist("Height"))
        self.assertTrue(changed.ItemExist("Height"))
        self.assertEqual(original.Count, 37)
        self.assertEqual(changed.Item("Width"), original.Item("Width"))
        self.assertFalse(original.IsEquivalent(changed))

    def test_original_child_alias_blocks_before_property_put(self):
        def clone(parent):
            result = ParentParams(parent.child.values)
            result.child = parent.child
            return result

        with patch.object(ParentParams, "Clone", clone):
            with self.assertRaisesRegex(ValueError, "aliased original"):
                self.setting()
        self.assertEqual(self.target.writes, [])
        self.assertFalse(self.diagnostic["height_put_attempted"])

    def test_comparison_child_alias_blocks_before_property_put(self):
        calls = []

        def clone(parent):
            calls.append(parent)
            result = ParentParams(parent.child.values)
            if len(calls) > 1:
                result.child = parent.child
            return result

        with patch.object(ParentParams, "Clone", clone):
            with self.assertRaisesRegex(ValueError, "pending child copy"):
                self.setting()
        self.assertEqual(self.target.writes, [])
        self.assertFalse(self.diagnostic["height_put_attempted"])

    def test_cell_block_selection_never_enters_parameter_write(self):
        self.target.SelectionMode = 16
        with patch.object(
            ParentParams, "Clone", side_effect=AssertionError("No clone")
        ):
            with self.assertRaisesRegex(ValueError, "without selection"):
                self.setting()
        self.assertEqual(self.target.writes, [])

    def test_invalid_height_or_reference_does_not_write(self):
        for value in (False, True, 0, -1, 2**31, 1.5, "1282", None):
            with self.subTest(value=value), self.assertRaises(ValueError):
                self.setting(value)
        self.app.target = None
        with self.assertRaises(ValueError):
            self.setting()
        self.assertEqual(self.target.writes, [])

    def test_wrong_cell_text_does_not_write(self):
        self.target.old = "FOREIGN"
        with self.assertRaisesRegex(ValueError, "cell text"):
            self.setting()
        self.assertEqual(self.target.writes, [])
        self.target.ReleaseScan.assert_called_once()

    def test_unknown_document_or_source_bytes_does_not_write(self):
        self.target.XHwpDocuments.Item = lambda index: SimpleNamespace(
            FullName=str(self.root / "FOREIGN.hwp"), Path=str(self.root), Modified=False
        )
        with self.assertRaisesRegex(ValueError, "identity"):
            self.setting()
        self.assertEqual(self.target.writes, [])

    def test_non_height_parameter_change_on_copy_blocks_before_native_setter(self):
        self.target.corrupt_clone = True
        with self.assertRaisesRegex(ValueError, "width|more than Height"):
            self.setting()
        self.assertEqual(self.target.writes, [])

    def test_mismatched_readback_or_text_mutation_never_retries(self):
        for flag in ("bad_readback", "mutate_text"):
            with self.subTest(flag=flag):
                target = Target(self.source)
                setattr(target, flag, True)
                self.app = m.NativeReference(target)
                with self.assertRaises(ValueError):
                    self.setting()
                self.assertEqual(len(target.writes), 1)
        self.launch.assert_not_called()

    def test_missing_optin_and_foreign_output_fail_before_metadata_or_files(self):
        with (
            patch.object(m, "hwp_inventory", side_effect=AssertionError("No metadata")),
            patch.object(
                m.security,
                "registered_security_module",
                side_effect=AssertionError("No module query"),
            ),
        ):
            for kwargs in (
                {},
                {"run_native": True},
                {"run_native": True, "toy_only": 1},
            ):
                with self.subTest(kwargs=kwargs), self.assertRaises(ValueError):
                    m.run(self.root / "not-created", **kwargs)
            with self.assertRaises(ValueError):
                m.run(
                    self.root / "toy-native-height-foreign",
                    run_native=True,
                    toy_only=True,
                )
        self.assertEqual(list(self.root.iterdir()), [self.source])

    def test_security_registration_never_installs_or_changes_registry(self):
        app = SimpleNamespace(RegisterModule=Mock(return_value=True))
        with (
            patch.object(
                m.security,
                "registered_security_module",
                return_value={"auto_installed": False, "registry_updated": False},
            ) as read,
            patch.object(
                m.security,
                "ensure_security_module",
                side_effect=AssertionError("No installer"),
            ) as install,
        ):
            result = m.read_only_security(app)
        self.assertTrue(result["registered"])
        self.assertFalse(result["auto_installed"])
        self.assertFalse(result["registry_updated"])
        read.assert_called_once()
        install.assert_not_called()
        app.RegisterModule.assert_called_once_with(
            m.security.MODULE_TYPE, m.security.MODULE_ALIAS
        )

    def test_pixel_gate_is_exact_not_an_expanded_region(self):
        before, after = self.root / "before.pdf", self.root / "after.pdf"
        for path in (before, after):
            with m.r.fitz.open() as doc:
                doc.new_page(width=200, height=300).insert_text(
                    (20, 40), "SYNTHETIC", fontsize=12
                )
                doc.save(path)
        self.assertTrue(m.pixel_gate(before, after)["all_pixels_and_text_equal"])
        with m.r.fitz.open(after) as doc:
            doc[0].draw_line((100, 100), (120, 120))
            doc.save(self.root / "changed.pdf")
        with self.assertRaisesRegex(ValueError, "pixels"):
            m.pixel_gate(before, self.root / "changed.pdf")


if __name__ == "__main__":
    unittest.main()
