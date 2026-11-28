"""Pure failure boundaries: synthetic bytes only; no app, Windows setup, registry or UI."""

import hashlib
import json
import os
from pathlib import Path
import stat
import sys
import tempfile
import unittest
import zipfile

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import release_install_smoke as smoke


def digest(data):
    return hashlib.sha256(data).hexdigest()


def encoded(value):
    return json.dumps(value, sort_keys=True).encode()


class InstalledSmokeTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name).resolve()
        self.zip = self.root / smoke.KIT_NAME
        notices = {"NOTICE.txt": digest(b"notice")}
        names = smoke.bundle.payload_names(smoke.APP_VERSION, notices)
        self.members = {name: ("synthetic " + name).encode() for name in names}
        self.members["licenses/NOTICE.txt"] = b"notice"
        self.exe_sha = digest(self.members["HWPReview.exe"])
        self.build = {"version": smoke.APP_VERSION, "code_signed": False, "redistribution_ready": False,
                      "executable_sha256": self.exe_sha, "licenses_sha256": notices,
                      "source_archive": {"file": smoke.SOURCE_NAME, "bytes": len(self.members[smoke.SOURCE_NAME]),
                                         "sha256": digest(self.members[smoke.SOURCE_NAME])},
                      "input_sha256": {src: digest(self.members[name]) for name, src in smoke.bundle.ROOT_INPUTS.items()}}
        self.seal()

    def seal(self):
        self.members["BUILD.json"] = encoded(self.build)
        files = {name: {"bytes": len(blob), "sha256": digest(blob)} for name, blob in self.members.items()
                 if name not in (smoke.bundle.MANIFEST, "SHA256SUMS.txt")}
        self.members[smoke.bundle.MANIFEST] = encoded({"contract": smoke.bundle.CONTRACT,
            "version": smoke.APP_VERSION, "architecture": "x64", "code_signed": False,
            "redistribution_ready": False, "files": files})
        self.members["SHA256SUMS.txt"] = "".join(digest(self.members[name]) + "  " + name + "\n"
            for name in sorted(set(files) | {smoke.bundle.MANIFEST})).encode()

    def transport(self, extra=None, replace=None, comment=b""):
        with zipfile.ZipFile(self.zip, "w", compression=zipfile.ZIP_DEFLATED) as archive:
            for name, blob in self.members.items():
                archive.writestr(replace if replace and name == replace.filename else name, blob)
            if extra:
                archive.writestr(extra, b"foreign")
            archive.comment = comment
        return digest(self.zip.read_bytes())

    def test_exact_kit_and_exe_bind_every_extracted_file(self):
        output = self.root / "new"
        sha = self.transport()
        records = smoke._extract(self.zip, output, sha, self.exe_sha)
        self.assertEqual(records, {name: digest(blob) for name, blob in self.members.items() if name != "SHA256SUMS.txt"})
        self.assertEqual({path.relative_to(output).as_posix(): path.read_bytes() for path in output.rglob("*") if path.is_file()}, self.members)
        with self.assertRaises(FileExistsError):
            smoke._extract(self.zip, output, sha, self.exe_sha)

    def test_untrusted_names_aliases_and_unknown_members_never_extract(self):
        for extra in ("../escaped", r"licenses\..\escaped", "C:/escaped", "licenses/CON.txt", "licenses:n",
                      "HWPReview.exe.", "hwpreview.EXE", "wrapper/HWPReview.exe", "local-evidence.json"):
            with self.subTest(extra=extra):
                sha = self.transport(extra)
                with self.assertRaises(ValueError):
                    smoke._extract(self.zip, self.root / "new", sha, self.exe_sha)
                self.assertFalse((self.root / "new").exists())
                self.assertFalse((self.root / "escaped").exists())

    def test_links_reparse_and_ambiguous_zip_envelope_rejected_before_writes(self):
        link = zipfile.ZipInfo("HWPReview.exe")
        for attr in ((stat.S_IFLNK | 0o777) << 16, 0x400, 0x10):
            link.external_attr = attr
            with self.subTest(attr=attr), self.assertRaises(ValueError):
                smoke._extract(self.zip, self.root / "new", self.transport(replace=link), self.exe_sha)
        with self.assertRaises(ValueError):
            smoke._extract(self.zip, self.root / "new", self.transport(comment=b"ambiguous"), self.exe_sha)
        self.assertFalse((self.root / "new").exists())

    def test_wrong_identity_or_private_build_is_not_the_public_kit(self):
        sha = self.transport()
        for kit, exe in (("0" * 64, self.exe_sha), (sha, "0" * 64), (sha, self.exe_sha.upper())):
            with self.subTest(kit=kit, exe=exe), self.assertRaises(ValueError):
                smoke._extract(self.zip, self.root / "new", kit, exe)
        self.build["private_evidence"] = {"machine": "synthetic"}
        self.seal()
        with self.assertRaisesRegex(ValueError, "sanitized public build"):
            smoke._extract(self.zip, self.root / "new", self.transport(), self.exe_sha)
        self.assertFalse((self.root / "new").exists())

    def test_hardlinked_input_must_not_be_opened_as_the_kit(self):
        sha = self.transport()
        try:
            os.link(self.zip, self.root / "alias")
        except OSError as error:
            self.skipTest("Hardlinks unavailable on temporary volume: " + str(error))
        with self.assertRaises(ValueError):
            smoke._extract(self.zip, self.root / "new", sha, self.exe_sha)
        self.assertFalse((self.root / "new").exists())

    def test_member_bytes_must_match_manifest_even_with_new_transport_hash(self):
        self.members["Install-HWPReview.ps1"] += b"changed"
        with self.assertRaisesRegex(ValueError, "member differs"):
            smoke._extract(self.zip, self.root / "new", self.transport(), self.exe_sha)
        self.assertFalse((self.root / "installed-smoke.json").exists())

    def test_prepared_only_requires_complete_applicable_components(self):
        components = {key: {"installed": True, "required": True, "detail": "synthetic"} for key in smoke.setup.COMPONENT_IDS}
        report = {"contract": "hwp-requirements-status-v2", "ready": True, "components": components,
                  "reboot_required": False}
        smoke._ready(report)
        components["hwp_security"].update(installed=False, required=False)
        smoke._ready(report)  # No Hancom here; never run it to satisfy this check.
        for bad in ({**report, "reboot_required": True}, {**report, "ready": 1},
                    {**report, "components": {**components, "omp_runtime": {"installed": False, "required": True, "detail": "missing"}}}):
            with self.subTest(bad=bad), self.assertRaises(ValueError):
                smoke._ready(bad)

    def test_exit_receipt_must_cover_distinct_owned_processes_and_managed_bytes(self):
        roles = ("app", "host", "wpf", "omp", "bun")
        ex, work, managed = (self.root / name for name in ("app.exe", "Workspace", "managed"))
        pins = {r["relative_path"]: r["sha256"] for r in smoke.omp._baseline()[0]["files"]}
        proof = {"contract": "hwp-owned-installed-window-v1", "exe": str(ex), "title": "HWP Editor — Workspace",
                 "sync": "실시간 동기화 중", "idle": "OMP · OMP", "hwnd": 42, "managed": str(managed),
                 "pids": dict(zip(roles, range(100, 105))), "exit_codes": dict.fromkeys(roles, 0),
                 "remaining_descendants": 0, "normal_descendant_exits": 10,
                 "omp_sha256": pins["bin/omp.exe"], "bun_sha256": pins["bin/bun.exe"]}
        smoke._window(proof, ex, work, managed)
        for change in ({"pids": {**proof["pids"], "bun": 101}}, {"exit_codes": {**proof["exit_codes"], "omp": 1}},
                       {"idle": "OMP 종료됨"}, {"remaining_descendants": 1}, {"bun_sha256": "0" * 64}):
            with self.subTest(change=change), self.assertRaises(ValueError):
                smoke._window({**proof, **change}, ex, work, managed)

    def test_preexisting_or_foreign_checkpoint_is_not_evidence(self):
        fixture = self.root / "document.hwpx"
        fixture.write_bytes(b"synthetic")
        sha = digest(fixture.read_bytes())
        store = self.root / ".hwpreview"
        history = store / "history" / digest(os.path.normcase(fixture.name).encode())[:16]
        history.mkdir(parents=True)
        original = store / "originals" / (sha + ".hwpx")
        original.parent.mkdir()
        original.write_bytes(fixture.read_bytes())
        name = "20261129-000000-123456-" + sha[:12] + ".hwpx"
        row = {"file": name, "reason": "initial", "sha256": sha, "size": fixture.stat().st_size}
        index = {"schema": "hwp-live-history-v1", "relative_path": fixture.name, "original_sha256": sha,
                 "original_path": str(original), "checkpoints": [row]}
        (history / "index.json").write_bytes(encoded(index))
        (history / name).write_bytes(b"old")
        with self.assertRaises(ValueError):
            smoke._checkpoint(fixture, sha)
        row["file"] = str(original)
        (history / "index.json").write_bytes(encoded(index))
        with self.assertRaises(ValueError):
            smoke._checkpoint(fixture, sha)


if __name__ == "__main__":
    unittest.main()
