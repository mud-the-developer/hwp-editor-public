"""App-owned Pi text chat. Profile/model reuse and each send require GUI consent."""

from copy import deepcopy
import hashlib
import json
import math
from pathlib import Path
import re
import secrets
import threading
import time

from hwp_pi_probe import ProbeFailure, file_digest, resolve_runtime, run_sdk_command
from hwp_pi_context import validate_artifact
from hwp_resources import resource_root

HISTORY_LIMIT = 1048576


def resolve_chat_runtime(cli):
    base = resolve_runtime(cli)
    helper = (resource_root() / "scripts/pi_chat.mjs").resolve(strict=True)
    rules = resource_root() / "docs/hwp-agent-runtime.md"
    if rules.is_symlink() or rules.resolve(strict=True).parent != (
        resource_root() / "docs"
    ).resolve(strict=True):
        raise ProbeFailure("runtime_binding")
    rules = rules.resolve(strict=True)
    runtime = {
        **base,
        "base_sha256": base["sha256"],
        "chat_helper": str(helper),
        "chat_helper_sha256": file_digest(helper),
        "rules_path": str(rules),
        "rules_sha256": file_digest(rules, 16384),
    }
    runtime["sha256"] = hashlib.sha256(
        json.dumps(
            runtime, sort_keys=True, ensure_ascii=False, separators=(",", ":")
        ).encode()
    ).hexdigest()
    return runtime


def text_content(message):
    content = message.get("content")
    if isinstance(content, str):
        return content
    if not isinstance(content, list):
        raise ProbeFailure("chat_protocol")
    return "".join(
        part.get("text", "")
        for part in content
        if isinstance(part, dict)
        and part.get("type") == "text"
        and isinstance(part.get("text"), str)
    )


READ_TOOLS = ("get_review_context", "get_job_status")
STAGING_TOOLS = ("propose_text_edits", "request_approved_job")
ALL_TOOLS = READ_TOOLS + STAGING_TOOLS


def checked_usage(value):
    if value is None:
        return None
    keys = (
        "input_tokens",
        "output_tokens",
        "cache_read_tokens",
        "cache_write_tokens",
        "total_tokens",
    )
    if (
        not isinstance(value, dict)
        or set(value) != set(keys) | {"cost_usd"}
        or any(type(value[k]) is not int or not 0 <= value[k] <= 10**9 for k in keys)
    ):
        raise ProbeFailure("chat_stream_protocol")
    cost = value["cost_usd"]
    if cost is not None and (
        type(cost) not in (int, float)
        or not 0 <= cost <= 10**6
        or not math.isfinite(cost)
    ):
        raise ProbeFailure("chat_stream_protocol")
    return dict(value)


