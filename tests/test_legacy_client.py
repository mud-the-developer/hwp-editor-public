"""Synthetic regression of the real desktop loopback handler; no Native job runs."""

from pathlib import Path
import sys
import tempfile
import threading
import unittest
import zipfile

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

import hwp_review as r
from hwp_legacy_client import LegacyReviewClient, LegacyReviewError
from packaged_smoke_runtime import fixture


class LegacyClientTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name).resolve()
        self.workspace = self.root / "SYNTHETIC"
        fixture(self.workspace, r)
        baseline = self.workspace / "baseline.hwpx"
        with zipfile.ZipFile(baseline, "w") as package:
            package.writestr("mimetype", "application/hwp+zip")
            package.writestr(
                "Contents/section0.xml",
                '<sec xmlns="http://www.hancom.co.kr/hwpml/2011/section" '
                'xmlns:hp="http://www.hancom.co.kr/hwpml/2011/paragraph">'
                '<hp:p id="0"><hp:run><hp:t>Alpha original</hp:t></hp:run></hp:p>'
                '<hp:p id="1"><hp:run><hp:t>Bravo original</hp:t></hp:run></hp:p></sec>',
            )
        self.manifest = r.read_json(self.workspace / "manifest.json")
        anchors, _ = r.index_hwpx(baseline)
        for anchor in anchors:
            anchor.pop("raw")
            anchor.update(page_hint=1, boxes=[[10, 10, 190, 35]])
        self.manifest.update(anchors=anchors, baseline_hwpx_sha256=r.file_hash(baseline))
        r.write_json(self.workspace / "manifest.json", self.manifest)
        self.edits = [
            {"anchor_id": a["anchor_id"], "old_text": a["old_text"],
             "new_text": a["old_text"].replace("original", "replacement")}
            for a in anchors
        ]
        self.ids = [edit["anchor_id"] for edit in self.edits]
        self.server = r.create_review_server(self.workspace)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.addCleanup(self.stop_server)
        self.client = LegacyReviewClient(
            self.server.server_port, self.server.review_token,
            self.workspace, self.manifest["workspace_id"],
        )

    def stop_server(self):
        self.server.shutdown()
        self.thread.join(5)
        self.server.server_close()

    def hashes(self):
        return {p.relative_to(self.workspace): r.file_hash(p)
                for p in self.workspace.rglob("*") if p.is_file()}

    def assert_error(self, status, fn, *args, **kwargs):
        with self.assertRaises(LegacyReviewError) as caught:
            fn(*args, **kwargs)
        self.assertEqual(caught.exception.status, status)
        self.assertNotIn(self.server.review_token, str(caught.exception))

    def test_visible_exact_order_approval_and_separate_stale_apply(self):
        empty = self.client.snapshot("text")
        self.client.save_proposal("text", empty, self.edits)
        first = self.client.snapshot("text")
        approval = self.workspace / "approved-plan.json"
        self.assert_error(409, self.client.save_proposal, "text", empty, self.edits)
        self.assert_error(None, self.client.approve, "text", first,
                          reviewed=self.ids, confirmed=1)
        self.assert_error(None, self.client.approve, "text", first,
                          reviewed=self.ids[::-1], confirmed=True)
        self.assert_error(None, self.client.apply, "text", first, confirmed=True)
        self.assertFalse(approval.exists())
        self.assertIs(self.client.approve("text", first, reviewed=self.ids, confirmed=True)["approved"], True)
        approved = self.client.snapshot("text")
        self.assertEqual(approved["approved_sha256"], first["proposal_sha256"])
        approval_bytes = approval.read_bytes()
        self.assert_error(None, self.client.apply, "text", approved, confirmed=1)
        self.assertFalse((self.workspace / r.APPLY_LOCK).exists())
        self.assertIsNone(self.server.text_apply_controller.thread)

        replacement = [{**edit, "new_text": edit["new_text"] + " updated"} for edit in self.edits]
        self.client.save_proposal("text", approved, replacement)
        before = self.hashes()
        self.assert_error(400, self.client.approve, "text", first,
                          reviewed=self.ids, confirmed=True)
        self.assert_error(409, self.client.apply, "text", approved, confirmed=True)
        self.assert_error(None, self.client.apply, "text", self.client.snapshot("text"), confirmed=True)
        self.assertEqual(approval.read_bytes(), approval_bytes)
        self.assertEqual(self.hashes(), before)
        self.assertIsNone(self.server.text_apply_controller.thread)

    def test_comments_are_cas_only_and_missing_fill_is_a_server_error(self):
        state = self.client.snapshot("text")
        self.assertEqual(self.client.snapshot("layout")["proposal_kind"], "layout")
        self.assert_error(400, self.client.snapshot, "fill")
        self.assert_error(None, self.client.snapshot, "native")
        wrong = LegacyReviewClient(self.server.server_port, self.server.review_token,
                                   self.workspace, "OTHER")
        self.assert_error(None, wrong.snapshot, "text")
        comments = [{"id": "note-1", "page": 1, "anchor_id": self.ids[0],
                     "region": [0.2, 0.3], "comment": "Review only"}]
        self.assertIs(self.client.save_comments(state, comments)["saved"], True)
        self.assert_error(409, self.client.save_comments, state, [])
        self.assertEqual(self.client.snapshot("text")["comments"]["comments"], comments)
        self.assertFalse((self.workspace / "approved-plan.json").exists())
        self.assertFalse((self.workspace / "proposal.json").exists())

    def stage_result(self, hwp_bytes, *, bound=True):
        run_id = "20261013T000000-a1b2c3d4"
        directory = self.workspace / "runs" / run_id
        directory.mkdir(parents=True)
        plan = {"schema": 1, "workspace_id": self.manifest["workspace_id"],
                "baseline_hwpx_sha256": self.manifest["baseline_hwpx_sha256"],
                "edits": self.edits}
        report = {
            "status": "checks_passed_needs_human_visual_review",
            "stage": "checks_passed", "source_unchanged": True,
        }
        if bound:
            report["result_binding_policy"] = r.RESULT_POLICY
        r.write_json(directory / "plan.json", plan)
        r.write_json(directory / "verification.json", report)
        (directory / "candidate.hwp").write_bytes(hwp_bytes)
        (directory / "candidate.hwpx").write_bytes((self.workspace / "baseline.hwpx").read_bytes())
        for name in ("candidate.pdf", "candidate-buffer.pdf"):
            (directory / name).write_bytes((self.workspace / "baseline.pdf").read_bytes())
        if bound:
            r.seal_result(directory, self.manifest, plan)
        return run_id, directory

    def test_comparison_and_export_leave_review_workspace_unchanged(self):
        run_id, directory = self.stage_result(bytes.fromhex("d0cf11e0a1b11ae1") + b"SYNTHETIC ONLY")
        state = self.client.snapshot("text")
        before = self.hashes()
        png = self.client.page(1)
        self.assertEqual(png, (self.workspace / "pages/page-001.png").read_bytes())
        for side in ("before", "after", "diff"):
            self.assertTrue(self.client.page(1, result_id=run_id, side=side).startswith(b"\x89PNG"))
        self.assert_error(None, self.client.page, 1, result_id="../source", side="before")
        self.assert_error(400, self.client.page, 2, result_id=run_id)
        for kind in ("hwp", "pdf"):
            destination = self.root / f"download.{kind}"
            receipt = self.client.export(state, run_id, kind, destination)
            self.assertEqual(destination.read_bytes(), (directory / f"candidate.{kind}").read_bytes())
            self.assertEqual(receipt["sha256"], r.file_hash(destination))
            self.assert_error(None, self.client.export, state, run_id, kind, destination)
        self.assert_error(None, self.client.export, state, run_id, "hwp", self.workspace / "new.hwp")
        self.assert_error(None, self.client.export, state, run_id, "hwp", Path(self.manifest["source"]))
        self.assertEqual(before, self.hashes())

        pdf = directory / "candidate.pdf"
        pdf.write_bytes(pdf.read_bytes() + b"changed")
        changed = self.hashes()
        destination = self.root / "tampered.pdf"
        self.assert_error(400, self.client.page, 1, result_id=run_id)
        self.assert_error(400, self.client.export, state, run_id, "pdf", destination)
        self.assertFalse(destination.exists())
        self.assertEqual(changed, self.hashes())

    def test_verified_metadata_does_not_replace_document_header_check(self):
        run_id, _ = self.stage_result(b"SYNTHETIC NON-HWP BYTES")
        state = self.client.snapshot("text")
        self.assertEqual(state["versions"][0]["artifact_integrity"], "verified")
        before = self.hashes()
        destination = self.root / "invalid.hwp"
        self.assert_error(None, self.client.export, state, run_id, "hwp", destination)
        self.assertFalse(destination.exists())
        self.assertEqual(before, self.hashes())

    def test_unbound_history_can_be_compared_but_not_downloaded(self):
        run_id, _ = self.stage_result(b"SYNTHETIC LEGACY BYTES", bound=False)
        state = self.client.snapshot("text")
        self.assertEqual(state["versions"][0]["artifact_integrity"], "unbound_legacy")
        before = self.hashes()
        self.assertTrue(self.client.page(1, result_id=run_id).startswith(b"\x89PNG"))
        destination = self.root / "legacy.pdf"
        self.assert_error(None, self.client.export, state, run_id, "pdf", destination)
        self.assertFalse(destination.exists())
        self.assertEqual(before, self.hashes())


if __name__ == "__main__":
    unittest.main()
