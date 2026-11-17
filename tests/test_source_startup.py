"""Synthetic byte fixtures only: never download, compile, run dotnet or open a GUI."""

from contextlib import redirect_stderr, redirect_stdout
import io
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import build_native as builder
import hwp_native_payload as payload
import start_source as startup
from test_native_payload import write_runtime


class SourceStartupTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name).resolve() / "source"
        self.root.mkdir()
        repository = Path(startup.__file__).resolve().parents[1]
        for name in payload.NATIVE_SOURCE_FILES:
            target = self.root / name
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(repository / name, target)
        self.dotnet = self.root.parent / "selected-sdk/dotnet.exe"
        self.dotnet.parent.mkdir()
        self.dotnet.write_bytes(b"SYNTHETIC SDK: MUST NEVER EXECUTE")
        self.sdk = Mock(return_value=self.dotnet)
        self.addCleanup(patch.stopall)
        patch.dict(sys.modules, {"hwp_source_sdk": SimpleNamespace(ensure_sdk=self.sdk)}).start()
        patch.object(startup, "__file__", str(self.root / "scripts/start_source.py")).start()
        self.child = patch.object(startup.subprocess, "run", side_effect=self.build_child).start()
        self.gui = patch.object(startup.subprocess, "Popen", side_effect=AssertionError("No GUI is permitted")).start()
        self.marker = self.root / "build" / startup.PENDING_NAME
        self.pointer = self.root / "build/native-current.json"
        self.stdout = io.StringIO()
        self.stderr = io.StringIO()

    def make_payload(self, directory):
        directory.parent.mkdir(parents=True, exist_ok=True)
        write_runtime(directory, self.root)
        manifest = {
            "contract": payload.CONTRACT,
            "version": payload.APP_VERSION,
            "framework_dependent": True,
            "target_framework": "net10.0-windows",
            "runtime_requirement": dict(payload.RUNTIME_REQUIREMENT),
            "files_sha256": {name: payload._sha256(directory / name) for name in payload.PAYLOAD_FILES},
            "source_sha256": payload.source_hashes(self.root),
        }
        (directory / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
        payload.verify_payload(directory, source_root=self.root)
        return directory

    def register(self):
        directory = self.make_payload(self.root / "build/existing/native-ui")
        builder._register_source(self.root, directory, replace=False)
        return directory

    def build_child(self, command, **kwargs):
        self.assertTrue(self.marker.is_file())
        self.assertFalse(self.pointer.exists())
        destination = Path(command[command.index("--output") + 1])
        self.assertTrue(destination.is_relative_to(self.root / "build"))
        self.assertEqual(destination.name, "native-ui")
        self.assertFalse(destination.exists())
        kwargs["stdout"].write(b"SYNTHETIC BUILDER DIAGNOSTIC\n")
        self.make_payload(destination)
        return subprocess.CompletedProcess(command, 0)

    def main(self, *arguments):
        with redirect_stdout(self.stdout), redirect_stderr(self.stderr):
            return startup.main(list(arguments))

    def snapshot(self):
        return {file.relative_to(self.root).as_posix(): file.read_bytes()
                for file in self.root.rglob("*") if file.is_file()}

    def assert_no_preparation(self):
        self.sdk.assert_not_called()
        self.child.assert_not_called()
        self.gui.assert_not_called()

    def test_missing_build_prepares_registers_verifies_and_removes_only_owned_marker(self):
        before_environment = dict(os.environ)
        self.assertFalse((self.root / "build").exists())
        directory = startup.prepare_native(self.root)
        self.assertEqual(payload._source_payload(self.root), directory)
        self.assertEqual(payload.verify_payload(directory, source_root=self.root)["source_sha256"],
                         payload.source_hashes(self.root))
        self.assertEqual(directory.parent.parent, self.root / "build")
        self.assertFalse(self.marker.exists())
        self.gui.assert_not_called()
        self.assertEqual(dict(os.environ), before_environment)

    def test_unrelated_build_artifacts_are_not_treated_as_partial_setup_or_changed(self):
        unrelated = self.root / "build/manual-output/keep.bin"
        unrelated.parent.mkdir(parents=True)
        unrelated.write_bytes(b"USER OWNED OUTPUT")
        directory = startup.prepare_native(self.root)
        self.assertNotEqual(directory.parent, unrelated.parent)
        self.assertEqual(unrelated.read_bytes(), b"USER OWNED OUTPUT")

    def test_valid_registration_reuses_without_even_importing_sdk(self):
        directory = self.register()
        before = self.snapshot()
        with patch.dict(sys.modules, {"hwp_source_sdk": None}):
            self.assertEqual(startup.prepare_native(self.root), directory)
            self.assertEqual(startup.prepare_native(self.root), directory)
        self.assertEqual(self.snapshot(), before)
        self.assert_no_preparation()

    def test_existing_pending_blocks_even_an_otherwise_valid_registration(self):
        self.register()
        self.marker.write_bytes(b"UNFINISHED OTHER OWNER")
        before = self.snapshot()
        with self.assertRaises(RuntimeError):
            startup.prepare_native(self.root)
        self.assertEqual(self.snapshot(), before)
        self.assert_no_preparation()

    def test_pending_directory_is_not_removed_or_replaced(self):
        self.marker.mkdir(parents=True)
        evidence = self.marker / "evidence.bin"
        evidence.write_bytes(b"PARTIAL STATE")
        with self.assertRaises(RuntimeError):
            startup.prepare_native(self.root)
        self.assertEqual(evidence.read_bytes(), b"PARTIAL STATE")
        self.assert_no_preparation()

    def test_corrupt_duplicate_or_escaping_pointer_never_triggers_repair(self):
        self.pointer.parent.mkdir()
        cases = (
            b"not JSON",
            b'{"contract":"hwp-native-current-v1","path":"build/absent/native-ui","path":"build/other/native-ui"}',
            json.dumps({"contract": payload.CURRENT_CONTRACT, "path": "build/../outside/native-ui"}).encode(),
            json.dumps({"contract": payload.CURRENT_CONTRACT, "path": "build/missing/native-ui"}).encode(),
        )
        for content in cases:
            with self.subTest(pointer=content):
                self.pointer.write_bytes(content)
                before = self.snapshot()
                with self.assertRaises((ValueError, FileNotFoundError)):
                    startup.prepare_native(self.root)
                self.assertEqual(self.snapshot(), before)
                self.assertFalse(self.marker.exists())
        self.assert_no_preparation()

    def test_hardlinked_source_pointer_is_not_reused_or_repaired(self):
        self.register()
        alias = self.pointer.with_name("pointer-alias.json")
        try:
            os.link(self.pointer, alias)
        except (OSError, NotImplementedError) as error:
            self.skipTest("Hard links unavailable: " + str(error))
        before = self.snapshot()
        self.assertEqual(self.main(), 1)
        self.assertEqual(self.snapshot(), before)
        self.assert_no_preparation()

    def test_payload_byte_tamper_preserves_registered_payload_and_blocks_gui(self):
        directory = self.register()
        (directory / "HWPReview.Native.dll").write_bytes(b"TAMPERED")
        before = self.snapshot()
        self.assertEqual(self.main(), 1)
        self.assertEqual(self.snapshot(), before)
        self.assert_no_preparation()

    def test_unlisted_payload_file_blocks_without_rebuild(self):
        directory = self.register()
        (directory / "unexpected.exe").write_bytes(b"UNEXPECTED")
        before = self.snapshot()
        with self.assertRaises(ValueError):
            startup.prepare_native(self.root)
        self.assertEqual(self.snapshot(), before)
        self.assert_no_preparation()

    def test_every_registered_source_byte_change_blocks_without_sdk_or_rebuild(self):
        directory = self.register()
        pointer = self.pointer.read_bytes()
        for name in payload.NATIVE_SOURCE_FILES:
            target = self.root / name
            original = target.read_bytes()
            try:
                target.write_bytes(original + b"\nSOURCE DRIFT")
                with self.subTest(source=name), self.assertRaises(ValueError):
                    startup.prepare_native(self.root)
                self.assertEqual(target.read_bytes(), original + b"\nSOURCE DRIFT")
                self.assertEqual(self.pointer.read_bytes(), pointer)
                self.assertTrue(directory.is_dir())
                self.assertFalse(self.marker.exists())
            finally:
                target.write_bytes(original)
        self.assert_no_preparation()

    def test_exclusive_pending_race_preserves_the_winning_owners_marker(self):
        new_file = startup._new_file

        def competing_owner(path):
            if path == self.marker:
                path.write_bytes(b"OTHER OWNER WON")
            return new_file(path)

        with patch.object(startup, "_new_file", side_effect=competing_owner):
            with self.assertRaises(FileExistsError):
                startup.prepare_native(self.root)
        self.assertEqual(self.marker.read_bytes(), b"OTHER OWNER WON")
        self.assert_no_preparation()

    def test_sdk_failure_retains_partial_archive_and_prevents_subsequent_retry_or_gui(self):
        archive = self.root / "build/source-tools/partial.zip"

        def fail_sdk(_root):
            archive.parent.mkdir()
            archive.write_bytes(b"INCOMPLETE DOWNLOAD")
            raise ValueError("SYNTHETIC SDK FAILURE")

        self.sdk.side_effect = fail_sdk
        self.assertEqual(self.main(), 1)
        before = self.snapshot()
        self.assertEqual(self.main(), 1)
        self.assertEqual(self.snapshot(), before)
        self.assertEqual(archive.read_bytes(), b"INCOMPLETE DOWNLOAD")
        self.assertTrue(self.marker.is_file())
        self.assertEqual(self.sdk.call_count, 1)
        self.child.assert_not_called()
        self.gui.assert_not_called()

    def test_failed_builder_retains_diagnostic_and_partial_output_without_retry(self):
        def fail(command, **kwargs):
            destination = Path(command[-1])
            destination.mkdir()
            (destination / "partial.dll").write_bytes(b"PARTIAL")
            kwargs["stdout"].write(b"SYNTHETIC FAILURE DIAGNOSTIC")
            raise subprocess.CalledProcessError(1, command)

        self.child.side_effect = fail
        self.assertEqual(self.main(), 1)
        content = json.loads(self.marker.read_text(encoding="utf-8"))
        destination = self.root / content["path"]
        self.assertEqual((destination / "partial.dll").read_bytes(), b"PARTIAL")
        self.assertEqual((destination.parent / "builder.log").read_bytes(), b"SYNTHETIC FAILURE DIAGNOSTIC")
        self.assertFalse(self.pointer.exists())
        before = self.snapshot()
        self.assertEqual(self.main(), 1)
        self.assertEqual(self.snapshot(), before)
        self.assertEqual(self.sdk.call_count, 1)
        self.assertEqual(self.child.call_count, 1)
        self.gui.assert_not_called()

    def test_builder_timeout_retains_pending_and_cannot_launch_gui(self):
        self.child.side_effect = subprocess.TimeoutExpired(["SYNTHETIC"], 1)
        self.assertEqual(self.main(), 1)
        self.assertTrue(self.marker.is_file())
        self.assertFalse(self.pointer.exists())
        self.assertEqual(self.child.call_count, 1)
        self.gui.assert_not_called()

    def test_success_exit_without_complete_payload_is_not_registered(self):
        self.child.side_effect = lambda command, **kwargs: subprocess.CompletedProcess(command, 0)
        self.assertEqual(self.main(), 1)
        self.assertTrue(self.marker.is_file())
        self.assertFalse(self.pointer.exists())
        self.gui.assert_not_called()

    def test_source_drift_during_sdk_selection_stops_before_build(self):
        source = self.root / "native/HWPReview.Native/Program.cs"

        def drift(_root):
            source.write_bytes(source.read_bytes() + b"\nDURING SDK")
            return self.dotnet

        self.sdk.side_effect = drift
        self.assertEqual(self.main(), 1)
        self.assertTrue(source.read_bytes().endswith(b"DURING SDK"))
        self.assertTrue(self.marker.exists())
        self.assertFalse(self.pointer.exists())
        self.child.assert_not_called()
        self.gui.assert_not_called()

    def test_source_drift_during_build_stops_before_registration(self):
        def drift(command, **kwargs):
            result = self.build_child(command, **kwargs)
            source = self.root / "native/HWPReview.Native/Program.cs"
            source.write_bytes(source.read_bytes() + b"\nDURING BUILD")
            return result

        self.child.side_effect = drift
        self.assertEqual(self.main(), 1)
        self.assertTrue(self.marker.exists())
        self.assertFalse(self.pointer.exists())
        self.gui.assert_not_called()

    def test_build_payload_tamper_is_verified_before_publication(self):
        def tamper(command, **kwargs):
            result = self.build_child(command, **kwargs)
            (Path(command[-1]) / "HWPReview.Native.dll").write_bytes(b"TAMPERED AFTER BUILD")
            return result

        self.child.side_effect = tamper
        self.assertEqual(self.main(), 1)
        self.assertTrue(self.marker.exists())
        self.assertFalse(self.pointer.exists())
        self.gui.assert_not_called()

    def test_concurrent_registration_is_not_overwritten(self):
        other = self.root / "build/manual-winner/native-ui"

        def race(command, **kwargs):
            result = self.build_child(command, **kwargs)
            self.make_payload(other)
            builder._register_source(self.root, other, replace=False)
            return result

        self.child.side_effect = race
        self.assertEqual(self.main(), 1)
        self.assertEqual(payload._source_payload(self.root), other)
        self.assertTrue(self.marker.exists())
        self.assertEqual(self.main(), 1)
        self.assertEqual(self.child.call_count, 1)
        self.gui.assert_not_called()

    def test_source_change_after_publication_fails_final_verification_and_retains_marker(self):
        register = builder._register_source

        def drift(root, directory, *, replace):
            register(root, directory, replace=replace)
            source = root / "native/HWPReview.Native/Program.cs"
            source.write_bytes(source.read_bytes() + b"\nAFTER PUBLICATION")

        with patch.object(builder, "_register_source", side_effect=drift):
            self.assertEqual(self.main(), 1)
        self.assertTrue(self.pointer.is_file())
        self.assertTrue(self.marker.is_file())
        self.gui.assert_not_called()

    def test_changed_pending_marker_is_never_removed_even_after_valid_publication(self):
        def tamper(command, **kwargs):
            result = self.build_child(command, **kwargs)
            self.marker.write_bytes(b"CHANGED OWNERSHIP EVIDENCE")
            return result

        self.child.side_effect = tamper
        self.assertEqual(self.main(), 1)
        self.assertEqual(self.marker.read_bytes(), b"CHANGED OWNERSHIP EVIDENCE")
        self.assertTrue(self.pointer.exists())
        payload._source_payload(self.root)
        self.assertEqual(self.main(), 1)
        self.assertEqual(self.child.call_count, 1)
        self.gui.assert_not_called()

    def test_prepare_only_finishes_verified_setup_without_gui(self):
        self.assertEqual(self.main("--prepare-only"), 0)
        self.assertFalse(self.marker.exists())
        payload._source_payload(self.root)
        self.gui.assert_not_called()


    def test_missing_existing_pythonw_fails_without_an_alternate_app_executor(self):
        self.register()
        self.assertEqual(self.main(), 1)
        self.assert_no_preparation()

    def test_frozen_mode_rejects_source_preparation_before_any_source_io(self):
        absent = self.root.parent / "must-not-be-created"
        before = self.snapshot()
        with patch.object(sys, "frozen", True, create=True):
            with self.assertRaises(RuntimeError):
                startup.prepare_native(absent)
            self.assertEqual(self.main("--prepare-only"), 1)
            self.assertEqual(self.main(), 1)
        self.assertFalse(absent.exists())
        self.assertEqual(self.snapshot(), before)
        self.assert_no_preparation()


if __name__ == "__main__":
    unittest.main()
