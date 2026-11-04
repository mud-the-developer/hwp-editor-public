"""Required missing-only startup preparation; importing never starts setup."""

from html.parser import HTMLParser
from pathlib import Path
import queue
import sys
import threading
import tkinter as tk
from tkinter import messagebox, scrolledtext, ttk

from hwp_desktop_ui import configure_style
from hwp_resources import resource_root
import hwp_setup as setup


def _all_installed(statuses):
    return all(statuses[identifier]["installed"] is True for identifier in setup.COMPONENT_IDS)


class _NoticeText(HTMLParser):
    """Display extraction only; original HTML bytes remain bundled and pinned."""
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.parts = []

    def handle_data(self, data):
        self.parts.append(data)

    def handle_starttag(self, tag, attrs):
        if tag in ("p", "h3", "li", "br", "hr"):
            self.parts.append("\n")

    def handle_endtag(self, tag):
        if tag in ("p", "h3", "li"):
            self.parts.append("\n")


class SetupWindow:
    def __init__(self, root, *, backend=setup, manifest=None, statuses=None, autoclose=False):
        self.root, self.backend = root, backend
        self.manifest = backend.load_manifest() if manifest is None else manifest
        self.statuses = backend.installed_status(self.manifest) if statuses is None else statuses
        ready = _all_installed(self.statuses)
        self.components = {item["id"]: item for item in self.manifest["components"]}
        self.messages = queue.Queue()
        self.busy, self.attempted, self.exit_code = False, False, 1
        self.autoclose, self.closed = autoclose, False
        self.prepare_button = None
        self.worker = None
        self.start_after = self.drain_after = self.close_after = None
        self.state_labels = {}
        self.status = tk.StringVar(root, value="필수 항목의 설치 상태를 확인했습니다. 없는 항목을 자동으로 준비합니다.")
        root.title("HWPReview · 필수 도구 준비")
        width = min(940, max(640, root.winfo_screenwidth() - 80))
        height = min(820, max(600, root.winfo_screenheight() - 100))
        root.geometry(f"{width}x{height}")
        root.minsize(min(680, width), min(620, height))
        root.configure(background="#f5f7fa")
        configure_style(root)
        root.protocol("WM_DELETE_WINDOW", self.close)
        footer = ttk.Frame(root, padding=(24, 8))
        footer.pack(side="bottom", fill="x")
        footer.columnconfigure(0, weight=1)
        shell = ttk.Frame(root)
        shell.pack(fill="both", expand=True)
        self.canvas = tk.Canvas(shell, background="#f5f7fa", highlightthickness=0, takefocus=True)
        self.canvas.pack(side="left", fill="both", expand=True)
        scrollbar = ttk.Scrollbar(shell, orient="vertical", command=self.canvas.yview)
        scrollbar.pack(side="right", fill="y")
        self.canvas.configure(yscrollcommand=scrollbar.set)
        self.body = body = ttk.Frame(self.canvas, padding=(24, 18))
        body_id = self.canvas.create_window(0, 0, window=body, anchor="nw")
        body.columnconfigure(0, weight=1)
        body.bind("<Configure>", lambda event: self.canvas.configure(scrollregion=self.canvas.bbox("all")))
        self.canvas.bind("<Configure>", lambda event: self.canvas.itemconfigure(body_id, width=event.width))
        ttk.Label(body, text="앱 시작에 필요한 도구 준비", style="Title.TLabel").grid(row=0, column=0, sticky="w")
        self.intro = ttk.Label(
            body, wraplength=width - 60,
            text=".NET Desktop·WebView2·Bun/OMP·PowerShell 7·Propo 글꼴은 필수 항목입니다. 호환 설치가 있으면 유지하고 없는 항목만 준비합니다. 원본 문서·로그인·사용자 PATH는 바꾸지 않습니다.",
            style="Muted.TLabel",
        )
        self.intro.grid(row=1, column=0, sticky="ew", pady=(6, 12))
        card = ttk.Frame(body, style="Card.TFrame", padding=(16, 10))
        card.grid(row=2, column=0, sticky="ew")
        card.columnconfigure(0, weight=1)
        descriptions = {
            "powershell7": "Windows PowerShell 5.1 유지 · Microsoft 설치기/UAC에서 직접 확인 · PATH·원격 기능·자동 업데이트는 기본 해제",
            "d2coding_propo": "Propo Regular/Bold만 사용자 계정에 설치 · 실제 이름: D2KodingLigature Nerd Font Propo · 터미널/보고서 기본 글꼴은 유지",
            "dotnet10": "기존 호환 .NET 10 Desktop x64 유지 · Microsoft 설치기는 관리자 UAC 확인 필요 · 자동 재부팅 없음",
            "webview2": "Microsoft Runtime 약관 · 기본 SmartScreen의 Microsoft 정보 전송 및 Evergreen 자동 업데이트 안내 · 기존 공유 Runtime 유지",
            "omp_runtime": "검증한 Bun/OMP를 별도 앱 경로에 준비 · 기존 .bun·인증·전역 PATH 유지 · 패키지 설치 스크립트 미실행",
        }
        for index, identifier in enumerate(setup.COMPONENT_IDS):
            item = self.components[identifier]
            ttk.Label(card, text=f"필수 · {item['name']} · {item['version']}", style="Card.TLabel").grid(
                row=index * 3, column=0, sticky="w", pady=(6, 0)
            )
            ttk.Label(card, text=descriptions[identifier], style="CardMuted.TLabel", wraplength=width - 100).grid(
                row=index * 3 + 1, column=0, sticky="ew", padx=(24, 0), pady=(2, 0)
            )
            state_label = ttk.Label(card, style="Card.TLabel", wraplength=width - 100)
            state_label.grid(row=index * 3 + 2, column=0, sticky="ew", padx=(24, 0), pady=(2, 6))
            self.state_labels[identifier] = state_label
        ttk.Label(
            body,
            text="누락 항목만 공식 배포처에서 받아 고정 크기·SHA-256과 해당 Microsoft 서명을 검사합니다.\n외부 Runtime·Bun·OMP의 원문 조건과 WebView2 SmartScreen·업데이트 안내를 확인하세요. 고지 수집은 법적 재배포 인증이 아닙니다.",
            style="Muted.TLabel", wraplength=width - 60,
        ).grid(row=3, column=0, sticky="ew", pady=(12, 5))
        notices = ttk.Notebook(body)
        notices.grid(row=4, column=0, sticky="nsew")
        for identifier in setup.COMPONENT_IDS:
            item = self.components[identifier]
            panel = ttk.Frame(notices, padding=6)
            text = scrolledtext.ScrolledText(panel, wrap="word", height=6, font=("맑은 고딕", 10), relief="flat")
            text.pack(fill="both", expand=True)
            originals = []
            for relative in item["license_files"]:
                raw = (resource_root() / Path(relative)).read_text(encoding="utf-8")
                if Path(relative).suffix.lower() == ".html":
                    parser = _NoticeText()
                    parser.feed(raw)
                    raw = "아래는 표시용 텍스트 추출입니다. 동봉된 HTML 원문 바이트는 변경하지 않습니다.\n\n" + "".join(parser.parts)
                originals.append(f"===== {Path(relative).name} =====\n{raw}")
            text.insert("1.0", "\n\n".join(originals))
            text.configure(state="disabled")
            tab_names = {"powershell7": "PowerShell", "d2coding_propo": "Propo 글꼴",
                         "dotnet10": ".NET", "webview2": "WebView2", "omp_runtime": "Bun / OMP"}
            notices.add(panel, text=tab_names[identifier])
        ttk.Label(footer, textvariable=self.status, wraplength=width - 60).grid(row=0, column=0, sticky="ew", pady=(3, 7))
        self.progress = ttk.Progressbar(footer, mode="indeterminate")
        self.progress.grid(row=1, column=0, sticky="ew", pady=(0, 7))
        actions = ttk.Frame(footer)
        actions.grid(row=2, column=0, sticky="ew")
        actions.columnconfigure(0, weight=1)
        needs_terms = not ready and any(
            not self.statuses[identifier]["installed"]
            for identifier in ("dotnet10", "webview2", "omp_runtime")
        )
        if needs_terms:
            self.prepare_button = ttk.Button(actions, text="약관 확인 후 필수 환경 준비", command=self.install)
            self.prepare_button.grid(row=0, column=0, sticky="e")
        self.close_button = ttk.Button(actions, text="닫기", command=self.close)
        self.close_button.grid(row=0, column=1, padx=(8, 0))
        root.bind("<Configure>", self.resize)
        root.bind("<FocusIn>", self.reveal_focus)
        root.bind("<MouseWheel>", self.scroll)
        for key in ("Prior", "Next", "Home", "End"):
            root.bind(f"<{key}>", self.scroll_key)
        self.update_controls()
        if ready:
            self.exit_code = 0
            self.status.set("준비 완료 · 필수 항목이 모두 설치되어 있습니다. 기존 설치를 유지하며 다운로드·설치하지 않습니다.")
            if self.autoclose:
                self.close_after = root.after_idle(self.close)
        else:
            if needs_terms:
                self.status.set("외부 소프트웨어 원문 약관·SmartScreen·업데이트 안내를 확인한 뒤 준비를 시작하세요. 필수 항목은 선택 해제할 수 없으며 UAC·로그인은 직접 확인합니다.")
            else:
                self.start_after = root.after(100, self.install)
            self.drain_after = root.after(100, self.drain)

    def scroll(self, event):
        if not isinstance(event.widget, tk.Text):
            amount = int(-event.delta / 120)
            if amount:
                self.canvas.yview_scroll(amount, "units")
                return "break"

    def scroll_key(self, event):
        if isinstance(event.widget, tk.Text):
            return
        if event.keysym in ("Prior", "Next"):
            self.canvas.yview_scroll(-1 if event.keysym == "Prior" else 1, "pages")
        else:
            self.canvas.yview_moveto(0 if event.keysym == "Home" else 1)
        return "break"

    def reveal_focus(self, event):
        if not str(event.widget).startswith(str(self.body) + "."):
            return
        bounds = self.canvas.bbox("all")
        if not bounds or bounds[3] <= self.canvas.winfo_height():
            return
        top = event.widget.winfo_rooty() - self.body.winfo_rooty()
        bottom = top + event.widget.winfo_height()
        view_top = self.canvas.canvasy(0)
        if top < view_top:
            self.canvas.yview_moveto(max(0, top / bounds[3]))
        elif bottom > view_top + self.canvas.winfo_height():
            self.canvas.yview_moveto(max(0, (bottom - self.canvas.winfo_height()) / bounds[3]))

    def resize(self, event):
        if event.widget is self.root:
            width = max(240, event.width - 140)
            def wrap(parent):
                for widget in parent.winfo_children():
                    if isinstance(widget, ttk.Label) and widget.cget("wraplength"):
                        widget.configure(wraplength=width)
                    wrap(widget)

            wrap(self.root)

    def update_controls(self):
        for identifier, label in self.state_labels.items():
            current = self.statuses[identifier]
            if current["installed"]:
                prefix = "설치됨 · 확인"
            elif self.busy:
                prefix = "필수 · 준비 중"
            elif self.attempted:
                prefix = "필수 · 설치 확인 안 됨"
            else:
                prefix = "필수 · 자동 준비 대기"
            label.configure(text=f"{prefix}: {current['detail']}")
        self.close_button.configure(state="disabled" if self.busy else "normal")
        if self.prepare_button is not None:
            self.prepare_button.configure(state="disabled" if self.busy or self.attempted else "normal")

    def install(self):
        self.start_after = None
        if self.closed or self.busy or self.attempted or _all_installed(self.statuses):
            return
        self.busy, self.attempted = True, True
        self.status.set("없는 필수 항목만 준비합니다. Microsoft 설치기/UAC가 열리면 직접 확인하세요. 진행 중 창을 닫아 강제 종료하지 않습니다.")
        self.progress.start(12)
        self.update_controls()

        def work():
            receipt = None
            try:
                receipt = self.backend.install_missing(
                    manifest=self.manifest, progress=lambda message: self.messages.put(("progress", message))
                )
                statuses = self.backend.installed_status(self.manifest)
                ready = _all_installed(statuses)
            except Exception as error:
                receipt = getattr(error, "receipt", None) or receipt
                self.messages.put(("failed", (str(error) or type(error).__name__, receipt)))
            else:
                self.messages.put(("complete", (receipt, statuses, ready)))

        try:
            self.worker = threading.Thread(target=work, name="HwpRequiredSetup", daemon=False)
            self.worker.start()
        except Exception as error:
            self.messages.put(("failed", (str(error) or type(error).__name__, None)))

    def drain(self):
        self.drain_after = None
        if self.closed:
            return
        while True:
            try:
                kind, value = self.messages.get_nowait()
            except queue.Empty:
                self.drain_after = self.root.after(100, self.drain)
                return
            if kind == "progress":
                self.status.set(str(value))
                continue
            self.busy = False
            self.progress.stop()
            self.exit_code = 1
            receipt = (value[0] if kind == "complete" else value[1]) or {}
            directory = receipt.get("run_directory")
            if kind == "complete":
                _, self.statuses, ready = value
                failed = receipt.get("error") or any(
                    item.get("status") in ("failed", "cancelled") for item in receipt.get("components", ())
                )
                if receipt.get("reboot_required"):
                    self.exit_code = 3010
                    self.status.set("재부팅 필요 · 아직 앱을 시작할 수 없습니다. 자동 재부팅하지 않습니다. 재부팅 후 앱을 다시 시작해 설치 상태를 확인하세요.")
                elif failed:
                    self.exit_code = 1602 if receipt.get("error", {}).get("cancelled") else 1
                    self.status.set("필수 항목 준비가 실패하거나 취소되었습니다. 앱을 시작하지 않습니다. 자동 재시도·복구·강제 종료하지 않습니다.")
                elif not ready:
                    self.status.set("설치 확인 실패 · 처리 결과와 달리 필수 항목의 실제 설치 정보를 모두 확인하지 못했습니다. 앱을 시작하지 않으며 자동 재시도·복구하지 않습니다.")
                else:
                    self.exit_code = 0
                    self.status.set("준비 완료 · 필수 항목의 실제 설치 정보를 모두 확인했습니다. 문서·터미널 기본 글꼴은 변경하지 않았습니다.")
            else:
                self.exit_code = 1602 if receipt.get("error", {}).get("cancelled") else 1
                self.status.set(f"필수 항목 준비가 실패하거나 취소되었습니다. 앱을 시작하지 않습니다. 자동 재시도·복구·강제 종료하지 않습니다.\n{value[0]}")
            if directory:
                self.status.set(f"{self.status.get()}\n설치 기록: {directory}")
            self.update_controls()
            if self.exit_code == 0 and self.autoclose:
                self.close()
            return

    def close(self):
        if self.closed:
            return
        if self.busy:
            messagebox.showinfo("설치 진행 중", "진행 중인 설치를 창 닫기로 강제 종료하지 않습니다. Microsoft 설치기의 완료/취소 결과를 기다리세요.", parent=self.root)
            return
        if not self.attempted and self.exit_code != 0:
            self.exit_code = 1602
        self.closed = True
        for identifier in (self.start_after, self.drain_after, self.close_after):
            if identifier is not None:
                self.root.after_cancel(identifier)
        self.start_after = self.drain_after = self.close_after = None
        self.root.destroy()


