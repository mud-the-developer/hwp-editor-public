"""Local Tk presentation ONLY; no file IO, Hancom, approvals or browser launches."""

import tkinter as tk
from tkinter import ttk

from hwp_resources import APP_VERSION


def configure_style(root):
    """Shared local Tk styling for the retained tools and optional setup."""
    style = ttk.Style(root)
    style.theme_use("clam")
    style.configure(
        ".", font=("맑은 고딕", 11), background="#f5f7fa", foreground="#172033"
    )
    root.option_add("*TCombobox*Listbox.font", ("맑은 고딕", 11))
    style.configure("TFrame", background="#f5f7fa")
    style.configure("TLabel", background="#f5f7fa", foreground="#172033")
    style.configure(
        "TButton",
        padding=(16, 10),
        background="#ffffff",
        bordercolor="#d8e0ea",
        relief="flat",
    )
    style.map("TButton", background=[("active", "#f1f5f9"), ("disabled", "#f5f7fa")])
    style.configure(
        "Primary.TButton",
        padding=(22, 13),
        background="#2563eb",
        foreground="white",
        borderwidth=0,
    )
    style.map(
        "Primary.TButton",
        background=[("disabled", "#e2e8f0"), ("active", "#1d4ed8")],
        foreground=[("disabled", "#78869a")],
    )
    style.configure(
        "Title.TLabel", font=("맑은 고딕", 24, "bold"), foreground="#172033"
    )
    style.configure("Heading.TLabel", font=("맑은 고딕", 15, "bold"))
    style.configure("Muted.TLabel", foreground="#63728a", font=("맑은 고딕", 10))
    style.configure(
        "Card.TFrame",
        background="white",
        bordercolor="#e2e8f0",
        borderwidth=1,
        relief="solid",
    )
    style.configure("CardBody.TFrame", background="white", borderwidth=0)
    style.configure("Card.TLabel", background="white")
    style.configure(
        "CardTitle.TLabel",
        background="white",
        font=("맑은 고딕", 17, "bold"),
        foreground="#172033",
    )
    style.configure("CardMuted.TLabel", background="white", foreground="#63728a")
    style.configure("Error.TFrame", background="#fff3ee")
    style.configure("Error.TLabel", background="#fff3ee", foreground="#993e24")
    style.configure(
        "Horizontal.TProgressbar",
        background="#2563eb",
        troughcolor="#e8edf5",
        borderwidth=0,
    )