class ChatStream:
    """Only public nonce-bound JSONL events; the final exited receipt stays authoritative."""

    def __init__(self, request, on_progress=None):
        self.request = request
        self.on_progress = on_progress
        self.buffer = bytearray()
        self.digest = hashlib.sha256()
        self.received = 0
        self.sequence = 0
        self.final = None
        self.assistants = 0
        self.started = {}
        self.finished = set()
        self.limit = (
            3
            if request.get("proposal_tools")
            else 2
            if request.get("context") is not None
            else 1
        )
        self.allowed = (
            ALL_TOOLS
            if request.get("proposal_tools")
            else READ_TOOLS
            if request.get("context") is not None
            else ()
        )
        self.state = {
            "stage": "starting",
            "text": "",
            "requests": 0,
            "request_limit": self.limit,
            "tool_activity": [],
            "usage": None,
        }

    def feed(self, block):
        self.received += len(block)
        if self.received > 2097152:
            raise ProbeFailure("chat_stream_protocol")
        self.digest.update(block)
        self.buffer.extend(block)
        while b"\n" in self.buffer:
            line, _, rest = self.buffer.partition(b"\n")
            self.buffer = bytearray(rest)
            try:
                from hwp_review import parse_json

                frame = parse_json(line.removesuffix(b"\r").decode("utf-8"))
                self._frame(frame)
            except (ValueError, UnicodeError, KeyError, TypeError, RecursionError):
                raise ProbeFailure("chat_stream_protocol") from None

    def _frame(self, frame):
        if (
            self.final is not None
            or not isinstance(frame, dict)
            or frame.get("id") != self.request["id"]
            or frame.get("nonce") != self.request["nonce"]
        ):
            raise ProbeFailure("chat_stream_protocol")
        if frame.get("type") == "pi_chat_result":
            self.final = frame
            return
        if (
            self.request["type"] != "chat"
            or set(frame) != {"type", "id", "nonce", "seq", "event", "data"}
            or frame.get("type") != "pi_chat_event"
            or type(frame.get("seq")) is not int
            or frame["seq"] != self.sequence + 1
            or frame["seq"] > 4096
        ):
            raise ProbeFailure("chat_stream_protocol")
        self.sequence = frame["seq"]
        event, data = frame["event"], frame["data"]
        if event == "phase":
            if (
                not isinstance(data, dict)
                or set(data) != {"stage", "requests", "request_limit"}
                or data["stage"] not in ("model_setup", "model_request")
                or type(data["requests"]) is not int
                or not self.state["requests"] <= data["requests"] <= self.limit
                or type(data["request_limit"]) is not int
                or data["request_limit"] != self.limit
            ):
                raise ProbeFailure("chat_stream_protocol")
            self.state.update(data)
        elif event == "assistant_start":
            self.assistants += 1
            if data != {} or self.assistants > self.limit:
                raise ProbeFailure("chat_stream_protocol")
            self.state.update(text="", stage="responding")
        elif event == "text_delta":
            if (
                not isinstance(data, dict)
                or set(data) != {"delta"}
                or not isinstance(data["delta"], str)
                or not self.assistants
                or not 0 < len(data["delta"].encode("utf-8")) <= 16384
            ):
                raise ProbeFailure("chat_stream_protocol")
            text = self.state["text"] + data["delta"]
            if len(text.encode("utf-8")) > 262144:
                raise ProbeFailure("chat_stream_protocol")
            self.state.update(text=text, stage="responding")
        elif event == "tool":
            if (
                not isinstance(data, dict)
                or set(data) != {"id", "name", "status"}
                or data["name"] not in self.allowed
                or data["status"] not in ("started", "finished", "failed")
                or not isinstance(data["id"], str)
                or not 0 < len(data["id"]) <= 128
                or re.search(r"[\x00-\x1f\x7f]", data["id"])
                or len(self.state["tool_activity"]) >= 8
            ):
                raise ProbeFailure("chat_stream_protocol")
            if data["status"] == "started":
                if data["id"] in self.started or len(self.started) >= 4:
                    raise ProbeFailure("chat_stream_protocol")
                self.started[data["id"]] = data["name"]
            else:
                if (
                    self.started.get(data["id"]) != data["name"]
                    or data["id"] in self.finished
                ):
                    raise ProbeFailure("chat_stream_protocol")
                self.finished.add(data["id"])
            self.state["tool_activity"].append(dict(data))
        elif event == "usage":
            self.state["usage"] = checked_usage(data)
        else:
            raise ProbeFailure("chat_stream_protocol")
        if self.on_progress is not None:
            self.on_progress(deepcopy(self.state))

    def finish(self, blob):
        if self.received == 0:
            self.feed(
                blob
            )  # Fake transports may return a complete stream without callbacks.
        elif (
            self.received != len(blob)
            or self.digest.digest() != hashlib.sha256(blob).digest()
        ):
            raise ProbeFailure("chat_stream_protocol")
        if self.buffer or self.final is None:
            raise ProbeFailure("chat_stream_protocol")
        return self.final


