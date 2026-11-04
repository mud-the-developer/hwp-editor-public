"""Exact user-selected read-only HWP snapshots. Never model/COM/write/approval calls."""

from datetime import datetime, timezone
import json
from pathlib import Path
import re

STATUSES = {
    "idle",
    "starting",
    "running",
    "finished",
    "blocked",
    "in_progress",
    "checks_passed_needs_human_visual_review",
}
STAGES = {
    "input_preparation",
    "native_edit_export",
    "structural_verification",
    "saved_hwp_reopen",
    "rendered_verification",
    "final_source_and_approval_check",
    "checks_passed",
    "result_integrity",
    "lock_finalization",
    "interrupted",
}
PROPOSALS = {
    "text": "proposal.json",
    "native": "proposal-native.json",
    "layout": "proposal-layout.json",
}
SELECTION_KEYS = {
    "workspace_id",
    "baseline_hwpx_sha256",
    "comments_revision",
    "proposal_revision",
    "proposal_kind",
    "page",
    "anchor_id",
    "include_memos",
    "view_mode",
    "prepare_context_confirmed",
}


def validate_artifact(snapshot, artifact):
    if not isinstance(artifact, dict):
        raise ValueError("Invalid Pi artifact")
    if artifact.get("kind") == "text_draft":
        selected = snapshot["review_context"]["selected_anchor"]
        if (
            set(artifact) != {"kind", "anchor_id", "old_text", "new_text"}
            or snapshot["review_context"]["proposal_kind"] != "text"
            or artifact.get("anchor_id") != selected["anchor_id"]
            or artifact.get("old_text") != selected["old_text"]
        ):
            raise ValueError("Pi draft escapes the exact selected strict-text scope")
        new = artifact.get("new_text")
        if (
            not isinstance(new, str)
            or not new.strip()
            or len(new) > 8000
            or new == selected["old_text"]
            or re.search(r"[\x00-\x1f\x7f\u0085\u2028\u2029]", new)
        ):
            raise ValueError("Pi draft must be changed, nonempty single-line text")
        return {
            "kind": "text_draft",
            "anchor_id": selected["anchor_id"],
            "old_text": selected["old_text"],
            "new_text": new,
        }
    if artifact.get("kind") == "approved_job_request":
        if (
            set(artifact) != {"kind", "job"}
            or not snapshot.get("approved_job")
            or artifact.get("job") != snapshot["approved_job"]
        ):
            raise ValueError("Pi request has no exact captured UI approval")
        return {"kind": "approved_job_request", "job": dict(snapshot["approved_job"])}
    raise ValueError("Unknown Pi artifact; no arbitrary actions")


