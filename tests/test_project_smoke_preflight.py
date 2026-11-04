"""Preparation lab opt-ins/candidate pins only. No source authoring, COM or EXE."""

import importlib
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
m = importlib.import_module("native_project_smoke")
import build_exe as build


class SyntheticAuthoringBoundary(Exception):
    pass


class PreparationLabPreflightTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name).resolve()
        (self.root / "work").mkdir()
        self.lab = self.root / "work/toy-desktop-SYNTHETIC"
        self.creator = Mock(side_effect=SyntheticAuthoringBoundary("NO NATIVE"))
        for patcher in (
            patch.object(m, "ROOT", self.root),
            patch.object(
                m, "registered_security_module", return_value={"SYNTHETIC": True}
            ),
            patch.object(m, "hwp_inventory", return_value=[]),
            patch.object(m, "create_source", self.creator),
            patch.object(
                m.p, "prepare_project", side_effect=AssertionError("No preparation")
            ),
            patch.object(build, "SOURCE_FILES", ("scripts/app.py",)),
            patch.object(build, "ASSETS", ("web/view.html",)),
        ):
            patcher.start()
            self.addCleanup(patcher.stop)
        self.exe = self.root / "candidate/HWPReview.exe"
        self.exe.parent.mkdir()
        self.exe.write_bytes(b"SYNTHETIC NOT AN EXECUTABLE")
        for name in (*build.SOURCE_FILES, *build.ASSETS):
            path = self.root / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(b"SYNTHETIC INPUT")
        self.metadata = {
            "executable_sha256": m.r.file_hash(self.exe),
            "source_sha256": {
                name: m.r.file_hash(self.root / name) for name in build.SOURCE_FILES
            },
            "assets_sha256": {
                name: m.r.file_hash(self.root / name) for name in build.ASSETS
            },
        }
        self.save_metadata()

    def save_metadata(self):
        (self.exe.parent / "BUILD.json").write_text(
            json.dumps(self.metadata), encoding="utf-8"
        )

    def invoke(self, executable=None):
        return m.run(self.lab, executable, run_native=True, toy_only=True)

    def test_nonliteral_optins_fail_before_path_security_inventory_or_source(self):
        for options in (
            {},
            {"run_native": True},
            {"toy_only": True},
            {"run_native": 1, "toy_only": True},
            {"run_native": True, "toy_only": 1},
        ):
            with self.subTest(options=options), patch.object(m, "real_path") as paths:
                with self.assertRaisesRegex(ValueError, "opt-ins"):
                    m.run(self.lab, **options)
                paths.assert_not_called()
        m.registered_security_module.assert_not_called()
        m.hwp_inventory.assert_not_called()
        self.creator.assert_not_called()
        self.assertFalse(self.lab.exists())

    def test_frozen_call_is_denied_before_even_path_validation(self):
        with (
            patch.object(m.sys, "frozen", True, create=True),
            patch.object(m, "real_path") as paths,
        ):
            with self.assertRaises(ValueError):
                self.invoke()
            paths.assert_not_called()
        self.creator.assert_not_called()

    def test_foreign_existing_nested_and_linked_labs_do_not_reach_native(self):
        existing = self.root / "work/toy-desktop-EXISTS"
        existing.mkdir()
        for path in (
            existing,
            self.root / "toy-desktop-FOREIGN",
            self.root / "work/other/toy-desktop-NESTED",
            self.root / "work/wrong-prefix",
        ):
            with self.subTest(path=path), self.assertRaises(ValueError):
                m.run(path, run_native=True, toy_only=True)
        original = Path.is_symlink
        linked = self.root / "work/toy-desktop-LINK"
        with patch.object(
            Path, "is_symlink", lambda path: path == linked or original(path)
        ):
            with self.assertRaises(ValueError):
                m.run(linked, run_native=True, toy_only=True)
        self.creator.assert_not_called()
        m.hwp_inventory.assert_not_called()

    def test_existing_hwp_blocks_before_lab_creation(self):
        m.hwp_inventory.return_value = [{"pid": 100, "creation_ms": 1}]
        with self.assertRaisesRegex(RuntimeError, "Existing"):
            self.invoke()
        self.creator.assert_not_called()
        self.assertFalse(self.lab.exists())

    def test_explicit_source_run_stops_at_fake_authoring_boundary_preserving_report(
        self,
    ):
        with self.assertRaises(SyntheticAuthoringBoundary):
            self.invoke()
        self.creator.assert_called_once_with(self.lab / "source")
        report = json.loads(
            (self.lab / "TOY-ONLY-project-verification.json").read_text(
                encoding="utf-8"
            )
        )
        self.assertEqual(report["status"], "blocked")
        self.assertIn("SyntheticAuthoringBoundary", report["error"])
        m.p.prepare_project.assert_not_called()

    def test_exact_candidate_source_assets_reach_only_fake_boundary(self):
        with self.assertRaises(SyntheticAuthoringBoundary):
            self.invoke(self.exe)
        self.creator.assert_called_once()
        m.p.prepare_project.assert_not_called()

    def test_changed_candidate_or_current_inputs_stop_before_process_inventory(self):
        for name in (
            self.exe,
            *(self.root / entry for entry in (*build.SOURCE_FILES, *build.ASSETS)),
        ):
            before = name.read_bytes()
            with self.subTest(file=name.name):
                name.write_bytes(b"SYNTHETIC CHANGED")
                with self.assertRaises(ValueError):
                    self.invoke(self.exe)
                self.assertFalse(self.lab.exists())
                self.creator.assert_not_called()
                m.hwp_inventory.assert_not_called()
                name.write_bytes(before)

    def test_missing_extra_or_invalid_snapshot_records_cannot_read_other_paths(self):
        original = json.loads(json.dumps(self.metadata))
        variants = (
            {"source_sha256": {}},
            {
                "source_sha256": {
                    **self.metadata["source_sha256"],
                    "../UNDECLARED.txt": "0" * 64,
                }
            },
            {"assets_sha256": {build.ASSETS[0]: "NOT A HASH"}},
        )
        for delta in variants:
            with self.subTest(delta=delta):
                self.metadata = {**original, **delta}
                self.save_metadata()
                with self.assertRaises(ValueError):
                    self.invoke(self.exe)
                self.creator.assert_not_called()
                m.hwp_inventory.assert_not_called()
                self.assertFalse(self.lab.exists())


if __name__ == "__main__":
    unittest.main()
