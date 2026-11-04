"""HTTP integration tests use an artificial fixture, never the real report workspace."""

import http.client
import importlib.util
import json
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tempfile
import unittest
import zipfile

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("review", ROOT / "scripts/hwp_review.py")
if spec is None or spec.loader is None:
    raise RuntimeError("Synthetic review module loader missing")
r = importlib.util.module_from_spec(spec)
spec.loader.exec_module(r)


def ui_token(body):
    match = re.search(r"const token='([a-f0-9]+)'", body.decode())
    if match is None:
        raise AssertionError("Synthetic review token missing")
    return match.group(1)


class HttpTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp = tempfile.TemporaryDirectory()
        cls.path = Path(cls.temp.name)
        # Copy the server/UI into the fixture so hot-reload tests never alter project files.
        runtime = cls.path / "runtime"
        (runtime / "scripts").mkdir(parents=True)
        (runtime / "web").mkdir()
        (runtime / "LICENSE").write_bytes((ROOT / "LICENSE").read_bytes())
        cls.server_script = runtime / "scripts/hwp_review.py"
        cls.server_script.write_bytes((ROOT / "scripts/hwp_review.py").read_bytes())
        for module in (
            "hwp_resources.py",
            "hwp_native_plan.py",
            "hwp_fixed_layout.py",
            "hwp_fill_plan.py",
            "hwp_form_draft.py",
            "hwp_text_proposal.py",
            "hwp_text_apply.py",
            "hwp_security.py",
            "hwp_process_identity.py",
            "hwp_native_owner.py",
            "hwp_contract_control.py",
            "hwp_contract_apply.py",
            "hwp_native_buffer.py",
            "hwp_native_cells.py",
            "hwp_agents.py",
            "hwp_pi_probe.py",
            "hwp_pi_chat.py",
            "hwp_pi_context.py",
        ):
            (runtime / "scripts" / module).write_bytes(
                (ROOT / "scripts" / module).read_bytes()
            )
        cls.template_path = runtime / "web/review.html"
        cls.template_path.write_bytes((ROOT / "web/review.html").read_bytes())
        for name in ["fixture.hwp", "baseline.pdf"]:
            (cls.path / name).write_bytes(b"artificial-server-fixture-only")
        # A real synthetic HWPX container is needed for the read-only cache preflight.
        with zipfile.ZipFile(cls.path / "baseline.hwpx", "w") as z:
            z.writestr("mimetype", "application/hwp+zip")
            z.writestr(
                "Contents/section0.xml",
                """<hs:sec
                xmlns:hs="http://www.hancom.co.kr/hwpml/2011/section"
                xmlns:hp="http://www.hancom.co.kr/hwpml/2011/paragraph">
                <hp:p id="0"><hp:run charPrIDRef="1"><hp:t>Original fixture phrase</hp:t></hp:run>
                <hp:linesegarray><hp:lineseg textpos="0"/><hp:lineseg textpos="16"/></hp:linesegarray>
                </hp:p></hs:sec>""",
            )
        pdf = r.fitz.open()
        pdf_page = pdf.new_page(width=300, height=400)
        pdf_page.insert_text((20, 40), "Synthetic unique report text", fontsize=12)
        (cls.path / "baseline.pdf").unlink()
        pdf.save(cls.path / "baseline.pdf")
        (cls.path / "pages").mkdir()
        pdf_page.get_pixmap(dpi=100).save(cls.path / "pages/page-001.png")
        pdf.close()
        cls.anchor = r.index_hwpx(cls.path / "baseline.hwpx")[0][0]
        cls.anchor.pop("raw")  # Lexical bytes are transient, not manifest JSON.
        cls.anchor.update(page_hint=1, boxes=[[10, 10, 100, 30]])
        cls.manifest = {
            "workspace_id": "http-test-only",
            "source": str(cls.path / "fixture.hwp"),
            "source_audit": {"sha256": r.file_hash(cls.path / "fixture.hwp")},
            "baseline_hwpx_sha256": r.file_hash(cls.path / "baseline.hwpx"),
            "baseline_pdf_sha256": r.file_hash(cls.path / "baseline.pdf"),
            "roundtrip_passed": True,
            "anchors": [cls.anchor],
            "protected_pages": [],
            "objects": {"tbl": 0, "pic": 0},
            "pages": [{"page": 1, "width": 300, "height": 400}],
        }
        r.write_json(cls.path / "manifest.json", cls.manifest)
        r.write_json(
            cls.path / "comments.json",
            {"workspace_id": "http-test-only", "comments": []},
        )
        cls.process = subprocess.Popen(
            [
                sys.executable,
                str(cls.server_script),
                "serve",
                str(cls.path),
                "--port",
                "0",
            ],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            encoding="utf-8",
        )
        if cls.process.stdout is None:
            raise RuntimeError("Synthetic server output pipe missing")
        line = cls.process.stdout.readline()
        if not line.startswith("Local review: http://127.0.0.1:"):
            cls.process.terminate()
            raise RuntimeError("Test server failed: " + line)
        cls.port = int(line.strip().rsplit(":", 1)[-1])
        cls.origin = f"http://127.0.0.1:{cls.port}"
        code, body = cls.request("GET", "/")
        if code != 200:
            raise RuntimeError("Test UI failed")
        cls.token = ui_token(body)

    @classmethod
    def tearDownClass(cls):
        cls.process.terminate()
        cls.process.communicate(timeout=10)
        cls.temp.cleanup()

    @classmethod
    def request(cls, method, path, payload=None, headers=None):
        conn = http.client.HTTPConnection("127.0.0.1", cls.port, timeout=10)
        body = json.dumps(payload).encode() if payload is not None else None
        conn.request(method, path, body=body, headers=headers or {})
        response = conn.getresponse()
        data = response.read()
        conn.close()
        return response.status, data

    def post(self, route, body, **overrides):
        headers = {
            "Origin": self.origin,
            "X-Review-Token": self.token,
            "Content-Type": "application/json",
        }
        if route == "/api/text-proposal":
            headers["If-Match"] = (
                '"' + r.file_revision(self.path / "proposal.json") + '"'
            )
        if route == "/api/comments":
            headers["If-Match"] = '"' + r.file_hash(self.path / "comments.json") + '"'
        headers.update(overrides)
        return self.request("POST", route, body, headers)

    def test_failed_attempt_without_candidate_pdf_is_visible_without_approval_write(
        self,
    ):
        run = self.path / "runs/20261009T144602-cafe1234"
        run.mkdir(parents=True)
        self.addCleanup(shutil.rmtree, run)
        path = self.path / "proposal.json"
        previous = path.read_bytes() if path.exists() else None
        if previous is None:
            self.addCleanup(path.unlink, missing_ok=True)
        else:
            self.addCleanup(path.write_bytes, previous)
        plan = {
            "schema": 1,
            "workspace_id": self.manifest["workspace_id"],
            "baseline_hwpx_sha256": self.manifest["baseline_hwpx_sha256"],
            "edits": [
                {
                    "anchor_id": self.anchor["anchor_id"],
                    "old_text": self.anchor["old_text"],
                    "new_text": self.anchor["old_text"] + " updated",
                }
            ],
        }
        r.write_json(path, plan)
        r.write_json(run / "plan.json", plan)
        r.write_json(
            run / "verification.json",
            {
                "status": "blocked",
                "source_unchanged": True,
                "error": "Fixture table height changed",
            },
        )
        approval = self.path / "approved-plan.json"
        before = approval.read_bytes() if approval.exists() else None
        code, body = self.request("GET", "/api/state")
        state = json.loads(body)
        self.assertEqual(code, 200)
        self.assertEqual(state["latest_attempt"]["id"], run.name)
        self.assertEqual(state["latest_attempt"]["status"], "blocked")
        code, body = self.request("GET", "/api/versions")
        versions = json.loads(body)
        self.assertEqual(code, 200)
        self.assertTrue(any(x["id"] == run.name for x in versions["attempts"]))
        self.assertFalse(any(x["id"] == run.name for x in versions["versions"]))
        self.assertEqual(approval.read_bytes() if approval.exists() else None, before)

    def test_license_source_notice_is_fixed_host_checked_and_document_readonly(self):
        before = {p.name: r.file_hash(p) for p in self.path.iterdir() if p.is_file()}
        code, body = self.request("GET", "/legal")
        self.assertEqual(code, 200)
        text = body.decode("utf-8")
        self.assertNotIn(str(self.path), text)
        self.assertEqual(self.request("GET", "/legal?file=baseline.hwpx"), (code, body))
        self.assertEqual(
            self.request("GET", "/legal", headers={"Host": "example.invalid"})[0], 403
        )
        self.assertEqual(
            before, {p.name: r.file_hash(p) for p in self.path.iterdir() if p.is_file()}
        )

    def test_state_and_unknown_routes(self):
        code, body = self.request("GET", "/api/state")
        self.assertEqual(code, 200)
        self.assertEqual(json.loads(body)["manifest"]["workspace_id"], "http-test-only")
        self.assertEqual(self.request("GET", "/../manifest.json")[0], 404)

    def test_template_changes_are_visible_without_restart(self):
        original = self.template_path.read_text(encoding="utf-8")
        marker = "HTTP hot-reload fixture"
        try:
            self.template_path.write_text(
                original.replace("<title>", "<title>" + marker), encoding="utf-8"
            )
            code, body = self.request("GET", "/")
            self.assertEqual(code, 200)
            self.assertIn(marker, body.decode())
            self.assertEqual(ui_token(body), self.token)
        finally:
            self.template_path.write_text(original, encoding="utf-8")

    def test_wrong_host_and_cross_origin_are_rejected(self):
        self.assertEqual(
            self.request("GET", "/", headers={"Host": "evil.invalid"})[0], 403
        )
        payload = {"workspace_id": "http-test-only", "comments": []}
        self.assertEqual(
            self.post("/api/comments", payload, Origin="https://evil.invalid")[0], 403
        )
        self.assertEqual(
            self.post("/api/comments", payload, **{"X-Review-Token": "wrong"})[0], 403
        )

    def test_comment_save_and_no_approval(self):
        comments = {
            "workspace_id": "http-test-only",
            "comments": [
                {
                    "id": "fixture-comment",
                    "page": 1,
                    "anchor_id": self.anchor["anchor_id"],
                    "region": [0.1, 0.2],
                    "comment": "Synthetic test only.",
                }
            ],
        }
        self.assertEqual(self.post("/api/comments", comments)[0], 200)
        self.assertEqual(r.read_json(self.path / "comments.json"), comments)
        comments["comments"][0]["approved"] = True
        self.assertEqual(self.post("/api/comments", comments)[0], 400)

    def test_applied_version_comparison_is_read_only_and_bound_to_baseline(self):
        # Synthetic run ONLY; never creates artifacts under the real work/review.
        run_id = "20261008T120000-01234567"
        run = self.path / "runs" / run_id
        run.mkdir(parents=True)
        plan = {
            "schema": 1,
            "workspace_id": self.manifest["workspace_id"],
            "baseline_hwpx_sha256": self.manifest["baseline_hwpx_sha256"],
            "edits": [],
        }
        r.write_json(run / "plan.json", plan)
        r.write_json(
            run / "verification.json",
            {
                "status": "checks_passed_needs_human_visual_review",
                "layout_gate": {"any_pixel_changed_pages": [1]},
            },
        )
        pdf = r.fitz.open()
        page = pdf.new_page(width=300, height=400)
        page.insert_text((20, 40), "Edited synthetic report text", fontsize=12)
        pdf.save(run / "candidate.pdf")
        pdf.close()
        before = r.file_hash(self.path / "baseline.pdf")
        candidate = r.file_hash(run / "candidate.pdf")
        code, data = self.request("GET", "/api/versions")
        self.assertEqual(code, 200)
        item = next(v for v in json.loads(data)["versions"] if v["id"] == run_id)
        self.assertEqual(item["changed_pages"], [1])
        for side in ["before", "after", "diff"]:
            code, data = self.request(
                "GET", f"/comparison/{run_id}/{side}/1.png?dpi=100"
            )
            self.assertEqual(code, 200)
            self.assertTrue(data.startswith(bytes([137]) + b"PNG"))
            if side == "diff":
                samples = r.fitz.Pixmap(data).samples
                self.assertTrue(
                    any(
                        samples[i : i + 3] == bytes((239, 68, 86))
                        for i in range(0, len(samples), 3)
                    )
                )
        self.assertEqual(r.file_hash(self.path / "baseline.pdf"), before)
        self.assertEqual(r.file_hash(run / "candidate.pdf"), candidate)
        with self.assertRaises(ValueError):
            r.review_version(self.path, self.manifest, "../../outside")
        plan["baseline_hwpx_sha256"] = "another-baseline"
        r.write_json(run / "plan.json", plan)
        self.assertEqual(
            self.request("GET", f"/comparison/{run_id}/after/1.png")[0], 400
        )
        r.write_json(
            run / "plan.json",
            {**plan, "baseline_hwpx_sha256": self.manifest["baseline_hwpx_sha256"]},
        )
        r.write_json(
            run / "verification.json",
            {"status": "blocked", "layout_gate": {"any_pixel_changed_pages": [1]}},
        )
        _, data = self.request("GET", "/api/versions")
        self.assertEqual(
            next(v for v in json.loads(data)["versions"] if v["id"] == run_id)[
                "status"
            ],
            "blocked",
        )

    def test_multiple_notes_on_one_page_have_distinct_ids_and_locations(self):
        body = {
            "workspace_id": "http-test-only",
            "comments": [
                {
                    "id": "note-one",
                    "page": 1,
                    "anchor_id": None,
                    "region": [0.2, 0.2],
                    "comment": "First fixture note",
                },
                {
                    "id": "note-two",
                    "page": 1,
                    "anchor_id": None,
                    "region": [0.6, 0.7],
                    "comment": "Second fixture note",
                },
            ],
        }
        self.assertEqual(self.post("/api/comments", body)[0], 200)
        self.assertEqual(len(r.read_json(self.path / "comments.json")["comments"]), 2)
        body["comments"][1]["id"] = "note-one"
        self.assertEqual(self.post("/api/comments", body)[0], 400)

    def test_pdf_search_is_read_only_and_case_whitespace_insensitive(self):
        before = r.file_hash(self.path / "baseline.pdf")
        code, body = self.request("GET", "/api/search?q=UNIQUEreport")
        self.assertEqual(code, 200)
        data = json.loads(body)
        self.assertEqual([m["page"] for m in data["matches"]], [1])
        self.assertIn("unique report", data["matches"][0]["snippet"])
        self.assertFalse(data["ocr"])
        self.assertEqual(self.request("GET", "/api/search?q=missing")[0], 200)
        self.assertEqual(self.request("GET", "/api/search?q=" + "x" * 201)[0], 400)
        self.assertEqual(r.file_hash(self.path / "baseline.pdf"), before)

    def test_high_resolution_preview_is_bounded_and_read_only(self):
        before = r.file_hash(self.path / "baseline.pdf")
        code, body = self.request("GET", "/page/1.png?dpi=144")
        self.assertEqual(code, 200)
        self.assertTrue(body.startswith(bytes([137]) + b"PNG"))
        self.assertEqual(r.fitz.Pixmap(body).width, 600)
        self.assertEqual(self.request("GET", "/page/1.png?dpi=300")[0], 400)
        self.assertEqual(self.request("GET", "/page/2.png?dpi=144")[0], 400)
        self.assertEqual(r.file_hash(self.path / "baseline.pdf"), before)

    def test_comment_revision_prevents_stale_overwrite(self):
        _, raw = self.request("GET", "/api/state")
        revision = json.loads(raw)["comments_revision"]
        body = {"workspace_id": "http-test-only", "comments": []}
        self.assertEqual(self.post("/api/comments", body)[0], 200)
        body["comments"].append(
            {
                "id": "another-window",
                "page": 1,
                "anchor_id": None,
                "region": None,
                "comment": "Newer fixture update",
            }
        )
        self.assertEqual(self.post("/api/comments", body)[0], 200)
        latest = (self.path / "comments.json").read_bytes()
        stale = {"workspace_id": "http-test-only", "comments": []}
        self.assertEqual(
            self.post("/api/comments", stale, **{"If-Match": '"' + revision + '"'})[0],
            409,
        )
        self.assertEqual((self.path / "comments.json").read_bytes(), latest)
        headers = {
            "Origin": self.origin,
            "X-Review-Token": self.token,
            "Content-Type": "application/json",
        }
        self.assertEqual(self.request("POST", "/api/comments", stale, headers)[0], 428)

    def test_invalid_cached_offsets_disable_proposal_and_approval(self):
        plan = {
            "schema": 1,
            "workspace_id": "http-test-only",
            "baseline_hwpx_sha256": self.manifest["baseline_hwpx_sha256"],
            "edits": [
                {
                    "anchor_id": self.anchor["anchor_id"],
                    "old_text": self.anchor["old_text"],
                    "new_text": "short",
                }
            ],
        }
        r.write_json(self.path / "proposal.json", plan)
        approval = self.path / "approved-plan.json"
        before = approval.read_bytes() if approval.exists() else None
        code, body = self.request("GET", "/api/state")
        state = json.loads(body)
        self.assertEqual(code, 200)
        self.assertIsNone(state["proposal"])
        self.assertIn("cached textpos 16", state["proposal_error"])
        self.assertEqual(
            self.post("/api/approve", {"plan_sha256": r.plan_hash(plan)})[0], 400
        )
        self.assertEqual(approval.read_bytes() if approval.exists() else None, before)

    def test_native_scope_needs_new_explicit_ui_confirmation(self):
        legacy = {
            "schema": 1,
            "workspace_id": self.manifest["workspace_id"],
            "baseline_hwpx_sha256": self.manifest["baseline_hwpx_sha256"],
            "edits": [
                {
                    "anchor_id": self.anchor["anchor_id"],
                    "old_text": self.anchor["old_text"],
                    "new_text": "short",
                }
            ],
        }
        plan = r.hwp_native_plan.proposal(
            legacy, self.manifest, self.path / "baseline.hwpx"
        )
        r.write_json(self.path / "proposal-native.json", plan)
        approval = self.path / "approved-plan.json"
        before = approval.read_bytes() if approval.exists() else None
        code, data = self.request("GET", "/api/state?proposal=native")
        state = json.loads(data)
        self.assertEqual(code, 200)
        self.assertEqual(state["proposal"]["schema"], 2)
        self.assertEqual(state["proposal_kind"], "native")
        self.assertIsNone(state["approved_sha256"])
        self.assertEqual(approval.read_bytes() if approval.exists() else None, before)
        payload = {
            "plan_sha256": r.plan_hash(plan),
            "proposal_kind": "native",
            "native_policy_confirmed": True,
            "regions_reviewed": [self.anchor["anchor_id"]],
        }
        for change in (
            {"regions_reviewed": []},
            {"native_policy_confirmed": False},
            {"native_policy_confirmed": 1},
            {"plan_sha256": "stale"},
        ):
            self.assertEqual(self.post("/api/approve", {**payload, **change})[0], 400)
        self.assertEqual(approval.read_bytes() if approval.exists() else None, before)
        self.assertEqual(self.post("/api/approve", payload)[0], 200)
        saved = r.read_json(approval)
        self.assertEqual(saved["approved_scope"], r.native_approval_scope(plan))
        r.approved_plan(self.path, plan, self.manifest)
        if before is not None:
            self.assertEqual(
                (
                    self.path / "approval-history" / (r.digest(before) + ".json")
                ).read_bytes(),
                before,
            )
        changed = json.loads(json.dumps(plan))
        changed["edits"][0]["region"][2] += 1
        r.write_json(self.path / "proposal-native.json", changed)
        self.assertEqual(self.post("/api/approve", payload)[0], 400)
        r.write_json(self.path / "proposal-native.json", plan)
        self.assertEqual(
            self.request("GET", "/api/state?proposal=../approved-plan")[0], 400
        )

    def test_exact_proposal_approval_and_changed_proposal_rejection(self):
        # Synthetic approval fixture ONLY, not an approval for an actual HWP document.
        plan = {
            "schema": 1,
            "workspace_id": "http-test-only",
            "baseline_hwpx_sha256": self.manifest["baseline_hwpx_sha256"],
            "edits": [
                {
                    "anchor_id": self.anchor["anchor_id"],
                    "old_text": "Original fixture phrase",
                    "new_text": "Updated fixture phrase",
                }
            ],
        }
        r.write_json(self.path / "proposal.json", plan)
        code, body = self.request("GET", "/api/state")
        self.assertEqual(code, 200)
        visible_hash = json.loads(body)["proposal_sha256"]
        changed = json.loads(json.dumps(plan))
        changed["edits"][0]["new_text"] = "Different fixture phrase"
        r.write_json(self.path / "proposal.json", changed)
        self.assertEqual(
            self.post("/api/approve", {"plan_sha256": visible_hash})[0], 400
        )
        r.write_json(self.path / "proposal.json", plan)
        self.assertEqual(
            self.post("/api/approve", {"plan_sha256": visible_hash})[0], 200
        )
        r.approved_plan(self.path, plan, self.manifest)
        with self.assertRaises(ValueError):
            r.approved_plan(self.path, changed, self.manifest)


if __name__ == "__main__":
    unittest.main()
