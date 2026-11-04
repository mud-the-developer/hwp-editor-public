"""Disposable byte binding + valid synthetic PDF ONLY. NO Hancom/real consent/history."""

from copy import deepcopy
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import test_review_attempts as fixtures

r = fixtures.r


class ResultIntegrityTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.w = Path(self.temp.name)
        self.run_dir = self.w / "runs/20261011T000000-a1b2c3d4"
        self.run_dir.mkdir(parents=True)
        self.manifest = {
            "workspace_id": "SYNTHETIC-INTEGRITY-ONLY",
            "baseline_hwpx_sha256": "a" * 64,
            "pages": [{"page": 1, "width": 300, "height": 400}],
        }
        self.plan = {
            "schema": 1,
            "workspace_id": self.manifest["workspace_id"],
            "baseline_hwpx_sha256": "a" * 64,
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
        for name in ("candidate.hwp", "candidate.hwpx"):
            (self.run_dir / name).write_bytes(b"SYNTHETIC-NOT-HWP-" + name.encode())
        self.pdf(self.w / "baseline.pdf", "SYNTHETIC BEFORE")
        self.pdf(self.run_dir / "candidate.pdf", "SYNTHETIC AFTER")
        self.pdf(self.run_dir / "candidate-buffer.pdf", "SYNTHETIC BUFFER")
        r.seal_result(self.run_dir, self.manifest, self.plan)
        self.binding = (self.run_dir / r.RESULT_FILE).read_bytes()
        self.baseline = r.file_hash(self.w / "baseline.pdf")

    def pdf(self, path, label):
        with r.fitz.open() as doc:
            doc.new_page(width=300, height=400).insert_text((20, 40), label)
            doc.save(path)

    def inspect(self):
        return r.inspect_result(
            self.run_dir,
            self.manifest,
            r.result_json(self.run_dir, "plan.json"),
            r.result_json(self.run_dir, "verification.json"),
        )

    def blocked(self):
        self.assertIn(self.inspect()["state"], r.RESULT_BAD_STATES)
        with self.assertRaises(ValueError):
            r.review_version(self.w, self.manifest, self.run_dir.name)
        self.assertEqual(r.review_versions(self.w, self.manifest), [])
        attempt = r.review_attempts(self.w, self.manifest)[0]
        self.assertEqual(attempt["status"], "blocked")
        self.assertEqual(attempt["stage"], "result_integrity")
        self.assertEqual(attempt["plan_sha256"], r.plan_hash(self.plan))
        self.assertEqual(attempt["rendered_issues"], [])
        self.assertFalse(attempt["source_unchanged"])
        self.assertEqual(r.file_hash(self.w / "baseline.pdf"), self.baseline)
        return attempt

    def test_complete_binding_is_type_checked_and_read_only(self):
        self.assertEqual(self.inspect()["state"], "verified")
        _, version = r.review_version(self.w, self.manifest, self.run_dir.name)
        self.assertEqual(version["artifact_integrity"], "verified")
        self.assertEqual(
            r.review_attempts(self.w, self.manifest)[0]["artifact_integrity"],
            "verified",
        )
        self.assertEqual((self.run_dir / r.RESULT_FILE).read_bytes(), self.binding)
        self.assertEqual(r.file_hash(self.w / "baseline.pdf"), self.baseline)

    def test_each_of_six_artifact_byte_changes_blocks_preview_not_reseals(self):
        for name in r.RESULT_ARTIFACTS:
            with self.subTest(name=name):
                path = self.run_dir / name
                before = path.read_bytes()
                path.write_bytes(before + b"\n")
                attempt = self.blocked()
                self.assertIn(
                    {"code": "artifact_changed_or_missing", "artifact": name},
                    attempt["artifact_issues"],
                )
                self.assertEqual(
                    (self.run_dir / r.RESULT_FILE).read_bytes(), self.binding
                )
                path.write_bytes(before)
        self.assertEqual(self.inspect()["state"], "verified")

    def test_valid_foreign_pdf_same_page_count_never_inherits_prior_pass(self):
        (self.run_dir / "candidate.pdf").unlink()
        self.pdf(self.run_dir / "candidate.pdf", "PRIVATE FOREIGN CONTENT")
        self.blocked()
        self.assertNotIn("PRIVATE FOREIGN", str(self.inspect()))
        self.assertEqual(
            r.read_json(self.run_dir / "verification.json")["status"],
            self.report["status"],
        )
        self.assertEqual((self.run_dir / r.RESULT_FILE).read_bytes(), self.binding)

    def test_same_size_same_timestamp_content_change_is_not_a_stat_cache_hit(self):
        path = self.run_dir / "candidate.hwp"
        info = path.stat()
        blob = path.read_bytes()
        path.write_bytes(b"X" + blob[1:])
        os.utime(path, ns=(info.st_atime_ns, info.st_mtime_ns))
        self.assertEqual(path.stat().st_size, len(blob))
        self.blocked()

    def test_each_missing_artifact_blocks_and_remains_associated_with_original_digest(
        self,
    ):
        for name in r.RESULT_ARTIFACTS:
            with self.subTest(name=name):
                path = self.run_dir / name
                before = path.read_bytes()
                path.unlink()
                self.blocked()
                path.write_bytes(before)
        self.assertEqual(self.inspect()["state"], "verified")

    def test_corrupt_and_oversized_json_do_not_hide_a_bound_failed_attempt_or_leak_values(
        self,
    ):
        for name in ("plan.json", "verification.json"):
            path = self.run_dir / name
            before = path.read_bytes()
            for blob in (b"{PRIVATE BROKEN", b"x" * (1024 * 1024 + 1)):
                path.write_bytes(blob)
                attempt = self.blocked()
                self.assertNotIn("PRIVATE BROKEN", str(attempt))
            path.write_bytes(before)
        self.assertEqual((self.run_dir / r.RESULT_FILE).read_bytes(), self.binding)

    def test_changed_plan_does_not_move_the_attempt_to_a_different_approval(self):
        r.write_json(
            self.run_dir / "plan.json",
            {**self.plan, "edits": [{"new_text": "PRIVATE REPLACEMENT"}]},
        )
        attempt = self.blocked()
        self.assertEqual(attempt["plan_sha256"], r.plan_hash(self.plan))
        self.assertNotIn("PRIVATE REPLACEMENT", str(attempt))

    def test_missing_or_invalid_binding_never_falls_back_to_legacy_pass(self):
        path = self.run_dir / r.RESULT_FILE
        path.unlink()
        self.assertEqual(self.inspect()["state"], "missing")
        self.blocked()
        for blob in (b"{bad json", b"x" * 16385, b"{}"):
            path.write_bytes(blob)
            self.assertEqual(self.inspect()["state"], "invalid")
            self.blocked()

    def test_binding_types_foreign_identity_unknown_paths_and_hashes_are_rejected(self):
        original = r.parse_json(self.binding)
        variants = [
            {**original, "schema": True},
            {**original, "policy": "foreign"},
            {**original, "workspace_id": "foreign"},
            {**original, "baseline_hwpx_sha256": "foreign"},
            {**original, "run_id": "../escape"},
            {**original, "plan_sha256": True},
            {**original, "artifact_sha256": []},
            {
                **original,
                "artifact_sha256": {
                    **original["artifact_sha256"],
                    "../PRIVATE": "a" * 64,
                },
            },
            {
                **original,
                "artifact_sha256": {
                    **original["artifact_sha256"],
                    "candidate.pdf": True,
                },
            },
            {**original, "unknown_path": "C:/PRIVATE/never-read"},
        ]
        for data in variants:
            r.write_json(self.run_dir / r.RESULT_FILE, data)
            with patch.object(r, "file_hash", wraps=r.file_hash) as hashes:
                self.blocked()
                self.assertTrue(
                    all(
                        Path(call.args[0]).name in {*r.RESULT_ARTIFACTS, "baseline.pdf"}
                        for call in hashes.call_args_list
                    )
                )
        self.assertEqual(r.file_hash(self.w / "baseline.pdf"), self.baseline)

    def test_partial_blocked_run_is_bound_without_creating_a_candidate_or_claiming_pass(
        self,
    ):
        for name in r.RESULT_ARTIFACTS:
            if name not in {"plan.json", "verification.json"}:
                (self.run_dir / name).unlink()
        r.write_json(
            self.run_dir / "verification.json",
            {**self.report, "status": "blocked", "stage": "native_edit_export"},
        )
        r.seal_result(self.run_dir, self.manifest, self.plan)
        self.assertEqual(self.inspect()["state"], "verified")
        self.assertEqual(r.review_versions(self.w, self.manifest), [])
        self.assertEqual(
            r.review_attempts(self.w, self.manifest)[0]["status"], "blocked"
        )
        self.pdf(self.run_dir / "candidate.pdf", "LATE UNVERIFIED PDF")
        attempt = self.blocked()
        self.assertIn(
            {"code": "unexpected_artifact", "artifact": "candidate.pdf"},
            attempt["artifact_issues"],
        )

    def test_legacy_history_is_explicitly_unbound_and_never_rewritten_or_backfilled(
        self,
    ):
        (self.run_dir / r.RESULT_FILE).unlink()
        report = deepcopy(self.report)
        report.pop("result_binding_policy")
        r.write_json(self.run_dir / "verification.json", report)
        before = {name: r.file_hash(self.run_dir / name) for name in r.RESULT_ARTIFACTS}
        self.assertEqual(self.inspect()["state"], "unbound_legacy")
        self.assertEqual(
            r.review_versions(self.w, self.manifest)[0]["artifact_integrity"],
            "unbound_legacy",
        )
        self.assertEqual(
            r.review_attempts(self.w, self.manifest)[0]["artifact_integrity"],
            "unbound_legacy",
        )
        with self.assertRaises(ValueError):
            r.seal_result(self.run_dir, self.manifest, self.plan)
        self.assertFalse((self.run_dir / r.RESULT_FILE).exists())
        self.assertEqual(
            before,
            {name: r.file_hash(self.run_dir / name) for name in r.RESULT_ARTIFACTS},
        )

    def test_reopened_hwpx_evidence_is_bound_and_drift_or_removal_blocks_results(self):
        path = self.run_dir / "candidate-reopened.hwpx"
        path.write_bytes(b"SYNTHETIC REOPENED STRUCTURE")
        report = {
            **self.report,
            "saved_hwp_reopen_export": {"reopened_hwpx_sha256": r.file_hash(path)},
        }
        r.write_json(self.run_dir / "verification.json", report)
        r.seal_result(self.run_dir, self.manifest, self.plan)
        self.assertEqual(self.inspect()["state"], "verified")
        binding = (self.run_dir / r.RESULT_FILE).read_bytes()
        before = path.read_bytes()
        path.write_bytes(b"CHANGED REOPENED STRUCTURE")
        attempt = self.blocked()
        self.assertIn(
            {"code": "artifact_changed_or_missing", "artifact": path.name},
            attempt["artifact_issues"],
        )
        path.unlink()
        self.blocked()
        self.assertEqual((self.run_dir / r.RESULT_FILE).read_bytes(), binding)
        path.write_bytes(before)
        self.assertEqual(self.inspect()["state"], "verified")

    def test_reopened_proof_sha_must_match_before_result_can_be_bound(self):
        for proof in ({"reopened_hwpx_sha256": "0" * 64}, None):
            r.write_json(
                self.run_dir / "verification.json",
                {**self.report, "saved_hwp_reopen_export": proof},
            )
            with self.assertRaisesRegex(ValueError, "structure proof"):
                r.seal_result(self.run_dir, self.manifest, self.plan)
        self.assertEqual((self.run_dir / r.RESULT_FILE).read_bytes(), self.binding)

    def test_late_unbound_reopened_structure_does_not_inherit_old_pass(self):
        (self.run_dir / "candidate-reopened.hwpx").write_bytes(b"LATE STRUCTURE")
        attempt = self.blocked()
        self.assertIn(
            {"code": "unexpected_artifact", "artifact": "candidate-reopened.hwpx"},
            attempt["artifact_issues"],
        )
        self.assertEqual((self.run_dir / r.RESULT_FILE).read_bytes(), self.binding)

    def test_progress_never_becomes_sealed_success_and_changed_during_checks_cannot_bind_pass(
        self,
    ):
        report = {**self.report, "status": "in_progress"}
        r.write_json(self.run_dir / "verification.json", report)
        self.assertEqual(self.inspect()["state"], "in_progress")
        with self.assertRaises(ValueError):
            r.seal_result(self.run_dir, self.manifest, self.plan)
        self.assertEqual(r.review_versions(self.w, self.manifest), [])
        r.write_json(self.run_dir / "verification.json", self.report)
        expected = r.result_artifact_hashes(self.run_dir)
        (self.run_dir / "candidate.hwp").write_bytes(b"CHANGED DURING CHECKS")
        with self.assertRaisesRegex(ValueError, "changed during"):
            r.seal_result(self.run_dir, self.manifest, self.plan, expected)
        self.assertEqual((self.run_dir / r.RESULT_FILE).read_bytes(), self.binding)
