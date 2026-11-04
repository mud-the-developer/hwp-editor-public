"""Synthetic byte/runtime bindings; no real device read, model or Native calls."""

import hashlib
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import hwp_report_runtime as runtime


class ReportRuntimeTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.scripts = self.root / "scripts"
        self.scripts.mkdir()
        for name in runtime.SOURCE_FILES:
            (self.scripts / name).write_bytes(b"# synthetic source bytes: " + name.encode())
        for name in runtime.NATIVE_SOURCE_FILES:
            path = self.root / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(b"# synthetic native input bytes: " + name.encode())
        (self.root / "docs").mkdir()
        self.rule = self.root / runtime.RULE_FILE
        self.rule.write_bytes(b"Synthetic fixed runtime rules")
        self.exe = self.root / "HWPReview.exe"
        self.exe.write_bytes(b"Synthetic executable bytes, never executed")
        for mocked in (
            patch.object(runtime.resources, "resource_root", return_value=self.root),
            patch.object(runtime, "__file__", str(self.scripts / "hwp_report_runtime.py")),
        ):
            mocked.start()
            self.addCleanup(mocked.stop)

    def test_source_identity_binds_exact_engine_and_rule_bytes(self):
        with patch.object(runtime.resources, "frozen", return_value=False):
            original = runtime.identity()
            for name in ("hwp_report_harness.py", "hwp_template_draft.py", "hwp_report_runtime.py"):
                with self.subTest(engine=name):
                    path = self.scripts / name
                    before, metadata = path.read_bytes(), path.stat()
                    # A comment-only, same-size change with the old timestamps
                    # still invalidates proof. Neither AST nor stat is identity.
                    path.write_bytes(b"# CHANGED" + before[9:])
                    os.utime(path, ns=(metadata.st_atime_ns, metadata.st_mtime_ns))
                    self.assertNotEqual(runtime.identity(), original)
                    path.write_bytes(before)
            self.rule.write_bytes(b"Changed fixed runtime rules")
            self.assertNotEqual(runtime.identity(), original)
            self.assertNotIn("local_scope_sha256", original)
            self.assertNotIn("executable_sha256", original)

    def test_native_ui_or_host_change_invalidates_source_permission_identity(self):
        with patch.object(runtime.resources, "frozen", return_value=False):
            original = runtime.identity()
            for name in runtime.NATIVE_SOURCE_FILES:
                with self.subTest(runtime_input=name):
                    path = self.root / name
                    before, metadata = path.read_bytes(), path.stat()
                    path.write_bytes(b"!" + before[1:])
                    os.utime(path, ns=(metadata.st_atime_ns, metadata.st_mtime_ns))
                    self.assertNotEqual(runtime.identity(), original)
                    path.write_bytes(before)

    def test_expanded_runtime_inputs_still_allow_bounded_document_intake(self):
        import hwp_report_project as projects
        from hwp_synthetic_report import write_inputs

        profile, _ = write_inputs(self.root / "unsigned")
        source = Path(profile["jobs"][0]["source"])
        with patch.object(runtime.resources, "frozen", return_value=False):
            info = projects.new_document(source, base=self.root / "projects")
            self.assertTrue(info.gate_passed, info.issues)
            asset = self.root / next(name for name in runtime.NATIVE_SOURCE_FILES if name.endswith("files.svg"))
            asset.write_bytes(asset.read_bytes() + b"CHANGED BYTE")
            self.assertFalse(projects.inspect(info.root).gate_passed)

    def test_frozen_identity_uses_actual_exe_not_missing_pyz_sources(self):
        with (
            patch.object(runtime.resources, "frozen", return_value=True),
            patch.object(sys, "executable", str(self.exe)),
            patch.object(runtime, "__file__", str(self.root / "missing-PYZ-module.py")),
            patch.object(runtime, "_windows_scope", return_value=("device-A", "user-A")),
        ):
            original = runtime.identity()
            self.assertEqual(original["origin"], "frozen")
            self.assertEqual(
                original["executable_sha256"],
                hashlib.sha256(self.exe.read_bytes()).hexdigest(),
            )
            self.assertNotIn("source_sha256", original)
            self.assertNotIn("device-A", str(original))
            self.assertNotIn("user-A", str(original))
            before, metadata = self.exe.read_bytes(), self.exe.stat()
            self.exe.write_bytes(b"X" + before[1:])
            os.utime(self.exe, ns=(metadata.st_atime_ns, metadata.st_mtime_ns))
            self.assertNotEqual(runtime.identity(), original)

    def test_frozen_device_and_user_changes_invalidate_local_identity(self):
        with (
            patch.object(runtime.resources, "frozen", return_value=True),
            patch.object(sys, "executable", str(self.exe)),
            patch.object(runtime, "_windows_scope", return_value=("device-A", "user-A")),
        ):
            original = runtime.identity()
            for scope in (("device-B", "user-A"), ("device-A", "user-B")):
                with self.subTest(scope=scope), patch.object(
                    runtime, "_windows_scope", return_value=scope
                ):
                    self.assertNotEqual(runtime.identity(), original)
            self.rule.write_bytes(b"Changed packaged rules")
            self.assertNotEqual(runtime.identity(), original)

    def test_frozen_missing_scope_has_no_invented_or_source_fallback(self):
        with (
            patch.object(runtime.resources, "frozen", return_value=True),
            patch.object(sys, "executable", str(self.exe)),
            patch.object(runtime, "_windows_scope", side_effect=OSError("denied")),
            self.assertRaises(ValueError),
        ):
            runtime.identity()

    def test_frozen_missing_executable_or_rules_is_not_certified(self):
        with (
            patch.object(runtime.resources, "frozen", return_value=True),
            patch.object(sys, "executable", str(self.root / "absent.exe")),
            patch.object(runtime, "_windows_scope", return_value=("device-A", "user-A")),
            self.assertRaises(ValueError),
        ):
            runtime.identity()
        self.rule.unlink()
        with (
            patch.object(runtime.resources, "frozen", return_value=True),
            patch.object(sys, "executable", str(self.exe)),
            patch.object(runtime, "_windows_scope", return_value=("device-A", "user-A")),
            self.assertRaises(ValueError),
        ):
            runtime.identity()


if __name__ == "__main__":
    unittest.main()
