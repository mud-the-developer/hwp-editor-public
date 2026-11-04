"""Fixed synthetic agent seam only: no SDK/model/profile or Native process."""

import importlib
from pathlib import Path
import subprocess
import tempfile
import threading
import unittest
from unittest.mock import patch

m = importlib.import_module("gui_workflow_fixture")


class SyntheticGuiAgentTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        denied = patch.object(
            subprocess,
            "Popen",
            side_effect=AssertionError("No SDK/model/Native launch"),
        )
        self.launch = denied.start()
        self.addCleanup(denied.stop)

    def prepared(self):
        fake = m.SyntheticPi(self.root)
        presence = fake.scan_path()
        runtime = fake.resolve_runtime(presence["agents"][0]["path"])
        cancel = threading.Event()
        profile = fake.call_chat(runtime, "profile_info", cancel)
        request = {
            "model": profile["model"],
            "history": [],
            "message": m.AGENT_QUESTION,
            "proposal_tools": True,
            "context": {
                "review_context": {
                    "proposal_kind": "text",
                    "selected_anchor": {
                        "anchor_id": "SYNTHETIC-anchor",
                        "old_text": m.OLD,
                    },
                }
            },
        }
        return fake, runtime, cancel, request

    def test_missing_consent_changed_context_or_nonfixed_input_cannot_propose(self):
        changes = (
            {"proposal_tools": False},
            {"proposal_tools": 1},
            {"context": None},
            {
                "context": {
                    "review_context": {
                        "proposal_kind": "text",
                        "selected_anchor": {
                            "anchor_id": "SYNTHETIC-anchor",
                            "old_text": "FOREIGN",
                        },
                    }
                }
            },
            {"model": {"provider": "real", "id": "forbidden"}},
            {"history": [{"role": "user", "content": "not empty"}]},
            {"message": "not the fixed synthetic question"},
        )
        for change in changes:
            with self.subTest(change=change):
                fake, runtime, cancel, request = self.prepared()
                with self.assertRaises(ValueError):
                    fake.call_chat(runtime, "chat", cancel, **{**request, **change})
                self.assertEqual(fake.calls["chat"], 0)
        self.assertEqual(list(self.root.rglob("*")), [])
        self.launch.assert_not_called()

    def test_fixture_literal_snapshot_does_not_follow_later_global_drift(self):
        fake, runtime, cancel, request = self.prepared()
        old, new = m.OLD, m.NEW
        with (
            patch.object(m, "OLD", "FOREIGN OLD"),
            patch.object(m, "NEW", "FOREIGN NEW"),
        ):
            reply = fake.call_chat(runtime, "chat", cancel, **request)
        self.assertEqual(
            (reply["artifacts"][0]["old_text"], reply["artifacts"][0]["new_text"]),
            (old, new),
        )
        self.assertEqual(fake.calls["chat"], 1)
        self.assertEqual(list(self.root.rglob("*")), [])
        self.launch.assert_not_called()

    def test_cancellation_unknown_operation_and_repeats_never_add_a_turn(self):
        fake, runtime, cancel, request = self.prepared()
        with self.assertRaises(ValueError):
            fake.call_chat(runtime, "shell", cancel, **request)
        cancel.set()
        with self.assertRaises(ValueError):
            fake.call_chat(runtime, "chat", cancel, **request)
        cancel.clear()
        fake.call_chat(runtime, "chat", cancel, **request)
        with self.assertRaises(ValueError):
            fake.call_chat(runtime, "chat", cancel, **request)
        with self.assertRaises(ValueError):
            fake.call_chat(runtime, "profile_info", cancel)
        with self.assertRaises(ValueError):
            fake.resolve_runtime(runtime["cli"])
        self.assertEqual(
            fake.calls, {"discovery": 1, "metadata": 1, "profile": 1, "chat": 1}
        )
        self.assertEqual(list(self.root.rglob("*")), [])
        self.launch.assert_not_called()


if __name__ == "__main__":
    unittest.main()
