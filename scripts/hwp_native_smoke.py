"""Opt-in fresh unsigned WPF/actual-OMP fixture with an inert loopback provider.

No real provider, credentials, Hancom, automatic consent, acceptance or alternate
document executor. The production native TUI, bridge and report UI are exercised.
A failed scope retains its host and files; never replay it or kill its children.
"""
from contextlib import ExitStack
import hashlib
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
from pathlib import Path
import subprocess
import sys
import threading
import time
from unittest.mock import patch

import hwp_native_host as native
import hwp_omp_tui as omp
import hwp_pi_chat
import hwp_report_project as projects
import hwp_report_writer as writer
import hwp_resources as resources
from hwp_synthetic_report import write_inputs

PROVIDER = "hwp-owned-synthetic"
MODEL = "TOY-ONLY"
TUI_REQUEST = "TOY ONLY 한글 😀 terminal advisory"
TUI_ANSWER = "TOY ONLY 한글 😀 공개 참고 답변: 문서 생성이나 채택 권한이 아닙니다."


class _HostRecords:
    """Append-only owned status: published files are never replaced or retried."""

    def __init__(self, directory):
        self.root = Path(directory)
        self.records = self.root / "HOST-records"
        self.records.mkdir(exist_ok=False)
        self.generation = 0
        self.failed = False
        with (self.root / "HOST.json").open("x", encoding="utf-8") as stream:
            json.dump({"contract": "hwp-native-omp-host-index-v1", "records": "HOST-records"}, stream)

    def publish(self, value):
        if self.failed:
            raise RuntimeError("Status publication already failed; preserve the scope without retry")
        try:
            generation = self.generation + 1
            stem = f"{generation:08d}"
            payload = dict(value, receipt_generation=generation)
            with (self.records / (stem + ".json")).open("x", encoding="utf-8") as stream:
                json.dump(payload, stream, ensure_ascii=False, indent=2)
                stream.flush()
                os.fsync(stream.fileno())
            # Directory creation is the commit point, after the immutable file
            # is closed. No rename/delete sharing with an existing reader.
            (self.records / (stem + ".ready")).mkdir(exist_ok=False)
            self.generation = generation
        except Exception:
            self.failed = True
            raise


def read_host_receipt(index):
    """Read one complete immutable generation, never a partial or older fallback."""
    index = omp._unlinked(Path(index).absolute())
    omp._regular(index, 4096)
    descriptor = json.loads(index.read_text(encoding="utf-8"))
    if descriptor != {"contract": "hwp-native-omp-host-index-v1", "records": "HOST-records"}:
        raise ValueError("Expected the immutable owned OMP status index")
    records = omp._unlinked(index.parent / "HOST-records")
    published = sorted(path for path in records.glob("*.ready") if path.is_dir())
    if not published:
        raise ValueError("No complete owned OMP status has been published")
    marker = omp._unlinked(published[-1])
    stem = marker.name.removesuffix(".ready")
    if len(stem) != 8 or not stem.isascii() or not stem.isdecimal():
        raise ValueError("Invalid owned status generation")
    path = records / (stem + ".json")
    omp._regular(path, 1024 * 1024)
    value = json.loads(path.read_text(encoding="utf-8"))
    if type(value) is not dict or value.get("receipt_generation") != int(stem):
        raise ValueError("Owned status generation does not match its publication marker")
    return value


