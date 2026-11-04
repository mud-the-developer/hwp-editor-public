"""Synthetic desktop report consumers; real preparation/HTTP, no consent or Native."""

from copy import deepcopy
import http.client
from pathlib import Path
import subprocess
import sys
import tempfile
import threading
import time
import tkinter as tk
import unittest
from unittest.mock import PropertyMock, patch

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
            connection.request("GET", "/")
            response = connection.getresponse()
            self.assertEqual(response.status, 200)
            self.assertIn(b'name="review-token"', response.read())
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
            for mode in desktop.MODES:
                with self.subTest(mode=mode), self.assertRaises(ValueError):
                    session.url(mode)
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
    def setUp(self):
        super().setUp()
        try:
            self.root = tk.Tk()
        except tk.TclError as error:
            self.skipTest("Tk display unavailable; not a visual pass: " + str(error))
        self.root.withdraw()
        self.addCleanup(self.destroy)
        with patch.object(desktop, "find_edge", return_value=None):
            self.app = desktop.DesktopApp(self.root, report_base=self.base)
        self.addCleanup(self.app.session.stop)

    def destroy(self):
        if hasattr(self, "app"):
            self.app.reports.close_dialog()
        for identifier in self.root.tk.call("after", "info"):
            self.root.after_cancel(identifier)
        self.root.destroy()

    def pump_until(self, predicate, timeout=15):
        deadline = time.monotonic() + timeout
        while not predicate() and time.monotonic() < deadline:
            self.root.update()
            time.sleep(.01)
        self.assertTrue(predicate(), "Synthetic report desktop transition did not finish")
        self.root.update()

    def check(self, root):
        self.app.path.set(str(root))
        self.app.check_workspace()
        self.pump_until(lambda: not self.app.busy)

    def test_explicit_wizard_submit_prepares_in_report_base_and_opens_comparison_only_once(self):
        profile_path, feedback_path = self.files / "profile.json", self.files / "feedback.json"
        review.write_json(profile_path, self.profile)
        review.write_json(feedback_path, self.feedback)
        with patch.object(desktop.webbrowser, "open", return_value=True) as browser:
            self.app.reports.advanced()
            dialog = self.app.reports.dialog
            self.assertIsNotNone(dialog)
            self.assertFalse(self.base.exists())
            browser.assert_not_called()
            dialog._load("profile", profile_path)
            dialog._load("feedback", feedback_path)
            self.pump_until(lambda: not dialog._pending)
            dialog.notebook.select(1)
            dialog.prepare_button.invoke()
            self.pump_until(lambda: self.app.session.running and not self.app.busy)
            browser.assert_called_once_with(self.app.session.url("report"), new=1)
            info = self.app.info
            self.assertEqual(info.root.parent, self.base)
            self.assertEqual(self.app.path.get(), str(info.root))
            self.assertIsNone(self.app.project)
            self.assertFalse(hasattr(info, "path"))
            self.assertFalse(hasattr(info, "pages"))
            self.app.session.stop(timeout=5)
            before = snapshot(info.root)
            browser.reset_mock()
            self.check(info.root)
            self.root.update_idletasks()
            browser.assert_not_called()
            self.assertFalse(self.app.session.running)
            self.assertEqual(snapshot(info.root), before)
            self.assertEqual(str(self.app.mode_picker.cget("state")), "disabled")
            self.assertEqual(str(self.app.export_button.cget("state")), "disabled")
            self.assertFalse(list(info.root.rglob("approved-plan.json")))

    def test_source_picker_opens_document_workspace_without_model_or_draft(self):
        source = Path(self.profile["jobs"][0]["source"])
        original = source.read_bytes()
        with (
            patch.object(desktop.filedialog, "askopenfilename", return_value=str(source)),
            patch.object(desktop.webbrowser, "open", return_value=True) as browser,
        ):
            self.app.reports.new()
            self.pump_until(lambda: self.app.session.running and not self.app.busy)
        self.assertTrue(self.app.info.document_first)
        self.assertEqual(self.app.info.root.parent, self.base)
        self.assertEqual(self.app.session.info.root, self.app.info.root)
        state = self.app.session._server.report_control.state()
        self.assertFalse(state["versions"])
        self.assertEqual(state["model"]["status"], "disconnected")
        self.assertEqual(source.read_bytes(), original)
        self.assertFalse(list(self.app.info.root.rglob("draft-permission.json")))
        browser.assert_called_once()

    def test_revision_closes_only_same_owned_report_and_preserves_original_inputs(self):
        info = self.create()
        other = self.session(info.root)
        self.check(info.root)
        self.app.session.start(info.root)
        before = snapshot(info.root)
        self.app.reports.revise()
        self.pump_until(lambda: self.app.reports.dialog is not None and not self.app.busy)
        self.assertFalse(self.app.session.running)
        self.assertTrue(other.running)
        self.assert_report_http(other)
        self.assertEqual(snapshot(info.root), before)
        dialog = self.app.reports.dialog
        self.assertEqual(dialog.goal.get("1.0", "end").strip(), self.feedback["goal"])
        self.assertEqual(projects.read_inputs(info.root), (self.profile, self.feedback))
        # An explicit changed-input submit creates a new root, not an old-run rewrite.
        changed = deepcopy(self.feedback)
        changed["decisions"].append("Keep the previous report intact")
        dialog.close()
        with patch.object(desktop, "open_window", return_value="Synthetic comparison opened"):
            self.app.reports.prepare(self.profile, changed, previous=info.root)
            self.pump_until(lambda: self.app.session.running and not self.app.busy)
        self.assertNotEqual(self.app.info.root, info.root)
        self.assertEqual(projects.read_inputs(self.app.info.root), (self.profile, changed))
        self.assertEqual(snapshot(info.root), before)

    def test_damaged_revision_seed_preserves_server_and_event_polling(self):
        info = self.create()
        self.check(info.root)
        self.app.session.start(info.root)
        source_input = info.root / "profile-input.json"
        source_input.write_bytes(source_input.read_bytes() + b" ")
        damaged = snapshot(info.root)
        self.app.reports.revise()
        self.pump_until(lambda: not self.app.busy)
        self.assertTrue(self.app.last_error)
        self.assertTrue(self.app.session.running)
        self.assertIsNone(self.app.reports.dialog)
        self.app.session.stop(timeout=5)
        changed = deepcopy(self.feedback)
        changed["decisions"].append("Prepare a different valid report after a blocked revision")
        following = projects.create(self.profile, changed, base=self.base)
        self.assertTrue(following.gate_passed, following.issues)
        self.app.path.set(str(following.root))
        self.app.check_workspace()
        self.pump_until(lambda: not self.app.busy, timeout=5)
        self.assertEqual(self.app.info.root, following.root)
        self.assertTrue(self.app.info.gate_passed)
        self.assertEqual(snapshot(info.root), damaged)

    def test_busy_stopping_and_foreign_session_refuse_revision_without_stopping_it(self):
        info = self.create()
        self.check(info.root)
        for state in ("busy", "stopping"):
            with self.subTest(state=state):
                if state == "busy":
                    self.app.busy = True
                    try:
                        self.app.reports.revise()
                        self.assertIsNone(self.app.reports.dialog)
                    finally:
                        self.app.busy = False
                else:
                    with patch.object(type(self.app.session), "stopping", new_callable=PropertyMock, return_value=True):
                        self.app.reports.revise()
                        self.assertIsNone(self.app.reports.dialog)
        hwp = self.files / "legacy"
        hwp_fixture(hwp)
        changed = deepcopy(self.feedback)
        changed["decisions"].append("A separate synthetic report session")
        foreign = projects.create(self.profile, changed, base=self.base)
        self.assertTrue(foreign.gate_passed, foreign.issues)
        for root in (foreign.root, hwp):
            with self.subTest(root=root):
                self.app.session.start(root)
                with self.assertRaises(ValueError):
                    self.app.reports.revise()
                self.assertTrue(self.app.session.running)
                self.assertIsNone(self.app.reports.dialog)
                self.app.update_controls()
                self.assertEqual(str(self.app.report_revision_button.cget("state")), "disabled")
                self.app.session.stop(timeout=5)

    def test_damaged_report_check_never_offers_legacy_preparation(self):
        root = self.files / "damaged"
        hwp_fixture(root)
        (root / projects.PROJECT_FILE).write_text("{", encoding="utf-8")
        before = snapshot(root)
        with patch.object(desktop, "inspect_workspace", side_effect=AssertionError("No legacy fallback")), patch.object(
            desktop, "open_window", side_effect=AssertionError("No automatic browser")
        ):
            self.check(root)
        self.assertFalse(self.app.session.running)
        self.assertFalse(self.app.info.gate_passed)
        self.assertIsNone(self.app.project)
        self.assertEqual(str(self.app.prepare_button.cget("state")), "disabled")
        self.assertEqual(snapshot(root), before)


if __name__ == "__main__":
    unittest.main()
