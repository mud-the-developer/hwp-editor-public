"""Real loopback discovery API; SYNTHETIC workspace, no CLI/COM/model launch."""

import http.client
import importlib
import json
import re
import threading
import unittest
from unittest.mock import Mock, patch

fixture = importlib.import_module("test_contract_control")
r = importlib.import_module("hwp_review")
a = importlib.import_module("hwp_agents")
p = importlib.import_module("hwp_pi_probe")
chat_module = importlib.import_module("hwp_pi_chat")


class AgentHttpTests(unittest.TestCase):
    def setUp(self):
        owner = fixture.ContractControlTests(
            "test_default_disabled_never_launches_or_changes_approval"
        )
        owner.setUp()
        self.addCleanup(owner.doCleanups)
        self.w, _, _, _ = owner.stage()
        self.server = r.create_review_server(self.w)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.addCleanup(self.close)
        self.origin = f"http://127.0.0.1:{self.server.server_port}"
        code, body = self.request("GET", "/")
        self.assertEqual(code, 200)
        match = re.search(r"const token='([a-f0-9]+)'", body.decode())
        if match is None:
            raise AssertionError("Synthetic token missing")
        self.token = match[1]

    def close(self):
        self.server.shutdown()
        self.thread.join(5)
        self.server.server_close()

    def request(self, method, path, payload=None, headers=None):
        connection = http.client.HTTPConnection(
            "127.0.0.1", self.server.server_port, timeout=3
        )
        try:
            connection.request(
                method,
                path,
                json.dumps(payload).encode() if payload is not None else None,
                headers or {},
            )
            response = connection.getresponse()
            return response.status, response.read()
        finally:
            connection.close()

    def headers(self):
        return {
            "Origin": self.origin,
            "X-Review-Token": self.token,
            "Content-Type": "application/json",
        }

    def test_discovery_and_paths_are_same_origin_token_gated_and_payload_is_closed(
        self,
    ):
        with patch.object(a, "scan_path") as scan:
            for headers in (
                {},
                {**self.headers(), "Origin": "https://example.invalid"},
                {**self.headers(), "X-Review-Token": "wrong"},
                {**self.headers(), "Host": "example.invalid"},
            ):
                self.assertEqual(
                    self.request("POST", "/api/agents/discover", {}, headers)[0], 403
                )
            for headers in (
                {},
                {**self.headers(), "Origin": "https://example.invalid"},
                {**self.headers(), "Host": "example.invalid"},
            ):
                self.assertEqual(
                    self.request("GET", "/api/agents", headers=headers)[0], 403
                )
            self.assertEqual(
                self.request("GET", "/api/agents?path=PRIVATE", headers=self.headers())[
                    0
                ],
                400,
            )
            for payload in (
                [],
                {"path": "DO-NOT-READ"},
                {"prompt": "DO-NOT-SEND"},
                {"command": "DO-NOT-RUN"},
            ):
                self.assertEqual(
                    self.request(
                        "POST", "/api/agents/discover", payload, self.headers()
                    )[0],
                    400,
                )
            scan.assert_not_called()

    def test_slow_scan_does_not_block_serial_document_state_or_duplicate_scan(self):
        entered, release = threading.Event(), threading.Event()
        before = {
            str(p.relative_to(self.w)): r.file_hash(p)
            for p in self.w.rglob("*")
            if p.is_file()
        }

        def slow():
            entered.set()
            if not release.wait(10):
                raise AssertionError("Synthetic release missing")
            return {
                "agents": [],
                "scope": "SYNTHETIC",
                "ignored_entries": 0,
                "unreadable_candidates": 0,
            }

        discovery = getattr(self.server, "agent_discovery")
        with (
            patch.object(a, "scan_path", side_effect=slow) as scan,
            patch.object(r, "apply") as apply,
        ):
            try:
                self.assertEqual(
                    self.request("POST", "/api/agents/discover", {}, self.headers())[0],
                    202,
                )
                self.assertTrue(entered.wait(2))
                owned = discovery.thread
                self.assertEqual(
                    self.request("POST", "/api/agents/discover", {}, self.headers())[0],
                    202,
                )
                self.assertIs(discovery.thread, owned)
                code, body = self.request("GET", "/api/agents", headers=self.headers())
                self.assertEqual(code, 200)
                self.assertEqual(json.loads(body)["status"], "discovering")
                self.assertEqual(self.request("GET", "/api/state")[0], 200)
                self.assertEqual(self.request("GET", "/api/versions")[0], 200)
                release.set()
                if owned is None:
                    raise AssertionError("Synthetic thread missing")
                owned.join(5)
                self.assertFalse(owned.is_alive())
                code, body = self.request("GET", "/api/agents", headers=self.headers())
                result = json.loads(body)
                self.assertEqual(code, 200)
                self.assertEqual(result["status"], "finished")
                self.assertFalse(result["executed"])
                self.assertFalse(result["connected"])
                self.assertFalse(result["model_called"])
                scan.assert_called_once()
                apply.assert_not_called()
            finally:
                release.set()
                if discovery.thread is not None:
                    discovery.thread.join(5)
        self.assertEqual(
            before,
            {
                str(p.relative_to(self.w)): r.file_hash(p)
                for p in self.w.rglob("*")
                if p.is_file()
            },
        )

    def test_pi_probe_is_token_gated_and_has_no_raw_prompt_or_command_route(self):
        check = getattr(self.server, "pi_probe_controller")
        with (
            patch.object(check, "prepare") as prepare,
            patch.object(check, "start") as start,
        ):
            for route in ("/api/pi/prepare", "/api/pi/probe"):
                for headers in (
                    {},
                    {**self.headers(), "Origin": "https://example.invalid"},
                    {**self.headers(), "X-Review-Token": "wrong"},
                ):
                    self.assertEqual(self.request("POST", route, {}, headers)[0], 403)
            self.assertEqual(self.request("GET", "/api/pi")[0], 403)
            self.assertEqual(
                self.request(
                    "GET",
                    "/api/pi",
                    headers={**self.headers(), "Origin": "https://example.invalid"},
                )[0],
                403,
            )
            self.assertEqual(
                self.request("GET", "/api/pi?prompt=BLOCK", headers=self.headers())[0],
                400,
            )
            for payload in (
                [],
                {},
                {"prepare_confirmed": 1},
                {"prepare_confirmed": True, "prompt": "BLOCK"},
                {"path": "BLOCK"},
            ):
                self.assertEqual(
                    self.request("POST", "/api/pi/prepare", payload, self.headers())[0],
                    400,
                )
            prepare.assert_not_called()
            start.assert_not_called()

    def test_pi_prepare_and_probe_are_separate_exact_actions_and_do_not_block_documents(
        self,
    ):
        before = {
            str(item.relative_to(self.w)): r.file_hash(item)
            for item in self.w.rglob("*")
            if item.is_file()
        }
        discovery = getattr(self.server, "agent_discovery")
        check = getattr(self.server, "pi_probe_controller")
        runtime = {
            "cli": "SYNTHETIC/pi.cmd",
            "node": "SYNTHETIC/node.exe",
            "sdk_entry": "SYNTHETIC/index.js",
            "sdk_version": "1.2.0",
            "sha256": "a" * 64,
        }
        presence = {
            "status": "finished",
            "generation": 5,
            "agents": [{"id": "pi", "status": "detected", "path": runtime["cli"]}],
        }
        entered, release = threading.Event(), threading.Event()

        def slow(runtime, cancel):
            entered.set()
            if not release.wait(8):
                raise AssertionError("Synthetic release missing")
            return {
                "transport_verified": True,
                "connected": False,
                "authenticated": False,
                "model_called": False,
                "tools_enabled": False,
                "document_shared": False,
                "process_exited": True,
            }

        with (
            patch.object(discovery, "snapshot", return_value=presence),
            patch.object(p, "resolve_runtime", return_value=runtime),
            patch.object(p, "run_probe", side_effect=slow) as run,
            patch.object(r, "apply") as apply,
        ):
            try:
                self.assertEqual(
                    self.request(
                        "POST",
                        "/api/pi/prepare",
                        {"prepare_confirmed": True},
                        self.headers(),
                    )[0],
                    202,
                )
                if check.thread is not None:
                    check.thread.join(5)
                code, body = self.request("GET", "/api/pi", headers=self.headers())
                state = json.loads(body)
                self.assertEqual(code, 200)
                self.assertEqual(state["status"], "prepared")
                run.assert_not_called()
                exact = {
                    "probe_id": state["probe_id"],
                    "runtime_sha256": state["runtime"]["sha256"],
                    "execute_confirmed": True,
                }
                for payload in (
                    {},
                    {**exact, "execute_confirmed": 1},
                    {**exact, "runtime_sha256": "wrong"},
                    {**exact, "command": "BLOCK"},
                ):
                    self.assertEqual(
                        self.request("POST", "/api/pi/probe", payload, self.headers())[
                            0
                        ],
                        400,
                    )
                run.assert_not_called()
                self.assertEqual(
                    self.request("POST", "/api/pi/probe", exact, self.headers())[0], 202
                )
                self.assertTrue(entered.wait(2))
                self.assertEqual(self.request("GET", "/api/state")[0], 200)
                self.assertEqual(
                    self.request("POST", "/api/pi/probe", exact, self.headers())[0], 400
                )
                release.set()
                if check.thread is not None:
                    check.thread.join(5)
                code, body = self.request("GET", "/api/pi", headers=self.headers())
                done = json.loads(body)
                self.assertEqual(code, 200)
                self.assertEqual(done["status"], "passed")
                self.assertFalse(done["connected"])
                self.assertFalse(done["model_called"])
                run.assert_called_once()
                apply.assert_not_called()
            finally:
                release.set()
                if check.thread is not None:
                    check.thread.join(5)
        self.assertEqual(
            before,
            {
                str(item.relative_to(self.w)): r.file_hash(item)
                for item in self.w.rglob("*")
                if item.is_file()
            },
        )

    def test_chat_profile_and_model_routes_are_strictly_token_origin_gated(self):
        check = getattr(self.server, "pi_chat_controller")
        actions = (
            "/api/pi/chat/prepare",
            "/api/pi/chat/connect",
            "/api/pi/chat/send",
            "/api/pi/chat/cancel",
            "/api/pi/chat/disconnect",
        )
        with (
            patch.object(chat_module, "resolve_chat_runtime") as resolve,
            patch.object(chat_module, "call_chat") as call,
        ):
            self.assertEqual(self.request("GET", "/api/pi/chat")[0], 403)
            for route in actions:
                for headers in (
                    {},
                    {**self.headers(), "Origin": "https://example.invalid"},
                ):
                    self.assertEqual(self.request("POST", route, {}, headers)[0], 403)
            for payload in (
                {},
                {"prepare_confirmed": 1},
                {"prepare_confirmed": True, "profile_dir": "BLOCK"},
            ):
                self.assertEqual(
                    self.request(
                        "POST", "/api/pi/chat/prepare", payload, self.headers()
                    )[0],
                    400,
                )
            self.assertEqual(
                self.request(
                    "GET", "/api/pi/chat?history=BLOCK", headers=self.headers()
                )[0],
                400,
            )
            resolve.assert_not_called()
            call.assert_not_called()
            self.assertEqual(check.snapshot()["status"], "disconnected")

    def test_chat_send_is_separate_from_profile_consent_and_document_actions(self):
        before = {
            str(item.relative_to(self.w)): r.file_hash(item)
            for item in self.w.rglob("*")
            if item.is_file()
        }
        discovery = getattr(self.server, "agent_discovery")
        check = getattr(self.server, "pi_chat_controller")
        runtime = {
            "cli": "SYNTHETIC/agent/bin/pi.cmd",
            "node": "SYNTHETIC/node.exe",
            "sdk_entry": "SYNTHETIC/index.js",
            "sdk_version": "1.2.0",
            "sha256": "a" * 64,
        }
        model = {"provider": "fixture", "id": "model"}
        presence = {
            "status": "finished",
            "generation": 1,
            "agents": [{"id": "pi", "status": "detected", "path": runtime["cli"]}],
        }
        entered, release = threading.Event(), threading.Event()

        def fake(runtime, action, cancel, **kwargs):
            if action == "profile_info":
                return {"model": model}
            entered.set()
            if not release.wait(8):
                raise AssertionError("Synthetic chat release missing")
            return {
                "model": model,
                "text": "<img src=x> SYNTHETIC reply",
                "history": [
                    {"role": "user", "content": kwargs["message"]},
                    {"role": "assistant", "content": "<img src=x> SYNTHETIC reply"},
                ],
                "outcome": "settled",
            }

        with (
            patch.object(discovery, "snapshot", return_value=presence),
            patch.object(chat_module, "resolve_chat_runtime", return_value=runtime),
            patch.object(chat_module, "call_chat", side_effect=fake) as call,
            patch.object(r, "apply") as apply,
        ):
            try:
                self.assertEqual(
                    self.request(
                        "POST",
                        "/api/pi/chat/prepare",
                        {"prepare_confirmed": True},
                        self.headers(),
                    )[0],
                    202,
                )
                if check.thread is not None:
                    check.thread.join(5)
                info = json.loads(
                    self.request("GET", "/api/pi/chat", headers=self.headers())[1]
                )
                call.assert_not_called()
                connect = {
                    "chat_id": info["chat_id"],
                    "runtime_sha256": info["runtime"]["sha256"],
                    "reuse_pi_confirmed": True,
                }
                self.assertEqual(
                    self.request(
                        "POST",
                        "/api/pi/chat/connect",
                        {**connect, "reuse_pi_confirmed": 1},
                        self.headers(),
                    )[0],
                    400,
                )
                self.assertEqual(
                    self.request(
                        "POST", "/api/pi/chat/connect", connect, self.headers()
                    )[0],
                    202,
                )
                if check.thread is not None:
                    check.thread.join(5)
                state = json.loads(
                    self.request("GET", "/api/pi/chat", headers=self.headers())[1]
                )
                self.assertEqual(call.call_count, 1)
                self.assertEqual(call.call_args.args[1], "profile_info")
                send = {
                    "session_id": state["session_id"],
                    "turn_id": "t-" + "a" * 16,
                    "message": "SYNTHETIC chat only",
                    "send_confirmed": True,
                }
                for payload in (
                    {**send, "send_confirmed": 1},
                    {**send, "session_id": "foreign"},
                    {**send, "history": []},
                    {**send, "command": "BLOCK"},
                ):
                    self.assertEqual(
                        self.request(
                            "POST", "/api/pi/chat/send", payload, self.headers()
                        )[0],
                        400,
                    )
                self.assertEqual(call.call_count, 1)
                self.assertEqual(
                    self.request("POST", "/api/pi/chat/send", send, self.headers())[0],
                    202,
                )
                self.assertTrue(entered.wait(2))
                self.assertEqual(self.request("GET", "/api/state")[0], 200)
                self.assertEqual(
                    self.request("POST", "/api/pi/chat/send", send, self.headers())[0],
                    400,
                )
                release.set()
                if check.thread is not None:
                    check.thread.join(5)
                done = json.loads(
                    self.request("GET", "/api/pi/chat", headers=self.headers())[1]
                )
                self.assertEqual(
                    done["turns"][-1]["text"], "<img src=x> SYNTHETIC reply"
                )
                self.assertFalse(done["tools_enabled"])
                self.assertFalse(done["document_shared"])
                self.assertEqual(call.call_count, 2)
                self.assertNotIn("workspace", call.call_args.kwargs)
                self.assertNotIn("plan", call.call_args.kwargs)
                self.assertEqual(
                    self.request(
                        "POST",
                        "/api/pi/chat/disconnect",
                        {
                            "session_id": state["session_id"],
                            "disconnect_confirmed": True,
                        },
                        self.headers(),
                    )[0],
                    202,
                )
                self.assertEqual(check.snapshot()["turns"], [])
                apply.assert_not_called()
            finally:
                release.set()
                if check.thread is not None:
                    check.thread.join(5)
        self.assertEqual(
            before,
            {
                str(item.relative_to(self.w)): r.file_hash(item)
                for item in self.w.rglob("*")
                if item.is_file()
            },
        )

    def test_hwp_context_preview_is_exact_private_and_stale_send_never_calls_model(
        self,
    ):
        check = getattr(self.server, "pi_chat_controller")
        discovery = getattr(self.server, "agent_discovery")
        runtime = {
            "cli": "SYNTHETIC/agent/bin/pi.cmd",
            "node": "SYNTHETIC/node.exe",
            "sdk_entry": "SYNTHETIC/index.js",
            "sdk_version": "1.2.0",
            "sha256": "a" * 64,
        }
        presence = {
            "status": "finished",
            "generation": 1,
            "agents": [{"id": "pi", "status": "detected", "path": runtime["cli"]}],
        }
        with (
            patch.object(discovery, "snapshot", return_value=presence),
            patch.object(chat_module, "resolve_chat_runtime", return_value=runtime),
            patch.object(
                chat_module,
                "call_chat",
                return_value={"model": {"provider": "fixture", "id": "model"}},
            ) as model,
        ):
            check.prepare()
            if check.thread is not None:
                check.thread.join(5)
            info = check.snapshot()
            check.connect(
                {
                    "chat_id": info["chat_id"],
                    "runtime_sha256": info["runtime"]["sha256"],
                    "reuse_pi_confirmed": True,
                }
            )
            if check.thread is not None:
                check.thread.join(5)
            model.reset_mock()
            sid = check.snapshot()["session_id"]
            manifest = r.verify_baseline(self.w)
            plan = r.read_json(self.w / "proposal-layout.json")
            anchor_id = plan["edits"][0]["anchor_id"]
            anchor = next(
                row for row in manifest["anchors"] if row["anchor_id"] == anchor_id
            )
            selection = {
                "workspace_id": manifest["workspace_id"],
                "baseline_hwpx_sha256": manifest["baseline_hwpx_sha256"],
                "comments_revision": r.file_hash(self.w / "comments.json"),
                "proposal_revision": r.file_hash(self.w / "proposal-layout.json"),
                "proposal_kind": "layout",
                "page": anchor["page_hint"],
                "anchor_id": anchor_id,
                "include_memos": False,
                "view_mode": "original",
                "prepare_context_confirmed": True,
            }
            body = {"session_id": sid, "selection": selection}
            self.assertEqual(
                self.request("POST", "/api/pi/chat/context", body, {})[0], 403
            )
            self.assertEqual(
                self.request(
                    "POST",
                    "/api/pi/chat/context",
                    {**body, "path": "BLOCK"},
                    self.headers(),
                )[0],
                400,
            )
            for change in (
                {"view_mode": "diff"},
                {"comments_revision": "stale"},
                {"anchor_id": "foreign"},
            ):
                self.assertEqual(
                    self.request(
                        "POST",
                        "/api/pi/chat/context",
                        {**body, "selection": {**selection, **change}},
                        self.headers(),
                    )[0],
                    400,
                )
            code, blob = self.request(
                "POST", "/api/pi/chat/context", body, self.headers()
            )
            preview = json.loads(blob)
            self.assertEqual(code, 202)
            self.assertEqual(
                preview["preview"]["review_context"]["selected_anchor"]["old_text"],
                anchor["old_text"],
            )
            self.assertNotIn(str(self.w), blob.decode())
            self.assertFalse(preview["preview"]["job_snapshot"]["live"])
            model.assert_not_called()
            exact = {
                "session_id": sid,
                "turn_id": "t-" + "a" * 16,
                "message": "SYNTHETIC review",
                "send_confirmed": True,
                "context_id": preview["context_id"],
                "context_sha256": preview["context_sha256"],
                "context_confirmed": True,
                "read_tools_confirmed": True,
            }
            self.assertEqual(
                self.request(
                    "POST",
                    "/api/pi/chat/send",
                    {**exact, "read_tools_confirmed": 1},
                    self.headers(),
                )[0],
                400,
            )
            model.assert_not_called()
            approval = (self.w / "approved-plan.json").read_bytes()
            r.write_json(
                self.w / "comments.json",
                {
                    "workspace_id": manifest["workspace_id"],
                    "comments": [
                        {
                            "id": "new",
                            "page": selection["page"],
                            "anchor_id": anchor_id,
                            "region": None,
                            "comment": "SYNTHETIC material changed",
                        }
                    ],
                },
            )
            self.assertEqual(
                self.request("POST", "/api/pi/chat/send", exact, self.headers())[0], 202
            )
            if check.thread is not None:
                check.thread.join(5)
            model.assert_not_called()
            self.assertFalse(check.snapshot()["document_shared"])
            self.assertEqual(check.snapshot()["last_turn"]["outcome"], "failed")
            self.assertEqual((self.w / "approved-plan.json").read_bytes(), approval)
            self.assertFalse((self.w / r.APPLY_LOCK).exists())

    def test_staged_artifact_review_is_readonly_and_live_approval_changes_block_job(
        self,
    ):
        check = getattr(self.server, "pi_chat_controller")
        discovery = getattr(self.server, "agent_discovery")
        runtime = {
            "cli": "SYNTHETIC/agent/bin/pi.cmd",
            "node": "SYNTHETIC/node.exe",
            "sdk_entry": "SYNTHETIC/index.js",
            "sdk_version": "1.2.0",
            "sha256": "a" * 64,
        }
        presence = {
            "status": "finished",
            "generation": 1,
            "agents": [{"id": "pi", "status": "detected", "path": runtime["cli"]}],
        }
        jobs = {
            "text_apply": {
                "enabled": True,
                "available": True,
                "active": False,
                "status": "idle",
            },
            "contract_apply": {
                "enabled": True,
                "available": True,
                "active": False,
                "status": "idle",
                "supported_kinds": ["layout"],
            },
            "apply_state": {"locked": False},
            "attempts": [],
        }

        def fake(runtime, action, cancel, **kwargs):
            if action == "profile_info":
                return {"model": {"provider": "fixture", "id": "model"}}
            scope = kwargs["context"]
            source = scope["review_context"]["selected_anchor"]
            artifact = (
                {
                    "kind": "text_draft",
                    "anchor_id": source["anchor_id"],
                    "old_text": source["old_text"],
                    "new_text": source["old_text"] + "X",
                }
                if scope["review_context"]["proposal_kind"] == "text"
                else {"kind": "approved_job_request", "job": scope["approved_job"]}
            )
            return {
                "text": "SYNTHETIC staged only",
                "history": [],
                "outcome": "settled",
                "artifacts": [artifact],
            }

        with (
            patch.object(discovery, "snapshot", return_value=presence),
            patch.object(chat_module, "resolve_chat_runtime", return_value=runtime),
            patch.object(chat_module, "call_chat", side_effect=fake) as model,
            patch.object(check.context_provider, "job_reader", return_value=jobs),
            patch.object(r, "apply") as apply,
        ):
            check.prepare()
            if check.thread is not None:
                check.thread.join(5)
            info = check.snapshot()
            check.connect(
                {
                    "chat_id": info["chat_id"],
                    "runtime_sha256": info["runtime"]["sha256"],
                    "reuse_pi_confirmed": True,
                }
            )
            if check.thread is not None:
                check.thread.join(5)
            sid = check.snapshot()["session_id"]
            manifest = r.verify_baseline(self.w)
            plan = r.read_json(self.w / "proposal-layout.json")
            anchor = next(
                row
                for row in manifest["anchors"]
                if row["anchor_id"] == plan["edits"][0]["anchor_id"]
            )
            for index, kind in enumerate(("text", "layout")):
                selection = {
                    "workspace_id": manifest["workspace_id"],
                    "baseline_hwpx_sha256": manifest["baseline_hwpx_sha256"],
                    "comments_revision": r.file_hash(self.w / "comments.json"),
                    "proposal_revision": "absent"
                    if kind == "text"
                    else r.file_hash(self.w / "proposal-layout.json"),
                    "proposal_kind": kind,
                    "page": anchor["page_hint"],
                    "anchor_id": anchor["anchor_id"],
                    "include_memos": False,
                    "view_mode": "original",
                    "prepare_context_confirmed": True,
                }
                preview = check.preview_context(
                    {"session_id": sid, "selection": selection}
                )
                before = {
                    str(item.relative_to(self.w)): r.file_hash(item)
                    for item in self.w.rglob("*")
                    if item.is_file()
                }
                body = {
                    "session_id": sid,
                    "turn_id": "t-" + str(index + 1) * 16,
                    "message": "SYNTHETIC stage",
                    "send_confirmed": True,
                    "context_id": preview["context_id"],
                    "context_sha256": preview["context_sha256"],
                    "context_confirmed": True,
                    "read_tools_confirmed": True,
                    "proposal_tools_confirmed": True,
                }
                self.assertEqual(
                    self.request("POST", "/api/pi/chat/send", body, self.headers())[0],
                    202,
                )
                if check.thread is not None:
                    check.thread.join(5)
                item = check.snapshot()["artifacts"][-1]
                payload = {
                    "session_id": sid,
                    "artifact_id": item["artifact_id"],
                    "review_confirmed": True,
                }
                calls = model.call_count
                self.assertEqual(
                    self.request("POST", "/api/pi/chat/artifact-review", payload, {})[
                        0
                    ],
                    403,
                )
                self.assertEqual(
                    self.request(
                        "POST",
                        "/api/pi/chat/artifact-review",
                        {**payload, "review_confirmed": 1},
                        self.headers(),
                    )[0],
                    400,
                )
                code, blob = self.request(
                    "POST", "/api/pi/chat/artifact-review", payload, self.headers()
                )
                reviewed = json.loads(blob)
                self.assertEqual(code, 202)
                self.assertFalse(reviewed["applied"])
                self.assertEqual(model.call_count, calls)
                apply.assert_not_called()
                self.assertEqual(
                    before,
                    {
                        str(item.relative_to(self.w)): r.file_hash(item)
                        for item in self.w.rglob("*")
                        if item.is_file()
                    },
                )
                if kind == "layout":
                    approval = r.read_json(self.w / "approved-plan.json")
                    approval["plan_sha256"] = "changed"
                    r.write_json(self.w / "approved-plan.json", approval)
                    self.assertEqual(
                        self.request(
                            "POST",
                            "/api/pi/chat/artifact-review",
                            payload,
                            self.headers(),
                        )[0],
                        400,
                    )
                    self.assertEqual(model.call_count, calls)
                    apply.assert_not_called()

    def test_public_partial_polling_stays_responsive_and_cancel_never_commits_it(self):
        check = self.server.pi_chat_controller
        discovery = self.server.agent_discovery
        runtime = {
            "cli": "SYNTHETIC/agent/bin/pi.cmd",
            "node": "SYNTHETIC/node.exe",
            "sdk_entry": "SYNTHETIC/index.js",
            "sdk_version": "1.2.0",
            "sha256": "a" * 64,
        }
        presence = {
            "status": "finished",
            "generation": 1,
            "agents": [{"id": "pi", "status": "detected", "path": runtime["cli"]}],
        }
        before = {
            str(p.relative_to(self.w)): r.file_hash(p)
            for p in self.w.rglob("*")
            if p.is_file()
        }
        entered, release = threading.Event(), threading.Event()

        def fake(runtime, action, cancel, **kwargs):
            if action == "profile_info":
                return {"model": {"provider": "fixture", "id": "model"}}
            update = {
                "stage": "responding",
                "text": "SYNTHETIC 한글 <img onerror=BAD>",
                "requests": 1,
                "request_limit": 1,
                "tool_activity": [],
                "usage": {
                    "input_tokens": 9,
                    "output_tokens": 3,
                    "cache_read_tokens": 0,
                    "cache_write_tokens": 0,
                    "total_tokens": 12,
                    "cost_usd": None,
                },
            }
            kwargs["on_progress"](update)
            entered.set()
            if not release.wait(5):
                raise AssertionError("Synthetic release missing")
            kwargs["on_progress"]({**update, "text": "LATE MUST NOT REPLACE PARTIAL"})
            return {"text": "late final", "history": [], "outcome": "settled"}

        with (
            patch.object(discovery, "snapshot", return_value=presence),
            patch.object(chat_module, "resolve_chat_runtime", return_value=runtime),
            patch.object(chat_module, "call_chat", side_effect=fake) as model,
            patch.object(r, "apply") as apply,
        ):
            check.prepare()
            if check.thread is not None:
                check.thread.join(3)
            info = check.snapshot()
            check.connect(
                {
                    "chat_id": info["chat_id"],
                    "runtime_sha256": info["runtime"]["sha256"],
                    "reuse_pi_confirmed": True,
                }
            )
            if check.thread is not None:
                check.thread.join(3)
            sid = check.snapshot()["session_id"]
            try:
                code, _ = self.request(
                    "POST",
                    "/api/pi/chat/send",
                    {
                        "session_id": sid,
                        "turn_id": "t-" + "a" * 16,
                        "message": "SYNTHETIC QUESTION",
                        "send_confirmed": True,
                    },
                    self.headers(),
                )
                self.assertEqual(code, 202)
                self.assertTrue(entered.wait(2))
                self.assertEqual(self.request("GET", "/api/pi/chat")[0], 403)
                code, blob = self.request("GET", "/api/pi/chat", headers=self.headers())
                self.assertEqual(code, 200)
                data = json.loads(blob)
                self.assertEqual(data["progress"]["state"], "sending")
                self.assertIn("한글", data["progress"]["text"])
                self.assertEqual(data["progress"]["usage"]["cost_usd"], None)
                self.assertEqual(data["artifacts"], [])
                self.assertEqual([t["role"] for t in data["turns"]], ["user"])
                self.assertEqual(self.request("GET", "/api/state")[0], 200)
                self.assertEqual(model.call_count, 2)
                self.assertEqual(
                    self.request(
                        "POST",
                        "/api/pi/chat/cancel",
                        {"session_id": sid, "cancel_confirmed": True},
                        self.headers(),
                    )[0],
                    202,
                )
            finally:
                release.set()
                if check.thread is not None:
                    check.thread.join(3)
            done = check.snapshot()
            self.assertEqual(done["progress"]["state"], "cancelled")
            self.assertNotIn("LATE", json.dumps(done))
            self.assertEqual(done["artifacts"], [])
            self.assertEqual(check.history, [])
            apply.assert_not_called()
            self.assertEqual(model.call_count, 2)
        self.assertEqual(
            before,
            {
                str(p.relative_to(self.w)): r.file_hash(p)
                for p in self.w.rglob("*")
                if p.is_file()
            },
        )

    def test_presence_is_independent_of_document_lock_and_does_not_read_baseline(self):
        result = {
            "agents": [],
            "scope": "SYNTHETIC",
            "ignored_entries": 0,
            "unreadable_candidates": 0,
        }
        with (
            patch.object(a, "scan_path", return_value=result),
            patch.object(
                r,
                "verify_baseline",
                side_effect=AssertionError("Presence must not read documents"),
            ),
            patch.object(r, "apply_state", return_value={"locked": True}),
            patch.object(
                self.server.text_apply_controller, "thread", Mock(is_alive=lambda: True)
            ),
            patch.object(r, "apply") as apply,
        ):
            self.assertEqual(
                self.request("POST", "/api/agents/discover", {}, self.headers())[0], 202
            )
            discovery = getattr(self.server, "agent_discovery")
            if discovery.thread is not None:
                discovery.thread.join(5)
            self.assertEqual(
                self.request("GET", "/api/agents", headers=self.headers())[0], 200
            )
            apply.assert_not_called()
        self.assertEqual(discovery.snapshot()["status"], "finished")


if __name__ == "__main__":
    unittest.main()
