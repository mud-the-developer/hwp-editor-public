"""Immutable document-first projects, consuming only the existing template writer.

The existing UI alone persists candidate permission and exact-file acceptance.
Reopen is read-only; historical prepared-project authority is never migrated.
"""

from dataclasses import dataclass
from copy import deepcopy
from datetime import datetime, timezone
import hashlib
import json
import ntpath
import os
from pathlib import Path
import re
import stat
import uuid
import hwp_template_draft as draft

import hwp_report_harness as harness
import hwp_review as review

PROJECT_FILE = "report-project.json"
_CONTRACT = "hwp-report-prepared-project-v2"
_BINDING_FILE = "report-binding.json"
_FAILURE_FILE = "prepare-failed.json"
_PROFILE_FILE = "profile-input.json"
_FEEDBACK_FILE = "feedback-input.json"
_META_LIMIT = 8192
_INPUT_LIMIT = 262144
_GENERATED_JSON_LIMIT = 64 * 1024 * 1024
_HASH = re.compile(r"[a-f0-9]{64}\Z")
_SIBLING_LIMIT = 200


@dataclass(frozen=True)
class ReportProjectInfo:
    root: Path
    review: Path
    binding: str | None
    name: str
    jobs: int
    tables: int | None
    images: int | None
    phase: str
    gate_passed: bool
    locked: bool
    issues: tuple
    outputs: tuple
    versions: tuple = ()
    accepted: tuple = ()
    requests: tuple = ()
    document_first: bool = False
    source_sha256: str | None = None

    @property
    def kind(self):
        return "report"


class ReportProjectError(RuntimeError):
    """A hard filesystem failure still identifies its exclusively owned folder."""

    def __init__(self, message, project_root):
        super().__init__(message)
        self.project_root = project_root


def _digest(raw):
    return hashlib.sha256(raw).hexdigest()


def _json_bytes(data, limit):
    raw = json.dumps(
        data, ensure_ascii=False, sort_keys=True, indent=2, allow_nan=False
    ).encode("utf-8")
    if len(raw) > limit:
        raise ValueError("보고서 입력/메타데이터가 허용 크기를 넘었습니다.")
    return raw


def _present(path):
    return os.path.lexists(path)


def _unlinked(path, *, allow_parent=False):
    """Check lexical ancestors before resolving; includes Windows junctions."""
    path = Path(path)
    if not path.is_absolute() or (".." in path.parts and not allow_parent):
        raise ValueError("실제 절대 경로만 사용할 수 있습니다.")
    for item in (*reversed(path.parents), path):
        try:
            info = item.lstat()
        except FileNotFoundError:
            continue
        if stat.S_ISLNK(info.st_mode) or getattr(info, "st_file_attributes", 0) & 0x400:
            raise ValueError("보고서 경로에는 링크/정션을 사용할 수 없습니다.")
    return path


def _capture(path, limit):
    path = _unlinked(path)
    before = path.lstat()
    if not stat.S_ISREG(before.st_mode) or before.st_nlink != 1:
        raise ValueError("보고서 입력/메타데이터는 링크가 아닌 실제 파일이어야 합니다.")
    if before.st_size > limit:
        raise ValueError("보고서 입력/메타데이터가 허용 크기를 넘었습니다.")
    with path.open("rb") as stream:
        opened = os.fstat(stream.fileno())
        if (opened.st_dev, opened.st_ino) != (before.st_dev, before.st_ino):
            raise ValueError("읽는 동안 보고서 파일이 바뀌었습니다.")
        raw = stream.read(limit + 1)
        after = os.fstat(stream.fileno())
    _unlinked(path)
    current = path.lstat()
    if len(raw) > limit or (
        after.st_size != len(raw)
        or after.st_mtime_ns != before.st_mtime_ns
        or (current.st_dev, current.st_ino) != (before.st_dev, before.st_ino)
        or current.st_mtime_ns != after.st_mtime_ns
        or current.st_nlink != 1
    ):
        raise ValueError("읽는 동안 보고서 파일이 바뀌었거나 크기를 넘었습니다.")
    return raw


def _read_json(path, limit):
    raw = _capture(path, limit)
    data = review.parse_json(raw)
    if not isinstance(data, dict):
        raise ValueError("보고서 JSON은 객체여야 합니다.")
    return data, raw


def _write_new(path, raw):
    _unlinked(path)
    with path.open("xb") as stream:
        stream.write(raw)
        stream.flush()
        os.fsync(stream.fileno())


def has_project_marker(path):
    """Dispatch even malformed, dangling-link or nonregular report markers."""
    return _present(Path(path) / PROJECT_FILE)


def _metadata(root):
    root = _unlinked(Path(root))
    if not root.is_dir():
        raise ValueError("보고서 작업 폴더가 없습니다.")
    data, raw = _read_json(root / PROJECT_FILE, _META_LIMIT)
    required = {
        "contract", "state", "created_at", "name", "profile_sha256",
        "feedback_sha256", "previous",
    }
    if (
        set(data) != required
        or data["contract"] != _CONTRACT
        or data["state"] != "preparing"
        or not isinstance(data["name"], str)
        or not 1 <= len(data["name"]) <= 120
        or not isinstance(data["created_at"], str)
        or len(data["created_at"]) > 64
        or any(not isinstance(data[key], str) or not _HASH.fullmatch(data[key])
               for key in ("profile_sha256", "feedback_sha256"))
        or (data["previous"] is not None and (
            not isinstance(data["previous"], str) or len(data["previous"]) > 2000
            or not Path(data["previous"]).is_absolute()
        ))
    ):
        raise ValueError("보고서 작업 메타데이터가 올바르지 않습니다.")
    return root, data, raw


def read_inputs(root):
    """Return original user input, never expanded run/feedback.json defaults."""
    root, marker, _ = _metadata(root)
    values = []
    for name, key, limit in (
        (_PROFILE_FILE, "profile_sha256", _INPUT_LIMIT),
        (_FEEDBACK_FILE, "feedback_sha256", 65536),
    ):
        raw = _capture(root / name, limit)
        if _digest(raw) != marker[key]:
            raise ValueError("원래 보고서 입력 스냅샷이 변경되었습니다.")
        data = review.parse_json(raw)
        if not isinstance(data, dict):
            raise ValueError("보고서 입력 JSON은 객체여야 합니다.")
        values.append(data)
    return tuple(values)


def _input_paths(profile, feedback):
    jobs = profile.get("jobs", [])
    if not isinstance(jobs, list):
        raise ValueError("명시적 보고서 작업 목록이 필요합니다.")
    for job in jobs:
        for which in ("source", "payload"):
            yield job[which], job[which + "_sha256"]
    for key in ("sources", "baselines"):
        for record in feedback.get(key, []):
            yield record["path"], record["sha256"]


