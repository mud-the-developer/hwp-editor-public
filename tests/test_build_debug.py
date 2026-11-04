"""Snapshot/promotion regressions on temporary synthetic BUILD files only."""

import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import build_exe as b
import hwp_build_source as build_source


class BuildDebugTests(unittest.TestCase):
    def snapshot_fixture(self, root):
        names = (
            "scripts/app.py",
            "web/view.html",
            *("packaging/licenses/" + name for name in b.LEGAL_FILES),
        )
        for name in names:
            path = root / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(b"SYNTHETIC BUILD INPUT")
        ignored = root / "work/PRIVATE-OWNER.txt"
        ignored.parent.mkdir()
        ignored.write_bytes(b"NOT AN APP ASSET; NEVER COPY")
        return names

    def test_compiler_snapshot_does_not_track_later_edits_or_collect_private_work(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp) / "source"
            root.mkdir()
            names = self.snapshot_fixture(root)
            snapshot = Path(temp) / "snapshot"
            hashes = build_source.snapshot_inputs(root, snapshot, names)
            (root / "scripts/app.py").write_bytes(b"CHANGED DURING BUILD")
            self.assertEqual(
                (snapshot / "scripts/app.py").read_bytes(), b"SYNTHETIC BUILD INPUT"
            )
            self.assertFalse((snapshot / "work").exists())
            with self.assertRaises(RuntimeError):
                build_source.verify_inputs(root, hashes)

    def test_snapshot_must_be_a_new_directory(self):
        with tempfile.TemporaryDirectory() as temp:
            with self.assertRaises(FileExistsError):
                build_source.snapshot_inputs(Path(temp), Path(temp), ())

    def test_source_path_escape_is_rejected(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp) / "source"
            root.mkdir()
            (Path(temp) / "OUTSIDE.txt").write_bytes(b"NOT AN ASSET")
            with self.assertRaises(ValueError):
                build_source.snapshot_inputs(
                    root, Path(temp) / "snapshot", ("../OUTSIDE.txt",)
                )

    def test_version_resource_tracks_semver_rc_number(self):
        with (
            tempfile.TemporaryDirectory() as temp,
            patch.object(b, "APP_VERSION", "1.2.3-rc.4"),
        ):
            file = Path(temp) / "version.txt"
            b.version_file(file)
            text = file.read_text(encoding="utf-8")
            self.assertIn("filevers=(1, 2, 3, 4)", text)
            self.assertIn("prodvers=(1, 2, 3, 4)", text)
            self.assertIn("'1.2.3.4'", text)
            self.assertNotIn("0.1.0.1", text)
            self.assertIn("flags=0x2", text)

    def test_stable_version_sorts_after_rc_and_is_not_marked_prerelease(self):
        with (
            tempfile.TemporaryDirectory() as temp,
            patch.object(b, "APP_VERSION", "1.2.3"),
        ):
            file = Path(temp) / "version.txt"
            b.version_file(file)
            text = file.read_text(encoding="utf-8")
            self.assertIn("filevers=(1, 2, 3, 65535)", text)
            self.assertIn("flags=0x0", text)
            self.assertIn("'ProductVersion','1.2.3'", text)

    def test_invalid_or_overflow_version_is_rejected_before_writing(self):
        with tempfile.TemporaryDirectory() as temp:
            file = Path(temp) / "version.txt"
            for invalid in (
                "../bad",
                "0.1.0-personal.1",
                "0.1.0-rc.0",
                "0.1.0-rc.65535",
                "01.2.3",
                "1.02.3",
                "1.2.03",
                "65536.0.0",
                "0.1.65536",
                "1.2.3-beta.1",
                "1.2.3+private",
                "1.2.3\n",
            ):
                with patch.object(b, "APP_VERSION", invalid):
                    with self.assertRaises(ValueError):
                        b.version_file(file)
            self.assertFalse(file.exists())

    def test_locked_running_exe_keeps_old_convenience_manifest_and_complete_new_set(
        self,
    ):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            release, dist = root / "release", root / "dist"
            release.mkdir()
            dist.mkdir()
            for name in ("HWPReview.exe", "BUILD.json", "SHA256SUMS.txt"):
                (release / name).write_bytes(b"NEW SYNTHETIC BUILD")
                (dist / name).write_bytes(b"OLD SYNTHETIC BUILD")
            artifact = dist / "personal-builds/new"
            original = b.os.replace

            def replace(source, destination):
                if Path(destination) == dist / "HWPReview.exe":
                    raise PermissionError("Synthetic running EXE lock")
                return original(source, destination)

            with patch.object(b.os, "replace", side_effect=replace):
                with self.assertRaises(PermissionError):
                    b.promote_release(release, dist, artifact)
            for name in ("HWPReview.exe", "BUILD.json", "SHA256SUMS.txt"):
                self.assertEqual((dist / name).read_bytes(), b"OLD SYNTHETIC BUILD")
                self.assertEqual((artifact / name).read_bytes(), b"NEW SYNTHETIC BUILD")

    def test_existing_release_set_is_never_replaced(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            release, dist, artifact = (
                root / "release",
                root / "dist",
                root / "dist/releases/version/id",
            )
            release.mkdir()
            artifact.mkdir(parents=True)
            (artifact / "OWNER").write_bytes(b"EXISTING")
            with self.assertRaises(FileExistsError):
                b.promote_release(release, dist, artifact)
            self.assertEqual((artifact / "OWNER").read_bytes(), b"EXISTING")
            self.assertTrue(release.exists())

    def test_successful_promotion_keeps_a_coherent_immutable_build_set(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            release, dist = root / "release", root / "dist"
            release.mkdir()
            dist.mkdir()
            executable = release / "HWPReview.exe"
            executable.write_bytes(b"NEW SYNTHETIC APP, NEVER RUN")
            source = release / "HWPReview-source-toy.zip"
            source.write_bytes(b"EXACT SYNTHETIC SOURCE")
            record = {"exe_sha256": b.build_source.sha256(executable),
                      "source_sha256": b.build_source.sha256(source)}
            (release / "BUILD.json").write_text(json.dumps(record), encoding="utf-8")
            artifact = dist / "releases/toy/new"
            b.promote_release(release, dist, artifact)
            recorded = json.loads((artifact / "BUILD.json").read_text(encoding="utf-8"))
            self.assertEqual(recorded["exe_sha256"], b.build_source.sha256(artifact / "HWPReview.exe"))
            self.assertEqual(recorded["source_sha256"], b.build_source.sha256(artifact / source.name))
            (dist / "HWPReview.exe").write_bytes(b"NEXT CONVENIENCE APP")
            self.assertEqual(recorded["exe_sha256"], b.build_source.sha256(artifact / "HWPReview.exe"))
            self.assertFalse(release.exists())


if __name__ == "__main__":
    unittest.main()
