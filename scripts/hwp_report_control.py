"""Document-first UI orchestration. Models propose; the existing engine writes.

Permission/acceptance records are returned to the existing HTTP UI handler for
exclusive persistence. This controller never creates those authority records.
"""

from copy import deepcopy
import hashlib
from io import BytesIO
import json
import os
from pathlib import Path, PurePosixPath
import secrets
import threading

import hwp_agents
import hwp_pi_chat
import hwp_report_project as projects
import hwp_report_writer as writer


_TEXT_SUFFIXES = frozenset((
    ".txt .md .markdown .rst .log .csv .tsv .json .jsonl .ndjson .yaml .yml "
    ".toml .ini .cfg .conf .xml .html .htm .css .scss .less .js .jsx .mjs "
    ".cjs .ts .tsx .py .pyi .cs .csx .c .h .cc .cpp .cxx .hpp .java .kt "
    ".kts .go .rs .rb .php .swift .sh .bash .zsh .ps1 .psm1 .psd1 .bat "
    ".cmd .sql .r .lua .pl .ex .exs .vue .svelte .tex .bib .cmake .gradle "
    ".properties .editorconfig .gitignore .gitattributes"
    " .sass .fs .fsx .vb .dart .xaml .csproj .fsproj .vbproj .props .targets .sln .m .mm"
).split())
_HWPX_TEXT_NOTE = "HWPX 본문 텍스트 발췌입니다. 페이지·배치 검증이나 이미지/OCR 결과가 아닙니다.\n\n"


def _material_controls(value):
    return any(
        (ord(char) < 32 and char not in "\n\r\t") or 127 <= ord(char) <= 159
        or ord(char) in (0x2028, 0x2029)
        for char in value
    )


def _material_section_text(section):
    """Keep XML source order around nested paragraphs and inline separators."""
    import hwp_template_draft as draft

    lines, chunks = [], []
    separators = {draft.HP + name: value for name, value in (
        ("tab", "\t"), ("lineBreak", "\n"), ("nbSpace", "\u00a0"), ("fwSpace", "\u3000"),
    )}

    def flush():
        if chunks:
            value = "".join(chunks)
            if value.strip():
                lines.append(value)
            chunks.clear()

    def visit(node):
        if node.tag == draft.HP + "p":
            flush()
            for child in node:
                visit(child)
            flush()
        elif node.tag == draft.HP + "t":
            chunks.append(node.text or "")
            for child in node:
                if child.tag not in separators:
                    raise ValueError("지원하지 않는 인라인 텍스트 제어가 있어 자료를 추출하지 않습니다.")
                chunks.append(separators[child.tag])
                chunks.append(child.tail or "")
        elif node.tag in separators:
            chunks.append(separators[node.tag])
        elif node.tag == draft.HP + "autoNum":
            value = node.get("num")
            if value is None:
                raise ValueError("본문 자동 번호를 확인할 수 없습니다.")
            chunks.append(value)
        else:
            for child in node:
                visit(child)

    visit(section)
    flush()
    return lines