class ReviewContext:
    def __init__(self, workspace, job_reader):
        self.workspace = Path(workspace).resolve(strict=True)
        self.job_reader = job_reader

    def _capture(self, selection):
        import hwp_review as r

        if (
            not isinstance(selection, dict)
            or set(selection) != SELECTION_KEYS
            or selection.get("prepare_context_confirmed") is not True
            or selection.get("view_mode") != "original"
            or type(selection.get("include_memos")) is not bool
        ):
            raise ValueError(
                "Confirm the exact original selection and memo sharing; no paths/commands"
            )
        kind = selection.get("proposal_kind")
        if not isinstance(kind, str) or kind not in PROPOSALS:
            raise ValueError("Unsupported selection contract")
        manifest = r.verify_baseline(self.workspace)
        if (
            selection.get("workspace_id") != manifest["workspace_id"]
            or selection.get("baseline_hwpx_sha256") != manifest["baseline_hwpx_sha256"]
        ):
            raise ValueError("Document selection changed; no model transmission")
        page = selection.get("page")
        anchor_id = selection.get("anchor_id")
        if (
            type(page) is not int
            or not 1 <= page <= len(manifest["pages"])
            or page in manifest.get("protected_pages", [])
            or manifest["pages"][page - 1].get("image_only")
            or not isinstance(anchor_id, str)
        ):
            raise ValueError(
                "Select a mapped unprotected original text paragraph; OCR is not supported"
            )
        for name in ("comments.json", PROPOSALS[kind]):
            path = self.workspace / name
            if path.is_symlink() or path.resolve().parent != self.workspace:
                raise ValueError("Unsafe context source")
        comments_path = self.workspace / "comments.json"
        proposal_path = self.workspace / PROPOSALS[kind]
        comments_blob = comments_path.read_bytes()
        comments = r.validate_comments(r.parse_json(comments_blob), manifest)[
            "comments"
        ]
        proposal_revision = (
            r.file_hash(proposal_path) if proposal_path.exists() else "absent"
        )
        if (
            selection.get("comments_revision") != r.digest(comments_blob)
            or selection.get("proposal_revision") != proposal_revision
        ):
            raise ValueError("Comments/proposal changed; preview again before sharing")
        matches = [
            item for item in manifest["anchors"] if item.get("anchor_id") == anchor_id
        ]
        raw, _ = r.index_hwpx(self.workspace / "baseline.hwpx")
        originals = [item for item in raw if item.get("anchor_id") == anchor_id]
        if len(matches) != 1 or len(originals) != 1:
            raise ValueError("Ambiguous/missing original anchor")
        anchor = matches[0]
        original = originals[0]
        text = anchor.get("old_text")
        if (
            anchor.get("page_hint") != page
            or anchor.get("simple") is not True
            or original.get("simple") is not True
            or not anchor.get("boxes")
            or text != original.get("old_text")
            or not isinstance(text, str)
            or not 0 < len(text) <= 8000
        ):
            raise ValueError(
                "Unsupported/oversized selection; no guessing or region expansion"
            )
        memos = (
            [
                {"id": item["id"], "comment": item["comment"]}
                for item in comments
                if item["anchor_id"] == anchor_id
            ]
            if selection["include_memos"]
            else []
        )
        if len(memos) > 10:
            raise ValueError(
                "Too many selected-anchor memos; share without memos or narrow the input"
            )
        review = {
            "workspace_id": manifest["workspace_id"],
            "baseline_hwpx_sha256": manifest["baseline_hwpx_sha256"],
            "proposal_kind": kind,
            "selected_anchor": {"anchor_id": anchor_id, "page": page, "old_text": text},
            "saved_memos": memos,
            "document_text_is_untrusted_data": True,
            "write_authority": "none",
        }
        bindings = {
            key: selection[key]
            for key in (
                "workspace_id",
                "baseline_hwpx_sha256",
                "comments_revision",
                "proposal_revision",
                "proposal_kind",
                "page",
                "anchor_id",
                "include_memos",
                "view_mode",
            )
        }
        return manifest, review, bindings

    def _approved_job(self, selection, manifest, jobs):
        import hwp_review as r

        kind = selection["proposal_kind"]
        if (
            kind not in ("text", "layout")
            or jobs.get("apply_state", {}).get("locked") is not False
            or any(
                jobs.get(key, {}).get("active") is True
                for key in ("text_apply", "contract_apply")
            )
        ):
            return None
        path = self.workspace / PROPOSALS[kind]
        approval = self.workspace / "approved-plan.json"
        if (
            not path.is_file()
            or approval.is_symlink()
            or approval.resolve().parent != self.workspace
        ):
            return None
        try:
            plan = r.read_json(path)
            if (
                not isinstance(plan, dict)
                or plan.get("schema") != ({"text": 1, "layout": 3}[kind])
            ):
                return None
            digest = r.approved_digest(self.workspace, plan, manifest)
            if digest is None:
                return None
            controller = jobs.get(
                "text_apply" if kind == "text" else "contract_apply", {}
            )
            if (
                controller.get("enabled") is not True
                or controller.get("available") is not True
                or kind == "layout"
                and kind not in controller.get("supported_kinds", [])
            ):
                return None
            if (
                controller.get("plan_sha256") == digest
                and controller.get("status") != "idle"
                or any(
                    row.get("plan_sha256") == digest for row in jobs.get("attempts", [])
                )
            ):
                return None
            return {
                "workspace_id": manifest["workspace_id"],
                "baseline_hwpx_sha256": manifest["baseline_hwpx_sha256"],
                "proposal_kind": kind,
                "proposal_revision": selection["proposal_revision"],
                "plan_sha256": digest,
                "approval_scope_sha256": r.plan_hash(r.native_approval_scope(plan))
                if kind == "layout"
                else None,
                "requires_separate_apply_confirmation": True,
            }
        except (OSError, ValueError, KeyError, TypeError):
            return None

    def capture(self, selection):
        manifest, review, bindings = self._capture(selection)
        jobs = self.job_reader(manifest)
        controllers = []
        for name in ("text_apply", "contract_apply"):
            job = jobs.get(name, {})
            controllers.append(
                {
                    "kind": name,
                    "active": job.get("active") is True,
                    "status": job.get("status")
                    if job.get("status") in STATUSES
                    else "unknown",
                    "plan_sha256": job.get("plan_sha256")
                    if isinstance(job.get("plan_sha256"), str)
                    and re.fullmatch("[a-f0-9]{64}", job["plan_sha256"])
                    else None,
                }
            )
        attempts = []
        for item in jobs.get("attempts", [])[:5]:
            attempts.append(
                {
                    "id": item.get("id")
                    if isinstance(item.get("id"), str)
                    and re.fullmatch(r"\d{8}T\d{6}-[a-f0-9]{8}", item["id"])
                    else None,
                    "status": item.get("status")
                    if item.get("status") in STATUSES
                    else "unknown",
                    "stage": item.get("stage")
                    if item.get("stage") in STAGES
                    else "unconfirmed",
                    "plan_sha256": item.get("plan_sha256")
                    if isinstance(item.get("plan_sha256"), str)
                    and re.fullmatch("[a-f0-9]{64}", item["plan_sha256"])
                    else None,
                    "artifact_integrity": item.get("artifact_integrity")
                    if item.get("artifact_integrity")
                    in ("verified", "unconfirmed", "unverified", "failed", "unbound")
                    else "unconfirmed",
                }
            )
        job_snapshot = {
            "captured_at": datetime.now(timezone.utc).isoformat(),
            "live": False,
            "controllers": controllers,
            "attempts": attempts,
            "apply_locked": jobs.get("apply_state", {}).get("locked") is not False,
            "request_can_execute": False,
        }
        snapshot = {
            "review_context": review,
            "job_snapshot": job_snapshot,
            "bindings": bindings,
            "approved_job": self._approved_job(selection, manifest, jobs),
        }
        if len(json.dumps(snapshot, ensure_ascii=False).encode("utf-8")) > 32768:
            raise ValueError(
                "Selected context exceeds sharing limit; no automatic truncation"
            )
        return snapshot

    def verify(self, selection, snapshot):
        _, review, bindings = self._capture(selection)
        if review != snapshot.get("review_context") or bindings != snapshot.get(
            "bindings"
        ):
            raise ValueError("Selected document context changed; no model transmission")
        # Job stages may advance; the explicitly previewed captured-at snapshot stays frozen.

    def review_artifact(self, selection, snapshot, artifact):
        import hwp_review as r

        self.verify(selection, snapshot)
        validated = validate_artifact(snapshot, artifact)
        manifest = r.verify_baseline(self.workspace)
        jobs = self.job_reader(manifest)
        if validated["kind"] == "text_draft":
            if jobs.get("apply_state", {}).get("locked") is not False or any(
                jobs.get(key, {}).get("active") is True
                for key in ("text_apply", "contract_apply")
            ):
                raise ValueError(
                    "Document work locked/active; Pi draft stays read-only"
                )
            import hwp_text_proposal as proposals

            edit = {
                key: validated[key] for key in ("anchor_id", "old_text", "new_text")
            }
            proposals.compile_text(
                {
                    "workspace_id": manifest["workspace_id"],
                    "baseline_hwpx_sha256": manifest["baseline_hwpx_sha256"],
                    "edits": [edit],
                },
                manifest,
                self.workspace / "baseline.hwpx",
            )
            return {
                "kind": "text_draft",
                "workspace_id": manifest["workspace_id"],
                "baseline_hwpx_sha256": manifest["baseline_hwpx_sha256"],
                "proposal_revision": selection["proposal_revision"],
                "edit": edit,
                "saved": False,
                "approved": False,
                "applied": False,
            }
        live = self._approved_job(selection, manifest, jobs)
        if live is None or live != validated["job"]:
            raise ValueError(
                "Exact approval/plan/scope/job availability changed; no apply"
            )
        return {
            "kind": "approved_job_request",
            "job": live,
            "applied": False,
            "requires_separate_apply_confirmation": True,
        }
