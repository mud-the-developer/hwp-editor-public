"""Owned synthetic checkpoint tests; no real documents, approvals or Hancom."""

from copy import deepcopy
import http.client
import json
from pathlib import Path
import re
import sys
import tempfile
import threading
import unittest
from unittest.mock import patch
import zipfile

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from scripts import hwp_review as r, hwp_review_session as session
from scripts.packaged_smoke_runtime import fixture


class ReviewSessionTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.w = Path(self.temp.name) / "SYNTHETIC"
        fixture(self.w, r)
        with zipfile.ZipFile(self.w / "baseline.hwpx", "w") as package:
            package.writestr("mimetype", "application/hwp+zip")
            package.writestr(
                "Contents/section0.xml",
                '<sec xmlns="http://www.hancom.co.kr/hwpml/2011/section" xmlns:hp="http://www.hancom.co.kr/hwpml/2011/paragraph"><hp:p><hp:run charPrIDRef="1"><hp:t>SYNTHETIC ORIGINAL TEXT</hp:t></hp:run><hp:linesegarray><hp:lineseg textpos="0"/></hp:linesegarray></hp:p></sec>',
            )
        self.m = r.read_json(self.w / "manifest.json")
        anchor = r.index_hwpx(self.w / "baseline.hwpx")[0][0]
        anchor.pop("raw")
        anchor.update(page_hint=1, boxes=[[20, 20, 230, 50]])
        self.m.update(
            anchors=[anchor], baseline_hwpx_sha256=r.file_hash(self.w / "baseline.hwpx")
        )
        r.write_json(self.w / "manifest.json", self.m)
        edit = {
            "anchor_id": anchor["anchor_id"],
            "old_text": anchor["old_text"],
            "new_text": "SYNTHETIC NEW TEXT",
        }
        self.data = {
            "schema": 1,
            "workspace_id": self.m["workspace_id"],
            "baseline_hwpx_sha256": self.m["baseline_hwpx_sha256"],
            "comments_revision": r.file_hash(self.w / "comments.json"),
            "proposal_revision": "absent",
            "cursor": {"page": 1, "view_mode": "original", "version_id": None},
            "memo": {
                "page": 1,
                "text": "UNSAVED <img src=x onerror=alert(1)> ONLY",
                "anchor_id": anchor["anchor_id"],
                "region": [0.1, 0.2],
                "editing_id": None,
            },
            "text_draft": {
                "revision": "absent",
                "base_edits": [],
                "edits": [edit],
                "target_id": anchor["anchor_id"],
                "pending_text": "PENDING\nINCOMPLETE",
                "open": True,
            },
        }
        self.original = {
            str(p.relative_to(self.w)): r.file_hash(p)
            for p in self.w.rglob("*")
            if p.is_file()
        }

    def preserved(self):
        for name, sha in self.original.items():
            self.assertEqual(r.file_hash(self.w / name), sha)
        self.assertFalse((self.w / "approved-plan.json").exists())
        self.assertFalse((self.w / "proposal.json").exists())
        self.assertFalse((self.w / r.APPLY_LOCK).exists())

    def test_missing_and_roundtrip_cursor_memo_pending_draft_never_publish_or_approve(
        self,
    ):
        self.assertEqual(
            session.read_snapshot(self.w, self.m),
            {"revision": "absent", "snapshot": None, "issue": None},
        )
        result = session.save_snapshot(self.w, self.m, self.data, '"absent"')
        self.assertFalse(result["document_changed"])
        self.assertFalse(result["approval_changed"])
        saved = session.read_snapshot(self.w, self.m)
        self.assertEqual(saved["snapshot"], self.data)
        self.assertEqual(saved["revision"], result["revision"])
        self.preserved()

    def test_raw_history_and_explicit_clear_keep_old_draft_and_all_inputs(self):
        result = session.save_snapshot(self.w, self.m, self.data, '"absent"')
        raw = (self.w / session.FILE).read_bytes()
        cleared = session.clear_snapshot(self.w, '"' + result["revision"] + '"')
        self.assertEqual(
            (self.w / "session-history" / (r.digest(raw) + ".json")).read_bytes(), raw
        )
        self.assertIsNone(session.read_snapshot(self.w, self.m)["snapshot"])
        self.assertTrue(cleared["saved"])
        self.preserved()

    def test_stale_and_same_revision_or_noop_do_not_replace_or_create_history(self):
        session.save_snapshot(self.w, self.m, self.data, '"absent"')
        before = (self.w / session.FILE).read_bytes()
        for rev in ('"absent"', '"wrong"', '"' + r.digest(before) + '"'):
            with self.assertRaises(ValueError):
                session.save_snapshot(self.w, self.m, self.data, rev)
        self.assertEqual((self.w / session.FILE).read_bytes(), before)
        self.assertFalse((self.w / "session-history").exists())
        self.preserved()

    def test_wrong_identity_and_authority_or_unknown_fields_are_not_checkpoint_data(
        self,
    ):
        for data in (
            {**self.data, "workspace_id": "foreign"},
            {**self.data, "baseline_hwpx_sha256": "foreign"},
            {**self.data, "approved": True},
            {**self.data, "schema": True},
            {**self.data, "execute": "HWP"},
        ):
            with self.assertRaises(ValueError):
                session.save_snapshot(self.w, self.m, data, '"absent"')
        self.assertFalse((self.w / session.FILE).exists())
        self.preserved()

    def test_cursor_memo_target_and_draft_limits_are_closed(self):
        variants = []
        for field, value in (
            ("page", 0),
            ("page", True),
            ("view_mode", "Native"),
            ("version_id", "../OUTSIDE"),
        ):
            variants.append(
                {**self.data, "cursor": {**self.data["cursor"], field: value}}
            )
        for field, value in (
            ("text", "x" * 10001),
            ("region", [1.1, 0.2]),
            ("anchor_id", "UNKNOWN"),
            ("editing_id", " "),
        ):
            variants.append({**self.data, "memo": {**self.data["memo"], field: value}})
        for field, value in (
            ("open", 1),
            ("target_id", "UNKNOWN"),
            ("revision", "not-a-hash"),
            ("pending_text", "x" * 20001),
            ("edits", self.data["text_draft"]["edits"] * 101),
        ):
            variants.append(
                {**self.data, "text_draft": {**self.data["text_draft"], field: value}}
            )
        for data in variants:
            with self.assertRaises(ValueError):
                session.validate(data, self.m)
        self.preserved()

    def test_duplicate_stale_and_extra_edit_fields_cannot_restore_execution(self):
        edit = self.data["text_draft"]["edits"][0]
        for entries in (
            [edit, edit],
            [{**edit, "old_text": "FOREIGN"}],
            [{**edit, "region": [0, 0, 300, 400]}],
        ):
            with self.assertRaises(ValueError):
                session.validate(
                    {
                        **self.data,
                        "text_draft": {**self.data["text_draft"], "edits": entries},
                    },
                    self.m,
                )
        self.preserved()

    def test_corrupt_and_foreign_existing_data_is_reported_read_only_not_repaired(self):
        path = self.w / session.FILE
        for raw in (
            b"{BROKEN",
            json.dumps({**self.data, "workspace_id": "foreign"}).encode(),
        ):
            path.write_bytes(raw)
            result = session.read_snapshot(self.w, self.m)
            self.assertIsNone(result["snapshot"])
            self.assertTrue(result["issue"])
            self.assertEqual(result["revision"], r.digest(raw))
            self.assertEqual(path.read_bytes(), raw)
        self.preserved()

    def test_utf8_budget_and_atomic_failure_never_damage_previous_checkpoint(self):
        result = session.save_snapshot(self.w, self.m, self.data, '"absent"')
        raw = (self.w / session.FILE).read_bytes()
        data = deepcopy(self.data)
        data["memo"]["text"] = "CHANGED"
        with patch.object(
            session.os, "replace", side_effect=OSError("SYNTHETIC replace failure")
        ):
            with self.assertRaises(OSError):
                session.save_snapshot(
                    self.w, self.m, data, '"' + result["revision"] + '"'
                )
        self.assertEqual((self.w / session.FILE).read_bytes(), raw)
        with patch.object(session, "LIMIT", 16):
            with self.assertRaises(ValueError):
                session.read_snapshot(self.w, self.m)
        self.preserved()

    def test_active_apply_lease_is_preserved_by_recovery_reads_and_writes(self):
        lock = self.w / r.APPLY_LOCK
        lock.write_bytes(b"SYNTHETIC UNKNOWN OWNERSHIP")
        session.save_snapshot(self.w, self.m, self.data, '"absent"')
        session.read_snapshot(self.w, self.m)
        self.assertEqual(lock.read_bytes(), b"SYNTHETIC UNKNOWN OWNERSHIP")
        lock.unlink()
        self.preserved()

    def test_history_deletion_only_one_exact_confirmed_entry_preserves_current_and_source(self):
        saved=session.save_snapshot(self.w,self.m,self.data,'"absent"');old=(self.w/session.FILE).read_bytes()
        cleared=session.clear_snapshot(self.w,'"'+saved['revision']+'"');current=(self.w/session.FILE).read_bytes()
        entries=session.list_history(self.w);self.assertEqual(len(entries),1);sha=r.digest(old)
        with self.assertRaises(ValueError):session.delete_history(self.w,{'sha256':sha,'delete_confirmed':1},'"'+cleared['revision']+'"')
        with self.assertRaises(ValueError):session.delete_history(self.w,{'sha256':'../source','delete_confirmed':True},'"'+cleared['revision']+'"')
        session.delete_history(self.w,{'sha256':sha,'delete_confirmed':True},'"'+cleared['revision']+'"')
        self.assertEqual(session.list_history(self.w),[]);self.assertEqual((self.w/session.FILE).read_bytes(),current);self.preserved()

    def test_changed_history_never_deleted_or_repaired(self):
        saved=session.save_snapshot(self.w,self.m,self.data,'"absent"');old=(self.w/session.FILE).read_bytes()
        cleared=session.clear_snapshot(self.w,'"'+saved['revision']+'"');sha=r.digest(old)
        target=self.w/'session-history'/(sha+'.json');target.write_bytes(b'CHANGED')
        with self.assertRaises(ValueError):session.delete_history(self.w,{'sha256':sha,'delete_confirmed':True},'"'+cleared['revision']+'"')
        self.assertEqual(target.read_bytes(),b'CHANGED');self.preserved()

    def test_actual_http_token_origin_revision_and_no_destructive_clear(self):
        server = r.create_review_server(self.w)
        thread = threading.Thread(target=server.serve_forever)
        thread.start()
        origin = f"http://127.0.0.1:{server.server_port}"

        def request(method, path, body=None, headers=None):
            conn = http.client.HTTPConnection(
                "127.0.0.1", server.server_port, timeout=5
            )
            try:
                conn.request(
                    method,
                    path,
                    json.dumps(body) if body is not None else None,
                    headers or {},
                )
                response = conn.getresponse()
                return response.status, response.read()
            finally:
                conn.close()

        try:
            token_match = re.search(
                r"const token='([a-f0-9]+)'", request("GET", "/")[1].decode()
            )
            if token_match is None:
                self.fail("Synthetic UI token missing")
            headers = {
                "Origin": origin,
                "X-Review-Token": token_match.group(1),
                "Content-Type": "application/json",
                "If-Match": '"absent"',
            }
            self.assertEqual(
                request(
                    "POST",
                    "/api/session",
                    self.data,
                    {**headers, "Origin": "http://evil.invalid"},
                )[0],
                403,
            )
            missing = dict(headers)
            del missing["If-Match"]
            self.assertEqual(
                request("POST", "/api/session", self.data, missing)[0], 428
            )
            self.assertEqual(
                request(
                    "POST",
                    "/api/session",
                    self.data,
                    {**headers, "If-Match": '"wrong"'},
                )[0],
                409,
            )
            code, raw = request("POST", "/api/session", self.data, headers)
            self.assertEqual(code, 200)
            saved = json.loads(raw)
            self.assertEqual(
                json.loads(request("GET", "/api/session")[1])["snapshot"], self.data
            )
            headers["If-Match"] = '"' + saved["revision"] + '"'
            self.assertEqual(
                request("POST", "/api/session-clear", {"clear_confirmed": 1}, headers)[
                    0
                ],
                400,
            )
            self.assertEqual(
                request(
                    "POST", "/api/session-clear", {"clear_confirmed": True}, headers
                )[0],
                200,
            )
            self.assertIsNone(json.loads(request("GET", "/api/session")[1])["snapshot"])
        finally:
            server.shutdown()
            thread.join(5)
            server.server_close()
        self.preserved()


if __name__ == "__main__":
    unittest.main()