def run(directory, *, provider_call_limit=4, expected_memo_feedback=(), workspace_first=False, workspace_chat=False):
    if type(workspace_first) is not bool or type(workspace_chat) is not bool or workspace_chat and not workspace_first:
        raise ValueError("Workspace chat requires explicit workspace-first synthetic mode")
    maximum_calls = 6 if workspace_chat else 4
    if type(provider_call_limit) is not int or not 1 <= provider_call_limit <= maximum_calls:
        raise ValueError("Synthetic provider call limit exceeded for this explicit fixture mode")
    if (
        not isinstance(expected_memo_feedback, tuple) or len(expected_memo_feedback) > 64
        or any(not isinstance(value, str) or not value.startswith("TOY ONLY: ")
               or not 1 <= len(value) <= 8000 for value in expected_memo_feedback)
        or len(set(expected_memo_feedback)) != len(expected_memo_feedback)
    ):
        raise ValueError("Memo smoke requires distinct explicit unsigned TOY feedback only")
    directory = omp._unlinked(Path(directory).expanduser().absolute())
    directory.mkdir(parents=True, exist_ok=False)
    guard = threading.RLock()
    receipt = {
        "contract": "hwp-native-omp-gui-host-v1", "version": resources.APP_VERSION,
        "frozen": resources.frozen(), "status": "starting",
        "purpose": "NEW UNSIGNED TOY; actual owned OMP TUI; inert local provider; no real model/Hancom",
        "actual_omp_required": True, "model_boundary_mocked": True,
        "os_handoff_mocked": True, "automatic_permission_or_acceptance": False,
        "provider_calls": 0, "provider_attempts": 0, "provider_waiting": None,
        "provider_call_limit": provider_call_limit,
        "native_background_calls": 0, "native_background_responses": 0,
        "native_background_limit": 32, "native_background_skills": [],
        "expected_memo_feedback": list(expected_memo_feedback),
        "workspace_first": workspace_first,
        "workspace_chat": workspace_chat, "omp_launches": [], "shell_actions": [],
        "gui_memo_feedback_verified": False,
        "omp_released": False,
        "provider_release_required": True, "opens": [], "sdk_calls": 0,
        "tui_request": TUI_REQUEST, "tui_answer": TUI_ANSWER,
    }

    records = _HostRecords(directory)

    def record(**fields):
        with guard:
            if receipt["status"] != "failed_preserved":
                receipt.update(fields)
            records.publish(receipt)

    record()
    if resources.frozen():
        with Path(sys.executable).open("rb") as stream:
            record(executable_sha256=hashlib.file_digest(stream, "sha256").hexdigest())
    if workspace_first:
        from hwp_blank_document import blank_document

        inputs = directory / "unsigned-inputs"
        inputs.mkdir()
        source = inputs / "SYNTHETIC-template.hwpx"
        blank = blank_document()
        donor = projects._donor(blank, [1, 2], ["SYNTHETIC TEMPLATE BODY"])
        template, _ = projects.draft.compose(blank, donor, {"target_range": [1, 2], "payload_ranges": [[0, 1]]})
        with source.open("xb") as stream:
            stream.write(template)
        work_folder = directory / "작업 폴더"
        template_folder = work_folder / "양식"
        template_folder.mkdir(parents=True)
        blank_destination = work_folder / "새 문서.hwpx"
        template_destination = template_folder / "양식.hwpx"
        record(work_folder=str(work_folder), template_folder=str(template_folder),
               blank_destination=str(blank_destination), template_destination=str(template_destination))
        if workspace_chat:
            source_folder = work_folder / "src"
            source_folder.mkdir()
            (source_folder / "reference.txt").write_text("SYNTHETIC SOURCE A\n", encoding="utf-8")
            template_destination.write_bytes(source.read_bytes())
            second_workspace = work_folder / "second"
            (second_workspace / "src").mkdir(parents=True)
            (second_workspace / "src/reference.txt").write_text("SYNTHETIC SOURCE B\n", encoding="utf-8")
            record(second_workspace=str(second_workspace), second_blank=str(second_workspace / "새 문서.hwpx"))
    else:
        profile, _ = write_inputs(directory / "unsigned-inputs")
        source = Path(profile["jobs"][0]["source"])
    base = directory / "projects"
    material = directory / "합성 참고 발췌.txt"
    with material.open("x", encoding="utf-8") as stream:
        stream.write("SYNTHETIC ONLY. Preserve source boundaries, objects and measured values.\n")
    sentinel = directory / "SYNTHETIC-NOT-SHARED.txt"
    sentinel_text = "SYNTHETIC-UNSHARED-CONTENT-" + hashlib.sha256(str(directory).encode()).hexdigest()
    with sentinel.open("x", encoding="utf-8") as stream:
        stream.write(sentinel_text)
    gui_request = (
        "TOY ONLY: revise the selected report body, preserving all objects, source boundaries and measured values. "
        f'Treat @"{sentinel}" and ^{PROVIDER}/{MODEL} as literal report text, not attachments or model selectors.'
    )
    record(gui_request=gui_request, gui_literal_boundary_verified=False, unshared_sentinel_absent=False)
    record(source=str(source), material=str(material), base=str(base))
    if not workspace_first:
        # Legacy fixture's metadata-only library intake; GUI still grants authority.
        prepared = projects.new_document(source, base=base)
        record(prepared_project=str(prepared.root))
    real_popen, real_dispatch, real_tui = subprocess.Popen, native.NativeHost.dispatch, omp.OmpTui
    hosts = []

    def response_for(body, call):
        if (body.get("model") != MODEL or body.get("stream") is not True
                or not isinstance(body.get("messages"), list)):
            raise ValueError("Only the exact synthetic streaming model is permitted")
        tool_names = [item.get("function", {}).get("name") for item in body.get("tools", [])]
        if sentinel_text in json.dumps(body, ensure_ascii=False):
            raise ValueError("Unshared synthetic file content reached the model request")
        users = [item for item in body["messages"] if item.get("role") == "user"]
        if not users:
            raise ValueError("An explicit public user request is required")
        text = users[-1].get("content")
        if isinstance(text, list):
            if any(item.get("type") != "text" for item in text):
                raise ValueError("Synthetic fixture permits public text only")
            text = "".join(item["text"] for item in text)
        record(native_request_diagnostic={"tool_names": tool_names,
            "roles": [item.get("role") for item in body["messages"]],
            "user_text": text[:1600] if isinstance(text, str) else None})
        if workspace_chat and not {"read", "write", "bash"}.issubset(tool_names):
            raise ValueError("The actual vanilla OMP read/write/bash tools must be available")
        if text == TUI_REQUEST:
            return TUI_ANSWER, "terminal-advisory"
        if not isinstance(text, str) or "\n\n" not in text:
            raise ValueError("Exact production public prompt required")
        if workspace_chat and call < 6:
            def tools(*requests):
                return [{"index": index, "id": f"toy-call-{call}-{index}", "type": "function",
                         "function": {"name": name, "arguments": json.dumps(arguments)}}
                        for index, (name, arguments) in enumerate(requests)]

            def shell(label):
                powershell = str(Path(os.environ["SystemRoot"]) / "System32/WindowsPowerShell/v1.0/powershell.exe").replace("\\", "/")
                code = (
                    "[Console]::OutputEncoding = [Text.UTF8Encoding]::new(); "
                    "if ([IO.File]::Exists('toy-shell-note.txt')) { throw 'Synthetic output already exists' }; "
                    f"[IO.File]::WriteAllText('toy-shell-note.txt','SYNTHETIC SHELL {label}'); "
                    "[Console]::WriteLine((Get-Location).Path); "
                    "[Console]::Write([IO.File]::ReadAllText('src/reference.txt'))"
                )
                return {"command": f'"{powershell}" -NoLogo -NoProfile -NonInteractive -Command "{code}"', "timeout": 15}
            if call in (1, 3, 4):
                chat = json.loads(text.split("\n\n", 1)[1])
                expected = {1: "TOY ONLY: inspect current workspace; run harmless shell and create two synthetic notes",
                            3: "TOY ONLY: selected reference",
                            4: "TOY ONLY: inspect switched workspace; run harmless shell and create two synthetic notes"}[call]
                if chat.get("contract") != "hwp-workspace-chat-v1" or chat.get("instruction") != expected:
                    raise ValueError("Expected the exact explicit ordinary GUI chat")
            if call == 1:
                if chat["materials"] or "HWPReview 작업 안내" not in json.dumps(body, ensure_ascii=False):
                    raise ValueError("Project Markdown guidance must reach vanilla OMP before report intake")
                return tools(("bash", shell("A")), ("read", {"path": "src/reference.txt"}),
                             ("write", {"path": "toy-native-write.txt", "content": "SYNTHETIC NATIVE WRITE A\n"})), "native-tool-request"
            if call in (2, 5):
                captured = json.dumps([item for item in body["messages"] if item.get("role") == "tool"], ensure_ascii=False)
                label = "A" if call == 2 else "B"
                folder = work_folder if call == 2 else second_workspace
                shell_file, written_file = folder / "toy-shell-note.txt", folder / "toy-native-write.txt"
                if (f"SYNTHETIC SOURCE {label}" not in captured
                        or shell_file.read_text(encoding="utf-8") != f"SYNTHETIC SHELL {label}"
                        or written_file.read_text(encoding="utf-8") != f"SYNTHETIC NATIVE WRITE {label}\n"):
                    raise ValueError("Actual native shell/read/write results or working directory differ")
                if call == 5 and "SYNTHETIC SOURCE A" in json.dumps(body, ensure_ascii=False):
                    raise ValueError("Old workspace conversation crossed the new session boundary")
                action = {"workspace": str(folder), "shell_sha256": hashlib.sha256(shell_file.read_bytes()).hexdigest(),
                          "write_sha256": hashlib.sha256(written_file.read_bytes()).hexdigest()}
                record(native_shell_verified=True, switched_workspace_verified=call == 5,
                       shell_actions=[*receipt["shell_actions"], action])
                return ("SYNTHETIC CHAT A: 기본 도구와 실제 셸로 자료를 읽고 시험 파일을 만들었습니다." if call == 2
                        else "SYNTHETIC CHAT B: 변경한 폴더에서 기본 도구와 셸을 실행했습니다."), "native-chat-answer"
            if call == 3:
                selected = chat.get("materials")
                if len(selected) != 1 or "SYNTHETIC ONLY. Preserve source boundaries" not in selected[0].get("text", ""):
                    raise ValueError("Explicitly selected GUI source snapshot is missing")
                record(selected_chat_material_verified=True)
                return "SYNTHETIC SELECTED: 선택 자료로 일반 대화를 이어갑니다.", "selected-source-chat"
            return tools(("bash", shell("B")), ("read", {"path": "src/reference.txt"}),
                         ("write", {"path": "toy-native-write.txt", "content": "SYNTHETIC NATIVE WRITE B\n"})), "switched-native-tool-request"
        request = writer.validate_request(json.loads(text.split("\n\n", 1)[1]))
        if call == 1:
            if "@" in text or "^" in text:
                raise ValueError("Native request did not preserve literal JSON without implicit mentions")
            if expected_memo_feedback:
                if (gui_request not in request["instruction"]
                        or any(request["instruction"].count(value) != 1 for value in expected_memo_feedback)):
                    raise ValueError("Actual memo batch omitted, duplicated or replaced explicit TOY feedback")
                record(gui_memo_feedback_verified=True)
            elif request["instruction"] != gui_request:
                raise ValueError("Native request did not preserve the explicit GUI request")
            record(gui_literal_boundary_verified=True, unshared_sentinel_absent=True)
        revised = "SYNTHETIC REPORT BODY REVISED" if call == 1 else "SYNTHETIC REPORT BODY FOLLOWUP"
        proposal = {
            "schema": "hwp-report-proposal-v1", "request_id": request["request_id"],
            "context_sha256": request["context_sha256"],
            "jobs": [{"job_id": job["job_id"], "paragraphs": [revised]} for job in request["jobs"]],
            "summary": ["합성 본문만 수정하고 원본의 고정 경계를 유지했습니다."],
        }
        writer.validate_proposal(request, proposal)
        return json.dumps(proposal, ensure_ascii=False, separators=(",", ":")), "gui-proposal"

    def skill_routing_request(body):
        messages = body.get("messages")
        if (body.get("model") != MODEL or body.get("stream") is not True or body.get("tools")
                or not isinstance(messages, list) or len(messages) != 1
                or messages[0].get("role") != "user" or not isinstance(messages[0].get("content"), str)):
            return None
        text = messages[0]["content"]
        if not text.startswith("Compress into one routing hint of at most 12 words:"):
            return None
        if "\n\nSkill: " not in text or "\nDescription: " not in text:
            return None
        return text.split("\n\nSkill: ", 1)[1].split("\nDescription: ", 1)[0]

    class ProviderHandler(BaseHTTPRequestHandler):
        def log_message(self, _format, *args):
            # Never log prompt, HTTP headers or credentials into a console.
            return

        def do_POST(self):
            try:
                if self.path != "/v1/chat/completions" or self.headers.get("Transfer-Encoding"):
                    raise ValueError("Only the owned loopback chat endpoint is permitted")
                size = int(self.headers.get("Content-Length", "0"))
                if not 0 < size <= 1024 * 1024:
                    raise ValueError("Synthetic request size exceeded")
                body = json.loads(self.rfile.read(size).decode("utf-8"))
                background_skill = skill_routing_request(body)
                with guard:
                    if receipt["status"] == "failed_preserved":
                        raise ValueError("Stopped synthetic request; no retry")
                    record(provider_attempts=receipt["provider_attempts"] + 1)
                    if background_skill is not None:
                        call = receipt["native_background_calls"] + 1
                        if call > receipt["native_background_limit"]:
                            raise ValueError("Native skill-routing call bound exceeded")
                        record(native_background_calls=call,
                               native_background_skills=[*receipt["native_background_skills"], background_skill])
                        text, kind = "Use for its documented workflow; not unrelated requests.", "native-skill-routing"
                    else:
                        if receipt["provider_waiting"] is not None:
                            raise ValueError("Overlapping interactive synthetic request; no retry")
                        call = receipt["provider_calls"] + 1
                        if call > provider_call_limit:
                            raise ValueError("Fresh synthetic call bound exceeded; no retry")
                        text, kind = response_for(body, call)
                        record(provider_calls=call, provider_waiting=call, last_request_kind=kind)
                if background_skill is None:
                    release = directory / ("PROVIDER-RELEASE-" + str(call) + ".json")
                    deadline = time.monotonic() + 180
                    while not release.exists():
                        if time.monotonic() >= deadline:
                            raise TimeoutError("Synthetic response release not observed; preserve scope")
                        time.sleep(0.05)
                    omp._regular(release, 4096)
                    if json.loads(release.read_text(encoding="utf-8")) != {
                        "contract": "hwp-native-synthetic-provider-release-v1", "call": call,
                    }:
                        raise ValueError("Invalid owned synthetic response release")
                wire_id = "owned-toy-" + ("background-" if background_skill is not None else "") + str(call)
                self.send_response(200)
                self.send_header("Content-Type", "text/event-stream; charset=utf-8")
                self.send_header("Cache-Control", "no-cache")
                self.end_headers()
                if isinstance(text, list):
                    chunks = [{"role": "assistant", "content": None, "tool_calls": text}]
                else:
                    chunks = [
                        {"role": "assistant", "content": ""},
                        {"content": text[:len(text) // 2]}, {"content": text[len(text) // 2:]},
                    ]
                for delta in chunks:
                    frame = {"id": wire_id, "object": "chat.completion.chunk",
                             "created": 0, "model": MODEL,
                             "choices": [{"index": 0, "delta": delta, "finish_reason": None}]}
                    self.wfile.write(("data: " + json.dumps(frame, ensure_ascii=False) + "\n\n").encode("utf-8"))
                    self.wfile.flush()
                frame = {"id": wire_id, "object": "chat.completion.chunk",
                         "created": 0, "model": MODEL,
                         "choices": [{"index": 0, "delta": {}, "finish_reason": "tool_calls" if isinstance(text, list) else "stop"}]}
                self.wfile.write(("data: " + json.dumps(frame) + "\n\ndata: [DONE]\n\n").encode("utf-8"))
                self.wfile.flush()
                if background_skill is not None:
                    with guard:
                        record(native_background_responses=receipt["native_background_responses"] + 1)
                else:
                    record(provider_waiting=None, last_response_kind=kind, last_response_characters=len(text))
            except Exception as error:
                record(status="failed_preserved", error=type(error).__name__ + ": " + str(error))
                self.close_connection = True

    provider = ThreadingHTTPServer(("127.0.0.1", 0), ProviderHandler)
    provider.daemon_threads = True
    provider_thread = threading.Thread(target=provider.serve_forever, name="HWP-owned-synthetic-provider", daemon=True)
    provider_thread.start()
    record(provider_origin="http://127.0.0.1:" + str(provider.server_port), provider_external=False)

    def owned_tui(identity):
        if any(host.omp is not None for host in hosts):
            raise ValueError("Exactly one actual OMP instance per fresh fixture")
        return real_tui(identity, root=directory / "native-omp")

    def reject_sdk(*_args, **_kwargs):
        record(sdk_calls=receipt["sdk_calls"] + 1)
        raise ValueError("No SDK session/provider is permitted beside the one actual OMP TUI")

    def open_copy(path, *args, **kwargs):
        path = Path(path).resolve(strict=True)
        own_project = path.is_relative_to(base) or workspace_first and path.is_relative_to(work_folder / ".hwpreview/Projects")
        if not own_project or path.name != "review.hwpx" or path.parent.parent.name != "review-copies":
            raise ValueError("Only the owned editable review copy may be handed off")
        with path.open("rb") as stream:
            digest = hashlib.file_digest(stream, "sha256").hexdigest()
        record(opens=[*receipt["opens"], {"path": str(path), "sha256": digest}])

    def observed_popen(command, *args, **kwargs):
        if (not isinstance(command, list) or len(command) != 4
                or Path(command[0]).name.lower() != "dotnet.exe"
                or Path(command[1]).name != "HWPReview.Native.dll"
                or command[2] != "--owned-host" or kwargs.get("shell") is not False
                or "ui_pid" in receipt):
            raise ValueError("Only one verified owned WPF child may be started by this fixture")
        with Path(command[1]).open("rb") as stream:
            digest = hashlib.file_digest(stream, "sha256").hexdigest()
        child = real_popen(command, *args, **kwargs)
        record(status="ui_starting", ui_pid=child.pid, native_dll_sha256=digest)
        return child

    def bounded_dispatch(host, method, params):
        if receipt["status"] == "failed_preserved":
            raise ValueError("Stopped fixture: preserve host, process and files without new actions")
        if workspace_first:
            allowed_folders = (work_folder, second_workspace) if workspace_chat else (work_folder,)
            if method == "open_workspace" and Path(params.get("root", "")).resolve() not in allowed_folders:
                raise ValueError("Open only a fresh synthetic work folder in this scope")
            allowed_blanks = (blank_destination, second_workspace / "새 문서.hwpx") if workspace_chat else (blank_destination,)
            if method == "new_blank_report" and Path(params.get("destination", "")).resolve() not in allowed_blanks:
                raise ValueError("Create only this fixture's NEW unsigned blank")
            if method == "open_workspace_document" and Path(params.get("source", "")).resolve() not in (blank_destination, template_destination):
                raise ValueError("Select only this fixture's blank or explicit template")
        if method == "new_report" and Path(params.get("source", "")).resolve() != source:
            raise ValueError("Select only this fresh unsigned synthetic source")
        if method == "open_report" and not Path(params.get("root", "")).resolve().is_relative_to(base):
            raise ValueError("Open only this fixture's owned synthetic project")
        if method in ("legacy_tools", "setup_requirements"):
            raise ValueError("Legacy/installer paths are outside this actual-bridge fixture")
        result = real_dispatch(host, method, params)
        if host not in hosts:
            hosts.append(host)
        if method == "hello":
            record(status="ready")
            if sys.stdout is not None:
                print("SYNTHETIC_NATIVE_OMP_UI_READY", flush=True)
        if method in ("new_report", "open_report", "open_workspace_document", "new_blank_report"):
            record(status="project_ready", project=result["root"])
        if workspace_first and method == "open_workspace":
            record(selected_workspace=result["root"])
        if workspace_first and method == "new_blank_report":
            destination = Path(params["destination"])
            record(blank_project=result["root"], blank_source_sha256=hashlib.sha256(destination.read_bytes()).hexdigest())
        if method == "prepare_omp":
            profile_dir = Path(result["profile_directory"])
            if (not profile_dir.is_relative_to(directory / "native-omp")
                    or receipt["omp_launches"] and not (workspace_chat and receipt["omp_released"] and len(receipt["omp_launches"]) == 1)):
                raise ValueError("Fresh owned profile and at most two sequential normally released OMP launches required")
            models = {"providers": {PROVIDER: {
                "baseUrl": "http://127.0.0.1:" + str(provider.server_port) + "/v1",
                "api": "openai-completions", "apiKey": "SYNTHETIC-NO-CREDENTIAL", "authHeader": True,
                "models": [{"id": MODEL, "name": "TOY ONLY: no real model", "reasoning": False,
                            "input": ["text"], "supportsTools": workspace_chat, "contextWindow": 131072,
                            "maxTokens": 8192, "cost": {"input": 0, "output": 0, "cacheRead": 0, "cacheWrite": 0}}],
            }}}
            # Fixture-only model isolation. Production launch keeps OMP defaults.
            config = {
                "modelRoles": {"default": PROVIDER + "/" + MODEL},
                "enabledProviders": [PROVIDER], "enabledModels": [PROVIDER + "/" + MODEL],
                "startup": {"setupWizard": False}, "providers": {"cacheWarming": "off"},
                "recap": {"enabled": False}, "composer": {"predictions": False},
                "memory": {"backend": "off"}, "autolearn": {"enabled": False},
                "compaction": {"enabled": False}, "retry": {"enabled": False, "modelFallback": False},
                "spelling": {"autocomplete": "off"}, "tui": {"autoGraph": "off"},
                "prewalk": {"enabled": False, "auto": "off"},
                "advisor": {"enabled": False}, "lsp": {"enabled": False},
            }
            for name, value in (("models.yml", models), ("config.yml", config)):
                encoded = json.dumps(value, ensure_ascii=False)
                target = profile_dir / name
                if target.exists():
                    if not workspace_chat or target.read_text(encoding="utf-8") != encoded:
                        raise ValueError("Existing synthetic profile bytes differ; no repair or overwrite")
                else:
                    with target.open("x", encoding="utf-8") as stream:
                        stream.write(encoded)
            record(omp_launch_id=result["launch_id"], omp_runtime_sha256=result["runtime_sha256"],
                   bridge_sha256=result["bridge"]["extension_sha256"], omp_profile=str(profile_dir),
                   omp_session_directory=result["session_directory"], omp_working_directory=result["working_directory"],
                   omp_released=False, omp_launches=[*receipt["omp_launches"], result["launch_id"]])
        if method == "release_omp":
            record(omp_released=result.get("released") is True)
        return result

    try:
        with ExitStack() as stack:
            allowed_environment = {
                "PATH", "SYSTEMROOT", "WINDIR", "OS", "PROCESSOR_ARCHITECTURE", "PROCESSOR_ARCHITEW6432",
                "PATHEXT", "COMSPEC", "NUMBER_OF_PROCESSORS", "PROGRAMFILES", "PROGRAMFILES(X86)",
                "PROGRAMDATA", "USERPROFILE", "HOME", "APPDATA", "LOCALAPPDATA", "TEMP", "TMP",
                "DOTNET_ROOT_X64", "DOTNET_ROOT",
            }
            synthetic_environment = {key: value for key, value in os.environ.items() if key.upper() in allowed_environment}
            synthetic_environment["PI_NO_TITLE"] = "true"
            stack.enter_context(patch.dict(os.environ, synthetic_environment, clear=True))
            stack.enter_context(patch.object(omp, "OmpTui", side_effect=owned_tui))
            stack.enter_context(patch.object(hwp_pi_chat, "call_chat", side_effect=reject_sdk))
            stack.enter_context(patch.object(os, "startfile", side_effect=open_copy))
            stack.enter_context(patch.object(subprocess, "Popen", side_effect=observed_popen))
            stack.enter_context(patch.object(native.NativeHost, "dispatch", bounded_dispatch))
            code = native.run(workspace=work_folder if workspace_first else source.parent, base=base)
        if (receipt["provider_waiting"] is not None or receipt["status"] == "failed_preserved"
                or receipt["native_background_calls"] != receipt["native_background_responses"]):
            raise RuntimeError("Provider or stopped scope remains unresolved; preserve host")
        provider.shutdown()
        provider.server_close()
        provider_thread.join(timeout=5)
        if provider_thread.is_alive():
            raise RuntimeError("Owned synthetic provider did not stop; no forced cleanup")
        record(status="closed", ui_exit_code=code,
               owned_server_stopped=all(not host.session.running and host.pending_session is None for host in hosts))
        return code
    except Exception as error:
        record(status="failed_preserved", error=type(error).__name__ + ": " + str(error))
        if sys.stdout is not None:
            print("SYNTHETIC_NATIVE_OMP_STOPPED_PRESERVED", flush=True)
        threading.Event().wait()
