"""Owned synthetic Tk/HTTP flow. Only the local handler writes test approval; no COM."""
from pathlib import Path
import subprocess
import tempfile
import time
import tkinter as tk
import unittest
from unittest.mock import patch
import zipfile

from test_desktop import d, fixture, r


class LegacyWindowTests(unittest.TestCase):
    def test_real_review_window_renders_and_separates_saved_plan_approval_and_apply(self):
        try:
            root = tk.Tk()
        except tk.TclError as error:
            self.skipTest("Tk display unavailable: " + str(error))
        root.withdraw()
        with tempfile.TemporaryDirectory() as temp:
            scope = Path(temp).resolve() / "SYNTHETIC-HWP-ONLY"
            fixture(scope)
            baseline = scope / "baseline.hwpx"
            old, new = "SYNTHETIC AAAA", "SYNTHETIC BBBB"
            with zipfile.ZipFile(baseline, "w") as archive:
                archive.writestr("mimetype", "application/hwp+zip")
                archive.writestr("Contents/section0.xml", f'<sec xmlns:hp="http://www.hancom.co.kr/hwpml/2011/paragraph"><hp:p><hp:run><hp:t>{old}</hp:t></hp:run></hp:p></sec>')
            manifest = r.read_json(scope / "manifest.json")
            anchors, _ = r.index_hwpx(baseline)
            for anchor in anchors:
                anchor.pop("raw")
                anchor.update(page_hint=1, boxes=[[20, 20, 220, 70]])
            manifest.update(anchors=anchors, baseline_hwpx_sha256=r.file_hash(baseline))
            r.write_json(scope / "manifest.json", manifest)
            before = {name: r.file_hash(scope / name) for name in ("baseline.hwpx", "baseline.pdf", "manifest.json")}
            app = d.DesktopApp(root)

            def wait(predicate):
                deadline = time.monotonic() + 10
                while not predicate() and time.monotonic() < deadline:
                    root.update()
                    time.sleep(.01)
                self.assertTrue(predicate(), f"app error: {app.last_error}")

            try:
                with patch.object(subprocess, "Popen", side_effect=AssertionError("No model, browser or COM worker")):
                    app.path.set(str(scope))
                    app.check_workspace()
                    wait(lambda: not app.busy)
                    app.open_button.invoke()
                    wait(lambda: app.review_window is not None and not app.review_window.busy and app.review_window._bitmap is not None)
                    view = app.review_window
                    self.assertGreater(view._bitmap.width(), 100)
                    self.assertIn("1/1쪽", view.page_label.cget("text"))
                    self.assertNotIn("읽고 있습니다", view.status.get())
                    self.assertFalse((scope / "approved-plan.json").exists())
                    view.targets.selection_set(0)
                    view.select_target()
                    wait(lambda: not view.busy)
                    view.edit.insert(0, new)
                    view.stage_button.invoke()
                    view.save_button.invoke()
                    wait(lambda: not view.busy)
                    self.assertIn(new, view.plan_text.get("1.0", "end"))
                    self.assertFalse(view.can_apply())
                    self.assertFalse((scope / "approved-plan.json").exists())
                    self.assertFalse((scope / "runs").exists())
                    view.review_check.invoke()
                    with patch.object(d.messagebox, "askyesno", return_value=False):
                        view.approve_button.invoke()
                    self.assertFalse((scope / "approved-plan.json").exists())
                    with patch.object(d.messagebox, "askyesno", return_value=True):
                        view.approve_button.invoke()
                    wait(lambda: not view.busy)
                    self.assertTrue(view.can_apply(), view.status.get())
                    self.assertTrue((scope / "approved-plan.json").is_file())
                    self.assertFalse((scope / "runs").exists())
                    with patch.object(d.messagebox, "askyesno", return_value=False):
                        view.apply_button.invoke()
                    self.assertFalse((scope / "runs").exists())
                    self.assertEqual(before, {name: r.file_hash(scope / name) for name in before})
                    view.close()
                    self.assertTrue(app.session.running)
                    self.assertIsNone(app.review_window)
            finally:
                app.session.stop()
                for identifier in root.tk.call("after", "info"):
                    root.after_cancel(identifier)
                root.destroy()


if __name__ == "__main__":
    unittest.main()
