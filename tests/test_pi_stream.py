"""Synthetic JSONL/pipe/controller streaming tests; no real Pi/auth/model/HWP."""

from copy import deepcopy
import importlib
import json
from pathlib import Path
import subprocess
import sys
import threading
import unittest
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
c = importlib.import_module("hwp_pi_chat")
p = importlib.import_module("hwp_pi_probe")
fixture = importlib.import_module("test_pi_chat")
process_fixture = importlib.import_module("test_pi_probe")
REQUEST = {
    "type": "chat",
    "id": "p-" + "a" * 16,
    "nonce": "b" * 32,
    "context": None,
    "proposal_tools": False,
}
USAGE = {
    "input_tokens": 9,
    "output_tokens": 3,
    "cache_read_tokens": 0,
    "cache_write_tokens": 0,
    "total_tokens": 12,
    "cost_usd": 0.002,
}


def event(seq, kind, data, **changes):
    frame = {
        "type": "pi_chat_event",
        "id": REQUEST["id"],
        "nonce": REQUEST["nonce"],
        "seq": seq,
        "event": kind,
        "data": data,
    }
    frame.update(changes)
    return json.dumps(frame, ensure_ascii=False).encode() + b"\n"


def phase(count=1, limit=1):
    return {"stage": "model_request", "requests": count, "request_limit": limit}


