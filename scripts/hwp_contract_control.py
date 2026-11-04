"""Separate exact Native-contract apply consent; default DISABLED, not approval.

One guarded non-daemon job. Peer controllers must share the same RLock so starts
cannot race before the engine acquires the disk lease. No automatic repeat/reset.
"""

import importlib
import threading
import time

from hwp_text_apply import real_path
from hwp_security import registered_security_module
from hwp_fixed_layout import require_native_profile
from hwp_native_cells import minima_profile, plain_profile

KINDS = {"native": 2, "layout": 3, "fill": 4}
SUPPORTED_KINDS = ("layout", "fill")


class NativeApplyController:
    def __init__(self, workspace, enabled=False, guard=None, peers=()):
        self.workspace = real_path(workspace).resolve(strict=True)
        self.enabled = enabled
        self.guard = guard if guard is not None else threading.RLock()
        self.peers = peers
        self.thread = None
        self.started = None
        self.plan_sha256 = None
        self.kind = None
        self.status = "idle"
        self.error = ""

    def snapshot(self):
        with self.guard:
            active = self.thread is not None and self.thread.is_alive()
            available = False
            reason = "이 서버에서는 Native 계약 적용이 활성화되지 않았습니다."
            if self.enabled:
                try:
                    require_native_profile(self.workspace / "baseline.hwpx")
                except Exception as error:
                    reason = (
                        "이 양식의 읽기 순서 보존이 검증되지 않아 Native 표 적용을 사용할 수 없습니다. 읽기 전용 검토는 가능합니다. "
                        + str(error)[:500]
                    )
                else:
                    try:
                        registered_security_module()
                        available = True
                        reason = "원래 표 행 높이 또는 단순 무날인 빈칸 profile만 정확한 승인·별도 확인 뒤 적용합니다. 선택 profile은 시작 전에 재검사하며 새 후보의 실제 검증·사람 시각 검토·제출 승인을 대신하지 않습니다."
                    except Exception as error:
                        reason = "한글/등록된 모듈 확인 필요: " + str(error)[:500]
            return {
                "enabled": self.enabled,
                "available": available,
                "supported_kinds": list(SUPPORTED_KINDS),
                "active": active,
                "status": self.status,
                "plan_sha256": self.plan_sha256,
                "proposal_kind": self.kind,
                "error": self.error[:1000],
                "elapsed_seconds": int(time.monotonic() - self.started)
                if self.started is not None and active
                else None,
                "reason": reason,
            }

    def start(self, payload, revision):
        import hwp_review as r

        n = importlib.import_module("hwp_contract_apply")
        with self.guard:
            if not self.enabled:
                raise ValueError("Native contract apply disabled")
            self.require_idle()
            for peer in self.peers:
                peer.require_idle()
            if (
                isinstance(payload, dict)
                and payload.get("plan_sha256") == self.plan_sha256
                and self.status != "idle"
            ):
                raise ValueError(
                    "This Native contract already started/failed; no repeat"
                )
            kind = payload.get("proposal_kind") if isinstance(payload, dict) else None
            if not isinstance(kind, str) or kind not in KINDS:
                raise ValueError("Unknown Native contract kind")
            if kind not in SUPPORTED_KINDS:
                raise ValueError(
                    "This Native contract remains unqualified under existing preservation rules; no apply/copy/retry"
                )
            manifest = r.verify_baseline(self.workspace)
            r.require_apply_unlocked(self.workspace)
            r.validate_comments(r.read_json(self.workspace / "comments.json"), manifest)
            path = self.workspace / n.PROPOSALS[KINDS[kind]]
            plan = n.read_plan(path)
            n.require_qualified(plan)
            if plan["schema"] != KINDS[kind]:
                raise ValueError("Native proposal kind/schema mismatch")
            require_native_profile(self.workspace / "baseline.hwpx")
            if plan["schema"] == 3:
                minima_profile(self.workspace / "baseline.hwpx", plan, manifest)
            else:
                selected = r.hwp_fill_plan.validate(
                    plan, manifest, self.workspace / "baseline.hwpx"
                )
                plain_profile(self.workspace / "baseline.hwpx", selected)
            expected = {
                "workspace_id": manifest["workspace_id"],
                "baseline_hwpx_sha256": manifest["baseline_hwpx_sha256"],
                "proposal_kind": kind,
                "plan_sha256": r.plan_hash(plan),
                "approval_scope_sha256": r.plan_hash(r.native_approval_scope(plan)),
                "contract_apply_confirmed": True,
            }
            if (
                payload != expected
                or payload.get("contract_apply_confirmed") is not True
            ):
                raise ValueError(
                    "Separate exact Native contract apply confirmation required"
                )
            if revision != '"' + r.file_hash(path) + '"':
                raise ValueError("Native contract revision changed; read fresh state")
            r.approved_plan(self.workspace, plan, manifest)
            if any(
                a["plan_sha256"] == expected["plan_sha256"]
                for a in r.review_attempts(self.workspace, manifest)
            ):
                raise ValueError(
                    "Native contract already has an apply record; inspect, never repeat"
                )
            registered_security_module()
            self.kind, self.plan_sha256, self.status, self.error = (
                kind,
                expected["plan_sha256"],
                "starting",
                "",
            )
            self.started = time.monotonic()
            self.thread = threading.Thread(
                target=self._run, name="HWP-approved-native-contract", daemon=False
            )
            try:
                self.thread.start()
            except Exception as error:
                self.status, self.error = "blocked", str(error)
                raise
            return {
                "accepted": True,
                "proposal_kind": kind,
                "plan_sha256": self.plan_sha256,
                "note": "적용 시작 요청입니다. 아직 검증 통과/완성본이 아닙니다.",
            }

    def _run(self):
        import hwp_review as r

        n = importlib.import_module("hwp_contract_apply")
        try:
            with self.guard:
                self.status = "running"
                kind = self.kind
                expected = self.plan_sha256
            if (
                not isinstance(kind, str)
                or kind not in KINDS
                or not isinstance(expected, str)
            ):
                raise ValueError("Owned Native job identity missing")
            exporter = n.NativeContractExporter(self.workspace, expected)
            r.apply(
                self.workspace,
                self.workspace / n.PROPOSALS[KINDS[kind]],
                contract_exporter=exporter,
                expected_plan_sha256=expected,
            )
            if exporter.phase != 2:
                raise RuntimeError("Incomplete fixed Native contract exports")
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
            if self.thread is not None and self.thread.is_alive():
                raise RuntimeError(
                    "한글 계약 적용이 진행 중입니다. worker/한글을 강제 종료하거나 잠금을 지우지 마세요."
                )
