"""Offline source staging tests using only tiny synthetic candidate and upstream bytes."""

from io import BytesIO
import json
import os
from pathlib import Path
import stat
import sys
import tarfile
import tempfile
import unittest
from unittest.mock import patch
import zipfile

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import hwp_build_source as source
import hwp_install_bundle as bundle
import hwp_release_sources as release

ROOT = Path(__file__).resolve().parents[1]
VERSION = "1.2.3-test"
COMMIT = "aBcD" * 10
WINDOWS = f"HWPReview-win-x64-{VERSION}.zip"
APP = f"HWPReview-source-{VERSION}.zip"
KIT = f"HWPReview-corresponding-source-{VERSION}.zip"
PIN_DOCS = ("third-party-sources", "terminal-dependencies", "hwpx-renderer-dependencies")


class ReleaseSourcesTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name).resolve()
        self.candidate = self.root / "checked-release"
        self.snapshot = self.root / "snapshot"
        self.upstream = self.root / "upstream"
        self.output = self.root / "staged"
        self.legal = self.root / "notices"
        for folder in (self.candidate, self.snapshot / "packaging", self.upstream, self.legal):
            folder.mkdir(parents=True)
        self.index = release._load_index()
        for item in self.index["artifacts"]:
            path = self.upstream / item["filename"]
            if item["component"] == "pywin32":
                prefix = "pywin32-" + item["commit"] + "/"
                with tarfile.open(path, "x:gz") as archive:
                    for filename in ("win32/license.txt", "AutoDuck/bin/autoduck.exe", "SWIG/swig.exe"):
                        data = filename.encode()
                        entry = tarfile.TarInfo(prefix + filename)
                        entry.size = len(data)
                        archive.addfile(entry, BytesIO(data))
            else:
                path.write_bytes(("inert toy package: " + path.name).encode())
            item.update(bytes=path.stat().st_size, sha256=source.sha256(path))
        self.pins = release._pins(self.index)
        self.pin_docs = {name: json.loads((ROOT / f"packaging/{name}.json").read_bytes()) for name in PIN_DOCS}
        for name in ("pymupdf", "mupdf"):
            item = self.pin_docs["third-party-sources"][name]
            pin = self.pins[item["filename"]]
            item.update(bytes=pin["bytes"], sha256=pin["sha256"])
        for name in ("webview2", "xterm", "addon_fit", "unicode_graphemes"):
            item = self.pin_docs["terminal-dependencies"][name]
            pin = self.pins[item["package_url"].rsplit("/", 1)[-1]]
            item["package_sha256" if name == "webview2" else "sha256"] = pin["sha256"]
        renderer = self.pin_docs["hwpx-renderer-dependencies"]
        preferred = renderer["preferred_source_material"]
        for name in ("core_archive", "registry_sources", "runtime_package"):
            item = preferred[name]
            pin = self.pins[item["filename"]]
            item.update(bytes=pin["bytes"], sha256=pin["sha256"])
            if "member_index" in item:
                item["member_index_sha256"] = self.pins[item["member_index"]]["sha256"]
            else:
                renderer["tarball_sha256"] = pin["sha256"]
        for name, path in bundle.ROOT_INPUTS.items():
            (self.snapshot / path).write_text(f"toy input: {name}", encoding="utf-8")
        (self.legal / "TOY-NOTICE.txt").write_bytes(b"toy notice")
        (self.candidate / "HWPReview.exe").write_bytes(b"toy bytes, never executed")

    def candidate_build(self, extra=None, source_mutator=None):
        for name, document in self.pin_docs.items():
            (self.snapshot / f"packaging/{name}.json").write_text(json.dumps(document), encoding="utf-8")
        inputs = {path.relative_to(self.snapshot).as_posix(): source.sha256(path)
                  for path in self.snapshot.rglob("*") if path.is_file()}
        app = self.candidate / APP
        app_record = source.write_source_archive(self.snapshot, inputs, app, version=VERSION)
        if source_mutator is not None:
            source_mutator(app)
            app_record.update(bytes=app.stat().st_size, sha256=source.sha256(app))
        build = {
            "version": VERSION,
            "redistribution_ready": False,
            "executable_sha256": source.sha256(self.candidate / "HWPReview.exe"),
            "input_sha256": inputs,
            "licenses_sha256": {name.name: source.sha256(name) for name in self.legal.iterdir()},
            "source_archive": app_record,
            "artifact_set": f"releases/{VERSION}/toy",
            "source_url": "https://github.com/owner/hwp-review",
            **(extra or {}),
        }
        (self.candidate / "BUILD.json").write_text(json.dumps(build), encoding="utf-8")
        manifest = bundle.write_payload(self.candidate, self.snapshot, self.legal, version=VERSION)
        members = sorted([*manifest["files"], bundle.MANIFEST])
        sums = "".join(source.sha256(self.candidate / name) + "  " + name + "\n" for name in members)
        (self.candidate / release.SUMS).write_text(sums, encoding="utf-8", newline="\n")
        archive = self.candidate / WINDOWS
        with zipfile.ZipFile(archive, "x") as package:
            for name in [*members, release.SUMS]:
                package.write(self.candidate / name, name)
        (self.candidate / (WINDOWS + ".sha256")).write_text(
            source.sha256(archive) + "  " + WINDOWS + "\n", encoding="utf-8", newline="\n",
        )

    def stage(self, output=None, commit=COMMIT):
        with patch.object(release, "_load_index", return_value=self.index):
            return release.stage_release(self.candidate, self.upstream, output or self.output, commit=commit)

    def file_hashes(self, root):
        return {path.relative_to(root).as_posix(): source.sha256(path) for path in root.rglob("*") if path.is_file()}

    def test_real_index_agrees_with_checked_in_authoritative_pins(self):
        with zipfile.ZipFile(self.root / "pins.zip", "x") as archive:
            for name in PIN_DOCS:
                archive.write(ROOT / f"packaging/{name}.json", f"packaging/{name}.json")
        with zipfile.ZipFile(self.root / "pins.zip") as archive:
            release._check_snapshot_pins(archive, release._pins(release._load_index()))

    def prepare_public_fixture(self):
        import prepare_public_release as public

        index = self.snapshot / "packaging" / release.INDEX.name
        index.write_bytes(release._json_bytes(self.index))
        self.candidate_build({"private_receipt": {"machine": str(self.root)}})
        build = json.loads((self.candidate / "BUILD.json").read_bytes())
        dist = self.root / "prepared"
        with patch.object(public, "validate", return_value=(self.candidate, build)):
            public.prepare(self.candidate, dist, COMMIT.lower(), release.PUBLIC_REPOSITORY)
        return dist, build, index

    def test_public_stage_uses_only_the_installed_transport_and_all_evidence(self):
        import build_exe
        import prepare_public_release as public
        import shutil

        dist, build, index = self.prepare_public_fixture()
        installer = dist / "installer" / WINDOWS
        evidence = [self.root / name for name in ("schema1", "schema3", "schema4")]
        digest = build["executable_sha256"]

        def notice_fixture(root, target, pinned, *, app_version):
            shutil.copytree(self.legal, target)

        with (patch.object(build_exe, "validate_checked_candidate", return_value=(self.candidate, None, digest)) as native,
              patch.object(public, "validate", return_value=(self.candidate, build)) as binding,
              patch.object(build_exe.license_bundle, "write_licenses", side_effect=notice_fixture),
              patch.object(release, "INDEX", index)):
            manifest = release.stage_public_assets(self.candidate, dist, self.upstream, self.output,
                                                   commit=COMMIT.lower(), kit_sha256=source.sha256(installer),
                                                   exe_sha256=digest, native_evidence=evidence[0],
                                                   layout_evidence=evidence[1], fill_evidence=evidence[2])
        native.assert_called_once_with(self.candidate, *evidence)
        binding.assert_called_once_with(self.candidate, COMMIT.lower(), release.PUBLIC_REPOSITORY)
        self.assertEqual((self.output / WINDOWS).read_bytes(), installer.read_bytes())
        with zipfile.ZipFile(self.output / WINDOWS) as transport:
            self.assertNotIn(b"private_receipt", transport.read("BUILD.json"))
        self.assertNotEqual(source.sha256(installer), source.sha256(self.candidate / WINDOWS))
        self.assertEqual(set(manifest["assets"]), {WINDOWS, APP, KIT, release.MATERIALS})
        self.assertEqual(set(self.file_hashes(self.output)), set(manifest["assets"]) | {release.MANIFEST, release.SUMS})
        self.assertNotIn(str(self.root), json.dumps(manifest))
        self.assertEqual(manifest["qualification"]["native_evidence_schemas"], [1, 3, 4])

    def test_public_distribution_rejects_swapped_candidate_input_commit_and_transport(self):
        dist, build, _ = self.prepare_public_fixture()
        kit = source.sha256(dist / "installer" / WINDOWS)
        exe = build["executable_sha256"]

        def check(commit=COMMIT.lower(), kit=kit, exe=exe):
            return release._distribution(self.candidate, dist, build, commit, kit, exe)

        for args in ({"commit": "a" * 40}, {"kit": source.sha256(self.candidate / WINDOWS)}, {"exe": "0" * 64}):
            with self.subTest(args=args), self.assertRaises(ValueError):
                check(**args)
        (self.candidate / "licenses" / "TOY-NOTICE.txt").write_bytes(b"swapped candidate")
        with self.assertRaisesRegex(ValueError, "checked candidate"):
            check()

    def test_public_rejects_private_transport_metadata_even_with_new_checksums(self):
        dist, build, _ = self.prepare_public_fixture()
        transport = dist / "installer" / WINDOWS
        with zipfile.ZipFile(transport, "a") as archive:
            archive.comment = b"private install receipt"
        digest = source.sha256(transport)
        (transport.parent / (WINDOWS + ".sha256")).write_bytes(f"{digest}  {WINDOWS}\n".encode())
        with self.assertRaisesRegex(ValueError, "ZIP envelope"):
            release._distribution(self.candidate, dist, build, COMMIT.lower(), digest, build["executable_sha256"])

    def test_exact_deterministic_closed_output_without_private_paths(self):
        self.candidate_build()
        before = self.file_hashes(self.candidate)
        manifest = self.stage()
        self.assertEqual(before, self.file_hashes(self.candidate))
        self.assertEqual(manifest["reviewed_commit_provenance_only"], COMMIT.lower())
        self.assertEqual(set(self.file_hashes(self.output)), {WINDOWS, APP, KIT, release.MANIFEST, release.MATERIALS, release.SUMS})
        for name in (WINDOWS, APP):
            self.assertEqual(source.sha256(self.output / name), before[name])
        with zipfile.ZipFile(self.output / KIT) as archive:
            upstream = {name for name, pin in self.pins.items() if pin["component"] not in {"webview2", "pywin32"}}
            pywin32 = "upstream/pywin32-311-source.zip"
            expected = {APP, release.INDEX.name, release.MATERIALS, pywin32} | {f"upstream/{name}" for name in upstream}
            self.assertEqual(set(archive.namelist()), expected)
            self.assertEqual(archive.read(APP), (self.output / APP).read_bytes())
            self.assertEqual(json.loads(archive.read(release.INDEX.name)), self.index)
            for name in upstream:
                self.assertEqual(archive.read(f"upstream/{name}"), (self.upstream / name).read_bytes())
            with zipfile.ZipFile(BytesIO(archive.read(pywin32))) as derived:
                self.assertEqual(set(derived.namelist()), {"win32/license.txt", "PUBLIC-SOURCE-ORIGIN.json"})
                self.assertEqual(derived.read("win32/license.txt"), b"win32/license.txt")
                origin = json.loads(derived.read("PUBLIC-SOURCE-ORIGIN.json"))
                self.assertEqual(origin["excluded"], ["AutoDuck/bin/autoduck.exe", "SWIG/swig.exe"])
                self.assertEqual(origin["original"], next(pin for pin in self.pins.values() if pin["component"] == "pywin32"))
        sums = (self.output / release.SUMS).read_text(encoding="utf-8")
        for name in set(manifest["assets"]) | {release.MANIFEST}:
            self.assertIn(f"{source.sha256(self.output / name)}  {name}\n", sums)
        for name in (release.MANIFEST, release.MATERIALS, release.SUMS):
            text = (self.output / name).read_text(encoding="utf-8")
            self.assertNotIn(str(self.root), text)
            self.assertNotIn("BUILD.json", text)
            self.assertNotIn("redistribution_ready", text)
        second = self.root / "second"
        self.stage(second)
        self.assertEqual(self.file_hashes(self.output), self.file_hashes(second))

    def test_changed_candidate_or_upstream_is_rejected_before_staging(self):
        self.candidate_build()
        sdk = next(name for name, pin in self.pins.items() if pin["component"] == "webview2")
        for path in (self.candidate / "HWPReview.exe", self.upstream / next(iter(self.pins)), self.upstream / sdk):
            original = path.read_bytes()
            with self.subTest(name=path.name):
                path.write_bytes(bytes([original[0] ^ 1]) + original[1:])
                with self.assertRaises(ValueError):
                    self.stage()
                self.assertFalse(self.output.exists())
                path.write_bytes(original)

    def test_source_snapshot_is_verified_after_self_consistent_repack(self):
        def mutate(app):
            with zipfile.ZipFile(app) as package:
                records = [(name, package.read(name)) for name in package.namelist()]
            with zipfile.ZipFile(app, "w") as package:
                for name, data in records:
                    package.writestr(name, b"tampered source" if name == "LICENSE" else data)

        self.candidate_build(source_mutator=mutate)
        with self.assertRaisesRegex(ValueError, "Build-source content changed"):
            self.stage()
        self.assertFalse(self.output.exists())

    def test_pin_conflict_with_exact_snapshot_is_rejected(self):
        self.pin_docs["terminal-dependencies"]["xterm"]["integrity"] = "different"
        self.candidate_build()
        with self.assertRaisesRegex(ValueError, "의존성 고정값"):
            self.stage()
        self.assertFalse(self.output.exists())

    def test_private_build_fields_in_valid_installer_are_rejected(self):
        self.candidate_build({"self_test": {"result_path": r"C:\Users\Synthetic\Private\output.json"}})
        bundle.verify_payload(self.candidate, version=VERSION)
        with self.assertRaisesRegex(ValueError, "비공개") as raised:
            self.stage()
        self.assertNotIn("Synthetic", str(raised.exception))
        self.assertFalse(self.output.exists())
        for sample in ({"apiKey": "dummy"}, {"note": "Bearer syntheticToken"}, {"note": "/home/toy/private"},
                       {"note": "-----BEGIN PRIVATE KEY-----"}, {"note": "https://u:p@example.org"}):
            with self.subTest(sample=sample), self.assertRaisesRegex(ValueError, "비공개"):
                release._private_check(sample)

    def test_closed_upstream_case_and_type_and_output_rules(self):
        self.candidate_build()
        original = self.upstream / next(iter(self.pins))
        wrong_case = original.with_name(original.name.upper())
        temp_name = original.with_name("temp-name")
        original.rename(temp_name)
        temp_name.rename(wrong_case)
        with self.assertRaises(ValueError):
            self.stage()
        wrong_case.rename(temp_name)
        temp_name.rename(original)
        with self.assertRaises(ValueError):
            release._names(["upstream/One", "UPSTREAM/two"])
        with self.assertRaises(ValueError):
            release._names(["Windows/../unsafe"])
        with self.assertRaises(ValueError):
            release._names(["One", "One/sub"])
        original.rename(temp_name)
        os.link(temp_name, original)
        with self.assertRaises(ValueError):
            release._record(original)
        original.unlink()
        original.mkdir()
        temp_name.unlink()
        with self.assertRaises(ValueError):
            self.stage()
        original.rmdir()
        original.write_bytes(("inert toy package: " + original.name).encode())
        with self.assertRaises(ValueError):
            self.stage(self.candidate / "new-stage")
        with self.assertRaises(ValueError):
            self.stage(commit="short")
        self.output.mkdir()
        with self.assertRaises(FileExistsError):
            self.stage()
        self.assertEqual(list(self.output.iterdir()), [])

    def test_zip_members_cannot_be_links_or_windows_aliases(self):
        archive = self.root / "unsafe.zip"
        with zipfile.ZipFile(archive, "x") as package:
            link = zipfile.ZipInfo("upstream/link.zip")
            link.create_system = 3
            link.external_attr = (stat.S_IFLNK | 0o777) << 16
            package.writestr(link, b"target")
        with zipfile.ZipFile(archive) as package, self.assertRaises(ValueError):
            release._zip_names(package)

    def test_copy_guard_rejects_changed_bytes_after_preflight(self):
        path = self.upstream / next(iter(self.pins))
        expected = release._record(path)
        path.write_bytes(b"changed")
        with self.assertRaisesRegex(ValueError, "복사본"):
            release._copy(path, self.root / "guarded-copy", expected)


if __name__ == "__main__":
    unittest.main()
