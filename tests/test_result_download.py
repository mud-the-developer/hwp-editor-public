"""Synthetic result byte snapshots only. Never Hancom/real data/consent/resealing."""

import http.client
import io
from pathlib import Path
import re
import sys
import tempfile
import threading
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from scripts import hwp_review as r
from scripts.packaged_smoke_runtime import fixture


class ResultDownloadTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.w = Path(self.temp.name).resolve() / "SYNTHETIC"
        fixture(self.w, r)
        self.m = r.verify_baseline(self.w)
        self.run_id = "20261013T000000-a1b2c3d4"
        self.run_dir = self.w / "runs" / self.run_id
        self.run_dir.mkdir(parents=True)
        self.plan = {
            "schema": 1,
            "workspace_id": self.m["workspace_id"],
            "baseline_hwpx_sha256": self.m["baseline_hwpx_sha256"],
            "edits": [],
        }
        self.report = {
            "status": "checks_passed_needs_human_visual_review",
            "stage": "checks_passed",
            "result_binding_policy": r.RESULT_POLICY,
            "source_unchanged": True,
            "layout_gate": {"any_pixel_changed_pages": []},
        }
        r.write_json(self.run_dir / "plan.json", self.plan)
        r.write_json(self.run_dir / "verification.json", self.report)
        (self.run_dir / "candidate.hwp").write_bytes(
            b"SYNTHETIC NOT HWP; DOWNLOAD ONLY"
        )
        (self.run_dir / "candidate.hwpx").write_bytes(
            (self.w / "baseline.hwpx").read_bytes()
        )
        for name in ("candidate.pdf", "candidate-buffer.pdf"):
            (self.run_dir / name).write_bytes((self.w / "baseline.pdf").read_bytes())
        r.seal_result(self.run_dir, self.m, self.plan)
        self.before = {
            str(p.relative_to(self.w)): r.file_hash(p)
            for p in self.w.rglob("*")
            if p.is_file()
        }

    def assert_preserved(self):
        self.assertEqual(
            self.before,
            {
                str(p.relative_to(self.w)): r.file_hash(p)
                for p in self.w.rglob("*")
                if p.is_file()
            },
        )
        self.assertFalse((self.w / "approved-plan.json").exists())

    def test_exact_hwp_and_reopened_pdf_metadata_bytes_and_hashes_are_read_only(self):
        _, version = r.review_version(self.w, self.m, self.run_id)
        for kind, name in r.DOWNLOAD_FILES.items():
            data, sha = r.result_download(self.w, self.run_id, kind)
            self.assertEqual(data, (self.run_dir / name).read_bytes())
            self.assertEqual(sha, r.digest(data))
            self.assertEqual(
                version["downloads"][kind], {"sha256": sha, "bytes": len(data)}
            )
        self.assert_preserved()

    def test_all_six_artifact_changes_are_blocked_without_resealing(self):
        binding = (self.run_dir / r.RESULT_FILE).read_bytes()
        for name in r.RESULT_ARTIFACTS:
            path = self.run_dir / name
            old = path.read_bytes()
            path.write_bytes(old + b"CHANGED")
            with self.assertRaises(ValueError):
                r.result_download(self.w, self.run_id, "hwp")
            self.assertEqual((self.run_dir / r.RESULT_FILE).read_bytes(), binding)
            path.write_bytes(old)
        self.assert_preserved()

    def test_bound_blocked_or_in_progress_result_is_not_downloadable(self):
        for status in ("blocked", "in_progress"):
            r.write_json(
                self.run_dir / "verification.json", {**self.report, "status": status}
            )
            if status == "blocked":
                r.seal_result(self.run_dir, self.m, self.plan)
            with self.assertRaises(ValueError):
                r.result_download(self.w, self.run_id, "pdf")
        self.assertFalse((self.w / "approved-plan.json").exists())

    def test_legacy_success_remains_unbound_and_cannot_be_downloaded(self):
        (self.run_dir / r.RESULT_FILE).unlink()
        legacy = dict(self.report)
        legacy.pop("result_binding_policy")
        r.write_json(self.run_dir / "verification.json", legacy)
        _, version = r.review_version(self.w, self.m, self.run_id)
        self.assertEqual(version["artifact_integrity"], "unbound_legacy")
        self.assertEqual(version["downloads"], {})
        with self.assertRaises(ValueError):
            r.result_download(self.w, self.run_id, "pdf")
        self.assertFalse((self.run_dir / r.RESULT_FILE).exists())

    def test_invalid_identity_kind_and_requested_arbitrary_path_never_read_source(self):
        for run_id, kind in (
            ("../OUTSIDE", "hwp"),
            (self.run_id, "../plan.json"),
            (self.run_id, "source"),
            ("20261013T000000-ffffffff", "hwp"),
        ):
            with self.assertRaises((ValueError, FileNotFoundError)):
                r.result_download(self.w, run_id, kind)
        self.assert_preserved()

    def test_changed_original_and_baseline_block_snapshot(self):
        for name in ("synthetic-source.hwp", "baseline.hwpx", "baseline.pdf"):
            path = self.w / name
            old = path.read_bytes()
            path.write_bytes(old + b"CHANGED")
            with self.assertRaises(ValueError):
                r.result_download(self.w, self.run_id, "hwp")
            path.write_bytes(old)
        self.assert_preserved()

    def test_size_budget_disables_only_oversized_file_without_reading_it_as_download(
        self,
    ):
        with patch.object(r, "DOWNLOAD_LIMIT", 64):
            _, version = r.review_version(self.w, self.m, self.run_id)
            self.assertIn("hwp", version["downloads"])
            self.assertNotIn("pdf", version["downloads"])
            with self.assertRaises(ValueError):
                r.result_download(self.w, self.run_id, "pdf")
        self.assert_preserved()

    def test_snapshot_bytes_changed_after_initial_integrity_check_never_served(self):
        target = self.run_dir / "candidate.hwp"
        original = Path.open
        reads = 0

        def opening(path, *args, **kwargs):
            nonlocal reads
            if path == target and args and args[0] == "rb":
                reads += 1
                if reads == 2:
                    return io.BytesIO(b"CORRUPT SNAPSHOT")
            return original(path, *args, **kwargs)

        with patch.object(Path, "open", opening):
            with self.assertRaises(ValueError):
                r.result_download(self.w, self.run_id, "hwp")
        self.assert_preserved()

    def test_other_artifact_drift_during_snapshot_read_is_detected(self):
        target = self.run_dir / "candidate.hwp"
        original = Path.open
        reads = 0
        plan = self.run_dir / "plan.json"
        old = plan.read_bytes()

        class Snapshot(io.BytesIO):
            def read(self, *args):
                data = super().read(*args)
                plan.write_bytes(b"CHANGED WHILE READING")
                return data

        def opening(path, *args, **kwargs):
            nonlocal reads
            if path == target and args and args[0] == "rb":
                reads += 1
                if reads == 2:
                    return Snapshot(target.read_bytes())
            return original(path, *args, **kwargs)

        binding = (self.run_dir / r.RESULT_FILE).read_bytes()
        with patch.object(Path, "open", opening):
            with self.assertRaises(ValueError):
                r.result_download(self.w, self.run_id, "hwp")
        self.assertEqual((self.run_dir / r.RESULT_FILE).read_bytes(), binding)
        plan.write_bytes(old)
        self.assert_preserved()

    def test_http_token_origin_exact_route_and_attachment_hashes(self):
        server = r.create_review_server(self.w)
        thread = threading.Thread(target=server.serve_forever)
        thread.start()

        def get(path, headers=None):
            conn = http.client.HTTPConnection(
                "127.0.0.1", server.server_port, timeout=5
            )
            try:
                conn.request("GET", path, headers=headers or {})
                response = conn.getresponse()
                return response.status, dict(response.getheaders()), response.read()
            finally:
                conn.close()

        try:
            match = re.search(r"const token='([a-f0-9]+)'", get("/")[2].decode())
            if match is None:
                self.fail("Synthetic token missing")
            token = match.group(1)
            route = f"/result/{self.run_id}/pdf"
            for headers in (
                {},
                {"X-Review-Token": "WRONG"},
                {"X-Review-Token": token, "Origin": "http://evil.invalid"},
                {"X-Review-Token": token, "Host": "evil.invalid"},
            ):
                self.assertEqual(get(route, headers)[0], 403)
            status, headers, blob = get(route, {"X-Review-Token": token})
            self.assertEqual(status, 200)
            self.assertEqual(blob, (self.run_dir / "candidate.pdf").read_bytes())
            self.assertEqual(headers["X-Artifact-Sha256"], r.digest(blob))
            self.assertEqual(headers["ETag"], '"' + r.digest(blob) + '"')
            self.assertEqual(headers["Content-Length"], str(len(blob)))
            self.assertEqual(headers["Cache-Control"], "no-store")
            self.assertTrue(
                headers["Content-Disposition"].startswith(
                    "attachment; filename*=UTF-8''"
                )
            )
            self.assertIn(self.run_id, headers["Content-Disposition"])
            for suffix in ("plan.json", "../source", "hwp%00"):
                self.assertEqual(
                    get(f"/result/{self.run_id}/{suffix}", {"X-Review-Token": token})[
                        0
                    ],
                    404,
                )
        finally:
            server.shutdown()
            thread.join(5)
            server.server_close()
        self.assert_preserved()


if __name__ == "__main__":
    unittest.main()