def _check_input_paths(profile, feedback):
    for name, expected in _input_paths(profile, feedback):
        if not isinstance(name, str) or not isinstance(expected, str) or not _HASH.fullmatch(expected):
            raise ValueError("입력 파일의 경로와 정확한 SHA256이 필요합니다.")
        path = _unlinked(Path(name))
        info = path.lstat()
        if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
            raise ValueError("입력 파일은 링크가 아닌 실제 파일이어야 합니다.")
        # Harness owns byte authentication and bounded package parsing.


def _binding(root, marker, marker_raw):
    record, _ = _read_json(root / _BINDING_FILE, _META_LIMIT)
    if (
        set(record) != {"contract", "binding_sha256", "project_sha256", "profile_sha256", "feedback_sha256"}
        or record["contract"] != _CONTRACT
        or record["project_sha256"] != _digest(marker_raw)
        or any(record[key] != marker[key] for key in ("profile_sha256", "feedback_sha256"))
        or not isinstance(record["binding_sha256"], str)
        or not _HASH.fullmatch(record["binding_sha256"])
    ):
        raise ValueError("외부에 보관한 보고서 결합 정보가 변경되었습니다.")
    return record["binding_sha256"]


def _counts(root, observation):
    # Counts describe selected incoming objects, never rendered pages. Authenticate
    # this optional summary against the plan digest already checked by inspect.
    try:
        plan, _ = _read_json(root / "run/review/template-plan.json", _GENERATED_JSON_LIMIT)
        if review.plan_hash(plan) != observation["plan_sha256"]:
            return None, None
        counts = []
        for key in ("inserted_tables", "inserted_pictures"):
            values = [job["preview"][key] for job in plan["jobs"]]
            if any(type(value) is not int or value < 0 for value in values):
                return None, None
            counts.append(sum(values))
        return tuple(counts)
    except (OSError, ValueError, KeyError, TypeError):
        return None, None


def _check_run_tree(root):
    """Reject unsafe run metadata before the harness opens any of its files."""
    pending = [root / "run"]
    seen = 0
    while pending:
        directory = _unlinked(pending.pop())
        if not _present(directory):
            continue
        if not directory.is_dir():
            raise ValueError("보고서 실행 경로가 실제 폴더가 아닙니다.")
        for child in directory.iterdir():
            seen += 1
            if seen > 10000 or len(child.relative_to(root).parts) > 8:
                raise ValueError("보고서 실행 자료가 허용 범위를 넘었습니다.")
            _unlinked(child)
            info = child.lstat()
            if stat.S_ISDIR(info.st_mode):
                pending.append(child)
            elif not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
                raise ValueError("보고서 실행 자료에는 링크/특수 파일을 사용할 수 없습니다.")
            elif child.suffix.lower() == ".json":
                # Full plans/approvals/results contain selected report text, not
                # just intake metadata. The harness authenticates/parses those
                # files; preflight only bounds size and rejects linked objects.
                maximum = (
                    _GENERATED_JSON_LIMIT
                    if child.is_relative_to(root / "run/review")
                    else _INPUT_LIMIT
                )
                if info.st_size > maximum:
                    raise ValueError("보고서 실행 JSON이 허용 크기를 넘었습니다.")


def inspect(root):
    """Fully revalidate a project without repairing, retrying or rebinding it."""
    root = Path(root).absolute()
    try:
        marker, _ = _read_json(root / PROJECT_FILE, _META_LIMIT)
        if marker.get("contract") == DOCUMENT_PROJECT:
            return _inspect_document(root)
    except (OSError, ValueError):
        pass
    name, jobs, binding, locked = root.name, 0, None, False
    try:
        root, marker, marker_raw = _metadata(root)
        name = marker["name"]
        profile, feedback = read_inputs(root)
        jobs = len(profile["jobs"]) if isinstance(profile.get("jobs"), list) else 0
        locked = _present(root / "run/review" / review.APPLY_LOCK)
        if _present(root / _FAILURE_FILE):
            failure, _ = _read_json(root / _FAILURE_FILE, _META_LIMIT)
            if set(failure) != {"status", "error", "retry"} or failure["status"] != "failed_preserved" or failure["retry"] is not False or not isinstance(failure["error"], str):
                raise ValueError("준비 실패 기록이 올바르지 않습니다.")
            return ReportProjectInfo(root, root / "run/review", None, name, jobs, None, None,
                                     "failed_preserved", False, locked, (failure["error"],), ())
        if not _present(root / _BINDING_FILE):
            return ReportProjectInfo(root, root / "run/review", None, name, jobs, None, None,
                                     "preparing", False, locked,
                                     ("준비가 완료되지 않았습니다. 파일을 보존하며 자동 재시도하지 않습니다.",), ())
        binding = _binding(root, marker, marker_raw)
        _check_input_paths(profile, feedback)
        _check_run_tree(root)
        observed = harness.inspect(root / "run", binding)
        phase = observed["phase"]
        passed = observed["gate_passed"] is True and phase in (
            "awaiting_draft_permission", "awaiting_user_create", "generated_needs_hancom_review"
        )
        tables, images = _counts(root, observed)
        issues = tuple(str(issue) for issue in observed["issues"])
        if not passed and not issues:
            issues = ("실행 기록 또는 미완료 상태를 보존합니다. 다시 생성하지 않습니다.",)
        return ReportProjectInfo(root, root / "run/review", binding, name, jobs, tables, images,
                                 phase, passed, locked, issues,
                                 tuple(observed["outputs"]) if passed and phase == "generated_needs_hancom_review" else ())
    except Exception as exc:
        return ReportProjectInfo(root, root / "run/review", binding, name, jobs, None, None,
                                 "blocked", False, locked, (str(exc),), ())


def _matching(root, profile_raw, feedback_raw):
    try:
        _, marker, _ = _metadata(root)
        if marker["profile_sha256"] != _digest(profile_raw) or marker["feedback_sha256"] != _digest(feedback_raw):
            return None
        profile, feedback = read_inputs(root)
        if _json_bytes(profile, _INPUT_LIMIT) != profile_raw or _json_bytes(feedback, 65536) != feedback_raw:
            return None
        info = inspect(root)
        return info if info.gate_passed and (not info.locked or info.outputs) else None
    except (OSError, ValueError, KeyError, TypeError):
        return None