def call_chat(
    runtime,
    action,
    cancel,
    *,
    model=None,
    history=(),
    message=None,
    context=None,
    document_history=False,
    proposal_tools=False,
    on_progress=None,
):
    request = {
        "type": action,
        "id": "p-" + secrets.token_hex(8),
        "nonce": secrets.token_hex(16),
        "profile_dir": str(Path(runtime["cli"]).parent.parent),
    }
    if action == "chat":
        rules_path = Path(runtime["rules_path"])
        with rules_path.open("rb") as stream:
            rules_raw = stream.read(16385)
        if (
            not 0 < len(rules_raw) <= 16384
            or hashlib.sha256(rules_raw).hexdigest() != runtime["rules_sha256"]
        ):
            raise ProbeFailure("runtime_changed")
        request.update(
            model=model,
            history=list(history),
            message=message,
            rules=rules_raw.decode("utf-8"),
            context=context,
            document_history=document_history,
            proposal_tools=proposal_tools,
        )
    elif action != "profile_info":
        raise ProbeFailure("chat_protocol")
    decoder = ChatStream(request, on_progress)
    blob = run_sdk_command(
        runtime,
        cancel,
        request,
        helper_key="chat_helper",
        timeout=300 if action == "chat" else 30,
        output_limit=2097152,
        on_stdout=decoder.feed if action == "chat" else None,
    )
    data = decoder.finish(blob)
    keys = {
        "type",
        "id",
        "nonce",
        "success",
        "stage",
        "model",
        "text",
        "messages",
        "outcome",
        "requests",
        "process_attempts",
        "tools_enabled",
        "document_shared",
        "session_persisted",
        "write_tools_enabled",
        "tool_activity",
        "proposal_tools_enabled",
        "artifacts",
        "usage",
    }
    if (
        not isinstance(data, dict)
        or set(data) != keys
        or data.get("type") != "pi_chat_result"
        or data.get("id") != request["id"]
        or data.get("nonce") != request["nonce"]
    ):
        raise ProbeFailure("chat_protocol")
    shared = (
        bool(context is not None or document_history) if action == "chat" else False
    )
    if (
        data.get("tools_enabled") is not bool(context)
        or data.get("document_shared") is not shared
        or data.get("session_persisted") is not False
        or data.get("write_tools_enabled") is not False
    ):
        raise ProbeFailure("chat_protocol")
    if (
        data.get("proposal_tools_enabled") is not proposal_tools
        or proposal_tools
        and context is None
    ):
        raise ProbeFailure("chat_protocol")
    allowed_tools = ALL_TOOLS if proposal_tools else READ_TOOLS
    model_limit = 3 if proposal_tools else 2 if context is not None else 1
    activity = data.get("tool_activity")
    if not isinstance(activity, list) or len(activity) > 8:
        raise ProbeFailure("chat_protocol")
    for event in activity:
        if (
            not isinstance(event, dict)
            or set(event) != {"id", "name", "status"}
            or event.get("name") not in allowed_tools
            or event.get("status") not in ("started", "finished", "failed")
            or not isinstance(event.get("id"), str)
            or not 0 < len(event["id"]) <= 128
        ):
            raise ProbeFailure("chat_protocol")
    if context is None and activity:
        raise ProbeFailure("chat_protocol")
    if decoder.sequence and decoder.state["tool_activity"] != activity:
        raise ProbeFailure("chat_stream_protocol")
    usage = checked_usage(data.get("usage"))
    if (
        type(data.get("requests")) is not int
        or type(data.get("process_attempts")) is not int
        or data["process_attempts"] != 0
        or not 0 <= data["requests"] <= model_limit
    ):
        raise ProbeFailure("chat_protocol")
    if data.get("success") is not True:
        stage = data.get("stage")
        raise ProbeFailure(
            stage
            if stage
            in (
                "profile",
                "profile_changed",
                "model_setup",
                "model_request",
                "dispose",
                "forbidden_attempt",
                "request",
            )
            else "chat_protocol"
        )
    selected = data.get("model")
    if (
        not isinstance(selected, dict)
        or set(selected) != {"provider", "id"}
        or any(
            not isinstance(v, str)
            or not 0 < len(v) <= 512
            or re.search(r"[\x00-\x1f\x7f]", v)
            for v in selected.values()
        )
    ):
        raise ProbeFailure("chat_protocol")
    artifacts = data.get("artifacts")
    if (
        not isinstance(artifacts, list)
        or len(artifacts) > 2
        or artifacts
        and not proposal_tools
    ):
        raise ProbeFailure("chat_protocol")
    validated = []
    for artifact in artifacts:
        try:
            checked = validate_artifact(context, artifact)
        except (ValueError, KeyError, TypeError):
            raise ProbeFailure("artifact_scope") from None
        if any(row["kind"] == checked["kind"] for row in validated):
            raise ProbeFailure("artifact_scope")
        tool = (
            "propose_text_edits"
            if checked["kind"] == "text_draft"
            else "request_approved_job"
        )
        if not any(
            event["name"] == tool and event["status"] == "finished"
            for event in activity
        ):
            raise ProbeFailure("artifact_scope")
        validated.append(checked)
    if action == "profile_info":
        if (
            data["requests"] != 0
            or data.get("stage") != "profile_ready"
            or data.get("messages") != []
            or data.get("text") != ""
            or usage is not None
        ):
            raise ProbeFailure("chat_protocol")
        return {"model": selected}
    if (
        selected != model
        or not 1 <= data["requests"] <= model_limit
        or data.get("stage") != "settled"
        or data.get("outcome") not in ("settled", "incomplete", "history_full")
        or not isinstance(data.get("text"), str)
        or not 0 < len(data["text"].encode()) <= 262144
    ):
        raise ProbeFailure("chat_protocol")
    messages = data.get("messages")
    if (
        not isinstance(messages, list)
        or len(messages) > 100
        or len(json.dumps(messages).encode()) > HISTORY_LIMIT
    ):
        raise ProbeFailure("chat_protocol")
    if data["outcome"] == "history_full":
        if messages:
            raise ProbeFailure("chat_protocol")
    else:
        if any(
            not isinstance(item, dict)
            or item.get("role") not in ("user", "assistant", "toolResult")
            or item.get("role") == "toolResult"
            and item.get("toolName") not in ALL_TOOLS
            for item in messages
        ):
            raise ProbeFailure("chat_history")
        if (
            not len(history) + 2 <= len(messages) <= len(history) + 8
            or messages[: len(history)] != list(history)
            or messages[len(history)].get("role") != "user"
            or messages[-1].get("role") != "assistant"
            or text_content(messages[len(history)]) != message
            or any(item.get("role") == "user" for item in messages[len(history) + 1 :])
            or text_content(messages[-1]) != data["text"]
        ):
            raise ProbeFailure("chat_history")
    return {
        "model": selected,
        "text": data["text"],
        "history": messages,
        "outcome": data["outcome"],
        "tool_activity": activity,
        "document_shared": shared,
        "artifacts": validated,
        "usage": usage,
    }


