"""Read-only Table.Cell observation: fake COM and no document put/save."""

import ast
import inspect
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

import native_parameter_copy_probe as probe
from test_native_table_height_probe import Params, Target


class Table(Params):
    @property
    def SetID(self) -> str:
        return "Table"

    @property
    def Count(self):
        return 37

    def __init__(self, cell=None):
        super().__init__({"Height": 6728, "Width": 41954})
        self.cell = (
            cell
            if cell is not None
            else Params({"Height": 282, "Width": 9000, "Border": "SYNTHETIC"})
        )

    def ItemExist(self, name):
        return name in ("Cell", "Height", "Width")

    def Item(self, name):
        return {"Cell": self.cell, "Height": 6728, "Width": 41954}[name]

    def Clone(self):
        return Table(self.cell.Clone())

    def IsEquivalent(self, other):
        return self.cell.values == other.cell.values


class ReadOnlyTarget(Target):
    def __init__(self, source):
        super().__init__(source)
        self.table = Table()
        self.SaveAs = Mock(side_effect=AssertionError("Observation cannot save"))

    @property
    def CellShape(self):
        return self.table

    @CellShape.setter
    def CellShape(self, value):
        raise AssertionError("Observation cannot put document shape")


class ParameterCopyProbeTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.source = Path(temp.name).resolve() / "SYNTHETIC-NOT-REAL.hwp"
        self.source.write_bytes(b"SYNTHETIC ONLY")
        self.target = ReadOnlyTarget(self.source)
        self.app = probe.NativeReference(self.target)
        self.digest = probe.r.file_hash(self.source)

    def observe(self):
        self.evidence = {}
        return probe.observe(
            self.app,
            self.source,
            self.digest,
            "SYNTHETIC full",
            [5, 0, 0],
            "SYNTHETIC CELL",
            self.evidence,
        )

    def test_child_copy_and_height_restore_never_put_save_or_remove(self):
        with patch.object(
            Params, "RemoveItem", side_effect=AssertionError("No removal assumption")
        ):
            evidence = self.observe()
        self.assertEqual(evidence["original_table"]["SetID"], "Table")
        self.assertEqual(evidence["original_cell"]["SetID"], "Cell")
        self.assertEqual(evidence["original_after_copy_change"]["Height"]["value"], 282)
        self.assertEqual(evidence["changed_cell_copy"]["Height"]["value"], 1282)
        self.assertEqual(evidence["restored_comparison_cell"]["Height"]["value"], 282)
        self.assertEqual(
            evidence["changed_cell_after_comparison_restore"]["Height"]["value"], 1282
        )
        self.assertEqual(
            evidence["restored_comparison"], {"python_type": "bool", "value": True}
        )
        self.assertEqual(self.target.table.cell.Item("Height"), 282)
        self.assertEqual(self.target.writes, [])
        self.target.SaveAs.assert_not_called()
        self.assertEqual(probe.r.file_hash(self.source), self.digest)

    def test_false_comparison_is_observation_not_write_or_qualification(self):
        with patch.object(Table, "IsEquivalent", return_value=False):
            evidence = self.observe()
        self.assertEqual(
            evidence["original_vs_clone"], {"python_type": "bool", "value": False}
        )
        self.assertFalse(evidence["restored_comparison"]["value"])
        self.assertEqual(self.target.writes, [])
        self.target.SaveAs.assert_not_called()

    def test_parameter_error_or_modified_document_stops(self):
        with patch.object(Table, "Clone", side_effect=ValueError("SYNTHETIC error")):
            with self.assertRaisesRegex(ValueError, "SYNTHETIC error"):
                self.observe()
        self.target.XHwpDocuments.Item = lambda _: SimpleNamespace(
            FullName=str(self.source), Path=str(self.source.parent), Modified=True
        )
        with self.assertRaisesRegex(ValueError, "Read-only"):
            self.observe()
        self.assertEqual(self.target.writes, [])
        self.target.SaveAs.assert_not_called()

    def test_wrong_parent_or_child_scope_stops_before_parameter_mutation(self):
        for value in ("Cell", "FOREIGN"):
            with self.subTest(value=value), patch.object(Table, "SetID", value):
                with self.assertRaisesRegex(ValueError, "Table.Cell"):
                    self.observe()
        with patch.object(
            Params, "SetID", new_callable=property, fget=lambda _: "FOREIGN"
        ):
            with self.assertRaisesRegex(ValueError, "Cell child"):
                self.observe()
        self.assertEqual(self.target.table.cell.Item("Height"), 282)

    def test_shallow_child_clone_is_rejected(self):
        with patch.object(Table, "Clone", lambda table: Table(table.cell)):
            with self.assertRaisesRegex(ValueError, "aliased original"):
                self.observe()
        self.assertEqual(self.target.writes, [])
        self.target.SaveAs.assert_not_called()

    def test_comparison_clone_must_not_reset_pending_copy(self):
        calls = []

        def clone(table):
            calls.append(table)
            return Table(table.cell.Clone() if len(calls) == 1 else table.cell)

        with patch.object(Table, "Clone", clone):
            with self.assertRaisesRegex(ValueError, "pending child copy"):
                self.observe()
        self.assertEqual(self.target.table.cell.Item("Height"), 282)
        self.assertEqual(self.target.writes, [])
        self.target.SaveAs.assert_not_called()

    def test_missing_optin_or_foreign_path_never_queries_or_creates(self):
        with (
            patch.object(
                probe, "hwp_inventory", side_effect=AssertionError("No process query")
            ),
            patch.object(
                probe.security,
                "registered_security_module",
                side_effect=AssertionError("No security query"),
            ),
        ):
            for kwargs in (
                {},
                {"run_native": True},
                {"run_native": True, "toy_only": 1},
            ):
                with self.subTest(kwargs=kwargs), self.assertRaises(ValueError):
                    probe.run(
                        self.source.parent / "toy-parameter-copy-foreign", **kwargs
                    )
            with self.assertRaises(ValueError):
                probe.run(
                    self.source.parent / "toy-parameter-copy-foreign",
                    run_native=True,
                    toy_only=True,
                )
        self.assertEqual(list(self.source.parent.iterdir()), [self.source])

    def test_observation_ast_has_no_property_put_save_action_or_remove(self):
        for node in ast.walk(ast.parse(inspect.getsource(probe.observe))):
            if isinstance(node, ast.Attribute):
                self.assertNotIn(
                    node.attr, ("SaveAs", "set_height", "HAction", "RemoveItem")
                )
                if node.attr == "CellShape":
                    self.assertIsInstance(node.ctx, ast.Load)


if __name__ == "__main__":
    unittest.main()