def create(profile, feedback, *, base=None, previous=None):
    """Prepare a NEW owned project, or deliver a live-valid exact existing one."""
    if not isinstance(profile, dict) or not isinstance(feedback, dict):
        raise ValueError("보고서 프로필과 피드백 객체가 필요합니다.")
    # A caller may omit unchanged decisions during explicit revision. Explicit
    # edits remain new inputs; the previous immutable snapshots are never edited.
    feedback = dict(feedback)
    if previous is not None:
        previous = _unlinked(Path(previous).absolute())
        _, old_feedback = read_inputs(previous)
        for key in ("goal", "decisions"):
            if key not in feedback:
                feedback[key] = old_feedback[key]
    profile_raw = _json_bytes(profile, _INPUT_LIMIT)
    feedback_raw = _json_bytes(feedback, 65536)
    # Detach caller-owned objects before invoking any preparation code.
    profile, feedback = review.parse_json(profile_raw), review.parse_json(feedback_raw)
    if previous is not None:
        reused = _matching(previous, profile_raw, feedback_raw)
        if reused is not None:
            return reused
    if base is None:
        location = os.environ.get("LOCALAPPDATA")
        if not location:
            raise RuntimeError("사용자 저장 위치가 없습니다. 실제 절대 작업 폴더를 지정하세요.")
        base = Path(location) / "HwpReview/Projects"
    base = _unlinked(Path(base))
    base.mkdir(parents=True, exist_ok=True)
    _unlinked(base)
    if not base.is_dir():
        raise ValueError("보고서 저장 위치는 실제 폴더여야 합니다.")
    for index, candidate in enumerate(base.iterdir()):
        if index >= _SIBLING_LIMIT:
            break
        if has_project_marker(candidate):
            reused = _matching(candidate, profile_raw, feedback_raw)
            if reused is not None:
                return reused
    label = str(profile.get("jobs", [{}])[0].get("label", "보고서")) if isinstance(profile.get("jobs"), list) and profile["jobs"] and isinstance(profile["jobs"][0], dict) else "보고서"
    name = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "_", label).strip(" .")[:60] or "보고서"
    root = base / (name + "-" + uuid.uuid4().hex)
    root.mkdir(exist_ok=False)
    marker = {
        "contract": _CONTRACT, "state": "preparing",
        "created_at": datetime.now(timezone.utc).isoformat(), "name": name,
        "profile_sha256": _digest(profile_raw), "feedback_sha256": _digest(feedback_raw),
        "previous": str(previous) if previous is not None else None,
    }
    try:
        marker_raw = _json_bytes(marker, _META_LIMIT)
        _write_new(root / PROJECT_FILE, marker_raw)
    except Exception as exc:
        raise ReportProjectError(str(exc), root) from exc
    try:
        _write_new(root / _PROFILE_FILE, profile_raw)
        _write_new(root / _FEEDBACK_FILE, feedback_raw)
        _check_input_paths(profile, feedback)
        prepared = harness.prepare(root / _PROFILE_FILE, root / _FEEDBACK_FILE, root / "run")
        binding = prepared["binding_sha256"]
        if not isinstance(binding, str) or not _HASH.fullmatch(binding):
            raise ValueError("준비 단계에서 유효한 외부 결합 정보를 받지 못했습니다.")
        _write_new(root / _BINDING_FILE, _json_bytes({
            "contract": _CONTRACT, "binding_sha256": binding,
            "project_sha256": _digest(marker_raw),
            "profile_sha256": marker["profile_sha256"],
            "feedback_sha256": marker["feedback_sha256"],
        }, _META_LIMIT))
    except Exception as exc:
        # Never erase a partially prepared run or recover authority from its files.
        try:
            _write_new(root / _FAILURE_FILE, _json_bytes({
                "status": "failed_preserved", "error": str(exc)[:1000], "retry": False,
            }, _META_LIMIT))
        except Exception as record_error:
            raise ReportProjectError(
                str(exc) + " / 실패 기록 저장도 중단되었습니다: " + str(record_error), root
            ) from exc
    return inspect(root)


# A document project has its own versioned authority. Advanced donor projects
# above remain inspectable; their approval cannot authorize these operations.
DOCUMENT_PROJECT = "hwp-report-project-v2"
PERMISSION = "hwp-report-draft-permission-v1"
ACCEPTANCE = "hwp-report-artifact-acceptance-v1"
_DOCUMENT_LIMIT = 64 * 1024 * 1024
_REQUEST_LIMIT = 1024 * 1024
_TASK_ID = re.compile(r"r-[a-f0-9]{32}\Z")
_VERSION_ID = re.compile(r"v-[a-f0-9]{32}\Z")


def _canonical(data):
    return json.dumps(data, ensure_ascii=False, sort_keys=True,
                      separators=(",", ":"), allow_nan=False).encode("utf-8")


def _document(root):
    root = _unlinked(Path(root).absolute())
    marker, raw = _read_json(root / PROJECT_FILE, _META_LIMIT)
    if set(marker) != {"contract", "name", "created_at", "source", "source_sha256",
                       "runtime_identity"} or marker["contract"] != DOCUMENT_PROJECT:
        raise ValueError("이전 작업은 보존됩니다. 새 문서 작업 권한으로 사용할 수 없습니다.")
    if marker["runtime_identity"] != harness.runtime.identity():
        raise ValueError("실행 환경/장치/사용자 또는 소스 규칙이 변경되었습니다.")
    original = _capture(Path(marker["source"]), _DOCUMENT_LIMIT)
    snapshot = _capture(root / "source.hwpx", _DOCUMENT_LIMIT)
    if _digest(original) != marker["source_sha256"] or original != snapshot:
        raise ValueError("원본 또는 보관한 원본 바이트가 변경되었습니다.")
    draft.parts_from(snapshot)
    return root, marker, raw, snapshot


def source(root):
    """Return only validated locked-original bytes; no authority or file writes."""
    _, marker, _, blob = _document(root)
    return {"sha256": marker["source_sha256"], "data": blob}


def new_document(source, *, base=None, name=None):
    """Capture a fresh original; no donor, approval or generated output exists."""
    source = _unlinked(Path(source).absolute())
    raw = _capture(source, _DOCUMENT_LIMIT)
    draft.parts_from(raw)
    if source.suffix.lower() != ".hwpx":
        raise ValueError("원본 HWPX 파일을 선택하세요.")
    if base is None:
        location = os.environ.get("LOCALAPPDATA")
        if not location:
            raise ValueError("실제 사용자 작업 저장 위치가 필요합니다.")
        base = Path(location) / "HwpReview/Projects"
    base = _unlinked(Path(base).absolute())
    base.mkdir(parents=True, exist_ok=True)
    _unlinked(base)
    label = source.stem if name is None else harness.text(name, 120)
    label = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "_", label).strip(" .")[:60] or "보고서"
    root = base / (label + "-" + uuid.uuid4().hex)
    root.mkdir(exist_ok=False)
    try:
        marker = {
            "contract": DOCUMENT_PROJECT, "name": label,
            "created_at": datetime.now(timezone.utc).isoformat(),
            "source": str(source), "source_sha256": _digest(raw),
            "runtime_identity": harness.runtime.identity(),
        }
        _write_new(root / PROJECT_FILE, _json_bytes(marker, _META_LIMIT))
        _write_new(root / "source.hwpx", raw)
        (root / "tasks").mkdir()
        (root / "versions").mkdir()
    except Exception as exc:
        raise ReportProjectError(str(exc), root) from exc
    return inspect(root)


