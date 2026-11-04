"""Human-authored schema-1 text proposal only. Never infer content or execute HWP.

Called by the existing token/origin/CAS-protected local UI. Approval remains the
existing separate exact-plan human action. No fallback to wider Native policies.
"""
from pathlib import Path
import hwp_review as review
import json
import os
import uuid


def compile_text(payload, manifest, baseline):
    if not isinstance(payload, dict) or set(payload) != {"workspace_id", "baseline_hwpx_sha256", "edits"}:
        raise ValueError("문서 ID·기준 해시와 명시한 교체 문구만 허용합니다.")
    plan = {"schema": 1, **payload}
    if not isinstance(plan["edits"], list):raise ValueError("수정 항목 목록이 필요합니다.")
    for edit in plan["edits"]:
        if not isinstance(edit, dict) or not isinstance(edit.get("new_text"), str) or not edit["new_text"].strip():
            raise ValueError("빈 문구 삭제·자료 추측은 지원하지 않습니다. 정확한 새 문구를 입력하세요.")
    review.validate_plan(plan, manifest, baseline)
    return plan


def archive_previous(workspace: Path, previous: bytes | None):
    """Archive raw prior proposal bytes ONLY, never read/write approval records."""
    if previous is None:return
    folder = workspace / "proposal-history"
    if folder.is_symlink() or (hasattr(folder, "is_junction") and folder.is_junction()):
        raise ValueError("연결된 수정안 이력 폴더에는 저장하지 않습니다.")
    folder.mkdir(exist_ok=True)
    target = folder / (review.digest(previous) + ".json")
    if target.is_symlink() or (hasattr(target, "is_junction") and target.is_junction()):
        raise ValueError("연결된 수정안 이력은 지원하지 않습니다.")
    if target.exists():
        if target.read_bytes() != previous:raise ValueError("수정안 이력의 바이트가 일치하지 않습니다.")
    else:
        with target.open("xb") as stream:stream.write(previous)


def write_proposal(workspace, plan, expected_revision):
    # Atomic whole-file publication, not a filesystem transaction/CAS against an
    # arbitrary external writer. Local browser requests are server-serialized.
    path = workspace / "proposal.json"
    if review.file_revision(path) != expected_revision:raise ValueError("수정안이 저장 전에 변경됐습니다. 최신안을 다시 확인하세요.")
    raw = json.dumps(plan, ensure_ascii=False, indent=2).encode("utf-8")
    temporary = workspace / (".proposal-" + uuid.uuid4().hex + ".tmp")
    with temporary.open("xb") as stream:stream.write(raw)
    if path.is_symlink() or (hasattr(path, "is_junction") and path.is_junction()) or review.file_revision(path) != expected_revision:
        raise ValueError("수정안 경로/내용이 변경됐습니다. 초안 파일은 보존하며 자동 재시도하지 않습니다.")
    os.replace(temporary, path)
