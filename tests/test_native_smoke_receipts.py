"""Synthetic status persistence under real Windows read locks; no UI/model/Native."""
from contextlib import contextmanager
import ctypes
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import hwp_native_smoke as smoke


@contextmanager
def read_lock_without_delete_sharing(path):
    from ctypes import wintypes
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    create = kernel.CreateFileW
    create.argtypes = (wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD,
                       wintypes.LPVOID, wintypes.DWORD, wintypes.DWORD, wintypes.HANDLE)
    create.restype = wintypes.HANDLE
    close = kernel.CloseHandle
    close.argtypes = (wintypes.HANDLE,)
    close.restype = wintypes.BOOL
    handle = create(str(path), 0x80000000, 1, None, 3, 0, None)
    if handle == ctypes.c_void_p(-1).value:
        raise ctypes.WinError(ctypes.get_last_error())
    try:
        yield
    finally:
        close(handle)


class NativeSmokeReceiptTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.root = Path(directory.name).resolve()
        self.records = smoke._HostRecords(self.root)
        self.index = self.root / "HOST.json"
        self.records.publish({"status": "starting", "omp_released": False})

    def test_closed_state_is_readable_without_rewriting_previous_generation(self):
        first = self.root / "HOST-records/00000001.json"
        before = first.read_bytes()
        self.records.publish({"status": "ready", "omp_released": False})
        self.records.publish({"status": "closed", "omp_released": True})
        final = smoke.read_host_receipt(self.index)
        self.assertEqual((final["status"], final["omp_released"], final["receipt_generation"]),
                         ("closed", True, 3))
        self.assertEqual(json.loads(before), json.loads(first.read_bytes()))

    @unittest.skipUnless(os.name == "nt", "Windows sharing contract")
    def test_held_index_and_old_snapshot_cannot_block_next_state_or_release(self):
        first = self.root / "HOST-records/00000001.json"
        with read_lock_without_delete_sharing(self.index), read_lock_without_delete_sharing(first):
            self.records.publish({"status": "closed", "omp_released": True})
            final = smoke.read_host_receipt(self.index)
            self.assertEqual((final["status"], final["omp_released"], final["receipt_generation"]),
                             ("closed", True, 2))
            self.assertEqual(json.loads(first.read_text())["status"], "starting")

    def test_unpublished_partial_record_is_not_reported_as_current(self):
        (self.root / "HOST-records/00000002.json").write_text('{"status":"clo', encoding="utf-8")
        current = smoke.read_host_receipt(self.index)
        self.assertEqual((current["status"], current["receipt_generation"]), ("starting", 1))

    def test_malformed_latest_published_record_has_no_older_fallback(self):
        (self.root / "HOST-records/00000002.json").write_text('{"status":', encoding="utf-8")
        (self.root / "HOST-records/00000002.ready").mkdir()
        with self.assertRaises(ValueError):
            smoke.read_host_receipt(self.index)

    def test_generation_mismatch_cannot_borrow_a_publication_marker(self):
        (self.root / "HOST-records/00000002.json").write_text(
            json.dumps({"status": "closed", "receipt_generation": 1}), encoding="utf-8")
        (self.root / "HOST-records/00000002.ready").mkdir()
        with self.assertRaisesRegex(ValueError, "generation"):
            smoke.read_host_receipt(self.index)

    def test_failed_publication_preserves_partial_and_refuses_any_retry(self):
        partial = self.root / "HOST-records/00000002.json"
        partial.write_bytes(b"PREEXISTING-STOPPED-RECORD")
        with self.assertRaises(FileExistsError):
            self.records.publish({"status": "closed"})
        with self.assertRaisesRegex(RuntimeError, "without retry"):
            self.records.publish({"status": "ready"})
        self.assertEqual(partial.read_bytes(), b"PREEXISTING-STOPPED-RECORD")
        self.assertFalse((self.root / "HOST-records/00000002.ready").exists())
        self.assertEqual(smoke.read_host_receipt(self.index)["receipt_generation"], 1)


if __name__ == "__main__":
    unittest.main()
