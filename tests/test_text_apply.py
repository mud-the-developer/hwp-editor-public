"""Synthetic ONLY: no Hancom, real document or real human approval."""

from pathlib import Path
from types import ModuleType, SimpleNamespace
import http.client
import json
import re
import sys
import tempfile
import threading
import unittest
from unittest.mock import Mock, patch
import zipfile

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import hwp_review as r
import hwp_text_apply as a
from packaged_smoke_runtime import fixture
from test_com_cache import cached_com_modules
from test_native_owner import fake_owned_session


class TextApplyTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.w = Path(self.temp.name).resolve() / "fixture"
        fixture(self.w, r)
        with zipfile.ZipFile(self.w / "baseline.hwpx", "w") as z:
            z.writestr("mimetype", "application/hwp+zip")
            z.writestr(
                "Contents/section0.xml",
                '<hs:sec xmlns:hs="http://www.hancom.co.kr/hwpml/2011/section" xmlns:hp="http://www.hancom.co.kr/hwpml/2011/paragraph"><hp:p id="0"><hp:run charPrIDRef="1"><hp:t>SYNTHETIC ABCD TEXT</hp:t></hp:run><hp:linesegarray><hp:lineseg textpos="0"/><hp:lineseg textpos="8"/></hp:linesegarray></hp:p></hs:sec>',
            )
        self.m = r.read_json(self.w / "manifest.json")
        anchor = r.index_hwpx(self.w / "baseline.hwpx")[0][0]
        anchor.pop("raw")
        anchor.update(page_hint=1, boxes=[[20, 20, 250, 50]])
        self.m.update(
            anchors=[anchor], baseline_hwpx_sha256=r.file_hash(self.w / "baseline.hwpx")
        )
        r.write_json(self.w / "manifest.json", self.m)
        self.plan = {
            "schema": 1,
            "workspace_id": self.m["workspace_id"],
            "baseline_hwpx_sha256": self.m["baseline_hwpx_sha256"],
            "edits": [
                {
                    "anchor_id": anchor["anchor_id"],
                    "old_text": anchor["old_text"],
                    "new_text": "SYNTHETIC WXYZ TEXT",
                }
            ],
        }
        r.write_json(self.w / "proposal.json", self.plan)
        self.sha = r.plan_hash(self.plan)
        self.revision = '"' + r.file_hash(self.w / "proposal.json") + '"'
        self.payload = {
            "workspace_id": self.m["workspace_id"],
            "baseline_hwpx_sha256": self.m["baseline_hwpx_sha256"],
            "plan_sha256": self.sha,
            "text_apply_confirmed": True,
        }
        self.module = patch.object(
            a, "registered_security_module", return_value={"SYNTHETIC_ONLY": True}
        )
        self.module.start()
        self.addCleanup(self.module.stop)

    def consent(self):
        # Synthetic fixture only. Actual documents must use the local human UI.
        r.write_json(
            self.w / "approved-plan.json",
            {
                "approved_via": "local-review-ui",
                "plan_sha256": self.sha,
                "plan": self.plan,
            },
        )

    def phase(self):
        self.consent()
        self.run_dir = self.w / "runs" / "20261012T000000-a1b2c3d4"
        self.run_dir.mkdir(parents=True)
        r.write_json(self.run_dir / "plan.json", self.plan)
        r.patch_hwpx(
            self.w / "baseline.hwpx",
            self.run_dir / "candidate.hwpx",
            self.plan["edits"],
        )
        self.lease = r.acquire_apply_lock(self.w, self.m, self.plan, self.run_dir.name)
        self.request = {
            "schema": 1,
            "phase": 0,
            "lease": self.lease,
            "input_sha256": r.file_hash(self.run_dir / "candidate.hwpx"),
            "plan_sha256": self.sha,
        }
        r.write_json(self.run_dir / "text-export-request-0.json", self.request)

    def test_disabled_missing_consent_wrong_hash_or_confirmation_never_launches(self):
        c = a.TextApplyController(self.w, enabled=True)
        with patch.object(a.threading.Thread, "start") as launch:
            with self.assertRaises(ValueError):
                a.TextApplyController(self.w).start(self.payload, self.revision)
            with self.assertRaises(FileNotFoundError):
                c.start(self.payload, self.revision)
            self.consent()
            for payload in (
                {**self.payload, "text_apply_confirmed": 1},
                {**self.payload, "command": "APPLY"},
                {**self.payload, "plan_sha256": "x"},
            ):
                with self.assertRaises(ValueError):
                    c.start(payload, self.revision)
            with self.assertRaises(ValueError):
                c.start(self.payload, '"stale"')
            launch.assert_not_called()
            self.assertFalse((self.w / "runs").exists())

    def test_active_job_blocks_duplicate_stop_and_returns_no_nonce_or_pid(self):
        self.consent()
        c = a.TextApplyController(self.w, enabled=True)
        entered = threading.Event()
        release = threading.Event()

        def slow(*args, **kwargs):
            entered.set()
            release.wait(5)
            kwargs["text_exporter"].phase = 2

        with patch.object(r, "apply", side_effect=slow):
            reply = c.start(self.payload, self.revision)
            self.assertTrue(entered.wait(2))
            try:
                self.assertTrue(reply["accepted"])
                self.assertTrue(c.snapshot()["active"])
                with self.assertRaises(ValueError):
                    c.start(self.payload, self.revision)
                with self.assertRaises(RuntimeError):
                    c.require_idle()
                self.assertNotIn("owner_nonce", json.dumps(c.snapshot()))
                self.assertNotIn('"pid"', json.dumps(c.snapshot()))
            finally:
                release.set()
                assert c.thread is not None
                c.thread.join(5)
        self.assertEqual(c.snapshot()["status"], "finished")
        with self.assertRaises(ValueError):
            c.start(self.payload, self.revision)

    def test_thread_failure_is_visible_and_same_hash_never_retries(self):
        self.consent()
        c = a.TextApplyController(self.w, enabled=True)
        with patch.object(
            r, "apply", side_effect=RuntimeError("SYNTHETIC no retry")
        ) as apply:
            c.start(self.payload, self.revision)
            assert c.thread is not None
            c.thread.join(5)
            self.assertEqual(c.snapshot()["status"], "blocked")
            self.assertIn("SYNTHETIC", c.snapshot()["error"])
            with self.assertRaises(ValueError):
                c.start(self.payload, self.revision)
            self.assertEqual(apply.call_count, 1)

    def test_source_cache_protection_and_schema_failure_precede_thread_launch(self):
        self.consent()
        c = a.TextApplyController(self.w, enabled=True)
        with patch.object(a.threading.Thread, "start") as launch:
            original = self.plan["edits"][0]["new_text"]
            for new in ("", "x", "new\ntext"):
                self.plan["edits"][0]["new_text"] = new
                r.write_json(self.w / "proposal.json", self.plan)
                with self.assertRaises(ValueError):
                    c.start(self.payload, self.revision)
            self.plan["edits"][0]["new_text"] = original
            self.plan["schema"] = 2
            r.write_json(self.w / "proposal.json", self.plan)
            with self.assertRaises(ValueError):
                c.start(self.payload, self.revision)
            self.plan["schema"] = 1
            r.write_json(self.w / "proposal.json", self.plan)
            self.m["protected_pages"] = [1]
            r.write_json(self.w / "manifest.json", self.m)
            with self.assertRaises(ValueError):
                c.start(self.payload, self.revision)
            self.m["protected_pages"] = []
            r.write_json(self.w / "manifest.json", self.m)
            (self.w / "synthetic-source.hwp").write_bytes(b"CHANGED SYNTHETIC")
            with self.assertRaises(ValueError):
                c.start(self.payload, self.revision)
            launch.assert_not_called()

    def test_worker_rejects_bad_phase_paths_lease_approval_and_input_before_com(self):
        self.phase()
        with patch.object(a, "export_phase") as native:
            for run_id, phase in (
                ("../outside", 0),
                (self.run_dir.name, True),
                (self.run_dir.name, 2),
            ):
                with self.assertRaises(ValueError):
                    a.run_worker(self.w, run_id, phase)
            wrong = {**self.request, "phase": 1}
            r.write_json(self.run_dir / "text-export-request-0.json", wrong)
            with self.assertRaises(ValueError):
                a.run_worker(self.w, self.run_dir.name, 0)
            r.write_json(self.run_dir / "text-export-request-0.json", self.request)
            (self.w / "approved-plan.json").unlink()
            with self.assertRaises(FileNotFoundError):
                a.run_worker(self.w, self.run_dir.name, 0)
            self.consent()
            (self.run_dir / "candidate.hwpx").write_bytes(b"CHANGED")
            with self.assertRaises(ValueError):
                a.run_worker(self.w, self.run_dir.name, 0)
            native.assert_not_called()

    def test_exporter_never_calls_generic_native_and_derives_only_frozen_worker_flags(
        self,
    ):
        self.phase()
        (self.run_dir / "text-export-request-0.json").unlink()
        exporter = a.TextExporter(self.w, self.sha)

        def child(command, **kwargs):
            self.assertEqual(
                command[1:],
                [
                    "--text-export",
                    str(self.w),
                    "--text-run",
                    self.run_dir.name,
                    "--text-phase",
                    "0",
                ],
            )
            self.assertEqual(kwargs["timeout"], 240)
            self.assertEqual(kwargs["stdin"], a.subprocess.DEVNULL)
            r.write_json(
                self.run_dir / "text-export-reply-0.json",
                {
                    "ok": True,
                    "phase": 0,
                    "plan_sha256": self.sha,
                    "native": {"SYNTHETIC": True},
                },
            )
            return SimpleNamespace(returncode=0)

        with (
            patch.object(a, "frozen", return_value=True),
            patch.object(a.subprocess, "run", side_effect=child),
            patch.object(r, "native_export") as generic,
        ):
            result = exporter(
                self.run_dir / "candidate.hwpx",
                self.run_dir / "candidate-buffer.pdf",
                hwp=self.run_dir / "candidate.hwp",
            )
            self.assertTrue(result["SYNTHETIC"])
            generic.assert_not_called()
            self.assertEqual(exporter.phase, 1)
        with patch.object(a.subprocess, "run") as process:
            with self.assertRaises(ValueError):
                exporter(self.run_dir / "candidate.hwpx", self.w / "OUTSIDE.pdf")
            process.assert_not_called()

    def test_no_security_module_never_creates_worker_copy_or_starts_com(self):
        self.phase()
        with patch.object(
            a,
            "registered_security_module",
            side_effect=ValueError("SYNTHETIC missing module"),
        ):
            with self.assertRaises(ValueError):
                a.export_phase(self.w, self.run_dir.name, 0)
        self.assertFalse((self.run_dir / "text-export-input-0.hwpx").exists())

    @patch.object(a, "owned_session", new=fake_owned_session)
    def test_owned_com_opens_only_exact_new_copy_no_edit_and_always_cleans_own_instance(
        self,
    ):
        self.phase()
        app = Mock()
        app.PageCount = 1
        app.Version = "SYNTHETIC COM"
        app.SaveAs.side_effect = lambda path, *args: (
            Path(path).write_bytes(b"SYNTHETIC EXPORT") or True
        )
        com = ModuleType("pythoncom")
        setattr(com, "CoInitialize", Mock())
        setattr(com, "CoUninitialize", Mock())
        client = ModuleType("win32com.client")
        setattr(client, "DispatchEx", Mock(return_value=app))
        before = (self.run_dir / "candidate.hwpx").read_bytes()
        with patch.dict(sys.modules, cached_com_modules(com, client, self.w)):
            a.export_phase(self.w, self.run_dir.name, 0)
        self.assertEqual(
            app.Open.call_args.args[0], str(self.run_dir / "text-export-input-0.hwpx")
        )
        self.assertEqual(before, (self.run_dir / "candidate.hwpx").read_bytes())
        app.Save.assert_not_called()
        app.HAction.Execute.assert_not_called()
        app.Run.assert_not_called()
        app.Clear.assert_called_once_with(1)
        app.Quit.assert_called_once()
        getattr(com, "CoUninitialize").assert_called_once()
        self.assertTrue((self.w / r.APPLY_LOCK).exists())

    def test_generic_frozen_export_remains_blocked_and_injected_broad_exporter_is_rejected(
        self,
    ):
        self.consent()
        with (
            patch.object(r, "frozen", return_value=True),
            patch.object(r.subprocess, "run") as process,
        ):
            with self.assertRaises(RuntimeError):
                r.native_export(self.w / "synthetic-source.hwp", self.w / "OUT.pdf")
            process.assert_not_called()
        with self.assertRaises(ValueError):
            r.apply(
                self.w,
                self.w / "proposal.json",
                text_exporter=lambda *a: None,
                expected_plan_sha256=self.sha,
            )
        with self.assertRaises(ValueError):
            r.apply(self.w, self.w / "proposal.json", expected_plan_sha256="wrong")
        self.assertFalse((self.w / r.APPLY_LOCK).exists())

    def test_http_guards_and_active_job_block_writes_and_server_close(self):
        self.consent()
        server = r.create_review_server(self.w, text_apply=True)
        thread = threading.Thread(target=server.serve_forever)
        thread.start()
        entered = threading.Event()
        release = threading.Event()

        def slow(*args, **kwargs):
            entered.set()
            release.wait(5)
            kwargs["text_exporter"].phase = 2

        origin = f"http://127.0.0.1:{server.server_port}"

        def request(method, path, body=None, headers=None):
            connection = http.client.HTTPConnection(
                "127.0.0.1", server.server_port, timeout=5
            )
            try:
                connection.request(
                    method,
                    path,
                    json.dumps(body) if body is not None else None,
                    headers or {},
                )
                response = connection.getresponse()
                return response.status, response.read()
            finally:
                connection.close()

        match = re.search(r"const token='([a-f0-9]+)'", request("GET", "/")[1].decode())
        if match is None:
            self.fail("Synthetic token missing")
        token = match.group(1)
        headers = {
            "Origin": origin,
            "X-Review-Token": token,
            "Content-Type": "application/json",
            "If-Match": self.revision,
        }
        try:
            self.assertEqual(
                request(
                    "POST",
                    "/api/apply-text",
                    self.payload,
                    {**headers, "Origin": "http://evil.invalid"},
                )[0],
                403,
            )
            self.assertEqual(
                request(
                    "POST",
                    "/api/apply-text",
                    self.payload,
                    {**headers, "X-Review-Token": "wrong"},
                )[0],
                403,
            )
            missing = dict(headers)
            del missing["If-Match"]
            self.assertEqual(
                request("POST", "/api/apply-text", self.payload, missing)[0], 428
            )
            self.assertEqual(
                request(
                    "POST",
                    "/api/apply-text",
                    self.payload,
                    {**headers, "If-Match": '"stale"'},
                )[0],
                409,
            )
            with patch.object(r, "apply", side_effect=slow):
                self.assertEqual(
                    request("POST", "/api/apply-text", self.payload, headers)[0], 202
                )
                self.assertTrue(entered.wait(2))
                for path in ("/api/apply-text", "/api/approve", "/api/text-proposal"):
                    self.assertEqual(
                        request("POST", path, self.payload, headers)[0], 423
                    )
                self.assertTrue(
                    json.loads(request("GET", "/api/state")[1])["text_apply"]["active"]
                )
                with self.assertRaises(RuntimeError):
                    server.server_close()
                release.set()
                getattr(server, "text_apply_controller").thread.join(5)
        finally:
            release.set()
            if getattr(server, "text_apply_controller").thread:
                getattr(server, "text_apply_controller").thread.join(5)
            server.shutdown()
            thread.join(5)
            server.server_close()


if __name__ == "__main__":
    unittest.main()