def new_blank_document(destination, *, base=None):
    """Exclusively create a real unsigned blank, then use normal locked intake."""
    from hwp_blank_document import blank_document

    destination = _unlinked(Path(destination).absolute())
    if destination.suffix.lower() != ".hwpx" or ntpath.isreserved(str(destination)):
        raise ValueError("새 빈 문서는 예약 이름이나 대체 데이터 스트림이 아닌 .hwpx 파일로 저장하세요.")
    if not destination.parent.is_dir():
        raise ValueError("새 문서를 저장할 실제 폴더를 먼저 선택하세요.")
    # Existing files and failed new outputs are never replaced or cleaned up.
    _write_new(destination, blank_document())
    return new_document(destination, base=base)


def _body(raw):
    return review.xml_root(draft.parts_from(raw)[draft.PART])


def _objects(node):
    allowed = {draft.HP + key for key in ("p", "run", "t", "linesegarray", "lineseg")}
    return sorted({item.tag.rsplit("}", 1)[-1] for item in node.iter()
                   if item.tag not in allowed})


def _scope_id(marker, index):
    return "s-" + _digest(_canonical([marker["source_sha256"], index]))[:24]


def sections(root):
    """Opaque bounded textual choices; raw XML indexes stay internal."""
    _, marker, _, raw = _document(root)
    nodes = list(_body(raw))
    if len(nodes) > 10000:
        raise ValueError("문서 구조가 선택 한도를 넘었습니다.")
    result = []
    for index, node in enumerate(nodes):
        values = draft.texts([node])
        display_text = " / ".join(values)
        objects = _objects(node)
        result.append({
            "id": _scope_id(marker, index),
            "label": display_text[:160] or "텍스트 없는 문단", "text": display_text,
            "paragraphs": values, "has_objects": bool(objects), "objects": objects,
            "selectable": 0 < index < len(nodes) - 1 and node.tag == draft.HP + "p",
            "boundary": index in (0, len(nodes) - 1),
        })
    return result


def _task_directory(root, request_id):
    if not isinstance(request_id, str) or not _TASK_ID.fullmatch(request_id):
        raise ValueError("초안 요청 식별자가 올바르지 않습니다.")
    return _unlinked(root / "tasks" / request_id)


def _task_records(root):
    directory = _unlinked(root / "tasks")
    result = []
    for entry in directory.iterdir():
        _unlinked(entry)
        if len(result) >= 200:
            raise ValueError("보존된 초안 작업 수가 한도를 넘었습니다.")
        if not entry.is_dir() or not _TASK_ID.fullmatch(entry.name):
            raise ValueError("보존된 작업 폴더가 올바르지 않습니다.")
        grant, raw = _read_json(entry / "draft-permission.json", _REQUEST_LIMIT)
        result.append((entry, grant, raw))
    return sorted(result, key=lambda item: (item[1].get("created_at", ""), item[0].name))


def _source_for(root, marker, original, previous_version):
    if previous_version is None:
        return original, None
    item = candidate(root, previous_version)
    return _capture(Path(item["path"]), _DOCUMENT_LIMIT), item


def memo_target(root, previous_version, sha256, section_index, paragraph_index):
    """Resolve a read-only rendered target against exact immutable artifact bytes."""
    if (
        not isinstance(sha256, str) or not _HASH.fullmatch(sha256)
        or type(section_index) is not int or not 0 <= section_index < 10000
        or type(paragraph_index) is not int or not 0 <= paragraph_index < 100000
    ):
        raise ValueError("현재 문서의 정확한 메모 위치를 확인하세요.")
    root, marker, _, original = _document(root)
    source, previous = _source_for(root, marker, original, previous_version)
    if _digest(source) != sha256:
        raise ValueError("메모의 기준 문서가 바뀌었습니다. 현재 쪽에서 다시 선택하세요.")
    result = {
        "sha256": sha256, "previous_version": previous_version,
        "section_index": section_index, "paragraph_index": paragraph_index,
        "quote": "", "quote_truncated": False, "selection_ids": [],
        "editable": False, "reason": "이 구역은 현재 본문 자동 작성 범위가 아닙니다.",
    }
    # Existing authoring supports the first body section only. Other renderer
    # targets remain advisory notes; they never acquire scope or write authority.
    if section_index != 0:
        return result
    nodes = list(_body(source))
    if any(node.tag != draft.HP + "p" for node in nodes):
        raise ValueError("문단 위치를 정확히 연결할 수 없는 본문 구조입니다.")
    if paragraph_index >= len(nodes):
        raise ValueError("메모 위치가 현재 문서의 문단을 벗어났습니다.")
    value = "\n".join(draft.texts([nodes[paragraph_index]]))
    result["quote"], result["quote_truncated"] = value[:2000], len(value) > 2000
    if previous is None:
        if paragraph_index in (0, len(nodes) - 1):
            result["reason"] = "원본의 앞뒤 보존 경계입니다. 자동 수정 권한은 없습니다."
            return result
        ids = [_scope_id(marker, paragraph_index)]
    else:
        start, end = previous["target_range"]
        if not start <= paragraph_index < end:
            result["reason"] = "이 수정본에서 허용된 본문 범위 밖입니다."
            return result
        # A revised paragraph may split or merge. Preserve its authenticated
        # original scope, never reinterpret its new index as an original index.
        ids = list(previous["selection_ids"])
    try:
        _text_body(source, [paragraph_index, paragraph_index + 1])
    except ValueError as error:
        result["reason"] = str(error)
        return result
    result.update(selection_ids=ids, editable=True, reason="")
    return result


def _bounds(marker, original, ids):
    nodes = list(_body(original))
    lookup = {_scope_id(marker, i): i for i in range(1, len(nodes) - 1)}
    if not isinstance(ids, (tuple, list)) or not ids or len(set(ids)) != len(ids):
        raise ValueError("수정할 본문 범위를 텍스트로 명시적으로 선택하세요.")
    if any(value not in lookup for value in ids):
        raise ValueError("선택 범위가 원본의 허용된 내부 문단과 일치하지 않습니다.")
    indexes = sorted(lookup[value] for value in ids)
    if indexes != list(range(indexes[0], indexes[-1] + 1)):
        raise ValueError("한 번에 경계가 보존되는 연속된 본문 범위를 선택하세요.")
    return [indexes[0], indexes[-1] + 1]


