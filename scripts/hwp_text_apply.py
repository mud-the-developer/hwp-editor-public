"""Bounded schema-1 apply only. Exact UI approval + separate apply request required.

Fixed derived worker paths/phases, no general Native export/edit commands, install,
source Save, PID kill, automatic retry or failed-lease recovery. COM is child-only.
"""

from pathlib import Path
import os
import shutil
import subprocess
import sys
import threading
import time

from hwp_resources import frozen
from hwp_security import MODULE_ALIAS, MODULE_TYPE, registered_security_module
from hwp_native_owner import owned_session


def real_path(path):
    path = Path(path).absolute()
    if any(
        p.is_symlink() or (hasattr(p, "is_junction") and p.is_junction())
        for p in (path, *path.parents)
    ):
        raise ValueError("연결된 적용 경로는 지원하지 않습니다.")
    return path


def read_plan(path):
    import hwp_review as r

    path = real_path(path)
    if not path.is_file() or path.stat().st_size > 8 * 1024 * 1024:
        raise ValueError("적용 수정안 파일을 소유자가 확인하세요.")
    plan = r.read_json(path)
    if (
        not isinstance(plan, dict)
        or type(plan.get("schema")) is not int
        or plan["schema"] != 1
    ):
        raise ValueError(
            "앱 적용은 승인된 schema-1 글자 교체만 지원합니다. Native 범위를 확대하지 않습니다."
        )
    if any(
        not isinstance(e, dict)
        or not isinstance(e.get("new_text"), str)
        or not e["new_text"].strip()
        for e in plan.get("edits", [])
    ):
        raise ValueError("앱 적용은 빈 문구 삭제를 지원하지 않습니다.")
    return plan


def step_paths(workspace, run_id, phase):
    import hwp_review as r

    if (
        type(phase) is not int
        or phase not in (0, 1)
        or not isinstance(run_id, str)
        or not r.RUN_ID.fullmatch(run_id)
    ):
        raise ValueError("Unknown exact text apply phase/run")
    workspace = real_path(workspace).resolve(strict=True)
    run = real_path(workspace / "runs" / run_id).resolve(strict=True)
    if not run.is_dir() or run.parent != workspace / "runs":
        raise ValueError("Invalid owned text run")
    manifest = r.verify_baseline(workspace)
    plan = read_plan(run / "plan.json")
    current = read_plan(workspace / "proposal.json")
    if r.plan_hash(current) != r.plan_hash(plan):
        raise ValueError("Text proposal changed during apply")
    r.approved_plan(workspace, plan, manifest)
    request_path = real_path(run / f"text-export-request-{phase}.json")
    if not request_path.is_file() or request_path.stat().st_size > 8192:
        raise ValueError("Missing bounded text worker request")
    request = r.read_json(request_path)
    if (
        not isinstance(request, dict)
        or set(request) != {"schema", "phase", "lease", "input_sha256", "plan_sha256"}
        or type(request["schema"]) is not int
        or request["schema"] != 1
        or type(request["phase"]) is not int
        or request["phase"] != phase
        or request["plan_sha256"] != r.plan_hash(plan)
    ):
        raise ValueError("Text worker request does not match this phase/plan")
    lease = request["lease"]
    if (
        not isinstance(lease, dict)
        or lease.get("run_id") != run_id
        or lease.get("plan_sha256") != r.plan_hash(plan)
    ):
        raise ValueError("Text worker requires this exact owned lease")
    r.assert_apply_lock_owned(workspace, lease)
    if not r.apply_state(workspace, manifest).get("record_valid"):
        raise ValueError("Invalid text apply lease")
    source = real_path(run / ("candidate.hwpx" if phase == 0 else "candidate.hwp"))
    if not source.is_file() or r.file_hash(source) != request["input_sha256"]:
        raise ValueError("Text export input changed")
    # Both phases require the exact locked XML patch. HWP reopening additionally
    # uses immutable parent hashes and final pixel/text/binary gates in r.apply.
    r.structural_gate(
        workspace / "baseline.hwpx", run / "candidate.hwpx", plan["edits"]
    )
    outputs = (
        (run / "candidate-buffer.pdf", run / "candidate.hwp")
        if phase == 0
        else (run / "candidate.pdf", None)
    )
    reply = run / f"text-export-reply-{phase}.json"
    copied = run / (f"text-export-input-{phase}" + source.suffix)
    for path in (*outputs, reply, copied):
        if path is not None:
            real_path(path)
            if path.exists():
                raise FileExistsError(
                    "Text phase output exists; no overwrite/retry: " + str(path)
                )
    return workspace, run, source, outputs, reply, copied, request