def _show_preparation_error(error, root=None):
    detail = str(error) or type(error).__name__
    message = f"필수 항목의 목록·원문 고지 또는 설치 상태를 확인하지 못했습니다. 다운로드·설치를 시작하지 않으며 앱을 열지 않습니다.\n{detail}"
    try:
        if root is not None:
            root.withdraw()
        messagebox.showerror("필수 도구 준비 중단", message, parent=root)
    except Exception as display_error:
        print(f"{message}\n오류 창을 표시하지 못했습니다: {display_error}", file=sys.stderr)
    finally:
        if root is not None:
            try:
                root.destroy()
            except tk.TclError:
                pass


def run(*, backend=setup, manifest=None, statuses=None, autoclose=False) -> int:
    root = None
    try:
        root = tk.Tk()
        window = SetupWindow(root, backend=backend, manifest=manifest, statuses=statuses, autoclose=autoclose)
    except Exception as error:
        _show_preparation_error(error, root)
        return 1
    root.mainloop()
    return window.exit_code


def ensure_ready(*, backend=setup) -> bool:
    try:
        manifest = backend.load_manifest()
        statuses = backend.installed_status(manifest)
        if _all_installed(statuses):
            return True
    except Exception as error:
        _show_preparation_error(error)
        return False
    return run(backend=backend, manifest=manifest, statuses=statuses, autoclose=True) == 0
