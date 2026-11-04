"""Synthetic exact-text proposal API. No Native/private source/real approval."""
import json
import shutil
import unittest

from test_review_http import HttpTests, r


class TextProposalTests(HttpTests):
    def setUp(self):
        self.proposal = self.path / "proposal.json"
        self.previous = self.proposal.read_bytes() if self.proposal.exists() else None
        self.addCleanup(self.restore_proposal)
        self.addCleanup(shutil.rmtree, self.path / "proposal-history", ignore_errors=True)
        self.proposal.unlink(missing_ok=True)
        self.payload = {"workspace_id": self.manifest["workspace_id"], "baseline_hwpx_sha256": self.manifest["baseline_hwpx_sha256"],
                        "edits": [{"anchor_id": self.anchor["anchor_id"], "old_text": self.anchor["old_text"], "new_text": "Changed fixture phrase"}]}

    def restore_proposal(self):
        if self.previous is None:self.proposal.unlink(missing_ok=True)
        else:self.proposal.write_bytes(self.previous)

    def assert_blocked(self, payload, code=400, **headers):
        status, _ = self.post("/api/text-proposal", payload, **headers)
        self.assertEqual(status, code)
        self.assertFalse(self.proposal.exists())
        self.assertFalse((self.path / "proposal-history").exists())
        self.assertFalse((self.path / "approved-plan.json").exists())

    def test_publish_unapproved_plan_preserves_source_baselines_and_comments(self):
        files = ["fixture.hwp", "baseline.hwpx", "baseline.pdf", "comments.json"]
        before = {name:r.file_hash(self.path / name) for name in files}
        status, body = self.post("/api/text-proposal", self.payload)
        self.assertEqual(status, 200);reply = json.loads(body)
        self.assertEqual(reply["proposal"]["schema"], 1)
        self.assertFalse(reply["approved"])
        self.assertEqual(reply["proposal_revision"], r.file_hash(self.proposal))
        self.assertFalse((self.path / "approved-plan.json").exists())
        self.assertEqual(before, {name:r.file_hash(self.path / name) for name in files})
        self.assertFalse(list(self.path.glob(".proposal-*.tmp")))

    def test_raw_previous_bytes_archived_and_previous_approval_not_changed(self):
        old = {"schema": 1, **self.payload}
        r.write_json(self.proposal, old)
        status, _ = self.post("/api/approve", {"plan_sha256": r.plan_hash(old)})
        self.assertEqual(status, 200)
        approval = self.path / "approved-plan.json";self.addCleanup(approval.unlink, missing_ok=True)
        before = approval.read_bytes();raw = self.proposal.read_bytes()
        self.payload["edits"][0]["new_text"] = "Another fixture phrase"
        self.assertEqual(self.post("/api/text-proposal", self.payload)[0], 200)
        self.assertEqual(approval.read_bytes(), before)
        self.assertEqual((self.path / "proposal-history" / (r.digest(raw) + ".json")).read_bytes(), raw)
        self.assertNotEqual(r.plan_hash(r.read_json(self.proposal)), r.read_json(approval)["plan_sha256"])

    def test_missing_or_stale_revision_cannot_publish_or_archive(self):
        headers = {"Origin": self.origin, "X-Review-Token": self.token, "Content-Type": "application/json"}
        self.assertEqual(self.request("POST", "/api/text-proposal", self.payload, headers)[0], 428)
        self.assert_blocked(self.payload, 409, **{"If-Match": '"wrong"'})

    def test_missing_token_cross_origin_or_wrong_content_type_are_denied(self):
        for headers in ({"X-Review-Token": "wrong"}, {"Origin": "http://evil.invalid"}, {"Content-Type": "text/plain"}):
            status, _ = self.post("/api/text-proposal", self.payload, **headers)
            self.assertIn(status, (403, 415))
            self.assertFalse(self.proposal.exists())

    def test_wrong_workspace_or_baseline_cannot_publish(self):
        for field in ("workspace_id", "baseline_hwpx_sha256"):
            payload = {**self.payload, field: "WRONG"}
            self.assert_blocked(payload)

    def test_source_changed_after_open_blocks_state_and_publish(self):
        source = self.path / "fixture.hwp";raw = source.read_bytes();self.addCleanup(source.write_bytes, raw)
        source.write_bytes(b"CHANGED SYNTHETIC SOURCE")
        self.assertEqual(self.request("GET", "/api/state")[0], 400)
        self.assert_blocked(self.payload)

    def test_strict_cache_failure_does_not_escalate_to_native(self):
        self.payload["edits"][0]["new_text"] = "short"
        self.assert_blocked(self.payload)
        self.assertFalse((self.path / "proposal-native.json").exists())

    def test_delete_linebreak_duplicate_unknown_anchor_and_extra_actions_rejected(self):
        edit = self.payload["edits"][0]
        for new_text in ("", " ", "NEW\nTEXT"):
            self.assert_blocked({**self.payload, "edits": [{**edit, "new_text": new_text}]})
        self.assert_blocked({**self.payload, "edits": [edit, edit]})
        self.assert_blocked({**self.payload, "edits": [{**edit, "anchor_id": "UNKNOWN"}]})
        self.assert_blocked({**self.payload, "edits": [{**edit, "command": "DELETE"}]})
        self.assert_blocked({**self.payload, "schema": 2})

    def test_stale_original_and_noop_are_rejected(self):
        edit = self.payload["edits"][0]
        self.assert_blocked({**self.payload, "edits": [{**edit, "old_text": "WRONG"}]})
        self.assert_blocked({**self.payload, "edits": [{**edit, "new_text": edit["old_text"]}]})

    def test_empty_or_too_many_edits_rejected(self):
        self.assert_blocked({**self.payload, "edits": []})
        self.assert_blocked({**self.payload, "edits": self.payload["edits"] * 101})

    def test_protected_unmapped_or_complex_anchor_rejected(self):
        original = (self.path / "manifest.json").read_bytes();self.addCleanup((self.path / "manifest.json").write_bytes, original)
        for field, value in (("simple", False), ("page_hint", None), ("boxes", [])):
            manifest = json.loads(original);manifest["anchors"][0][field] = value;r.write_json(self.path / "manifest.json", manifest)
            self.assert_blocked(self.payload)
        manifest = json.loads(original);manifest["protected_pages"] = [1];r.write_json(self.path / "manifest.json", manifest)
        self.assert_blocked(self.payload)

    def test_invalid_prior_proposal_is_visible_with_raw_revision_and_archived_only_after_user_save(self):
        self.proposal.write_bytes(b"{INVALID SYNTHETIC JSON")
        raw = self.proposal.read_bytes()
        status, body = self.request("GET", "/api/state")
        self.assertEqual(status, 200);state = json.loads(body)
        self.assertIsNone(state["proposal"]);self.assertTrue(state["proposal_error"])
        self.assertEqual(state["proposal_revision"], r.digest(raw))
        self.assertEqual(self.post("/api/text-proposal", self.payload)[0], 200)
        self.assertEqual((self.path / "proposal-history" / (r.digest(raw) + ".json")).read_bytes(), raw)

    def test_existing_identical_plan_does_not_write_history_or_revoke_approval(self):
        r.write_json(self.proposal, {"schema": 1, **self.payload})
        raw = self.proposal.read_bytes()
        self.assertEqual(self.post("/api/text-proposal", self.payload)[0], 400)
        self.assertEqual(self.proposal.read_bytes(), raw)
        self.assertFalse((self.path / "proposal-history").exists())


def load_tests(loader, tests, pattern):
    suite = unittest.TestSuite()
    for name in TextProposalTests.__dict__:
        if name.startswith("test_"):suite.addTest(TextProposalTests(name))
    return suite


if __name__ == "__main__":unittest.main()
