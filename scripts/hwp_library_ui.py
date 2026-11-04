"""Owned local workspace list dialog. Read-only scan; selection is revalidated."""
from datetime import datetime
import tkinter as tk
from tkinter import ttk, filedialog

from hwp_workspace import STATES, filter_library, default_library


class LibraryWindow:
    def __init__(self, app):
        self.app = app
        self.top = tk.Toplevel(app.root)
        self.top.title("작업 목록 · 로컬 폴더")
        self.top.geometry("900x590")
        self.top.minsize(700, 480)
        self.top.transient(app.root)
        self.top.protocol("WM_DELETE_WINDOW", self.close)
        self.base = default_library()
        self.listing = None
        self.rows = {}
        self.query = tk.StringVar()
        self.state = tk.StringVar(value="전체")
        self.status = tk.StringVar(value="작업 폴더를 읽고 있습니다…")
        body = ttk.Frame(self.top, padding=20)
        body.pack(fill="both", expand=True)
        body.columnconfigure(0, weight=1);body.rowconfigure(2, weight=1)
        ttk.Label(body, text="작업 목록", style="Heading.TLabel").grid(row=0, column=0, sticky="w")
        toolbar = ttk.Frame(body)
        toolbar.grid(row=1, column=0, sticky="ew", pady=(12, 10))
        toolbar.columnconfigure(0, weight=1)
        ttk.Label(toolbar, text="문서 / 폴더 이름 검색", style="Muted.TLabel").grid(row=0, column=0, sticky="w", pady=(0, 4))
        self.search = ttk.Entry(toolbar, textvariable=self.query)
        self.search.grid(row=1, column=0, sticky="ew", ipady=6)
        self.query.trace_add("write", lambda *_:self.render())
        self.filter = ttk.Combobox(toolbar, textvariable=self.state, state="readonly", width=16, values=["전체", *STATES.values()])
        self.filter.grid(row=1, column=1, padx=8, ipady=6)
        self.filter.bind("<<ComboboxSelected>>", lambda event:self.render())
        self.refresh = ttk.Button(toolbar, text="새로 읽기", command=self.reload)
        self.refresh.grid(row=1, column=2)
        frame = ttk.Frame(body)
        frame.grid(row=2, column=0, sticky="nsew")
        frame.columnconfigure(0, weight=1);frame.rowconfigure(0, weight=1)
        style = ttk.Style(self.top)
        style.configure("Library.Treeview", rowheight=30, font=("Malgun Gothic", 10), foreground="#162a44")
        self.tree = ttk.Treeview(frame, style="Library.Treeview", columns=("name", "state", "pages", "modified"), show="headings", selectmode="browse")
        for key, title, width in (("name", "문서 / 작업 폴더", 340), ("state", "상태", 140), ("pages", "쪽", 50), ("modified", "자료 변경 시각", 155)):
            self.tree.heading(key, text=title);self.tree.column(key, width=width, minwidth=40, stretch=key == "name")
        self.tree.grid(row=0, column=0, sticky="nsew")
        scrollbar = ttk.Scrollbar(frame, orient="vertical", command=self.tree.yview)
        scrollbar.grid(row=0, column=1, sticky="ns")
        self.tree.configure(yscrollcommand=scrollbar.set)
        self.tree.bind("<<TreeviewSelect>>", lambda event:self.selection_changed())
        self.tree.bind("<Double-1>", lambda event:self.open_selected())
        self.tree.bind("<Return>", lambda event:self.open_selected())
        self.hint = tk.StringVar(value="검색은 문서·폴더 이름 기준입니다. 내용이나 개인정보를 외부로 보내지 않습니다.")
        self.hint_label = ttk.Label(body, textvariable=self.hint, wraplength=790, style="Muted.TLabel")
        self.hint_label.grid(row=3, column=0, sticky="ew", pady=(10, 6))
        self.status_label = ttk.Label(body, textvariable=self.status, wraplength=790)
        self.status_label.grid(row=4, column=0, sticky="ew")
        self.top.bind("<Configure>", self.resize)
        actions = ttk.Frame(body)
        actions.grid(row=5, column=0, sticky="ew", pady=(12, 0))
        self.open = ttk.Button(actions, text="선택한 작업 열기", style="Primary.TButton", command=self.open_selected)
        self.open.pack(side="left")
        self.choose = ttk.Button(actions, text="다른 폴더 모음", command=self.choose_base)
        self.choose.pack(side="left", padx=8)
        ttk.Button(actions, text="닫기", command=self.close).pack(side="right")
        self.top.bind("<Control-f>", lambda event:self.search.focus_set())
        self.top.bind("<Escape>", lambda event:self.close())
        self.reload()
        self.search.focus_set()

    def resize(self, event):
        if event.widget is self.top:
            for label in (self.hint_label, self.status_label):label.configure(wraplength=max(300, event.width - 48))

    def exists(self):return bool(self.top.winfo_exists())

    def close(self):
        self.app.library_window = None
        self.top.destroy()

    def reload(self):
        if not self.app.busy:self.app.load_library(self.base)

    def choose_base(self):
        if self.app.busy:return
        selected = filedialog.askdirectory(parent=self.top, title="작업 폴더들이 들어 있는 상위 폴더 선택", mustexist=True)
        if selected:
            from pathlib import Path
            self.base = Path(selected)
            self.reload()

    def display(self, listing):
        self.listing = listing
        self.render()

    def controls(self):
        blocked = self.app.busy or self.app.closing or self.app.session.running or getattr(self.app.session, "stopping", False)
        self.refresh.configure(state="disabled" if self.app.busy else "normal")
        self.choose.configure(state="disabled" if self.app.busy else "normal")
        self.open.configure(state="normal" if self.tree.selection() and not blocked else "disabled")

    def render(self):
        if self.listing is None:
            self.controls();return
        selection = self.tree.selection()
        selected_root = self.rows[selection[0]].root if selection and selection[0] in self.rows else None
        self.tree.delete(*self.tree.get_children())
        state = next((key for key, label in STATES.items() if label == self.state.get()), "all")
        visible = filter_library(self.listing, self.query.get(), state)
        self.rows = {}
        for index, row in enumerate(visible):
            key = f"row-{index}";self.rows[key] = row
            try:modified = datetime.fromtimestamp(row.modified).strftime("%Y-%m-%d %H:%M") if row.modified else "확인 필요"
            except (OSError, ValueError, OverflowError):modified = "확인 필요"
            self.tree.insert("", "end", iid=key, values=(row.name + (" — " + row.root.name if row.name != row.root.name else ""), STATES[row.state], row.pages if row.pages is not None else "—", modified))
            if selected_root == row.root:self.tree.selection_set(key)
        self.status.set(f"{len(visible)} / {len(self.listing.entries)}개 · {self.listing.base or '기본 저장 위치 없음'}"
                        + (" · 목록 한도 도달: 일부 작업은 직접 폴더를 선택하세요" if self.listing.limited else ""))
        if not visible:self.hint.set("목록이 비었습니다. 검색/필터를 바꾸거나 문서를 선택해 새 작업을 만드세요.")
        else:self.hint.set("‘검토 자료 있음’은 파일 존재 상태일 뿐입니다. 열 때 전체 검증을 하며 수정 승인·제출 완료로 표시하지 않습니다.")
        self.controls()

    def selection_changed(self):
        selection = self.tree.selection()
        if selection and selection[0] in self.rows:
            row = self.rows[selection[0]]
            self.hint.set(str(row.root) + "\n" + row.note)
        self.controls()

    def open_selected(self):
        if self.app.busy or self.app.closing or self.app.session.running or getattr(self.app.session, "stopping", False):return
        selection = self.tree.selection()
        if not selection or selection[0] not in self.rows:return
        root = self.rows[selection[0]].root
        self.close()
        self.app.path.set(str(root))
        self.app.check_workspace()  # same mandatory baseline/comments gate as folder picker