def _text_body(raw, bounds):
    nodes = draft.selected(_body(raw), [bounds])
    if any(_objects(node) for node in nodes):
        raise ValueError("선택 범위에 표·그림·컨트롤이 있습니다. 해당 객체를 보존하는 준비된 HWPX 입력을 사용하세요.")
    paragraphs = []
    for node in nodes:
        runs = node.findall(draft.HP + "run")
        if not runs or any(run.attrib != runs[0].attrib for run in runs):
            raise ValueError("혼합 글자 서식은 일반 문단 제안으로 보존할 수 없습니다. 준비된 HWPX를 사용하세요.")
        if any(list(item) for run in runs for item in run if item.tag == draft.HP + "t"):
            raise ValueError("복합 인라인 텍스트는 준비된 HWPX 입력이 필요합니다.")
        text_nodes = [item for run in runs for item in run if item.tag == draft.HP + "t"]
        if not text_nodes:
            raise ValueError("일반 본문에는 실제 텍스트 요소가 필요합니다. 준비된 HWPX를 사용하세요.")
        value = "".join(item.text or "" for item in text_nodes)
        if (value and not value.strip()) or len(value) > 8000:
            raise ValueError("공백만 있거나 너무 긴 문단은 일반 본문 제안 범위로 사용할 수 없습니다.")
        paragraphs.append(value)
    if not 1 <= len(paragraphs) <= 128:
        raise ValueError("본문 범위는 1~128개 문단이어야 합니다.")
    return paragraphs


def preview_request(root, instruction, material_paths=(), selection_ids=(), previous_version=None):
    """Pure captured model context and separate exact local authority binding."""
    root, marker, marker_raw, original = _document(root)
    instruction = harness.text(instruction, 16000)
    previous_grants = _task_records(root)
    if not selection_ids:
        if not previous_grants:
            raise ValueError("원본에서 수정할 본문 범위를 먼저 선택하세요.")
        selection_ids = previous_grants[-1][1]["preview"]["binding"]["selection_ids"]
    original_bounds = _bounds(marker, original, selection_ids)
    source, previous = _source_for(root, marker, original, previous_version)
    bounds = original_bounds if previous is None else previous["target_range"]
    if previous is not None and previous["selection_ids"] != sorted(selection_ids):
        raise ValueError("기존 버전과 다른 범위는 원본에서 새 작업으로 명시적으로 선택하세요.")
    paragraphs = _text_body(source, bounds)
    materials, local_materials = [], []
    if not isinstance(material_paths, (tuple, list)) or len(material_paths) > 16:
        raise ValueError("명시적으로 선택한 근거 파일은 최대 16개입니다.")
    for selected in material_paths:
        own_achievement = False
        name = None
        if isinstance(selected, dict):
            if (set(selected) != {"path", "name", "own_achievement"}
                    or not isinstance(selected["name"], str)
                    or type(selected["own_achievement"]) is not bool):
                raise ValueError("근거 선언에는 경로, 표시 파일 이름, 명시적인 own_achievement 참/거짓이 필요합니다.")
            own_achievement = selected["own_achievement"]
            name = selected["name"]
            selected = selected["path"]
        path = _unlinked(Path(selected).absolute())
        if name is None:
            name = path.name
        if (not isinstance(name, str) or not 1 <= len(name) <= 200
                or Path(name).name != name or "/" in name or "\\" in name
                or any(ord(char) < 32 or 127 <= ord(char) <= 159 for char in name)):
            raise ValueError("근거의 표시 파일 이름을 확인하세요.")
        raw = _capture(path, 65536)
        if path.suffix.lower() != ".txt":
            raise ValueError("공유할 근거 발췌문은 명시적으로 선택한 UTF-8 TXT 파일이어야 합니다.")
        value = raw.decode("utf-8-sig")
        harness.text(value, 65536)
        digest = _digest(raw)
        identifier = "m-" + digest[:24]
        if any(item["id"] == identifier for item in materials):
            raise ValueError("동일한 근거 파일이 중복 선택되었습니다.")
        materials.append({"id": identifier, "name": name, "text": value, "sha256": digest})
        local_materials.append({"path": str(path), "name": name, "sha256": digest,
                                "own_achievement": own_achievement})
    binding = {
        "project_root": str(root),
        "project_sha256": _digest(marker_raw), "source_sha256": marker["source_sha256"],
        "runtime_sha256": review.plan_hash(marker["runtime_identity"]),
        "selection_ids": sorted(selection_ids), "original_bounds": original_bounds,
        "target_range": bounds, "input_sha256": _digest(source),
        "previous_version": previous_version, "materials": local_materials,
        "policy": draft.POLICY,
    }
    seed = {"instruction": instruction, "binding": binding, "materials": materials}
    request_id = "r-" + _digest(_canonical(seed))[:32]
    request = {
        "schema": "hwp-report-request-v1", "request_id": request_id,
        "instruction": instruction,
        "jobs": [{"job_id": "j-" + _digest(_canonical(sorted(selection_ids)))[:24],
                  "label": marker["name"], "paragraphs": paragraphs, "has_objects": False}],
        "materials": materials, "decisions": [],
    }
    request["context_sha256"] = _digest(_canonical(request))
    import hwp_report_writer as writer
    request = writer.validate_request(request)
    preview = {"request": request, "binding": binding}
    preview["preview_sha256"] = _digest(_canonical(preview))
    _json_bytes(preview, _REQUEST_LIMIT)
    return preview


def _validate_preview(root, preview):
    if not isinstance(preview, dict) or set(preview) != {"request", "binding", "preview_sha256"}:
        raise ValueError("초안 요청 미리보기 구조가 올바르지 않습니다.")
    request, binding = preview["request"], preview["binding"]
    expected = preview_request(
        root, request["instruction"], [{"path": item["path"], "name": item["name"],
                                       "own_achievement": item["own_achievement"]}
                                      for item in binding["materials"]],
        binding["selection_ids"], binding["previous_version"],
    )
    if expected != preview:
        raise ValueError("공유할 요청/근거/범위/실행 환경이 미리보기와 달라졌습니다.")
    return expected


def request_record(root, preview):
    """Return UI-only exclusive-write destination/data; never persist permission."""
    root, _, _, _ = _document(root)
    preview = _validate_preview(root, preview)
    directory = _task_directory(root, preview["request"]["request_id"])
    path = directory / "draft-permission.json"
    record = {
        "contract": PERMISSION, "granted_via": "local-review-ui",
        "authority": "create_unapproved_candidate_only",
        "runtime_sha256": preview["binding"]["runtime_sha256"],
        "preview": deepcopy(preview),
        "created_at": datetime.now(timezone.utc).isoformat(),
    }
    if _present(path):
        existing, _ = _read_json(path, _REQUEST_LIMIT)
        if {key: value for key, value in existing.items() if key != "created_at"} != {
            key: value for key, value in record.items() if key != "created_at"
        }:
            raise ValueError("이 요청에 다른 초안 권한이 이미 보존되어 있습니다.")
        return path, existing
    return path, record


