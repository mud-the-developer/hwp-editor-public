"""Owned source-environment boundaries; no network, model, registry or document use."""

import hashlib
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch
import venv

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import prepare_source as setup


@unittest.skipUnless(sys.platform == "win32", "Windows source launch environment")
class SourceEnvironmentTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name).resolve()
        self.requirements = self.root / "requirements.txt"

    def installed_environment(self, path=None):
        environment = path or self.root / ".venv"
        venv.EnvBuilder(with_pip=True, symlinks=False).create(environment)
        result = subprocess.run(
            [str(environment / "Scripts/python.exe"), "-I", "-B", "-c",
             "import importlib.metadata; print(importlib.metadata.version('pip'))"],
            check=True, capture_output=True, text=True, timeout=60,
        )
        self.requirements.write_text("pip==" + result.stdout.strip() + "\n", encoding="utf-8")
        return environment

    def snapshot(self):
        return {
            path.relative_to(self.root).as_posix(): hashlib.sha256(path.read_bytes()).hexdigest()
            for path in self.root.rglob("*") if path.is_file()
        }

    def test_unpinned_requirements_refuse_before_creating_environment(self):
        self.requirements.write_text("olefile>=0.47\n", encoding="utf-8")
        before = self.snapshot()
        with self.assertRaises(setup.SetupError):
            setup.prepare(self.root)
        self.assertFalse((self.root / ".venv").exists())
        self.assertEqual(self.snapshot(), before)

    def test_incomplete_environment_is_preserved_without_repair(self):
        self.requirements.write_text("olefile==0.47\n", encoding="utf-8")
        environment = self.root / ".venv"
        environment.mkdir()
        (environment / "owner.txt").write_bytes(b"Existing incomplete environment: preserve")
        before = self.snapshot()
        with (
            patch.object(setup.venv, "EnvBuilder", side_effect=AssertionError("No environment repair")),
            self.assertRaises(setup.SetupError),
        ):
            setup.prepare(self.root)
        self.assertEqual(self.snapshot(), before)

    def test_matching_environment_reuse_has_no_package_or_file_mutation(self):
        self.installed_environment()
        before = self.snapshot()
        with patch.object(setup.venv, "EnvBuilder", side_effect=AssertionError("No environment recreation")):
            result = setup.prepare(self.root)
        self.assertEqual(result, "reused")
        self.assertEqual(self.snapshot(), before)

    def test_existing_version_mismatch_refuses_without_upgrade(self):
        self.installed_environment()
        self.requirements.write_text("pip==0.0.0\n", encoding="utf-8")
        before = self.snapshot()
        with (
            patch.object(setup.venv, "EnvBuilder", side_effect=AssertionError("No environment repair")),
            self.assertRaises(subprocess.CalledProcessError),
        ):
            setup.prepare(self.root)
        self.assertEqual(self.snapshot(), before)

    def test_failed_install_keeps_partial_environment_and_never_retries(self):
        self.requirements.write_text("olefile==0.47\n", encoding="utf-8")
        original_run = subprocess.run
        install_attempts = []

        def fail_install(command, **kwargs):
            if "pip" in command and "install" in command:
                install_attempts.append(command)
                raise subprocess.CalledProcessError(7, command)
            return original_run(command, **kwargs)

        with patch.object(setup.subprocess, "run", side_effect=fail_install):
            with self.assertRaises(subprocess.CalledProcessError):
                setup.prepare(self.root)
            self.assertTrue((self.root / ".venv/pyvenv.cfg").is_file())
            partial = self.snapshot()
            with self.assertRaises(subprocess.CalledProcessError):
                setup.prepare(self.root)
        self.assertEqual(len(install_attempts), 1)
        self.assertEqual(self.snapshot(), partial)
        self.assertEqual(self.requirements.read_text(encoding="utf-8"), "olefile==0.47\n")

    def test_junction_environment_cannot_redirect_source_setup(self):
        target = self.installed_environment(self.root / "owned-target")
        link = self.root / ".venv"
        result = subprocess.run(
            [os.environ.get("COMSPEC", "cmd.exe"), "/d", "/c", "mklink", "/J", str(link), str(target)],
            capture_output=True, timeout=30,
        )
        self.assertEqual(result.returncode, 0, result.stderr.decode(errors="replace"))
        self.addCleanup(os.rmdir, link)
        target_before = {path.relative_to(target).as_posix(): hashlib.sha256(path.read_bytes()).hexdigest()
                         for path in target.rglob("*") if path.is_file()}
        with self.assertRaises(setup.SetupError):
            setup.prepare(self.root)
        self.assertTrue(link.is_dir())
        self.assertEqual(target_before, {path.relative_to(target).as_posix(): hashlib.sha256(path.read_bytes()).hexdigest()
                                        for path in target.rglob("*") if path.is_file()})


if __name__ == "__main__":
    unittest.main()