def build_ui(app, root, workspace, modes):
    root.title("HWP Editor")
    root.geometry("880x740")
    root.minsize(700, 600)
    root.configure(background="#f5f7fa")
    menu = tk.Menu(root, tearoff=False)
    app.action_menu = tk.Menu(menu, tearoff=False)
    app.action_menu.add_command(label="새 보고서 (HWPX) · Ctrl+N", command=app.reports.new)
    app.action_menu.add_command(label="새 HWP 문서 · Ctrl+O", command=app.new_document)
    app.action_menu.add_command(label="작업 폴더 열기", command=app.choose_folder)
    app.action_menu.add_command(label="작업 목록", command=app.show_library)
    app.action_menu.add_command(label="고급: 준비된 HWPX 원고 합성", command=app.reports.advanced)
    app.action_menu.add_separator()
    app.action_menu.add_command(label="닫기", command=app.close)
    menu.add_cascade(label="작업", menu=app.action_menu)
    root.configure(menu=menu)
    configure_style(root)
    shell = ttk.Frame(root)
    shell.pack(fill="both", expand=True)
    app.canvas = tk.Canvas(shell, background="#f5f7fa", highlightthickness=0)
    app.canvas.pack(side="left", fill="both", expand=True)
    scrollbar = ttk.Scrollbar(shell, orient="vertical", command=app.canvas.yview)
    scrollbar.pack(side="right", fill="y")
    app.canvas.configure(yscrollcommand=scrollbar.set)
    body = ttk.Frame(app.canvas, padding=(28, 24))
    body_id = app.canvas.create_window(0, 0, window=body, anchor="nw")
    body.columnconfigure(0, weight=1)
    body.bind(
        "<Configure>",
        lambda event: app.canvas.configure(scrollregion=app.canvas.bbox("all")),
    )

    def wrap(parent, width):
        for widget in parent.winfo_children():
            if isinstance(widget, ttk.Label) and widget.cget("wraplength"):
                widget.configure(wraplength=max(280, width - 110))
            wrap(widget, width)

    def resize(event):
        app.canvas.itemconfigure(body_id, width=event.width)
        wrap(body, event.width)

    app.canvas.bind("<Configure>", resize)
    app.wheel_delta = 0
    root.bind("<MouseWheel>", app.scroll_wheel)
    ttk.Label(body, text="HWPX 보고서 · 문서 검토", style="Title.TLabel").grid(
        row=0, column=0, sticky="w"
    )
    ttk.Label(
        body,
        text="원본 양식과 준비된 원고를 비교하고, 승인한 범위만 새 검토본으로 만드세요.",
        style="Muted.TLabel",
        wraplength=700,
    ).grid(row=1, column=0, sticky="w", pady=(5, 20))
    steps = ttk.Frame(body)
    steps.grid(row=2, column=0, sticky="ew", pady=(0, 18))
    app.step_labels = []
    for i, text in enumerate(("1  문서 선택", "2  원본 확인", "3  검토 시작")):
        steps.columnconfigure(i, weight=1)
        label = tk.Label(
            steps,
            text=text,
            font=("맑은 고딕", 10, "bold"),
            padx=12,
            pady=10,
            background="#e9edf4",
            foreground="#79869b",
        )
        label.grid(row=0, column=i, sticky="ew", padx=(0, 6))
        app.step_labels.append(label)
    card = ttk.Frame(body, style="Card.TFrame", padding=24)
    card.grid(row=3, column=0, sticky="ew")
    card.columnconfigure(0, weight=1)
    app.document_title = tk.StringVar(value="보고서 양식과 HWPX 원고를 선택하세요")
    ttk.Label(
        card, textvariable=app.document_title, style="CardTitle.TLabel", wraplength=680
    ).grid(row=0, column=0, sticky="w")
    app.summary = tk.StringVar(
        value="새 보고서는 한글 실행 없이 HWPX를 직접 읽습니다. 기존 HWP 문서는 별도 원본 확인 경로를 사용합니다."
    )
    ttk.Label(
        card,
        textvariable=app.summary,
        style="CardMuted.TLabel",
        wraplength=680,
        justify="left",
    ).grid(row=1, column=0, sticky="ew", pady=(10, 20))
    app.mode = tk.StringVar(value=modes["memo"][0])
    app.review_options = ttk.Frame(card, style="CardBody.TFrame")
    app.review_options.grid(row=2, column=0, sticky="ew", pady=(0, 16))
    app.review_options.columnconfigure(0, weight=1)
    app.mode_picker = ttk.Combobox(
        app.review_options,
        textvariable=app.mode,
        values=[v[0] for v in modes.values()],
        state="readonly",
        font=("맑은 고딕", 11),
    )
    app.mode_picker.grid(row=0, column=0, sticky="ew", ipady=6)
    app.mode_picker.bind("<<ComboboxSelected>>", lambda event: app.update_controls())
    app.mode_picker.bind("<MouseWheel>", app.scroll_wheel)
    app.mode_help = tk.StringVar()
    ttk.Label(
        app.review_options,
        textvariable=app.mode_help,
        style="CardMuted.TLabel",
        wraplength=680,
    ).grid(row=1, column=0, sticky="w", pady=(8, 0))
    app.primary_actions = ttk.Frame(card, style="CardBody.TFrame")
    app.primary_actions.grid(row=3, column=0, sticky="ew")
    app.report_new_button = ttk.Button(
        app.primary_actions,
        text="새 보고서 (HWPX)",
        style="Primary.TButton",
        command=app.reports.new,
    )
    app.report_revision_button = ttk.Button(
        app.primary_actions, text="새 원고로 다시 검토", command=app.reports.revise
    )
    app.new_button = ttk.Button(
        app.primary_actions,
        text="HWP 문서 선택",
        style="TButton",
        command=app.new_document,
    )
    app.add_button = ttk.Button(
        app.primary_actions,
        text="이 폴더에 HWP 추가",
        style="Primary.TButton",
        command=app.add_hwp,
    )
    app.prepare_button = ttk.Button(
        app.primary_actions,
        text="원본 확인 시작",
        style="Primary.TButton",
        command=app.prepare_hwp,
    )
    app.open_button = ttk.Button(
        app.primary_actions,
        text="검토 시작",
        style="Primary.TButton",
        command=lambda: app.launch(app.edge),
    )
    app.export_button = ttk.Button(
        app.primary_actions, text="저장된 메모 내보내기", command=app.export_saved_notes
    )
    app.cancel_button = ttk.Button(
        app.primary_actions, text="중단 요청", command=app.cancel_preparation
    )
    app.choose = ttk.Button(
        app.primary_actions, text="작업 폴더 열기", command=app.choose_folder
    )
    app.choose.pack(side="right")
    app.status = tk.StringVar(value="문서를 선택하면 시작할 수 있습니다")
    ttk.Label(
        body, textvariable=app.status, style="Heading.TLabel", wraplength=700
    ).grid(row=4, column=0, sticky="w", pady=(18, 6))
    app.elapsed = tk.StringVar()
    ttk.Label(
        body, textvariable=app.elapsed, style="Muted.TLabel", wraplength=700
    ).grid(row=5, column=0, sticky="w")
    app.progress = ttk.Progressbar(body, mode="indeterminate", maximum=6)
    app.progress.grid(row=6, column=0, sticky="ew", pady=(8, 0))
    app.progress.grid_remove()
    app.error_panel = ttk.Frame(body, style="Error.TFrame", padding=14)
    app.error_panel.grid(row=7, column=0, sticky="ew", pady=(12, 0))
    app.error_panel.columnconfigure(0, weight=1)
    app.error_hint = tk.StringVar()
    ttk.Label(
        app.error_panel,
        textvariable=app.error_hint,
        style="Error.TLabel",
        wraplength=660,
        justify="left",
    ).grid(row=0, column=0, sticky="ew")
    ttk.Button(app.error_panel, text="기술 정보 보기", command=app.show_error).grid(
        row=1, column=0, sticky="w", pady=(8, 0)
    )
    app.error_panel.grid_remove()
    ttk.Label(
        body,
        text="원본은 덮어쓰지 않습니다. 확인 작업은 문서 수정·제출 승인이 아닙니다.",
        style="Muted.TLabel",
        wraplength=700,
    ).grid(row=8, column=0, sticky="w", pady=(16, 8))
    utilities = ttk.Frame(body)
    utilities.grid(row=9, column=0, sticky="ew")
    ttk.Button(utilities, text="폴더 · 연결 설정", command=app.toggle_details).pack(
        side="left"
    )
    app.library_button = ttk.Button(
        utilities, text="작업 목록", command=app.show_library
    )
    app.library_button.pack(side="left", padx=8)
    ttk.Button(utilities, text="사용 안내", command=app.show_help).pack(side="left")
    ttk.Button(utilities, text="AGPL · 소스", command=app.show_license).pack(
        side="left", padx=8
    )
    app.version_label = ttk.Label(
        body, text=f"v{APP_VERSION} · 로컬 작업", style="Muted.TLabel"
    )
    app.version_label.grid(row=10, column=0, sticky="w", pady=(8, 0))
    app.details = ttk.Frame(body, padding=(0, 14))
    app.details.grid(row=11, column=0, sticky="ew")
    app.details.columnconfigure(0, weight=1)
    app.path = tk.StringVar(value=str(workspace) if workspace else "")
    ttk.Label(app.details, text="현재 작업 폴더", style="Muted.TLabel").grid(
        row=0, column=0, sticky="w"
    )
    app.entry = ttk.Entry(app.details, textvariable=app.path)
    app.entry.grid(row=1, column=0, sticky="ew", ipady=6, pady=(4, 8))
    app.check = ttk.Button(
        app.details, text="폴더 다시 확인", command=app.check_workspace
    )
    app.check.grid(row=1, column=1, padx=(8, 0))
    address = ttk.Frame(app.details)
    address.grid(row=2, column=0, columnspan=2, sticky="ew")
    address.columnconfigure(0, weight=1)
    app.address = tk.StringVar()
    ttk.Entry(address, textvariable=app.address, state="readonly").grid(
        row=0, column=0, sticky="ew", ipady=6
    )
    app.copy_button = ttk.Button(address, text="주소 복사", command=app.copy_address)
    app.copy_button.grid(row=0, column=1, padx=(8, 0))
    tools = ttk.Frame(app.details)
    tools.grid(row=3, column=0, columnspan=2, sticky="ew", pady=(10, 0))
    app.browser_button = ttk.Button(
        tools, text="기본 브라우저로 열기", command=lambda: app.launch(None)
    )
    app.browser_button.pack(side="left")
    app.stop_button = ttk.Button(
        tools, text="내 검토 서버 닫기", command=app.stop_server
    )
    app.stop_button.pack(side="left", padx=8)
    ttk.Button(tools, text="작업 폴더 보기", command=app.reveal_project).pack(
        side="left"
    )
    app.details.grid_remove()