class StreamCodecTests(unittest.TestCase):
    def test_fragmented_utf8_crlf_and_unicode_separators_are_text_not_frames(self):
        seen = []
        stream = c.ChatStream(REQUEST, seen.append)
        text = "한글\n<svg onload=BAD>\u2028\u2029🙂"
        blob = (
            event(1, "phase", phase())
            + event(2, "assistant_start", {})
            + event(3, "text_delta", {"delta": text})
            + event(4, "usage", USAGE)
            + fixture.frame(REQUEST)
        ).replace(b"\n", b"\r\n")
        for byte in blob:
            stream.feed(bytes([byte]))
        result = stream.finish(blob)
        self.assertEqual(result["type"], "pi_chat_result")
        self.assertEqual(seen[-1]["text"], text)
        self.assertEqual(seen[-1]["usage"], USAGE)
        self.assertEqual(stream.sequence, 4)

    def test_foreign_out_of_order_private_or_extra_payload_is_not_forwarded(self):
        invalid = [
            event(2, "phase", phase()),
            event(True, "phase", phase()),
            event(1, "phase", phase(), nonce="foreign"),
            event(1, "phase", phase(), id="foreign"),
            event(1, "phase", {**phase(), "path": "PRIVATE"}),
            event(1, "phase", phase(limit=3)),
            event(1, "thinking", {"text": "PRIVATE REASONING"}),
            event(1, "text_delta", {"delta": "NO ASSISTANT START"}),
            event(1, "assistant_start", {"reasoning": "PRIVATE"}),
            event(1, "tool", {"id": "call", "name": "bash", "status": "started"}),
        ]
        for blob in invalid:
            seen = []
            with self.subTest(blob=blob[:70]), self.assertRaises(p.ProbeFailure):
                c.ChatStream(REQUEST, seen.append).feed(blob)
            self.assertEqual(seen, [])

    def test_duplicate_keys_nan_and_unfinished_or_post_final_data_fail_closed(self):
        broken = [
            b'{"id":"x","id":"y"}\n',
            b'{"value":NaN}\n',
            fixture.frame(REQUEST).rstrip(b"\n"),
            fixture.frame(REQUEST) + event(1, "phase", phase()),
            fixture.frame(REQUEST) + fixture.frame(REQUEST),
            b"\n",
        ]
        for blob in broken:
            with self.subTest(blob=blob[:50]), self.assertRaises(p.ProbeFailure):
                c.ChatStream(REQUEST).finish(blob)

    def test_reader_callbacks_cannot_be_mixed_with_a_different_returned_blob(self):
        stream = c.ChatStream(REQUEST)
        blob = fixture.frame(REQUEST)
        stream.feed(blob)
        with self.assertRaises(p.ProbeFailure):
            stream.finish(blob + b" ")
        with self.assertRaises(p.ProbeFailure):
            stream.finish(blob.replace(b"profile_ready", b"profile_wrong"))

    def test_phase_limits_text_event_and_total_size_caps_are_independent(self):
        stream = c.ChatStream(REQUEST)
        stream.feed(event(1, "phase", phase()))
        with self.assertRaises(p.ProbeFailure):
            stream.feed(event(2, "phase", phase(count=0)))
        stream = c.ChatStream(REQUEST)
        stream.feed(event(1, "assistant_start", {}))
        with self.assertRaises(p.ProbeFailure):
            for n in range(2, 69):
                stream.feed(event(n, "text_delta", {"delta": "x" * 4096}))
        stream = c.ChatStream(REQUEST)
        with self.assertRaises(p.ProbeFailure):
            for n in range(1, 4098):
                stream.feed(event(n, "phase", phase(count=0)))
        with self.assertRaises(p.ProbeFailure):
            c.ChatStream(REQUEST).feed(b"x" * 2097153)

    def test_assistant_reset_and_scoped_tool_lifecycle_do_not_accumulate_preambles(
        self,
    ):
        request = {**REQUEST, "context": {"SYNTHETIC": True}}
        seen = []
        stream = c.ChatStream(request, seen.append)
        chunks = [
            event(1, "phase", phase(limit=2)),
            event(2, "assistant_start", {}),
            event(3, "text_delta", {"delta": "checking"}),
            event(
                4,
                "tool",
                {"id": "c1", "name": "get_review_context", "status": "started"},
            ),
            event(
                5,
                "tool",
                {"id": "c1", "name": "get_review_context", "status": "finished"},
            ),
            event(6, "phase", phase(count=2, limit=2)),
            event(7, "assistant_start", {}),
            event(8, "text_delta", {"delta": "final public answer"}),
        ]
        for blob in chunks:
            stream.feed(blob)
        self.assertEqual(seen[-1]["text"], "final public answer")
        self.assertEqual(len(seen[-1]["tool_activity"]), 2)
        with self.assertRaises(p.ProbeFailure):
            stream.feed(
                event(
                    9,
                    "tool",
                    {"id": "c1", "name": "get_review_context", "status": "finished"},
                )
            )

    def test_missing_cost_is_unknown_not_free_and_usage_is_bounded(self):
        self.assertEqual(c.checked_usage({**USAGE, "cost_usd": None})["cost_usd"], None)
        for change in (
            {"input_tokens": True},
            {"output_tokens": -1},
            {"total_tokens": 10**9 + 1},
            {"cost_usd": float("nan")},
            {"cost_usd": -1},
            {"cost_usd": 10**500},
            {"path": "PRIVATE"},
        ):
            with self.subTest(change=change), self.assertRaises(p.ProbeFailure):
                c.checked_usage({**USAGE, **change})

    def test_profile_info_may_not_stream_and_canonical_result_replaces_partial(self):
        with self.assertRaises(p.ProbeFailure):
            c.ChatStream({**REQUEST, "type": "profile_info"}).feed(
                event(1, "phase", phase())
            )
        runtime = {
            "cli": "SYNTHETIC",
            "rules_path": str(fixture.ROOT / "docs/hwp-agent-runtime.md"),
            "rules_sha256": p.file_digest(fixture.ROOT / "docs/hwp-agent-runtime.md"),
        }
        seen = []

        def runner(runtime, cancel, request, **kwargs):
            def bound(blob):
                data = json.loads(blob)
                data.update(id=request["id"], nonce=request["nonce"])
                return json.dumps(data, ensure_ascii=False).encode() + b"\n"

            blob = b"".join(
                bound(b)
                for b in (
                    event(1, "phase", phase()),
                    event(2, "assistant_start", {}),
                    event(3, "text_delta", {"delta": "unverified preview"}),
                )
            )
            blob += fixture.frame(
                request,
                stage="settled",
                requests=1,
                text="verified answer",
                messages=[
                    {"role": "user", "content": "QUESTION"},
                    {"role": "assistant", "content": "verified answer"},
                ],
                usage=USAGE,
            )
            for n in range(0, len(blob), 11):
                kwargs["on_stdout"](blob[n : n + 11])
            return blob

        with patch.object(c, "run_sdk_command", side_effect=runner):
            result = c.call_chat(
                runtime,
                "chat",
                threading.Event(),
                model={"provider": "fixture", "id": "model"},
                message="QUESTION",
                on_progress=seen.append,
            )
        self.assertEqual(seen[-1]["text"], "unverified preview")
        self.assertEqual(result["text"], "verified answer")
        self.assertEqual(result["usage"], USAGE)