def _grant(root, request_id):
    directory = _task_directory(root, request_id)
    path = directory / "draft-permission.json"
    record, raw = _read_json(path, _REQUEST_LIMIT)
    if set(record) != {"contract", "granted_via", "authority", "runtime_sha256", "preview", "created_at"}:
        raise ValueError("초안 권한은 닫힌 UI 권한 형식이어야 합니다.")
    if record["contract"] != PERMISSION or record["granted_via"] != "local-review-ui" or record["authority"] != "create_unapproved_candidate_only":
        raise ValueError("이전 승인 또는 모델 제안은 UI 초안 권한이 아닙니다.")
    if record["preview"]["request"]["request_id"] != request_id:
        raise ValueError("다른 요청의 초안 권한입니다.")
    _validate_preview(root, record["preview"])
    if record["runtime_sha256"] != record["preview"]["binding"]["runtime_sha256"]:
        raise ValueError("초안 권한의 실행 환경 결합이 변경되었습니다.")
    checkpoint = directory / "model-start.json"
    if _present(checkpoint):
        started, _ = _read_json(checkpoint, _META_LIMIT)
        if started != {
            "request_id": request_id, "grant_sha256": _digest(raw),
            "preview_sha256": record["preview"]["preview_sha256"],
            "status": "model_running_unknown_until_result", "retry": False,
        }:
            raise ValueError("모델 전송 기록과 UI 초안 권한의 결합이 변경되었습니다.")
    return directory, record, raw


def _donor(source, bounds, paragraphs):
    """Pure proposal adapter. The only document executor remains template.apply."""
    _text_body(source, bounds)
    parts = draft.parts_from(source)
    section = review.xml_root(parts[draft.PART])
    templates = draft.selected(section, [bounds])
    result = deepcopy(section)
    for node in list(result):
        result.remove(node)
    for index, value in enumerate(paragraphs):
        node = deepcopy(templates[min(index, len(templates) - 1)])
        runs = node.findall(draft.HP + "run")
        template = deepcopy(runs[0])
        for child in list(template):
            template.remove(child)
        text = draft.ET.SubElement(template, draft.HP + "t")
        text.text = value
        for child in list(node):
            node.remove(child)
        node.append(template)
        result.append(node)
    parts[draft.PART] = draft._xml(result)
    return draft._zip(source, parts, [])


def generate(root, request_id, proposal):
    """Exactly one UI-authorized unapproved candidate; failures never retry."""
    import hwp_report_writer as writer

    root, marker, _, original = _document(root)
    directory, grant, grant_raw = _grant(root, request_id)
    validated = writer.validate_proposal(grant["preview"]["request"], proposal)
    proposal_raw = _json_bytes(validated, _REQUEST_LIMIT)
    version_id = "v-" + request_id[2:]
    result_path = directory / "result.json"
    if _present(result_path):
        result, _ = _read_json(result_path, _REQUEST_LIMIT)
        if result.get("proposal_sha256") != _digest(proposal_raw) or result.get("grant_sha256") != _digest(grant_raw):
            raise ValueError("이미 실행한 요청의 제안 또는 권한이 달라졌습니다.")
        candidate(root, version_id)
        return inspect(root)
    if _present(directory / "execution.json") or _present(directory / "failed.json"):
        raise ValueError("이 작업의 실행/실패 기록을 보존합니다. 다시 생성하지 않습니다.")
    _write_new(directory / "execution.json", _json_bytes({
        "request_id": request_id, "grant_sha256": _digest(grant_raw),
        "proposal_sha256": _digest(proposal_raw), "retry": False,
    }, _META_LIMIT))
    try:
        _write_new(directory / "proposal.json", proposal_raw)
        binding = grant["preview"]["binding"]
        source, previous = _source_for(root, marker, original, binding["previous_version"])
        source_path = root / "source.hwpx" if previous is None else Path(previous["path"])
        donor = _donor(source, binding["target_range"], validated["jobs"][0]["paragraphs"])
        donor_path = directory / "proposal.hwpx"
        _write_new(donor_path, donor)
        profile = {"contract": draft.CONTRACT, "jobs": [{
            "label": marker["name"], "source": str(source_path),
            "source_sha256": _digest(source), "payload": str(donor_path),
            "payload_sha256": _digest(donor), "target_range": binding["target_range"],
            "payload_ranges": [[0, len(validated["jobs"][0]["paragraphs"])]],
        }]}
        feedback = {"schema": harness.FEEDBACK, "goal": grant["preview"]["request"]["instruction"],
                    "mode": "authoring", "decisions": grant["preview"]["request"]["decisions"],
                    "sources": [{"id": item["id"], "path": local["path"], "sha256": local["sha256"],
                                 "ownership": "own" if local["own_achievement"] else "unknown",
                                 "use": "achievement" if local["own_achievement"] else "background"}
                                for item, local in zip(grant["preview"]["request"]["materials"], binding["materials"])]}
        _write_new(directory / "profile.json", _json_bytes(profile, _REQUEST_LIMIT))
        _write_new(directory / "feedback.json", _json_bytes(feedback, 65536))
        prepared = harness.prepare(directory / "profile.json", directory / "feedback.json",
                                   directory / "run", candidate_authority={
                                       "path": str(directory / "draft-permission.json"),
                                       "sha256": _digest(grant_raw)})
        run_binding = prepared["binding_sha256"]
        _write_new(directory / "run-binding.json", _json_bytes({"sha256": run_binding}, _META_LIMIT))

        def recheck():
            if _present(directory / "failed.json"):
                raise ValueError("이 작업의 최초 실패/중단 기록을 보존합니다.")
            _, _, current = _grant(root, request_id)
            if current != grant_raw:
                raise ValueError("생성 중 UI 초안 권한이 변경되었습니다.")
            if _capture(directory / "proposal.json", _REQUEST_LIMIT) != proposal_raw:
                raise ValueError("생성 중 구조화 제안이 변경되었습니다.")

        observed = harness.generate_candidate(directory / "run", run_binding, recheck=recheck)
        if len(observed["outputs"]) != 1:
            raise ValueError("검증된 실제 초안 결과가 없습니다.")
        output = observed["outputs"][0]
        blob = _capture(Path(output["path"]), _DOCUMENT_LIMIT)
        target = [binding["target_range"][0],
                  binding["target_range"][0] + len(validated["jobs"][0]["paragraphs"])]
        _validate_revision(original, blob, binding["original_bounds"], target)
        version_dir = root / "versions" / version_id
        version_dir.mkdir(exist_ok=False)
        _write_new(version_dir / "draft.hwpx", blob)
        version = {
            "contract": "hwp-report-version-v1", "version_id": version_id,
            "request_id": request_id, "previous_version": binding["previous_version"],
            "sha256": _digest(blob), "selection_ids": binding["selection_ids"],
            "original_bounds": binding["original_bounds"], "target_range": target,
            "summary": validated["summary"], "kind": "generated",
            "grant_sha256": _digest(grant_raw), "proposal_sha256": _digest(proposal_raw),
            "created_at": datetime.now(timezone.utc).isoformat(),
            "run_binding": run_binding,
        }
        _write_new(version_dir / "version.json", _json_bytes(version, _REQUEST_LIMIT))
        recheck()
        _write_new(result_path, _json_bytes({
            "version_id": version_id, "grant_sha256": _digest(grant_raw),
            "proposal_sha256": _digest(proposal_raw),
            "version_sha256": _digest(_capture(version_dir / "version.json", _REQUEST_LIMIT)),
        }, _META_LIMIT))
    except Exception as exc:
        try:
            _write_new(directory / "failed.json", _json_bytes({
                "status": "failed_preserved", "error": str(exc)[:2000], "retry": False,
            }, _META_LIMIT))
        except FileExistsError:
            pass
        raise
    return inspect(root)


