"""Independently implemented local library + saved-notes export.

Inspired by Paperless-ngx/Paperdesk/Okular workflows; NO borrowed source/assets.
Library presence != integrity/approval. No watcher, history DB, Native or cleanup.
"""
from dataclasses import dataclass
from pathlib import Path
import os
import re
import stat

import hwp_report_project as projects

import hwp_review as review

MAX_METADATA = 4 * 1024 * 1024
MAX_COMMENTS = 16 * 1024 * 1024
STATES = {"prepared": "검토 자료 있음", "staged": "준비 전", "incomplete": "미완료·중단", "attention": "확인 필요", "empty": "빈 폴더"}


@dataclass(frozen=True)
class LibraryEntry:
    root: Path
    name: str
    state: str
    modified: float
    pages: int | None = None
    note: str = ""


@dataclass(frozen=True)
class LibraryListing:
    base: Path | None
    entries: tuple[LibraryEntry, ...]
    limited: bool = False


def linked(path):
    return path.is_symlink() or (hasattr(path, "is_junction") and path.is_junction())


def default_library():
    location = os.environ.get("LOCALAPPDATA")
    return Path(location) / "HwpReview" / "Projects" if location else None


def bounded_json(path, maximum):
    if linked(path) or not path.is_file() or path.stat().st_size > maximum:raise ValueError("Non-regular, linked or oversized metadata")
    with path.open("rb") as stream:
        raw = stream.read(maximum + 1)
    if len(raw) > maximum:raise ValueError("Metadata changed beyond its size limit")
    return review.parse_json(raw.decode("utf-8"))


def describe_folder(root):
    modified = root.stat().st_mtime
    if linked(root):return LibraryEntry(root, root.name, "attention", modified, note="연결된 폴더는 열지 않습니다")
    report_marker = root / "report-project.json"
    if os.path.lexists(report_marker):
        try:
            marker = bounded_json(report_marker, 262144)
            if not isinstance(marker, dict) or marker.get("contract") not in ("hwp-report-project-v2", "hwp-report-prepared-project-v2"):
                raise ValueError("Invalid report project")
            name = marker.get("name")
            if not isinstance(name, str) or not name.strip() or len(name) > 200:
                raise ValueError("Invalid report name")
            if marker["contract"] == "hwp-report-project-v2":
                ready = True  # Metadata presence only; live admission happens on open.
            else:
                binding = root / "report-binding.json"
                run = root / "run"
                ready = binding.is_file() and not linked(binding) and run.is_dir() and not linked(run)
            return LibraryEntry(
                root, name, "prepared" if ready else "incomplete",
                max(modified, report_marker.stat().st_mtime),
                note="HWPX 보고서 · 열 때 입력·실행 파일·기기·승인 상태를 다시 검증합니다. 목록은 승인이 아닙니다.",
            )
        except (OSError, ValueError, UnicodeError):
            return LibraryEntry(root, root.name, "attention", modified, note="보고서 메타데이터 확인 필요 · HWP 경로로 우회하지 않습니다")
    workspace = root if (root / "manifest.json").exists() else root / "_review"
    if linked(workspace):return LibraryEntry(root, root.name, "attention", modified, note="연결된 검토 자료")
    manifest_file = workspace / "manifest.json"
    if manifest_file.is_file():
        try:
            manifest = bounded_json(manifest_file, MAX_METADATA)
            if not isinstance(manifest, dict) or not isinstance(manifest.get("pages"), list) or not manifest["pages"] or not isinstance(manifest.get("source"), str):
                raise ValueError("Incomplete metadata")
            comments = workspace / "comments.json"
            comments_modified = comments.stat().st_mtime if comments.is_file() and not linked(comments) else 0
            return LibraryEntry(root, Path(manifest["source"]).name, "prepared", max(modified, manifest_file.stat().st_mtime, comments_modified),
                                len(manifest["pages"]), "열 때 원본·기준 자료를 다시 검증합니다. 수정 승인이 아닙니다")
        except (OSError, ValueError, UnicodeError):
            return LibraryEntry(root, root.name, "attention", modified, note="메타데이터를 읽을 수 없습니다. 임의로 복구하지 않습니다")
    if workspace.exists():return LibraryEntry(root, root.name, "incomplete", max(modified, workspace.stat().st_mtime), note="자료 보존 · 자동 재시도 없음")
    candidates = []
    for index, path in enumerate(root.iterdir()):
        if index >= 1000:return LibraryEntry(root, root.name, "attention", modified, note="너무 많은 파일 · 직접 폴더를 확인하세요")
        if path.suffix.lower() == ".hwp" and not path.is_dir():candidates.append(path)
        if len(candidates) > 1:break
    if len(candidates) > 1 or (candidates and linked(candidates[0])):
        return LibraryEntry(root, root.name, "attention", modified, note="여러 HWP 또는 연결 파일 · 자동 선택하지 않습니다")
    if candidates:return LibraryEntry(root, candidates[0].name, "staged", max(modified, candidates[0].stat().st_mtime), note="한글을 실행하지 않았습니다")
    return LibraryEntry(root, root.name, "empty", modified)


