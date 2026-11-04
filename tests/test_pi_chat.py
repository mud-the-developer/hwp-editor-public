"""M3 fake SDK/profile/controller tests. No real Pi profile or model request."""

import importlib
import json
from pathlib import Path
import secrets
from types import SimpleNamespace
import shutil
import subprocess
import sys
import tempfile
import threading
import unittest
from unittest.mock import Mock, patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
c = importlib.import_module("hwp_pi_chat")
p = importlib.import_module("hwp_pi_probe")


def frame(request, **changes):
    model = {"provider": "fixture", "id": "model"}
    data = {
        "type": "pi_chat_result",
        "id": request["id"],
        "nonce": request["nonce"],
        "success": True,
        "stage": "profile_ready",
        "model": model,
        "text": "",
        "messages": [],
        "outcome": "settled",
        "requests": 0,
        "process_attempts": 0,
        "tools_enabled": False,
        "document_shared": False,
        "session_persisted": False,
        "write_tools_enabled": False,
        "tool_activity": [],
        "proposal_tools_enabled": False,
        "artifacts": [],
        "usage": None,
    }
    data.update(changes)
    return json.dumps(data, ensure_ascii=False).encode() + b"\n"


class ChatTests(unittest.TestCase):
    def setUp(self):
        self.runtime = {
            "cli": "SYNTHETIC/agent/bin/pi.cmd",
            "node": "SYNTHETIC/node.exe",
            "sdk_entry": "SYNTHETIC/index.js",
            "sdk_version": "1.2.0",
            "sha256": "a" * 64,
            "rules_path": str(ROOT / "docs/hwp-agent-runtime.md"),
            "rules_sha256": p.file_digest(ROOT / "docs/hwp-agent-runtime.md"),
        }
        self.model = {"provider": "fixture", "id": "model"}
        self.presence = {
            "status": "finished",
            "generation": 1,
            "agents": [{"id": "pi", "status": "detected", "path": self.runtime["cli"]}],
        }
        self.discovery = Mock(snapshot=Mock(return_value=self.presence))
        self.chat = c.Chat(self.discovery)
        self.addCleanup(self.chat.close)
        deny = patch.object(
            subprocess,
            "Popen",
            side_effect=AssertionError("No real profile/model process"),
        )
        deny.start()
        self.addCleanup(deny.stop)

    def join(self):
        if self.chat.thread is not None:
            self.chat.thread.join(5)
            self.assertFalse(self.chat.thread.is_alive())

    def connect(self):
        with (
            patch.object(c, "resolve_chat_runtime", return_value=self.runtime),
            patch.object(c, "call_chat", return_value={"model": self.model}),
        ):
            self.chat.prepare()
            self.join()
            s = self.chat.snapshot()
            self.chat.connect(
                {
                    "chat_id": s["chat_id"],
                    "runtime_sha256": s["runtime"]["sha256"],
                    "reuse_pi_confirmed": True,
                }
            )
            self.join()
        return self.chat.snapshot()["session_id"]

    def test_prepare_and_profile_use_are_explicit_and_not_model_calls(self):
        self.assertEqual(self.chat.snapshot()["status"], "disconnected")
        with (
            patch.object(c, "resolve_chat_runtime", return_value=self.runtime),
            patch.object(c, "call_chat", return_value={"model": self.model}) as call,
        ):
            self.chat.prepare()
            self.join()
            call.assert_not_called()
            s = self.chat.snapshot()
            exact = {
                "chat_id": s["chat_id"],
                "runtime_sha256": s["runtime"]["sha256"],
                "reuse_pi_confirmed": True,
            }
            for bad in (
                {},
                {**exact, "reuse_pi_confirmed": 1},
                {**exact, "runtime_sha256": "wrong"},
                {**exact, "prompt": "BLOCK"},
            ):
                with self.assertRaises(ValueError):
                    self.chat.connect(bad)
            self.chat.connect(exact)
            self.join()
            self.assertEqual(call.call_args.args[1], "profile_info")
            self.assertEqual(self.chat.snapshot()["status"], "ready")
            self.assertFalse(self.chat.snapshot()["authentication_verified"])
        p.subprocess.Popen.assert_not_called()

    def test_send_is_bound_deduplicated_and_history_stays_internal(self):
        sid = self.connect()
        payload = {
            "session_id": sid,
            "turn_id": "t-" + "a" * 16,
            "message": "hello\nfixture",
            "send_confirmed": True,
        }
        entered, release = threading.Event(), threading.Event()
        canonical = [
            {"role": "user", "content": payload["message"]},
            {
                "role": "assistant",
                "content": [
                    {"type": "thinking", "thinking": "PRIVATE INTERNAL HISTORY"},
                    {"type": "text", "text": "<img onerror=BAD> reply"},
                ],
            },
        ]

        def fake(*args, **kwargs):
            entered.set()
            if not release.wait(5):
                raise AssertionError("Synthetic release missing")
            return {
                "model": self.model,
                "text": "<img onerror=BAD> reply",
                "history": canonical,
                "outcome": "settled",
            }

        with patch.object(c, "call_chat", side_effect=fake) as call:
            for bad in (
                {**payload, "send_confirmed": 1},
                {**payload, "session_id": "wrong"},
                {**payload, "history": []},
                {**payload, "message": "\0"},
            ):
                with self.assertRaises(ValueError):
                    self.chat.send(bad)
            self.chat.send(payload)
            self.assertTrue(entered.wait(2))
            try:
                with self.assertRaises(ValueError):
                    self.chat.send(payload)
            finally:
                release.set()
                self.join()
            self.assertEqual(call.call_args.kwargs["history"], [])
            self.assertEqual(
                self.chat.snapshot()["turns"][-1]["text"], "<img onerror=BAD> reply"
            )
            self.assertNotIn("PRIVATE INTERNAL", json.dumps(self.chat.snapshot()))
            with self.assertRaises(ValueError):
                self.chat.send(payload)
        with patch.object(
            c,
            "call_chat",
            return_value={"text": "next", "history": canonical, "outcome": "settled"},
        ) as call:
            self.chat.send({**payload, "turn_id": "t-" + "b" * 16})
            self.join()
            self.assertEqual(call.call_args.kwargs["history"], canonical)

    def test_completed_turn_retains_answer_and_usage_after_progress_clears(self):
        sid = self.connect()
        usage = {
            "input_tokens": 9,
            "output_tokens": 3,
            "cache_read_tokens": 0,
            "cache_write_tokens": 0,
            "total_tokens": 12,
            "cost_usd": 0.002,
        }
        payload = {
            "session_id": sid,
            "turn_id": "t-" + "c" * 16,
            "message": "FAKE ONLY",
            "send_confirmed": True,
        }
        with patch.object(
            c,
            "call_chat",
            return_value={
                "model": self.model,
                "text": "FAKE ANSWER",
                "history": [],
                "outcome": "settled",
                "usage": usage,
            },
        ) as call:
            self.chat.send(payload)
            self.join()
            snapshot = self.chat.snapshot()
            self.assertEqual(snapshot["status"], "ready")
            self.assertIsNone(snapshot["stage"])
            self.assertIsNone(snapshot["progress"])
            answer = snapshot["turns"][-1]
            self.assertEqual(
                (answer["role"], answer["text"], answer["usage"]),
                ("assistant", "FAKE ANSWER", usage),
            )
            self.assertEqual(snapshot["last_turn"]["outcome"], "settled")
            call.assert_called_once()
            self.assertEqual(call.call_args.kwargs["history"], [])
            self.chat.close()
            self.assertEqual(self.chat.snapshot()["turns"], [])
            self.assertEqual((answer["text"], answer["usage"]), ("FAKE ANSWER", usage))
        p.subprocess.Popen.assert_not_called()

    def test_failure_does_not_retry_or_disclose_raw_profile_provider_errors(self):
        sid = self.connect()
        with patch.object(
            c, "call_chat", side_effect=RuntimeError("PRIVATE KEY/URL DO NOT DISPLAY")
        ) as call:
            self.chat.send(
                {
                    "session_id": sid,
                    "turn_id": "t-" + "a" * 16,
                    "message": "hello",
                    "send_confirmed": True,
                }
            )
            self.join()
            call.assert_called_once()
            s = self.chat.snapshot()
            self.assertEqual(s["last_turn"]["outcome"], "failed")
            self.assertNotIn("PRIVATE KEY", json.dumps(s))

    def test_cancel_and_disconnect_ignore_late_results_without_touching_documents(self):
        sid = self.connect()
        entered, release = threading.Event(), threading.Event()

        def slow(*args, **kwargs):
            entered.set()
            release.wait(5)
            return {"text": "LATE", "history": [], "outcome": "settled"}

        with patch.object(c, "call_chat", side_effect=slow):
            self.chat.send(
                {
                    "session_id": sid,
                    "turn_id": "t-" + "a" * 16,
                    "message": "hello",
                    "send_confirmed": True,
                }
            )
            self.assertTrue(entered.wait(2))
            self.chat.cancel_turn({"session_id": sid, "cancel_confirmed": True})
            self.chat.disconnect({"session_id": sid, "disconnect_confirmed": True})
            release.set()
            self.join()
            self.assertEqual(self.chat.snapshot()["status"], "disconnected")
            self.assertEqual(self.chat.snapshot()["turns"], [])
        p.subprocess.Popen.assert_not_called()

    def test_memory_cap_and_unknown_client_history_never_trigger_model(self):
        sid = self.connect()
        self.chat.used_ids = {str(n) for n in range(40)}
        with patch.object(c, "call_chat") as call:
            with self.assertRaises(ValueError):
                self.chat.send(
                    {
                        "session_id": sid,
                        "turn_id": "t-" + "a" * 16,
                        "message": "hello",
                        "send_confirmed": True,
                    }
                )
            self.assertEqual(self.chat.snapshot()["status"], "full")
            call.assert_not_called()

    def test_staging_scope_uses_host_artifact_ids_and_review_rechecks_without_model(
        self,
    ):
        sid = self.connect()
        provider = Mock()
        snapshot = {
            "review_context": {
                "proposal_kind": "text",
                "selected_anchor": {"anchor_id": "selected", "old_text": "OLD"},
            },
            "job_snapshot": {"live": False},
            "bindings": {},
            "approved_job": None,
        }
        provider.capture.return_value = snapshot
        self.chat.context_provider = provider
        preview = self.chat.preview_context(
            {"session_id": sid, "selection": {"synthetic": True}}
        )
        payload = {
            "session_id": sid,
            "turn_id": "t-" + "a" * 16,
            "message": "draft",
            "send_confirmed": True,
            "context_id": preview["context_id"],
            "context_sha256": preview["context_sha256"],
            "context_confirmed": True,
            "read_tools_confirmed": True,
            "proposal_tools_confirmed": True,
        }
        raw = {
            "kind": "text_draft",
            "anchor_id": "selected",
            "old_text": "OLD",
            "new_text": "NEW",
        }
        with patch.object(
            c,
            "call_chat",
            return_value={
                "model": self.model,
                "text": "staged",
                "history": [],
                "outcome": "settled",
                "artifacts": [raw],
            },
        ) as model:
            with self.assertRaises(ValueError):
                self.chat.send({**payload, "proposal_tools_confirmed": 1})
            self.chat.send(payload)
            self.join()
            self.assertTrue(model.call_args.kwargs["proposal_tools"])
            model.assert_called_once()
            artifacts = self.chat.snapshot()["artifacts"]
            self.assertEqual(len(artifacts), 1)
            artifact_id = artifacts[0]["artifact_id"]
            self.assertRegex(artifact_id, r"^a-[a-f0-9]{32}$")
            self.assertNotIn("scope", artifacts[0])
            self.assertFalse(self.chat.snapshot()["write_tools_enabled"])
            review = {
                "session_id": sid,
                "artifact_id": artifact_id,
                "review_confirmed": True,
            }
            for bad in (
                {**review, "session_id": "foreign"},
                {**review, "artifact_id": "forged"},
                {**review, "review_confirmed": 1},
                {**review, "command": "BLOCK"},
            ):
                with self.assertRaises(ValueError):
                    self.chat.review_artifact(bad)
            provider.review_artifact.return_value = {
                "kind": "text_draft",
                "saved": False,
                "approved": False,
                "applied": False,
            }
            self.assertFalse(self.chat.review_artifact(review)["applied"])
            self.assertEqual(provider.review_artifact.call_args.args[2], raw)
            model.assert_called_once()
            provider.review_artifact.side_effect = ValueError("source/approval changed")
            with self.assertRaises(ValueError):
                self.chat.review_artifact(review)
            model.assert_called_once()
        self.chat.disconnect({"session_id": sid, "disconnect_confirmed": True})
        self.assertEqual(self.chat.snapshot()["artifacts"], [])

    def test_staging_codec_rejects_wrong_source_or_unconsented_artifacts(self):
        context = {
            "review_context": {
                "proposal_kind": "text",
                "selected_anchor": {"anchor_id": "selected", "old_text": "OLD"},
            },
            "job_snapshot": {"live": False},
            "bindings": {},
            "approved_job": None,
        }
        artifact = {
            "kind": "text_draft",
            "anchor_id": "selected",
            "old_text": "OLD",
            "new_text": "NEW",
        }

        def reply(runtime, cancel, request, **kwargs):
            return frame(
                request,
                stage="settled",
                requests=3,
                text="draft ready",
                messages=[
                    {"role": "user", "content": "draft"},
                    {"role": "assistant", "content": "draft ready"},
                ],
                tools_enabled=True,
                document_shared=True,
                proposal_tools_enabled=True,
                artifacts=[artifact],
                tool_activity=[
                    {"id": "c1", "name": "propose_text_edits", "status": "finished"}
                ],
            )

        with patch.object(c, "run_sdk_command", side_effect=reply):
            data = c.call_chat(
                self.runtime,
                "chat",
                threading.Event(),
                model=self.model,
                message="draft",
                context=context,
                proposal_tools=True,
            )
            self.assertEqual(data["artifacts"], [artifact])
            with self.assertRaises(p.ProbeFailure):
                c.call_chat(
                    self.runtime,
                    "chat",
                    threading.Event(),
                    model=self.model,
                    message="draft",
                    context=context,
                )
        for bad in (
            {**artifact, "anchor_id": "foreign"},
            {**artifact, "old_text": "wrong"},
            {**artifact, "new_text": "x\ny"},
            {**artifact, "command": "BLOCK"},
        ):

            def wrong(runtime, cancel, request, **kwargs):
                response = json.loads(reply(runtime, cancel, request, **kwargs))
                response["artifacts"] = [bad]
                return json.dumps(response).encode() + b"\n"

            with (
                patch.object(c, "run_sdk_command", side_effect=wrong),
                self.assertRaises(p.ProbeFailure),
            ):
                c.call_chat(
                    self.runtime,
                    "chat",
                    threading.Event(),
                    model=self.model,
                    message="draft",
                    context=context,
                    proposal_tools=True,
                )

    def test_context_preview_and_read_tools_require_exact_per_send_consent(self):
        sid = self.connect()
        provider = Mock()
        snapshot = {
            "review_context": {"selected_anchor": {"old_text": "SYNTHETIC SOURCE"}},
            "job_snapshot": {"live": False},
            "bindings": {},
        }
        provider.capture.return_value = snapshot
        self.chat.context_provider = provider
        preview = self.chat.preview_context(
            {"session_id": sid, "selection": {"SYNTHETIC": True}}
        )
        self.assertFalse(self.chat.snapshot()["document_shared"])
        exact = {
            "session_id": sid,
            "turn_id": "t-" + "a" * 16,
            "message": "review selected text",
            "send_confirmed": True,
            "context_id": preview["context_id"],
            "context_sha256": preview["context_sha256"],
            "context_confirmed": True,
            "read_tools_confirmed": True,
        }
        result = {
            "model": self.model,
            "text": "read only reply",
            "history": [],
            "outcome": "settled",
            "tool_activity": [
                {"id": "call-1", "name": "get_review_context", "status": "finished"}
            ],
        }
        with patch.object(c, "call_chat", return_value=result) as call:
            for change in (
                {"context_id": "foreign"},
                {"context_sha256": "wrong"},
                {"context_confirmed": 1},
                {"read_tools_confirmed": False},
            ):
                with self.assertRaises(ValueError):
                    self.chat.send({**exact, **change})
            call.assert_not_called()
            self.chat.send(exact)
            self.join()
            provider.verify.assert_called_once()
            self.assertEqual(call.call_args.kwargs["context"], snapshot)
            self.assertTrue(self.chat.snapshot()["document_shared"])
            self.assertTrue(self.chat.snapshot()["tools_enabled"])
            self.assertFalse(self.chat.snapshot()["write_tools_enabled"])
            self.assertNotIn("approved", json.dumps(self.chat.snapshot()))
        self.chat.disconnect({"session_id": sid, "disconnect_confirmed": True})
        self.assertFalse(self.chat.snapshot()["document_shared"])
        self.assertIsNone(self.chat.snapshot()["context_preview"])

    def test_changed_context_is_blocked_before_any_model_request(self):
        sid = self.connect()
        provider = Mock()
        provider.capture.return_value = {
            "review_context": {},
            "job_snapshot": {},
            "bindings": {},
        }
        provider.verify.side_effect = ValueError("CHANGED CONTEXT")
        self.chat.context_provider = provider
        preview = self.chat.preview_context({"session_id": sid, "selection": {}})
        payload = {
            "session_id": sid,
            "turn_id": "t-" + "a" * 16,
            "message": "review",
            "send_confirmed": True,
            "context_id": preview["context_id"],
            "context_sha256": preview["context_sha256"],
            "context_confirmed": True,
            "read_tools_confirmed": True,
        }
        with patch.object(c, "call_chat") as call:
            self.chat.send(payload)
            self.join()
            call.assert_not_called()
        self.assertFalse(self.chat.snapshot()["document_shared"])
        self.assertEqual(self.chat.snapshot()["last_turn"]["outcome"], "failed")

    def test_scoped_codec_accepts_only_known_read_results_and_exact_history(self):
        context = {"review_context": {}, "job_snapshot": {}, "bindings": {}}
        activity = [
            {"id": "call-1", "name": "get_review_context", "status": "finished"}
        ]
        messages = [
            {"role": "user", "content": "review"},
            {
                "role": "assistant",
                "content": [
                    {
                        "type": "toolCall",
                        "name": "get_review_context",
                        "id": "call-1",
                        "arguments": {},
                    }
                ],
            },
            {
                "role": "toolResult",
                "toolName": "get_review_context",
                "toolCallId": "call-1",
                "content": [{"type": "text", "text": "SYNTHETIC"}],
            },
            {"role": "assistant", "content": "reply"},
        ]

        def scoped(runtime, cancel, request, **kwargs):
            return frame(
                request,
                stage="settled",
                requests=2,
                text="reply",
                messages=messages,
                tools_enabled=True,
                document_shared=True,
                tool_activity=activity,
            )

        with patch.object(c, "run_sdk_command", side_effect=scoped):
            result = c.call_chat(
                self.runtime,
                "chat",
                threading.Event(),
                model=self.model,
                message="review",
                context=context,
            )
            self.assertEqual(result["tool_activity"], activity)
        for changes in (
            {"requests": 3},
            {"write_tools_enabled": True},
            {"tools_enabled": False},
            {"tool_activity": [{"id": "bad", "name": "bash", "status": "finished"}]},
        ):

            def bad(runtime, cancel, request, **kwargs):
                data = dict(
                    stage="settled",
                    requests=2,
                    text="reply",
                    messages=messages,
                    tools_enabled=True,
                    document_shared=True,
                    tool_activity=activity,
                )
                data.update(changes)
                return frame(request, **data)

            with (
                patch.object(c, "run_sdk_command", side_effect=bad),
                self.assertRaises(p.ProbeFailure),
            ):
                c.call_chat(
                    self.runtime,
                    "chat",
                    threading.Event(),
                    model=self.model,
                    message="review",
                    context=context,
                )

    def test_codec_rejects_unbound_or_unsafe_history_and_no_model_profile_info(self):
        def info(runtime, cancel, request, **kwargs):
            self.assertEqual(request["type"], "profile_info")
            self.assertNotIn("message", request)
            return frame(request)

        with patch.object(c, "run_sdk_command", side_effect=info):
            self.assertEqual(
                c.call_chat(self.runtime, "profile_info", threading.Event())["model"],
                self.model,
            )
        for change in (
            {"nonce": "wrong"},
            {"requests": 1},
            {"tools_enabled": True},
            {"document_shared": True},
            {"session_persisted": True},
            {"messages": [{"role": "assistant", "content": "BAD"}]},
        ):

            def bad(runtime, cancel, request, **kwargs):
                return frame(request, **change)

            with (
                patch.object(c, "run_sdk_command", side_effect=bad),
                self.assertRaises(p.ProbeFailure),
            ):
                c.call_chat(self.runtime, "profile_info", threading.Event())

        def good(runtime, cancel, request, **kwargs):
            self.assertEqual(request["message"], "hello")
            return frame(
                request,
                stage="settled",
                requests=1,
                text="reply",
                messages=[
                    {"role": "user", "content": "hello"},
                    {"role": "assistant", "content": "reply"},
                ],
            )

        with patch.object(c, "run_sdk_command", side_effect=good):
            self.assertEqual(
                c.call_chat(
                    self.runtime,
                    "chat",
                    threading.Event(),
                    model=self.model,
                    message="hello",
                )["text"],
                "reply",
            )


