"""Bounded SDK process transport and strict no-model probe; no CLI/document API."""

import hashlib
import json
import os
from pathlib import Path
import secrets
import subprocess
import tempfile
import threading
import time

from hwp_agents import path_directories
from hwp_resources import resource_root

SDK_VERSIONS = ("1.2.0", "1.3.0", "1.4.0")
TIMEOUT = 30
OUTPUT_LIMIT = 32768


class ProbeFailure(RuntimeError):
    def __init__(self, stage):
        super().__init__("Pi 무모델 검사 차단: " + stage + " · 자동 재시도 없음")
        self.stage = stage


def file_digest(path, limit=256 * 1024 * 1024):
    if path.is_symlink() or not path.is_file() or not 0 < path.stat().st_size <= limit:
        raise ProbeFailure("runtime_binding")
    digest = hashlib.sha256()
    size = 0
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(65536), b""):
            size += len(block)
            if size > limit:
                raise ProbeFailure("runtime_binding")
            digest.update(block)
    return digest.hexdigest()


def resolve_runtime(cli_path):
    cli = Path(cli_path).resolve(strict=True)
    if os.name != "nt" or cli.name.casefold() != "pi.cmd":
        raise ProbeFailure("unsupported_layout")
    if cli.stat().st_size > 4096 or cli.read_text(encoding="utf-8").strip().replace(
        "\r\n", "\n"
    ).lower() not in (
        '@echo off\nnode "%~dp0pi-launcher.js" %*',
        '@echo off\nbun --smol --no-env-file --no-install --config=nul "%~dp0pi-launcher.js" %*',
        r'''@echo off
setlocal disabledelayedexpansion
set "errorlevel="
set "bun_exe=%userprofile%\.bun\bin\bun.exe"
if not exist "%bun_exe%" (
  echo pi requires bun at "%bun_exe%". 1>&2
  exit /b 1
)
"%bun_exe%" --smol --no-env-file --no-install --config=nul "%~dp0pi-launcher.js" %*
endlocal & exit /b %errorlevel%''',
    ):
        raise ProbeFailure("unsupported_layout")
    install = cli.parent.parent / "install"
    current = install / "current-version"
    file_digest(current, 128)
    sdk_version = current.read_text(encoding="utf-8").strip()
    if sdk_version not in SDK_VERSIONS:
        raise ProbeFailure("unsupported_version")
    package = (
        install
        / "releases"
        / sdk_version
        / "node_modules/@earendil-works/pi-coding-agent"
    ).resolve(strict=True)
    metadata = package / "package.json"
    file_digest(metadata, 65536)
    try:
        data = json.loads(metadata.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, ValueError):
        raise ProbeFailure("runtime_binding") from None
    if not isinstance(data, dict):
        raise ProbeFailure("runtime_binding")
    if (
        data.get("name") != "@earendil-works/pi-coding-agent"
        or data.get("version") != sdk_version
        or data.get("main") != "./dist/index.js"
    ):
        raise ProbeFailure("unsupported_version")
    sdk = (package / "dist/index.js").resolve(strict=True)
    if not sdk.is_relative_to(package):
        raise ProbeFailure("runtime_binding")
    directories, _ = path_directories()
    node = next(
        (
            folder / "node.exe"
            for folder in directories
            if (folder / "node.exe").is_file()
        ),
        None,
    )
    if node is None:
        raise ProbeFailure("node_missing")
    node = node.resolve(strict=True)
    helper = (resource_root() / "scripts/pi_probe.mjs").resolve(strict=True)
    files = (cli, cli.parent / "pi-launcher.js", current, metadata, sdk, node, helper)
    hashes = {str(path): file_digest(path) for path in files}
    runtime = {
        "cli": str(cli),
        "node": str(node),
        "sdk_entry": str(sdk),
        "helper": str(helper),
        "sdk_version": sdk_version,
        "files_sha256": hashes,
    }
    runtime["sha256"] = hashlib.sha256(
        json.dumps(
            runtime, sort_keys=True, ensure_ascii=False, separators=(",", ":")
        ).encode()
    ).hexdigest()
    return runtime


def child_environment(sandbox, node):
    # No inherited API keys, NODE_OPTIONS, user settings or current-project paths.
    root = os.environ.get("SystemRoot", r"C:\Windows")
    return {
        "SystemRoot": root,
        "WINDIR": root,
        "PATH": str(Path(node).parent) + os.pathsep + str(Path(root) / "System32"),
        "HOME": str(sandbox),
        "USERPROFILE": str(sandbox),
        "TEMP": str(sandbox),
        "TMP": str(sandbox),
        "APPDATA": str(sandbox / "roaming"),
        "LOCALAPPDATA": str(sandbox / "local"),
        "PI_CODING_AGENT_DIR": str(sandbox / "profile"),
        "PI_OFFLINE": "1",
        "PI_SKIP_VERSION_CHECK": "1",
    }


