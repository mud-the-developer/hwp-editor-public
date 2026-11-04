"""Standard-Tk exact HWPX intake. No server, approval, model or document writes."""

from copy import deepcopy
from dataclasses import dataclass
from pathlib import Path
import queue
import threading
import tkinter as tk
from tkinter import filedialog, messagebox, ttk

import hwp_report_harness as harness
import hwp_report_project as project
import hwp_review as review
import hwp_template_draft as draft

_CAPTIONS = {
    "선택하세요": None,
    "기존 그림 하단 캡션 정책": "legacy",
    "표·그림 개체 캡션과 범위 내 상호참조": draft.captions.POLICY,
}


@dataclass(frozen=True)
class ParagraphChoice:
    index: int
    text: str
    is_paragraph: bool
    tables: int
    images: int


@dataclass(frozen=True)
class DocumentPreview:
    path: Path
    sha256: str
    paragraphs: tuple


def capture_document(path, cancelled=None):
    """Digest and parse the very same bounded byte buffer, off the Tk thread."""
    path = Path(path).absolute()
    if path.suffix.lower() != ".hwpx":
        raise ValueError("변환하지 않은 실제 .hwpx 파일을 선택하세요.")
    if cancelled is not None and cancelled.is_set():
        raise InterruptedError("문서 읽기가 취소되었습니다.")
    raw = project._capture(path, 512 * 1024 * 1024)
    digest = review.digest(raw)
    if cancelled is not None and cancelled.is_set():
        raise InterruptedError("문서 읽기가 취소되었습니다.")
    parts = draft.parts_from(raw)
    if cancelled is not None and cancelled.is_set():
        raise InterruptedError("문서 읽기가 취소되었습니다.")
    section = review.xml_root(parts[draft.PART])
    paragraphs = []
    for index, node in enumerate(section):
        if cancelled is not None and cancelled.is_set():
            raise InterruptedError("문서 읽기가 취소되었습니다.")
        paragraphs.append(ParagraphChoice(
            index, " / ".join(draft.texts([node])), node.tag == draft.HP + "p",
            sum(1 for _ in node.iter(draft.HP + "tbl")),
            sum(1 for _ in node.iter(draft.HP + "pic")),
        ))
    return DocumentPreview(path, digest, tuple(paragraphs))


def capture_evidence(path):
    path = Path(path).absolute()
    if path.suffix.lower() not in (".txt", ".md"):
        raise ValueError("UTF-8 .txt/.md 발췌문만 지원합니다.")
    raw = project._capture(path, 524288)
    raw.decode("utf-8")
    return {"path": str(path), "sha256": review.digest(raw)}


def exact_range(preview, start, end, *, source=False):
    if type(start) is not int or type(end) is not int or not 0 <= start < end <= len(preview.paragraphs):
        raise ValueError("문단 범위는 0기준 [시작, 끝 제외) 연속 구간이어야 합니다.")
    if source and (start == 0 or end == len(preview.paragraphs)):
        raise ValueError("원본의 앞·뒤 경계 문단을 각각 남겨야 합니다. 전체 교체는 지원하지 않습니다.")
    if any(not row.is_paragraph for row in preview.paragraphs[start:end]):
        raise ValueError("실제 최상위 문단만 선택할 수 있습니다.")
    return [start, end]


def selected_range(preview, indexes, *, source=False):
    values = sorted(set(indexes))
    if not values or any(type(i) is not int for i in values) or values != list(range(values[0], values[-1] + 1)):
        raise ValueError("하나의 연속 문단 구간을 명시적으로 선택하세요.")
    return exact_range(preview, values[0], values[-1] + 1, source=source)