FAKE = """
export const VERSION='1.2.0';
export const SettingsManager={create:()=>({getDefaultProvider:()=> 'fixture',getDefaultModel:()=> 'model',getDefaultThinkingLevel:()=> 'off'}),inMemory:(s)=>{if(s.cacheWarming!=='off'||s.retry.enabled||s.retry.provider.maxRetries!==0||s.compaction.enabled)throw Error('Unexpected cost defaults');return s}};
export const ModelRuntime={create:async()=>{if(process.env.PROFILE_ONLY==='yes')throw Error('Profile info must not load auth');return {getPhysicalModel:(provider,id)=>({provider,id})}}};
export const SessionManager={inMemory:()=>{const messages=[];return {messages,appendMessage:m=>messages.push(m),getSessionFile:()=>undefined}}};
export const createExtensionRuntime=()=>({});
export async function createAgentSession(o){
 if(o.noTools!=='all'||o.tools.some(name=>!['get_review_context','get_job_status','propose_text_edits','request_approved_job'].includes(name))||o.customTools.length!==o.tools.length||!o.resourceLoader.getAppendSystemPrompt()[0].includes('HWP'))throw Error('Unexpected tools/rules');
 const s={messages:o.sessionManager.messages,isStreaming:false,agent:{streamFunction:(model,context,options)=>{if(options.maxTokens!==4096)throw Error('Unbounded output')}},getActiveToolNames:()=>o.tools,getLastAssistantText:()=>'<b>reply</b>',dispose:()=>{}};
 let listener=()=>{};s.subscribe=fn=>{listener=fn;return ()=>{listener=()=>{}}};
 s.prompt=async(message)=>{
  s.agent.streamFunction(null,null,{maxTokens:999999});s.messages.push({role:'user',content:message});
  const action=o.tools.includes('propose_text_edits');
  const groups=action?[[o.customTools[0]],[o.customTools.find(t=>t.name===(message==='job'?'request_approved_job':'propose_text_edits'))]]:o.tools.length?[o.customTools]:[];
  let index=0;
  for(const group of groups){
   s.messages.push({role:'assistant',content:group.map(tool=>({type:'toolCall',id:'call-'+index,name:tool.name,arguments:tool.name==='propose_text_edits'?{new_text:'NEW'}:{}})),stopReason:'toolUse'});
   for(const tool of group){
    if(tool.parameters.additionalProperties!==false)throw Error('Open args');
    const params=message==='bad-args'?{path:'DO NOT READ'}:tool.name==='propose_text_edits'?{new_text:'NEW'}:{};
    const result=await tool.execute('call-'+index,params);if(result.details.write_authority!=='none')throw Error('Write authority');
    s.messages.push({role:'toolResult',toolName:tool.name,toolCallId:'call-'+index,content:result.content,isError:false});index++;
   }
   s.agent.streamFunction(null,null,{});
  }
  listener({type:'message_start',message:{role:'assistant'}});
  listener({type:'message_update',assistantMessageEvent:{type:'thinking_delta',delta:'PRIVATE REASONING NEVER DISPLAY'}});
  listener({type:'tool_execution_update',args:{private:'PRIVATE TOOL ARGS'}});
  listener({type:'message_update',assistantMessageEvent:{type:'text_delta',delta:'<b>reply</b>'}});
  listener({type:'message_end',message:{role:'assistant',usage:{input:9,output:3,cacheRead:0,cacheWrite:0,totalTokens:12,cost:{total:0.002}}}});
  s.messages.push({role:'assistant',content:'<b>reply</b>',stopReason:'stop'});if(message==='double')s.agent.streamFunction(null,null,{});
 };
 return {session:s}
}
"""