def _validate_revision(original, blob, original_bounds, target):
    parts, new_parts = draft.parts_from(original), draft.parts_from(blob)
    if target[0] != original_bounds[0]:
        raise ValueError("수정 범위의 시작 경계가 변경되었습니다.")
    count = (len(review.xml_root(new_parts[draft.PART]))
             - len(review.xml_root(parts[draft.PART]))
             + original_bounds[1] - original_bounds[0])
    if target[1] - target[0] != count or count < 1:
        raise ValueError("수정본의 정확한 범위/경계를 확인할 수 없습니다.")
    draft.validate(original, blob, original_bounds, count,
                   [name for name in new_parts if name not in parts])


def candidate(root, version_id):
    """Verify exact owned bytes, task, result and locked original on every read."""
    root, marker, _, original = _document(root)
    if not isinstance(version_id, str) or not _VERSION_ID.fullmatch(version_id):
        raise ValueError("수정본 식별자가 올바르지 않습니다.")
    directory = _unlinked(root / "versions" / version_id)
    version, version_raw = _read_json(directory / "version.json", _REQUEST_LIMIT)
    required = {"contract", "version_id", "request_id", "previous_version", "sha256",
                "selection_ids", "original_bounds", "target_range", "summary", "kind",
                "grant_sha256", "proposal_sha256", "run_binding", "created_at"}
    if set(version) != required or version["contract"] != "hwp-report-version-v1" or version["version_id"] != version_id:
        raise ValueError("수정본 메타데이터가 올바르지 않습니다.")
    path = directory / "draft.hwpx"
    blob = _capture(path, _DOCUMENT_LIMIT)
    if _digest(blob) != version["sha256"]:
        raise ValueError("생성 후 수정본 바이트가 바뀌었습니다. 외부 수정본으로 새로 가져오세요.")
    if _bounds(marker, original, version["selection_ids"]) != version["original_bounds"]:
        raise ValueError("원본 범위 결합이 변경되었습니다.")
    _validate_revision(original, blob, version["original_bounds"], version["target_range"])
    if version["kind"] == "generated":
        task_dir, grant, grant_raw = _grant(root, version["request_id"])
        result, _ = _read_json(task_dir / "result.json", _META_LIMIT)
        proposal_raw = _capture(task_dir / "proposal.json", _REQUEST_LIMIT)
        execution, _ = _read_json(task_dir / "execution.json", _META_LIMIT)
        if execution != {
            "request_id": version["request_id"], "grant_sha256": _digest(grant_raw),
            "proposal_sha256": _digest(proposal_raw), "retry": False,
        }:
            raise ValueError("보존된 최초 실행 결합이 변경되었습니다.")
        _check_run_tree(task_dir)
        if result != {
            "version_id": version_id, "grant_sha256": _digest(grant_raw),
            "proposal_sha256": _digest(proposal_raw), "version_sha256": _digest(version_raw),
        } or version["grant_sha256"] != _digest(grant_raw) or version["proposal_sha256"] != _digest(proposal_raw):
            raise ValueError("수정본/작업/권한의 정확한 결과 결합이 변경되었습니다.")
        observed = harness.inspect(task_dir / "run", version["run_binding"])
        if not observed["gate_passed"] or len(observed["outputs"]) != 1 or observed["outputs"][0]["sha256"] != version["sha256"]:
            raise ValueError("기존 실행 결과의 무결성 검증이 실패했습니다.")
        before_text = grant["preview"]["request"]["jobs"][0]["paragraphs"]
    elif version["kind"] == "external":
        base = candidate(root, version["previous_version"])
        if base["selection_ids"] != version["selection_ids"]:
            raise ValueError("외부 수정본 범위가 기준 버전과 달라졌습니다.")
        _validate_external(_capture(Path(base["path"]), _DOCUMENT_LIMIT), blob, base["target_range"])
        before_text = base["text"]
    else:
        raise ValueError("알 수 없는 수정본 종류입니다.")
    text = draft.texts(draft.selected(_body(blob), [version["target_range"]]))
    return {
        **version, "path": str(path), "accepted": _accepted(directory, version, version_raw),
        "text": text,
        "comparison": [{"before": "\n\n".join(before_text), "after": "\n\n".join(text)}],
    }


def _accepted(directory, version, version_raw):
    path = directory / "acceptance.json"
    if not _present(path):
        return False
    record, _ = _read_json(path, _META_LIMIT)
    expected = {
        "contract": ACCEPTANCE, "accepted_via": "local-review-ui",
        "version_id": version["version_id"], "sha256": version["sha256"],
        "version_sha256": _digest(version_raw), "authority": "use_exact_artifact_only",
    }
    if record != expected:
        raise ValueError("현재 바이트와 일치하지 않는 수정본 선택 기록입니다.")
    return True


def acceptance_record(root, version_id, expected_sha256):
    """Pure exact-artifact acceptance preparation; generates/writes nothing."""
    item = candidate(root, version_id)
    if expected_sha256 != item["sha256"]:
        raise ValueError("확인한 수정본 바이트가 현재 파일과 일치하지 않습니다.")
    directory = Path(item["path"]).parent
    return directory / "acceptance.json", {
        "contract": ACCEPTANCE, "accepted_via": "local-review-ui",
        "version_id": version_id, "sha256": item["sha256"],
        "version_sha256": _digest(_capture(directory / "version.json", _REQUEST_LIMIT)),
        "authority": "use_exact_artifact_only",
    }