def basic_inputs(source, payload, target_range, payload_range, *, label, caption_policy, goal, decisions, feedback=None, evidence=None, own_achievement=False):
    """Build pinned contracts, without inferring ranges or waiving harness gates."""
    target = exact_range(source, *target_range, source=True)
    incoming = exact_range(payload, *payload_range)
    if caption_policy not in ("legacy", draft.captions.POLICY):
        raise ValueError("캡션 정책을 명시적으로 선택하세요.")
    job = {
        "label": harness.text(label, 120), "source": str(source.path),
        "source_sha256": source.sha256, "payload": str(payload.path),
        "payload_sha256": payload.sha256, "target_range": target,
        "payload_ranges": [incoming],
    }
    if caption_policy != "legacy":
        job["caption_policy"] = caption_policy
    policy = deepcopy(feedback) if feedback is not None else {
        "schema": harness.FEEDBACK, "mode": "authoring",
    }
    if policy.get("mode") != "authoring":
        raise ValueError("용어/그림 전용 수정은 고급 프로필과 피드백을 그대로 사용하세요.")
    policy.update(goal=goal, decisions=list(decisions))
    if evidence is not None:
        if own_achievement is not True:
            raise ValueError("발췌문을 자체 성과 근거로 사용할 권한을 명시적으로 확인하세요.")
        declared = policy.setdefault("sources", [])
        if not isinstance(declared, list) or any(not isinstance(item, dict) for item in declared):
            raise ValueError("기존 근거 목록이 올바르지 않습니다.")
        added = {
            "path": evidence["path"], "sha256": evidence["sha256"],
            "ownership": "own", "use": "achievement",
        }
        if not any(all(item.get(key) == value for key, value in added.items()) for item in declared):
            identifiers = {item.get("id") for item in declared}
            identifier, suffix = "own-excerpt", 1
            while identifier in identifiers:
                suffix += 1
                identifier = "own-excerpt-" + str(suffix)
            declared.append({"id": identifier, **added})
    harness.feedback_data(project._json_bytes(policy, 65536))
    return {"contract": draft.CONTRACT, "jobs": [job]}, policy


def _import_json(path, kind):
    path = Path(path).absolute()
    data, _ = project._read_json(path, 65536 if kind == "feedback" else 262144)
    # Match the existing harness's cwd-relative input contract, then pin its
    # canonical absolute paths. Check lexical links before resolving dot parts.
    def absolute(value):
        if not isinstance(value, str):
            raise ValueError("입력 파일 경로는 문자열이어야 합니다.")
        candidate = project._unlinked(Path(value).absolute(), allow_parent=True)
        return str(candidate.resolve(strict=True))

    if kind == "feedback":
        harness.feedback_data(project._json_bytes(data, 65536))
        for key in ("sources", "baselines"):
            for record in data.get(key, []):
                record["path"] = absolute(record["path"])
    else:
        if data.get("contract") != draft.CONTRACT or not isinstance(data.get("jobs"), list) or not 1 <= len(data["jobs"]) <= 2:
            raise ValueError(f"작업 1~2개의 {draft.CONTRACT} 프로필이 필요합니다.")
        for job in data["jobs"]:
            required = {"label", "source", "payload", "source_sha256", "payload_sha256", "target_range", "payload_ranges"}
            if not isinstance(job, dict) or not required <= job.keys():
                raise ValueError("작업에는 이름, 두 파일/해시와 정확한 두 범위가 필요합니다.")
            harness.text(job["label"])
            for key in ("source", "payload"):
                job[key] = absolute(job[key])
                if not isinstance(job[key + "_sha256"], str) or not project._HASH.fullmatch(job[key + "_sha256"]):
                    raise ValueError("가져온 프로필의 정확한 파일 SHA256이 필요합니다.")
            if not isinstance(job.get("payload_ranges"), list) or not 1 <= len(job["payload_ranges"]) <= 4:
                raise ValueError("작업마다 명시적 원고 구간 1~4개가 필요합니다.")
            last = -1
            for bounds in job["payload_ranges"]:
                if not isinstance(bounds, list) or len(bounds) != 2 or any(type(i) is not int for i in bounds) or not 0 <= bounds[0] < bounds[1] or bounds[0] < last:
                    raise ValueError("원고 범위는 겹치지 않는 0기준 연속 구간 목록이어야 합니다.")
                last = bounds[1]
            bounds = job["target_range"]
            if not isinstance(bounds, list) or len(bounds) != 2 or any(type(i) is not int for i in bounds) or not 0 < bounds[0] < bounds[1]:
                raise ValueError("앞 경계를 남긴 원본의 정확한 교체 구간이 필요합니다.")
            if "caption_policy" in job:
                draft.captions.enabled(job)
    return data


