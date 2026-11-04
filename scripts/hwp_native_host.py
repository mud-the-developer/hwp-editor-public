"""Owned WPF UI supervisor; existing report HTTP remains the only authority UI.

The child receives a per-project loopback capability on inherited pipes, never
argv or files. This module captures/opens projects and delegates explicit blank
creation; it never writes permissions, acceptance, model output or Native plans.
"""

from __future__ import annotations

import ctypes
import hmac
import hashlib
import json
import os
from pathlib import Path
import re
import secrets
import stat
import subprocess
import sys
import threading

import hwp_report_project as projects
import hwp_resources as resources
import hwp_workspace as workspace

PROTOCOL = "hwp-native-ui-v1"
REQUEST_LIMIT = 1024 * 1024
RESPONSE_LIMIT = 2 * 1024 * 1024
ERROR_LIMIT = 6000


def _closed(value, names):
    if not isinstance(value, dict) or set(value) != set(names):
        raise ValueError("이 앱에서 지원하는 작업 항목만 사용할 수 있습니다.")


def _absolute(value):
    if (
        not isinstance(value, str) or not value or len(value) > 32760
        or any(ord(char) < 32 for char in value)
    ):
        raise ValueError("직접 선택한 실제 파일·작업 폴더 경로가 필요합니다.")
    path = Path(value)
    if not path.is_absolute() or ".." in path.parts:
        raise ValueError("선택한 실제 절대 경로만 사용할 수 있습니다.")
    return path


def _capture_material(source):
    """Capture one explicitly chosen GUI material; never an agent file tool."""
    from hwp_omp_tui import _open_read, _regular
    from hwp_report_control import _material_text

    path = _absolute(source)
    maximum = 64 * 1024 * 1024 if path.suffix.lower() == ".hwpx" else 512 * 1024
    before = _regular(path, maximum)
    with _open_read(path) as stream:
        opened = os.fstat(stream.fileno())
        raw = stream.read(maximum + 1)
        after = os.fstat(stream.fileno())
    final = _regular(path, maximum)

    def identity(info):
        return (info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns, info.st_nlink,
                getattr(info, "st_birthtime_ns", None))

    if (not stat.S_ISREG(opened.st_mode) or not raw or len(raw) > maximum
            or identity(before) != identity(opened) or identity(opened) != identity(after)
            or identity(after) != identity(final)
            or before.st_ctime_ns != final.st_ctime_ns or opened.st_ctime_ns != after.st_ctime_ns):
        raise ValueError("선택 자료가 읽는 동안 바뀌었거나 허용 크기를 벗어났습니다. 자동 재시도하지 않습니다.")
    text, kind = _material_text(raw, path.suffix.lower())
    return {
        "id": "m-" + secrets.token_hex(16), "name": path.name, "source_kind": kind,
        "source_sha256": hashlib.sha256(raw).hexdigest(), "sha256": hashlib.sha256(text).hexdigest(),
        "text": text.decode("utf-8"),
    }


def _parse(raw):
    def pairs(items):
        value = {}
        for key, item in items:
            if key in value:
                raise ValueError("중복된 작업 항목을 사용할 수 없습니다.")
            value[key] = item
        return value

    value = json.loads(raw, object_pairs_hook=pairs)
    _closed(value, ("id", "method", "params"))
    if (
        not isinstance(value["id"], str)
        or re.fullmatch(r"[A-Za-z0-9_-]{1,100}", value["id"]) is None
        or not isinstance(value["method"], str)
        or not isinstance(value["params"], dict)
    ):
        raise ValueError("올바른 네이티브 작업 식별자가 필요합니다.")
    return value


