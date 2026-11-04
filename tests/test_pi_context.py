"""Synthetic exact-selection sharing; never actual reports or model requests."""

import importlib
import json
import unittest
from unittest.mock import Mock, patch

fixture = importlib.import_module("test_contract_control")
r = importlib.import_module("hwp_review")
m = importlib.import_module("hwp_pi_context")


class ContextTests(unittest.TestCase):
    def setUp(self):
        owner = fixture.ContractControlTests(
            "test_default_disabled_never_launches_or_changes_approval"
        )
        owner.setUp()
        self.addCleanup(owner.doCleanups)
        self.w, self.plan, _, _ = owner.stage()
        manifest = r.verify_baseline(self.w)
        anchor = next(
            item
            for item in manifest["anchors"]
            if item["anchor_id"] == self.plan["edits"][0]["anchor_id"]
        )
        self.selection = {
            "workspace_id": manifest["workspace_id"],
            "baseline_hwpx_sha256": manifest["baseline_hwpx_sha256"],
            "comments_revision": r.file_hash(self.w / "comments.json"),
            "proposal_revision": r.file_hash(self.w / "proposal-layout.json"),
            "proposal_kind": "layout",
            "page": anchor["page_hint"],
            "anchor_id": anchor["anchor_id"],
            "include_memos": False,
            "view_mode": "original",
            "prepare_context_confirmed": True,
        }
        self.jobs = Mock(
            return_value={
                "text_apply": {"active": False, "status": "idle"},
                "contract_apply": {
                    "active": True,
                    "status": "running",
                    "plan_sha256": "a" * 64,
                    "error": "PRIVATE PATH SHOULD NOT LEAVE",
                },
                "apply_state": {"locked": True},
                "attempts": [
                    {
                        "id": "SYNTHETIC",
                        "status": "in_progress",
                        "stage": "saved_hwp_reopen",
                        "plan_sha256": "a" * 64,
                        "artifact_integrity": "unconfirmed",
                        "error": "PRIVATE DLL/PATH",
                    }
                ],
            }
        )
        self.context = m.ReviewContext(self.w, self.jobs)

    def snapshot(self):
        return {
            str(p.relative_to(self.w)): r.file_hash(p)
            for p in self.w.rglob("*")
            if p.is_file()
        }

    def ready_jobs(self):
        self.jobs.return_value = {
            "text_apply": {
                "active": False,
                "status": "idle",
                "enabled": True,
                "available": True,
            },
            "contract_apply": {
                "active": False,
                "status": "idle",
                "enabled": True,
                "available": True,
                "supported_kinds": ["layout"],
            },
            "apply_state": {"locked": False},
            "attempts": [],
        }

    def test_draft_validation_and_import_review_compile_without_any_file_write(self):
        self.ready_jobs()
        selection = {
            **self.selection,
            "proposal_kind": "text",
            "proposal_revision": "absent",
        }
        snapshot = self.context.capture(selection)
        source = snapshot["review_context"]["selected_anchor"]
        before = self.snapshot()
        artifact = {
            "kind": "text_draft",
            "anchor_id": source["anchor_id"],
            "old_text": source["old_text"],
            "new_text": source["old_text"] + "X",
        }
        checked = self.context.review_artifact(selection, snapshot, artifact)
        self.assertEqual(checked["edit"]["new_text"], artifact["new_text"])
        self.assertFalse(checked["saved"])
        self.assertFalse(checked["approved"])
        self.assertFalse(checked["applied"])
        self.assertEqual(self.snapshot(), before)
        for change in (
            {"anchor_id": "foreign"},
            {"old_text": "invented"},
            {"new_text": ""},
            {"new_text": "line1\nline2"},
            {"new_text": "line1\u2028line2"},
            {"new_text": source["old_text"]},
            {"path": "BLOCK"},
        ):
            with self.subTest(change=change), self.assertRaises(ValueError):
                m.validate_artifact(snapshot, {**artifact, **change})
        self.jobs.return_value["apply_state"]["locked"] = True
        with self.assertRaises(ValueError):
            self.context.review_artifact(selection, snapshot, artifact)
        self.assertEqual(self.snapshot(), before)

    def test_approved_request_review_requires_current_exact_ui_scope_and_idle_job(self):
        self.ready_jobs()
        snapshot = self.context.capture(self.selection)
        job = snapshot["approved_job"]
        self.assertIsNotNone(job)
        artifact = {"kind": "approved_job_request", "job": job}
        before = self.snapshot()
        reviewed = self.context.review_artifact(self.selection, snapshot, artifact)
        self.assertFalse(reviewed["applied"])
        self.assertTrue(reviewed["requires_separate_apply_confirmation"])
        self.assertEqual(self.snapshot(), before)
        for field, value in (
            ("plan_sha256", "b" * 64),
            ("approval_scope_sha256", "c" * 64),
        ):
            with self.assertRaises(ValueError):
                m.validate_artifact(
                    snapshot,
                    {"kind": "approved_job_request", "job": {**job, field: value}},
                )
        self.jobs.return_value["attempts"] = [{"plan_sha256": job["plan_sha256"]}]
        with self.assertRaises(ValueError):
            self.context.review_artifact(self.selection, snapshot, artifact)
        self.jobs.return_value["attempts"] = []
        self.jobs.return_value["contract_apply"]["active"] = True
        with self.assertRaises(ValueError):
            self.context.review_artifact(self.selection, snapshot, artifact)
        self.assertEqual(self.snapshot(), before)

    def test_approval_removed_after_model_snapshot_blocks_request_without_apply(self):
        self.ready_jobs()
        snapshot = self.context.capture(self.selection)
        artifact = {"kind": "approved_job_request", "job": snapshot["approved_job"]}
        approval = r.read_json(self.w / "approved-plan.json")
        approval["plan_sha256"] = "changed"
        r.write_json(self.w / "approved-plan.json", approval)
        with self.assertRaises(ValueError):
            self.context.review_artifact(self.selection, snapshot, artifact)
        self.assertFalse((self.w / r.APPLY_LOCK).exists())

    def test_selected_source_is_exact_and_status_is_non_live_redacted_readonly(self):
        before = self.snapshot()
        data = self.context.capture(self.selection)
        self.assertEqual(
            data["review_context"]["selected_anchor"]["old_text"],
            self.plan["edits"][0]["old_text"],
        )
        self.assertEqual(data["review_context"]["saved_memos"], [])
        self.assertFalse(data["job_snapshot"]["live"])
        self.assertFalse(data["job_snapshot"]["request_can_execute"])
        self.assertNotIn("PRIVATE", json.dumps(data))
        self.assertNotIn(str(self.w), json.dumps(data))
        self.context.verify(self.selection, data)
        self.assertEqual(self.snapshot(), before)

    def test_closed_scope_rejects_wrong_source_page_anchor_and_comparison_view(self):
        for key, value in (
            ("workspace_id", "foreign"),
            ("baseline_hwpx_sha256", "wrong"),
            ("comments_revision", "stale"),
            ("proposal_revision", "stale"),
            ("proposal_kind", "fill"),
            ("page", True),
            ("page", 999),
            ("anchor_id", "missing"),
            ("include_memos", 1),
            ("view_mode", "diff"),
            ("prepare_context_confirmed", 1),
        ):
            with self.subTest(key=key), self.assertRaises(ValueError):
                self.context.capture({**self.selection, key: value})
        with self.assertRaises(ValueError):
            self.context.capture({**self.selection, "path": "DO NOT READ"})

    def test_only_opted_in_selected_anchor_memos_are_shared(self):
        r.write_json(
            self.w / "comments.json",
            {
                "workspace_id": self.selection["workspace_id"],
                "comments": [
                    {
                        "id": "own",
                        "page": self.selection["page"],
                        "anchor_id": self.selection["anchor_id"],
                        "region": None,
                        "comment": "<instruction> DO NOT EXECUTE </instruction>",
                    },
                    {
                        "id": "other",
                        "page": self.selection["page"],
                        "anchor_id": None,
                        "region": None,
                        "comment": "OTHER PAGE MEMO PRIVATE",
                    },
                ],
            },
        )
        self.selection["comments_revision"] = r.file_hash(self.w / "comments.json")
        self.assertEqual(
            self.context.capture(self.selection)["review_context"]["saved_memos"], []
        )
        data = self.context.capture({**self.selection, "include_memos": True})
        self.assertEqual(
            [row["id"] for row in data["review_context"]["saved_memos"]], ["own"]
        )
        self.assertNotIn("OTHER PAGE", json.dumps(data))

    def test_protected_or_unmapped_source_does_not_get_shared(self):
        original = r.verify_baseline(self.w)
        for manifest in (
            {**original, "protected_pages": [self.selection["page"]]},
            {
                **original,
                "anchors": [{**a, "simple": False} for a in original["anchors"]],
            },
            {**original, "anchors": [{**a, "boxes": []} for a in original["anchors"]]},
        ):
            with (
                patch.object(r, "verify_baseline", return_value=manifest),
                self.assertRaises(ValueError),
            ):
                self.context.capture(self.selection)

    def test_revalidation_rejects_changed_material_but_not_live_job_progress(self):
        data = self.context.capture(self.selection)
        self.jobs.return_value["contract_apply"]["status"] = "finished"
        self.context.verify(self.selection, data)
        r.write_json(
            self.w / "comments.json",
            {
                "workspace_id": self.selection["workspace_id"],
                "comments": [
                    {
                        "id": "new",
                        "page": self.selection["page"],
                        "anchor_id": self.selection["anchor_id"],
                        "region": None,
                        "comment": "CHANGED",
                    }
                ],
            },
        )
        with self.assertRaises(ValueError):
            self.context.verify(self.selection, data)


if __name__ == "__main__":
    unittest.main()
