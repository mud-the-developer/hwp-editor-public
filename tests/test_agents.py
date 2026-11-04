"""Synthetic PATH metadata and owned scanner tests; child launch is denied."""

import importlib
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import threading
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
a = importlib.import_module("hwp_agents")


class DiscoveryTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        for operation in ("Popen", "run", "call", "check_output", "check_call"):
            denied = patch.object(
                subprocess,
                operation,
                side_effect=AssertionError(
                    "Offline discovery must not launch anything"
                ),
            )
            denied.start()
            self.addCleanup(denied.stop)

    def cli(self, directory, name):
        directory.mkdir(exist_ok=True)
        path = directory / (name + (".cmd" if a.WINDOWS else ""))
        path.write_text("SYNTHETIC NOT A REAL CLI", encoding="utf-8")
        path.chmod(0o700)
        return path

    def test_exact_allowlist_path_order_and_presence_is_not_runtime_or_login_proof(
        self,
    ):
        first, second = self.root / "first", self.root / "second"
        expected = self.cli(first, "pi")
        self.cli(second, "pi")
        self.cli(second, "codex")
        self.cli(first, "pi-other")
        with patch.dict(
            os.environ, {"PATH": os.pathsep.join((str(first), str(second)))}
        ):
            result = a.scan_path()
        self.assertEqual([p["id"] for p in result["agents"]], ["pi"])
        self.assertEqual(result["agents"][0]["path"], str(expected))
        self.assertNotIn("codex", json.dumps(result))
        self.assertNotIn("hermes", json.dumps(result))
        for item in result["agents"]:
            self.assertFalse(item["connected"])
            self.assertFalse(item["runtime_verified"])

    def test_current_directory_relative_empty_and_unc_entries_are_not_probed(self):
        fake_cwd = self.root / "cwd"
        self.cli(fake_cwd, "pi")
        other = self.root / "other"
        expected = self.cli(other, "pi")
        entries = (
            "",
            ".",
            "relative-bin",
            str(fake_cwd),
            "//SYNTHETIC-NETWORK/bin",
            str(other),
        )
        with (
            patch.dict(os.environ, {"PATH": os.pathsep.join(entries)}),
            patch.object(a.os, "getcwd", return_value=str(fake_cwd)),
        ):
            result = a.scan_path()
        self.assertEqual(result["agents"][0]["path"], str(expected))
        self.assertEqual(result["ignored_entries"], 5)

    def test_profiles_auth_and_cli_file_contents_are_never_read(self):
        folder = self.root / "bin"
        self.cli(folder, "pi")
        profile = self.root / "PRIVATE-PROFILE"
        profile.mkdir()
        (profile / "auth.json").write_text("DO NOT READ", encoding="utf-8")
        with (
            patch.dict(
                os.environ,
                {
                    "PATH": str(folder),
                    "HOME": str(profile),
                    "USERPROFILE": str(profile),
                },
            ),
            patch.object(
                Path,
                "open",
                side_effect=AssertionError("Presence cannot read file contents"),
            ),
        ):
            result = a.scan_path()
        self.assertEqual(result["agents"][0]["status"], "detected")

    def test_directory_with_cli_name_is_not_a_cli(self):
        folder = self.root / "bin"
        folder.mkdir()
        (folder / ("pi.exe" if a.WINDOWS else "pi")).mkdir()
        with patch.dict(os.environ, {"PATH": str(folder)}):
            result = a.scan_path()
        self.assertEqual(result["agents"][0]["status"], "not_found_in_scanned_path")

    def test_oversized_or_invalid_path_fails_without_partial_absence_claim(self):
        for raw in ("x" * 65537, os.pathsep.join(["x"] * 257), "bad\0path"):
            with (
                self.subTest(raw_length=len(raw)),
                patch.object(a.os, "environ", {"PATH": raw}),
            ):
                with self.assertRaises(ValueError):
                    a.scan_path()

    def test_construct_snapshot_and_close_never_start_scan(self):
        with patch.object(a, "scan_path") as scan:
            c = a.Discovery()
            self.assertEqual(c.snapshot()["status"], "idle")
            c.close()
            with self.assertRaises(ValueError):
                c.start()
            scan.assert_not_called()
        self.assertFalse(c.snapshot()["executed"])
        self.assertFalse(c.snapshot()["model_called"])

    def test_one_background_scan_and_detached_snapshots(self):
        entered, release = threading.Event(), threading.Event()
        payload = {
            "agents": [{"id": "pi", "path": "SYNTHETIC"}],
            "scope": "SYNTHETIC",
            "ignored_entries": 0,
            "unreadable_candidates": 0,
        }

        def slow():
            entered.set()
            if not release.wait(5):
                raise AssertionError("Synthetic scanner release missing")
            return payload

        with patch.object(a, "scan_path", side_effect=slow) as scan:
            c = a.Discovery()
            try:
                self.assertEqual(c.start()["status"], "discovering")
                self.assertTrue(entered.wait(2))
                thread = c.thread
                self.assertEqual(c.start()["status"], "discovering")
                self.assertIs(c.thread, thread)
                release.set()
                if thread is None:
                    raise AssertionError("Synthetic thread missing")
                thread.join(5)
                self.assertFalse(thread.is_alive())
                self.assertEqual(c.snapshot()["status"], "finished")
                copy = c.snapshot()
                copy["agents"][0]["path"] = "CHANGED"
                self.assertEqual(c.snapshot()["agents"][0]["path"], "SYNTHETIC")
                scan.assert_called_once()
            finally:
                release.set()
                c.close()
                if c.thread is not None:
                    c.thread.join(5)

    def test_close_while_scanning_never_republishes_a_result(self):
        entered, release = threading.Event(), threading.Event()

        def slow():
            entered.set()
            release.wait(5)
            return {"agents": [], "scope": "SHOULD NOT PUBLISH"}

        with patch.object(a, "scan_path", side_effect=slow):
            c = a.Discovery()
            try:
                c.start()
                self.assertTrue(entered.wait(2))
                c.close()
                release.set()
                if c.thread is not None:
                    c.thread.join(5)
                self.assertEqual(c.snapshot()["status"], "closed")
                self.assertNotIn("SHOULD NOT PUBLISH", json.dumps(c.snapshot()))
            finally:
                release.set()

    def test_failure_is_sanitized_and_not_retried(self):
        with patch.object(
            a, "scan_path", side_effect=OSError("PRIVATE-TOKEN-NOT-FOR-GUI")
        ) as scan:
            c = a.Discovery()
            c.start()
            if c.thread is not None:
                c.thread.join(5)
            self.assertEqual(c.snapshot()["status"], "failed")
            self.assertNotIn("PRIVATE-TOKEN", json.dumps(c.snapshot()))
            scan.assert_called_once()
            c.close()

    def test_thread_start_failure_is_visible_without_fallback(self):
        c = a.Discovery()
        with (
            patch.object(
                a.threading.Thread,
                "start",
                side_effect=RuntimeError("SYNTHETIC START FAILURE"),
            ),
            patch.object(a, "scan_path") as scan,
        ):
            self.assertEqual(c.start()["status"], "failed")
            scan.assert_not_called()
        c.close()


if __name__ == "__main__":
    unittest.main()
