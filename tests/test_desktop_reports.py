"""Synthetic desktop report consumers; real preparation/HTTP, no consent or Native."""

import http.client
from pathlib import Path
import subprocess
import sys
import tempfile
import threading
import time
import tkinter as tk
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import hwp_desktop as desktop
import hwp_report_project as projects
import hwp_report_ui as intake
import hwp_review as review
import hwp_workspace as workspace
from test_desktop import fixture as hwp_fixture, snapshot
from test_template_draft import fixture


def report_inputs(root):
    source, payload = root / "source.hwpx", root / "draft.hwpx"
    source.write_bytes(fixture())
    payload.write_bytes(fixture(True))
    return intake.basic_inputs(
        intake.capture_document(source), intake.capture_document(payload), [2, 3], [0, 2],
        label="SYNTHETIC REPORT ONLY", caption_policy="legacy",
        goal="Preserve the exact report scope", decisions=["Keep original geometry"],
    )


class ReportConsumerFixture(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.files = Path(temporary.name).resolve()
        self.base = self.files / "Reports"
        self.profile, self.feedback = report_inputs(self.files)
        for owner, name in ((subprocess, "Popen"), (review, "native_export")):
            deny = patch.object(owner, name, side_effect=AssertionError("No Native/model process"))
            deny.start()
            self.addCleanup(deny.stop)

    def create(self):
        info = projects.create(self.profile, self.feedback, base=self.base)
        self.assertTrue(info.gate_passed, info.issues)
        return info

    def session(self, root):
        session = desktop.ReviewSession()
        self.addCleanup(session.stop)
        session.start(root)
        return session

    def assert_report_http(self, session):
        connection = http.client.HTTPConnection("127.0.0.1", session._server.server_port, timeout=5)
        try:
            token = session._server.template_review_token
            connection.request("GET", "/")
            response = connection.getresponse()
            response.read()
            self.assertEqual(response.status, 403)
            connection.request("GET", "/", headers={"X-Review-Token": token})
            response = connection.getresponse()
            response.read()
            self.assertEqual(response.status, 404)
            endpoint = "/api/report-state" if getattr(session.info, "document_first", False) else "/api/template-state"
            connection.request("GET", endpoint, headers={"X-Review-Token": token})
            response = connection.getresponse()
            data = response.read()
            self.assertEqual(response.status, 200)
            self.assertIn(b'"revision"', data)
        finally:
            connection.close()


class ReportSessionTests(ReportConsumerFixture):
    def test_report_root_roundtrip_reopen_and_hwp_modes_never_mix(self):
        info = self.create()
        before = snapshot(info.root)
        with patch.object(desktop, "inspect_workspace", side_effect=AssertionError("No HWP dispatch")):
            session = self.session(info.root)
            self.assertEqual(session.info.root, info.root)
            self.assertEqual(session.info.review, info.root / "run" / "review")
            self.assertFalse(hasattr(session.info, "path"))
            self.assertFalse(hasattr(session.info, "pages"))
            with self.assertRaises(ValueError):
                session.legacy_client()
            self.assert_report_http(session)
            session.stop(timeout=5)
            session.start(info.root)
            self.assert_report_http(session)
            session.stop(timeout=5)
        self.assertEqual(snapshot(info.root), before)
        self.assertFalse(list(info.root.rglob("approved-plan.json")))

    def test_malformed_report_marker_overrides_even_a_valid_legacy_workspace(self):
        root = self.files / "mixed"
        hwp_fixture(root)
        (root / projects.PROJECT_FILE).write_text("{", encoding="utf-8")
        before = snapshot(root)
        session = desktop.ReviewSession()
        self.addCleanup(session.stop)
        with patch.object(desktop, "inspect_workspace", side_effect=AssertionError("No legacy fallback")):
            with self.assertRaises(ValueError):
                session.start(root)
        self.assertFalse(session.running)
        self.assertIsNone(session._server)
        self.assertEqual(snapshot(root), before)

    def test_report_shutdown_timeout_retains_ownership_and_other_server_survives(self):
        info = self.create()
        owned, other = self.session(info.root), self.session(info.root)
        release = threading.Event()
        self.addCleanup(release.set)
        original_shutdown = owned._server.shutdown

        def delayed_shutdown():
            release.wait(5)
            original_shutdown()

        with patch.object(owned._server, "shutdown", side_effect=delayed_shutdown):
            try:
                with self.assertRaises(RuntimeError):
                    owned.stop(timeout=.01)
                self.assertTrue(owned.stopping)
                self.assertEqual(owned.info.root, info.root)
                with self.assertRaises(RuntimeError):
                    owned.start(info.root)
                self.assert_report_http(other)
            finally:
                release.set()
                owned.stop(timeout=5)
        self.assertFalse(owned.running)
        self.assertTrue(other.running)
        self.assert_report_http(other)

    def test_library_is_metadata_only_and_damaged_marker_never_becomes_staged_hwp(self):
        info = self.create()
        before = snapshot(self.base)
        with patch.object(projects, "inspect", side_effect=AssertionError("No live admission on list")), patch.object(
            review, "file_hash", side_effect=AssertionError("No document/runtime hashing on list")
        ):
            row, = workspace.scan_library(self.base).entries
        self.assertEqual(row.root, info.root)
        self.assertEqual(row.state, "prepared")
        self.assertIsNone(row.pages)
        self.assertEqual(snapshot(self.base), before)
        (info.root / projects.PROJECT_FILE).write_text("{", encoding="utf-8")
        (info.root / "tempting-legacy.hwp").write_bytes(b"SYNTHETIC ONLY")
        damaged = snapshot(self.base)
        row, = workspace.scan_library(self.base).entries
        self.assertEqual(row.state, "attention")
        self.assertIsNone(row.pages)
        self.assertEqual(snapshot(self.base), damaged)


class ReportDesktopTests(ReportConsumerFixture):
    def test_legacy_window_does_not_fall_back_to_hwp_from_report_marker(self):
        try:
            root = tk.Tk()
        except tk.TclError as error:
            self.skipTest("Tk display unavailable: " + str(error))
        try:
            root.withdraw()
            directory = self.files / "damaged"
            hwp_fixture(directory)
            (directory / projects.PROJECT_FILE).write_text("{", encoding="utf-8")
            before = snapshot(directory)
            app = desktop.DesktopApp(root)
            app.path.set(str(directory))
            with patch.object(desktop, "inspect_workspace", side_effect=AssertionError("No legacy fallback")):
                app.check_workspace()
                deadline = time.monotonic() + 5
                while app.busy and time.monotonic() < deadline:
                    root.update()
                    time.sleep(.01)
                self.assertFalse(app.busy)
            self.assertFalse(app.session.running)
            self.assertIsNone(app.project)
            self.assertEqual(str(app.prepare_button.cget("state")), "disabled")
            self.assertEqual(snapshot(directory), before)
        finally:
            for identifier in root.tk.call("after", "info"):
                root.after_cancel(identifier)
            root.destroy()


if __name__ == "__main__":
    unittest.main()
