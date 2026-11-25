"""Synchronous desktop client for the existing HWP loopback review handler."""

import http.client
import json
import os
import re
import stat
from pathlib import Path

import hwp_review as r

_SCHEMAS = {"text": 1, "layout": 3, "fill": 4}
_SHA256 = re.compile(r"[0-9a-f]{64}\Z")
_PNG = b"\x89PNG\r\n\x1a\n"
_DOWNLOADS = {
    "hwp": ("application/octet-stream", bytes.fromhex("d0cf11e0a1b11ae1")),
    "pdf": ("application/pdf", b"%PDF-"),
}
_JSON_LIMIT = 16 * 1024 * 1024
_REQUEST_LIMIT = 1024 * 1024


class LegacyReviewError(ValueError):
    """A safe, localized error; status is available for conflict handling."""

    def __init__(self, message: str, *, status: int | None = None):
        super().__init__(message)
        self.status = status


def _sha(value) -> str:
    if not isinstance(value, str) or not _SHA256.fullmatch(value):
        raise LegacyReviewError("검토 해시가 올바르지 않습니다. 상태를 다시 읽어주세요.")
    return value


def _result_id(value) -> str:
    if not isinstance(value, str) or not value.isascii() or not r.RUN_ID.fullmatch(value):
        raise LegacyReviewError("결과 ID가 올바르지 않습니다.")
    return value


def _check_path(path: Path, *, new: bool = False) -> Path:
    """Reject every reparse component, not just symlinks and junctions."""
    path = path.absolute()
    if (
        not path.anchor
        or path.anchor.startswith(("\\\\", "//"))
        or any(
            part in (".", "..") or ":" in part or part.endswith((" ", "."))
            for part in path.parts[1:]
        )
    ):
        raise LegacyReviewError("특수 경로는 사용할 수 없습니다.")
    for entry in (*reversed(path.parents), path):
        is_reserved = getattr(os.path, "isreserved", None)
        if (is_reserved(entry) if is_reserved is not None else entry.is_reserved()):
            raise LegacyReviewError("특수 경로는 사용할 수 없습니다.")
        try:
            info = entry.lstat()
        except FileNotFoundError:
            if new and entry == path:
                return path
            raise
        if stat.S_ISLNK(info.st_mode) or (
            getattr(info, "st_file_attributes", 0) & stat.FILE_ATTRIBUTE_REPARSE_POINT
        ):
            raise LegacyReviewError("연결된 경로는 사용할 수 없습니다.")
        if entry == path and new:
            raise LegacyReviewError("기존 파일이나 폴더를 덮어쓰지 않습니다.")
        expected = stat.S_ISREG if entry == path else stat.S_ISDIR
        if not expected(info.st_mode):
            raise LegacyReviewError("일반 파일과 폴더 경로만 사용할 수 있습니다.")
    return path