def scan_library(base=None, *, limit=200):
    if type(limit) is not int or not 1 <= limit <= 200:raise ValueError("Library limit must be 1..200")
    base = default_library() if base is None else base
    if base is None:return LibraryListing(base, ())
    if not base.is_absolute() or any(linked(path) for path in (base, *base.parents)):
        raise ValueError("실제 작업 폴더 모음을 선택하세요. 연결 폴더를 순회하지 않습니다.")
    if not base.exists():return LibraryListing(base, ())
    if not base.is_dir():raise ValueError("작업 폴더들의 상위 디렉터리를 선택하세요.")
    base = base.resolve(strict=True)
    entries, limited = [], False
    for visited, path in enumerate(base.iterdir()):
        if visited >= 1200 or len(entries) >= limit:
            limited = True;break
        if not path.is_dir() and not linked(path):continue
        try:entries.append(describe_folder(path))
        except (OSError, ValueError):entries.append(LibraryEntry(path, path.name, "attention", 0, note="접근할 수 없습니다"))
    return LibraryListing(base, tuple(sorted(entries, key=lambda row: (-row.modified, row.name.casefold()))), limited)


def work_folder(root):
    """Admit an existing lexical, unlinked work directory without creating it."""
    root = projects._unlinked(Path(root))
    if not root.is_dir():
        raise ValueError("기존의 실제 작업 폴더를 선택하세요.")
    if any(part.casefold() == ".hwpreview" for part in root.parts):
        raise ValueError("앱 관리 자료가 아닌 실제 작업 폴더를 선택하세요.")
    if any(projects.has_project_marker(item) for item in (root, *root.parents)):
        raise ValueError("저장된 보고서 작업은 작업 열기로 여세요. 내부 자료를 작업 폴더로 선택하지 않습니다.")
    return root.resolve(strict=True)


def workspace_document(root, path, *, new=False):
    """Confine explicit intake/creation to real workspace files, not snapshots."""
    root = work_folder(root)
    path = projects._unlinked(Path(path))
    if not path.is_relative_to(root) or path == root or path.suffix.lower() != ".hwpx":
        raise ValueError("선택한 작업 폴더 안의 HWPX 파일을 선택하세요.")
    relative = path.relative_to(root)
    if any(part.casefold() == ".hwpreview" for part in relative.parts):
        raise ValueError("앱 관리 자료에는 문서를 열거나 만들 수 없습니다.")
    if any(projects.has_project_marker(item) for item in path.parents if item.is_relative_to(root)):
        raise ValueError("저장된 작업의 원본 사본·수정본은 새 원본으로 열지 않습니다.")
    if new:
        if os.path.lexists(path):
            raise ValueError("새 빈 문서는 아직 존재하지 않는 HWPX 파일로 만드세요.")
        if not path.parent.is_dir():
            raise ValueError("새 문서를 저장할 기존 폴더를 선택하세요.")
    else:
        info = path.lstat()
        if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
            raise ValueError("연결되지 않은 실제 HWPX 파일을 선택하세요.")
    return path


def workspace_project_base(root):
    return projects._unlinked(work_folder(root) / ".hwpreview" / "Projects")


