"""Windows-native HWPX authoring and retained advanced HWP tools.

Normal startup owns one WPF window. Explicit advanced entry opens the local
Tk HWP review tools; approvals and applies still use the owned review handler.
"""

from __future__ import annotations

import argparse
from dataclasses import dataclass
import http.client
import os
from pathlib import Path
import queue
import sys
import threading
import time
import tkinter as tk
from tkinter import filedialog, messagebox, scrolledtext, ttk
from typing import TYPE_CHECKING

from hwp_resources import legal_notice, resource_root
from hwp_legacy_ui import KINDS as MODES, LegacyReviewWindow
import hwp_project

if TYPE_CHECKING:
    from hwp_report_project import ReportProjectInfo

ROOT = resource_root()


def engine():
    # Lazy: missing project dependencies become a readable hub error, not silent
    # failure before a pythonw window can explain it. Import never starts Hancom.
    import hwp_review

    return hwp_review


def report_engine():
    # Same lazy-dependency convention as the HWP engine; import starts no job.
    import hwp_report_project

    return hwp_report_project


def is_report_info(info):
    return getattr(info, "kind", None) == "report"


@dataclass(frozen=True)
class WorkspaceInfo:
    path: Path
    workspace_id: str
    name: str
    pages: int
    tables: int
    images: int
    protected_pages: int
    has_fill_input: bool
    locked: bool


def inspect_workspace(path: Path) -> WorkspaceInfo:
    """Read-only checks. Never create/repair a workspace, lock, or consent file."""
    path = path.expanduser().resolve(strict=True)
    if not path.is_dir() or not (path / "manifest.json").is_file():
        raise ValueError(
            "준비된 검토 작업 폴더를 선택하세요. manifest.json이 있는 폴더입니다."
        )
    review = engine()
    manifest = review.verify_baseline(path)
    if (
        not isinstance(manifest.get("workspace_id"), str)
        or not manifest["workspace_id"]
    ):
        raise ValueError("작업 식별자가 없습니다. 새 검토 자료 준비가 필요합니다.")
    pages = manifest.get("pages")
    if not isinstance(pages, list) or not pages:
        raise ValueError("출력된 페이지 정보가 없습니다. 검토 자료 준비를 확인하세요.")
    for number, page in enumerate(pages, 1):
        if (
            not isinstance(page, dict)
            or type(page.get("page")) is not int
            or page["page"] != number
        ):
            raise ValueError(
                "페이지 순서가 올바르지 않습니다. 자료를 임의로 고치지 마세요."
            )
        if not (path / "pages" / f"page-{number:03}.png").is_file():
            raise ValueError(
                f"{number}쪽 미리보기가 없습니다. 이전 작업 상태를 확인하세요."
            )
    comments = review.read_json(path / "comments.json")
    review.validate_comments(comments, manifest)
    objects = manifest.get("objects", {})
    counts = [objects.get("tbl", 0), objects.get("pic", 0)]
    if any(type(count) is not int or count < 0 for count in counts):
        raise ValueError("문서의 표·그림 정보가 올바르지 않습니다.")
    return WorkspaceInfo(
        path,
        manifest["workspace_id"],
        Path(manifest["source"]).name,
        len(pages),
        counts[0],
        counts[1],
        len(manifest.get("protected_pages", [])),
        (path / "fill-input.json").is_file(),
        review.apply_state(path, manifest).get("locked") is not False,
    )