class NativeHost:
    """One visible project, with explicit idle-only project and UI handoff."""

    def __init__(self, nonce, *, base=None, suggested_root=None):
        if re.fullmatch(r"[a-f0-9]{64}", nonce) is None:
            raise ValueError("새 소유 UI 연결 식별자가 필요합니다.")
        from hwp_desktop import ReviewSession

        self.nonce = nonce
        self.base = Path(base).absolute() if base is not None else None
        self.suggested_root = suggested_root
        self.workspace_root = None
        self.runtime_identity = projects.harness.runtime.identity()
        self.session = ReviewSession()
        self.pending_session = None
        self.connected = False
        self.closed = False
        self.ownership_error = ""
        self.omp = None

    def _ready(self):
        if not self.connected:
            raise ValueError("이 앱이 시작한 네이티브 창의 연결을 먼저 확인하세요.")
        if self.closed:
            raise ValueError("이미 닫힌 네이티브 작업입니다.")
        if self.ownership_error:
            raise RuntimeError(self.ownership_error)

    def _idle(self, *, initial_intake=False):
        # Only first document intake may retain the app's rootless OMP launch.
        # A missing info alone is not proof that a partially stopped server is gone.
        initial = (
            initial_intake and self.session.info is None
            and not self.session.running and not self.session.stopping
            and self.session._server is None and self.pending_session is None
        )
        if self.omp is not None and self.omp.launch is not None and not initial:
            raise ValueError("이 앱의 OMP 터미널이 준비되어 있습니다. 터미널 안에서 종료한 뒤 해제하세요. 강제 종료하지 않습니다.")
        if self.session.stopping:
            raise ValueError("이 앱의 서버 종료 확인이 아직 끝나지 않았습니다.")
        if self.session.info is None and (
            self.session.running or self.session._server is not None or self.pending_session is not None
        ):
            raise ValueError("이 앱의 초기 서버 소유 상태를 확인하지 못했습니다. 기존 실행과 작업을 보존합니다.")
        server = self.session._server
        control = getattr(server, "report_control", None)
        if control is not None:
            control.require_idle()

    def descriptor(self):
        info = self.session.info
        if info is None or not self.session.running:
            raise ValueError("먼저 HWPX 보고서 작업을 여세요.")
        token = getattr(self.session._server, "template_review_token", "")
        if re.fullmatch(r"[a-f0-9]{64}", token) is None:
            raise RuntimeError("이 보고서 UI 서버의 연결을 확인할 수 없습니다.")
        return {
            "root": str(info.root), "name": info.name,
            "origin": self.session.origin, "token": token,
            "kind": "document", "product": "hwpx-authoring",
        }

    def _open(self, info, *, initial_intake=False):
        if not info.document_first:
            raise ValueError("준비된 원고 프로젝트는 기존 고급 도구에서 여세요. 자동으로 변환하거나 권한을 이관하지 않습니다.")
        if not info.gate_passed:
            raise ValueError("보고서 작업을 열 수 없습니다: " + "; ".join(info.issues))
        self._idle(initial_intake=initial_intake)
        if self.session.running and self.session.info.root == info.root:
            return self.descriptor()
        from hwp_desktop import ReviewSession

        # Keep the old UI usable if validating/starting the new read-only server
        # fails. The new token is not handed off before the old session stops.
        incoming = ReviewSession()
        incoming.start(info.root)
        try:
            self.session.stop()
        except Exception as error:
            # Retain BOTH owned servers when cleanup is uncertain. No hidden
            # retry/kill/new write or capability handoff after this first fault.
            self.pending_session = incoming
            self.ownership_error = (
                "작업 전환 중 서버 종료를 확인하지 못했습니다. 두 작업과 프로세스를 보존합니다. "
                "자동 재시도하거나 새 요청을 보내지 않습니다: " + str(error)
            )[:ERROR_LIMIT]
            raise RuntimeError(self.ownership_error) from error
        self.session = incoming
        return self.descriptor()

    def dispatch(self, method, params):
        if method == "hello":
            _closed(params, ("protocol", "nonce"))
            if (
                self.connected or self.closed
                or params["protocol"] != PROTOCOL
                or not isinstance(params["nonce"], str)
                or not hmac.compare_digest(params["nonce"], self.nonce)
            ):
                raise ValueError("이 앱이 시작한 새 WPF 창의 연결과 맞지 않습니다.")
            self.connected = True
            result = {
                "protocol": PROTOCOL, "nonce": self.nonce,
                "version": resources.APP_VERSION, "product": "hwpx-authoring",
                "legacy_tools_qualified": False,
            }
            if self.suggested_root is not None:
                result["suggested_root"] = str(self.suggested_root)
            return result
        self._ready()
        if method == "prepare_omp":
            _closed(params, ())
            self._idle()
            if projects.harness.runtime.identity() != self.runtime_identity:
                raise ValueError("앱 실행 파일·소스·규칙이 변경되었습니다. 새 앱에서 시작하세요.")
            if self.omp is None:
                from hwp_omp_tui import OmpTui

                self.omp = OmpTui(self.runtime_identity)
            return self.omp.prepare(workspace=self.workspace_root)
        if method == "release_omp":
            _closed(params, ("launch_id",))
            if self.omp is None:
                raise ValueError("이 앱이 준비한 OMP 터미널이 없습니다.")
            return self.omp.release(params["launch_id"])
        if method == "prepare_material":
            _closed(params, ("source",))
            return _capture_material(params["source"])
        if method in (
            "new_report", "open_report", "open_workspace",
            "open_workspace_document", "new_blank_report",
        ) and projects.harness.runtime.identity() != self.runtime_identity:
            raise ValueError("이 창이 시작된 뒤 실행 파일·소스·규칙이 바뀌었습니다. 이전 창과 작업을 보존하고 새 앱에서 시작하세요.")
        if method == "list_projects":
            _closed(params, ())
            listing = workspace.scan_library(self.base)
            rows = [
                {"root": str(item.root), "name": item.name,
                 "status": workspace.STATES[item.state], "detail": item.note}
                for item in listing.entries
                if os.path.lexists(item.root / projects.PROJECT_FILE)
            ]
            return {
                "projects": rows,
                "issues": ["목록 한도에 도달했습니다. 열기에서 작업 폴더를 직접 선택하세요."] if listing.limited else [],
            }
        if method == "list_workspace":
            _closed(params, ())
            return workspace.scan_workspace(self.workspace_root)
        if method == "open_workspace":
            _closed(params, ("root",))
            self._idle(initial_intake=True)
            root = _absolute(params["root"])
            listing = workspace.scan_workspace(root)
            root = Path(listing["root"])
            if root != self.workspace_root:
                try:
                    self.session.stop()
                except Exception as error:
                    self.ownership_error = (
                        "작업 폴더 전환 중 기존 서버 종료를 확인하지 못했습니다. 기존 작업과 프로세스를 보존합니다. "
                        "자동 재시도하지 않습니다: " + str(error)
                    )[:ERROR_LIMIT]
                    raise RuntimeError(self.ownership_error) from error
                self.workspace_root = root
            return listing
        if method in ("open_workspace_document", "new_blank_report"):
            key = "source" if method == "open_workspace_document" else "destination"
            _closed(params, (key,))
            self._idle(initial_intake=True)
            if self.workspace_root is None:
                raise ValueError("먼저 기존의 실제 작업 폴더를 선택하세요.")
            path = _absolute(params[key])
            if method == "open_workspace_document":
                info = workspace.open_workspace_document(self.workspace_root, path)
            else:
                info = workspace.create_blank_document(self.workspace_root, path)
            return self._open(info, initial_intake=True)
        if method == "new_report":
            _closed(params, ("source",))
            self._idle(initial_intake=True)
            source = _absolute(params["source"])
            info = (
                workspace._open_explicit_workspace_document(self.workspace_root, source)
                if self.workspace_root is not None else projects.new_document(source, base=self.base)
            )
            return self._open(info, initial_intake=True)
        if method == "open_report":
            _closed(params, ("root",))
            self._idle(initial_intake=True)
            return self._open(projects.inspect(_absolute(params["root"])), initial_intake=True)
        if method == "legal":
            _closed(params, ())
            return {
                "text": resources.legal_notice(resources.resource_root()),
                "version": resources.APP_VERSION, "source_url": resources.SOURCE_URL,
            }
        if method in ("legacy_tools", "setup_requirements"):
            _closed(params, ())
            self._idle()
            command = [sys.executable]
            if not resources.frozen():
                command.extend(("-X", "utf8", str(Path(__file__).with_name("hwp_desktop.py"))))
            command.append("--legacy-desktop" if method == "legacy_tools" else "--setup-requirements")
            environment = None
            if resources.frozen():
                environment = dict(os.environ)
                environment["PYINSTALLER_RESET_ENVIRONMENT"] = "1"
            subprocess.Popen(command, shell=False, close_fds=True, env=environment)
            if method == "legacy_tools":
                return {"launched": True, "qualified_for_v1": False}
            return {"launched": True}
        if method in ("close_report", "shutdown"):
            _closed(params, ())
            self._idle()
            self.session.stop()
            if method == "shutdown":
                self.closed = True
            return {"closed": True}
        raise ValueError("이 앱이 지원하는 고정 작업만 요청할 수 있습니다.")

    def close_after_ui_exit(self):
        """EOF is not consent to cancel, abandon or restart an active request."""
        if self.ownership_error:
            raise RuntimeError(self.ownership_error)
        if self.closed:
            return
        if self.omp is not None and self.omp.launch is not None:
            raise RuntimeError("UI 종료 후 OMP 소유 프로세스의 종료·해제를 확인하지 못했습니다. 작업·파일·서버를 보존하며 강제 종료하지 않습니다.")
        server = self.session._server
        control = getattr(server, "report_control", None)
        if control is not None:
            # A crashed UI must not turn its authorized background work into a
            # killed daemon. Wait once for these exact existing threads only.
            for thread in (control.thread, control.chat.thread):
                if thread is not None and thread.is_alive():
                    thread.join()
        self.session.stop()
        self.closed = True