def _material_text(raw, suffix):
    """Read only bounded package text; never render, execute or follow references."""
    if suffix == ".hwpx":
        import hwp_review as review
        import hwp_template_draft as draft

        parts = review.archive(BytesIO(raw))
        if parts.get("mimetype") != b"application/hwp+zip":
            raise ValueError("HWPX 자료의 패키지 형식을 확인하세요.")
        if any("signat" in name.lower() or "encrypt" in name.lower() for name in parts):
            raise ValueError("서명 또는 암호화된 HWPX 자료는 지원하지 않습니다.")
        package = review.xml_root(parts["Contents/content.hpf"])
        manifest, spine = package.find(draft.OPF + "manifest"), package.find(draft.OPF + "spine")
        if manifest is None or spine is None:
            raise ValueError("HWPX 자료의 본문 순서를 확인할 수 없습니다.")
        items = {}
        for item in manifest:
            identifier, href = item.get("id"), item.get("href", "")
            if not identifier or identifier in items:
                raise ValueError("HWPX 자료의 본문 항목이 중복되었거나 누락되었습니다.")
            # Both package-root and content.hpf-relative section names occur.
            relative = PurePosixPath(href)
            items[identifier] = href if href in parts else str(PurePosixPath("Contents") / relative)
        sections = {name for name in parts if review.SECTIONS.fullmatch(name)}
        ordered, seen = [], set()
        for item in spine:
            name = items.get(item.get("idref"))
            if name in seen:
                raise ValueError("HWPX 자료의 본문 순서 항목이 중복되었습니다.")
            seen.add(name)
            if name == "Contents/header.xml" and name in parts:
                continue
            if name not in sections or name in ordered:
                raise ValueError("HWPX 자료의 본문 순서가 지원 범위를 벗어났습니다.")
            ordered.append(name)
        if not ordered or set(ordered) != sections:
            raise ValueError("HWPX 자료의 모든 본문 구역을 확인할 수 없습니다.")
        paragraphs = []
        for name in ordered:
            section = review.xml_root(parts[name])
            if any(_material_controls(node.text or "") or _material_controls(node.tail or "")
                   for node in section.iter()):
                raise ValueError("제어 문자가 포함된 HWPX 본문 자료는 사용할 수 없습니다.")
            paragraphs.extend(_material_section_text(section))
        value = "\n".join(paragraphs)
        if not value.strip():
            raise ValueError("HWPX 자료에 추출할 본문 텍스트가 없습니다.")
        value = _HWPX_TEXT_NOTE + value
        kind = "hwpx_text"
    else:
        if suffix and suffix not in _TEXT_SUFFIXES:
            raise ValueError("UTF-8 텍스트·코드 또는 HWPX 본문 자료만 지원합니다. PDF·HWP·이미지·OCR은 지원하지 않습니다.")
        try:
            value = raw.decode("utf-8-sig")
        except UnicodeError as error:
            raise ValueError("자료는 UTF-8 텍스트·코드 파일이어야 합니다.") from error
        kind = "utf8_text"
    value = value.replace("\r\n", "\n").replace("\r", "\n")
    if not value.strip() or _material_controls(value):
        raise ValueError("비어 있거나 제어 문자가 포함된 자료는 사용할 수 없습니다.")
    staged = value.encode("utf-8")
    if len(staged) > 65536:
        raise ValueError("자료의 본문 텍스트는 64 KiB 이하여야 합니다. 자동으로 자르지 않습니다.")
    return staged, kind


def _hash(value):
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")).hexdigest()


def _file_hash(path):
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def _closed(payload, keys):
    if not isinstance(payload, dict) or set(payload) != set(keys):
        raise ValueError("요청 항목이 현재 작업과 맞지 않습니다.")