def _workspace_records(root):
    """Read bounded saved-project identities; listing never performs live intake."""
    base = workspace_project_base(root)
    managed = base.parent
    if os.path.lexists(managed) and not managed.is_dir():
        raise ValueError("작업 폴더의 앱 관리 위치가 실제 폴더가 아닙니다.")
    rows, records, issues = [], {}, []
    if not os.path.lexists(base):
        return rows, records, issues, False
    if not base.is_dir():
        raise ValueError("작업 폴더의 앱 관리 저장 위치가 실제 폴더가 아닙니다.")
    children = []
    with os.scandir(base) as entries:
        for entry in entries:
            if len(children) >= 200:
                return [], {}, ["저장된 작업 목록 한도를 넘었습니다. 새 원본을 자동 등록하지 않습니다."], True
            children.append(Path(entry.path))
    for child in sorted(children, key=lambda item: (item.name.casefold(), item.name)):
        try:
            projects._unlinked(child)
            if not child.is_dir():
                raise ValueError("저장된 작업 항목이 실제 폴더가 아닙니다.")
            description = describe_folder(child)
            rows.append({"root": str(child), "name": description.name,
                         "status": STATES[description.state], "detail": description.note})
            marker, _ = projects._read_json(child / projects.PROJECT_FILE, projects._META_LIMIT)
            source = marker.get("source")
            if marker.get("contract") != projects.DOCUMENT_PROJECT or not isinstance(source, str):
                raise ValueError("저장된 원본 연결을 확인할 수 없습니다.")
            source = Path(source)
            if not source.is_absolute() or ".." in source.parts:
                raise ValueError("저장된 원본 경로가 올바르지 않습니다.")
            records.setdefault(os.path.normcase(str(source)), []).append(child)
        except (OSError, ValueError, UnicodeError) as error:
            issues.append(f"저장된 작업 확인 필요 · {child.name}: {error}")
    return rows, records, issues, False


def _require_live_records(records):
    # Before creating a new identity, a changed/stopped record must not become
    # an apparent absence merely because its saved source path was changed.
    for roots in records.values():
        if len(roots) != 1:
            raise ValueError("같은 원본에 연결된 작업이 여러 개입니다. 저장된 작업을 직접 선택하세요.")
        info = projects.inspect(roots[0])
        if not info.document_first or not info.gate_passed:
            raise ValueError("기존 원본 연결을 다시 검증하지 못해 새 작업을 만들지 않습니다: " + "; ".join(info.issues))


def open_workspace_document(root, source):
    """The workspace-tree capability admits only in-folder original documents."""
    return _capture_workspace_source(root, workspace_document(root, source))


def _open_explicit_workspace_document(root, source):
    """Only the fixed explicit-file picker may intake an external original."""
    root = work_folder(root)
    source = projects._unlinked(Path(source))
    if source.suffix.lower() != ".hwpx":
        raise ValueError("직접 선택한 원본 HWPX 파일이 필요합니다.")
    if any(part.casefold() == ".hwpreview" for part in source.parts):
        raise ValueError("앱 관리 자료는 새 원본으로 열지 않습니다.")
    if any(projects.has_project_marker(item) for item in source.parents):
        raise ValueError("저장된 작업의 내부 사본은 원본으로 다시 등록하지 않습니다.")
    info = source.lstat()
    if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
        raise ValueError("연결되지 않은 실제 HWPX 파일을 선택하세요.")
    return _capture_workspace_source(root, source)


def _capture_workspace_source(root, source):
    """Reuse one exact live-valid capture, or capture an admitted original."""
    _, records, issues, limited = _workspace_records(root)
    matches = records.get(os.path.normcase(str(source)), [])
    if len(matches) > 1:
        raise ValueError("같은 원본에 연결된 작업이 여러 개입니다. 저장된 작업을 직접 선택하세요.")
    if issues or limited:
        raise ValueError("저장된 원본 연결을 모두 확인할 수 없어 새 작업을 만들지 않습니다. " + "; ".join(issues))
    if matches:
        info = projects.inspect(matches[0])
        if not info.document_first or not info.gate_passed:
            raise ValueError("기존 원본 작업을 다시 검증하지 못했습니다. 보존하며 새로 만들지 않습니다: " + "; ".join(info.issues))
        return info
    _require_live_records(records)
    return projects.new_document(source, base=workspace_project_base(root))