class Chat:
    def __init__(self, discovery, context_provider=None):
        self.discovery = discovery
        self.context_provider = context_provider
        self.context_preview = None
        self.document_history = False
        self.artifacts = []
        self.turn_scope = None
        self.guard = threading.RLock()
        self.thread = None
        self.cancel = threading.Event()
        self.closed = False
        self.epoch = 0
        self.status = "disconnected"
        self.runtime = None
        self.chat_id = None
        self.generation = None
        self.session_id = None
        self.model = None
        self.history = []
        self.turns = []
        self.used_ids = set()
        self.stage = None
        self.last_turn = None
        self.progress = None
        self.started_at = None

    def snapshot(self):
        with self.guard:
            runtime = (
                {
                    key: self.runtime[key]
                    for key in ("cli", "node", "sdk_entry", "sdk_version", "sha256")
                }
                if self.runtime
                else None
            )
            progress = deepcopy(self.progress)
            if progress is not None and self.started_at is not None:
                progress["elapsed_seconds"] = round(
                    max(0, time.monotonic() - self.started_at), 3
                )
            return {
                "status": self.status,
                "progress": progress,
                "chat_id": self.chat_id,
                "runtime": runtime,
                "session_id": self.session_id,
                "model": self.model.copy() if self.model else None,
                "turns": deepcopy(self.turns),
                "stage": self.stage,
                "last_turn": self.last_turn.copy() if self.last_turn else None,
                "profile_loaded": self.model is not None,
                "authentication_verified": False,
                "tools_enabled": bool(
                    self.last_turn and self.last_turn.get("read_tools")
                ),
                "tool_names": list(
                    ALL_TOOLS
                    if self.last_turn and self.last_turn.get("proposal_tools")
                    else READ_TOOLS
                )
                if self.last_turn and self.last_turn.get("read_tools")
                else [],
                "proposal_tools_enabled": bool(
                    self.last_turn and self.last_turn.get("proposal_tools")
                ),
                "artifacts": [
                    {
                        key: deepcopy(row[key])
                        for key in ("artifact_id", "turn_id", "artifact")
                    }
                    for row in self.artifacts
                ],
                "write_tools_enabled": False,
                "document_shared": self.document_history,
                "context_preview": deepcopy(self.context_preview),
                "session_persisted": False,
            }

    def _idle(self):
        if self.closed:
            raise ValueError("Pi chat is closed")
        if self.thread is not None and self.thread.is_alive():
            raise ValueError("Pi operation already active; no duplicate")

    def _launch(self, operation):
        epoch = self.epoch
        cancel = self.cancel

        def worker():
            try:
                data = operation(cancel)
                with self.guard:
                    if self.closed or epoch != self.epoch:
                        return
                    if cancel.is_set():
                        if self.progress is not None:
                            self.progress.update(
                                state="cancelled",
                                elapsed_seconds=round(
                                    max(
                                        0,
                                        time.monotonic()
                                        - (self.started_at or time.monotonic()),
                                    ),
                                    3,
                                ),
                            )
                        self.started_at = None
                        self.status, self.stage = "ready", "cancelled"
                        if self.last_turn:
                            self.last_turn = {**self.last_turn, "outcome": "cancelled"}
                        return
                    if self.status == "preparing":
                        self.runtime, self.status = data, "prepared"
                    elif self.status == "connecting":
                        self.model = data["model"]
                        self.session_id = secrets.token_hex(16)
                        self.status = "ready"
                    elif self.status == "sending":
                        if self.last_turn is None:
                            raise ProbeFailure("chat_infrastructure")
                        pending = []
                        for raw in data.get("artifacts", []):
                            if self.turn_scope is None or not self.last_turn.get(
                                "proposal_tools"
                            ):
                                raise ProbeFailure("artifact_scope")
                            checked = validate_artifact(self.turn_scope["preview"], raw)
                            pending.append(
                                {
                                    "artifact_id": "a-" + secrets.token_hex(16),
                                    "session_id": self.session_id,
                                    "turn_id": self.last_turn["turn_id"],
                                    "scope": deepcopy(self.turn_scope),
                                    "artifact": checked,
                                }
                            )
                        self.artifacts.extend(pending)
                        self.history = data["history"]
                        self.turns.append(
                            {
                                "role": "assistant",
                                "text": data["text"],
                                "outcome": data["outcome"],
                                "tool_activity": data.get("tool_activity", []),
                                "usage": data.get("usage"),
                            }
                        )
                        self.last_turn = {**self.last_turn, "outcome": data["outcome"]}
                        self.progress = None
                        self.started_at = None
                        self.status = (
                            "full"
                            if data["outcome"] == "history_full"
                            or len(json.dumps(self.turns).encode()) > HISTORY_LIMIT
                            else "ready"
                        )
            except Exception as error:
                with self.guard:
                    if self.closed or epoch != self.epoch:
                        return
                    self.stage = (
                        error.stage
                        if isinstance(error, ProbeFailure)
                        else "chat_infrastructure"
                    )
                    if self.progress is not None:
                        self.progress.update(
                            state="cancelled" if cancel.is_set() else "failed",
                            elapsed_seconds=round(
                                max(
                                    0,
                                    time.monotonic()
                                    - (self.started_at or time.monotonic()),
                                ),
                                3,
                            ),
                        )
                    self.started_at = None
                    if self.last_turn:
                        self.last_turn = {
                            **self.last_turn,
                            "outcome": "cancelled" if cancel.is_set() else "failed",
                        }
                    self.status = (
                        "ready"
                        if self.model is not None
                        and self.stage
                        not in ("runtime_changed", "runtime_binding", "profile_changed")
                        else "failed"
                    )

        self.thread = threading.Thread(
            target=worker, daemon=True, name="HWP-Pi-chat-operation"
        )
        try:
            self.thread.start()
        except RuntimeError:
            self.status, self.stage = "failed", "thread_start"
            if self.progress is not None:
                self.progress.update(state="failed", elapsed_seconds=0)
            if self.last_turn is not None:
                self.last_turn = {**self.last_turn, "outcome": "failed"}
            self.started_at = None
        return self.snapshot()

    def prepare(self):
        with self.guard:
            self._idle()
            if self.model is not None:
                raise ValueError(
                    "Disconnect explicitly before replacing the conversation"
                )
            state = self.discovery.snapshot()
            rows = [
                item
                for item in state["agents"]
                if item.get("id") == "pi" and item.get("status") == "detected"
            ]
            if state["status"] != "finished" or len(rows) != 1:
                raise ValueError("Find Pi first")
            self.epoch += 1
            self.cancel = threading.Event()
            self.generation = state["generation"]
            self.chat_id = secrets.token_hex(16)
            self.status = "preparing"
            self.runtime = self.stage = None
            cli = rows[0]["path"]
            return self._launch(lambda cancel: resolve_chat_runtime(cli))

    def connect(self, payload):
        with self.guard:
            self._idle()
            if self.status != "prepared" or self.runtime is None:
                raise ValueError("Prepare Pi chat programs first")
            expected = {
                "chat_id": self.chat_id,
                "runtime_sha256": self.runtime["sha256"],
                "reuse_pi_confirmed": True,
            }
            if payload != expected or payload.get("reuse_pi_confirmed") is not True:
                raise ValueError("Explicit exact existing-Pi profile consent required")
            state = self.discovery.snapshot()
            if state["status"] != "finished" or state["generation"] != self.generation:
                raise ValueError("Pi discovery changed; prepare again")
            self.status = "connecting"
            self.stage = None
            self.cancel = threading.Event()
            runtime = self.runtime
            return self._launch(
                lambda cancel: call_chat(runtime, "profile_info", cancel)
            )

    def preview_context(self, payload):
        with self.guard:
            self._idle()
            if self.status != "ready" or self.context_provider is None:
                raise ValueError("Connect Pi before selecting document context")
            if (
                not isinstance(payload, dict)
                or set(payload) != {"session_id", "selection"}
                or payload.get("session_id") != self.session_id
            ):
                raise ValueError("Exact context session required")
            snapshot = self.context_provider.capture(payload["selection"])
            self.context_preview = {
                "context_id": secrets.token_hex(16),
                "context_sha256": hashlib.sha256(
                    json.dumps(
                        snapshot,
                        sort_keys=True,
                        ensure_ascii=False,
                        separators=(",", ":"),
                    ).encode()
                ).hexdigest(),
                "selection": dict(payload["selection"]),
                "preview": snapshot,
            }
            return deepcopy(self.context_preview)

    def send(self, payload):
        with self.guard:
            self._idle()
            if self.status != "ready" or self.runtime is None or self.model is None:
                raise ValueError("Connect Pi model settings first")
            if (
                not isinstance(payload, dict)
                or set(payload)
                not in (
                    {"session_id", "turn_id", "message", "send_confirmed"},
                    {
                        "session_id",
                        "turn_id",
                        "message",
                        "send_confirmed",
                        "context_id",
                        "context_sha256",
                        "context_confirmed",
                        "read_tools_confirmed",
                    },
                    {
                        "session_id",
                        "turn_id",
                        "message",
                        "send_confirmed",
                        "context_id",
                        "context_sha256",
                        "context_confirmed",
                        "read_tools_confirmed",
                        "proposal_tools_confirmed",
                    },
                )
                or payload.get("session_id") != self.session_id
                or payload.get("send_confirmed") is not True
            ):
                raise ValueError("Exact Pi conversation/send consent required")
            proposal_tools = "proposal_tools_confirmed" in payload
            if proposal_tools and payload.get("proposal_tools_confirmed") is not True:
                raise ValueError(
                    "Explicit staging tool and max-three-request consent required"
                )
            selected_context = None
            if "context_id" in payload:
                preview = self.context_preview
                if (
                    self.context_provider is None
                    or not preview
                    or payload.get("context_confirmed") is not True
                    or payload.get("read_tools_confirmed") is not True
                    or payload.get("context_id") != preview["context_id"]
                    or payload.get("context_sha256") != preview["context_sha256"]
                ):
                    raise ValueError(
                        "Preview and explicitly confirm the exact selected context/read tools"
                    )
                selected_context = deepcopy(preview)
            turn_id = payload.get("turn_id")
            message = payload.get("message")
            if (
                not isinstance(turn_id, str)
                or re.fullmatch(r"t-[a-f0-9]{16}", turn_id) is None
                or turn_id in self.used_ids
            ):
                raise ValueError("Duplicate/invalid Pi turn; no automatic resend")
            if (
                not isinstance(message, str)
                or not message.strip()
                or len(message) > 16000
                or "\0" in message
            ):
                raise ValueError("Pi message must be plain text within limit")
            if (
                len(self.used_ids) >= 40
                or len(json.dumps(self.history).encode()) > HISTORY_LIMIT
                or len(json.dumps(self.turns).encode()) > HISTORY_LIMIT
            ):
                self.status = "full"
                raise ValueError(
                    "Conversation memory limit; explicitly disconnect/start a new conversation"
                )
            self.used_ids.add(turn_id)
            self.turns.append({"role": "user", "text": message, "turn_id": turn_id})
            self.last_turn = {
                "turn_id": turn_id,
                "message": message,
                "outcome": "sending",
                "read_tools": selected_context is not None,
                "proposal_tools": proposal_tools,
            }
            self.turn_scope = deepcopy(selected_context) if proposal_tools else None
            self.started_at = time.monotonic()
            self.progress = {
                "turn_id": turn_id,
                "state": "sending",
                "stage": "starting",
                "text": "",
                "requests": 0,
                "request_limit": 3 if proposal_tools else 2 if selected_context else 1,
                "tool_activity": [],
                "usage": None,
                "elapsed_seconds": 0,
            }
            self.status = "sending"
            self.stage = None
            self.cancel = threading.Event()
            runtime, model, history = (
                self.runtime,
                self.model.copy(),
                list(self.history),
            )
            document_history = self.document_history
            provider = self.context_provider
            epoch = self.epoch

            def progress(data):
                with self.guard:
                    if (
                        self.closed
                        or self.epoch != epoch
                        or self.cancel.is_set()
                        or self.status != "sending"
                        or self.progress is None
                        or self.progress["turn_id"] != turn_id
                    ):
                        return
                    for key in (
                        "stage",
                        "text",
                        "requests",
                        "request_limit",
                        "tool_activity",
                        "usage",
                    ):
                        self.progress[key] = deepcopy(data[key])

            def ask(cancel):
                context = None
                if selected_context is not None:
                    if provider is None:
                        raise ValueError("Document context provider unavailable")
                    provider.verify(
                        selected_context["selection"], selected_context["preview"]
                    )
                    context = selected_context["preview"]
                    with self.guard:
                        if self.closed or self.epoch != epoch or cancel.is_set():
                            raise ProbeFailure("cancelled")
                        self.document_history = True
                return call_chat(
                    runtime,
                    "chat",
                    cancel,
                    model=model,
                    history=history,
                    message=message,
                    context=context,
                    document_history=document_history,
                    proposal_tools=proposal_tools,
                    on_progress=progress,
                )

            return self._launch(ask)

    def review_artifact(self, payload):
        with self.guard:
            self._idle()
            if (
                not isinstance(payload, dict)
                or set(payload) != {"session_id", "artifact_id", "review_confirmed"}
                or payload.get("session_id") != self.session_id
                or payload.get("review_confirmed") is not True
                or self.context_provider is None
            ):
                raise ValueError("Exact session/artifact human review request required")
            matches = [
                row
                for row in self.artifacts
                if row["artifact_id"] == payload.get("artifact_id")
                and row["session_id"] == self.session_id
            ]
            if len(matches) != 1:
                raise ValueError("Missing/stale Pi artifact")
            row = matches[0]
            return self.context_provider.review_artifact(
                row["scope"]["selection"], row["scope"]["preview"], row["artifact"]
            )

    def cancel_turn(self, payload):
        with self.guard:
            if (
                payload != {"session_id": self.session_id, "cancel_confirmed": True}
                or payload.get("cancel_confirmed") is not True
            ):
                raise ValueError("Exact Pi cancellation confirmation required")
            if self.status == "sending":
                self.cancel.set()
            return self.snapshot()

    def disconnect(self, payload):
        with self.guard:
            if (
                payload != {"session_id": self.session_id, "disconnect_confirmed": True}
                or payload.get("disconnect_confirmed") is not True
            ):
                raise ValueError("Explicit Pi conversation clearing required")
            self.cancel.set()
            self.epoch += 1
            self.status = "disconnected"
            self.runtime = self.model = self.session_id = self.stage = None
            self.history = []
            self.turns = []
            self.used_ids = set()
            self.last_turn = None
            self.context_preview = None
            self.document_history = False
            self.artifacts = []
            self.turn_scope = None
            self.progress = None
            self.started_at = None
            return self.snapshot()

    def close(self):
        with self.guard:
            self.closed = True
            self.cancel.set()
            self.epoch += 1
            self.status = "closed"
            self.history = []
            self.turns = []
            self.context_preview = None
            self.document_history = False
            self.artifacts = []
            self.turn_scope = None
            self.progress = None
            self.started_at = None
        if self.thread is not None and self.thread.is_alive():
            self.thread.join(3)
