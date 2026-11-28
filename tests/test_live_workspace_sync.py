"""Unit tests for Plan A live workspace editing, originals backup, checkpoints, and NativeHost RPCs."""

from __future__ import annotations

import hashlib
import io
import os
from pathlib import Path
import secrets
import struct
import sys
import tempfile
import unittest
from unittest.mock import patch
import zipfile

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

import hwp_blank_document as blank_document
import hwp_native_host as native_host
import hwp_report_project as projects
import hwp_workspace as workspace


def _mutate_hwpx(raw: bytes, old_text: str, new_text: str) -> bytes:
    """Return a valid HWPX byte stream with section0.xml text updated."""
    buf = io.BytesIO()
    with zipfile.ZipFile(io.BytesIO(raw), "r") as zin, zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zout:
        for info in zin.infolist():
            data = zin.read(info.filename)
            if info.filename == "Contents/section0.xml":
                xml_text = data.decode("utf-8")
                if old_text and old_text in xml_text:
                    xml_text = xml_text.replace(old_text, new_text, 1)
                elif "<hp:t />" in xml_text:
                    xml_text = xml_text.replace("<hp:t />", f"<hp:t>{new_text}</hp:t>", 1)
                elif "<hp:t/>" in xml_text:
                    xml_text = xml_text.replace("<hp:t/>", f"<hp:t>{new_text}</hp:t>", 1)
                elif "</hp:t>" in xml_text:
                    xml_text = xml_text.replace("</hp:t>", f" {new_text}</hp:t>", 1)
                else:
                    raise AssertionError("No hp:t tag found in section0.xml")
                data = xml_text.encode("utf-8")
            zout.writestr(info, data)
    return buf.getvalue()


def _make_hwpx_with_section(section_xml: bytes) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zout:
        zout.writestr("Contents/section0.xml", section_xml)
    return buf.getvalue()


def _hwp_header_fixture(flags=0, version=5):
    """CFB v3 intake fixture, not a Hancom-rendered document."""
    free, end, fat = 0xffffffff, 0xfffffffe, 0xfffffffd
    header = bytearray(512)
    header[:8] = bytes.fromhex("d0cf11e0a1b11ae1")
    struct.pack_into("<HHHHH", header, 24, 62, 3, 0xfffe, 9, 6)
    struct.pack_into("<9I", header, 40, 0, 1, 1, 0, 4096, 2, 1, end, 0)
    struct.pack_into("<109I", header, 76, 0, *([free] * 108))
    allocation = struct.pack("<128I", fat, 4, end, end, end, *([free] * 123))
    directory = bytearray(1024)
    streams = [("Root Entry", 5, free, free, 1, 3, 384),
               ("BodyText", 1, 2, 3, 4, 0, 0), ("DocInfo", 2, free, free, free, 4, 4),
               ("FileHeader", 2, free, free, free, 0, 256), ("Section0", 2, free, free, free, 5, 4)]
    for i, (name, kind, left, right, child, start, size) in enumerate(streams):
        off = i * 128
        label = (name + "\0").encode("utf-16le")
        directory[off:off+len(label)] = label
        struct.pack_into("<HBBIII", directory, off+64, len(label), kind, 1, left, right, child)
        struct.pack_into("<IQ", directory, off+116, start, size)
    mini_fat = struct.pack("<128I", 1, 2, 3, end, end, end, *([free] * 122))
    mini_stream = bytearray(512)
    mini_stream[:17] = b"HWP Document File"
    mini_stream[35] = version
    struct.pack_into("<I", mini_stream, 36, flags)
    return bytes(header + allocation + directory[:512] + mini_fat + mini_stream + directory[512:])


class LiveWorkspaceSyncTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name).resolve() / "workspace"
        self.root.mkdir(parents=True)
        self.base_bytes = blank_document.blank_document()
        self.base_sha = hashlib.sha256(self.base_bytes).hexdigest()
        self.doc = self.root / "draft.hwpx"
        self.doc.write_bytes(self.base_bytes)

    def tearDown(self):
        self._tmp.cleanup()

    def test_only_transient_capture_lock_is_retried(self):
        locked = PermissionError("sharing violation")
        locked.winerror = 32
        capture = projects._capture
        attempts = []

        def capture_after_unlock(path, limit):
            if path == self.doc:
                attempts.append(path)
                if len(attempts) == 1:
                    raise locked
            return capture(path, limit)

        with patch.object(projects, "_capture", side_effect=capture_after_unlock):
            opened = workspace.open_live_document(self.root, self.doc)
        self.assertEqual(attempts, [self.doc, self.doc])
        self.assertEqual(Path(opened["file_path"]).read_bytes(), self.base_bytes)

    def test_evicted_checkpoint_never_becomes_current_render_target(self):
        with patch.object(workspace, "MAX_HISTORY_CHECKPOINTS", 1):
            original = workspace.open_live_document(self.root, self.doc)
            updated = _mutate_hwpx(self.base_bytes, "", "새 본문")
            self.doc.write_bytes(updated)
            changed = workspace.record_live_checkpoint(self.root, self.doc)
            self.assertEqual(len(changed["checkpoints"]), 1)
            self.assertNotEqual(original["file_path"], changed["file_path"])
            self.assertEqual(Path(changed["file_path"]).read_bytes(), updated)
            reopened = workspace.open_live_document(self.root, self.doc)
            self.assertEqual(changed["file_path"], reopened["file_path"])
            restored = workspace.restore_live_checkpoint(self.root, self.doc, "original")
            self.assertEqual(Path(restored["file_path"]).read_bytes(), self.base_bytes)

    def test_binary_snapshot_admission_is_read_only(self):
        doc = self.root / "binary.HWP"
        raw = _hwp_header_fixture()
        doc.write_bytes(raw)
        protected = self.root / "baseline.hwp"
        protected.write_bytes(raw)
        listed = {r["name"] for r in workspace.scan_workspace(self.root)["documents"]}
        self.assertEqual(listed, {self.doc.name, doc.name})
        opened = workspace.open_live_document(self.root, doc)
        cp = workspace.record_live_checkpoint(self.root, doc)
        self.assertFalse(cp["recorded"])
        self.assertEqual(cp["file_path"], opened["file_path"])
        self.assertEqual(cp["sha256"], hashlib.sha256(raw).hexdigest())
        for path in (opened["file_path"], opened["original_path"]):
            self.assertEqual(Path(path).suffix, ".hwp")
            self.assertEqual(Path(path).read_bytes(), raw)
        for operation, args in [(workspace.open_workspace_document, (doc,)),
                                (workspace.restore_live_checkpoint, (doc, "original")),
                                (workspace.export_live_document, (doc, self.root / "copy.hwpx")),
                                (workspace.open_in_hancom, (doc,)),
                                (workspace.open_live_document, (protected,))]:
            with self.subTest(operation=operation), self.assertRaises(ValueError):
                operation(self.root, *args)
        for invalid in (b"wrong", _hwp_header_fixture(version=4), *(_hwp_header_fixture(1 << n) for n in (1, 2, 4, 7, 8))):
            doc.write_bytes(invalid)
            with self.assertRaises(ValueError):
                workspace.record_live_checkpoint(self.root, doc)
        self.assertEqual(Path(opened["file_path"]).read_bytes(), raw)

    def test_tampered_snapshot_and_unsafe_packages_are_not_published(self):
        opened = workspace.open_live_document(self.root, self.doc)
        Path(opened["file_path"]).write_bytes(b"changed")
        with self.assertRaises(ValueError):
            workspace.record_live_checkpoint(self.root, self.doc)
        for member in ("../unsafe.xml", "contents/SECTION0.xml", "META-INF/signatures.xml",
                       "META-INF/encryption.xml", "ViewText/Section0"):
            buf = io.BytesIO()
            with zipfile.ZipFile(buf, "w") as archive:
                archive.writestr("Contents/section0.xml", "<section/>")
                archive.writestr(member, "extra")
            self.doc.write_bytes(buf.getvalue())
            with self.subTest(member=member), self.assertRaises(ValueError):
                workspace.open_live_document(self.root, self.doc)


    def test_protected_path_guard(self):
        self.assertFalse(workspace.is_protected_live_path(self.doc, self.root))
        protected_dir = self.root / "example"
        protected_dir.mkdir()
        self.assertTrue(workspace.is_protected_live_path(protected_dir / "form.hwpx", self.root))

        self.assertTrue(workspace.is_protected_live_path(self.root / ".hwpreview" / "originals" / "a.hwpx", self.root))
        self.assertTrue(workspace.is_protected_live_path(self.root / ".git" / "x.hwpx", self.root))
        self.assertTrue(workspace.is_protected_live_path(self.root / "baseline.hwpx", self.root))
        self.assertTrue(workspace.is_protected_live_path(self.root / "source.hwpx", self.root))

        project_dir = self.root / "proj"
        project_dir.mkdir()
        (project_dir / projects.PROJECT_FILE).write_text("{}", encoding="utf-8")
        self.assertTrue(workspace.is_protected_live_path(project_dir / "working.hwpx", self.root))

        listing = workspace.scan_workspace(self.root)
        by_rel = {item["relative_path"]: item for item in listing["documents"]}
        self.assertFalse(by_rel["draft.hwpx"]["protected"])

    def test_validate_hwpx_bytes_checks_zip_and_section_xml(self):
        self.assertEqual(workspace.validate_hwpx_bytes(self.base_bytes), self.base_sha)

        with self.assertRaises(ValueError):
            workspace.validate_hwpx_bytes(b"")
        with self.assertRaises(ValueError):
            workspace.validate_hwpx_bytes(b"not-a-zip")

        no_section = io.BytesIO()
        with zipfile.ZipFile(no_section, "w") as zout:
            zout.writestr("version.xml", b"<version/>")
        with self.assertRaises(ValueError):
            workspace.validate_hwpx_bytes(no_section.getvalue())

        bad_xml = _make_hwpx_with_section(b"<section><unclosed></section>")
        with self.assertRaises(ValueError):
            workspace.validate_hwpx_bytes(bad_xml)

        dtd_xml = _make_hwpx_with_section(b"<!DOCTYPE foo><section/>")
        with self.assertRaises(ValueError):
            workspace.validate_hwpx_bytes(dtd_xml)

    def test_open_checkpoint_and_restore_preserves_immutable_original(self):
        opened = workspace.open_live_document(self.root, self.doc)
        self.assertEqual(opened["sha256"], self.base_sha)
        self.assertEqual(opened["original_sha256"], self.base_sha)
        orig_path = Path(opened["original_path"])
        self.assertTrue(orig_path.is_file())
        self.assertEqual(orig_path.parent, self.root / ".hwpreview" / "originals")
        self.assertEqual(orig_path.read_bytes(), self.base_bytes)
        self.assertEqual(len(opened["checkpoints"]), 1)
        self.assertEqual(opened["checkpoints"][0]["reason"], "initial")

        # Duplicate record with unchanged bytes does not append.
        dup = workspace.record_live_checkpoint(self.root, self.doc, reason="auto")
        self.assertFalse(dup["recorded"])
        self.assertEqual(len(dup["checkpoints"]), 1)

        # Simulate Hancom / OMP edit #1
        v1_bytes = _mutate_hwpx(self.base_bytes, "", "EDIT-ONE")
        v1_sha = hashlib.sha256(v1_bytes).hexdigest()
        self.assertNotEqual(v1_sha, self.base_sha)
        self.doc.write_bytes(v1_bytes)

        cp1 = workspace.record_live_checkpoint(self.root, self.doc, reason="auto")
        self.assertTrue(cp1["recorded"])
        self.assertEqual(cp1["sha256"], v1_sha)
        self.assertEqual(len(cp1["checkpoints"]), 2)
        self.assertEqual(cp1["original_sha256"], self.base_sha)
        self.assertEqual(orig_path.read_bytes(), self.base_bytes)

        # Simulate edit #2
        v2_bytes = _mutate_hwpx(v1_bytes, "EDIT-ONE", "EDIT-TWO")
        v2_sha = hashlib.sha256(v2_bytes).hexdigest()
        self.doc.write_bytes(v2_bytes)

        # Re-opening never overwrites the original backup.
        reopened = workspace.open_live_document(self.root, self.doc)
        self.assertEqual(reopened["original_sha256"], self.base_sha)
        self.assertEqual(orig_path.read_bytes(), self.base_bytes)
        self.assertEqual(reopened["sha256"], v2_sha)

        # Unsaved edit #3 before restoring to v1 must create pre-restore checkpoint.
        v3_bytes = _mutate_hwpx(v2_bytes, "EDIT-TWO", "EDIT-THREE")
        v3_sha = hashlib.sha256(v3_bytes).hexdigest()
        self.doc.write_bytes(v3_bytes)

        cp1_id = cp1["checkpoint"]["id"]
        read_cp1 = workspace.read_live_checkpoint(self.root, self.doc, cp1_id)
        self.assertEqual(read_cp1["sha256"], v1_sha)

        restored_v1 = workspace.restore_live_checkpoint(self.root, self.doc, cp1_id)
        self.assertEqual(restored_v1["sha256"], v1_sha)
        self.assertEqual(self.doc.read_bytes(), v1_bytes)
        reasons = [row["reason"] for row in restored_v1["checkpoints"]]
        self.assertEqual(Path(restored_v1["file_path"]).read_bytes(), v1_bytes)
        self.assertIn("pre-restore", reasons)
        self.assertEqual(restored_v1["checkpoints"][-1]["reason"], "restore")
        self.assertTrue(any(row["sha256"] == v3_sha for row in restored_v1["checkpoints"]))

        # Restore back to immutable original
        restored_orig = workspace.restore_live_checkpoint(self.root, self.doc, "original")
        self.assertEqual(restored_orig["sha256"], self.base_sha)
        self.assertEqual(self.doc.read_bytes(), self.base_bytes)
        self.assertEqual(orig_path.read_bytes(), self.base_bytes)

    def test_protected_file_cannot_be_opened_or_created_live(self):
        protected_folder = self.root / "example"
        protected_folder.mkdir()
        target = protected_folder / "sample.hwpx"
        with target.open("wb") as stream:
            stream.write(self.base_bytes)

        with self.assertRaises(ValueError):
            workspace.open_live_document(self.root, target)
        with self.assertRaises(ValueError):
            workspace.create_blank_live_document(self.root, protected_folder / "new.hwpx")
        with self.assertRaises(ValueError):
            workspace.open_in_hancom(self.root, target)

    def test_create_blank_export_and_open_in_hancom(self):
        new_doc = self.root / "fresh.hwpx"
        created = workspace.create_blank_live_document(self.root, new_doc)
        self.assertTrue(new_doc.is_file())
        self.assertEqual(created["relative_path"], "fresh.hwpx")
        self.assertTrue(Path(created["original_path"]).is_file())

        # Refuses overwrite of existing file
        with self.assertRaises(ValueError):
            workspace.create_blank_live_document(self.root, new_doc)

        # Open in Hancom ensures original backup and delegates to os.startfile
        if os.name == "nt":
            with patch.object(os, "startfile", create=True) as mock_start:
                res = workspace.open_in_hancom(self.root, self.doc)
                self.assertTrue(res["opened"])
                mock_start.assert_called_once_with(str(self.doc))
                orig_file = self.root / ".hwpreview" / "originals" / f"{self.base_sha}.hwpx"
                self.assertTrue(orig_file.is_file())

        # Export active doc and original checkpoint
        out_dir = Path(self._tmp.name).resolve() / "exports"
        out_dir.mkdir()
        out_active = out_dir / "exported.hwpx"
        exp = workspace.export_live_document(self.root, self.doc, out_active)
        self.assertEqual(exp["sha256"], self.base_sha)
        self.assertEqual(out_active.read_bytes(), self.base_bytes)

        # Refuses overwrite and non-.hwpx
        with self.assertRaises(ValueError):
            workspace.export_live_document(self.root, self.doc, out_active)
        with self.assertRaises(ValueError):
            workspace.export_live_document(self.root, self.doc, out_dir / "bad.txt")
        with self.assertRaises(ValueError):
            workspace.export_live_document(self.root, self.doc, self.root / "example" / "blocked.hwpx")

    def test_native_host_live_rpcs_and_omp_guard(self):
        nonce = secrets.token_hex(32)
        host = native_host.NativeHost(nonce)
        hello = host.dispatch("hello", {"protocol": native_host.PROTOCOL, "nonce": nonce})
        self.assertEqual(hello["protocol"], native_host.PROTOCOL)

        listing = host.dispatch("open_workspace", {"root": str(self.root)})
        self.assertEqual(listing["root"], str(self.root))

        opened = host.dispatch("open_live_document", {"path": str(self.doc)})
        self.assertEqual(opened["sha256"], self.base_sha)

        # Simulate active OMP session
        class _FakeOmp:
            launch = object()

        host.omp = _FakeOmp()

        # Workspace refresh (list_workspace) & live document RPCs must still work while OMP runs
        refreshed = host.dispatch("list_workspace", {})
        self.assertEqual(refreshed["root"], str(self.root))

        v1_bytes = _mutate_hwpx(self.base_bytes, "", "HOST-EDIT")
        self.doc.write_bytes(v1_bytes)
        rec = host.dispatch("record_live_checkpoint", {"path": str(self.doc), "reason": "auto"})
        self.assertTrue(rec["recorded"])

        listed = host.dispatch("list_live_checkpoints", {"path": str(self.doc)})
        self.assertEqual(len(listed["checkpoints"]), 2)

        orig_read = host.dispatch("read_live_checkpoint", {"path": str(self.doc), "checkpoint_id": "original"})
        self.assertEqual(orig_read["sha256"], self.base_sha)

        restored = host.dispatch("restore_live_checkpoint", {"path": str(self.doc), "checkpoint_id": "original"})
        self.assertEqual(restored["sha256"], self.base_sha)
        self.assertEqual(self.doc.read_bytes(), self.base_bytes)

        blank_dest = self.root / "rpc_blank.hwpx"
        created = host.dispatch("new_blank_live_document", {"destination": str(blank_dest)})
        self.assertEqual(created["name"], "rpc_blank.hwpx")

        exp_dest = Path(self._tmp.name).resolve() / "rpc_export.hwpx"
        exp = host.dispatch(
            "export_live_document",
            {"path": str(self.doc), "destination": str(exp_dest), "checkpoint_id": "original"},
        )
        self.assertEqual(exp["sha256"], self.base_sha)

        if os.name == "nt":
            with patch.object(os, "startfile", create=True) as mock_start:
                res = host.dispatch("open_in_hancom", {"path": str(self.doc)})
                self.assertTrue(res["opened"])
                mock_start.assert_called_once_with(str(self.doc))

        # Switching to a DIFFERENT workspace folder must remain blocked while OMP is running
        other_root = Path(self._tmp.name).resolve() / "other_workspace"
        other_root.mkdir()
        with self.assertRaisesRegex(ValueError, "OMP"):
            host.dispatch("open_workspace", {"root": str(other_root)})


if __name__ == "__main__":
    unittest.main()