def create_blank_document(root, destination):
    """Delegate one new blank only when no preserved source identity conflicts."""
    destination = workspace_document(root, destination, new=True)
    _, records, issues, limited = _workspace_records(root)
    if issues or limited or records.get(os.path.normcase(str(destination))):
        raise ValueError("기존 원본 연결 또는 확인이 필요한 작업을 보존합니다. 다른 새 파일 위치를 선택하세요.")
    _require_live_records(records)
    return projects.new_blank_document(destination, base=workspace_project_base(root))


def scan_workspace(root=None, *, limit=500, visit_limit=10000, depth_limit=32):
    """Bounded deterministic filename-only HWPX discovery; no content sharing."""
    if (type(limit) is not int or not 1 <= limit <= 500
            or type(visit_limit) is not int or not 1 <= visit_limit <= 10000
            or type(depth_limit) is not int or not 1 <= depth_limit <= 32):
        raise ValueError("작업 폴더 목록 한도가 올바르지 않습니다.")
    if root is None:
        return {"root": "", "name": "", "documents": [], "projects": [], "issues": [], "limited": False}
    root = work_folder(root)
    try:
        rows, records, issues, limited = _workspace_records(root)
    except (OSError, ValueError) as error:
        rows, records, issues, limited = [], {}, [f"저장된 작업에 접근할 수 없습니다: {error}"], False
    documents, pending, visited = [], [(root, 0)], 0
    while pending:
        directory, depth = pending.pop()
        try:
            projects._unlinked(directory)
            if directory != root and projects.has_project_marker(directory):
                if len(rows) < 200:
                    description = describe_folder(directory)
                    rows.append({"root": str(directory), "name": description.name,
                                 "status": STATES[description.state], "detail": description.note})
                else:
                    limited = True
                    issues.append("저장된 작업 목록 한도에 도달했습니다. 작업 폴더를 직접 선택하세요.")
                continue
            children = []
            with os.scandir(directory) as entries:
                for entry in entries:
                    if visited >= visit_limit:
                        limited = True
                        issues.append(f"파일 목록 한도에 도달했습니다 · {directory.relative_to(root).as_posix()}")
                        children = []
                        break
                    visited += 1
                    children.append(Path(entry.path))
            subfolders = []
            for path in sorted(children, key=lambda item: (item.name.casefold(), item.name)):
                if path.name.casefold() == ".hwpreview":
                    continue
                try:
                    projects._unlinked(path)
                    info = path.lstat()
                    if stat.S_ISDIR(info.st_mode):
                        if depth >= depth_limit:
                            limited = True
                            issues.append(f"폴더 깊이 한도에 도달했습니다 · {path.relative_to(root).as_posix()}")
                        else:
                            subfolders.append((path, depth + 1))
                    elif path.suffix.lower() == ".hwpx":
                        if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
                            raise ValueError("연결되지 않은 실제 파일이 아닙니다.")
                        if len(documents) >= limit:
                            limited = True
                            issues.append("HWPX 문서 목록 한도에 도달했습니다. 파일을 직접 선택하세요.")
                            pending.clear()
                            subfolders.clear()
                            break
                        matches = records.get(os.path.normcase(str(path)), [])
                        documents.append({"path": str(path), "name": path.name,
                                          "relative_path": path.relative_to(root).as_posix(),
                                          "project_root": str(matches[0]) if len(matches) == 1 else ""})
                        if len(matches) > 1:
                            issues.append(f"같은 원본에 연결된 저장 작업이 여러 개입니다 · {path.name}")
                except (OSError, ValueError) as error:
                    issues.append(f"목록에서 제외했습니다 · {path.relative_to(root).as_posix()}: {error}")
            pending.extend(reversed(subfolders))
        except (OSError, ValueError) as error:
            issues.append(f"폴더에 접근할 수 없습니다 · {directory.relative_to(root).as_posix()}: {error}")
    documents.sort(key=lambda row: (row["relative_path"].casefold(), row["relative_path"]))
    if len(issues) > 100:
        issues = issues[:100] + ["확인 사항이 많아 처음 100개만 표시합니다."]
        limited = True
    return {"root": str(root), "name": root.name or str(root), "documents": documents,
            "projects": rows, "issues": issues, "limited": limited}


