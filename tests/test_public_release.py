"""Public distribution privacy and byte-binding boundaries; synthetic payloads only."""

import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest import mock
import zipfile

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import prepare_public_release as public

REPOSITORY = "https://github.com/mud-the-developer/hwp-editor-public"


def command(root, *args):
    return subprocess.run(
        ["git", "-c", "core.autocrlf=false", "-C", str(root), *args],
        check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
    ).stdout.decode().strip()


def seal(candidate, build, root):
    for name in (public.bundle.MANIFEST, "SHA256SUMS.txt"):
        (candidate / name).unlink(missing_ok=True)
    for path in candidate.glob("HWPReview-win-x64-*"):
        path.unlink()
    (candidate / "BUILD.json").write_text(json.dumps(build), encoding="utf-8")
    for name in public.bundle.ROOT_INPUTS:
        (candidate / name).unlink(missing_ok=True)
    legal = candidate.parent / "legal"
    legal.mkdir(exist_ok=True)
    for name in build["licenses_sha256"]:
        (legal / name).write_bytes((candidate / "licenses" / name).read_bytes())
        (candidate / "licenses" / name).unlink()
    (candidate / "licenses").rmdir()
    manifest = public.bundle.write_payload(candidate, root, legal, version="1.2.3")
    members = sorted([*manifest["files"], public.bundle.MANIFEST])
    (candidate / "SHA256SUMS.txt").write_text("".join(
        public.source.sha256(candidate / name) + "  " + name + "\n" for name in members
    ), encoding="utf-8")
    archive = candidate / "HWPReview-win-x64-1.2.3.zip"
    with zipfile.ZipFile(archive, "x") as package:
        for name in [*members, "SHA256SUMS.txt"]:
            package.write(candidate / name, name)
    (candidate / (archive.name + ".sha256")).write_text(
        public.source.sha256(archive) + "  " + archive.name + "\n", encoding="utf-8",
    )


def snapshot(path):
    return {p.relative_to(path).as_posix(): p.read_bytes() for p in path.rglob("*") if p.is_file()}


class PublicReleaseTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.base = Path(temporary.name).resolve()
        self.root = self.base / "source"
        self.root.mkdir()
        names = set(public.bundle.ROOT_INPUTS.values()) | {"recipe.py", "packaging/licenses/original.txt"}
        for name in names:
            path = self.root / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(("original " + name).encode())
        command(self.root, "init")
        command(self.root, "add", "--", *sorted(names))
        self.commit_source("fixture")
        self.commit = command(self.root, "rev-parse", "HEAD")
        root_patch = mock.patch.object(public, "ROOT", self.root)
        root_patch.start()
        self.addCleanup(root_patch.stop)
        recipe_patch = mock.patch.multiple(
            public.build_exe, APP_VERSION="1.2.3",
            SOURCE_FILES=tuple(sorted(names - {"packaging/licenses/original.txt"})),
            ASSETS=(), LEGAL_FILES=("original.txt",),
        )
        recipe_patch.start()
        self.addCleanup(recipe_patch.stop)
        inputs = {name: public.source.sha256(self.root / name) for name in names}
        self.candidate = self.base / "candidate"
        self.candidate.mkdir()
        (self.candidate / "HWPReview.exe").write_bytes(b"fixture-executable-not-run")
        archive = public.source.write_source_archive(
            self.root, inputs, self.candidate / "HWPReview-source-1.2.3.zip", version="1.2.3",
        )
        legal = self.candidate / "licenses"
        legal.mkdir()
        (legal / "original.txt").write_bytes((self.root / "packaging/licenses/original.txt").read_bytes())
        (legal / "THIRD-PARTY-LICENSES.json").write_text(json.dumps({
            "project": {"source_repository": REPOSITORY},
            "files_sha256": {"original.txt": inputs["packaging/licenses/original.txt"]},
        }), encoding="utf-8")
        self.build = {
            "version": "1.2.3", "source_archive": archive, "input_sha256": inputs,
            "source_sha256": {name: inputs[name] for name in public.build_exe.SOURCE_FILES},
            "assets_sha256": {}, "executable_sha256": public.source.sha256(self.candidate / "HWPReview.exe"),
            "licenses_sha256": {p.name: public.source.sha256(p) for p in legal.iterdir()},
            "code_signed": False, "redistribution_ready": False,
            "private_device_inventory": {"user": "must-not-survive"},
        }
        seal(self.candidate, self.build, self.root)
        self.output = self.base / "public"

    def commit_source(self, message):
        command(self.root, "-c", "user.name=Fixture", "-c", "user.email=fixture@example.invalid",
                "commit", "-m", message)

    def prepare(self):
        return public.prepare(self.candidate, self.output, self.commit, REPOSITORY)

    def assert_rejected_before_creation(self):
        with self.assertRaises((ValueError, RuntimeError)):
            self.prepare()
        self.assertFalse(self.output.exists())

    def test_sanitizes_private_inventory_without_mutating_candidate(self):
        before = snapshot(self.candidate)
        manifest = self.prepare()
        public.bundle.verify_payload(self.output / "installer", version="1.2.3")
        clean = json.loads((self.output / "installer/BUILD.json").read_text(encoding="utf-8"))
        self.assertEqual(set(clean), {
            "version", "executable_sha256", "source_archive", "licenses_sha256",
            "input_sha256", "code_signed", "redistribution_ready",
        })
        self.assertEqual(set(clean["input_sha256"]), set(public.bundle.ROOT_INPUTS.values()))
        self.assertEqual(manifest["commit"], self.commit)
        self.assertIs(manifest["other_pc_tested"], False)
        self.assertEqual(snapshot(self.candidate), before)

    def test_rejects_changed_current_source(self):
        (self.root / "recipe.py").write_bytes(b"uncommitted")
        self.assert_rejected_before_creation()

    def test_rejects_resealed_wrong_original_notice(self):
        (self.candidate / "licenses/original.txt").write_bytes(b"wrong notice")
        self.build["licenses_sha256"]["original.txt"] = public.source.sha256(self.candidate / "licenses/original.txt")
        seal(self.candidate, self.build, self.root)
        self.assert_rejected_before_creation()

    def test_rejects_changed_archive(self):
        (self.candidate / self.build["source_archive"]["file"]).write_bytes(b"wrong archive")
        self.assert_rejected_before_creation()

    def test_rejects_private_payload_extra(self):
        (self.candidate / "private-report.json").write_text("private")
        self.assert_rejected_before_creation()

    def test_rejects_changed_executable(self):
        (self.candidate / "HWPReview.exe").write_bytes(b"tampered")
        self.assert_rejected_before_creation()

    def test_rejects_different_committed_source(self):
        (self.root / "recipe.py").write_bytes(b"different commit")
        command(self.root, "add", "--", "recipe.py")
        self.commit_source("different")
        self.commit = command(self.root, "rev-parse", "HEAD")
        (self.root / "recipe.py").write_bytes(b"original recipe.py")
        self.assert_rejected_before_creation()

    def replace_binary_with_link(self, *, symlink):
        target = self.candidate / "HWPReview.exe"
        original = self.base / "original.exe"
        target.rename(original)
        try:
            if symlink:
                target.symlink_to(original)
            else:
                os.link(original, target)
        except OSError:
            self.skipTest("Host does not permit this link type")
        self.assert_rejected_before_creation()

    def test_rejects_hardlinked_binary(self):
        self.replace_binary_with_link(symlink=False)

    def test_rejects_symlinked_binary(self):
        self.replace_binary_with_link(symlink=True)

    def test_rejects_case_colliding_source_archive_members(self):
        archive = self.candidate / self.build["source_archive"]["file"]
        with zipfile.ZipFile(archive, "a") as package:
            package.writestr("Recipe.py", b"original recipe.py")
        self.build["source_archive"].update(
            sha256=public.source.sha256(archive), bytes=archive.stat().st_size,
        )
        seal(self.candidate, self.build, self.root)
        self.assert_rejected_before_creation()

    def test_rejects_existing_output(self):
        self.output.mkdir()
        with self.assertRaises(ValueError):
            self.prepare()
        self.assertEqual(list(self.output.iterdir()), [])

    def test_rejects_resealed_transport_with_wrong_source_members(self):
        archive = self.candidate / self.build["source_archive"]["file"]
        archive.unlink()
        wrong = dict(self.build["input_sha256"])
        wrong.pop("recipe.py")
        self.build["source_archive"] = public.source.write_source_archive(
            self.root, wrong, archive, version="1.2.3",
        )
        seal(self.candidate, self.build, self.root)
        self.assert_rejected_before_creation()

    def test_rejects_parent_traversal_destination(self):
        self.output = self.root / ".." / "output"
        self.assert_rejected_before_creation()


if __name__ == "__main__":
    unittest.main()