def _reply(stream, identifier, *, result=None, error=None):
    response = {"id": identifier, "ok": error is None}
    if error is None:
        response["result"] = result
    else:
        response["error"] = str(error)[:ERROR_LIMIT]
    raw = json.dumps(response, ensure_ascii=False, allow_nan=False).encode("utf-8") + b"\n"
    if len(raw) > RESPONSE_LIMIT:
        raise ValueError("네이티브 응답이 지원 크기를 넘었습니다.")
    stream.write(raw)
    stream.flush()


def serve(host, reader, writer):
    """Serve only this inherited UI channel; never discover or attach clients."""
    while not host.closed:
        raw = reader.readline(REQUEST_LIMIT + 1)
        if not raw:
            break
        if len(raw) > REQUEST_LIMIT or not raw.endswith(b"\n"):
            raise ValueError("네이티브 작업 요청이 크기 또는 줄 경계를 벗어났습니다.")
        request = _parse(raw.decode("utf-8"))
        try:
            result = host.dispatch(request["method"], request["params"])
        except Exception as error:
            _reply(writer, request["id"], error=error)
        else:
            _reply(writer, request["id"], result=result)


def _drain_errors(stream, captured):
    while block := stream.read(4096):
        remaining = ERROR_LIMIT - len(captured)
        if remaining > 0:
            captured.extend(block[:remaining])


