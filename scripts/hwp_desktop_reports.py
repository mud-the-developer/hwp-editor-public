"""Desktop report presentation/preparation glue; document execution stays in UI."""

from pathlib import Path
import tkinter as tk
from tkinter import filedialog


def is_report(info):
    return getattr(info, "kind", None) == "report"


class ReportActions:
    def __init__(self, app, base=None):
        self.app = app
        self.base = base
        self.dialog = None

    def dialog_exists(self):
        try:
            return self.dialog is not None and bool(self.dialog.top.winfo_exists())
        except tk.TclError:
            return False

    def available(self):
        app = self.app
        return not (
            app.busy or app.closing or app.session.running
            or getattr(app.session, "stopping", False)
        )

    def _open(self, seed=None, previous=None):
        if not self.available():
            return
        if self.dialog_exists():
            self.dialog.top.lift()
            return
        from hwp_report_ui import ReportDraftWindow

        dialog = ReportDraftWindow(
            self.app.root,
            lambda profile, feedback: self.prepare(profile, feedback, previous=previous),
            seed=seed,
        )
        self.dialog = dialog
        dialog.prepare_button.configure(text="검토 준비하고 비교 열기 (Ctrl+Enter)")
        dialog.top.grab_set()

        def destroyed(event):
            if event.widget is dialog.top and self.dialog is dialog:
                self.dialog = None
                if not self.app.closing and self.app.root.winfo_exists():
                    self.app.update_controls()

        dialog.top.bind("<Destroy>", destroyed, add="+")
        self.app.update_controls()

    def new(self):
        if not self.available():
            return
        selected = filedialog.askopenfilename(
            parent=self.app.root, title="보고서 원본 또는 양식 HWPX 선택",
            filetypes=[("HWPX 문서", "*.hwpx")],
        )
        if not selected:
            return
        source = Path(selected).absolute()
        self.app.info = self.app.project = None
        self.app.last_error = ""
        self.app.address.set("")

        def operation():
            from hwp_report_project import new_document

            return "report-ready", new_document(source, base=self.base)

        self.app.task("원본을 보존하고 보고서 작성 화면을 준비합니다…", operation, role="report")

    def advanced(self):
        self._open()

    def revise(self):
        app = self.app
        if app.busy or app.closing or getattr(app.session, "stopping", False) or not is_report(app.info):
            return
        if getattr(app.info, "document_first", False):
            app.launch(app.edge)
            return
        previous = app.info.root
        if app.session.running and (
            not is_report(app.session.info) or app.session.info.root != previous
        ):
            raise ValueError("현재 보고서와 다른 검토 서버입니다. 내 서버 상태를 먼저 확인하세요.")

        def prepare_revision():
            from hwp_report_project import ReportProjectError, read_inputs

            try:
                seed = read_inputs(previous)
                if app.session.running:
                    app.session.stop()
            except Exception as error:
                raise ReportProjectError(str(error), previous) from error
            return "report-revise", (previous, seed)

        app.task("보고서 입력을 확인하고 내 검토 서버만 닫아 새 원고 선택을 엽니다…", prepare_revision, role="report-revision")

    def open_revision(self, previous, seed):
        self._open(seed=seed, previous=previous)

    def prepare(self, profile, feedback, *, previous=None):
        """Explicit intake callback; never approves, applies or starts Hancom."""
        if not self.available():
            raise ValueError("진행 중인 작업과 내 검토 서버를 먼저 마친 뒤 새 보고서를 준비하세요.")
        app = self.app
        app.info = app.project = None
        app.last_error = ""
        app.address.set("")

        def operation():
            from hwp_report_project import create

            return "report-ready", create(
                profile, feedback, base=self.base, previous=previous
            )

        app.task(
            "HWPX 원본·원고·근거와 정확한 범위를 고정하고 있습니다. 한글·모델을 실행하지 않습니다…",
            operation,
            role="report",
        )

    def accept(self, info):
        app = self.app
        app.info, app.project = info, None
        app.path.set(str(info.root))
        if info.gate_passed:
            app.status.set(
                "보고서 작업 준비 완료 · 요청을 작성하고 실제 HWPX 수정본을 확인하세요"
                if info.document_first
                else ("검증된 기존 생성본 확인 · 다시 생성하지 않습니다"
                      if info.outputs else "준비된 원고의 초안을 만들 수 있습니다")
            )
        else:
            app.last_error = "ReportBlocked: " + "; ".join(info.issues)
            app.status.set("보고서 검토가 차단되었습니다. 입력과 실패 기록을 보존했습니다.")

    def accept_failure(self, root, message):
        from hwp_report_project import inspect

        self.accept(inspect(Path(root)))
        self.app.last_error = message

    def render(self):
        app, info = self.app, self.app.info
        active = is_report(info)
        if not active and not (app.busy and app.task_role == "report"):
            return False
        app.document_title.set(info.name if active else "HWPX 보고서 검토 준비 중")
        if active:
            counts = ["보고서 작업 " + str(info.jobs) + "개"] if info.jobs else []
            if info.tables is not None:
                counts.append("반영 원고의 표 " + str(info.tables) + "개")
            if info.images is not None:
                counts.append("그림 " + str(info.images) + "개")
            if info.document_first:
                app.summary.set("원본은 보존합니다. 요청·자동 초안 생성·HWPX 수정본을 한 화면에서 이어갑니다.")
            else:
                app.summary.set(
                    " · ".join(counts) + ("\n" if counts else "")
                    + "준비된 HWPX 원고를 새 미승인 초안으로 합성합니다."
                    + ("\n" + "; ".join(info.issues) if info.issues else "")
                )
        else:
            app.summary.set("원본을 확인하고 보고서 작업 화면을 준비합니다. 문서 생성이나 모델 요청은 아직 시작하지 않습니다.")
        for index, (label, text) in enumerate(zip(
            app.step_labels, ("1  요청 작성", "2  자동 초안·검사", "3  HWPX 검토")
        )):
            label.configure(
                text=text,
                background="#e0f1f4" if index == 1 else "#e9edf4",
                foreground="#0e7490" if index == 1 else "#79869b",
            )
        for button in (
            app.new_button, app.add_button, app.prepare_button, app.open_button,
            app.export_button, app.cancel_button, app.report_new_button,
            app.report_revision_button,
        ):
            button.pack_forget()
        if active:
            if info.gate_passed:
                app.open_button.configure(text="보고서 작업 열기" if info.document_first else "원고 합성 열기")
                app.open_button.pack(side="left")
            app.report_revision_button.pack(side="left", padx=8 if info.gate_passed else 0)
        app.review_options.grid_remove()
        if app.last_error and not app.busy:
            app.error_hint.set(
                "보고서 입력·실행 환경·검사 결과를 확인하세요. 원본과 이전 생성본·승인·실패 기록은 그대로입니다.\n"
                "이전 입력을 수정하거나 규칙을 완화하지 않고, 새 원고/근거로 새 검토 작업을 만드세요."
            )
            app.error_panel.grid()
        else:
            app.error_panel.grid_remove()
        return True

    def controls(self):
        app = self.app
        available = self.available() and not self.dialog_exists()
        app.report_new_button.configure(state="normal" if available else "disabled")
        revisable = (
            not app.busy and not app.closing and not self.dialog_exists()
            and not getattr(app.session, "stopping", False) and is_report(app.info)
            and (
                not app.session.running
                or (is_report(app.session.info) and app.session.info.root == app.info.root)
            )
        )
        app.report_revision_button.configure(
            state="normal" if revisable else "disabled",
            text="요청·수정본으로 돌아가기"
            if getattr(app.info, "document_first", False)
            else ("검토 닫고 새 원고 선택" if app.session.running else "새 원고로 다시 검토"),
        )
        for index in range(5):
            app.action_menu.entryconfigure(index, state="normal" if available else "disabled")
        if self.dialog_exists():
            for widget in (
                app.new_button, app.add_button, app.prepare_button, app.open_button,
                app.export_button, app.choose, app.check, app.library_button,
                app.browser_button, app.mode_picker,
            ):
                widget.configure(state="disabled")

    def close_dialog(self):
        if self.dialog_exists():
            self.dialog.close()
        self.dialog = None
