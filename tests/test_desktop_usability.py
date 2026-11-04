"""SYNTHETIC onboarding/progress/stop regressions; NOT Native fidelity evidence."""

from contextlib import ExitStack
from pathlib import Path
import os
import sys
import tempfile
import unittest
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import hwp_project as p
import hwp_review as r
import hwp_desktop as d
from test_desktop import DesktopWidgetTests, snapshot


class UsabilityBackendTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve()
        self.source = self.root / "SYNTHETIC-ONLY.hwp"
        self.source.write_bytes(b"SYNTHETIC HWP ONLY NOT REAL")
        self.audit = patch.object(
            r,
            "hwp_audit",
            side_effect=lambda path: {
                "sha256": r.file_hash(path),
                "embedded_binary_hashes": [],
            },
        )
        self.audit.start()
        self.addCleanup(self.audit.stop)

    def test_document_picker_creates_one_exact_local_copy_without_native(self):
        before = self.source.read_bytes()
        with (
            patch.dict(os.environ, {"LOCALAPPDATA": str(self.root / "local")}),
            patch.object(p.subprocess, "run") as process,
        ):
            project = p.new_project(self.source)
            process.assert_not_called()
        self.assertEqual(project.state, "staged")
        self.assertEqual(project.root.parent, self.root / "local/HwpReview/Projects")
        if project.source is None:
            raise AssertionError("Synthetic source copy missing")
        self.assertEqual(project.source.read_bytes(), before)
        self.assertEqual(self.source.read_bytes(), before)
        self.assertFalse(project.workspace.exists())

    def test_same_filename_always_uses_new_folder_never_replaces_previous_work(self):
        base = self.root / "projects"
        a, b = (
            p.new_project(self.source, base=base),
            p.new_project(self.source, base=base),
        )
        self.assertNotEqual(a.root, b.root)
        if a.source is None or b.source is None:
            raise AssertionError("Synthetic source copy missing")
        self.assertEqual(a.source.read_bytes(), b.source.read_bytes())

    def test_unsupported_input_audit_fails_before_any_new_folder(self):
        base = self.root / "projects"
        with patch.object(
            r, "hwp_audit", side_effect=ValueError("SYNTHETIC unsupported")
        ):
            with self.assertRaises(ValueError):
                p.new_project(self.source, base=base)
        self.assertFalse(base.exists())

    def test_missing_local_path_or_relative_base_never_guesses_a_location(self):
        with patch.dict(os.environ, {}, clear=True):
            with self.assertRaises(RuntimeError):
                p.new_project(self.source)
        with self.assertRaises(ValueError):
            p.new_project(self.source, base=Path("relative"))

    def test_cancel_before_start_never_creates_workspace_or_calls_exporter(self):
        workspace = self.root / "cancelled"
        export = Mock()
        with self.assertRaises(p.PreparationCancelled):
            r.prepare(self.source, workspace, exporter=export, cancelled=lambda: True)
        self.assertFalse(workspace.exists())
        export.assert_not_called()

    def test_cancel_after_first_native_phase_stops_before_next_export_and_keeps_files(
        self,
    ):
        workspace = self.root / "partial"
        stopped = []

        def export(source, pdf, **kwargs):
            pdf.write_bytes(b"SYNTHETIC PARTIAL EXPORT")
            stopped.append(True)
            return {"native_pages": 1}

        call = Mock(side_effect=export)
        with self.assertRaises(p.PreparationCancelled):
            r.prepare(
                self.source, workspace, exporter=call, cancelled=lambda: bool(stopped)
            )
        self.assertEqual(call.call_count, 1)
        self.assertTrue((workspace / "preparing.json").is_file())
        self.assertTrue((workspace / "baseline.pdf").is_file())
        self.assertFalse((workspace / "manifest.json").exists())
        self.assertFalse((workspace / "approved-plan.json").exists())

    def fake_pipeline(self, workspace, progress=None, cancelled=None):
        def export(source, pdf, **kwargs):
            with r.fitz.open() as doc:
                page = doc.new_page()
                page.insert_text((30, 50), "SYNTHETIC TEST ONLY")
                doc.save(pdf)
            for name in ("hwpx", "hwp"):
                if kwargs.get(name):
                    kwargs[name].write_bytes(b"SYNTHETIC OWNED INPUT")
            return {"native_pages": 1}

        with ExitStack() as stack:
            stack.enter_context(
                patch.object(r, "index_hwpx", return_value=([], {"tbl": 0, "pic": 0}))
            )
            stack.enter_context(
                patch.object(
                    r,
                    "map_anchors",
                    return_value=(
                        [],
                        [{"page": 1, "image_only": False, "preview": "pages/1.png"}],
                    ),
                )
            )
            return r.prepare(
                self.source,
                workspace,
                exporter=export,
                progress=progress,
                cancelled=cancelled,
            )

    def test_ready_marker_is_written_last_and_progress_has_real_stage_events(self):
        workspace = self.root / "complete"
        events = []
        original = r.write_json
        writes = []

        def record(path, data):
            writes.append(path.name)
            original(path, data)

        with patch.object(r, "write_json", side_effect=record):
            self.fake_pipeline(
                workspace, progress=lambda step, message: events.append((step, message))
            )
        self.assertEqual(writes[-2:], ["comments.json", "manifest.json"])
        self.assertEqual(events[-1][0], 6)
        self.assertTrue((workspace / "pages/1.png").is_file())

    def test_cancel_before_publish_preserves_previews_without_ready_marker(self):
        workspace = self.root / "last-cancel"
        stopped = []

        def progress(step, message):
            if "페이지 미리보기" in message:
                stopped.append(True)

        with self.assertRaises(p.PreparationCancelled):
            self.fake_pipeline(
                workspace, progress=progress, cancelled=lambda: bool(stopped)
            )
        self.assertTrue((workspace / "pages/1.png").is_file())
        self.assertFalse((workspace / "manifest.json").exists())
        self.assertFalse((workspace / "approved-plan.json").exists())