class ChatHelperTests(unittest.TestCase):
    def test_owned_node_fake_sdk_profile_chat_and_hidden_second_request(self):
        self._owned_node_case("1.2.0")

    def test_owned_node_fake_13_sdk_system_history_and_same_consent_boundaries(self):
        self._owned_node_case("1.3.0")

    def test_owned_node_fake_14_sdk_keeps_profile_cost_tools_and_history_boundaries(self):
        self._owned_node_case("1.4.0")

    def _owned_node_case(self, version):
        node = shutil.which("node")
        if node is None:
            self.skipTest("No Node; no automatic install")
        with tempfile.TemporaryDirectory(prefix="HWP-FAKE-Pi-Chat-") as temp:
            root = Path(temp)
            profile = root / "profile"
            sdk = (
                profile
                / f"install/releases/{version}/node_modules/@earendil-works/pi-coding-agent/dist/index.js"
            )
            sdk.parent.mkdir(parents=True)
            code = FAKE.replace("VERSION='1.2.0'", f"VERSION='{version}'")
            if version in ("1.3.0", "1.4.0"):
                code = code.replace(
                    "s.messages.push({role:'user'",
                    "s.messages.push({role:'system',content:'SYNTHETIC SDK SYSTEM MUST BE REBUILT'});s.messages.push({role:'user'",
                )
                code = code.replace(
                    "appendMessage:m=>messages.push(m)",
                    "appendMessage:m=>{if(m.role==='system')throw Error('SDK system history must not be replayed');messages.push(m)}",
                )
                code = code.replace(
                    "stopReason:'stop'",
                    "stopReason:'stop',opaqueSignature:'SYNTHETIC PRESERVE'",
                )
            sdk.write_text(code, encoding="utf-8")
            (sdk.parent.parent / "package.json").write_text(
                '{"type":"module"}', encoding="utf-8"
            )
            base = {
                "type": "profile_info",
                "id": "p-" + secrets.token_hex(8),
                "nonce": secrets.token_hex(16),
                "profile_dir": str(profile),
            }
            typebox = sdk.parent.parent / "node_modules/typebox"
            typebox.mkdir(parents=True)
            (typebox / "package.json").write_text(
                '{"type":"module","main":"index.js"}', encoding="utf-8"
            )
            (typebox / "index.js").write_text(
                'export const Type={Object:(properties,options)=>({type:"object",properties,...options}),String:options=>({type:"string",...options})};',
                encoding="utf-8",
            )
            env = p.child_environment(root, node)
            env["PROFILE_ONLY"] = "yes"

            def run(request, environment):
                result = subprocess.run(
                    [node, str(ROOT / "scripts/pi_chat.mjs"), str(sdk)],
                    input=json.dumps(request).encode() + b"\n",
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                    cwd=root,
                    env=environment,
                    timeout=30,
                )
                lines = result.stdout.splitlines()
                self.assertTrue(lines, result.stderr.decode(errors="replace"))
                return SimpleNamespace(
                    returncode=result.returncode,
                    stdout=lines[-1],
                    stderr=result.stderr,
                    events=[json.loads(line) for line in lines[:-1]],
                    raw_stdout=result.stdout,
                )

            info = run(base, env)
            data = json.loads(info.stdout)
            self.assertEqual(info.returncode, 0)
            self.assertTrue(data["success"])
            self.assertEqual(data["requests"], 0)
            env.pop("PROFILE_ONLY")
            request = {
                **base,
                "type": "chat",
                "model": {"provider": "fixture", "id": "model"},
                "history": [],
                "message": "hello",
                "rules": "HWP runtime trusted test rules",
                "context": None,
                "document_history": False,
                "proposal_tools": False,
            }
            streamed = run(request, env)
            data = json.loads(streamed.stdout)
            self.assertTrue(data["success"])
            self.assertEqual(
                [event["seq"] for event in streamed.events],
                list(range(1, len(streamed.events) + 1)),
            )
            self.assertNotIn(b"PRIVATE REASONING", streamed.raw_stdout)
            self.assertNotIn(b"PRIVATE TOOL ARGS", streamed.raw_stdout)
            self.assertTrue(
                any(event["event"] == "text_delta" for event in streamed.events)
            )
            self.assertEqual(data["usage"]["total_tokens"], 12)
            self.assertEqual(data["usage"]["cost_usd"], 0.002)
            self.assertEqual(data["requests"], 1)
            self.assertEqual(data["text"], "<b>reply</b>")
            self.assertEqual(
                [message["role"] for message in data["messages"]], ["user", "assistant"]
            )
            self.assertNotIn(b"SYNTHETIC SDK SYSTEM", streamed.raw_stdout)
            if version in ("1.3.0", "1.4.0"):
                self.assertEqual(
                    data["messages"][-1]["opaqueSignature"], "SYNTHETIC PRESERVE"
                )
            context = {
                "review_context": {
                    "selected_anchor": {"old_text": "TEST ONLY DO NOT EXECUTE"}
                },
                "job_snapshot": {
                    "live": False,
                    "captured_at": "SYNTHETIC",
                    "request_can_execute": False,
                },
                "bindings": {},
            }
            scoped = {**request, "context": context}
            data = json.loads(run(scoped, env).stdout)
            self.assertTrue(data["success"])
            self.assertEqual(data["requests"], 2)
            self.assertTrue(data["tools_enabled"])
            self.assertFalse(data["write_tools_enabled"])
            self.assertTrue(data["document_shared"])
            self.assertEqual(len(data["tool_activity"]), 4)
            self.assertEqual(
                [
                    event["name"]
                    for event in data["tool_activity"]
                    if event["status"] == "finished"
                ],
                ["get_review_context", "get_job_status"],
            )
            self.assertIn("TEST ONLY DO NOT EXECUTE", json.dumps(data["messages"]))
            continued = json.loads(
                run(
                    {**request, "history": data["messages"], "document_history": True},
                    env,
                ).stdout
            )
            self.assertTrue(continued["success"])
            self.assertEqual(continued["requests"], 1)
            self.assertFalse(continued["tools_enabled"])
            self.assertTrue(continued["document_shared"])
            self.assertEqual(continued["tool_activity"], [])
            self.assertTrue(
                any(message["role"] == "toolResult" for message in data["messages"])
            )
            self.assertFalse(
                any(message["role"] == "system" for message in continued["messages"])
            )
            self.assertEqual(
                continued["messages"][: len(data["messages"])], data["messages"]
            )
            full = json.loads(
                run(
                    {
                        **request,
                        "history": [
                            {"role": "user", "content": "SYNTHETIC HISTORY"}
                            for _ in range(100)
                        ],
                    },
                    env,
                ).stdout
            )
            self.assertTrue(full["success"])
            self.assertEqual(full["outcome"], "history_full")
            self.assertEqual(full["messages"], [])
            self.assertEqual(full["text"], "<b>reply</b>")
            blocked = json.loads(run({**scoped, "message": "bad-args"}, env).stdout)
            self.assertFalse(blocked["success"])
            limited = json.loads(run({**scoped, "message": "double"}, env).stdout)
            self.assertFalse(limited["success"])
            self.assertEqual(limited["requests"], 3)
            draft_context = {
                "review_context": {
                    "proposal_kind": "text",
                    "selected_anchor": {
                        "anchor_id": "fixture-anchor",
                        "old_text": "OLD",
                    },
                },
                "job_snapshot": {"live": False},
                "bindings": {},
                "approved_job": None,
            }
            action = {
                **request,
                "context": draft_context,
                "proposal_tools": True,
                "message": "draft",
            }
            staged = json.loads(run(action, env).stdout)
            self.assertTrue(staged["success"])
            self.assertEqual(staged["requests"], 3)
            self.assertTrue(staged["proposal_tools_enabled"])
            self.assertFalse(staged["write_tools_enabled"])
            self.assertEqual(
                staged["artifacts"],
                [
                    {
                        "kind": "text_draft",
                        "anchor_id": "fixture-anchor",
                        "old_text": "OLD",
                        "new_text": "NEW",
                    }
                ],
            )
            job = {
                "workspace_id": "fixture-workspace",
                "baseline_hwpx_sha256": "a" * 64,
                "proposal_kind": "text",
                "proposal_revision": "revision",
                "plan_sha256": "b" * 64,
                "approval_scope_sha256": None,
                "requires_separate_apply_confirmation": True,
            }
            requested = json.loads(
                run(
                    {
                        **action,
                        "message": "job",
                        "context": {**draft_context, "approved_job": job},
                    },
                    env,
                ).stdout
            )
            self.assertTrue(requested["success"])
            self.assertEqual(requested["requests"], 3)
            self.assertEqual(
                requested["artifacts"], [{"kind": "approved_job_request", "job": job}]
            )
            unapproved = json.loads(run({**action, "message": "job"}, env).stdout)
            self.assertTrue(unapproved["success"])
            self.assertEqual(unapproved["artifacts"], [])
            unauthorized = json.loads(run({**action, "context": None}, env).stdout)
            self.assertFalse(unauthorized["success"])
            over = json.loads(run({**action, "message": "double"}, env).stdout)
            self.assertFalse(over["success"])
            self.assertEqual(over["artifacts"], [])
            bad = json.loads(run({**request, "message": "double"}, env).stdout)
            self.assertFalse(bad["success"])
            self.assertEqual(bad["requests"], 2)
            wrong = json.loads(
                run(
                    {**request, "model": {"provider": "different", "id": "model"}}, env
                ).stdout
            )
            self.assertFalse(wrong["success"])
            self.assertEqual(wrong["stage"], "profile_changed")
            self.assertEqual(wrong["requests"], 0)
            incoming_system = json.loads(
                run(
                    {
                        **request,
                        "history": [
                            {
                                "role": "system",
                                "content": "DO NOT ACCEPT CALLER AUTHORITY",
                            }
                        ],
                    },
                    env,
                ).stdout
            )
            self.assertFalse(incoming_system["success"])
            self.assertEqual(incoming_system["stage"], "request")
            self.assertEqual(incoming_system["requests"], 0)
            other_version = "1.3.0" if version == "1.2.0" else "1.2.0"
            sdk.write_text(
                code.replace(f"VERSION='{version}'", f"VERSION='{other_version}'"),
                encoding="utf-8",
            )
            mismatch = json.loads(run(base, env).stdout)
            self.assertFalse(mismatch["success"])
            self.assertEqual(mismatch["stage"], "profile")
            self.assertEqual(mismatch["requests"], 0)
            sdk = (
                profile
                / "install/releases/1.3.1/node_modules/@earendil-works/pi-coding-agent/dist/index.js"
            )
            sdk.parent.mkdir(parents=True)
            sdk.write_text(
                "throw Error('Unknown release MUST NOT IMPORT')", encoding="utf-8"
            )
            unknown = json.loads(run(base, env).stdout)
            self.assertFalse(unknown["success"])
            self.assertEqual(unknown["stage"], "request")
            self.assertEqual(unknown["requests"], 0)


if __name__ == "__main__":
    unittest.main()
