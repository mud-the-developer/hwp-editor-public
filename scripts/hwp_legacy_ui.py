"""Legacy HWP review presentation; the owned local handler remains the authority."""
from pathlib import Path
from queue import SimpleQueue, Empty
from threading import Thread
import tkinter as tk
from tkinter import filedialog, messagebox, scrolledtext, ttk
import uuid


KINDS = {
    "text": "글자 교체 · 페이지 메모",
    "layout": "표 행 높이 보존",
    "fill": "무서명 양식 빈 셀 채우기",
}


class LegacyReviewWindow:
    def __init__(self, parent, client, kind, state, on_close):
        self.client, self.kind, self.state = client, kind, state
        self.on_close = on_close
        self.busy = self.unsynced = False
        self._refresh_id = None
        self._draft = {}
        self._seen_page = None
        self._result_index = 0
        self._bitmap = None
        self.window = tk.Toplevel(parent)
        self.window.title(f"기존 HWP 검토 — {KINDS[kind]}")
        self.window.geometry("1260x800")
        self.window.minsize(920, 660)
        self.window.transient(parent)
        self.window.protocol("WM_DELETE_WINDOW", self.close)
        self.window.bind("<F5>", lambda _: self.refresh())
        self.status = tk.StringVar(self.window)
        self.page_no = tk.StringVar(self.window, "1")
        self.view = tk.StringVar(self.window, "기준")
        self.confirmed = tk.BooleanVar(self.window)
        self.controls = []
        box = ttk.Frame(self.window, padding=14)
        box.pack(fill="both", expand=True)
        ttk.Label(box, text=KINDS[kind], style="Heading.TLabel").pack(anchor="w")
        ttk.Label(box, text=Path(state["manifest"]["source"]).name).pack(anchor="w", pady=(2, 8))
        panes = ttk.Panedwindow(box, orient="horizontal")
        panes.pack(fill="both", expand=True)
        left = ttk.Frame(panes, padding=(0, 0, 8, 0))
        right = ttk.Frame(panes, padding=(8, 0, 0, 0))
        panes.add(left, weight=3)
        panes.add(right, weight=2)
        left.columnconfigure(0, weight=1)
        left.rowconfigure(1, weight=1)
        bar = ttk.Frame(left)
        bar.grid(row=0, column=0, columnspan=2, sticky="ew", pady=(0, 6))
        self.button(bar, "이전", lambda: self.navigate(-1)).pack(side="left")
        self.page_picker = ttk.Spinbox(bar, from_=1, to=len(self.pages), width=5, textvariable=self.page_no)
        self.page_picker.pack(side="left", padx=4)
        self.page_picker.bind("<Return>", lambda _: self.render_page())
        self.button(bar, "다음", lambda: self.navigate(1)).pack(side="left")
        self.button(bar, "쪽 보기", self.render_page).pack(side="left", padx=4)
        self.view_picker = ttk.Combobox(bar, state="readonly", values=("기준", "결과", "차이"), textvariable=self.view, width=7)
        self.view_picker.pack(side="right")
        self.view_picker.bind("<<ComboboxSelected>>", lambda _: self.render_page())
        self.canvas = tk.Canvas(left, highlightthickness=1, highlightbackground="#b8c4d4", background="#eff2f6")
        self.canvas.grid(row=1, column=0, sticky="nsew")
        sy = ttk.Scrollbar(left, orient="vertical", command=self.canvas.yview)
        sy.grid(row=1, column=1, sticky="ns")
        sx = ttk.Scrollbar(left, orient="horizontal", command=self.canvas.xview)
        sx.grid(row=2, column=0, sticky="ew")
        self.canvas.configure(yscrollcommand=sy.set, xscrollcommand=sx.set)
        self.canvas.bind("<MouseWheel>", self.scroll_page)
        self.canvas.bind("<Button-4>", lambda _: self.canvas.yview_scroll(-3, "units"))
        self.canvas.bind("<Button-5>", lambda _: self.canvas.yview_scroll(3, "units"))
        self.page_label = ttk.Label(left, style="Muted.TLabel")
        self.page_label.grid(row=3, column=0, sticky="w", pady=(4, 0))
        self.notebook = ttk.Notebook(right)
        self.notebook.pack(fill="both", expand=True)
        self._editor()
        self._approval()
        self._history()
        if kind == "text":
            self._notes()
        footer = ttk.Frame(box)
        footer.pack(fill="x", pady=(8, 0))
        ttk.Label(footer, textvariable=self.status, wraplength=730, justify="left").pack(side="left", fill="x", expand=True)
        self.button(footer, "다시 읽기", self.refresh).pack(side="left", padx=4)
        ttk.Button(footer, text="닫기", command=self.close).pack(side="right")
        self.render_state(clear_draft=True)
        self.window.after_idle(self.render_page)

    @property
    def pages(self):
        return self.state["manifest"]["pages"]

    @property
    def active(self):
        return any(self.state.get(key, {}).get("active") for key in ("text_apply", "contract_apply"))

    @property
    def locked(self):
        return self.state.get("apply_state", {}).get("locked") is not False

    def button(self, parent, name, command, *, style="TButton"):
        button = ttk.Button(parent, text=name, command=command, style=style)
        self.controls.append(button)
        return button

    def tab(self, title):
        panel = ttk.Frame(self.notebook, padding=10)
        self.notebook.add(panel, text=title)
        return panel

    @staticmethod
    def listbox(parent, height):
        shell = ttk.Frame(parent)
        shell.pack(fill="x", pady=(4, 8))
        listbox = tk.Listbox(shell, height=height, exportselection=False, font=("맑은 고딕", 10),
                            background="white", foreground="#172033", selectbackground="#164d84",
                            selectforeground="white", borderwidth=1, relief="solid", highlightthickness=1)
        listbox.pack(side="left", fill="both", expand=True)
        scrollbar = ttk.Scrollbar(shell, command=listbox.yview)
        scrollbar.pack(side="right", fill="y")
        listbox.configure(yscrollcommand=scrollbar.set)
        return listbox

    @staticmethod
    def text_box(parent, height, *, expand=False):
        text = scrolledtext.ScrolledText(parent, wrap="word", height=height, font=("맑은 고딕", 10), padx=6, pady=6)
        text.pack(fill="both" if expand else "x", expand=expand, pady=(4, 8))
        return text

    @staticmethod
    def put(text, value):
        text.configure(state="normal")
        text.delete("1.0", "end")
        text.insert("1.0", value)
        text.configure(state="disabled")

    def _editor(self):
        panel = self.tab("수정안 작성")
        ttk.Label(panel, text="수정 대상" if self.kind != "fill" else "입력 값과 원래 빈 셀 연결").pack(anchor="w")
        self.targets = self.listbox(panel, 8)
        self.targets.bind("<<ListboxSelect>>", lambda _: self.select_target())
        self.target_text = self.text_box(panel, 5)
        self.put(self.target_text, "위 목록에서 수정할 대상을 선택하세요.")
        self.replacement = tk.StringVar(self.window)
        if self.kind == "fill":
            ttk.Label(panel, text="미리보기에서 확인할 빈 셀 위치").pack(anchor="w")
            self.regions = ttk.Combobox(panel, state="readonly")
            self.regions.pack(fill="x", pady=(4, 8))
            self.regions.bind("<<ComboboxSelected>>", lambda _: self.show_region())
        else:
            ttk.Label(panel, text="바꿀 문구").pack(anchor="w")
            self.edit = ttk.Entry(panel, textvariable=self.replacement)
            self.edit.pack(fill="x", pady=(4, 8))
        actions = ttk.Frame(panel)
        actions.pack(fill="x")
        self.stage_button = self.button(actions, "초안에 담기", self.stage)
        self.stage_button.pack(side="left")
        self.button(actions, "선택 항목 취소", self.unstage).pack(side="left", padx=4)
        self.save_button = self.button(panel, "수정안 저장 · 승인 아님", self.save_proposal, style="Primary.TButton")
        self.save_button.pack(anchor="w", pady=(12, 2))
        self.draft_label = ttk.Label(panel, style="Muted.TLabel", wraplength=380)
        self.draft_label.pack(anchor="w")

    def _approval(self):
        panel = self.tab("승인 · 적용")
        ttk.Label(panel, text="저장된 수정안의 문구·영역·보존 조건", wraplength=380).pack(anchor="w")
        self.plan_rows = self.listbox(panel, 4)
        self.plan_rows.bind("<<ListboxSelect>>", lambda _: self.review_position())
        self.plan_text = self.text_box(panel, 8, expand=True)
        self.review_check = ttk.Checkbutton(panel, text="표시된 수정안과 모든 대상 위치를 확인했습니다", variable=self.confirmed, command=self.enable_controls)
        self.review_check.pack(anchor="w", pady=(0, 6))
        self.approve_button = self.button(panel, "이 수정안만 승인", self.approve)
        self.approve_button.pack(anchor="w")
        self.apply_button = self.button(panel, "승인한 수정안 적용…", self.apply, style="Primary.TButton")
        self.apply_button.pack(anchor="w", pady=(8, 0))
        ttk.Label(panel, text="승인만으로 한글을 실행하지 않습니다. 적용과 기관 제출은 서로 다른 단계입니다.", wraplength=370, style="Muted.TLabel").pack(anchor="w", pady=(8, 0))

    def _history(self):
        panel = self.tab("이력 · 내보내기")
        self.history = self.listbox(panel, 8)
        self.history.bind("<<ListboxSelect>>", lambda _: self.select_result())
        self.result_text = self.text_box(panel, 5, expand=True)
        self.export_buttons = []
        for kind in ("hwp", "pdf"):
            button = self.button(panel, f"검증된 {kind.upper()}를 새 파일로 저장", lambda k=kind: self.export(k))
            button.pack(anchor="w", pady=(4, 0))
            self.export_buttons.append(button)

    def _notes(self):
        panel = self.tab("페이지 메모")
        self.notes = self.listbox(panel, 6)
        self.notes.bind("<<ListboxSelect>>", lambda _: self.select_note())
        self.note_text = self.text_box(panel, 7)
        self.button(panel, "새 메모", self.new_note).pack(anchor="w")
        self.note_save = self.button(panel, "메모 저장", self.save_note)
        self.note_save.pack(anchor="w", pady=(4, 0))
        self.note_delete = self.button(panel, "선택 메모 삭제…", self.delete_note)
        self.note_delete.pack(anchor="w", pady=(4, 0))

    @staticmethod
    def index(listbox):
        selected = listbox.curselection()
        return selected[0] if selected else None

    def scroll_page(self, event):
        self.canvas.yview_scroll(-max(1, abs(event.delta) // 120) * (1 if event.delta > 0 else -1), "units")
        return "break"

    def navigate(self, offset):
        if self.busy:
            return
        try:
            target = min(len(self.pages), max(1, int(self.page_no.get()) + offset))
        except ValueError:
            target = 1
        self.page_no.set(str(target))
        self.render_page()

    def render_page(self, *, rectangle=None):
        if self.busy:
            return
        try:
            number = int(self.page_no.get())
            if not 1 <= number <= len(self.pages):
                raise ValueError
        except ValueError:
            self.status.set(f"쪽 번호는 1–{len(self.pages)} 사이여야 합니다.")
            return
        side = {"기준": "before", "결과": "after", "차이": "diff"}[self.view.get()]
        item = self.result()
        result_id = item["id"] if side != "before" and item is not None else ""
        if side != "before" and not result_id:
            self.status.set("‘이력 · 내보내기’에서 비교할 검증된 결과를 선택하세요.")
            return
        label = self.view.get()

        def shown(data):
            bitmap = tk.PhotoImage(master=self.window, data=data, format="png")
            self._bitmap = bitmap
            self.canvas.delete("all")
            self.canvas.create_image(0, 0, image=bitmap, anchor="nw")
            self.canvas.configure(scrollregion=(0, 0, bitmap.width(), bitmap.height()))
            self.canvas.xview_moveto(0)
            self.canvas.yview_moveto(0)
            if rectangle and not result_id:
                page = self.pages[number - 1]
                sx, sy = bitmap.width() / page["width"], bitmap.height() / page["height"]
                for x0, y0, x1, y1 in rectangle:
                    self.canvas.create_rectangle(x0 * sx, y0 * sy, x1 * sx, y1 * sy, outline="#955000", width=2)
                self.canvas.yview_moveto(max(0, rectangle[0][1] * sy - 30) / bitmap.height())
            self._seen_page = number
            self.page_label.configure(text=f"{label} · {number}/{len(self.pages)}쪽" + (" · 선택 위치 표시" if rectangle and not result_id else ""))
            self.status.set(self.describe_status())

        self.run("쪽 미리보기를 읽고 있습니다…", lambda: self.client.page(number, result_id=result_id, side=side if result_id else "after"), shown)

    def _show_position(self, number, boxes):
        self.page_no.set(str(number))
        self.view.set("기준")
        self.render_page(rectangle=boxes)

    def fields(self):
        if self.kind != "fill":
            protected = self.state["manifest"].get("protected_pages", [])
            return [a for a in self.state["manifest"]["anchors"] if a.get("simple") and a.get("page_hint") and a["page_hint"] not in protected and a.get("boxes")]
        assigned = {r["field_id"]: r for r in self.state["draft"]["assignments"] if r.get("value") is not None}
        return [{**field, **assigned[field["field_id"]]} for field in self.state["fields"]]

    def selected_target(self):
        index = self.index(self.targets)
        return self.target_rows[index] if index is not None else None

    def select_target(self):
        target = self.selected_target()
        if target is None or self.busy:
            return
        if self.kind == "fill":
            info = self._draft.get(target["field_id"])
            self.put(self.target_text, f"{target.get('label', target['field_id'])}\n값: {target['value']}\n근거: {target.get('source') or '–'}")
            matches = [i for i, r in enumerate(self.state["regions"]) if info and info["page"] == r["page"] and info["region"] == r["region"]]
            if matches:
                self.regions.current(matches[0])
                self.show_region()
            else:
                self.regions.set("")
        else:
            self.put(self.target_text, target["old_text"])
            pending = self._draft.get(target["anchor_id"])
            self.replacement.set(pending["new_text"] if pending else "")
            self._show_position(target["page_hint"], target["boxes"])

    def show_region(self):
        index = self.regions.current()
        if index >= 0 and not self.busy:
            region = self.state["regions"][index]
            self._show_position(region["page"], [region["region"]])

    def stage(self):
        target = self.selected_target()
        if self.busy or target is None or self.active or self.locked:
            return
        if self.kind == "fill":
            i = self.regions.current()
            if i < 0:
                self.status.set("원래 빈 셀 위치를 먼저 선택하세요.")
                return
            region = self.state["regions"][i]
            self._draft[target["field_id"]] = {"field_id": target["field_id"], "page": region["page"], "region": region["region"]}
        else:
            replacement = self.replacement.get()
            if not replacement.strip() or replacement == target["old_text"]:
                self.status.set("원문과 다른 새 문구를 입력하세요. 빈 문구로 삭제할 수 없습니다.")
                return
            self._draft[target["anchor_id"]] = {"anchor_id": target["anchor_id"], "old_text": target["old_text"], "new_text": replacement}
        self.status.set("화면 초안에 담았습니다. 저장하거나 승인하지 않았습니다.")
        self.enable_controls()

    def unstage(self):
        target = self.selected_target()
        if self.busy or self.active or self.locked or target is None:
            return
        self._draft.pop(target["field_id" if self.kind == "fill" else "anchor_id"], None)
        self.enable_controls()

    def save_proposal(self):
        if self.busy or self.active or self.locked or self.unsynced or not self._draft:
            return
        if self.kind == "fill":
            try:
                items = [self._draft[r["field_id"]] for r in self.state["draft"]["assignments"] if r.get("value") is not None]
            except KeyError:
                self.status.set("사용하는 모든 값에 빈 셀 위치를 지정하세요.")
                return
        else:
            items = list(self._draft.values())
        state = self.state
        self.mutate("수정안 저장 중 · 승인 아님", lambda: self.client.save_proposal(self.kind, state, items), clear_draft=True)

    def plan_ids(self):
        plan = self.state.get("proposal")
        return [] if not plan else [row["field_id"] for row in plan["placements"]] if self.kind == "fill" else [row["anchor_id"] for row in plan["edits"]]

    def review_position(self):
        index = self.index(self.plan_rows)
        plan = self.state.get("proposal")
        if index is None or not plan or self.busy:
            return
        if self.kind == "fill":
            entry = plan["placements"][index]
            self._show_position(entry["page"], [entry["region"]])
        else:
            entry = plan["edits"][index]
            target = next(a for a in self.state["manifest"]["anchors"] if a["anchor_id"] == entry["anchor_id"])
            self._show_position(target["page_hint"], [entry["region"]] if "region" in entry else target["boxes"])

    def approve(self):
        if not self.can_approve():
            return
        state, reviewed = self.state, self.plan_ids()
        detail = {
            "text": "표시된 원문과 교체할 글자만 승인합니다.",
            "layout": "선택 영역과 원래 표 행 높이 보존 정책을 확인하고 승인합니다.",
            "fill": "무서명 양식임과 모든 빈 셀 위치, 머리말 읽기 순서 범위를 확인하고 승인합니다.",
        }[self.kind]
        if messagebox.askyesno("정확한 수정안 승인", detail + "\n이 동작은 한글 실행, 실제 적용 또는 제출 승인이 아닙니다.", parent=self.window, default="no"):
            self.mutate("선택 수정안을 승인하고 있습니다…", lambda: self.client.approve(self.kind, state, reviewed=reviewed, confirmed=True))

    def apply(self):
        if not self.can_apply():
            return
        state = self.state
        if messagebox.askyesno("별도 적용 확인", "승인한 수정안을 작업용 사본에 적용하려고 새 한글 인스턴스를 엽니다.\n원본·기준 파일은 그대로 두고 저장·재개방 검사를 합니다. 실패하면 자동 재시도하지 않습니다.\n계속할까요?", parent=self.window, default="no"):
            self.mutate("한글 적용 요청 중 · 완료 전에 닫지 마세요", lambda: self.client.apply(self.kind, state, confirmed=True))

    def result(self):
        versions = self.state.get("versions", [])
        return versions[self._result_index] if self._result_index is not None and 0 <= self._result_index < len(versions) else None

    def select_result(self):
        self._result_index = self.index(self.history)
        self.render_result()
        if self.view.get() != "기준":
            self.render_page()

    def render_result(self):
        item = self.result()
        if item:
            downloads = item.get("downloads") or {}
            self.put(self.result_text, f"{item['id']}\n실행 상태: {self.result_label(item)}\n{item['before_pages']}쪽 → {item['after_pages']}쪽 · 변경 쪽: {item.get('changed_pages', [])}\n" + ("내용과 배치를 직접 확인한 뒤 검토용으로 보관하세요." if downloads else "이 상태에서는 결과를 내보낼 수 없습니다."))
        else:
            attempt = self.state.get("latest_attempt") or {}
            diagnostic = attempt.get("error") or attempt.get("stage") or ""
            self.put(self.result_text, ("최근 작업: " + str(diagnostic)) if diagnostic else "검증된 저장 결과가 없습니다.")
        self.enable_controls()

    @staticmethod
    def result_label(item):
        if item.get("status") == "checks_passed_needs_human_visual_review":
            return "검사 통과 · 사람 검토 필요" if item.get("artifact_integrity") == "verified" else "파일 무결성 미확인"
        return "차단 · 내보낼 수 없음"

    def export(self, kind):
        item = self.result()
        if self.busy or self.active or self.unsynced or not item or not item.get("downloads", {}).get(kind):
            return
        path = filedialog.asksaveasfilename(parent=self.window, title="검증 결과를 새 파일로 저장", initialfile=f"검토용-{item['id']}.{kind}", defaultextension=f".{kind}", filetypes=[(kind.upper(), f"*.{kind}")])
        if path:
            state = self.state
            self.run("파일 해시를 확인하고 새 파일을 저장합니다…", lambda: self.client.export(state, item["id"], kind, Path(path)), lambda _: self.status.set("검증 결과 저장 완료 · 제출 승인이 아닙니다."))

    def new_note(self):
        self.notes.selection_clear(0, "end")
        self.note_text.delete("1.0", "end")
        self.note_text.focus_set()

    def select_note(self):
        i = self.index(self.notes)
        if i is None or self.busy:
            return
        stored = self.state["comments"]["comments"][i]
        self.note_text.delete("1.0", "end")
        self.note_text.insert("1.0", stored["comment"])
        self._show_position(stored["page"], [])

    def save_note(self):
        if self.busy or self.active or self.unsynced:
            return
        text = self.note_text.get("1.0", "end-1c")
        if not text.strip():
            self.status.set("저장할 메모를 입력하세요.")
            return
        notes = list(self.state["comments"]["comments"])
        index = self.index(self.notes)
        if index is None:
            if self._seen_page is None:
                self.status.set("미리보기가 열린 쪽에만 메모를 추가할 수 있습니다.")
                return
            notes.append({"id": uuid.uuid4().hex, "page": self._seen_page, "anchor_id": None, "region": None, "comment": text})
        else:
            notes[index] = {**notes[index], "comment": text}
        self.save_notes(notes)

    def delete_note(self):
        if self.busy or self.active or self.unsynced:
            return
        index = self.index(self.notes)
        if index is not None and messagebox.askyesno("메모 삭제", "저장된 이 메모만 삭제할까요? 문서와 승인에는 영향이 없습니다.", parent=self.window, default="no"):
            notes = list(self.state["comments"]["comments"])
            notes.pop(index)
            self.save_notes(notes)

    def save_notes(self, notes):
        state = self.state
        self.mutate("메모 저장 중…", lambda: self.client.save_comments(state, notes))

    def can_approve(self):
        digest = self.state.get("proposal_sha256")
        return bool(digest and self.confirmed.get() and digest != self.state.get("approved_sha256") and not (self.busy or self.active or self.locked or self.unsynced or self._draft))

    def can_apply(self):
        digest = self.state.get("proposal_sha256")
        attempt = self.state.get("latest_attempt") or {}
        return bool(digest and self.state.get("approved_sha256") == digest and not attempt and not (self.busy or self.active or self.locked or self.unsynced or self._draft))

    def enable_controls(self):
        if not self.window.winfo_exists():
            return
        for widget in self.controls:
            widget.state(["disabled"] if self.busy else ["!disabled"])
        locked = self.active or self.locked or self.unsynced or self.busy
        self.stage_button.state(["disabled"] if locked else ["!disabled"])
        self.save_button.state(["disabled"] if locked or not self._draft else ["!disabled"])
        self.approve_button.state(["!disabled"] if self.can_approve() else ["disabled"])
        self.apply_button.state(["!disabled"] if self.can_apply() else ["disabled"])
        self.review_check.state(["disabled"] if locked or not self.plan_ids() else ["!disabled"])
        item = self.result() or {}
        for key, button in zip(("hwp", "pdf"), self.export_buttons):
            button.state(["disabled"] if self.busy or self.active or self.unsynced or key not in item.get("downloads", {}) else ["!disabled"])
        if self.kind == "text":
            for widget in (self.note_save, self.note_delete):
                widget.state(["disabled"] if self.busy or self.active or self.unsynced else ["!disabled"])
        self.draft_label.configure(text=f"화면 초안 {len(self._draft)}개 · 미저장" if self._draft else "미저장 변경 없음")

    def render_state(self, *, clear_draft=False):
        state, plan = self.state, self.state.get("proposal")
        self.target_rows = self.fields()
        self.confirmed.set(False)
        if clear_draft:
            self._draft.clear()
        self.targets.delete(0, "end")
        for item in self.target_rows:
            name = (f"{item.get('label') or item['field_id']} · {item['value']}" if self.kind == "fill" else f"{item['page_hint']}쪽 · {item['old_text']}")
            self.targets.insert("end", name.replace("\n", " ")[:160])
        if self.kind == "fill":
            self.regions.configure(values=[f"{r['page']}쪽 · 빈 셀 {i + 1} · ({r['region'][0]:.1f}, {r['region'][1]:.1f})" for i, r in enumerate(state["regions"])])
            self.regions.set("")
        self.plan_rows.delete(0, "end")
        if plan:
            if self.kind == "fill":
                for index, item in enumerate(plan["placements"], 1):
                    self.plan_rows.insert("end", f"{index}. {item['page']}쪽 · 빈 셀 위치 보기")
            else:
                for index, item in enumerate(plan["edits"], 1):
                    self.plan_rows.insert("end", f"{index}. {item['old_text'].replace(chr(10), ' ')[:90]}")
        self.put(self.plan_text, self.describe_plan(plan))
        self.history.delete(0, "end")
        for item in state.get("versions", []):
            self.history.insert("end", f"{item['id']} · {self.result_label(item)}")
        if self.history.size():
            self._result_index = min(self._result_index or 0, self.history.size() - 1)
            self.history.selection_set(self._result_index)
        else:
            self._result_index = None
        if self.kind == "text":
            self.notes.delete(0, "end")
            for note in state["comments"]["comments"]:
                self.notes.insert("end", f"{note['page']}쪽 · {note['comment'][:110]}")
        self.render_result()
        self.status.set(self.describe_status())
        self.enable_controls()
        self.watch()

    def describe_plan(self, plan):
        if not plan:
            return self.state.get("proposal_error") or "저장된 수정안이 없습니다. ‘수정안 작성’에서 먼저 저장하세요."
        lines = [f"수정안 SHA-256: {self.state['proposal_sha256']}", ""]
        if self.kind == "fill":
            mapping = {item["field_id"]: item for item in self.target_rows}
            for placement in plan["placements"]:
                item = mapping[placement["field_id"]]
                lines.append(f"{placement['page']}쪽 · {item.get('label') or item['field_id']} → {item['value']}\n영역: {placement['region']}")
            header = plan["header_spine"]
            lines.extend(["", "무서명 양식의 검토한 빈 셀에만 입력합니다.", "머리말 읽기 순서 변경 범위: " + (f"{header['idref']}: {header['before'] or 'yes'} → {header['after']} 허용" if header else "없음")])
        else:
            anchors = {a["anchor_id"]: a for a in self.state["manifest"]["anchors"]}
            for edit in plan["edits"]:
                lines.append(f"{anchors[edit['anchor_id']]['page_hint']}쪽\n이전: {edit['old_text']}\n변경: {edit['new_text']}")
                if "region" in edit:
                    lines.append(f"원래 영역: {edit['region']}")
            if self.kind == "layout":
                lines.append("\n원래 행 높이 (한글 내부 단위):")
                for spec in plan["row_minima"]:
                    lines.append(f"표 {spec['table_id']}: {spec['row_heights']}")
        return "\n\n".join(lines)

    def describe_status(self):
        if self.active:
            return "한글 적용 검사 중 · 원본 보호 · 완료 전에 닫지 마세요"
        if self.locked:
            return "이전 또는 현재 작업 잠금이 있습니다. 다시 적용하거나 잠금을 지우지 않습니다."
        attempt = self.state.get("latest_attempt") or {}
        if attempt.get("status") == "checks_passed_needs_human_visual_review":
            return "저장·재개방 검사 통과 · ‘이력 · 내보내기’에서 사람이 최종 확인하세요."
        if attempt.get("status") == "blocked":
            return "적용 검사가 차단됐습니다. ‘이력 · 내보내기’에서 원인을 확인하세요."
        digest = self.state.get("proposal_sha256")
        return "정확한 수정안 승인 완료 · 실제 적용은 별도입니다." if digest and digest == self.state.get("approved_sha256") else "원본은 덮어쓰지 않습니다. 수정안 저장, 승인, 적용은 별도입니다."

    def mutate(self, label, write, *, clear_draft=False):
        def operation():
            write()
            return self.client.snapshot(self.kind)

        self.run(label, operation, lambda state: self.accept(state, clear_draft=clear_draft), mutation=True)

    def accept(self, state, *, clear_draft=False):
        self.state = state
        self.unsynced = False
        self.render_state(clear_draft=clear_draft)

    def refresh(self):
        if self.busy:
            return
        if self._draft and not messagebox.askyesno("새로 읽기", "저장하지 않은 화면 초안을 버리고 현재 작업 상태를 읽을까요?", parent=self.window, default="no"):
            return
        self.run("현재 작업 상태를 읽고 있습니다…", lambda: self.client.snapshot(self.kind), lambda state: self.accept(state, clear_draft=True))

    def watch(self):
        if self.active and self._refresh_id is None:
            def refresh_once():
                self._refresh_id = None
                if self.busy:
                    self.watch()
                    return
                self.run("저장·재개방 검사 결과를 기다리는 중…", lambda: self.client.snapshot(self.kind), self.accept)
            self._refresh_id = self.window.after(750, refresh_once)

    def run(self, label, operation, done, *, mutation=False):
        if self.busy:
            return
        queue = SimpleQueue()
        self.busy = True
        self.status.set(label)
        self.enable_controls()

        def worker():
            try:
                queue.put((True, operation()))
            except Exception as error:
                queue.put((False, str(error)[:1600]))

        def receive():
            try:
                good, value = queue.get_nowait()
            except Empty:
                self.window.after(60, receive)
                return
            self.busy = False
            if good:
                try:
                    done(value)
                except (KeyError, ValueError, tk.TclError) as error:
                    self.unsynced = True
                    self.status.set(f"화면 상태를 확인하지 못했습니다: {error}. 다시 읽어주세요.")
            else:
                if mutation:
                    self.unsynced = True
                self.status.set(f"완료하지 못했습니다: {value}" + (" · 초안을 유지했습니다. 결과를 다시 읽어주세요." if mutation else ""))
            self.enable_controls()

        try:
            Thread(target=worker, name="HWP-legacy-review-action", daemon=True).start()
        except RuntimeError as error:
            queue.put((False, str(error)[:1600]))
        self.window.after(60, receive)

    def may_close(self):
        if self.busy or self.active:
            self.status.set("진행 중인 요청 또는 한글 적용 검사를 마친 뒤 닫으세요.")
            return False
        if self._draft:
            return messagebox.askyesno("검토 창 닫기", "저장하지 않은 화면 초안이 있습니다. 버리고 닫을까요?", parent=self.window, default="no")
        return True

    def close(self):
        if not self.may_close():
            return
        self.destroy()

    def destroy(self):
        if self._refresh_id is not None:
            self.window.after_cancel(self._refresh_id)
        self.window.destroy()
        self.on_close()