class PipeStreamTests(unittest.TestCase):
    def runtime(self):
        return {
            "cli": "SYNTHETIC",
            "node": "SYNTHETIC/node.exe",
            "helper": "SYNTHETIC/helper.mjs",
            "sdk_entry": "SYNTHETIC/index.js",
            "sha256": "a" * 64,
        }

    def test_small_pipe_bytes_arrive_before_process_exit_and_cancel_owns_one_process(
        self,
    ):
        process = process_fixture.FakeProcess(
            transform=lambda _: b"PUBLIC PARTIAL\n", hang=True
        )
        seen = threading.Event()
        cancel = threading.Event()
        errors = []

        def run():
            try:
                p.run_sdk_command(
                    self.runtime(),
                    cancel,
                    REQUEST,
                    helper_key="helper",
                    timeout=10,
                    output_limit=1024,
                    on_stdout=lambda blob: seen.set(),
                )
            except p.ProbeFailure as error:
                errors.append(error.stage)

        with (
            patch.object(p, "resolve_runtime", return_value={"sha256": "a" * 64}),
            patch.object(p.subprocess, "Popen", return_value=process) as spawn,
        ):
            worker = threading.Thread(target=run)
            worker.start()
            try:
                self.assertTrue(seen.wait(2))
                self.assertIsNone(process.poll())
                self.assertTrue(worker.is_alive())
            finally:
                cancel.set()
                worker.join(3)
            self.assertFalse(worker.is_alive())
            spawn.assert_called_once()
            self.assertTrue(process.terminated)
            self.assertEqual(errors, ["cancelled"])

    def test_stderr_is_never_streamed_to_public_callback(self):
        import io

        process = process_fixture.FakeProcess(transform=lambda _: b"PUBLIC\n")
        process.stderr = io.BytesIO(b"PRIVATE PROVIDER ERROR OR KEY")
        seen = []
        with (
            patch.object(p, "resolve_runtime", return_value={"sha256": "a" * 64}),
            patch.object(p.subprocess, "Popen", return_value=process),
        ):
            result = p.run_sdk_command(
                self.runtime(),
                threading.Event(),
                REQUEST,
                helper_key="helper",
                timeout=5,
                output_limit=1024,
                on_stdout=seen.append,
            )
        self.assertEqual(result, b"PUBLIC\n")
        self.assertEqual(b"".join(seen), b"PUBLIC\n")

    def test_callback_failure_or_nonzero_exit_never_becomes_valid_receipt(self):
        for code, callback, stage in (
            (
                0,
                Mock(side_effect=ValueError("PRIVATE BAD FRAME")),
                "chat_stream_protocol",
            ),
            (1, Mock(), "process_exit"),
        ):
            process = process_fixture.FakeProcess(
                transform=lambda _: b"partial\n", code=code
            )
            with (
                patch.object(p, "resolve_runtime", return_value={"sha256": "a" * 64}),
                patch.object(p.subprocess, "Popen", return_value=process),
                self.assertRaises(p.ProbeFailure) as error,
            ):
                p.run_sdk_command(
                    self.runtime(),
                    threading.Event(),
                    REQUEST,
                    helper_key="helper",
                    timeout=5,
                    output_limit=1024,
                    on_stdout=callback,
                )
            self.assertEqual(error.exception.stage, stage)
            callback.assert_called_once()


