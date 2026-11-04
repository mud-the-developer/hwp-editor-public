"""Owned synthetic GUI-host failure lifecycle, no browser/consent/Native/model."""

import json
from pathlib import Path
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
import hwp_report_smoke as smoke


class ReportGuiFailureTests(unittest.TestCase):
    def test_first_failure_cannot_be_replaced_by_late_ready_or_manual_close(self):
        try:
            root = tk.Tk()
        except tk.TclError as error:
            self.skipTest("Tk display unavailable; not a visual pass: " + str(error))
        root.withdraw()

        def destroy_owned():
            try:
                root.destroy()
            except tk.TclError:
                pass  # The ordinary app close already destroyed this owned root.

        self.addCleanup(destroy_owned)
        release = threading.Event()
        self.addCleanup(release.set)
        original_create, original_app = projects.create, desktop.DesktopApp
        owners, errors = [], []
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary) / "fresh-host"

            def delayed_create(*args, **kwargs):
                if not release.wait(5):
                    raise RuntimeError("Synthetic release callback did not run")
                return original_create(*args, **kwargs)

            class OwnedApp(original_app):
                def __init__(self, owner, **kwargs):
                    super().__init__(owner, **kwargs)
                    owners.append(self)
                    (directory / "STOP-REQUEST.json").write_text(
                        '{"contract":"INVALID-SYNTHETIC-STOP"}', encoding="utf-8"
                    )
                    deadline = time.monotonic() + 10
                    owner.after(250, release.set)

                    def close_when_idle():
                        if self.busy and time.monotonic() < deadline:
                            owner.after(50, close_when_idle)
                            return
                        if self.busy:
                            errors.append("Late preparation did not settle")
                            owner.quit()
                            return
                        try:
                            self.session.stop(timeout=5)
                            self.close()
                        except Exception as error:
                            errors.append(str(error))
                            owner.quit()

                    owner.after(500, close_when_idle)

            with (
                patch.object(smoke.tk, "Tk", return_value=root),
                patch.object(desktop, "DesktopApp", OwnedApp),
                patch.object(projects, "create", side_effect=delayed_create),
                patch.object(desktop.subprocess, "Popen", side_effect=AssertionError("No process")),
            ):
                smoke.run_gui_host(directory)
            for app in owners:
                app.session.stop(timeout=5)
            self.assertEqual(errors, [])
            receipt = json.loads((directory / "GUI-HOST.json").read_text(encoding="utf-8"))
            self.assertEqual(receipt["status"], "failed_preserved")
            self.assertIn("Invalid owned GUI stop contract", receipt["error"])
            self.assertTrue(receipt["manual_close_observed"])
            self.assertNotIn("url", receipt)
            self.assertFalse(list(directory.rglob("approved-plan.json")))
            self.assertFalse(list(directory.rglob("template-result.json")))


if __name__ == "__main__":
    unittest.main()
