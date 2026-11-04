"""Fresh unsigned document HTTP workflow; provider and final OS open only are mocked."""

import hashlib
import http.client
from io import BytesIO
import json
from pathlib import Path
import re
import sys
import tempfile
import threading
import unittest
from unittest.mock import patch
from urllib.parse import quote
import xml.etree.ElementTree as ET
import zipfile

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import hwp_pi_chat
import hwp_report_project as projects
import hwp_review as review
from hwp_synthetic_report import write_inputs


def digest(value):
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


class DocumentHttpTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.files = Path(self.temporary.name).resolve()
        profile, _ = write_inputs(self.files / "unsigned")
        self.source = Path(profile["jobs"][0]["source"])
        self.original = self.source.read_bytes()
        self.info = projects.new_document(self.source, base=self.files / "projects")
        self.server = review.create_report_document_server(self.info.root)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.addCleanup(self.close_server)
        self.origin = "http://127.0.0.1:" + str(self.server.server_port)
        status, body = self.request("GET", "/", token=False)
        self.assertEqual(status, 200)
        self.token = re.search(rb'name="review-token" content="([a-f0-9]{64})"', body)[1].decode()
        self.control = self.server.report_control
        runtime = {
            "cli": "SYNTHETIC-NOT-EXECUTED/profile/bin/pi.cmd",
            "node": "SYNTHETIC-NOT-EXECUTED/node.exe",
            "sdk_entry": "SYNTHETIC-NOT-EXECUTED/sdk/index.js",
            "sdk_version": "1.2.0", "rules_sha256": "1" * 64,
        }
        runtime["sha256"] = digest(runtime)
        self.control.chat.runtime = runtime
        self.control.chat.model = {"provider": "SYNTHETIC", "id": "MOCKED-ONLY"}
        self.control.chat.status = "ready"
        self.provider = patch.object(hwp_pi_chat, "call_chat", side_effect=self.model_response)
        self.provider_mock = self.provider.start()
        self.addCleanup(self.provider.stop)
        process_deny = patch("subprocess.Popen", side_effect=AssertionError("No actual process/model/Native"))
        process_deny.start()
        self.addCleanup(process_deny.stop)
        self.output_text = "SYNTHETIC REPORT BODY REVISED"
        self.section = next(row for row in projects.sections(self.info.root) if "REPORT BODY BEFORE" in row["text"])

    def close_server(self):
        if self.control.thread is not None:
            self.control.thread.join(10)
        self.server.shutdown()
        self.thread.join(5)
        self.server.server_close()

    def model_response(self, runtime, action, cancel, **kwargs):
        self.assertEqual(action, "chat")
        request = kwargs["context"]["review_context"]
        proposal = {
            "schema": "hwp-report-proposal-v1", "request_id": request["request_id"],
            "context_sha256": request["context_sha256"],
            "jobs": [{"job_id": job["job_id"], "paragraphs": self.output_text if isinstance(self.output_text, list) else [self.output_text]} for job in request["jobs"]],
            "summary": ["SYNTHETIC 문장을 수정했습니다."],
        }
        return {"proposal": proposal, "model": kwargs["model"], "outcome": "settled",
                "text": json.dumps(proposal), "tool_activity": [], "artifacts": [],
                "document_shared": True, "usage": None}

    def request(self, method, path, payload=None, *, token=True, origin=True, revision=None,
                extra_headers=None, return_headers=False):
        connection = http.client.HTTPConnection("127.0.0.1", self.server.server_port, timeout=15)
        headers = {}
        if token:
            headers["X-Review-Token"] = self.token
        if method == "POST":
            if origin:
                headers["Origin"] = self.origin
            headers["Content-Type"] = "application/json"
            if revision is None and token:
                revision = self.state()["revision"]
            headers["If-Match"] = revision or ""
        raw = payload if isinstance(payload, bytes) else None if payload is None else json.dumps(payload).encode()
        headers.update(extra_headers or {})
        try:
            connection.request(method, path, raw, headers)
            response = connection.getresponse()
            body = response.read()
            if return_headers:
                return response.status, body, dict(response.getheaders())
            return response.status, body
        finally:
            connection.close()

    def state(self):
        code, body = self.request("GET", "/api/report-state")
        self.assertEqual(code, 200, body)
        return json.loads(body)

    def preview(self, instruction="선택한 문장을 간결하게 다듬어 주세요.", previous=None):
        code, body = self.request("POST", "/api/report-preview", {
            "instruction": instruction, "selection_ids": [self.section["id"]],
            "materials": [], "previous_version": previous,
        })
        self.assertEqual(code, 200, body)
        return json.loads(body)

    def start(self, preview):
        consent = {"preview_sha256": preview["preview_sha256"], "context_confirmed": True,
                   "cost_confirmed": True, "draft_only_confirmed": True}
        code, body = self.request("POST", "/api/report-start", consent)
        self.assertEqual(code, 202, body)
        self.control.thread.join(10)
        self.assertFalse(self.control.thread.is_alive(), "Owned synthetic task did not settle")
        return consent

    def upload_material(self, raw, name, **headers):
        return self.request("POST", "/api/report-material", raw, extra_headers={
            "Content-Type": "application/octet-stream", "X-Report-Name": quote(name), **headers,
        })

    def material_context(self, item, instruction="선택한 자료를 참고하여 본문을 다듬어 주세요."):
        return self.request("POST", "/api/report-omp-context", {
            "instruction": instruction, "selection_ids": [self.section["id"]],
            "materials": [{"id": item["id"], "own_achievement": False}], "previous_version": None,
        })

    def material_hwpx(self, *, oversized_text=False, incomplete_spine=False, invalid_control=False, entity=False,
                      header_spine=False, ordered_text=False):
        import hwp_template_draft as draft

        parts = review.archive(BytesIO(self.original))
        original_section = review.xml_root(parts[draft.PART])
        section_texts = {0: "SYNTHETIC FIRST @inert.py", 1: "SYNTHETIC SECOND ^inert", 2: "SYNTHETIC THIRD"}
        for index, text in section_texts.items():
            section = ET.Element(original_section.tag)
            paragraph = ET.SubElement(section, draft.HP + "p")
            run = ET.SubElement(paragraph, draft.HP + "run")
            ET.SubElement(run, draft.HP + "t").text = (
                "x" * 65537 if oversized_text else "\u0085" + text if invalid_control else text
            )
            if index == 0 and not oversized_text:
                table = ET.SubElement(run, draft.HP + "tbl")
                cell = ET.SubElement(table, draft.HP + "tc")
                nested = ET.SubElement(cell, draft.HP + "p")
                nested_run = ET.SubElement(nested, draft.HP + "run")
                ET.SubElement(nested_run, draft.HP + "t").text = "SYNTHETIC NESTED CELL"
                if ordered_text:
                    after = ET.SubElement(run, draft.HP + "t")
                    after.text = "SYNTHETIC AFTER CELL"
                    ET.SubElement(after, draft.HP + "tab").tail = "TABFIELD"
                    ET.SubElement(after, draft.HP + "lineBreak").tail = "NEXTLINE"
            parts[f"Contents/section{index}.xml"] = ET.tostring(section, encoding="utf-8")
            if entity:
                parts[f"Contents/section{index}.xml"] = (
                    b'<!DOCTYPE sec [<!ENTITY forbidden "SYNTHETIC">]>'
                    + parts[f"Contents/section{index}.xml"]
                )
        package = review.xml_root(parts["Contents/content.hpf"])
        manifest = package.find(draft.OPF + "manifest")
        for index in (1, 2):
            ET.SubElement(manifest, draft.OPF + "item", {
                "id": f"section{index}", "href": f"section{index}.xml", "media-type": "application/xml",
            })
        spine = package.find(draft.OPF + "spine")
        spine.clear()
        if header_spine:
            header = next(item for item in manifest if item.get("href") in ("header.xml", "Contents/header.xml"))
            ET.SubElement(spine, draft.OPF + "itemref", {"idref": header.get("id")})
        for index in ((2, 0) if incomplete_spine else (2, 0, 1)):
            ET.SubElement(spine, draft.OPF + "itemref", {"idref": f"section{index}"})
        parts["Contents/content.hpf"] = ET.tostring(package, encoding="utf-8")
        # An uncompressed, inert asset crosses the text-upload bound without
        # becoming body text or requiring any image/parser/Native execution.
        parts["BinData/synthetic-unused.bin"] = b"\x00" * (512 * 1024)
        output = BytesIO()
        with zipfile.ZipFile(output, "w", compression=zipfile.ZIP_STORED) as archive:
            for name, raw in parts.items():
                archive.writestr(name, raw)
        return output.getvalue()

    def test_selected_utf8_code_reaches_exact_context_by_display_name_without_expanding_mentions(self):
        hidden = self.files / "not-selected-secret.txt"
        hidden.write_text("SYNTHETIC NEVER SHARED", encoding="utf-8")
        text = f"# 선택한 코드\nprint('@{hidden}')\n# ^literal-model\n"
        raw = b"\xef\xbb\xbf" + text.replace("\n", "\r\n").encode("utf-8")
        code, body = self.upload_material(raw, "선택한 코드.py")
        self.assertEqual(code, 201, body)
        item = json.loads(body)
        self.assertEqual(item["name"], "선택한 코드.py")
        self.assertEqual(item["source_kind"], "utf8_text")
        self.assertEqual(item["source_sha256"], hashlib.sha256(raw).hexdigest())
        self.assertEqual(item["sha256"], hashlib.sha256(text.encode()).hexdigest())
        snapshot, = (self.info.root / "inbox").iterdir()
        self.assertEqual(snapshot.read_bytes(), text.encode())
        self.assertNotEqual(snapshot.name, item["name"])
        instruction = "@선택한 코드.py 안의 @표현과 ^표현은 자료로만 읽어 주세요."
        code, body = self.material_context(item, instruction)
        self.assertEqual(code, 200, body)
        context = json.loads(body)
        request = context["shared_context"]["review_context"]
        self.assertEqual(request["instruction"], instruction)
        reference, = request["materials"]
        self.assertEqual(reference["name"], item["name"])
        self.assertEqual(reference["text"], text)
        self.assertEqual(reference["sha256"], item["sha256"])
        self.assertNotIn("SYNTHETIC NEVER SHARED", body.decode())
        self.assertNotIn("@", context["prompt"])
        self.assertNotIn("^", context["prompt"])
        self.assertEqual(json.loads(context["prompt"].rsplit("\n\n", 1)[1]), request)
        self.assertFalse(self.control.omp_pending["core"]["binding"]["materials"][0]["own_achievement"])
        self.provider_mock.assert_not_called()
        self.assertFalse(list(self.info.root.rglob("draft-permission.json")))
        snapshot.write_bytes(snapshot.read_bytes() + b" drift")
        code, _ = self.material_context(item)
        self.assertEqual(code, 409)
        self.provider_mock.assert_not_called()

    def test_hwpx_material_captures_all_section_text_in_spine_order_not_zip_or_filename_order(self):
        raw = self.material_hwpx()
        self.assertGreater(len(raw), 512 * 1024)
        code, body = self.upload_material(raw, "선택한 자료.hwpx")
        self.assertEqual(code, 201, body)
        item = json.loads(body)
        self.assertEqual(item["source_kind"], "hwpx_text")
        self.assertEqual(item["source_sha256"], hashlib.sha256(raw).hexdigest())
        snapshot, = (self.info.root / "inbox").iterdir()
        text = snapshot.read_text(encoding="utf-8")
        self.assertTrue(text.endswith("\n\nSYNTHETIC THIRD\nSYNTHETIC FIRST @inert.py\n"
                                      "SYNTHETIC NESTED CELL\nSYNTHETIC SECOND ^inert"))
        self.assertNotIn("SYNTHETIC ONLY", text)  # Preview/PrvText.txt is not body text.
        self.assertEqual(item["sha256"], hashlib.sha256(snapshot.read_bytes()).hexdigest())
        code, body = self.material_context(item)
        self.assertEqual(code, 200, body)
        request = json.loads(body)["shared_context"]["review_context"]
        reference, = request["materials"]
        self.assertEqual(reference, {"id": "m-" + item["sha256"][:24], "name": item["name"],
                                     "text": text, "sha256": item["sha256"]})
        self.assertEqual(self.source.read_bytes(), self.original)
        self.provider_mock.assert_not_called()
        self.assertFalse(list(self.info.root.rglob("draft-permission.json")))

    def test_hwpx_reference_accepts_declared_header_before_body_spine(self):
        code, body = self.upload_material(self.material_hwpx(header_spine=True), "header-spine.hwpx")
        self.assertEqual(code, 201, body)
        code, body = self.material_context(json.loads(body))
        self.assertEqual(code, 200, body)
        self.assertIn("SYNTHETIC FIRST @inert.py", json.loads(body)["shared_context"]["review_context"]["materials"][0]["text"])
        self.provider_mock.assert_not_called()

    def test_hwpx_reference_preserves_nested_source_order_and_inline_separators(self):
        code, body = self.upload_material(self.material_hwpx(ordered_text=True), "ordered-text.hwpx")
        self.assertEqual(code, 201, body)
        code, body = self.material_context(json.loads(body))
        self.assertEqual(code, 200, body)
        text = json.loads(body)["shared_context"]["review_context"]["materials"][0]["text"]
        self.assertTrue(text.endswith("\n\nSYNTHETIC THIRD\nSYNTHETIC FIRST @inert.py\n"
                                      "SYNTHETIC NESTED CELL\nSYNTHETIC AFTER CELL\tTABFIELD\nNEXTLINE\nSYNTHETIC SECOND ^inert"))
        self.provider_mock.assert_not_called()

    def test_material_refusals_leave_no_inbox_snapshot_or_authority(self):
        before = {path.relative_to(self.info.root): path.read_bytes()
                  for path in self.info.root.rglob("*") if path.is_file()}
        cases = [
            ("unknown.bin", b"unknown ASCII binary format"),
            ("image.png", b"\x89PNG\r\n\x1a\n"),
            ("invalid.py", b"\xff\xfeUTF16"),
            ("control.txt", b"before\x01after"),
            ("control.py", "before\u0085after".encode()),
            ("control.json", "before\u2028after".encode()),
            ("empty.txt", b" \r\n\t"),
            ("empty.py", b""),
            ("staged.txt", b"x" * 65537),
            ("multibyte.py", ("가" * 21846).encode()),
            ("broken.hwpx", b"not a package"),
            ("overflow.hwpx", self.material_hwpx(oversized_text=True)),
            ("missing-section.hwpx", self.material_hwpx(incomplete_spine=True)),
            ("control.hwpx", self.material_hwpx(invalid_control=True)),
            ("entity.hwpx", self.material_hwpx(entity=True)),
        ]
        for name, raw in cases:
            with self.subTest(name=name):
                code, _ = self.upload_material(raw, name)
                self.assertEqual(code, 409)
                self.assertFalse((self.info.root / "inbox").exists())
                self.assertFalse(self.control.materials)
        with self.assertRaises(ValueError):
            self.control.upload(b"x" * (512 * 1024 + 1), "too-large.txt")
        for name, maximum in (("too-large.txt", 512 * 1024), ("too-large.hwpx", 64 * 1024 * 1024)):
            code, _ = self.upload_material(b"", name, **{"Content-Length": str(maximum + 1)})
            self.assertEqual(code, 409)
        self.assertFalse((self.info.root / "inbox").exists())
        self.assertEqual(before, {path.relative_to(self.info.root): path.read_bytes()
                                 for path in self.info.root.rglob("*") if path.is_file()})
        self.provider_mock.assert_not_called()

    def test_maximum_staged_text_is_local_only_and_context_overflow_is_not_truncated(self):
        raw = b"x" * 65536
        code, body = self.upload_material(raw, "bounded.txt")
        self.assertEqual(code, 201, body)
        item = json.loads(body)
        snapshot, = (self.info.root / "inbox").iterdir()
        self.assertEqual(snapshot.read_bytes(), raw)
        code, _ = self.material_context(item)
        self.assertEqual(code, 409)
        self.assertEqual(snapshot.read_bytes(), raw)
        bounded_materials = []
        for name, text in (("first.py", b"a" * 20000), ("second.py", b"b" * 20000)):
            code, body = self.upload_material(text, name)
            self.assertEqual(code, 201, body)
            bounded_materials.append(json.loads(body))
        code, _ = self.request("POST", "/api/report-omp-context", {
            "instruction": "두 자료를 참고해 주세요.", "selection_ids": [self.section["id"]],
            "materials": [{"id": selected["id"], "own_achievement": False} for selected in bounded_materials],
            "previous_version": None,
        })
        self.assertEqual(code, 409)  # Each source fits; their combined model context does not.
        snapshots = {path.read_bytes() for path in (self.info.root / "inbox").iterdir()}
        self.assertEqual(snapshots, {raw, b"a" * 20000, b"b" * 20000})
        self.provider_mock.assert_not_called()
        self.assertFalse(list(self.info.root.rglob("draft-permission.json")))

    def test_rendered_memo_targets_are_authenticated_read_only_and_do_not_grant_scope(self):
        rows = projects.sections(self.info.root)
        index = next(i for i, row in enumerate(rows) if row["id"] == self.section["id"])
        before = {path.relative_to(self.files): path.read_bytes()
                  for path in self.files.rglob("*") if path.is_file()}
        payload = {"previous_version": None, "sha256": self.state()["source_sha256"],
                   "section_index": 0, "paragraph_index": index}
        code, body = self.request("POST", "/api/report-memo-target", payload)
        self.assertEqual(code, 200, body)
        target = json.loads(body)
        self.assertEqual(target["quote"], "SYNTHETIC REPORT BODY BEFORE")
        self.assertTrue(target["editable"])
        self.assertEqual(target["selection_ids"], [self.section["id"]])
        code, body = self.request("POST", "/api/report-memo-target",
                                  {**payload, "paragraph_index": 0})
        self.assertEqual(code, 200, body)
        self.assertFalse(json.loads(body)["editable"])
        self.assertEqual(json.loads(body)["selection_ids"], [])
        for override in ({"sha256": "0" * 64}, {"paragraph_index": True},
                         {"paragraph_index": len(rows)}, {"extra": "untrusted"}):
            with self.subTest(override=override):
                code, _ = self.request("POST", "/api/report-memo-target", {**payload, **override})
                self.assertEqual(code, 409)
        for options in ({"token": False}, {"origin": False}, {"revision": "stale"}):
            code, _ = self.request("POST", "/api/report-memo-target", payload, **options)
            self.assertEqual(code, 409 if "revision" in options else 403)
        self.assertEqual({path.relative_to(self.files): path.read_bytes()
                          for path in self.files.rglob("*") if path.is_file()}, before)
        self.provider_mock.assert_not_called()

    def test_memo_target_uses_current_revision_range_after_paragraph_split(self):
        self.output_text = ["SYNTHETIC FIRST PARAGRAPH", "SYNTHETIC SECOND PARAGRAPH"]
        self.start(self.preview())
        version, = self.state()["versions"]
        artifact = projects.candidate(self.info.root, version["id"])
        end = artifact["target_range"][1]
        payload = {"previous_version": version["id"], "sha256": version["sha256"],
                   "section_index": 0, "paragraph_index": end - 1}
        code, body = self.request("POST", "/api/report-memo-target", payload)
        self.assertEqual(code, 200, body)
        target = json.loads(body)
        self.assertEqual(target["quote"], "SYNTHETIC SECOND PARAGRAPH")
        self.assertTrue(target["editable"])
        self.assertEqual(target["selection_ids"], [self.section["id"]])
        code, body = self.request("POST", "/api/report-memo-target",
                                  {**payload, "paragraph_index": end})
        self.assertEqual(code, 200, body)
        self.assertFalse(json.loads(body)["editable"])
        self.assertEqual(json.loads(body)["selection_ids"], [])
        code, _ = self.request("POST", "/api/report-memo-target",
                              {**payload, "sha256": self.state()["source_sha256"]})
        self.assertEqual(code, 409)
        self.assertEqual(self.source.read_bytes(), self.original)
        self.assertEqual(self.provider_mock.call_count, 1)

    def test_original_source_is_exact_authenticated_and_read_only(self):
        before = {path.relative_to(self.files): path.read_bytes()
                  for path in self.files.rglob("*") if path.is_file()}
        expected = hashlib.sha256(self.original).hexdigest()
        state = self.state()
        self.assertEqual(state["source_sha256"], expected)
        self.assertFalse(state["versions"])
        code, body, headers = self.request("GET", "/api/report-source", return_headers=True)
        self.assertEqual(code, 200)
        self.assertEqual(body, self.original)
        self.assertEqual(headers["Content-Type"], "application/octet-stream")
        self.assertEqual(headers["X-Artifact-Sha256"], expected)
        self.assertEqual(int(headers["Content-Length"]), len(self.original))
        self.assertEqual(headers["Cache-Control"], "no-store")
        self.assertEqual(headers["X-Content-Type-Options"], "nosniff")
        self.assertEqual(
            {path.relative_to(self.files): path.read_bytes()
             for path in self.files.rglob("*") if path.is_file()}, before)
        self.provider_mock.assert_not_called()

    def test_source_auth_host_and_fixed_namespace(self):
        for token, headers in (
            (False, {}), (True, {"X-Review-Token": "wrong"}),
            (True, {"Host": "localhost:" + str(self.server.server_port)}),
        ):
            with self.subTest(token=token, headers=headers):
                code, _ = self.request("GET", "/api/report-source", token=token,
                                       extra_headers=headers)
                self.assertEqual(code, 403)
        for route in ("/api/report-source?path=source.hwpx",
                      "/api/report-source/source.hwpx",
                      "/api/report-source/../report-project.json",
                      "/api/report-source?member=Contents/section0.xml"):
            with self.subTest(route=route):
                code, _ = self.request("GET", route)
                self.assertEqual(code, 404)
        self.provider_mock.assert_not_called()

    def test_source_revalidates_locked_original_snapshot_runtime_and_size(self):
        for path in (self.source, self.info.root / "source.hwpx"):
            with self.subTest(path=path):
                saved = path.read_bytes()
                path.write_bytes(saved + b"SYNTHETIC TAMPER")
                try:
                    code, _ = self.request("GET", "/api/report-source")
                    self.assertEqual(code, 409)
                    self.assertFalse(self.state()["gate_passed"])
                    self.assertIsNone(self.state()["source_sha256"])
                finally:
                    path.write_bytes(saved)
        with patch.object(projects.harness.runtime, "identity", return_value={"changed": True}):
            code, _ = self.request("GET", "/api/report-source")
            self.assertEqual(code, 409)
        with patch.object(projects, "_DOCUMENT_LIMIT", len(self.original) - 1):
            code, _ = self.request("GET", "/api/report-source")
            self.assertEqual(code, 409)
        self.provider_mock.assert_not_called()

    def test_source_rejects_hardlinked_snapshot(self):
        import os

        snapshot = self.info.root / "source.hwpx"
        alias = self.files / "snapshot-hardlink.hwpx"
        os.link(snapshot, alias)
        code, _ = self.request("GET", "/api/report-source")
        self.assertEqual(code, 409)
        self.assertFalse(self.state()["gate_passed"])
        self.provider_mock.assert_not_called()

    def test_unapproved_file_exists_before_exact_acceptance_and_duplicate_never_resends(self):
        preview = self.preview()
        consent = self.start(preview)
        state = self.state()
        self.assertEqual(state["error"], "")
        version, = state["versions"]
        self.assertFalse(version["accepted"])
        self.assertIn("SYNTHETIC REPORT BODY BEFORE", version["comparison"][0]["before"])
        self.assertEqual(version["comparison"][0]["after"], self.output_text)
        artifact = projects.candidate(self.info.root, version["id"])
        path = Path(artifact["path"])
        self.assertTrue(path.is_file())
        self.assertEqual(self.source.read_bytes(), self.original)
        package = review.archive(path)
        self.assertIn(self.output_text, package["Contents/section0.xml"].decode())
        before = path.read_bytes()
        code, body = self.request("POST", "/api/report-start", consent)
        self.assertEqual(code, 202, body)
        self.assertEqual(self.provider_mock.call_count, 1)
        self.assertEqual(path.read_bytes(), before)
        code, body = self.request("POST", "/api/report-accept", {"version_id": version["id"], "sha256": version["sha256"]})
        self.assertEqual(code, 200, body)
        self.assertTrue(self.state()["versions"][0]["accepted"])
        self.assertEqual(path.read_bytes(), before)
        self.assertEqual(self.provider_mock.call_count, 1)
        reopened = projects.inspect(self.info.root)
        self.assertTrue(reopened.gate_passed, reopened.issues)
        self.assertIn(version["id"], reopened.accepted)
        code, downloaded = self.request("GET", "/api/report-artifact/" + version["id"])
        self.assertEqual(code, 200)
        self.assertEqual(hashlib.sha256(downloaded).hexdigest(), version["sha256"])

    def test_provider_failure_preserves_error_and_does_not_generate_a_document(self):
        self.provider_mock.side_effect = RuntimeError("SYNTHETIC PROVIDER FAILURE")
        self.start(self.preview())
        state = self.state()
        self.assertIn("RuntimeError: SYNTHETIC PROVIDER FAILURE", state["error"])
        self.assertFalse(state["active"])
        self.assertIsNone(state["progress"])
        self.assertFalse(state["versions"])
        self.assertEqual(self.provider_mock.call_count, 1)
        self.assertEqual(len(list(self.info.root.rglob("failed.json"))), 1)
        self.assertEqual(self.source.read_bytes(), self.original)

    def test_context_cost_scope_and_same_origin_are_required_before_provider_or_grant(self):
        preview = self.preview()
        valid = {"preview_sha256": preview["preview_sha256"], "context_confirmed": True,
                 "cost_confirmed": True, "draft_only_confirmed": True}
        for key in ("context_confirmed", "cost_confirmed", "draft_only_confirmed"):
            for value in (False, 1):
                with self.subTest(key=key, value=value):
                    code, _ = self.request("POST", "/api/report-start", {**valid, key: value})
                    self.assertEqual(code, 409)
        code, _ = self.request("POST", "/api/report-start", valid, origin=False)
        self.assertEqual(code, 403)
        changed = self.preview("다른 문장으로 새롭게 수정해 주세요.")
        self.assertNotEqual(changed["preview_sha256"], preview["preview_sha256"])
        code, _ = self.request("POST", "/api/report-start", valid)
        self.assertEqual(code, 409)
        self.provider_mock.assert_not_called()
        self.assertFalse(list(self.info.root.rglob("draft-permission.json")))
        self.assertFalse(self.state()["versions"])

    def test_verified_hwp_open_and_changed_artifact_refuse_acceptance_download_and_launch(self):
        self.start(self.preview())
        version = self.state()["versions"][0]
        selected = {"version_id": version["id"], "sha256": version["sha256"]}
        with patch("hwp_report_control.os.startfile") as opened:
            code, body = self.request("POST", "/api/report-open", selected)
            self.assertEqual(code, 200, body)
            owned = Path(projects.candidate(self.info.root, version["id"])["path"])
            editable = Path(opened.call_args.args[0])
            self.assertNotEqual(editable, owned)
            self.assertEqual(editable.read_bytes(), owned.read_bytes())
            owned.write_bytes(owned.read_bytes() + b"SYNTHETIC MUTATION")
            for route in ("/api/report-open", "/api/report-accept"):
                code, _ = self.request("POST", route, selected)
                self.assertEqual(code, 409)
            code, _ = self.request("GET", "/api/report-artifact/" + version["id"])
            self.assertEqual(code, 409)
            self.assertEqual(opened.call_count, 1)
        self.assertFalse(self.state()["gate_passed"])
        self.assertEqual(self.source.read_bytes(), self.original)

    def test_follow_up_creates_new_unaccepted_version_without_rewriting_selected_one(self):
        self.start(self.preview())
        first = self.state()["versions"][0]
        code, body = self.request("POST", "/api/report-accept", {"version_id": first["id"], "sha256": first["sha256"]})
        self.assertEqual(code, 200, body)
        original_path = Path(projects.candidate(self.info.root, first["id"])["path"])
        original = original_path.read_bytes()
        self.output_text = "SYNTHETIC REPORT BODY SECOND REVISION"
        self.start(self.preview("이 수정본을 한 번 더 다듬어 주세요.", previous=first["id"]))
        state = self.state()
        self.assertEqual(state["error"], "")
        self.assertEqual(len(state["versions"]), 2)
        self.assertTrue(state["versions"][0]["accepted"])
        self.assertFalse(state["versions"][1]["accepted"])
        self.assertEqual(original_path.read_bytes(), original)
        self.assertEqual(self.source.read_bytes(), self.original)
        self.assertEqual(self.provider_mock.call_count, 2)

    def test_hwp_editor_save_uses_review_copy_and_imports_as_new_unaccepted_version(self):
        from io import BytesIO
        import hwp_template_draft as draft

        self.start(self.preview())
        first = self.state()["versions"][0]
        chosen = {"version_id": first["id"], "sha256": first["sha256"]}
        canonical = Path(projects.candidate(self.info.root, first["id"])["path"])
        immutable = canonical.read_bytes()
        with patch("hwp_report_control.os.startfile") as opened:
            code, body = self.request("POST", "/api/report-open", chosen)
            self.assertEqual(code, 200, body)
            editable = Path(opened.call_args.args[0])
        self.assertNotEqual(editable, canonical, "Opening the canonical version makes ordinary Save corrupt its proof")
        self.assertEqual(editable.read_bytes(), immutable)
        parts = review.archive(BytesIO(editable.read_bytes()))
        section = review.xml_root(parts[draft.PART])
        section[2][0].find(draft.HP + "t").text = "SYNTHETIC REPORT BODY MANUALLY REVISED"
        parts[draft.PART] = draft._xml(section)
        editable.write_bytes(draft._zip(immutable, parts, []))
        self.assertEqual(canonical.read_bytes(), immutable)
        code, _ = self.request("POST", "/api/report-accept", chosen)
        self.assertEqual(code, 409)
        code, body = self.request("POST", "/api/report-import-opened", chosen)
        self.assertEqual(code, 201, body)
        state = self.state()
        self.assertTrue(state["gate_passed"], state["issues"])
        self.assertEqual(len(state["versions"]), 2)
        self.assertTrue(state["versions"][-1]["external"])
        self.assertFalse(state["versions"][-1]["accepted"])
        self.assertEqual(canonical.read_bytes(), immutable)
        self.assertEqual(self.provider_mock.call_count, 1)
        imported = state["versions"][-1]
        self.assertEqual(json.loads(body)["version_id"], imported["id"])
        self.output_text = "SYNTHETIC REPORT BODY FOLLOWUP AFTER IMPORT"
        self.start(self.preview("선택한 가져온 수정본에서 계속 다듬습니다.", previous=imported["id"]))
        latest = self.state()["versions"][-1]
        self.assertNotEqual(latest["id"], imported["id"])
        code, body = self.request("POST", "/api/report-import-opened", chosen)
        self.assertEqual(code, 201, body)
        result = json.loads(body)
        self.assertEqual(result["version_id"], imported["id"])
        self.assertEqual(result["sha256"], imported["sha256"])
        self.assertEqual(len(self.state()["versions"]), 3)
        self.assertEqual(self.provider_mock.call_count, 2)
        self.assertEqual(canonical.read_bytes(), immutable)

    def test_request_editor_accepts_long_bounded_text_and_rejects_overflow_before_permission(self):
        code, body = self.request("POST", "/api/report-preview", {
            "instruction": "a" * 9000, "selection_ids": [self.section["id"]],
            "materials": [], "previous_version": None,
        })
        self.assertEqual(code, 200)
        self.start(json.loads(body))
        version, = self.state()["versions"]
        self.assertTrue(self.state()["gate_passed"], self.state()["issues"])
        self.assertFalse(version["accepted"])
        self.assertEqual(self.provider_mock.call_count, 1)
        grants = list(self.info.root.rglob("draft-permission.json"))
        code, _ = self.request("POST", "/api/report-preview", {
            "instruction": "a" * 16001, "selection_ids": [self.section["id"]],
            "materials": [], "previous_version": None,
        })
        self.assertEqual(code, 409)
        self.assertEqual(self.provider_mock.call_count, 1)
        self.assertEqual(list(self.info.root.rglob("draft-permission.json")), grants)

    def test_model_paragraph_expansion_keeps_exact_original_boundaries(self):
        import hwp_template_draft as draft

        self.output_text = ["SYNTHETIC FIRST REVISED PARAGRAPH", "SYNTHETIC SECOND REVISED PARAGRAPH"]
        self.start(self.preview())
        state = self.state()
        self.assertEqual(state["error"], "")
        self.assertTrue(state["gate_passed"], state["issues"])
        item = projects.candidate(self.info.root, state["versions"][0]["id"])
        old = review.xml_root(draft.parts_from(self.original)[draft.PART])
        new = review.xml_root(draft.parts_from(Path(item["path"]).read_bytes())[draft.PART])
        self.assertEqual(draft.texts(list(new)[2:4]), self.output_text)
        self.assertEqual(draft.node_hash(list(old)[:2]), draft.node_hash(list(new)[:2]))
        self.assertEqual(draft.node_hash(list(old)[3:]), draft.node_hash(list(new)[4:]))
        self.assertFalse(state["versions"][0]["accepted"])

    def test_unsubmitted_preview_does_not_replace_retained_authorized_scope(self):
        self.start(self.preview())
        other = next(row for row in projects.sections(self.info.root) if row["text"] == "SYNTHETIC BEFORE BOUNDARY")
        code, body = self.request("POST", "/api/report-preview", {
            "instruction": "원본의 다른 부분을 수정할지 미리 확인합니다.",
            "selection_ids": [other["id"]], "materials": [], "previous_version": None,
        })
        self.assertEqual(code, 200, body)
        self.assertEqual(self.state()["selection_ids"], [self.section["id"]])
        self.assertEqual(projects.inspect(self.info.root).requests[-1]["selection_ids"], [self.section["id"]])
        self.assertEqual(self.provider_mock.call_count, 1)


if __name__ == "__main__":
    unittest.main()