class UsabilityWidgetTests(DesktopWidgetTests):
    def test_welcome_has_one_primary_action_and_hides_advanced_controls(self):
        self.assertEqual(self.app.new_button.winfo_manager(), "pack")
        for button in (
            self.app.add_button,
            self.app.prepare_button,
            self.app.open_button,
        ):
            self.assertFalse(button.winfo_manager())
        self.assertFalse(self.app.details.winfo_manager())
        self.assertFalse(self.app.review_options.winfo_manager())

    def test_compact_footer_version_is_not_clipped_by_utility_buttons(self):
        before = snapshot(self.info.path)
        for width in (700, 880):
            self.root.geometry(f"{width}x680")
            self.root.update()
            label = self.app.version_label
            self.assertGreaterEqual(label.winfo_width(), label.winfo_reqwidth())
            self.assertGreaterEqual(label.winfo_rootx(), self.root.winfo_rootx())
            self.assertLessEqual(
                label.winfo_rootx() + label.winfo_width(),
                self.root.winfo_rootx() + self.root.winfo_width(),
            )
        self.assertEqual(snapshot(self.info.path), before)
        self.assertFalse(self.app.session.running)

    def test_folder_selection_checks_automatically_without_starting_native(self):
        with (
            tempfile.TemporaryDirectory() as temp,
            patch.object(d.filedialog, "askdirectory", return_value=temp),
            patch.object(p, "prepare_project") as prepare,
        ):
            self.app.choose_folder()
            self.wait()
            if self.app.project is None:
                raise AssertionError("Synthetic folder did not open")
            self.assertEqual(self.app.project.state, "empty")
            prepare.assert_not_called()
            self.assertEqual(snapshot(Path(temp)), {})

    def test_document_selection_copies_and_shows_only_next_confirmation_action(self):
        with tempfile.TemporaryDirectory() as temp:
            source = Path(temp) / "SYNTHETIC.hwp"
            source.write_bytes(b"NOT A REAL HWP")
            with (
                patch.dict(os.environ, {"LOCALAPPDATA": temp}),
                patch.object(d.filedialog, "askopenfilename", return_value=str(source)),
                patch.object(
                    r,
                    "hwp_audit",
                    side_effect=lambda path: {"sha256": r.file_hash(path)},
                ),
                patch.object(p, "prepare_project") as prepare,
            ):
                self.app.new_document()
                self.wait()
                prepare.assert_not_called()
            if self.app.project is None:
                raise AssertionError("Synthetic source did not stage")
            self.assertEqual(self.app.project.state, "staged")
            self.assertEqual(self.app.prepare_button.winfo_manager(), "pack")
            self.assertFalse(self.app.new_button.winfo_manager())
            self.assertEqual(source.read_bytes(), b"NOT A REAL HWP")

    def test_cancelled_picker_does_not_create_job_or_replace_current_state(self):
        with (
            patch.object(d.filedialog, "askopenfilename", return_value=""),
            patch.object(p, "new_project") as create,
        ):
            self.app.new_document()
            create.assert_not_called()
        self.assertFalse(self.app.busy)
        self.assertIsNone(self.app.project)

    def test_progress_event_is_nonterminal_and_never_grants_ready_state(self):
        self.app.busy = self.app.preparing = True
        self.app.events.put(("progress", (2, "SYNTHETIC STEP")))
        self.app.poll()
        self.assertTrue(self.app.busy)
        self.assertTrue(self.app.preparing)
        self.assertIn("SYNTHETIC STEP", self.app.status.get())
        self.assertEqual(float(self.app.progress["value"]), 2)
        self.assertIsNone(self.app.info)
        self.app.busy = self.app.preparing = False

    def test_safety_stop_only_sets_event_after_confirmation_never_closes_process_or_deletes(
        self,
    ):
        with tempfile.TemporaryDirectory() as temp:
            file = Path(temp) / "SYNTHETIC-PARTIAL"
            file.write_text("KEEP")
            self.app.busy = self.app.preparing = True
            with patch.object(d.messagebox, "askyesno", return_value=False):
                self.app.cancel_preparation()
            self.assertFalse(self.app.cancel_requested.is_set())
            with (
                patch.object(d.messagebox, "askyesno", return_value=True),
                patch.object(self.app.session, "stop") as stop,
            ):
                self.app.cancel_preparation()
                stop.assert_not_called()
            self.assertTrue(self.app.cancel_requested.is_set())
            self.assertEqual(file.read_text(), "KEEP")
            self.app.busy = self.app.preparing = False

    def test_stale_progress_after_failure_does_not_restore_busy_or_ready(self):
        self.app.events.put(
            ("error", ("ValueError: SYNTHETIC roundtrip failure", None))
        )
        self.app.poll()
        self.app.events.put(("progress", (6, "SYNTHETIC STALE DONE")))
        self.app.poll()
        self.assertFalse(self.app.busy)
        self.assertIsNone(self.app.info)
        self.assertIn("통과하지", self.app.error_hint.get())
        self.assertNotIn("STALE", self.app.status.get())

    def test_details_toggle_does_not_check_files_or_start_server(self):
        with patch.object(d, "inspect_workspace") as inspect:
            self.app.toggle_details()
            self.assertTrue(self.app.details.winfo_manager())
            self.app.toggle_details()
            self.assertFalse(self.app.details.winfo_manager())
            inspect.assert_not_called()
        self.assertFalse(self.app.session.running)

    def test_thread_launch_failure_unlocks_ui_without_running_operation(self):
        operation = Mock()
        with patch.object(
            d.threading.Thread, "start", side_effect=RuntimeError("SYNTHETIC capacity")
        ):
            self.app.task("SYNTHETIC", operation)
            self.wait()
        operation.assert_not_called()
        self.assertFalse(self.app.busy)
        self.assertIn("capacity", self.app.last_error)
        self.assertEqual(str(self.app.new_button["state"]), "normal")

    def test_new_project_is_blocked_during_active_review_or_busy_task(self):
        self.app.busy = True
        with patch.object(d.filedialog, "askopenfilename") as picker:
            self.app.new_document()
            picker.assert_not_called()
        self.app.busy = False
        session = Mock()
        session.running = True
        session.stopping = False
        self.app.session = session
        with patch.object(d.filedialog, "askopenfilename") as picker:
            self.app.new_document()
            picker.assert_not_called()


def load_tests(loader, tests, pattern):
    suite = unittest.TestSuite(loader.loadTestsFromTestCase(UsabilityBackendTests))
    for name in UsabilityWidgetTests.__dict__:
        if name.startswith("test_"):
            suite.addTest(UsabilityWidgetTests(name))
    return suite


if __name__ == "__main__":
    unittest.main()