class TextExporter:
    def __init__(self, workspace, expected_hash):
        self.workspace = real_path(workspace).resolve(strict=True)
        self.expected_hash, self.phase = expected_hash, 0

    def __call__(self, source, pdf, *, hwpx=None, hwp=None):
        import hwp_review as r

        if self.phase not in (0, 1) or hwpx is not None:
            raise ValueError("Only two fixed approved text exports are supported")
        run = real_path(source).parent
        if run.parent != self.workspace / "runs" or not r.RUN_ID.fullmatch(run.name):
            raise ValueError("Text export must own one run")
        lease = r.read_json(self.workspace / r.APPLY_LOCK)
        if (
            lease.get("pid") != os.getpid()
            or lease.get("plan_sha256") != self.expected_hash
        ):
            raise ValueError("Text exporter does not own this parent lease")
        expected_source = run / (
            "candidate.hwpx" if self.phase == 0 else "candidate.hwp"
        )
        outputs = (
            (run / "candidate-buffer.pdf", run / "candidate.hwp")
            if self.phase == 0
            else (run / "candidate.pdf", None)
        )
        if Path(source) != expected_source or (pdf, hwp) != outputs:
            raise ValueError("Text export paths differ from the exact phase")
        request = {
            "schema": 1,
            "phase": self.phase,
            "lease": lease,
            "input_sha256": r.file_hash(source),
            "plan_sha256": self.expected_hash,
        }
        with real_path(run / f"text-export-request-{self.phase}.json").open(
            "x", encoding="utf-8"
        ) as stream:
            import json

            json.dump(request, stream)
            stream.flush()
            os.fsync(stream.fileno())
        _, _, _, _, reply, _, _ = step_paths(self.workspace, run.name, self.phase)
        command = [sys.executable]
        if not frozen():
            command.append(str(Path(__file__).with_name("hwp_desktop.py")))
        command += [
            "--text-export",
            str(self.workspace),
            "--text-run",
            run.name,
            "--text-phase",
            str(self.phase),
        ]
        try:
            result = subprocess.run(
                command,
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                timeout=240,
                check=False,
            )
        except subprocess.TimeoutExpired as error:
            raise RuntimeError(
                "한글 적용 시간이 초과됐습니다. 소유자가 한글/접근 창을 확인하세요. 강제 종료·잠금 해제·자동 재시도하지 않습니다."
            ) from error
        real_path(reply)
        if not reply.is_file() or reply.stat().st_size > 65536:
            raise RuntimeError(
                "적용 worker 완료 응답이 없습니다. 잠금과 증거를 유지합니다."
            )
        data = r.read_json(reply)
        if result.returncode or data.get("ok") is not True:
            raise RuntimeError(
                "한글 글자 적용 차단: " + str(data.get("error", "unknown"))
            )
        if (
            data.get("phase") != self.phase
            or data.get("plan_sha256") != self.expected_hash
            or not isinstance(data.get("native"), dict)
        ):
            raise ValueError("Unbound text worker reply")
        if r.file_hash(source) != request["input_sha256"]:
            raise ValueError("Text export input changed during worker")
        r.assert_apply_lock_owned(self.workspace, lease)
        self.phase += 1
        return data["native"]


def export_phase(workspace, run_id, phase):
    import hwp_review as r

    workspace, run, source, outputs, _, copied, request = step_paths(
        workspace, run_id, phase
    )
    security = registered_security_module()  # READ ONLY; before COM/copy
    with source.open("rb") as original, copied.open("xb") as target:
        shutil.copyfileobj(original, target, length=1024 * 1024)
    if (
        r.file_hash(copied) != request["input_sha256"]
        or r.file_hash(source) != request["input_sha256"]
    ):
        raise ValueError("Native text input copy changed")
    r.approved_plan(
        workspace, read_plan(run / "plan.json"), r.verify_baseline(workspace)
    )
    r.assert_apply_lock_owned(workspace, request["lease"])
    with owned_session((copied, *outputs)) as app:
        if not app.RegisterModule(MODULE_TYPE, MODULE_ALIAS):
            raise RuntimeError("Approved security module rejected; no document opened")
        if not app.Open(
            str(copied.resolve()),
            "HWPX" if phase == 0 else "HWP",
            "versionwarning:false",
        ):
            raise RuntimeError(
                "한글이 승인된 적용 사본을 열지 못했습니다. 자동 재시도하지 않습니다."
            )
        pages = int(app.PageCount)
        r.approved_plan(
            workspace, read_plan(run / "plan.json"), r.verify_baseline(workspace)
        )
        r.assert_apply_lock_owned(workspace, request["lease"])
        # This worker NEVER edits in COM or saves an input. All paths are fresh.
        for path, format in ((outputs[1], "HWP"), (outputs[0], "PDF")):
            if path is None:
                continue
            real_path(path)
            if path.exists():
                raise FileExistsError("Text output exists; no overwrite")
            if (
                not app.SaveAs(str(path.resolve()), format, "")
                or not path.is_file()
                or not path.stat().st_size
            ):
                raise RuntimeError("한글 적용 출력 실패: " + format)
        return {
            "native_pages": pages,
            "hancom_version": str(app.Version),
            "security_module": {**security, "registered": True},
            "owned_session": app.ownership,
        }