def import_revision(root, path, base_version):
    """Return the exact verified imported/reused artifact, never a guessed latest."""
    root, _, _, original = _document(root)
    base = candidate(root, base_version)
    path = _unlinked(Path(path).absolute())
    if path.suffix.lower() != ".hwpx":
        raise ValueError("명시적으로 수정된 HWPX 파일을 선택하세요.")
    blob = _capture(path, _DOCUMENT_LIMIT)
    sha256 = _digest(blob)
    if sha256 == base["sha256"]:
        raise ValueError("기준 버전과 변경 없는 파일입니다.")
    count = len(_body(blob)) - len(_body(original)) + base["original_bounds"][1] - base["original_bounds"][0]
    target = [base["original_bounds"][0], base["original_bounds"][0] + count]
    _validate_revision(original, blob, base["original_bounds"], target)
    # External editing cannot alter objects or styles inherited from the base.
    # The existing strict text revision validator permits text only, and checks
    # locked binary/header/package members plus native object/control identity.
    _validate_external(_capture(Path(base["path"]), _DOCUMENT_LIMIT), blob, base["target_range"])
    version_id = "v-" + _digest(_canonical([base_version, sha256]))[:32]
    directory = root / "versions" / version_id
    if _present(directory):
        return candidate(root, version_id)
    directory.mkdir(exist_ok=False)
    _write_new(directory / "draft.hwpx", blob)
    version = {key: base[key] for key in (
        "contract", "selection_ids", "original_bounds", "summary",
        "grant_sha256", "proposal_sha256", "run_binding")}
    version.update({"version_id": version_id, "request_id": None,
                    "previous_version": base_version, "sha256": sha256,
                    "target_range": target, "kind": "external",
                    "created_at": datetime.now(timezone.utc).isoformat(),
                    "summary": ["외부에서 수정한 HWPX를 새 미선택 버전으로 가져왔습니다."]})
    _write_new(directory / "version.json", _json_bytes(version, _REQUEST_LIMIT))
    return candidate(root, version_id)


def _inspect_document(root):
    name, versions, accepted, requests, issues = root.name, [], [], [], []
    locked = False
    try:
        root, marker, _, _ = _document(root)
        name = marker["name"]
        entries = list(_unlinked(root / "versions").iterdir())
        if len(entries) > 200:
            raise ValueError("수정본 수가 허용 한도를 넘었습니다.")
        for entry in sorted(entries, key=lambda item: item.name):
            item = candidate(root, entry.name)
            versions.append(item)
            if item["accepted"]:
                accepted.append(item["version_id"])
        versions.sort(key=lambda item: (item["created_at"], item["version_id"]))
        outputs = [{"path": item["path"], "sha256": item["sha256"],
                    "version_id": item["version_id"], "label": name}
                   for item in versions]
        for directory, grant, _ in _task_records(root):
            _grant(root, directory.name)
            state = "authorized"
            if _present(directory / "failed.json"):
                failure, _ = _read_json(directory / "failed.json", _META_LIMIT)
                issues.append(failure["error"])
                state = "failed_preserved"
            elif _present(directory / "result.json"):
                state = "generated"
            elif _present(directory / "model-start.json"):
                started, _ = _read_json(directory / "model-start.json", _META_LIMIT)
                _, _, grant_raw = _grant(root, directory.name)
                if started != {
                    "request_id": directory.name, "grant_sha256": _digest(grant_raw),
                    "preview_sha256": grant["preview"]["preview_sha256"],
                    "status": "model_running_unknown_until_result", "retry": False,
                }:
                    raise ValueError("모델 전송 기록의 요청/권한 결합이 변경되었습니다.")
                state = "model_running"
                issues.append("모델 요청 결과가 확인되지 않았습니다. 자동으로 다시 전송하지 않습니다.")
            elif _present(directory / "execution.json"):
                state = "execution_recorded"
                issues.append("완료되지 않은 실행 기록을 보존합니다. 다시 생성하지 않습니다.")
            locked = locked or state in ("generated", "failed_preserved", "execution_recorded", "model_running")
            requests.append({"request_id": directory.name, "state": state,
                             "preview_sha256": grant["preview"]["preview_sha256"],
                             "selection_ids": grant["preview"]["binding"]["selection_ids"],
                             "previous_version": grant["preview"]["binding"]["previous_version"]})
        phase = "blocked" if issues else (("accepted" if versions[-1]["accepted"] else "generated_unapproved")
                                          if versions else ("authorized_unapproved_draft" if requests else "awaiting_request"))
        return ReportProjectInfo(root, root, None, name, 1, None, None, phase,
                                 not issues, locked, tuple(issues), tuple(outputs),
                                 tuple(versions), tuple(accepted), tuple(requests), True,
                                 marker["source_sha256"])
    except Exception as exc:
        return ReportProjectInfo(root, root, None, name, 1, None, None, "blocked",
                                 False, locked, (str(exc),), (), tuple(versions),
                                 tuple(accepted), tuple(requests), True)


def begin_request(root, request_id):
    """Persist the one allowed model-send boundary before any provider call."""
    root, _, _, _ = _document(root)
    directory, grant, grant_raw = _grant(root, request_id)
    for filename, state in (("result.json", "generated"), ("failed.json", "failed_preserved"),
                            ("execution.json", "execution_recorded"), ("model-start.json", "model_running")):
        if _present(directory / filename):
            if state == "generated":
                result, _ = _read_json(directory / filename, _META_LIMIT)
                candidate(root, result["version_id"])
            return {"request_id": request_id, "state": state, "may_call_model": False}
    try:
        _write_new(directory / "model-start.json", _json_bytes({
            "request_id": request_id, "grant_sha256": _digest(grant_raw),
            "preview_sha256": grant["preview"]["preview_sha256"],
            "status": "model_running_unknown_until_result", "retry": False,
        }, _META_LIMIT))
    except FileExistsError:
        return {"request_id": request_id, "state": "model_running", "may_call_model": False}
    return {"request_id": request_id, "state": "model_running", "may_call_model": True}


def fail_request(root, request_id, error):
    """Preserve the first surfaced model/controller failure; never grant/retry."""
    root, _, _, _ = _document(root)
    directory, _, _ = _grant(root, request_id)
    if _present(directory / "result.json"):
        return inspect(root)
    path = directory / "failed.json"
    if not _present(path):
        try:
            _write_new(path, _json_bytes({
                "status": "failed_preserved", "error": harness.text(str(error)[:2000] or type(error).__name__, 2000),
                "retry": False,
            }, _META_LIMIT))
        except FileExistsError:
            pass
    return inspect(root)


def _validate_external(before, after, bounds):
    harness.strict_delta(before, after, [bounds], "terminology", [])
    old_text = "\n".join(harness.plain_texts(before, [bounds]))
    new_text = "\n".join(harness.plain_texts(after, [bounds]))
    if harness.NUMBER.findall(old_text) != harness.NUMBER.findall(new_text):
        raise ValueError("외부 수정본의 수치 변경은 별도 근거가 결합된 새 본문 요청이 필요합니다.")
    for marker in ("PASS", "인증을 획득", "인증을 취득", "인증 완료", "certified"):
        if new_text.count(marker) > old_text.count(marker):
            raise ValueError("외부 수정본에 새 인증/성과 주장이 추가되었습니다. 근거 검토가 필요합니다.")


def request_preview(root, request_id):
    """Read only the validated retained request/scope, never raw authority files."""
    root, _, _, _ = _document(root)
    _, grant, _ = _grant(root, request_id)
    return deepcopy(grant["preview"])