def ui_environment():
    """Forward OS/UI locations, never ambient .NET startup/profile extensions."""
    environment = {
        name: os.environ[name] for name in (
            "SystemRoot", "WINDIR", "OS", "PROCESSOR_ARCHITECTURE",
            "PROCESSOR_ARCHITEW6432", "NUMBER_OF_PROCESSORS", "SystemDrive",
            "ProgramFiles", "ProgramFiles(x86)", "ProgramW6432", "ProgramData",
            "USERPROFILE", "APPDATA", "LOCALAPPDATA", "HOMEDRIVE", "HOMEPATH",
            "TEMP", "TMP", "USERNAME", "USERDOMAIN", "SESSIONNAME",
        ) if name in os.environ
    }
    windows = Path(environment.get("SystemRoot", r"C:\Windows"))
    environment["PATH"] = str(windows / "System32") + os.pathsep + str(windows)
    environment["DOTNET_CLI_TELEMETRY_OPTOUT"] = "1"
    environment["DOTNET_NOLOGO"] = "1"
    return environment


def run(workspace=None, *, base=None):
    """Start verified WPF, then supervise its exact pipe/server/child lifetime."""
    from hwp_native_payload import locate_dotnet, locate_payload

    payload = locate_payload()
    try:
        dotnet = locate_dotnet()
    except (OSError, ValueError) as error:
        raise ValueError(
            "이미 설치된 x64 .NET 10 Desktop Runtime이 필요합니다. "
            "앱은 런타임을 다운로드·설치하거나 다른 실행기로 재시도하지 않습니다.\n" + str(error)
        ) from error
    nonce = secrets.token_hex(32)
    host = NativeHost(nonce, base=base, suggested_root=workspace)
    child = subprocess.Popen(
        [str(dotnet), str(payload / "HWPReview.Native.dll"), "--owned-host", nonce],
        shell=False, cwd=payload, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
        stderr=subprocess.PIPE, close_fds=True,
        creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
        env=ui_environment(),
    )
    captured = bytearray()
    errors = threading.Thread(
        target=_drain_errors, args=(child.stderr, captured),
        name="HWP-native-ui-errors", daemon=True,
    )
    errors.start()
    try:
        channel_error = None
        try:
            serve(host, child.stdout, child.stdin)
        except Exception as error:
            channel_error = error
        try:
            host.close_after_ui_exit()
        except Exception as cleanup_error:
            if channel_error is not None:
                channel_error.add_note("Owned cleanup also needs inspection: " + str(cleanup_error))
                raise channel_error from cleanup_error
            raise
        if channel_error is not None:
            raise channel_error
        try:
            code = child.wait(timeout=20)
        except subprocess.TimeoutExpired as error:
            raise RuntimeError("WPF 창 종료를 확인하지 못했습니다. 프로세스를 보존하며 강제 종료하지 않습니다.") from error
        errors.join(timeout=2)
        if code != 0:
            details = captured.decode("utf-8", errors="replace").strip()
            raise RuntimeError(
                "Windows 창이 정상적으로 시작·종료되지 않았습니다. x64 .NET 10 Desktop Runtime 설치 여부를 확인하세요. "
                "앱은 런타임을 다운로드·설치하지 않습니다. " + details
            )
    finally:
        # A live child/server is retained on uncertain ownership. Closing its
        # pipe or killing it would erase the failure or abandon approved work.
        if child.poll() is not None and host.closed:
            for stream in (child.stdin, child.stdout, child.stderr):
                stream.close()
    return 0


def show_startup_error(error):
    message = str(error)[:ERROR_LIMIT]
    if sys.platform == "win32":
        ctypes.windll.user32.MessageBoxW(None, message, "HWP Editor 시작 확인", 0x10)
    elif sys.stderr is not None:
        print(message, file=sys.stderr)