class InputMailbox:
    """Only current owned asynchronous selections are observable by the dialog."""

    def __init__(self):
        self._tokens = {}
        self._closed = False
        self._results = queue.Queue()

    def begin(self, key):
        if self._closed:
            raise RuntimeError("닫힌 입력 창입니다.")
        previous = self._tokens.get(key)
        if previous is not None:
            previous.set()
        token = threading.Event()
        self._tokens[key] = token
        return token

    def publish(self, key, token, value=None, error=None):
        self._results.put((key, token, value, error))

    def take(self):
        accepted = []
        while True:
            try:
                key, token, value, error = self._results.get_nowait()
            except queue.Empty:
                return accepted
            if not self._closed and self._tokens.get(key) is token and not token.is_set():
                accepted.append((key, value, error))

    def close(self):
        self._closed = True
        for token in self._tokens.values():
            token.set()
        self.take()


class ReportDraftWindow:
    def __init__(self, parent, on_prepare, *, seed=None):
        self.top = tk.Toplevel(parent)
        self.top.title("HWPX 보고서 검토 준비")
        self.top.geometry("1000x850")
        self.top.minsize(750, 620)
        self.top.transient(parent)
        self.on_prepare = on_prepare
        self._closed = False
        self._mailbox = InputMailbox()
        self._after = set()
        self._previews = {}
        self._pending = set()
        self._invalid = {}
        self._feedback = deepcopy(seed[1]) if seed is not None else None
        self._advanced_profile = deepcopy(seed[0]) if seed is not None else None
        self._evidence = None
        self._expected = {}
        self._render_tokens = {}
        self._build()
        if seed is not None:
            self._seed(seed)
        self.top.protocol("WM_DELETE_WINDOW", self.close)
        self.top.bind("<Escape>", lambda event: self.close())
        self.top.bind("<Control-Return>", lambda event: self._prepare())
        self.top.bind("<MouseWheel>", self._wheel)
        self.top.bind("<FocusIn>", self._reveal_focus)
        self._later(60, self._poll)

    def _later(self, delay, callback):
        def owned():
            self._after.discard(handle)
            if not self._closed:
                callback()
        handle = self.top.after(delay, owned)
        self._after.add(handle)

    def _build(self):
        footer = ttk.Frame(self.top, padding=12)
        footer.pack(side="bottom", fill="x")
        self.status = tk.StringVar(value="파일과 정확한 범위를 선택하세요. 승인/생성은 다음 검토 화면에서 별도 진행합니다.")
        ttk.Label(footer, textvariable=self.status, wraplength=740).pack(side="left", fill="x", expand=True)
        ttk.Button(footer, text="닫기 (Esc)", command=self.close).pack(side="right", padx=6)
        self.prepare_button = ttk.Button(footer, text="새 검토 준비 (Ctrl+Enter)", command=self._prepare)
        self.prepare_button.pack(side="right")
        shell = ttk.Frame(self.top)
        shell.pack(fill="both", expand=True)
        self.canvas = tk.Canvas(shell, highlightthickness=0, background="#f5f7fa")
        scrollbar = ttk.Scrollbar(shell, orient="vertical", command=self.canvas.yview)
        self.canvas.configure(yscrollcommand=scrollbar.set)
        scrollbar.pack(side="right", fill="y")
        self.canvas.pack(side="left", fill="both", expand=True)
        body = ttk.Frame(self.canvas, padding=18)
        window = self.canvas.create_window(0, 0, anchor="nw", window=body)
        self.canvas.bind("<Configure>", lambda event: self.canvas.itemconfigure(window, width=event.width))
        body.bind("<Configure>", lambda event: self.canvas.configure(scrollregion=self.canvas.bbox("all")))
        ttk.Label(body, text="HWPX 보고서의 정확한 변경 범위", font=("맑은 고딕", 18, "bold")).pack(anchor="w")
        ttk.Label(body, text="추출한 최상위 구조이며 한글 쪽 렌더링이 아닙니다. 원본·원고는 수정하지 않으며 변환/OCR·모델·한글을 실행하지 않습니다.", wraplength=820).pack(anchor="w", pady=(6, 14))
        self.notebook = ttk.Notebook(body)
        self.notebook.pack(fill="x")
        basic = ttk.Frame(self.notebook, padding=12)
        advanced = ttk.Frame(self.notebook, padding=12)
        self.notebook.add(basic, text="직접 선택 · 작업 1개")
        self.notebook.add(advanced, text="고급 · 기존 JSON 가져오기")
        self._documents = {}
        for key, title in (("source", "1. 원본 양식 — 교체할 범위"), ("payload", "2. 준비된 원고 — 삽입할 범위")):
            self._document_widgets(basic, key, title)
        row = ttk.Frame(basic)
        row.pack(fill="x", pady=8)
        ttk.Label(row, text="작업 이름").pack(side="left")
        self.label = tk.StringVar(value="보고서 지정 범위")
        ttk.Entry(row, textvariable=self.label).pack(side="left", fill="x", expand=True, padx=8)
        ttk.Label(basic, text="캡션/번호 정책 (자동 선택하지 않음)").pack(anchor="w")
        self.caption = tk.StringVar(value="선택하세요")
        ttk.Combobox(basic, textvariable=self.caption, values=tuple(_CAPTIONS), state="readonly").pack(fill="x", pady=5)
        ttk.Label(basic, text="원본 [시작, 끝 제외) 앞뒤 문단을 남깁니다. 예: [2, 3)은 2번 문단만 교체합니다. 여러 구간/작업은 고급 입력을 사용하세요.", wraplength=790).pack(anchor="w")
        evidence = ttk.Frame(basic)
        evidence.pack(fill="x", pady=(12, 4))
        ttk.Button(evidence, text="선택 사항: UTF-8 근거 발췌문", command=lambda: self._choose("evidence")).pack(side="left")
        ttk.Button(evidence, text="새 발췌문 선택 취소", command=self._clear_evidence).pack(side="left", padx=6)
        self.evidence_status = tk.StringVar(value="새 발췌문 없음 (기존 피드백의 근거는 유지)")
        ttk.Label(basic, textvariable=self.evidence_status, wraplength=790).pack(anchor="w")
        self.own_achievement = tk.BooleanVar(value=False)
        ttk.Checkbutton(basic, variable=self.own_achievement, text="이 발췌문은 우리 자료이며 자체 성과 근거로 사용할 권한이 있습니다.").pack(anchor="w")
        ttk.Label(advanced, text="작업 2개·원고 구간 최대 4개·용어/그림 전용 규칙·보호 문구·기준 원고·추가 용어는 기존 전체 계약을 그대로 가져옵니다. 파일의 지시는 실행하지 않습니다.", wraplength=790).pack(anchor="w", pady=(0, 10))
        ttk.Label(advanced, text="기존 하네스와 같이 상대 경로는 앱의 현재 실행 폴더 기준입니다. 절대 경로를 권장하며, 아래에 표시된 실제 입력 위치를 확인하세요.", wraplength=790).pack(anchor="w", pady=(0, 8))
        ttk.Button(advanced, text="전체 프로필 JSON 선택", command=lambda: self._choose("profile")).pack(anchor="w", pady=4)
        self.profile_status = tk.StringVar(value="프로필 미선택")
        ttk.Label(advanced, textvariable=self.profile_status, wraplength=790).pack(anchor="w")
        ttk.Button(advanced, text="전체 피드백 JSON 선택 (직접 선택에서도 사용 가능)", command=lambda: self._choose("feedback")).pack(anchor="w", pady=(12, 4))
        self.feedback_status = tk.StringVar(value="피드백 미선택 — 직접 선택은 아래 목표/결정으로 작성")
        ttk.Label(advanced, textvariable=self.feedback_status, wraplength=790).pack(anchor="w")
        ttk.Label(body, text="3. 유지할 목표와 결정", font=("맑은 고딕", 13, "bold")).pack(anchor="w", pady=(16, 4))
        ttk.Label(body, text="목표").pack(anchor="w")
        self.goal = tk.Text(body, height=3, wrap="word", undo=True)
        self.goal.pack(fill="x", pady=4)
        ttk.Label(body, text="기존 결정 — 한 줄에 하나 (최대 32개). 수정본에서도 보존하며 명시한 변경만 새 입력으로 사용합니다.", wraplength=820).pack(anchor="w")
        self.decisions = tk.Text(body, height=5, wrap="word", undo=True)
        self.decisions.pack(fill="x", pady=4)
        ttk.Label(body, text="새 수치는 자체 성과 근거가 없으면 차단됩니다. 수치 토큰 일치는 사실/단위/달성/인증 증명이 아닙니다. 준비 통과와 정확한 승인, 별도 생성, 한글 검수, 기관 제출 승인은 서로 다릅니다.", wraplength=820).pack(anchor="w", pady=(8, 6))

    def _document_widgets(self, parent, key, title):
        frame = ttk.LabelFrame(parent, text=title, padding=8)
        frame.pack(fill="x", pady=(0, 10))
        path = tk.StringVar(value="파일 미선택")
        ttk.Button(frame, text="HWPX 선택…", command=lambda: self._choose(key)).pack(anchor="w")
        ttk.Label(frame, textvariable=path, wraplength=770).pack(anchor="w", pady=4)
        holder = ttk.Frame(frame)
        holder.pack(fill="x")
        tree = ttk.Treeview(holder, columns=("index", "text", "objects"), show="headings", height=6, selectmode="extended")
        for col, title, width in (("index", "0기준 번호", 85), ("text", "추출 문구 (실제 최상위 문단)", 535), ("objects", "개체", 120)):
            tree.heading(col, text=title)
            tree.column(col, width=width, stretch=col == "text")
        scroll = ttk.Scrollbar(holder, orient="vertical", command=tree.yview)
        tree.configure(yscrollcommand=scroll.set)
        tree.pack(side="left", fill="x", expand=True)
        scroll.pack(side="right", fill="y")
        tree.bind("<Return>", lambda event: self._use_selection(key))
        tree.bind("<Double-1>", lambda event: self._show_paragraph(key))
        bounds = ttk.Frame(frame)
        bounds.pack(fill="x", pady=(6, 0))
        start, end = tk.StringVar(), tk.StringVar()
        ttk.Label(bounds, text="[시작").pack(side="left")
        ttk.Entry(bounds, textvariable=start, width=7).pack(side="left", padx=4)
        ttk.Label(bounds, text=", 끝 제외").pack(side="left")
        ttk.Entry(bounds, textvariable=end, width=7).pack(side="left", padx=4)
        ttk.Label(bounds, text=")").pack(side="left")
        ttk.Button(bounds, text="선택 문단을 범위로 (Enter)", command=lambda: self._use_selection(key)).pack(side="left", padx=8)
        ttk.Button(bounds, text="문단 전체 텍스트", command=lambda: self._show_paragraph(key)).pack(side="left")
        self._documents[key] = (path, tree, start, end)

    def _seed(self, seed):
        profile, feedback = deepcopy(seed)
        self._set_feedback(feedback)
        self._set_profile(profile)
        jobs = profile.get("jobs", [])
        basic_keys = {"label", "source", "source_sha256", "payload", "payload_sha256", "target_range", "payload_ranges", "caption_policy"}
        if (
            set(profile) != {"contract", "jobs"} or len(jobs) != 1
            or not set(jobs[0]) <= basic_keys
            or len(jobs[0].get("payload_ranges", [])) != 1
            or feedback.get("mode") != "authoring"
        ):
            self.notebook.select(1)
            return
        job = jobs[0]
        self.label.set(job["label"])
        policy = job.get("caption_policy", "legacy")
        self.caption.set(next((name for name, value in _CAPTIONS.items() if value == policy), "선택하세요"))
        for key, ranges in (("source", job["target_range"]), ("payload", job["payload_ranges"][0])):
            self._documents[key][2].set(str(ranges[0]))
            self._documents[key][3].set(str(ranges[1]))
            self._expected[key] = job[key + "_sha256"]
            self._load(key, Path(job[key]))

    def _set_feedback(self, data):
        self._feedback = deepcopy(data)
        self.goal.delete("1.0", "end")
        self.goal.insert("1.0", data["goal"])
        self.decisions.delete("1.0", "end")
        self.decisions.insert("1.0", "\n".join(data["decisions"]))
        self.feedback_status.set("유지할 피드백: " + data["mode"] + " / 근거 " + str(len(data.get("sources", []))) + "개 / 추가 용어 " + str(len(data.get("terms", []))) + "개")

    def _set_profile(self, data):
        self._advanced_profile = deepcopy(data)
        lines = []
        for job in data["jobs"]:
            lines.append(str(job["label"]) + " | 원본 " + str(job["target_range"]) + " ← 원고 " + str(job["payload_ranges"]) + "\n" + str(job["source"]) + "\n" + str(job["payload"]))
        self.profile_status.set("\n".join(lines))

    def _choose(self, key):
        if key in ("source", "payload"):
            types = [("HWPX 문서", "*.hwpx")]
        elif key == "evidence":
            types = [("UTF-8 발췌문", "*.txt *.md")]
        else:
            types = [("JSON 입력", "*.json")]
        path = filedialog.askopenfilename(parent=self.top, title="보고서 입력 선택", filetypes=types)
        if path:
            self._expected.pop(key, None)
            if key in self._documents:
                self._documents[key][2].set("")
                self._documents[key][3].set("")
            self._load(key, Path(path))

    def _load(self, key, path):
        token = self._mailbox.begin(key)
        self._invalid.pop(key, None)
        self._pending.add(key)
        self.prepare_button.state(["disabled"])
        if key in self._documents:
            self._previews.pop(key, None)
            self._render_tokens[key] = token
            self._documents[key][0].set(str(path) + " — 구조를 읽는 중…")
            self._documents[key][1].delete(*self._documents[key][1].get_children())
        if key == "evidence":
            self._evidence = None
            self.own_achievement.set(False)
            self.evidence_status.set(str(path) + " — 읽는 중…")
        if key == "profile":
            self._advanced_profile = None
            self.profile_status.set("프로필을 읽는 중…")
            self.notebook.select(1)
        if key == "feedback":
            self._feedback = None
            self.feedback_status.set("피드백을 읽는 중…")
        def worker():
            try:
                if key in ("source", "payload"):
                    value = capture_document(path, token)
                elif key == "evidence":
                    value = capture_evidence(path)
                else:
                    value = _import_json(path, key)
                self._mailbox.publish(key, token, value=value)
            except Exception as exc:
                self._mailbox.publish(key, token, error=str(exc))
        threading.Thread(target=worker, daemon=True, name="report-input-" + key).start()

    def _poll(self):
        for key, value, error in self._mailbox.take():
            self._pending.discard(key)
            if error is not None:
                self._invalid[key] = error
                self.status.set("입력 차단: " + error)
                if key in self._documents:
                    self._documents[key][0].set("입력 차단: " + error)
                continue
            if key in ("source", "payload"):
                expected = self._expected.get(key)
                if expected is not None and value.sha256 != expected:
                    self._invalid[key] = "원래 입력 해시가 바뀌었습니다."
                    self._documents[key][0].set("원래 입력 해시가 바뀌었습니다. 새 파일 선택으로 명시적으로 새 입력을 지정하세요.")
                    self.status.set("이전 입력은 새 바이트로 자동 결합하지 않습니다.")
                    continue
                self._previews[key] = value
                self._documents[key][0].set(str(value.path) + " — 최상위 항목 " + str(len(value.paragraphs)) + "개 / 실제 문단 " + str(sum(row.is_paragraph for row in value.paragraphs)) + "개")
                self._render(key, value, self._render_tokens[key], 0)
            elif key == "evidence":
                self._evidence = value
                self.evidence_status.set(value["path"] + " — UTF-8 발췌문 (자체 성과 사용 확인 필요)")
            elif key == "profile":
                self._set_profile(value)
                self.notebook.select(1)
            elif key == "feedback":
                self._set_feedback(value)
                if value["mode"] != "authoring":
                    self.notebook.select(1)
        if not self._pending:
            self.prepare_button.state(["!disabled"])
        self._later(60, self._poll)

    def _render(self, key, preview, token, offset):
        if token.is_set() or self._render_tokens.get(key) is not token:
            return
        tree = self._documents[key][1]
        for row in preview.paragraphs[offset:offset + 120]:
            text = row.text or "(추출 텍스트 없음 — 빈 내용이라는 판정 아님)"
            if not row.is_paragraph:
                text = "[선택 불가: 문단 아님] " + text
            tree.insert("", "end", iid=str(row.index), values=(row.index, text[:240], "표 " + str(row.tables) + " · 그림 " + str(row.images)))
        if offset + 120 < len(preview.paragraphs):
            self._later(1, lambda: self._render(key, preview, token, offset + 120))

    def _use_selection(self, key):
        try:
            preview = self._previews[key]
            bounds = selected_range(preview, [int(i) for i in self._documents[key][1].selection()], source=key == "source")
            self._documents[key][2].set(str(bounds[0]))
            self._documents[key][3].set(str(bounds[1]))
            if key == "source":
                self.status.set("앞 경계: " + preview.paragraphs[bounds[0] - 1].text[:100] + " / 뒤 경계: " + preview.paragraphs[bounds[1]].text[:100])
        except (KeyError, ValueError) as exc:
            self.status.set(str(exc) if isinstance(exc, ValueError) else "먼저 문서 구조를 읽어주세요.")
        return "break"

    def _show_paragraph(self, key):
        chosen = self._documents[key][1].selection()
        preview = self._previews.get(key)
        if not chosen or preview is None:
            self.status.set("전체 텍스트를 볼 문단을 선택하세요.")
            return
        row = preview.paragraphs[int(chosen[0])]
        # Owned child; closing the wizard destroys it as well.
        window = tk.Toplevel(self.top)
        window.title("추출 문단 " + str(row.index) + " — 한글 쪽 렌더링 아님")
        window.geometry("760x480")
        text = tk.Text(window, wrap="word", padx=12, pady=12)
        scroll = ttk.Scrollbar(window, orient="vertical", command=text.yview)
        text.configure(yscrollcommand=scroll.set)
        scroll.pack(side="right", fill="y")
        text.pack(fill="both", expand=True)
        content = row.text or "(추출 텍스트 없음)"
        text.configure(state="disabled")
        def append(offset=0):
            if not window.winfo_exists():
                return
            text.configure(state="normal")
            text.insert("end", content[offset:offset + 8192])
            text.configure(state="disabled")
            if offset + 8192 < len(content):
                self._later(1, lambda: append(offset + 8192))
        append()
        window.bind("<Escape>", lambda event: window.destroy())

    def _clear_evidence(self):
        self._mailbox.begin("evidence")
        self._pending.discard("evidence")
        self._evidence = None
        self._invalid.pop("evidence", None)
        self.own_achievement.set(False)
        self.evidence_status.set("새 발췌문 없음 (기존 피드백의 근거는 유지)")

    def _prepare(self):
        if self._pending or self._closed:
            return "break"
        try:
            relevant = {"feedback", "profile"} if self.notebook.index("current") == 1 else {"source", "payload", "feedback", "evidence"}
            invalid = [value for key, value in self._invalid.items() if key in relevant]
            if invalid:
                raise ValueError("선택한 입력이 차단되었습니다. 유효한 입력을 다시 선택하세요: " + invalid[0])
            goal = self.goal.get("1.0", "end-1c")
            decision_text = self.decisions.get("1.0", "end-1c")
            if self._feedback is not None and decision_text == "\n".join(self._feedback["decisions"]):
                decisions = deepcopy(self._feedback["decisions"])
            else:
                decisions = [line.strip() for line in decision_text.splitlines() if line.strip()]
            if self.notebook.index("current") == 1:
                if self._advanced_profile is None or self._feedback is None:
                    raise ValueError("고급 입력에는 전체 프로필과 전체 피드백이 모두 필요합니다.")
                profile, feedback = deepcopy(self._advanced_profile), deepcopy(self._feedback)
                feedback.update(goal=goal, decisions=decisions)
                harness.feedback_data(project._json_bytes(feedback, 65536))
            else:
                if any(key not in self._previews for key in ("source", "payload")):
                    raise ValueError("원본과 원고의 실제 구조를 모두 읽어주세요.")
                bounds = [[int(self._documents[key][i].get()) for i in (2, 3)] for key in ("source", "payload")]
                profile, feedback = basic_inputs(
                    self._previews["source"], self._previews["payload"], *bounds,
                    label=self.label.get(), caption_policy=_CAPTIONS[self.caption.get()],
                    goal=goal, decisions=decisions, feedback=self._feedback,
                    evidence=self._evidence, own_achievement=self.own_achievement.get(),
                )
                if self._advanced_profile is not None and len(self._advanced_profile.get("jobs", [])) == 1:
                    old = self._advanced_profile["jobs"][0]
                    new = profile["jobs"][0]
                    if set(old) == set(new) and all(
                        Path(old[key]) == Path(value) if key in ("source", "payload") else old[key] == value
                        for key, value in new.items()
                    ):
                        profile = deepcopy(self._advanced_profile)
            self.on_prepare(profile, feedback)
        except (ValueError, KeyError, TypeError) as exc:
            messagebox.showerror("보고서 입력 확인", str(exc), parent=self.top)
            return "break"
        self.close()
        return "break"

    def _reveal_focus(self, event):
        widget = event.widget
        if self._closed or widget.winfo_toplevel() is not self.top:
            return
        ancestor = widget
        while ancestor is not self.canvas:
            ancestor = getattr(ancestor, "master", None)
            if ancestor is None:
                return  # Fixed footer/other owned dialogs do not scroll the body.

        def reveal():
            if not widget.winfo_exists():
                return
            top = widget.winfo_rooty() - self.canvas.winfo_rooty()
            bottom = top + widget.winfo_height()
            height = self.canvas.winfo_height()
            bounds = self.canvas.bbox("all")
            if bounds and bounds[3] > height:
                delta = top - 8 if top < 0 else bottom - height + 8 if bottom > height else 0
                if delta:
                    self.canvas.yview_moveto(
                        max(0, (self.canvas.canvasy(0) + delta) / bounds[3])
                    )

        self._later(0, reveal)

    def _wheel(self, event):
        # Lists/text retain their own scrolling; no bind_all or parent hijacking.
        if isinstance(event.widget, (tk.Text, ttk.Treeview, ttk.Combobox)):
            return None
        self.canvas.yview_scroll(-int(event.delta / 120), "units")
        return "break"

    def close(self):
        if self._closed:
            return
        self._closed = True
        self._mailbox.close()
        for handle in self._after:
            self.top.after_cancel(handle)
        self._after.clear()
        self.top.destroy()
