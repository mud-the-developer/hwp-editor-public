"""Shared empty-cell primitive: fake owned buffers only; no COM/paths/executor."""

import importlib
import unittest
from unittest.mock import Mock
import test_direct_fill_probe as own

buffer = importlib.import_module("hwp_native_buffer")


class BoundEmptyBufferTests(unittest.TestCase):
    def setUp(self):
        self.fixture = own.DirectFillProbeTests()
        self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)
        self.app = self.fixture.app
        self.pos = [105, 0, 0]
        self.item = {"field_id": "SYNTHETIC-CELL", "new_text": "한글 TEST VALUE"}

    def test_exact_empty_cell_addition_uses_actual_position_and_canonical_evidence(
        self,
    ):
        before = self.app.buffer
        proof, after = buffer.insert_bound_empty_cell(
            self.app, self.item, self.pos, before
        )
        self.assertEqual(after.replace(self.item["new_text"], "", 1), before)
        self.assertEqual(proof["verified_position"], self.pos)
        self.assertTrue(proof["exact_literal_addition_only"])
        self.app.InitScan.assert_called_once_with(0, 0x55)
        self.app.ReleaseScan.assert_called_once()

    def test_occupied_or_unreadable_cell_never_executes_insert(self):
        for state, text in ((2, "NOT EMPTY\r\n"), (4, ""), (101, "")):
            self.app.GetText = Mock(side_effect=iter(((state, text), (1, ""))))
            with self.assertRaises(ValueError):
                buffer.insert_bound_empty_cell(
                    self.app, self.item, self.pos, self.app.buffer
                )
        self.app.HAction.Execute.assert_not_called()

    def test_wrong_snapshot_value_or_position_never_navigates(self):
        before = self.app.buffer
        cases = [
            ({**self.item, "new_text": ""}, self.pos, before),
            ({**self.item, "new_text": "Name"}, self.pos, before),
            ({**self.item, "new_text": "line\nbreak"}, self.pos, before),
            (self.item, [0, 0, 0], before),
            (self.item, [True, 0, 0], before),
            (self.item, self.pos, "STALE"),
        ]
        for item, pos, snapshot in cases:
            with self.assertRaises(ValueError):
                buffer.insert_bound_empty_cell(self.app, item, pos, snapshot)
        self.app.SetPos.assert_not_called()
        self.app.HAction.Execute.assert_not_called()

    def test_selection_flags_and_ranges_are_not_coerced(self):
        for raw in (
            (0, 0, 0, 0, 0, 0, 0),
            (False, True, 0, 0, 0, 0, 0),
            (False, 105, 0, 0, 105, 0, 0),
            (True, 105, 0, 0, 106, 0, 0),
        ):
            self.app.GetSelectedPos = Mock(return_value=raw)
            with self.assertRaises(ValueError):
                buffer.insert_bound_empty_cell(
                    self.app, self.item, self.pos, self.app.buffer
                )
        self.app.HAction.Execute.assert_not_called()

    def test_calibrated_no_block_is_allowed_only_with_exact_empty_scan(self):
        self.app.GetSelectedPos = Mock(return_value=(False, 0, 0, 0, 0, 0, 0))
        proof, _ = buffer.insert_bound_empty_cell(
            self.app, self.item, self.pos, self.app.buffer
        )
        self.assertEqual(
            proof["selection_metadata"]["diagnostic_classification"],
            "calibrated_no_block",
        )

    def test_navigation_or_metadata_buffer_drift_blocks_execute(self):
        def move(*pos):
            self.app.pos = list(pos)
            self.app.buffer += "UNAPPROVED"
            return True

        self.app.SetPos = Mock(side_effect=move)
        with self.assertRaises(ValueError):
            buffer.insert_bound_empty_cell(
                self.app, self.item, self.pos, self.app.buffer
            )
        self.app.HAction.Execute.assert_not_called()

    def test_unexpected_insert_delta_or_execute_failure_never_returns_success(self):
        self.app.HAction.Execute = Mock(return_value=False)
        with self.assertRaises(ValueError):
            buffer.insert_bound_empty_cell(
                self.app, self.item, self.pos, self.app.buffer
            )
        self.app.HAction.Execute = Mock(
            side_effect=lambda *args: setattr(self.app, "buffer", "UNAPPROVED") or True
        )
        with self.assertRaises(ValueError):
            buffer.insert_bound_empty_cell(
                self.app, self.item, self.pos, self.app.buffer
            )


if __name__ == "__main__":
    unittest.main()