class ReviewSession:
    """Own precisely one Python HTTPServer/thread, NEVER other PIDs or Hancom."""

    def __init__(self):
        self._lock = threading.RLock()
        self._server = None
        self._thread = None
        self._failure = ""
        self._shutdown_thread = None
        self._shutdown_error = ""
        self.info = None

    @property
    def stopping(self) -> bool:
        # Do not acquire the lock here: Tk must remain responsive while the
        # worker owns the lock and waits for this exact server to finish.
        return self._server is not None and self._shutdown_thread is not None

    @property
    def running(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    @property
    def failure(self) -> str:
        return self._failure

    @property
    def origin(self) -> str:
        server = self._server
        if not self.running or server is None:
            raise RuntimeError("검토 서버가 열려 있지 않습니다.")
        return f"http://127.0.0.1:{server.server_port}"

    def start(self, path: Path) -> WorkspaceInfo | ReportProjectInfo:
        with self._lock:
            if self._server is not None:
                raise RuntimeError(
                    "현재 검토 서버를 먼저 닫고 새 작업 폴더를 선택하세요."
                )
            path = Path(path).expanduser().absolute()
            report = os.path.lexists(path / "report-project.json")
            if report:
                info = report_engine().inspect(path)
                if not info.gate_passed:
                    raise ValueError("보고서 작업이 차단되었습니다: " + "; ".join(info.issues))
                if info.document_first:
                    server = engine().create_report_document_server(info.root, 0)
                else:
                    if info.binding is None:
                        raise ValueError("준비된 원고의 고정 결합 정보를 확인하세요.")
                    server = engine().create_template_review_server(
                        info.review, 0, report_binding=info.binding
                    )
            else:
                info = inspect_workspace(path)
                server = engine().create_review_server(
                    info.path, 0, text_apply=True, contract_apply=True
                )
            self._server, self.info, self._failure = server, info, ""
            self._thread = threading.Thread(
                target=self._serve, args=(server,), name="HWP-review-server"
            )
            try:
                self._thread.start()
                # A fresh, bound socket alone is not readiness. Verify our exact
                # owned server before handing its URL to an external browser.
                connection = http.client.HTTPConnection(
                    "127.0.0.1", server.server_port, timeout=10
                )
                try:
                    if report:
                        token = getattr(server, "template_review_token", "")
                        if not token:
                            raise RuntimeError("다른 보고서 서버의 응답입니다.")
                        probe_path = "/api/report-state" if getattr(info, "document_first", False) else "/api/template-state"
                        connection.request("GET", probe_path, headers={"X-Review-Token": token})
                    else:
                        connection.request("GET", "/api/state")
                    response = connection.getresponse()
                    maximum = 16 * 1024 * 1024
                    data = response.read(maximum + 1)
                    if response.status != 200 or len(data) > maximum:
                        raise RuntimeError("검토 서버의 자료 확인이 실패했습니다.")
                    state = engine().parse_json(data)
                    if report:
                        if not isinstance(state, dict) or not state.get("revision"):
                            raise RuntimeError("다른 보고서 서버의 응답입니다.")
                    else:
                        if state.get("manifest", {}).get("workspace_id") != info.workspace_id:
                            raise RuntimeError("다른 작업의 서버 응답입니다.")
                finally:
                    connection.close()
            except Exception:
                self.stop()
                raise
            return info

    def _serve(self, server):
        try:
            server.serve_forever(poll_interval=0.1)
        except Exception as error:
            self._failure = str(error)

    def legacy_client(self):
        from hwp_legacy_client import LegacyReviewClient

        with self._lock:
            info, server = self.info, self._server
            if not isinstance(info, WorkspaceInfo) or server is None or not self.running or self.stopping:
                raise ValueError("기존 HWP 검토 작업에 연결하지 못했습니다.")
            return LegacyReviewClient(server.server_port, server.review_token, info.path, info.workspace_id)

    def stop(self, timeout: float = 20):
        if timeout <= 0:
            raise ValueError("Shutdown timeout must be positive")
        with self._lock:
            server, thread = self._server, self._thread
            if server is None:
                return
            for name in ("text_apply_controller", "contract_apply_controller", "report_control"):
                controller = getattr(server, name, None)
                if controller is not None:
                    controller.require_idle()
            deadline = time.monotonic() + timeout
            if thread and thread.is_alive() and self._shutdown_thread is None:
                # HTTPServer.shutdown itself waits for the active serial request.
                # Joining only AFTER shutdown does not bound that wait. Keep one
                # owned coordinator, never kill a PID/socket or auto-retry work.
                def shutdown():
                    try:
                        server.shutdown()
                    except Exception as error:
                        self._shutdown_error = str(error)

                self._shutdown_thread = threading.Thread(
                    target=shutdown, name="HWP-owned-server-shutdown", daemon=True
                )
                self._shutdown_thread.start()
            if self._shutdown_thread:
                self._shutdown_thread.join(timeout=max(0, deadline - time.monotonic()))
                if self._shutdown_thread.is_alive():
                    raise RuntimeError(
                        "이 앱의 검토 요청이 아직 끝나지 않았습니다. 기다린 뒤 ‘내 검토 서버 닫기’를 다시 누르세요. 강제 종료하지 않습니다."
                    )
                if self._shutdown_error:
                    raise RuntimeError("내 서버 종료 오류: " + self._shutdown_error)
            if thread and thread.ident is not None:
                thread.join(timeout=max(0, deadline - time.monotonic()))
                if thread.is_alive():
                    raise RuntimeError(
                        "이 앱의 검토 요청이 아직 끝나지 않았습니다. 기다린 뒤 다시 닫으세요."
                    )
            server.server_close()
            self._server = self._thread = self.info = self._shutdown_thread = None
            self._shutdown_error = ""



class DesktopApp:
    # build_ui initializes these widgets synchronously before any callback. Keep
    # the builder's explicit contract visible to type checking, not dynamic Any.
    canvas: tk.Canvas
    step_labels: list[tk.Label]
    document_title: tk.StringVar
    summary: tk.StringVar
    mode: tk.StringVar
    mode_help: tk.StringVar
    status: tk.StringVar
    elapsed: tk.StringVar
    error_hint: tk.StringVar
    path: tk.StringVar
    review_options: ttk.Frame
    primary_actions: ttk.Frame
    error_panel: ttk.Frame
    details: ttk.Frame
    mode_picker: ttk.Combobox
    progress: ttk.Progressbar
    entry: ttk.Entry
    new_button: ttk.Button
    action_menu: tk.Menu
    add_button: ttk.Button
    prepare_button: ttk.Button
    open_button: ttk.Button
    export_button: ttk.Button
    cancel_button: ttk.Button
    choose: ttk.Button
    check: ttk.Button
    library_button: ttk.Button
    stop_button: ttk.Button
    version_label: ttk.Label

    def __init__(
        self,
        root: tk.Tk,
        workspace: Path | None = None,
        session: ReviewSession | None = None,
    ):
        self.root, self.session = root, session or ReviewSession()
        self.review_window = None
        self.events = queue.Queue()
        self.busy = self.closing = False
        self.info = None
        self.project = None
        self.last_error = ""
        self.preparing = False
        self.library_window = None
        self.task_role = "document"
        self.cancel_requested = threading.Event()
        self.task_started = 0
        self.stage_message = ""
        from hwp_desktop_ui import build_ui

        build_ui(self, root, workspace, MODES)
        self.path.trace_add("write", self.path_changed)
        root.report_callback_exception = self.callback_error
        root.protocol("WM_DELETE_WINDOW", self.close)
        root.bind(
            "<Return>",
            lambda event: (
                self.check_workspace() if event.widget is self.entry else None
            ),
        )
        root.bind("<Control-o>", lambda event: self.new_document())
        root.bind("<Control-Shift-O>", lambda event: self.choose_folder())
        root.bind("<FocusIn>", self.reveal_focus)
        self.update_controls()
        root.after(100, self.poll)
        if workspace is not None:
            root.after(150, self.check_workspace)

    def scroll_wheel(self, event):
        if event.widget.winfo_toplevel() is not self.root:
            return
        self.wheel_delta += event.delta
        units = int(self.wheel_delta / 120)
        if units:
            self.wheel_delta -= units * 120
            self.canvas.yview_scroll(-units, "units")
        return "break"

    def callback_error(self, error_type, error, traceback):
        # pythonw/frozen builds have no stderr. Keep bounded LOCAL memory only,
        # no document paths/log files/upload and no automatic retry/consent.
        self.last_error = (error_type.__name__ + ": " + str(error))[:6000]
        self.status.set(
            "화면 오류가 발생했습니다. ‘오류 자세히’에서 이유를 확인하세요."
        )
        self.update_controls()

    def reveal_focus(self, event):
        # Keyboard users can reach controls below the fold on small/high-DPI screens.
        widget = event.widget
        if widget is self.root or widget.winfo_toplevel() is not self.root:
            return

        def reveal():
            if not widget.winfo_exists():
                return
            top = widget.winfo_rooty() - self.canvas.winfo_rooty()
            bottom = top + widget.winfo_height()
            height = self.canvas.winfo_height()
            bounds = self.canvas.bbox("all")
            if bounds and bounds[3] > height:
                delta = (
                    top - 8
                    if top < 0
                    else bottom - height + 8
                    if bottom > height
                    else 0
                )
                if delta:
                    self.canvas.yview_moveto(
                        max(0, (self.canvas.canvasy(0) + delta) / bounds[3])
                    )

        self.root.after_idle(reveal)

    def mode_key(self):
        return next((key for key, label in MODES.items() if label == self.mode.get()), "text")

    def same_path(self):
        selected = (
            self.project.root
            if self.project is not None
            else (self.info.path if self.info is not None else None)
        )
        return self.info is not None and self.path.get().strip() == str(selected)

    def same_project(self):
        return self.project is not None and self.path.get().strip() == str(
            self.project.root
        )

    def path_changed(self, *_):
        if (self.info is not None and not self.same_path()) or (
            self.project is not None and not self.same_project()
        ):
            self.info = self.project = None
            self.summary.set(
                "작업 폴더가 바뀌었습니다. ‘폴더 확인’을 눌러주세요. 문서를 자동으로 준비하지 않습니다."
            )
        self.update_controls()

    def show_library(self):
        if (
            self.busy
            or self.closing
            or self.session.running
            or getattr(self.session, "stopping", False)
        ):
            return
        if self.library_window is not None and self.library_window.exists():
            self.library_window.top.lift()
            return
        from hwp_library_ui import LibraryWindow

        self.library_window = LibraryWindow(self)
        self.update_controls()

    def load_library(self, base=None):
        from hwp_workspace import scan_library

        self.task(
            "로컬 작업 목록을 읽고 있습니다…",
            lambda: ("library", scan_library(base)),
            role="library",
        )

    def export_saved_notes(self):
        if (
            self.busy
            or self.closing
            or not self.same_path()
            or self.info is None
            or getattr(self.session, "stopping", False)
        ):
            return
        destination = filedialog.asksaveasfilename(
            parent=self.root,
            title="저장된 검토 메모만 내보내기 · 수정 승인이 아닙니다",
            defaultextension=".md",
            initialfile=Path(self.info.name).stem + "-검토메모.md",
            filetypes=[("검토 메모 Markdown", "*.md")],
        )
        if not destination:
            return
        workspace = self.info.path

        def operation():
            from hwp_workspace import export_notes

            return "exported", export_notes(workspace, Path(destination))

        self.task(
            "원본과 저장된 메모를 확인해 새 파일로 내보냅니다…",
            operation,
            role="export",
        )

    def toggle_details(self):
        if self.details.winfo_manager():
            self.details.grid_remove()
        else:
            self.details.grid()

    def reveal_project(self):
        if self.busy or not self.path.get().strip():
            return
        path = Path(self.path.get().strip()).resolve(strict=True)
        if not path.is_dir():
            raise ValueError("작업 폴더를 확인하세요.")
        if os.name == "nt":
            os.startfile(str(path))

    def new_document(self):
        if (
            self.busy
            or self.closing
            or self.session.running
            or getattr(self.session, "stopping", False)
        ):
            return
        source = filedialog.askopenfilename(
            parent=self.root,
            title="검토할 HWP 선택 · 원본은 그대로 보관합니다",
            filetypes=[("한글 HWP", "*.hwp")],
        )
        if not source:
            return

        def operation():
            return "checked", (hwp_project.new_project(Path(source)), None)

        self.task("작업용 사본을 만들고 있습니다. 원본은 변경하지 않습니다…", operation)

    def cancel_preparation(self):
        if not self.busy or not self.preparing or self.cancel_requested.is_set():
            return
        if not messagebox.askyesno(
            "안전하게 중단할까요?",
            "현재 한글 작업이 끝나면 다음 단계 전에 중단합니다. 원본과 이미 생성한 자료는 보존합니다. 한글 강제 종료·준비 자료 삭제·자동 재시도는 하지 않습니다.",
            parent=self.root,
        ):
            return
        self.cancel_requested.set()
        self.status.set("중단 요청됨 · 현재 한글 작업을 안전하게 마친 뒤 멈춥니다")
        self.update_controls()

    def render_state(self):
        self.open_button.configure(text="검토 시작")
        for label, text in zip(
            self.step_labels, ("1  문서 선택", "2  원본 확인", "3  검토 시작")
        ):
            label.configure(text=text)
        state = (
            "ready"
            if self.same_path() and self.info is not None
            else (
                self.project.state
                if self.same_project() and self.project is not None
                else "idle"
            )
        )
        if state == "ready" and self.info is not None:
            title = self.info.name
        elif (
            state == "staged"
            and self.project is not None
            and self.project.source is not None
        ):
            title = self.project.source.name
        elif state == "empty":
            title = "이 폴더에 검토할 HWP를 넣어주세요"
        else:
            title = "검토할 한글 문서를 선택하세요"
        self.document_title.set(title)
        active = (
            2 if state == "ready" else (1 if state == "staged" or self.preparing else 0)
        )
        for i, label in enumerate(self.step_labels):
            label.configure(
                background="#dbeafe" if i == active else "#e9edf4",
                foreground="#1d4ed8" if i == active else "#79869b",
            )
        for button in (
            self.new_button,
            self.add_button,
            self.prepare_button,
            self.open_button,
            self.export_button,
            self.cancel_button,
        ):
            button.pack_forget()
        button = {
            "idle": self.new_button,
            "empty": self.add_button,
            "staged": self.prepare_button,
            "ready": self.open_button,
        }[state]
        button.pack(side="left")
        if state == "ready":
            self.export_button.pack(side="left", padx=8)
        if self.preparing:
            self.cancel_button.pack(side="left", padx=8)
        if state == "ready":
            self.review_options.grid()
        else:
            self.review_options.grid_remove()
        if self.last_error and not self.busy:
            text = self.last_error
            if text.startswith("PreparationCancelled"):
                hint = "준비를 중단했습니다. 원본과 생성된 자료는 그대로입니다.\n다시 시작하려면 상태를 확인한 뒤 새 작업 폴더를 직접 선택하세요."
            elif "roundtrip" in text or "보존 검사" in text:
                hint = "이 문서는 원본 보존 검사를 통과하지 못했습니다.\n검토 준비를 차단하고 자료를 보존했습니다. 글꼴·표·쪽수를 바꿔 우회하지 않습니다."
            elif (
                "Security" in text
                or "security" in text
                or "COM registration" in text
                or "보안 모듈" in text
                or "bitness" in text
            ):
                hint = "한글 연결 환경을 확인해야 합니다.\n설치된 한글과 승인된 등록 보안 모듈이 필요합니다. 앱은 자동 설치하거나 레지스트리를 바꾸지 않습니다."
            elif "미완료" in text or "실패한 준비" in text:
                hint = "이 폴더에는 끝나지 않은 준비 자료가 있습니다.\n자동 삭제·재시도하지 않습니다. 원인을 확인하거나 별도의 새 작업 폴더를 선택하세요."
            elif "시간" in text or "Timeout" in text:
                hint = "한글의 응답을 기다리다 중단됐습니다.\n접근 대화상자와 한글 상태를 직접 확인하세요. 자료를 보존했으며 강제 종료하지 않았습니다."
            else:
                hint = "작업을 완료하지 못했습니다. 원본은 덮어쓰지 않습니다.\n기술 정보에서 원인을 확인하거나 작업 폴더를 다시 선택하세요."
            self.error_hint.set(hint)
            self.error_panel.grid()
        else:
            self.error_panel.grid_remove()

    def update_controls(self):
        stopping = getattr(self.session, "stopping", False)
        available = (
            self.same_path() and not self.busy and not self.closing and not stopping
        )
        fill_missing = (
            self.mode_key() == "fill" and self.info and not self.info.has_fill_input
        )
        self.open_button.configure(state="normal" if available and not fill_missing else "disabled")
        for widget in (self.entry, self.choose, self.check):
            widget.configure(
                state="disabled"
                if self.busy or self.closing or self.session.running or stopping
                else "normal"
            )
        self.check.configure(
            state="normal"
            if self.path.get().strip()
            and not self.busy
            and not self.closing
            and not self.session.running
            and not stopping
            else "disabled"
        )
        self.mode_picker.configure(
            state="disabled"
            if self.busy or self.closing or stopping or self.review_window is not None
            else "readonly"
        )
        self.stop_button.configure(
            state="normal"
            if (self.session.running or stopping) and not self.busy and not self.closing
            else "disabled"
        )
        input_ready = (
            self.same_project()
            and not self.busy
            and not self.closing
            and not self.session.running
            and not stopping
        )
        self.add_button.configure(
            state="normal"
            if input_ready
            and self.project is not None
            and self.project.state == "empty"
            else "disabled"
        )
        self.prepare_button.configure(
            state="normal"
            if input_ready
            and self.project is not None
            and self.project.state == "staged"
            else "disabled"
        )
        self.new_button.configure(
            state="disabled"
            if self.busy or self.closing or self.session.running or stopping
            else "normal"
        )
        self.cancel_button.configure(
            state="normal"
            if self.preparing and self.busy and not self.cancel_requested.is_set()
            else "disabled"
        )
        self.library_button.configure(
            state="disabled"
            if self.busy or self.closing or self.session.running or stopping
            else "normal"
        )
        self.export_button.configure(
            state="normal"
            if self.same_path()
            and not self.busy and not self.closing and not stopping
            else "disabled"
        )
        if self.library_window is not None and self.library_window.exists():
            self.library_window.controls()
        self.render_state()
        if self.busy:
            self.progress.grid()
        else:
            self.progress.grid_remove()
        self.mode_help.set(
            {
                "text": "원본과 교체 문구를 비교하고 페이지 메모를 저장합니다. 승인과 한글 적용은 별도입니다.",
                "layout": "선택 문구·영역과 원래 표 행 높이를 검토합니다. 임의 행 높이 변경은 지원하지 않습니다.",
                "fill": "값·근거·빈칸 위치를 직접 연결합니다. 입력 자료 준비가 먼저 필요합니다."
                if fill_missing
                else "준비된 값을 원본의 빈칸에 연결합니다. 서명·직인 없는 작성 양식에만 사용합니다.",
            }[self.mode_key()]
        )

    def choose_folder(self):
        if (
            self.busy
            or self.session.running
            or self.closing
            or getattr(self.session, "stopping", False)
        ):
            return
        location = os.environ.get("LOCALAPPDATA")
        saved = Path(location) / "HwpReview" / "Projects" if location else None
        initial = Path(self.path.get().strip()) if self.path.get().strip() else saved
        options = (
            {"initialdir": str(initial)}
            if initial is not None and initial.is_dir()
            else {}
        )
        selected = filedialog.askdirectory(
            parent=self.root,
            title="빈 작업 폴더 또는 이전 검토 폴더 선택",
            mustexist=True,
            **options,
        )
        if selected:
            self.path.set(selected)
            self.summary.set("작업 폴더를 확인하고 있습니다…")
            self.check_workspace()

    def task(self, label, operation, *, role="document"):
        if self.busy or self.closing:
            return
        self.busy = True
        self.task_role = role
        self.task_started = time.monotonic()
        self.stage_message = label
        self.elapsed.set("")
        self.status.set(label)
        self.progress.configure(mode="indeterminate")
        self.progress.grid()
        self.progress.start(12)
        self.update_controls()

        def worker():
            try:
                self.events.put(("ok", operation()))
            except Exception as error:
                # Discard traceback references; retain just source-safe context.
                self.events.put(("error", (type(error).__name__ + ": " + str(error))[:6000]))

        try:
            threading.Thread(
                target=worker, name="HWP-desktop-task", daemon=True
            ).start()
        except Exception as error:
            self.events.put(("error", (type(error).__name__ + ": " + str(error))[:6000]))

    def check_workspace(self):
        if (
            self.busy
            or self.closing
            or not self.path.get().strip()
            or self.session.running
            or getattr(self.session, "stopping", False)
        ):
            return
        path = Path(self.path.get().strip())

        def operation():
            if any(os.path.lexists(path / name) for name in ("report-project.json", "template-manifest.json", "harness.json")):
                raise ValueError("이 창은 기존 .hwp 검토 전용입니다. HWPX 편집은 기본 창에서 작업 폴더를 여세요.")
            project = hwp_project.scan_folder(path)
            info = (
                inspect_workspace(project.workspace)
                if project.state == "ready"
                else None
            )
            return "checked", (project, info)

        self.info = self.project = None
        self.task("작업 폴더를 확인하고 있습니다…", operation)

    def add_hwp(self):
        if (
            not self.same_project()
            or self.project is None
            or self.project.state != "empty"
            or self.busy
            or self.closing
            or self.session.running
            or getattr(self.session, "stopping", False)
        ):
            return
        source = filedialog.askopenfilename(
            parent=self.root,
            title="작업 폴더로 복사할 HWP 선택",
            filetypes=[("한글 HWP", "*.hwp")],
        )
        if not source:
            return
        root = self.project.root

        def operation():
            return "checked", (hwp_project.import_hwp(root, Path(source)), None)

        self.task("HWP 원본을 덮어쓰지 않고 작업 폴더로 복사합니다…", operation)

    def prepare_hwp(self):
        if (
            not self.same_project()
            or self.project is None
            or self.project.source is None
            or self.project.state != "staged"
            or self.busy
            or self.closing
            or self.session.running
            or getattr(self.session, "stopping", False)
        ):
            return
        root, source = self.project.root, self.project.source
        if not messagebox.askyesno(
            "HWP 검토 자료 준비 — 별도 확인",
            f"신뢰할 수 있는 문서인지 확인하세요.\n\n{source.name}\n\n설치된 한글의 새 자동화 인스턴스를 열어 PDF/HWPX와 무수정 왕복 검증 자료를 새 _review 폴더에 만듭니다. 원본을 덮어쓰거나 실제 수정·제출을 승인하지 않습니다.\n\n이미 등록된 승인 보안 모듈만 사용하며 DLL 설치·레지스트리 변경은 하지 않습니다. 실패하면 자료를 유지하고 자동 재시도·잠금 삭제·한글 강제 종료를 하지 않습니다.\n\n검토 자료를 준비할까요?",
            parent=self.root,
        ):
            return
        self.cancel_requested.clear()
        self.preparing = True

        def progress(step, message):
            self.events.put(("progress", (step, message)))

        def operation():
            project = hwp_project.prepare_project(
                root, progress=progress, cancelled=self.cancel_requested.is_set
            )
            return "checked", (project, inspect_workspace(project.workspace))

        self.task("한글 연결을 확인하고 있습니다…", operation)

    def launch(self):
        if self.review_window is not None:
            self.review_window.window.lift()
            return
        if (
            not self.same_path()
            or self.info is None
            or self.busy
            or self.closing
            or self.session.stopping
        ):
            return
        path, mode = self.info.path, self.mode_key()

        def operation():
            info = inspect_workspace(path)
            if mode == "fill" and not info.has_fill_input:
                raise ValueError("빈칸 입력 자료가 없습니다. fill-input.json을 준비한 뒤 다시 확인하세요.")
            if not self.session.running:
                if self.session.info is not None:
                    self.session.stop()
                self.session.start(path)
            elif not isinstance(self.session.info, WorkspaceInfo) or self.session.info.path != path:
                raise RuntimeError("다른 작업의 검토 창을 먼저 닫으세요.")
            client = self.session.legacy_client()
            return "opened", (info, client, mode, client.snapshot(mode))

        self.task("로컬 HWP 검토 화면을 준비합니다…", operation)

    def review_closed(self):
        self.review_window = None
        self.status.set("검토 창을 닫았습니다. 다른 작업을 선택하려면 내 검토 서버를 닫으세요.")
        self.update_controls()

    def stop_server(self):
        if (
            self.busy
            or self.closing
            or not (self.session.running or getattr(self.session, "stopping", False))
        ):
            return
        view = self.review_window
        if view is not None and not view.may_close():
            return
        if not messagebox.askyesno(
            "검토 서버 닫기",
            "이 앱이 연 검토 창과 서버를 닫을까요? 한글이나 다른 서버, 원본, 승인 기록은 변경하지 않습니다.",
            parent=self.root,
        ):
            return
        if view is not None:
            view.destroy()

        def operation():
            self.session.stop()
            return "stopped", None

        self.task("내 검토 서버의 요청이 끝나기를 기다립니다…", operation)

    def poll(self):
        if self.busy and self.task_started:
            seconds = int(time.monotonic() - self.task_started)
            self.elapsed.set(
                f"경과 {seconds // 60}분 {seconds % 60}초"
                + (
                    " · 현재 단계가 끝나면 중단합니다"
                    if self.cancel_requested.is_set() and self.preparing
                    else " · 한글 접근 대화상자가 표시되면 직접 확인하세요"
                    if self.preparing
                    else ""
                )
            )
        try:
            kind, payload = self.events.get_nowait()
        except queue.Empty:
            pass
        else:
            if kind == "progress":
                step, message = payload
                if self.preparing and self.busy:
                    self.stage_message = message
                    if not self.cancel_requested.is_set():
                        self.status.set(f"{min(step + 1, 6)}/6 · {message}")
                    self.progress.stop()
                    self.progress.configure(mode="determinate", value=step)
                self.root.after(100, self.poll)
                return
            self.busy = self.preparing = False
            self.progress.stop()
            self.progress.grid_remove()
            if kind == "error":
                self.last_error = payload
                self.status.set(
                    "작업을 완료하지 못했습니다. ‘오류 자세히’에서 원인을 확인하세요."
                )
                if self.task_role == "library":
                    self.status.set(
                        "작업 목록을 읽지 못했습니다. 원인 확인 후 다시 읽거나 다른 폴더를 선택하세요."
                    )
                    if self.library_window is not None and self.library_window.exists():
                        self.library_window.status.set(
                            "목록 확인 실패 · 자료를 삭제하거나 복구하지 않았습니다"
                        )
                else:
                    self.info = self.project = None
                    self.summary.set(
                        "준비 완료 표시를 해제했습니다. 실제 자료를 임의로 고치거나 잠금을 해제하지 않습니다. 서버가 남아 있으면 초안을 저장하고 ‘내 검토 서버 닫기’를 눌러주세요."
                    )
                self.closing = False
            else:
                self.last_error = ""
                operation, data = payload
                if operation == "checked":
                    project, info = data
                    self.project, self.info = project, info
                    self.path.set(str(project.root))
                    if info is not None:
                        self.summary.set(
                            f"{info.pages}쪽 · 표 {info.tables}개 · 그림 {info.images}개 · 보호 페이지 {info.protected_pages}쪽\n원본·기준 HWPX/PDF 해시와 페이지 미리보기, 메모 형식을 확인했습니다."
                            + (
                                "\n진행 중·중단 잠금이 있습니다. 검토만 가능하며 적용은 소유자 확인이 필요합니다."
                                if info.locked
                                else ""
                            )
                            + (
                                "\n빈칸 입력 자료가 있습니다. 값과 근거의 정확성은 검토 화면에서 확인합니다."
                                if info.has_fill_input
                                else ""
                            )
                        )
                        self.status.set(
                            "자료 확인 완료 · 검토할 화면을 골라 열 수 있습니다"
                        )
                    elif project.state == "empty":
                        self.summary.set(
                            "HWP 한 개를 추가하세요. 탐색기에서 넣었다면 ‘폴더 · 연결 설정’의 다시 확인을 누르세요."
                        )
                        self.status.set(
                            "다음: HWP 추가 · 아직 한글을 실행하지 않았습니다"
                        )
                    else:
                        self.summary.set(
                            "작업용 HWP가 있습니다. 설치된 한글로 미리보기를 만들고 원본과 같게 보존되는지 확인합니다.\n‘원본 확인 시작’을 누르면 실행 범위를 따로 확인합니다."
                        )
                        self.status.set(
                            "다음: 원본 확인 · 아직 한글을 실행하지 않았습니다"
                        )
                elif operation == "library":
                    if self.library_window is not None and self.library_window.exists():
                        self.library_window.display(data)
                    self.status.set(
                        "작업 목록 확인 완료 · 선택한 작업은 열 때 다시 검증합니다"
                    )
                elif operation == "exported":
                    self.status.set(
                        f"저장된 메모 {data['notes']}개를 내보냈습니다 · 승인/문서 적용 아님"
                    )
                elif operation == "opened":
                    self.info, client, mode, state = data
                    try:
                        self.review_window = LegacyReviewWindow(self.root, client, mode, state, self.review_closed)
                    except (ValueError, KeyError, tk.TclError) as error:
                        self.callback_error(type(error), error, None)
                    else:
                        self.status.set("기존 HWP 검토 창을 열었습니다. 브라우저는 실행하지 않습니다.")
                elif operation == "stopped":
                    self.status.set(
                        "내 검토 서버를 닫았습니다. 원본과 승인 기록은 그대로입니다."
                    )
                elif operation == "closed":
                    self.root.destroy()
                    return
            self.update_controls()
        if (
            not self.busy
            and self.session.info is not None and not self.last_error
            and not self.session.running
            and not getattr(self.session, "stopping", False)
        ):
            self.last_error = (
                self.session.failure or "이 앱의 로컬 검토 서버가 중단되었습니다."
            )
            self.status.set(
                "내 검토 서버 연결이 끊겼습니다. 자료를 확인한 뒤 직접 다시 열어주세요."
            )
            self.update_controls()
        self.root.after(100, self.poll)

    def review_window_may_close(self):
        return self.review_window is None or self.review_window.may_close()

    def show_text(self, title, text):
        window = tk.Toplevel(self.root)
        window.title(title)
        window.geometry("760x600")
        window.transient(self.root)
        view = scrolledtext.ScrolledText(
            window, wrap="word", font=("맑은 고딕", 12), padx=20, pady=20
        )
        view.pack(fill="both", expand=True)
        view.insert("1.0", text)
        view.configure(state="disabled")
        ttk.Button(window, text="닫기", command=window.destroy).pack(pady=10)

    def show_help(self):
        try:
            text = (ROOT / "docs/user-guide.md").read_text(encoding="utf-8")
        except (OSError, UnicodeError) as error:
            self.callback_error(
                RuntimeError,
                RuntimeError("사용 안내 파일을 읽을 수 없습니다: " + str(error)),
                None,
            )
            return
        self.show_text("쉬운 사용 안내", text)

    def show_license(self):
        try:
            text = legal_notice(ROOT)
        except (OSError, UnicodeError) as error:
            self.callback_error(
                RuntimeError,
                RuntimeError("라이선스 파일을 읽을 수 없습니다: " + str(error)),
                None,
            )
            return
        self.show_text("라이선스 · 대응 소스", text)

    def show_error(self):
        self.show_text(
            "오류 자세히",
            self.last_error
            or "현재 오류가 없습니다.\n앱은 문제가 생겨도 자동 재시도·문서 변환·승인·잠금 해제를 하지 않습니다.",
        )

    def close(self):
        if self.busy or self.closing:
            self.status.set("진행 중인 확인/검토 요청이 끝난 뒤 앱을 닫아주세요.")
            return
        if not self.review_window_may_close():
            return
        if (
            self.session.running or getattr(self.session, "stopping", False)
        ) and not messagebox.askyesno(
            "앱 닫기",
            "검토 창에서 초안을 저장했나요?\n이 앱의 검토 서버도 닫힙니다. 한글 프로세스와 문서는 변경하지 않습니다.",
            parent=self.root,
        ):
            return
        if self.review_window is not None:
            self.review_window.destroy()

        def operation():
            self.session.stop()
            return "closed", None

        self.task("내 검토 서버를 닫고 있습니다…", operation)
        self.closing = True
        self.update_controls()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    modes = parser.add_mutually_exclusive_group()
    modes.add_argument(
        "--workspace",
        type=Path,
        help="Pre-fill the existing workspace picker; does NOT open/approve/convert it",
    )
    parser.add_argument(
        "--legacy-desktop", action="store_true",
        help="Open the retained advanced Tk tools; not v1-qualified HWP automation",
    )
    modes.add_argument(
        "--setup-requirements", action="store_true",
        help="Check required Windows components and automatically prepare only missing items",
    )
    modes.add_argument(
        "--check-requirements", type=Path, metavar="NEW_JSON",
        help="Write a new read-only prerequisite status report; never install or open a GUI",
    )
    modes.add_argument(
        "--self-test-dir",
        type=Path,
        help="Explicit packaging test: NEW directory, synthetic data only, no browser/Hancom/approval",
    )
    modes.add_argument(
        "--native-gui-host-dir", type=Path,
        help="Explicit NEW unsigned WPF/actual-OMP fixture; inert local provider/OS, UI-only consent",
    )
    modes.add_argument("--baseline-export", type=Path, help=argparse.SUPPRESS)
    modes.add_argument("--text-export", type=Path, help=argparse.SUPPRESS)
    modes.add_argument("--contract-export", type=Path, help=argparse.SUPPRESS)
    parser.add_argument("--contract-run", help=argparse.SUPPRESS)
    parser.add_argument(
        "--contract-phase", type=int, choices=(0, 1), help=argparse.SUPPRESS
    )
    parser.add_argument("--baseline-probe", action="store_true", help=argparse.SUPPRESS)
    parser.add_argument("--text-run", help=argparse.SUPPRESS)
    parser.add_argument(
        "--text-phase", type=int, choices=(0, 1), help=argparse.SUPPRESS
    )
    parser.add_argument(
        "--baseline-phase", type=int, choices=(0, 1, 2), help=argparse.SUPPRESS
    )
    args = parser.parse_args()
    if args.legacy_desktop and any((
        args.setup_requirements, args.check_requirements, args.self_test_dir,
        args.native_gui_host_dir, args.baseline_export, args.text_export, args.contract_export,
    )):
        parser.error("Advanced desktop cannot mix synthetic or worker modes")
    if args.setup_requirements or args.check_requirements is not None:
        if (
            args.contract_run is not None or args.contract_phase is not None
            or args.text_run is not None or args.text_phase is not None
            or args.baseline_phase is not None or args.baseline_probe
        ):
            parser.error("Required setup cannot mix document or worker modes")
        if args.check_requirements is not None:
            from hwp_setup import write_status_report

            try:
                code = write_status_report(args.check_requirements)
            except (OSError, ValueError) as error:
                if sys.stderr is not None:
                    print(f"필수 항목 확인 실패: {error}", file=sys.stderr)
                code = 1
            raise SystemExit(code)
        from hwp_setup_ui import run

        raise SystemExit(run())
    if args.native_gui_host_dir is not None:
        if (
            args.contract_run is not None or args.contract_phase is not None
            or args.text_run is not None or args.text_phase is not None
            or args.baseline_phase is not None or args.baseline_probe
        ):
            parser.error("Synthetic native host cannot mix worker modes")
        from hwp_native_smoke import run

        raise SystemExit(run(args.native_gui_host_dir))
    if args.baseline_probe and args.baseline_export is None:
        parser.error("Probe requires the exact baseline workspace")
    if args.contract_export is not None:
        if (
            args.contract_run is None
            or args.contract_phase is None
            or args.text_run is not None
            or args.text_phase is not None
            or args.baseline_phase is not None
            or args.baseline_probe
        ):
            parser.error(
                "Exact owned Native contract run/phase required; no mixed worker modes"
            )
        import importlib

        run_worker = importlib.import_module("hwp_contract_apply").run_worker
        raise SystemExit(
            run_worker(args.contract_export, args.contract_run, args.contract_phase)
        )
    if args.contract_run is not None or args.contract_phase is not None:
        parser.error("Native contract phase requires its owned workspace")
    if args.text_export is not None:
        if (
            args.text_run is None
            or args.text_phase is None
            or args.baseline_phase is not None
        ):
            parser.error("Exact owned text run/phase required; no mixed worker modes")
        from hwp_text_apply import run_worker

        raise SystemExit(run_worker(args.text_export, args.text_run, args.text_phase))
    if args.text_run is not None or args.text_phase is not None:
        parser.error("Text phase requires its owned workspace")
    if args.baseline_export is not None:
        if args.baseline_phase is None:
            parser.error("Preparation phase is required")
        import importlib

        WorkerTrace = importlib.import_module("hwp_worker_trace").WorkerTrace
        trace = WorkerTrace(args.baseline_export, args.baseline_phase)
        trace.event("prepare_module_import_begin")
        from hwp_prepare_native import run, probe

        trace.event("prepare_module_import_done")
        code = (
            probe(args.baseline_export, args.baseline_phase, trace)
            if args.baseline_probe
            else run(args.baseline_export, args.baseline_phase, trace=trace)
        )
        trace.event("main_exit")
        raise SystemExit(code)
    if args.baseline_phase is not None:
        parser.error("Preparation phase requires its workspace")
    if args.self_test_dir is not None:
        # This explicit synthetic-only runtime lives beside app modules in both
        # source and bundle; no test/document/cwd path is added as a fallback.
        from packaged_smoke_runtime import run

        run(args.self_test_dir)
        return
    if not args.legacy_desktop:
        try:
            from hwp_setup_ui import ensure_ready

            if not ensure_ready():
                raise SystemExit(1)
            from hwp_native_host import run

            raise SystemExit(run(args.workspace))
        except Exception as error:
            # pythonw/frozen have no console. After prerequisite preparation,
            # missing native UI/runtime is explained without a fallback executor.
            import ctypes

            ctypes.windll.user32.MessageBoxW(
                None, str(error)[:6000], "HWP Editor 시작 확인", 0x10
            )
            raise SystemExit(1) from error
    root = tk.Tk()
    app = DesktopApp(root, args.workspace)
    try:
        root.mainloop()
    finally:
        app.session.stop()


if __name__ == "__main__":
    main()