class LegacyReviewClient:
    def __init__(self, port: int, token: str, workspace: Path, workspace_id: str):
        if (
            type(port) is not int
            or not 1 <= port <= 65535
            or not isinstance(token, str)
            or not re.fullmatch(r"[a-f0-9]{64}", token)
            or not isinstance(workspace_id, str)
            or not workspace_id
        ):
            raise LegacyReviewError("로컬 검토 연결 정보가 올바르지 않습니다.")
        self._port = port
        self._token = token
        self._workspace = Path(workspace).absolute()
        self._workspace_id = workspace_id

    def _request(self, path, *, payload=None, revision=None, mime="application/json", limit=_JSON_LIMIT):
        connection = http.client.HTTPConnection("127.0.0.1", self._port, timeout=15)
        headers = {"X-Review-Token": self._token}
        body = None
        if payload is not None:
            try:
                body = json.dumps(payload, ensure_ascii=False, allow_nan=False).encode("utf-8")
            except (ValueError, TypeError, RecursionError):
                raise LegacyReviewError("저장 데이터 형식이 올바르지 않습니다.") from None
            if not 0 < len(body) <= _REQUEST_LIMIT:
                raise LegacyReviewError("저장 데이터가 1 MiB 제한을 초과했습니다.")
            headers.update({
                "Content-Type": "application/json",
                "Origin": f"http://127.0.0.1:{self._port}",
            })
        if revision is not None:
            headers["If-Match"] = '"' + ("absent" if revision == "absent" else _sha(revision)) + '"'
        try:
            connection.request("GET" if body is None else "POST", path, body, headers)
            response = connection.getresponse()
            expected_status = 202 if path in ("/api/apply-text", "/api/apply-contract") else 200
            if response.status != expected_status:
                message = {
                    403: "로컬 검토 연결이 거부되었습니다.",
                    409: "다른 작업에서 내용이 바뀌었습니다. 초안을 유지한 채 다시 읽어주세요.",
                    423: "적용이 잠겨 있습니다. 기존 작업을 확인해주세요.",
                    428: "검토 상태를 다시 읽어주세요.",
                }.get(response.status, "검토 서버가 요청을 거부했습니다. 현재 상태를 확인해주세요.")
                if path == "/api/fill-state" and response.status == 400:
                    message = "빈칸 채우기 입력이 없거나 유효하지 않습니다. 준비된 입력을 확인해주세요."
                raise LegacyReviewError(message, status=response.status)
            if (response.getheader("Content-Type", "").split(";", 1)[0].strip() != mime
                or response.getheader("Transfer-Encoding") is not None):
                raise LegacyReviewError("검토 서버 응답 형식이 올바르지 않습니다.")
            declared = response.length
            if declared is not None and not 0 < declared <= limit:
                raise LegacyReviewError("검토 서버 응답 크기가 허용 범위를 벗어났습니다.")
            data = response.read(limit + 1)
            if not 0 < len(data) <= limit or (declared is not None and len(data) != declared):
                raise LegacyReviewError("검토 서버 응답 크기가 허용 범위를 벗어났습니다.")
            if mime != "application/json":
                return data, response.getheader("X-Artifact-Sha256")
            try:
                value = r.parse_json(data.decode("utf-8"))
            except (ValueError, RecursionError):
                raise LegacyReviewError("검토 서버 응답을 읽을 수 없습니다.") from None
            if not isinstance(value, dict):
                raise LegacyReviewError("검토 서버 응답 형식이 올바르지 않습니다.")
            return value
        except (OSError, http.client.HTTPException):
            raise LegacyReviewError("로컬 검토 연결이 끊겼습니다. 자동 재시도하지 않습니다. 상태를 확인해주세요.") from None
        finally:
            connection.close()

    @staticmethod
    def _kind(kind):
        if not isinstance(kind, str) or kind not in _SCHEMAS:
            raise LegacyReviewError("지원하지 않는 검토 종류입니다.")

    def _manifest(self, state, kind=None):
        manifest = state.get("manifest") if isinstance(state, dict) else None
        if not isinstance(manifest, dict) or manifest.get("workspace_id") != self._workspace_id:
            raise LegacyReviewError("현재 문서와 검토 문서가 일치하지 않습니다.")
        _sha(manifest.get("baseline_hwpx_sha256"))
        if kind is not None:
            self._kind(kind)
            if (kind == "fill" and "input_sha256" not in state) or (
                kind != "fill" and state.get("proposal_kind") != kind
            ):
                raise LegacyReviewError("선택한 검토 종류의 상태를 다시 읽어주세요.")
        return manifest

    def snapshot(self, kind: str) -> dict:
        self._kind(kind)
        state = self._request("/api/fill-state" if kind == "fill" else f"/api/state?proposal={kind}")
        self._manifest(state, kind)
        return state

    def page(self, number: int, *, result_id="", side="after", dpi=100) -> bytes:
        if (
            type(number) is not int or not 1 <= number <= 999999
            or type(dpi) is not int or dpi not in (100, 144)
            or side not in ("before", "after", "diff")
            or not isinstance(result_id, str)
            or (not result_id and side != "after")
        ):
            raise LegacyReviewError("페이지, 비교면 또는 해상도가 올바르지 않습니다.")
        if result_id:
            path = f"/comparison/{_result_id(result_id)}/{side}/{number}.png"
        else:
            path = f"/page/{number}.png"
        data, _ = self._request(f"{path}?dpi={dpi}", mime="image/png")
        if not data.startswith(_PNG):
            raise LegacyReviewError("페이지 이미지 형식이 올바르지 않습니다.")
        return data

    def save_proposal(self, kind, state, items) -> dict:
        self._kind(kind)
        manifest = self._manifest(state, kind)
        if not isinstance(items, list) or any(not isinstance(item, dict) for item in items):
            raise LegacyReviewError("수정 항목 목록이 필요합니다.")
        revision = state.get("proposal_revision")
        if revision != "absent":
            revision = _sha(revision)
        if kind == "fill":
            if any(
                set(item) != {"field_id", "page", "region"}
                or not isinstance(item["field_id"], str)
                or type(item["page"]) is not int
                or not isinstance(item["region"], list)
                or len(item["region"]) != 4
                or any(type(value) not in (int, float) for value in item["region"])
                for item in items
            ):
                raise LegacyReviewError("빈칸 필드 ID와 화면에서 선택한 페이지·영역이 필요합니다.")
            payload = {"input_sha256": _sha(state.get("input_sha256")), "placements": items}
        else:
            if any(
                set(item) != {"anchor_id", "old_text", "new_text"}
                or any(not isinstance(value, str) for value in item.values())
                for item in items
            ):
                raise LegacyReviewError("수정 항목에는 위치 ID와 기존·새 문구만 입력해주세요.")
            payload = {
                "workspace_id": self._workspace_id,
                "baseline_hwpx_sha256": manifest["baseline_hwpx_sha256"],
                "edits": items,
            }
        return self._request(f"/api/{kind}-proposal", payload=payload, revision=revision)

    def save_comments(self, state, comments: list) -> dict:
        self._manifest(state)
        if not isinstance(comments, list) or any(not isinstance(item, dict) for item in comments):
            raise LegacyReviewError("코멘트 목록이 필요합니다.")
        return self._request(
            "/api/comments",
            payload={"workspace_id": self._workspace_id, "comments": comments},
            revision=_sha(state.get("comments_revision")),
        )

    def _plan(self, kind, state):
        self._kind(kind)
        manifest = self._manifest(state, kind)
        plan = state.get("proposal")
        sha = _sha(state.get("proposal_sha256"))
        if (
            not isinstance(plan, dict)
            or type(plan.get("schema")) is not int
            or plan["schema"] != _SCHEMAS[kind]
            or plan.get("workspace_id") != self._workspace_id
            or plan.get("baseline_hwpx_sha256") != manifest["baseline_hwpx_sha256"]
            or r.plan_hash(plan) != sha
        ):
            raise LegacyReviewError("현재 화면의 유효한 수정안을 다시 읽어주세요.")
        return manifest, plan, sha

    def approve(self, kind, state, *, reviewed: list[str], confirmed: bool) -> dict:
        if confirmed is not True:
            raise LegacyReviewError("정확한 수정 범위를 직접 확인한 뒤 승인해주세요.")
        _, plan, sha = self._plan(kind, state)
        items = plan.get("placements" if kind == "fill" else "edits")
        key = "field_id" if kind == "fill" else "anchor_id"
        if (
            not isinstance(items, list) or not items
            or any(not isinstance(item, dict) or not isinstance(item.get(key), str) for item in items)
            or not isinstance(reviewed, list)
            or any(not isinstance(item, str) for item in reviewed)
            or reviewed != [item[key] for item in items]
        ):
            raise LegacyReviewError("화면의 모든 수정 위치를 표시 순서대로 확인해주세요.")
        payload = {"plan_sha256": sha}
        if kind == "layout":
            payload.update(proposal_kind="layout", native_policy_confirmed=True,
                           row_minima_confirmed=True, regions_reviewed=reviewed)
        elif kind == "fill":
            payload.update(proposal_kind="fill", fill_policy_confirmed=True,
                           unsigned_template_confirmed=True, header_spine_confirmed=True,
                           fields_reviewed=reviewed)
        return self._request("/api/approve", payload=payload)

    def apply(self, kind, state, *, confirmed: bool) -> dict:
        if confirmed is not True:
            raise LegacyReviewError("승인과 별도로 적용 범위를 직접 확인해주세요.")
        manifest, plan, sha = self._plan(kind, state)
        if state.get("approved_sha256") != sha:
            raise LegacyReviewError("현재 수정안과 정확히 일치하는 승인이 필요합니다.")
        payload = {
            "workspace_id": self._workspace_id,
            "baseline_hwpx_sha256": manifest["baseline_hwpx_sha256"],
            "plan_sha256": sha,
        }
        if kind == "text":
            path = "/api/apply-text"
            payload["text_apply_confirmed"] = True
        else:
            path = "/api/apply-contract"
            scope = _sha(state.get("approval_scope_sha256"))
            try:
                exact_scope = r.plan_hash(r.native_approval_scope(plan))
            except (KeyError, TypeError, ValueError):
                raise LegacyReviewError("승인 범위를 다시 읽어주세요.") from None
            if exact_scope != scope:
                raise LegacyReviewError("승인 범위가 현재 수정안과 일치하지 않습니다.")
            payload.update(proposal_kind=kind, approval_scope_sha256=scope, contract_apply_confirmed=True)
        return self._request(path, payload=payload, revision=_sha(state.get("proposal_revision")))

    def export(self, state, result_id, kind, destination: Path) -> dict:
        manifest = self._manifest(state)
        result_id = _result_id(result_id)
        if not isinstance(kind, str) or kind not in _DOWNLOADS:
            raise LegacyReviewError("HWP 또는 PDF 결과만 저장할 수 있습니다.")
        versions = state.get("versions")
        matches = [v for v in versions if isinstance(v, dict) and v.get("id") == result_id] if isinstance(versions, list) else []
        if (len(matches) != 1 or matches[0].get("artifact_integrity") != "verified"
            or matches[0].get("status") != "checks_passed_needs_human_visual_review"):
            raise LegacyReviewError("현재 목록에 있는 검증된 결과만 저장할 수 있습니다.")
        downloads = matches[0].get("downloads")
        info = downloads.get(kind) if isinstance(downloads, dict) else None
        size = info.get("bytes") if isinstance(info, dict) else None
        if type(size) is not int or not 0 < size <= r.DOWNLOAD_LIMIT:
            raise LegacyReviewError("저장할 수 있는 결과 파일이 없습니다.")
        sha = _sha(info.get("sha256"))
        try:
            source = manifest.get("source")
            if not isinstance(source, str) or not Path(source).is_absolute():
                raise LegacyReviewError("원본 보호 경로를 확인할 수 없습니다.")
            source = _check_path(Path(source)).resolve(strict=True)
            destination = _check_path(Path(destination), new=True)
            # Canonicalize the existing parent to catch aliases such as Windows 8.3 names.
            destination = destination.parent.resolve(strict=True) / destination.name
            protected = (self._workspace, r.ROOT / "work", r.ROOT / "example")
            if (
                destination.suffix.lower() != f".{kind}"
                or destination == source
                or any(destination.is_relative_to(root.resolve()) for root in protected)
            ):
                raise LegacyReviewError("원본·작업 영역 밖의 새 결과 파일을 선택해주세요.")
            mime, signature = _DOWNLOADS[kind]
            data, wire_sha = self._request(f"/result/{result_id}/{kind}", mime=mime, limit=size)
            if len(data) != size or wire_sha != sha or not data.startswith(signature) or r.digest(data) != sha:
                raise LegacyReviewError("받은 결과의 형식, 크기 또는 해시가 일치하지 않습니다.")
            # Recheck after the network read; exclusive creation is the final overwrite guard.
            _check_path(destination, new=True)
            with destination.open("xb") as stream:
                stream.write(data)
        except (OSError, TypeError):
            raise LegacyReviewError("결과를 새 파일로 저장하지 못했습니다. 경로를 확인하고 일부 파일이 남았는지 확인해주세요.") from None
        return {"path": str(destination), "sha256": sha, "bytes": size}
