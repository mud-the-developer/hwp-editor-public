"""Pinned synthetic reference copies only: TEMP bytes, NO SDK/COM/Native."""

import hashlib
import importlib
import json
from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
m = importlib.import_module("native_contract_smoke")


class ReferenceToyTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name).resolve()
        (self.root / "work").mkdir()
        self.data = {
            3: b"SYNTHETIC REFERENCE THREE, NOT HWP",
            4: b"SYNTHETIC REFERENCE FOUR, NOT HWP",
        }
        self.sources = {}
        for schema, data in self.data.items():
            path = self.root / f"REFERENCE-{schema}.hwp"
            path.write_bytes(data)
            self.sources[schema] = path
        pins = {
            schema: hashlib.sha256(data).hexdigest()
            for schema, data in self.data.items()
        }
        for item in (
            patch.object(m, "ROOT", self.root),
            patch.object(m, "REFERENCE_TOY_SHA256", pins),
            patch.object(
                m.security,
                "registered_security_module",
                side_effect=AssertionError("No real registry/security"),
            ),
            patch.object(
                m, "owned_session", side_effect=AssertionError("No real COM/Native")
            ),
            patch.object(
                m.p.subprocess,
                "run",
                side_effect=AssertionError("No child Native/SDK process"),
            ),
        ):
            item.start()
            self.addCleanup(item.stop)

    def lab(self, name="toy-contract-SYNTHETIC"):
        lab = self.root / "work" / name
        lab.mkdir()
        return lab

    def test_validation_is_readonly_exact_schema_and_bytes(self):
        before = {p: p.read_bytes() for p in self.sources.values()}
        for schema, source in self.sources.items():
            self.assertEqual(m.reference_toy(source, schema), source)
        self.assertEqual({p: p.read_bytes() for p in before}, before)
        self.assertEqual(list((self.root / "work").iterdir()), [])
        m.security.registered_security_module.assert_not_called()
        m.owned_session.assert_not_called()

    def test_frozen_or_unknown_schema_denies_before_any_path(self):
        for schema in (1, 2, True, "3", None):
            with patch.object(m.n, "real_path") as path:
                with self.assertRaises(ValueError):
                    m.reference_toy(self.root / "NOT-READ", schema)
                path.assert_not_called()
        with (
            patch.object(m.sys, "frozen", True, create=True),
            patch.object(m.n, "real_path") as path,
        ):
            with self.assertRaises(ValueError):
                m.reference_toy(self.sources[3], 3)
            path.assert_not_called()

    def test_arbitrary_or_cross_schema_bytes_and_non_hwp_are_not_references(self):
        for source, schema in ((self.sources[3], 4), (self.sources[4], 3)):
            with self.assertRaises(ValueError):
                m.reference_toy(source, schema)
        other = self.root / "REAL-LOOKING.hwp"
        other.write_bytes(b"ARBITRARY NOT A PINNED REFERENCE")
        with self.assertRaises(ValueError):
            m.reference_toy(other, 3)
        wrong_extension = self.root / "REFERENCE.hwpx"
        wrong_extension.write_bytes(self.data[3])
        with self.assertRaises(ValueError):
            m.reference_toy(wrong_extension, 3)

    def test_exclusive_copy_never_moves_or_rewrites_original_or_consent(self):
        for schema, name in (
            (3, "toy-contract-SYNTHETIC"),
            (4, "toy-gui-native-SYNTHETIC"),
        ):
            lab = self.lab(name)
            target = m.copy_reference_toy(self.sources[schema], lab / "source", schema)
            self.assertEqual(target.read_bytes(), self.data[schema])
            self.assertEqual(self.sources[schema].read_bytes(), self.data[schema])
            self.assertEqual(
                {p.name for p in (lab / "source").iterdir()}, {target.name}
            )
            self.assertFalse((lab / "approved-plan.json").exists())
        m.p.subprocess.run.assert_not_called()
        m.owned_session.assert_not_called()

    def test_existing_or_foreign_nested_destinations_never_overwrite(self):
        lab = self.lab()
        directory = lab / "source"
        directory.mkdir()
        keep = directory / "TOY-ONLY-reference-source.hwp"
        keep.write_bytes(b"KEEP FAILED OUTPUT")
        with self.assertRaises(FileExistsError):
            m.copy_reference_toy(self.sources[3], directory, 3)
        self.assertEqual(keep.read_bytes(), b"KEEP FAILED OUTPUT")
        foreign = self.root / "NOT-OWNED"
        foreign.mkdir()
        nested = lab / "nested"
        nested.mkdir()
        for directory in (foreign / "source", nested / "source", lab / "wrong-name"):
            with self.assertRaises(ValueError):
                m.copy_reference_toy(self.sources[3], directory, 3)
            self.assertFalse(directory.exists())
        inside = lab / "inside.hwp"
        inside.write_bytes(self.data[3])
        with self.assertRaises(ValueError):
            m.copy_reference_toy(inside, lab / "source", 3)

    def test_symlink_reference_is_rejected_without_reading_target(self):
        link = self.root / "LINK.hwp"
        try:
            link.symlink_to(self.sources[3])
        except (OSError, NotImplementedError):
            self.skipTest("Symlink creation unavailable")
        with patch.object(m.r, "file_hash") as read:
            with self.assertRaises(ValueError):
                m.reference_toy(link, 3)
            read.assert_not_called()

    def test_link_metadata_guard_rejects_before_hash_without_os_link_privilege(self):
        original = Path.is_symlink
        with (
            patch.object(
                Path,
                "is_symlink",
                lambda path: path == self.sources[3] or original(path),
            ),
            patch.object(m.r, "file_hash") as read,
        ):
            with self.assertRaises(ValueError):
                m.reference_toy(self.sources[3], 3)
            read.assert_not_called()

    def test_postcopy_reference_drift_blocks_and_keeps_evidence(self):
        lab = self.lab()

        def copy(original, target, **kwargs):
            target.write(original.read())
            self.sources[3].write_bytes(b"CHANGED DURING COPY")

        with patch.object(m.shutil, "copyfileobj", side_effect=copy):
            with self.assertRaises(ValueError):
                m.copy_reference_toy(self.sources[3], lab / "source", 3)
        self.assertEqual(
            (lab / "source/TOY-ONLY-reference-source.hwp").read_bytes(), self.data[3]
        )
        m.owned_session.assert_not_called()

    def test_invalid_reference_run_stops_before_security_or_lab(self):
        exe = self.root / "HWPReview.exe"
        exe.write_bytes(b"SYNTHETIC NOT EXE")
        output = self.root / "work/toy-contract-BAD"
        with (
            patch.object(
                m,
                "qualification_preflight",
                return_value={"executable_sha256": "0" * 64},
            ),
            patch.object(m, "create_source") as author,
        ):
            with self.assertRaises(ValueError):
                m.run(
                    output,
                    exe,
                    3,
                    run_native=True,
                    toy_only=True,
                    reference_toy_source=self.sources[4],
                )
            author.assert_not_called()
        self.assertFalse(output.exists())
        m.security.registered_security_module.assert_not_called()

    def test_nested_run_lab_rejects_before_security_or_reference_copy(self):
        exe = self.root / "HWPReview.exe"
        exe.write_bytes(b"SYNTHETIC NOT EXE")
        parent = self.lab("toy-contract-parent")
        output = parent / "toy-contract-nested"
        with (
            patch.object(
                m,
                "qualification_preflight",
                return_value={"executable_sha256": "0" * 64},
            ),
            patch.object(m, "copy_reference_toy") as copied,
        ):
            with self.assertRaises(ValueError):
                m.run(
                    output,
                    exe,
                    3,
                    run_native=True,
                    toy_only=True,
                    reference_toy_source=self.sources[3],
                )
            copied.assert_not_called()
        self.assertFalse(output.exists())
        m.security.registered_security_module.assert_not_called()

    def test_fresh_no_profile_stops_before_proposal_or_ui_consent(self):
        exe = self.root / "HWPReview.exe"
        exe.write_bytes(b"SYNTHETIC NOT EXE")
        output = self.root / "work/toy-contract-PROFILE"
        workspace = output / "owned-project/_review"
        project = SimpleNamespace(workspace=workspace)

        def prepared(*args):
            workspace.mkdir()
            return project

        with (
            patch.object(
                m,
                "qualification_preflight",
                return_value={"executable_sha256": "0" * 64},
            ),
            patch.object(m.security, "registered_security_module", return_value={}),
            patch.object(m.p, "import_hwp", return_value=project),
            patch.object(m.p, "prepare_project", side_effect=prepared),
            patch.object(m.r, "verify_baseline", return_value={}),
            patch.object(
                m.r.hwp_fixed_layout,
                "require_native_profile",
                side_effect=ValueError("SYNTHETIC fresh NO profile"),
            ) as profile,
            patch.object(m.r, "create_review_server") as server,
            patch.object(m.r.hwp_fixed_layout, "proposal") as proposal,
        ):
            with self.assertRaisesRegex(ValueError, "fresh NO profile"):
                m.run(
                    output,
                    exe,
                    3,
                    run_native=True,
                    toy_only=True,
                    reference_toy_source=self.sources[3],
                )
            profile.assert_called_once_with(workspace / "baseline.hwpx")
            server.assert_not_called()
            proposal.assert_not_called()
        report = json.loads(
            (output / "TOY-ONLY-contract-verification.json").read_text(encoding="utf-8")
        )
        self.assertTrue(report["source_reference_copy"])
        self.assertEqual(report["status"], "blocked")
        self.assertFalse((workspace / "approved-plan.json").exists())
        self.assertEqual(self.sources[3].read_bytes(), self.data[3])
        m.owned_session.assert_not_called()
        m.p.subprocess.run.assert_not_called()


if __name__ == "__main__":
    unittest.main()