def run_worker(workspace, run_id, phase):
    _, _, _, _, reply, _, request = step_paths(workspace, run_id, phase)
    try:
        data = {"ok": True, "native": export_phase(workspace, run_id, phase)}
        code = 0
    except Exception as error:
        data = {"ok": False, "error": type(error).__name__ + ": " + str(error)}
        code = 1
    data.update(phase=phase, plan_sha256=request["plan_sha256"])
    with reply.open("x", encoding="utf-8") as stream:
        import json

        json.dump(data, stream, ensure_ascii=False)
        stream.flush()
        os.fsync(stream.fileno())
    return code


class TextApplyController:
    """One non-daemon job per server. Disk lease also serializes other servers."""

    def __init__(self, workspace, enabled=False):
        self.workspace, self.enabled = (
            real_path(workspace).resolve(strict=True),
            enabled,
        )
        self.guard = threading.RLock()
        self.peers: tuple = ()
        self.thread = None
        self.started = None
        self.plan_sha256 = None
        self.status = "idle"
        self.error = ""

    def snapshot(self):
        with self.guard:
            active = self.thread is not None and self.thread.is_alive()
            available, reason = (
                False,
                "이 서버에서는 앱 글자 적용이 활성화되지 않았습니다. 별도 원본 도구를 사용하세요.",
            )
            if self.enabled:
                try:
                    registered_security_module()
                    available, reason = (
                        True,
                        "승인된 글자 교체만 적용합니다. 실제 문서 제출 승인과 다릅니다.",
                    )
                except Exception as error:
                    reason = "한글/등록된 보안 모듈 확인 필요: " + str(error)[:500]
            return {
                "enabled": self.enabled,
                "available": available,
                "active": active,
                "status": self.status,
                "plan_sha256": self.plan_sha256,
                "error": self.error[:1000],
                "elapsed_seconds": int(time.monotonic() - self.started)
                if self.started is not None and active
                else None,
                "reason": reason,
            }

    def start(self, payload, revision):
        import hwp_review as r

        with self.guard:
            if not self.enabled:
                raise ValueError("Bounded text apply is disabled in this server")
            for peer in self.peers:
                peer.require_idle()
            if self.thread is not None and self.thread.is_alive():
                raise ValueError("An owned text apply is already active")
            if (
                isinstance(payload, dict)
                and payload.get("plan_sha256") == self.plan_sha256
                and self.status != "idle"
            ):
                raise ValueError(
                    "This exact plan already has an owned start/failure; no automatic repeat"
                )
            manifest = r.verify_baseline(self.workspace)
            r.require_apply_unlocked(self.workspace)
            r.validate_comments(r.read_json(self.workspace / "comments.json"), manifest)
            plan = read_plan(self.workspace / "proposal.json")
            expected = {
                "workspace_id": manifest["workspace_id"],
                "baseline_hwpx_sha256": manifest["baseline_hwpx_sha256"],
                "plan_sha256": r.plan_hash(plan),
                "text_apply_confirmed": True,
            }
            if (
                not isinstance(payload, dict)
                or payload != expected
                or payload.get("text_apply_confirmed") is not True
            ):
                raise ValueError("Separate exact text apply confirmation is required")
            if revision != '"' + r.file_hash(self.workspace / "proposal.json") + '"':
                raise ValueError(
                    "Text apply proposal revision changed; read fresh state"
                )
            r.approved_plan(self.workspace, plan, manifest)
            if any(
                a["plan_sha256"] == expected["plan_sha256"]
                for a in r.review_attempts(self.workspace, manifest)
            ):
                raise ValueError(
                    "This exact plan already has an apply record; inspect it, never automatically repeat"
                )
            registered_security_module()
            self.plan_sha256, self.status, self.error = (
                expected["plan_sha256"],
                "starting",
                "",
            )
            self.started = time.monotonic()
            self.thread = threading.Thread(
                target=self._run, name="HWP-approved-text-apply", daemon=False
            )
            try:
                self.thread.start()
            except Exception as error:
                self.status, self.error = "blocked", str(error)
                raise
            return {
                "accepted": True,
                "plan_sha256": self.plan_sha256,
                "note": "적용 시작 요청을 받았습니다. 아직 검증 통과/완성본이 아닙니다.",
            }

    def _run(self):
        import hwp_review as r

        try:
            with self.guard:
                self.status = "running"
            exporter = TextExporter(self.workspace, self.plan_sha256)
            r.apply(
                self.workspace,
                self.workspace / "proposal.json",
                text_exporter=exporter,
                expected_plan_sha256=self.plan_sha256,
            )
            if exporter.phase != 2:
                raise RuntimeError("Incomplete bounded text exports")
            with self.guard:
                self.status = "finished"
        except BaseException as error:
            with self.guard:
                self.status, self.error = (
                    "blocked",
                    type(error).__name__ + ": " + str(error),
                )

    def require_idle(self):
        with self.guard:
            for peer in self.peers:
                peer.require_idle()
            if self.thread is not None and self.thread.is_alive():
                raise RuntimeError(
                    "이 앱의 한글 적용이 진행 중입니다. 완료 상태를 확인한 뒤 닫으세요. worker/한글을 강제로 종료하지 않습니다."
                )