def decode_response(blob, request):
    frames = blob.split(b"\n")
    if len(frames) != 2 or frames[-1] or not frames[0] or len(blob) > OUTPUT_LIMIT:
        raise ProbeFailure("protocol")
    try:
        data = json.loads(frames[0].removesuffix(b"\r").decode("utf-8"))
    except (ValueError, UnicodeError):
        raise ProbeFailure("protocol") from None
    required = {
        "type",
        "id",
        "nonce",
        "success",
        "stage",
        "sdk_version",
        "node_version",
        "tools",
        "message_count",
        "streaming",
        "session_file",
        "attempts",
        "authenticated",
        "connected",
    }
    if (
        not isinstance(data, dict)
        or set(data) != required
        or not isinstance(data.get("node_version"), str)
        or not 0 < len(data["node_version"]) <= 128
        or data.get("type") != "sdk_probe_result"
        or data.get("id") != request["id"]
        or data.get("nonce") != request["nonce"]
    ):
        raise ProbeFailure("protocol")
    if (
        data.get("success") is not True
        or data.get("stage") != "passed"
        or data.get("sdk_version") not in SDK_VERSIONS
        or data.get("tools") != []
        or type(data.get("message_count")) is not int
        or data["message_count"] != 0
        or data.get("streaming") is not False
        or data.get("session_file") is not None
        or data.get("authenticated") is not False
        or data.get("connected") is not False
    ):
        raise ProbeFailure("sdk_session")
    attempts = data.get("attempts")
    if (
        not isinstance(attempts, dict)
        or set(attempts) != {"network", "process", "model"}
        or any(type(v) is not int or v != 0 for v in attempts.values())
    ):
        raise ProbeFailure("forbidden_attempt")
    return {
        "sdk_version": data["sdk_version"],
        "transport_verified": True,
        "authenticated": False,
        "connected": False,
        "model_called": False,
        "tools_enabled": False,
        "document_shared": False,
        "process_exited": True,
    }


def run_sdk_command(
    runtime, cancel, request, *, helper_key, timeout, output_limit, on_stdout=None
):
    expected = runtime.get("base_sha256", runtime["sha256"])
    if resolve_runtime(runtime["cli"])["sha256"] != expected:
        raise ProbeFailure("runtime_changed")
    if helper_key not in ("helper", "chat_helper"):
        raise ProbeFailure("runtime_binding")
    if (
        helper_key == "chat_helper"
        and file_digest(Path(runtime[helper_key])) != runtime["chat_helper_sha256"]
    ):
        raise ProbeFailure("runtime_changed")
    encoded = json.dumps(request, ensure_ascii=False).encode("utf-8") + b"\n"
    if len(encoded) > 1200000:
        raise ProbeFailure("input_limit")
    with tempfile.TemporaryDirectory(prefix="HWP-Pi-Session-") as temp:
        sandbox = Path(temp)
        process = None
        readers = []
        output = [bytearray(), bytearray()]
        overflow = threading.Event()
        pipe_failed = threading.Event()
        callback_failed = threading.Event()

        def capture(stream, target, limit):
            try:
                read = (
                    stream.read1
                    if on_stdout is not None and target is output[0]
                    else stream.read
                )
                for block in iter(lambda: read(4096), b""):
                    if len(target) + len(block) > limit:
                        overflow.set()
                        if process is not None and process.poll() is None:
                            process.terminate()
                        break
                    target.extend(block)
                    if on_stdout is not None and target is output[0]:
                        try:
                            on_stdout(block)
                        except Exception:
                            callback_failed.set()
                            if process is not None and process.poll() is None:
                                process.terminate()
                            break
            except (OSError, ValueError):
                pipe_failed.set()
            finally:
                try:
                    stream.close()
                except OSError:
                    pipe_failed.set()

        try:
            if cancel.is_set():
                raise ProbeFailure("cancelled")
            process = subprocess.Popen(
                [runtime["node"], runtime[helper_key], runtime["sdk_entry"]],
                cwd=sandbox,
                env=child_environment(sandbox, runtime["node"]),
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            )
            if (
                process.stdin is None
                or process.stdout is None
                or process.stderr is None
            ):
                raise ProbeFailure("pipes")
            for stream, target in zip((process.stdout, process.stderr), output):
                reader = threading.Thread(
                    target=capture,
                    args=(
                        stream,
                        target,
                        output_limit if target is output[0] else OUTPUT_LIMIT,
                    ),
                    daemon=True,
                    name="HWP-Pi-probe-pipe",
                )
                readers.append(reader)
                reader.start()
            process.stdin.write(encoded)
            process.stdin.close()
            deadline = time.monotonic() + timeout
            while True:
                if cancel.is_set():
                    raise ProbeFailure("cancelled")
                if time.monotonic() >= deadline:
                    raise ProbeFailure("timeout")
                try:
                    code = process.wait(timeout=0.1)
                    break
                except subprocess.TimeoutExpired:
                    continue
            for reader in readers:
                reader.join(2)
            if any(reader.is_alive() for reader in readers):
                raise ProbeFailure("pipe_shutdown")
            if overflow.is_set():
                raise ProbeFailure("output_limit")
            if callback_failed.is_set():
                raise ProbeFailure("chat_stream_protocol")
            if pipe_failed.is_set():
                raise ProbeFailure("pipe_read")
            if code != 0:
                raise ProbeFailure("process_exit")
            return bytes(output[0])
        except ProbeFailure:
            raise
        except Exception:
            raise ProbeFailure("probe_infrastructure") from None
        finally:
            if process is not None:
                if process.stdin is not None and not process.stdin.closed:
                    try:
                        process.stdin.close()
                    except OSError:
                        pass
                if process.poll() is None:
                    try:
                        process.terminate()
                    except OSError:
                        pass
                    try:
                        process.wait(timeout=2)
                    except subprocess.TimeoutExpired:
                        process.kill()
                        process.wait(timeout=2)
                for reader in readers:
                    reader.join(2)