class ReportControl:
    def __init__(self, root):
        self.root = Path(root).resolve(strict=True)
        info = projects.inspect(self.root)
        if not info.document_first or not info.gate_passed:
            raise ValueError("문서 중심 보고서 작업을 확인할 수 없습니다: " + "; ".join(info.issues))
        self.guard = threading.RLock()
        self.discovery = hwp_agents.Discovery()
        self.chat = hwp_pi_chat.Chat(self.discovery)
        self.thread = None
        self.cancel = threading.Event()
        self.closed = False
        self.pending = None
        self.pending_key = None
        self.omp_pending = None
        self.selection_ids = list(info.requests[-1].get("selection_ids", [])) if info.requests else []
        self.materials = {}
        self.active_request = None
        self.error = ""
        self.progress = None

    def require_idle(self):
        if self.thread is not None and self.thread.is_alive():
            raise ValueError("초안 작업이 진행 중입니다. 완료 상태를 확인한 뒤 닫아주세요. 다시 요청하거나 강제 종료하지 않습니다.")
        if self.chat.thread is not None and self.chat.thread.is_alive():
            raise ValueError("AI 연결 확인이 진행 중입니다. 완료 후 닫아주세요.")

    def close(self):
        with self.guard:
            self.require_idle()
            self.closed = True
            self.chat.close()
            self.discovery.close()

    def _idle(self):
        if self.closed:
            raise ValueError("이 보고서 작업은 닫혔습니다.")
        self.require_idle()

    def _model(self):
        state = self.chat.snapshot()
        discovery = self.discovery.snapshot()
        return {
            "status": state["status"],
            "selection": state["model"],
            "chat_id": state["chat_id"],
            "runtime_sha256": self.chat.runtime["sha256"] if self.chat.runtime else None,
            "discovery_status": discovery["status"],
            "operation_active": self.chat.thread is not None and self.chat.thread.is_alive(),
            "error": state.get("stage") or discovery.get("error") or "",
        }

    def _version(self, item, index, accepted):
        identifier = item.get("version_id", item.get("id"))
        if not isinstance(identifier, str):
            raise ValueError("수정본 식별자를 확인할 수 없습니다.")
        path = Path(item["path"])
        return {
            "id": identifier,
            "request_id": item.get("request_id"),
            "number": index + 1,
            "label": item.get("label") or "수정본 " + str(index + 1),
            "filename": path.name,
            "sha256": item["sha256"],
            "accepted": identifier in accepted or item.get("accepted") is True,
            "external": item.get("kind") == "external",
            "summary": list(item.get("summary") or []),
            "comparison": item.get("comparison") or [],
            "review_copy_available": self._review_copy(item) is not None,
        }

    def state(self):
        with self.guard:
            info = projects.inspect(self.root)
            accepted = set(info.accepted)
            versions = []
            issues = list(info.issues)
            for index, item in enumerate(info.versions):
                try:
                    versions.append(self._version(item, index, accepted))
                except (OSError, ValueError, KeyError) as error:
                    issues.append(str(error))
            model = self._model()
            active = self.thread is not None and self.thread.is_alive()
            snapshot = {
                "name": info.name,
                "source_name": "원본 · " + info.name,
                "source_sha256": info.source_sha256,
                "phase": info.phase,
                "gate_passed": info.gate_passed and not issues,
                "issues": issues,
                "sections": projects.sections(self.root) if info.gate_passed else [],
                "selection_ids": list(self.selection_ids),
                "versions": versions,
                "accepted": list(info.accepted),
                "requests": list(info.requests),
                "active": active,
                "active_request": self.active_request,
                "progress": deepcopy(self.progress),
                "model": model,
                "error": self.error,
            }
            # Partial model text and elapsed time do not invalidate an otherwise
            # identical human action. Actual artifacts/authority/model do.
            snapshot["revision"] = _hash({
                key: value for key, value in snapshot.items()
                if key not in ("progress", "sections")
            })
            snapshot["details"] = {
                "workflow": "unapproved draft first, exact artifact acceptance later",
                "phase": info.phase,
                "requests": list(info.requests),
                "model": model,
                "native_or_submission_approval": False,
                "original_overwrite": False,
            }
            return snapshot

    def discover(self, payload):
        _closed(payload, ())
        with self.guard:
            self._idle()
            return self.discovery.start()

    def prepare_model(self, payload):
        if payload != {"prepare_confirmed": True} or payload.get("prepare_confirmed") is not True:
            raise ValueError("기존 Pi 프로그램 확인을 직접 요청해 주세요.")
        with self.guard:
            self._idle()
            return self.chat.prepare()

    def connect_model(self, payload):
        with self.guard:
            self._idle()
            return self.chat.connect(payload)

    def upload(self, raw, name, *, external=False):
        with self.guard:
            self._idle()
            name = str(name)
            suffix = Path(name).suffix.lower()
            maximum = 64 * 1024 * 1024 if external or suffix == ".hwpx" else 512 * 1024
            if not isinstance(raw, bytes) or not 0 < len(raw) <= maximum:
                raise ValueError("선택한 파일의 크기가 허용 범위를 넘었습니다.")
            if (not 1 <= len(name) <= 200 or Path(name).name != name
                    or "/" in name or "\\" in name
                    or any(ord(char) < 32 or 127 <= ord(char) <= 159 for char in name)):
                raise ValueError("파일 이름을 확인하세요.")
            source_sha256 = hashlib.sha256(raw).hexdigest()
            if external:
                if Path(name).suffix.lower() != ".hwpx":
                    raise ValueError("직접 수정한 HWPX 파일을 선택하세요.")
                from hwp_template_draft import parts_from

                parts_from(raw)
                source_kind = "hwpx_document"
            else:
                raw, source_kind = _material_text(raw, suffix)
            inbox = self.root / "inbox"
            if inbox.is_symlink() or (hasattr(inbox, "is_junction") and inbox.is_junction()):
                raise ValueError("자료 저장 위치에 연결 경로를 사용할 수 없습니다.")
            inbox.mkdir(exist_ok=True)
            identifier = "m-" + secrets.token_hex(16)
            target = inbox / (identifier + (".hwpx" if external else ".txt"))
            with target.open("xb") as stream:
                stream.write(raw)
            record = {"id": identifier, "name": name, "path": str(target),
                      "sha256": _file_hash(target), "source_kind": source_kind,
                      "source_sha256": source_sha256}
            self.materials[identifier] = record
            self.pending = self.pending_key = None
            self.omp_pending = None
            result = {key: value for key, value in record.items() if key != "path"}
            if not external:
                result["text"] = raw.decode("utf-8")
            return result

    def _material_paths(self, payload):
        """One selection/evidence convention for SDK and native proposals."""
        _closed(payload, ("instruction", "selection_ids", "materials", "previous_version"))
        declared = payload["materials"]
        if not isinstance(declared, list) or len(declared) > 16:
            raise ValueError("직접 선택한 참고 자료만 사용할 수 있습니다.")
        ids, paths = [], []
        for declaration in declared:
            _closed(declaration, ("id", "own_achievement"))
            identifier = declaration["id"]
            if (not isinstance(identifier, str) or identifier not in self.materials
                    or identifier in ids or type(declaration["own_achievement"]) is not bool):
                raise ValueError("자료의 선택과 성과 근거 선언을 확인하세요.")
            ids.append(identifier)
            item = self.materials[identifier]
            path = Path(item["path"])
            for part in (path, *path.parents):
                if part.is_symlink() or (hasattr(part, "is_junction") and part.is_junction()):
                    raise ValueError("선택한 참고 자료에 연결 경로를 사용할 수 없습니다.")
            if not path.is_file() or path.stat().st_nlink != 1 or _file_hash(path) != item["sha256"]:
                raise ValueError("선택한 참고 자료가 변경되었습니다.")
            paths.append({"path": str(path), "name": item["name"],
                          "own_achievement": declaration["own_achievement"]})
        if not isinstance(payload["selection_ids"], list):
            raise ValueError("수정할 부분을 선택하세요.")
        return paths

    def _capture_core(self, payload):
        material_paths = self._material_paths(payload)
        return projects.preview_request(
            self.root, payload["instruction"], material_paths=material_paths,
            selection_ids=payload["selection_ids"], previous_version=payload["previous_version"],
        )

    def omp_context(self, payload):
        with self.guard:
            self._idle()
            if self.omp_pending is not None and self.omp_pending["input"] != payload:
                self.omp_pending = None
            core = self._capture_core(payload)
            request = core["request"]
            if self.omp_pending is None or self.omp_pending["core"] != core:
                self.omp_pending = {"core": core, "input": deepcopy(payload), "proposal": None}
            return {
                "request_id": request["request_id"], "context_sha256": request["context_sha256"],
                "preview_sha256": core["preview_sha256"], "shared_context": writer._context(deepcopy(request)),
                "prompt": writer.omp_prompt(request),
            }

    def _omp_fresh(self):
        if self.omp_pending is None:
            raise ValueError("현재 요청과 자료를 먼저 확인하세요.")
        pending = self.omp_pending
        if self._capture_core(pending["input"]) != pending["core"]:
            self.omp_pending = None
            raise ValueError("요청·원본·자료·수정 범위가 바뀌었습니다. 다시 확인하세요.")
        return pending

    def omp_proposal(self, payload):
        _closed(payload, ("request_id", "context_sha256", "proposal_text"))
        with self.guard:
            self._idle()
            pending = self._omp_fresh()
            request = pending["core"]["request"]
            if any(payload[key] != request[key] for key in ("request_id", "context_sha256")):
                raise ValueError("작성 결과가 현재 공유 요청과 일치하지 않습니다.")
            proposal = writer.parse_proposal(request, payload["proposal_text"])
            preview = _hash({"core": pending["core"], "proposal": proposal})
            pending.update(proposal=proposal, preview_sha256=preview)
            return {
                "request_id": request["request_id"], "preview_sha256": preview,
                "proposal_sha256": _hash(proposal), "summary": deepcopy(proposal["summary"]),
                "jobs": deepcopy(proposal["jobs"]),
            }

    def omp_task_record(self, payload):
        _closed(payload, ("preview_sha256", "draft_only_confirmed"))
        with self.guard:
            self._idle()
            pending = self._omp_fresh()
            if (pending["proposal"] is None or payload["draft_only_confirmed"] is not True
                    or payload["preview_sha256"] != _hash({"core": pending["core"], "proposal": pending["proposal"]})):
                raise ValueError("현재 제안으로 새 미승인 HWPX 초안을 만드는 범위만 직접 동의해 주세요.")
            writer.validate_proposal(pending["core"]["request"], pending["proposal"])
            path, record = projects.request_record(self.root, pending["core"])
            if ((path.parent / "failed.json").exists()
                    or ((path.parent / "execution.json").exists() and not (path.parent / "result.json").exists())):
                raise ValueError("이 요청의 최초 실패·진행 중 기록을 보존합니다. 다시 생성하지 않습니다.")
            return path, record

    def _record_generation_error(self, request_id, error):
        message = type(error).__name__ + ": " + str(error)
        try:
            projects.fail_request(self.root, request_id, message)
        except Exception as preservation_error:
            message += " / 실패 기록 확인 필요: " + str(preservation_error)
        with self.guard:
            self.error = message[:6000]

    def omp_start(self, payload):
        with self.guard:
            self._idle()
            path, record = self.omp_task_record(payload)
            if (not path.is_file() or path.is_symlink() or path.stat().st_nlink != 1
                    or path.stat().st_size > 1024 * 1024
                    or projects.review.parse_json(path.read_bytes()) != record):
                raise ValueError("기존 UI가 저장한 현재 초안 권한이 필요합니다.")
            pending = deepcopy(self.omp_pending)
            request_id = pending["core"]["request"]["request_id"]
            self.selection_ids = list(pending["core"]["binding"]["selection_ids"])
            self.active_request, self.error = request_id, ""
            self.progress = {"title": "HWPX 생성과 자동 검사를 진행합니다", "description": "가져온 제안은 모델 호출 없이 기존 문서 엔진으로만 처리합니다."}

            def operation():
                try:
                    projects.generate(self.root, request_id, pending["proposal"])
                except Exception as error:
                    self._record_generation_error(request_id, error)
                finally:
                    with self.guard:
                        self.progress = None

            self.thread = threading.Thread(target=operation, name="HWP-report-import", daemon=True)
            try:
                self.thread.start()
            except RuntimeError as error:
                projects.fail_request(self.root, request_id, str(error))
                self.progress = None
                self.error = str(error)
                raise
            return self.state()

    def preview(self, payload):
        _closed(payload, ("instruction", "selection_ids", "materials", "previous_version"))
        with self.guard:
            self._idle()
            if self.chat.status != "ready" or self.chat.runtime is None or self.chat.model is None:
                raise ValueError("먼저 기존 Pi 모델을 연결하세요. 연결만으로 자료를 전송하지 않습니다.")
            material_paths = self._material_paths(payload)
            key = _hash({"input": payload, "model": self.chat.model, "runtime": self.chat.runtime["sha256"]})
            if self.pending is None or key != self.pending_key:
                core = projects.preview_request(
                    self.root, payload["instruction"], material_paths=material_paths,
                    selection_ids=payload["selection_ids"], previous_version=payload["previous_version"],
                )
                model_preview = writer.preview(core["request"], self.chat.model, self.chat.runtime["sha256"])
                self.pending = {"core": core, "writer": model_preview, "input": deepcopy(payload)}
                self.pending_key = key
            return {
                "preview_sha256": self.pending["writer"]["preview_sha256"],
                "request_id": self.pending["core"]["request"]["request_id"],
                "shared_context": self.pending["writer"]["context"],
                "model": deepcopy(self.chat.model),
                "max_model_requests": self.pending["writer"]["max_model_requests"],
            }

    def task_record(self, payload):
        _closed(payload, ("preview_sha256", "context_confirmed", "cost_confirmed", "draft_only_confirmed"))
        with self.guard:
            self._idle()
            if self.pending is None:
                raise ValueError("현재 요청과 선택 자료를 먼저 확인하세요.")
            fresh = writer.preview(self.pending["core"]["request"], self.chat.model, self.chat.runtime["sha256"])
            if payload != {"preview_sha256": fresh["preview_sha256"], "context_confirmed": True, "cost_confirmed": True, "draft_only_confirmed": True} or any(payload[key] is not True for key in ("context_confirmed", "cost_confirmed", "draft_only_confirmed")):
                raise ValueError("현재 모델·자료 전송·사용료와 새 초안 생성 범위에 대한 명시적 동의가 필요합니다.")
            path, record = projects.request_record(self.root, self.pending["core"])
            return path, record

    def start(self, payload):
        with self.guard:
            self._idle()
            # Revalidates the actual UI-written record; never manufactures it.
            self.task_record(payload)
            pending = deepcopy(self.pending)
            request = pending["core"]["request"]
            self.selection_ids = list(pending["core"]["binding"]["selection_ids"])
            begun = projects.begin_request(self.root, request["request_id"])
            if not begun["may_call_model"]:
                return self.state()
            self.active_request = request["request_id"]
            self.error = ""
            self.cancel = threading.Event()
            self.progress = {"title": "AI가 초안을 작성하고 있습니다", "description": "선택한 본문과 자료만 전달합니다. 원본은 변경하지 않습니다."}
            runtime, model = deepcopy(self.chat.runtime), deepcopy(self.chat.model)

            def progress(data):
                with self.guard:
                    self.progress = {
                        "title": "AI가 초안을 작성하고 있습니다",
                        "description": "요청한 범위의 응답을 확인하고 있습니다.",
                        "stage": data.get("stage"),
                        "usage": data.get("usage"),
                    }

            def operation():
                try:
                    result = writer.generate(runtime, model, request, payload, self.cancel, on_progress=progress)
                    with self.guard:
                        self.progress = {"title": "HWPX 생성과 자동 검사를 진행합니다", "description": "원본과 범위 밖 서식·자원을 보존하는지 확인합니다."}
                    projects.generate(self.root, request["request_id"], result["proposal"])
                except Exception as error:
                    self._record_generation_error(request["request_id"], error)
                finally:
                    with self.guard:
                        self.progress = None

            self.thread = threading.Thread(target=operation, name="HWP-report-request", daemon=True)
            try:
                self.thread.start()
            except RuntimeError as error:
                projects.fail_request(self.root, request["request_id"], str(error))
                self.error = str(error)
                raise
            return self.state()

    def source(self):
        with self.guard:
            return projects.source(self.root)

    def memo_target(self, payload):
        _closed(payload, ("previous_version", "sha256", "section_index", "paragraph_index"))
        with self.guard:
            self._idle()
            return projects.memo_target(
                self.root, payload["previous_version"], payload["sha256"],
                payload["section_index"], payload["paragraph_index"],
            )

    def candidate(self, identifier, expected_sha256=None):
        with self.guard:
            artifact = projects.candidate(self.root, identifier)
            if expected_sha256 is not None and artifact["sha256"] != expected_sha256:
                raise ValueError("수정본이 달라졌습니다. 현재 파일을 다시 확인하세요.")
            path = Path(artifact["path"])
            if path.suffix.lower() != ".hwpx" or not path.is_relative_to(self.root):
                raise ValueError("이 작업의 검증된 HWPX만 열 수 있습니다.")
            return artifact

    def _review_copy(self, artifact, *, create=False):
        """Hand off an editable copy; canonical version bytes remain immutable."""
        directory = self.root / "review-copies" / artifact["version_id"]
        path, manifest = directory / "review.hwpx", directory / "review-copy.json"
        for entry in (path, manifest, *path.parents):
            if entry.is_symlink() or (hasattr(entry, "is_junction") and entry.is_junction()):
                raise ValueError("검토용 사본에 연결 경로를 사용할 수 없습니다.")
        expected = {
            "contract": "hwp-report-review-copy-v1",
            "version_id": artifact["version_id"],
            "initial_sha256": artifact["sha256"],
        }
        if manifest.exists():
            if (
                not manifest.is_file() or manifest.stat().st_nlink != 1
                or manifest.stat().st_size > 4096
                or json.loads(manifest.read_text(encoding="utf-8")) != expected
                or not path.is_file() or path.stat().st_nlink != 1
                or path.stat().st_size > 64 * 1024 * 1024
            ):
                raise ValueError("기존 검토용 사본의 기록을 확인하세요. 덮어쓰거나 복구하지 않습니다.")
            return path
        if path.exists():
            raise ValueError("완료되지 않은 검토용 사본을 보존합니다. 자동으로 다시 복사하지 않습니다.")
        if not create:
            return None
        directory.mkdir(parents=True, exist_ok=True)
        copied = hashlib.sha256()
        with Path(artifact["path"]).open("rb") as source, path.open("xb") as target:
            for block in iter(lambda: source.read(1024 * 1024), b""):
                copied.update(block)
                target.write(block)
        if copied.hexdigest() != artifact["sha256"]:
            raise ValueError("검토용 사본 준비 중 기준 파일이 바뀌었습니다. 사본과 기록을 보존합니다.")
        with manifest.open("x", encoding="utf-8") as stream:
            json.dump(expected, stream, ensure_ascii=False, sort_keys=True)
        return path

    def open(self, payload):
        _closed(payload, ("version_id", "sha256"))
        with self.guard:
            artifact = self.candidate(payload["version_id"], payload["sha256"])
            if os.name != "nt":
                raise ValueError("HWPX 직접 열기는 Windows 연결 프로그램을 사용합니다. 파일 내려받기를 이용하세요.")
            editable = self._review_copy(artifact, create=True)
            os.startfile(str(editable))
            return {"open_requested": True, "version_id": payload["version_id"], "editable_review_copy": True}

    def acceptance_record(self, payload):
        _closed(payload, ("version_id", "sha256"))
        with self.guard:
            self._idle()
            artifact = self.candidate(payload["version_id"], payload["sha256"])
            editable = self._review_copy(artifact)
            if editable is not None and _file_hash(editable) != artifact["sha256"]:
                raise ValueError("한글에서 저장한 수정 내용이 있습니다. 새 수정본으로 가져와 검사한 뒤 선택하세요.")
            return projects.acceptance_record(self.root, payload["version_id"], payload["sha256"])

    def import_revision(self, identifier, raw, name):
        with self.guard:
            self._idle()
            self.candidate(identifier)
            uploaded = self.upload(raw, name, external=True)
            source = Path(self.materials[uploaded["id"]]["path"])
            imported = projects.import_revision(self.root, source, identifier)
            self.pending = self.pending_key = None
            self.omp_pending = None
            self.error = ""
            return {"version_id": imported["version_id"], "sha256": imported["sha256"]}

    def import_opened(self, payload):
        _closed(payload, ("version_id", "sha256"))
        with self.guard:
            self._idle()
            artifact = self.candidate(payload["version_id"], payload["sha256"])
            editable = self._review_copy(artifact)
            if editable is None:
                raise ValueError("이 수정본의 검토용 사본을 먼저 열어주세요.")
            imported = projects.import_revision(self.root, editable, payload["version_id"])
            self.pending = self.pending_key = None
            self.omp_pending = None
            self.error = ""
            return {"version_id": imported["version_id"], "sha256": imported["sha256"]}
