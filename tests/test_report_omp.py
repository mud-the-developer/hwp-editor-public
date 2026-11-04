"""Imported native answers use the real existing HTTP consent/compiler boundary."""
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

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import hwp_report_project as projects
import hwp_review as review
import hwp_template_draft as draft
from hwp_synthetic_report import write_inputs


class ReportOmpTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.files = Path(temporary.name).resolve()
        profile, _ = write_inputs(self.files / "unsigned")
        self.source = Path(profile["jobs"][0]["source"])
        self.original = self.source.read_bytes()
        self.info = projects.new_document(self.source, base=self.files / "projects")
        self.server = review.create_report_document_server(self.info.root)
        self.control = self.server.report_control
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.addCleanup(self.close_server)
        self.origin = "http://127.0.0.1:" + str(self.server.server_port)
        code, body = self.request("GET", "/", token=False)
        self.assertEqual(code, 200)
        self.token = re.search(rb'name="review-token" content="([a-f0-9]{64})"', body)[1].decode()
        self.section = next(item for item in projects.sections(self.info.root) if "REPORT BODY BEFORE" in item["text"])
        self.input = {"instruction": "선택한 본문을 간결하게 수정해 주세요.", "selection_ids": [self.section["id"]], "materials": [], "previous_version": None}
        for target in ("subprocess.Popen", "hwp_report_writer.generate", "hwp_report_project.begin_request", "hwp_pi_chat.call_chat"):
            denied = patch(target, side_effect=AssertionError("No CLI/model/model-start for imported JSON"))
            denied.start()
            self.addCleanup(denied.stop)

    def close_server(self):
        if self.control.thread is not None:
            self.control.thread.join(10)
        self.server.shutdown()
        self.thread.join(5)
        self.server.server_close()

    def request(self, method, route, payload=None, *, token=True, origin=True, revision=None, raw=None):
        headers = {"X-Review-Token": self.token} if token else {}
        if method == "POST":
            headers["Content-Type"] = "application/json"
            if origin:
                headers["Origin"] = self.origin
            headers["If-Match"] = self.state()["revision"] if revision is None else revision
        connection = http.client.HTTPConnection("127.0.0.1", self.server.server_port, timeout=15)
        try:
            connection.request(method, route, raw if raw is not None else (None if payload is None else json.dumps(payload).encode()), headers)
            response = connection.getresponse()
            return response.status, response.read()
        finally:
            connection.close()

    def state(self):
        code, body = self.request("GET", "/api/report-state")
        self.assertEqual(code, 200, body)
        return json.loads(body)

    def context(self, payload=None):
        code, body = self.request("POST", "/api/report-omp-context", payload or self.input)
        self.assertEqual(code, 200, body)
        return json.loads(body)

    def proposal(self, context, text="SYNTHETIC IMPORTED REPORT BODY", *, proposal=None):
        request = context["shared_context"]["review_context"]
        data = proposal or {"schema": "hwp-report-proposal-v1", "request_id": context["request_id"], "context_sha256": context["context_sha256"],
                            "jobs": [{"job_id": job["job_id"], "paragraphs": [text]} for job in request["jobs"]], "summary": ["본문 문장을 수정했습니다."]}
        code, body = self.request("POST", "/api/report-omp-proposal", {"request_id": context["request_id"], "context_sha256": context["context_sha256"], "proposal_text": json.dumps(data)})
        self.assertEqual(code, 200, body)
        return json.loads(body)

    def create(self, proposal):
        consent = {"preview_sha256": proposal["preview_sha256"], "draft_only_confirmed": True}
        code, body = self.request("POST", "/api/report-omp-create", consent)
        self.assertEqual(code, 202, body)
        self.control.thread.join(10)
        self.assertFalse(self.control.thread.is_alive())
        return consent

    def test_context_and_proposal_are_pure_and_create_requires_ui_written_permission(self):
        context = self.context()
        self.assertEqual(set(context), {"request_id", "context_sha256", "preview_sha256", "shared_context", "prompt"})
        self.assertEqual(set(context["shared_context"]), {"review_context", "job_snapshot"})
        self.assertNotIn("get_review_context", context["prompt"])
        self.assertNotIn(str(self.info.root), context["prompt"])
        self.assertNotIn(self.token, context["prompt"])
        proposal = self.proposal(context)
        self.assertEqual(set(proposal), {"request_id", "preview_sha256", "proposal_sha256", "summary", "jobs"})
        self.assertFalse(list(self.info.root.rglob("draft-permission.json")))
        self.assertFalse(self.state()["versions"])
        consent = {"preview_sha256": proposal["preview_sha256"], "draft_only_confirmed": True}
        with self.assertRaises(ValueError):
            self.control.omp_start(consent)
        self.assertFalse(list(self.info.root.rglob("draft-permission.json")))
        for value in (False, 1):
            code, _ = self.request("POST", "/api/report-omp-create", {**consent, "draft_only_confirmed": value})
            self.assertEqual(code, 409)
        code, _ = self.request("POST", "/api/report-omp-create", consent, origin=False)
        self.assertEqual(code, 403)
        code, _ = self.request("POST", "/api/report-omp-create", consent, revision="stale")
        self.assertEqual(code, 409)
        self.assertFalse(list(self.info.root.rglob("draft-permission.json")))

    def test_native_context_preserves_literal_json_without_implicit_mentions(self):
        instruction = r'Keep @"C:/SYNTHETIC/private.txt" and ^SYNTHETIC/MODEL literal; keep \u0040, \u005e, quotes " and 한국어.'
        reference = r'Unselected paths are data: @../SYNTHETIC-SECRETS.txt ^SYNTHETIC/OTHER; backslash \ and @name.'
        material = self.control.upload(reference.encode("utf-8"), "literal-reference.txt")
        payload = {**self.input, "instruction": instruction,
                   "materials": [{"id": material["id"], "own_achievement": False}]}
        context = self.context(payload)
        wire = context["prompt"].split("\n\n", 1)[1]
        decoded = json.loads(wire)
        self.assertEqual(decoded["instruction"], instruction)
        self.assertEqual(decoded["materials"][0]["text"], reference)
        self.assertEqual(decoded, context["shared_context"]["review_context"])
        # Upstream recognizes these markers before before_agent_start; JSON
        # Unicode escapes preserve the captured text without attachment/model IO.
        self.assertNotIn("@", wire)
        self.assertNotIn("^", wire)
        self.assertFalse(list(self.info.root.rglob("draft-permission.json")))
        self.assertFalse(self.state()["versions"])

    def test_synthetic_real_compile_preserves_resources_scope_original_and_duplicate_bytes(self):
        proposal = self.proposal(self.context())
        consent = self.create(proposal)
        state = self.state()
        self.assertEqual(state["error"], "")
        self.assertTrue(state["gate_passed"], state["issues"])
        version, = state["versions"]
        self.assertFalse(version["accepted"])
        artifact = projects.candidate(self.info.root, version["id"])
        path = Path(artifact["path"])
        candidate = path.read_bytes()
        old, new = draft.parts_from(self.original), draft.parts_from(candidate)
        mutable_parts = {draft.PART, "Contents/header.xml", "Contents/content.hpf", "Preview/PrvText.txt"}
        for name in old:
            if name not in mutable_parts:
                self.assertEqual(old[name], new[name], name)
        old_header, new_header = review.xml_root(old["Contents/header.xml"]), review.xml_root(new["Contents/header.xml"])
        self.assertEqual(old_header.attrib, new_header.attrib)
        for child in old_header:
            if child.tag != draft.HH + "refList":
                self.assertEqual(draft.node_hash([child]), draft.node_hash([new_header.find(child.tag)]))

        def definitions(header):
            result = {}
            for category in header.find(draft.HH + "refList"):
                if category.tag == draft.HH + "fontfaces":
                    for face in category:
                        for font in face:
                            result[(category.tag, face.get("lang"), font.get("id"))] = draft.node_hash([font])
                else:
                    for resource in category:
                        result[(category.tag, resource.get("id"))] = draft.node_hash([resource])
            return result

        imported = definitions(new_header)
        for identifier, content in definitions(old_header).items():
            self.assertEqual(content, imported[identifier], identifier)
        old_body, new_body = review.xml_root(old[draft.PART]), review.xml_root(new[draft.PART])
        self.assertEqual(draft.node_hash(list(old_body)[:2]), draft.node_hash(list(new_body)[:2]))
        self.assertEqual(draft.node_hash(list(old_body)[3:]), draft.node_hash(list(new_body)[3:]))
        self.assertIn("SYNTHETIC IMPORTED REPORT BODY", new[draft.PART].decode())
        self.assertEqual(self.source.read_bytes(), self.original)
        self.assertFalse(list(self.info.root.rglob("model-start.json")))
        self.assertFalse(list(self.info.root.rglob("acceptance.json")))
        with patch.object(projects.harness, "generate_candidate", side_effect=AssertionError("Duplicate must reuse immutable result")):
            code, body = self.request("POST", "/api/report-omp-create", consent)
            self.assertEqual(code, 202, body)
            self.control.thread.join(10)
        self.assertEqual(self.state()["error"], "")
        self.assertEqual(path.read_bytes(), candidate)
        self.assertEqual(len(self.state()["versions"]), 1)

    def test_source_context_and_selected_evidence_drift_refuse_before_authority(self):
        uploaded = self.control.upload(b"SYNTHETIC EVIDENCE", "evidence.txt")
        payload = {**self.input, "materials": [{"id": uploaded["id"], "own_achievement": True}]}
        context = self.context(payload)
        proposal = self.proposal(context)
        material = Path(self.control.materials[uploaded["id"]]["path"])
        material.write_bytes(b"CHANGED EVIDENCE")
        code, _ = self.request("POST", "/api/report-omp-create", {"preview_sha256": proposal["preview_sha256"], "draft_only_confirmed": True})
        self.assertEqual(code, 409)
        self.assertFalse(list(self.info.root.rglob("draft-permission.json")))
        self.context()
        proposal = self.proposal(self.context())
        self.context({**self.input, "instruction": "새 요청입니다."})
        code, _ = self.request("POST", "/api/report-omp-create", {"preview_sha256": proposal["preview_sha256"], "draft_only_confirmed": True})
        self.assertEqual(code, 409)
        context = self.context()
        proposal = self.proposal(context)
        self.source.write_bytes(self.original + b"SOURCE DRIFT")
        code, _ = self.request("POST", "/api/report-omp-create", {"preview_sha256": proposal["preview_sha256"], "draft_only_confirmed": True})
        self.assertEqual(code, 409)
        self.assertFalse(list(self.info.root.rglob("draft-permission.json")))
        self.source.write_bytes(self.original)

    def test_untrusted_json_unknown_scope_duplicates_nan_and_protected_scope_reject(self):
        context = self.context()
        request = context["shared_context"]["review_context"]
        base = {"schema": "hwp-report-proposal-v1", "request_id": context["request_id"], "context_sha256": context["context_sha256"], "jobs": [{"job_id": request["jobs"][0]["job_id"], "paragraphs": ["CHANGED"]}], "summary": []}
        bad = []
        bad.append(json.dumps({**base, "approval": True}))
        bad.append(json.dumps({**base, "jobs": [{"job_id": "outside", "paragraphs": ["CHANGED"]}]}))
        bad.append(json.dumps({**base, "jobs": base["jobs"] * 2}))
        bad.append(json.dumps(base)[:-1] + ',"schema":"hwp-report-proposal-v1"}')
        bad.append(json.dumps({**base, "summary": [float("nan")]}))
        bad.append("x" * 262145)
        for text in bad:
            code, _ = self.request("POST", "/api/report-omp-proposal", {"request_id": context["request_id"], "context_sha256": context["context_sha256"], "proposal_text": text})
            self.assertEqual(code, 409)
        for row in projects.sections(self.info.root):
            if row["boundary"] or row["has_objects"]:
                code, _ = self.request("POST", "/api/report-omp-context", {**self.input, "selection_ids": [row["id"]]})
                self.assertEqual(code, 409)
        self.assertFalse(list(self.info.root.rglob("draft-permission.json")))

    def test_first_compiler_failure_is_preserved_and_never_retried(self):
        proposal = self.proposal(self.context())
        with patch.object(projects.harness, "generate_candidate", side_effect=ValueError("SYNTHETIC COMPILER FAILURE")) as compile_call:
            consent = self.create(proposal)
            self.assertEqual(compile_call.call_count, 1)
            code, _ = self.request("POST", "/api/report-omp-create", consent)
            self.assertEqual(code, 409)
            self.assertEqual(compile_call.call_count, 1)
        self.assertIn("SYNTHETIC COMPILER FAILURE", self.state()["error"])
        self.assertEqual(len(list(self.info.root.rglob("failed.json"))), 1)
        self.assertFalse(self.state()["versions"])
        self.assertEqual(self.source.read_bytes(), self.original)

    def test_generation_and_preservation_errors_remain_visible_after_worker_finishes(self):
        proposal = self.proposal(self.context())
        with (
            patch.object(projects, "generate", side_effect=ValueError("SYNTHETIC COMPILER FAILURE")),
            patch.object(projects, "fail_request", side_effect=PermissionError("SYNTHETIC RECORD DENIED")),
        ):
            self.create(proposal)
        state = self.state()
        self.assertIn("ValueError: SYNTHETIC COMPILER FAILURE", state["error"])
        self.assertIn("SYNTHETIC RECORD DENIED", state["error"])
        self.assertFalse(state["active"])
        self.assertIsNone(state["progress"])
        self.assertFalse(state["versions"])
        self.assertEqual(self.source.read_bytes(), self.original)

    def test_in_progress_import_does_not_launch_a_second_compiler(self):
        proposal = self.proposal(self.context())
        consent = {"preview_sha256": proposal["preview_sha256"], "draft_only_confirmed": True}
        entered, release = threading.Event(), threading.Event()
        generate = projects.harness.generate_candidate

        def held(*args, **kwargs):
            entered.set()
            if not release.wait(10):
                raise ValueError("Synthetic test release was not supplied")
            return generate(*args, **kwargs)

        with patch.object(projects.harness, "generate_candidate", side_effect=held) as compile_call:
            try:
                code, body = self.request("POST", "/api/report-omp-create", consent)
                self.assertEqual(code, 202, body)
                self.assertTrue(entered.wait(5))
                code, _ = self.request("POST", "/api/report-omp-create", consent)
                self.assertEqual(code, 409)
                self.assertEqual(compile_call.call_count, 1)
            finally:
                release.set()
                if self.control.thread is not None:
                    self.control.thread.join(10)
        self.assertEqual(self.state()["error"], "")
        self.assertEqual(len(self.state()["versions"]), 1)

    def test_mixed_character_format_is_not_silently_normalized(self):
        parts = draft.parts_from(self.original)
        body = review.xml_root(parts[draft.PART])
        extra = deepcopy(body[2].find(draft.HP + "run"))
        extra.set("charPrIDRef", "999")
        body[2].append(extra)
        parts[draft.PART] = draft._xml(body)
        mixed = self.files / "mixed.hwpx"
        mixed.write_bytes(draft._zip(self.original, parts, []))
        info = projects.new_document(mixed, base=self.files / "mixed-projects")
        from hwp_report_control import ReportControl
        control = ReportControl(info.root)
        self.addCleanup(control.close)
        selected = next(item for item in projects.sections(info.root) if "REPORT BODY BEFORE" in item["text"])
        with self.assertRaises(ValueError):
            control.omp_context({**self.input, "selection_ids": [selected["id"]]})
        self.assertFalse(list(info.root.rglob("draft-permission.json")))


if __name__ == "__main__":
    unittest.main()
