"""Synthetic text buffers only; never launches Hancom or applies a document."""
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import native_hwp


class NativeBufferTests(unittest.TestCase):
    def app(self):
        app = SimpleNamespace(text="TITLE_SLOT\r\nBODY_SLOT\r\nUNCHANGED")
        app.GetTextFile = lambda *args: app.text
        return app

    def edits(self):
        return [{"anchor_id": "title", "old_text": "TITLE_SLOT", "new_text": "Approved title"},
                {"anchor_id": "body", "old_text": "BODY_SLOT", "new_text": "Approved body"}]

    @staticmethod
    def replace(app, edit):
        app.text = app.text.replace(edit["old_text"], edit["new_text"], 1)
        return {"anchor_id": edit["anchor_id"], "old_text_exact": True}

    def test_exact_delta_returns_expected_text_and_evidence(self):
        app = self.app()
        with patch.object(native_hwp, "replace_plain_selection", self.replace):
            evidence, expected = native_hwp.edit_exact_buffer(app, self.edits())
        self.assertEqual(len(evidence), 2)
        self.assertEqual(expected, "Approved title\r\nApproved body\r\nUNCHANGED")
        self.assertEqual(app.text, expected)

    def test_extra_title_character_stops_before_second_edit_or_save(self):
        app = self.app()
        def extra(app, edit):
            proof = self.replace(app, edit)
            app.text = app.text.replace("Approved title", "Approved titleㅇ")
            return proof
        with patch.object(native_hwp, "replace_plain_selection", side_effect=extra) as worker:
            with self.assertRaisesRegex(ValueError, "Unexpected native text delta"):
                native_hwp.edit_exact_buffer(app, self.edits())
            self.assertEqual(worker.call_count, 1)
        self.assertIn("BODY_SLOT", app.text)

    def test_unselected_text_delta_is_not_normalized_or_ignored(self):
        app = self.app()
        def extra(app, edit):
            proof = self.replace(app, edit)
            app.text = app.text.replace("UNCHANGED", "UNAPPROVED")
            return proof
        with patch.object(native_hwp, "replace_plain_selection", side_effect=extra):
            with self.assertRaises(ValueError):native_hwp.edit_exact_buffer(app, self.edits())

    def test_missing_snapshot_stops_without_native_action(self):
        app = self.app();app.GetTextFile = lambda *args: None
        with patch.object(native_hwp, "replace_plain_selection") as worker:
            with self.assertRaises(ValueError):native_hwp.edit_exact_buffer(app, self.edits())
            worker.assert_not_called()