def filter_library(listing, query="", state="all"):
    if state != "all" and state not in STATES:raise ValueError("Unknown library filter")
    query = query.strip().casefold()
    return tuple(row for row in listing.entries if (state == "all" or row.state == state)
                 and (not query or query in row.name.casefold() or query in row.root.name.casefold()))


def export_notes(workspace, destination):
    """Explicit saved-notes-only export, NEVER approval/result/original archive."""
    if any(linked(path) for path in (workspace, *workspace.parents)):raise ValueError("Linked workspace is unsupported")
    workspace = workspace.resolve(strict=True)
    destination = destination.expanduser().absolute()
    if destination.suffix.lower() != ".md":raise ValueError("검토 메모는 새 .md 파일로 저장하세요.")
    if any(linked(path) for path in (destination, *destination.parents)):raise ValueError("연결 경로로 내보내지 않습니다.")
    destination = destination.resolve(strict=False)
    protected = workspace.parent if workspace.name == "_review" else workspace
    if destination.is_relative_to(protected):raise ValueError("원본·검토 자료 폴더 밖의 새 파일을 선택하세요.")
    # Always perform full baseline checks; library metadata is NOT a trusted gate.
    manifest = review.verify_baseline(workspace)
    path = workspace / "comments.json"
    if linked(path) or not path.is_file() or path.stat().st_size > MAX_COMMENTS:raise ValueError("저장된 메모는 허용 크기의 실제 파일이어야 합니다.")
    comments_sha = review.file_hash(path)
    payload = bounded_json(path, MAX_COMMENTS)
    review.validate_comments(payload, manifest)
    source = Path(manifest["source"])
    if destination == source.resolve():raise ValueError("원본은 내보내기 대상이 아닙니다.")
    if review.file_hash(path) != comments_sha:raise ValueError("저장된 메모가 읽는 도중 바뀌었습니다. 다시 확인하세요.")
    groups = {}
    for comment in payload["comments"]:groups.setdefault(comment["page"], []).append(comment)
    identity = {"source": source.name, "source_sha256": manifest["source_audit"]["sha256"],
                "workspace_id": manifest["workspace_id"], "saved_comments_sha256": comments_sha}
    import json
    lines = ["# 저장된 검토 메모", "", "> 문서 수정 승인·적용 결과·제출 확인이 아닙니다. 저장한 메모만 포함하며 브라우저의 미저장 초안은 포함하지 않습니다.", "",
             "```json", json.dumps(identity, ensure_ascii=True, indent=2), "```", "",
             f"- 원본 페이지: {len(manifest['pages'])}쪽", f"- 저장된 메모: {len(payload['comments'])}개", ""]
    for page in sorted(groups):
        lines.extend([f"## {page}쪽", ""])
        for comment in groups[page]:
            reference = {"id": comment["id"], "anchor_id": comment["anchor_id"], "region": comment["region"]}
            lines.extend(["```json", json.dumps(reference, ensure_ascii=True), "```", ""])
            text = comment["comment"]
            fence = "~" * max(3, 1 + max((len(run) for run in re.findall(r"~+", text)), default=0))
            lines.extend([fence + "text", text, fence, ""])
    if not groups:lines.append("저장된 메모가 없습니다.")
    text = "\n".join(lines) + "\n"
    # Detect concurrent saved-note changes before writing. An export snapshot
    # does not lock an active review server or save its unsubmitted drafts.
    if review.file_hash(path) != comments_sha:raise ValueError("메모가 다른 창에서 바뀌었습니다. 다시 확인한 뒤 내보내세요.")
    encoded = text.encode("utf-8")  # Validate before creating a destination (e.g. malformed Unicode).
    with destination.open("xb") as stream:stream.write(encoded)
    return {"path": str(destination), "notes": len(payload["comments"]), "saved_comments_sha256": comments_sha}