def run_probe(runtime, cancel):
    request = {
        "type": "sdk_probe",
        "id": "p-" + secrets.token_hex(8),
        "nonce": secrets.token_hex(16),
    }
    blob = run_sdk_command(
        runtime,
        cancel,
        request,
        helper_key="helper",
        timeout=TIMEOUT,
        output_limit=OUTPUT_LIMIT,
    )
    result = decode_response(blob, request)
    if result["sdk_version"] != runtime["sdk_version"]:
        raise ProbeFailure("runtime_version")
    return result


class PiProbe:
    def __init__(self, discovery):
        self.discovery = discovery
        self.guard = threading.RLock()
        self.thread = None
        self.cancel = threading.Event()
        self.status = "idle"
        self.closed = False
        self.runtime = None
        self.probe_id = None
        self.generation = None
        self.stage = None
        self.result = None

    def snapshot(self):
        with self.guard:
            public = (
                {
                    key: self.runtime[key]
                    for key in ("cli", "node", "sdk_entry", "sdk_version", "sha256")
                }
                if self.runtime
                else None
            )
            return {
                "status": self.status,
                "probe_id": self.probe_id,
                "runtime": public,
                "stage": self.stage,
                "result": self.result.copy() if self.result else None,
                "connected": False,
                "authenticated": False,
                "model_called": False,
                "document_shared": False,
            }

    def _launch(self, target):
        self.thread = threading.Thread(
            target=target, daemon=True, name="HWP-Pi-no-model-check"
        )
        try:
            self.thread.start()
        except RuntimeError:
            self.status, self.stage = "failed", "thread_start"
        return self.snapshot()

    def prepare(self):
        with self.guard:
            if self.closed:
                raise ValueError("Pi check closed")
            if self.thread is not None and self.thread.is_alive():
                raise ValueError("Pi check already active; no repeat")
            presence = self.discovery.snapshot()
            candidates = [
                item
                for item in presence["agents"]
                if item["id"] == "pi" and item["status"] == "detected"
            ]
            if presence["status"] != "finished" or len(candidates) != 1:
                raise ValueError("Find the Pi path first; no execution")
            self.generation = presence["generation"]
            path = candidates[0]["path"]
            self.runtime = self.result = self.stage = None
            self.probe_id = secrets.token_hex(16)
            self.status = "preparing"
            self.cancel.clear()

            def prepare_runtime():
                try:
                    runtime = resolve_runtime(path)
                    with self.guard:
                        if not self.closed:
                            self.runtime, self.status = runtime, "prepared"
                except Exception as error:
                    with self.guard:
                        if not self.closed:
                            self.status, self.stage = (
                                "failed",
                                error.stage
                                if isinstance(error, ProbeFailure)
                                else "runtime_binding",
                            )

            return self._launch(prepare_runtime)

    def start(self, payload):
        with self.guard:
            if self.closed or self.status != "prepared" or self.runtime is None:
                raise ValueError("Prepare a fresh Pi runtime first")
            expected = {
                "probe_id": self.probe_id,
                "runtime_sha256": self.runtime["sha256"],
                "execute_confirmed": True,
            }
            if payload != expected or payload.get("execute_confirmed") is not True:
                raise ValueError("Exact Pi program execution confirmation required")
            presence = self.discovery.snapshot()
            if (
                presence["status"] != "finished"
                or presence["generation"] != self.generation
            ):
                raise ValueError("Pi discovery changed; prepare again")
            self.status = "probing"
            runtime = self.runtime
            self.cancel.clear()

            def probe_runtime():
                try:
                    result = run_probe(runtime, self.cancel)
                    with self.guard:
                        if not self.closed:
                            self.result, self.status = result, "passed"
                except Exception as error:
                    with self.guard:
                        if not self.closed:
                            self.status, self.stage = (
                                "failed",
                                error.stage
                                if isinstance(error, ProbeFailure)
                                else "probe_infrastructure",
                            )

            return self._launch(probe_runtime)

    def close(self):
        with self.guard:
            self.closed = True
            self.status = "closed"
            self.cancel.set()
        if self.thread is not None:
            self.thread.join(3)