class StreamControllerTests(unittest.TestCase):
    def setUp(self):
        self.chat = c.Chat(Mock())
        self.chat.status = "ready"
        self.chat.runtime = {
            "cli": "SYNTHETIC/pi.cmd",
            "node": "SYNTHETIC/node.exe",
            "sdk_entry": "SYNTHETIC/index.js",
            "sdk_version": "1.2.0",
            "sha256": "a" * 64,
        }
        self.chat.model = {"provider": "fixture", "id": "model"}
        self.chat.session_id = "e" * 32
        self.addCleanup(self.chat.close)
        denied = patch.object(
            subprocess,
            "Popen",
            side_effect=AssertionError("No real process/model allowed"),
        )
        denied.start()
        self.addCleanup(denied.stop)
        self.payload = {
            "session_id": self.chat.session_id,
            "turn_id": "t-" + "a" * 16,
            "message": "QUESTION",
            "send_confirmed": True,
        }
        self.progress = {
            "stage": "responding",
            "text": "한글 <img onerror=BAD>",
            "requests": 1,
            "request_limit": 1,
            "tool_activity": [],
            "usage": deepcopy(USAGE),
        }

    def join(self):
        worker = self.chat.thread
        if worker is not None:
            worker.join(3)
            self.assertFalse(worker.is_alive())

    def test_thread_start_failure_is_not_displayed_as_active_and_can_close(self):
        with (
            patch.object(
                threading.Thread,
                "start",
                side_effect=RuntimeError("SYNTHETIC START FAILURE"),
            ),
            patch.object(c, "call_chat") as model,
        ):
            self.chat.send(self.payload)
            state = self.chat.snapshot()
            self.assertEqual(state["status"], "failed")
            self.assertEqual(state["stage"], "thread_start")
            self.assertEqual(state["last_turn"]["outcome"], "failed")
            self.assertEqual(state["progress"]["state"], "failed")
            self.assertIsNone(self.chat.started_at)
            model.assert_not_called()
        self.chat.close()
        self.assertIsNone(self.chat.snapshot()["progress"])

    def test_stream_is_provisional_until_receipt_and_partial_usage_is_not_history(self):
        entered = threading.Event()
        release = threading.Event()

        def fake(*args, **kwargs):
            kwargs["on_progress"](self.progress)
            entered.set()
            if not release.wait(3):
                raise AssertionError("Synthetic release missing")
            return {
                "text": "final checked reply",
                "history": [],
                "outcome": "settled",
                "usage": USAGE,
            }

        with patch.object(c, "call_chat", side_effect=fake) as model:
            self.chat.send(self.payload)
            self.assertTrue(entered.wait(2))
            try:
                state = self.chat.snapshot()
                self.assertEqual(state["progress"]["text"], self.progress["text"])
                self.assertEqual(state["turns"][-1]["role"], "user")
                self.assertEqual(state["artifacts"], [])
                self.assertEqual(self.chat.history, [])
            finally:
                release.set()
                self.join()
            state = self.chat.snapshot()
            self.assertIsNone(state["progress"])
            self.assertEqual(state["turns"][-1]["text"], "final checked reply")
            self.assertEqual(state["turns"][-1]["usage"], USAGE)
            model.assert_called_once()

    def test_failed_process_keeps_marked_partial_but_commits_no_history_or_artifacts(
        self,
    ):
        def fake(*args, **kwargs):
            kwargs["on_progress"](self.progress)
            raise p.ProbeFailure("process_exit")

        with patch.object(c, "call_chat", side_effect=fake) as model:
            self.chat.send(self.payload)
            self.join()
            model.assert_called_once()
        state = self.chat.snapshot()
        self.assertEqual(state["progress"]["state"], "failed")
        self.assertEqual(state["progress"]["text"], self.progress["text"])
        self.assertEqual(state["artifacts"], [])
        self.assertEqual(self.chat.history, [])
        self.assertEqual([t["role"] for t in state["turns"]], ["user"])

    def test_cancel_and_disconnect_do_not_accept_late_stream_callbacks(self):
        for disconnect in (False, True):
            entered = threading.Event()
            release = threading.Event()
            self.chat.status = "ready"

            def fake(*args, **kwargs):
                kwargs["on_progress"](self.progress)
                entered.set()
                release.wait(3)
                kwargs["on_progress"](
                    {**self.progress, "text": "LATE SHOULD NOT APPEAR"}
                )
                return {"text": "late final", "history": [], "outcome": "settled"}

            with patch.object(c, "call_chat", side_effect=fake):
                self.chat.send(
                    {
                        **self.payload,
                        "turn_id": "t-" + ("b" if disconnect else "c") * 16,
                    }
                )
                self.assertTrue(entered.wait(2))
                if disconnect:
                    self.chat.disconnect(
                        {
                            "session_id": self.payload["session_id"],
                            "disconnect_confirmed": True,
                        }
                    )
                else:
                    self.chat.cancel_turn(
                        {
                            "session_id": self.payload["session_id"],
                            "cancel_confirmed": True,
                        }
                    )
                release.set()
                self.join()
            state = self.chat.snapshot()
            self.assertNotIn("LATE", json.dumps(state))
            if disconnect:
                self.assertIsNone(state["progress"])
                self.assertEqual(state["turns"], [])
            else:
                self.assertEqual(state["progress"]["state"], "cancelled")
                self.assertEqual(state["artifacts"], [])


if __name__ == "__main__":
    unittest.main()
