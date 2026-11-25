"""Report packaging probe with synthetic runtime identity; no EXE, GUI, model or Native."""

from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import hwp_report_runtime as runtime
import hwp_report_smoke as smoke


class ReportSmokeTests(unittest.TestCase):
    def test_source_invocation_refuses_before_creating_the_fixture(self):
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary) / "fresh-probe"
            with self.assertRaisesRegex(RuntimeError, "requires the actual frozen executable"):
                smoke.readonly_checks(directory)
            self.assertFalse(directory.exists())

    def test_readonly_probe_uses_owned_sessions_and_grants_no_authority(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary).resolve()
            executable = root / "HWPReview.exe"
            executable.write_bytes(b"SYNTHETIC EXECUTABLE IDENTITY ONLY")
            (root / runtime.RULE_FILE).parent.mkdir()
            (root / runtime.RULE_FILE).write_bytes(b"SYNTHETIC PINNED RUNTIME RULES")
            directory = root / "fresh-probe"
            with (
                patch.object(sys, "frozen", True, create=True),
                patch.object(sys, "_MEIPASS", str(root), create=True),
                patch.object(sys, "executable", str(executable)),
                patch.object(runtime, "_windows_scope", return_value=("device-A", "user-A")),
                patch("subprocess.Popen", side_effect=AssertionError("No external process")),
            ):
                result = smoke.readonly_checks(directory)
            self.assertEqual(result, {
                "unsigned_inputs_prepared": True,
                "runtime_and_scope_bound": True,
                "raw_frozen_template_blocked": True,
                "owned_desktop_session_ready": True,
                "unpermitted_draft_blocked": True,
                "document_workspace_ready": True,
                "unpermitted_request_blocked": True,
                "no_model_native_or_permission": True,
            })
            for name in (
                "approved-plan.json", "draft-permission.json", "template-result.json",
                "artifact-*-acceptance.json",
            ):
                with self.subTest(name=name):
                    self.assertFalse(list(directory.rglob(name)))


if __name__ == "__main__":
    unittest.main()
