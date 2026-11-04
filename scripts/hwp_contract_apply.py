"""Fixed schema2/3/4 contract workers; NOT a generic Native CLI/edit API.

Exact UI policy/regions, separate parent apply and owned lease are required.
Inputs/outputs/phases derive from the immutable manifest and one exact plan.
Import creates NO COM; only explicit export_phase uses an owned instance.
"""

from pathlib import Path
from contextlib import AbstractContextManager, contextmanager
from collections.abc import Callable
from typing import Any
import os
import shutil
import subprocess
import sys

from hwp_resources import frozen
from hwp_security import MODULE_ALIAS, MODULE_TYPE, registered_security_module
from hwp_native_owner import NativeReference, _document, owned_session
from hwp_text_apply import real_path

_NativeReference = NativeReference  # Keep the code-owned source validation seam.
SAVED_EXPORT_COPY = "contract-saved-export-copy-0.hwp"

PROPOSALS = {
    2: "proposal-native.json",
    3: "proposal-layout.json",
    4: "proposal-fill.json",
}
# Strict Source-supported profiles; each new frozen candidate still needs Native evidence.
QUALIFIED_SCHEMAS = (3, 4)


def require_qualified(plan):
    if type(plan.get("schema")) is not int or plan["schema"] not in QUALIFIED_SCHEMAS:
        raise ValueError(
            "This Native contract has not passed existing preservation qualification; no copy/COM/retry"
        )


def read_plan(path):
    import hwp_review as r

    path = real_path(path)
    if not path.is_file() or path.stat().st_size > 8 * 1024 * 1024:
        raise ValueError("Missing/oversized exact Native contract plan")
    plan = r.read_json(path)
    if (
        not isinstance(plan, dict)
        or type(plan.get("schema")) is not int
        or plan["schema"] not in PROPOSALS
    ):
        raise ValueError(
            "Only the fixed Native paragraph/row-minima/unsigned-fill contracts are supported"
        )
    return plan


def contract_gate(workspace, candidate, plan, selected):
    import hwp_review as r

    if plan["schema"] == 4:
        return r.hwp_fill_plan.structural_gate(
            workspace / "baseline.hwpx", candidate, selected, plan["header_spine"]
        )
    gate = (
        r.hwp_fixed_layout.structural_gate
        if plan["schema"] == 3
        else r.hwp_native_plan.structural_gate
    )
    return gate(workspace / "baseline.hwpx", candidate, plan)


def step_paths(workspace, run_id, phase):
    import hwp_review as r

    if (
        type(phase) is not int
        or phase not in (0, 1)
        or not isinstance(run_id, str)
        or not r.RUN_ID.fullmatch(run_id)
    ):
        raise ValueError("Unknown owned Native contract phase/run")
    workspace = real_path(workspace).resolve(strict=True)
    run = real_path(workspace / "runs" / run_id).resolve(strict=True)
    if not run.is_dir() or run.parent != workspace / "runs":
        raise ValueError("Foreign Native contract run")
    manifest = r.verify_baseline(workspace)
    plan = read_plan(run / "plan.json")
    if r.plan_hash(read_plan(workspace / PROPOSALS[plan["schema"]])) != r.plan_hash(
        plan
    ):
        raise ValueError("Current Native contract changed during apply")
    selected = r.approved_plan(workspace, plan, manifest)
    if plan["schema"] == 3:
        r.hwp_fixed_layout.require_native_profile(workspace / "baseline.hwpx")
        import hwp_native_cells as cells

        cells.minima_profile(workspace / "baseline.hwpx", plan, manifest)
    elif plan["schema"] == 4:
        import hwp_native_cells as cells

        cells.plain_profile(workspace / "baseline.hwpx", selected)
    request_path = real_path(run / f"contract-export-request-{phase}.json")
    if not request_path.is_file() or request_path.stat().st_size > 8192:
        raise ValueError("Missing bounded Native worker request")
    request = r.read_json(request_path)
    keys = {
        "schema",
        "phase",
        "lease",
        "input_sha256",
        "plan_sha256",
        "approval_scope_sha256",
    }
    if (
        not isinstance(request, dict)
        or set(request) != keys
        or type(request["schema"]) is not int
        or request["schema"] != plan["schema"]
        or type(request["phase"]) is not int
        or request["phase"] != phase
        or request["plan_sha256"] != r.plan_hash(plan)
        or request["approval_scope_sha256"]
        != r.plan_hash(r.native_approval_scope(plan))
    ):
        raise ValueError("Native request policy/schema/phase/plan mismatch")
    lease = request["lease"]
    if (
        not isinstance(lease, dict)
        or lease.get("run_id") != run_id
        or lease.get("plan_sha256") != r.plan_hash(plan)
    ):
        raise ValueError("Exact owned Native contract lease required")
    r.assert_apply_lock_owned(workspace, lease)
    if not r.apply_state(workspace, manifest).get("record_valid"):
        raise ValueError("Invalid Native contract lease")
    source = real_path(
        Path(manifest["source"]) if phase == 0 else run / "candidate.hwp"
    )
    if not source.is_file() or r.file_hash(source) != request["input_sha256"]:
        raise ValueError("Native contract input changed")
    if phase == 0 and request["input_sha256"] != manifest["source_audit"]["sha256"]:
        raise ValueError("Native source differs from the captured original")
    if phase == 1:
        contract_gate(workspace, run / "candidate.hwpx", plan, selected)
    outputs = (
        (run / "candidate-buffer.pdf", run / "candidate.hwpx", run / "candidate.hwp")
        if phase == 0
        else (run / "candidate.pdf", run / "candidate-reopened.hwpx", None)
    )
    copied = run / f"contract-source-copy-{phase}.hwp"
    prepared = copied
    reply = run / f"contract-export-reply-{phase}.json"
    # An old prepared input remains failed evidence, never a direct-HWP fallback.
    legacy_input = (
        run / "contract-approved-input.hwpx"
        if phase == 0 and plan["schema"] in (3, 4)
        else None
    )
    saved_export = (
        run / SAVED_EXPORT_COPY if phase == 0 and plan["schema"] == 3 else None
    )
    for path in (*outputs, copied, prepared, legacy_input, saved_export, reply):
        if path is not None:
            real_path(path)
            if path.exists():
                raise FileExistsError(
                    "Native output exists; no overwrite/retry: " + str(path)
                )
    return (
        workspace,
        run,
        source,
        outputs,
        reply,
        copied,
        prepared,
        request,
        plan,
        selected,
    )


class NativeContractExporter:
    def __init__(self, workspace, expected_hash):
        self.workspace = real_path(workspace).resolve(strict=True)
        self.expected_hash = expected_hash
        self.phase = 0

    def _require_qualified(self, plan):
        require_qualified(plan)

    def _launch_worker(self, command, run, request):
        return subprocess.run(
            command,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            timeout=240,
            check=False,
        )

    def __call__(
        self,
        source,
        pdf,
        *,
        hwpx=None,
        hwp=None,
        native_workspace=None,
        native_plan=None,
    ):
        import hwp_review as r

        if self.phase not in (0, 1):
            raise ValueError("Only two fixed Native exports allowed")
        run = real_path(hwp if self.phase == 0 and hwp is not None else source).parent
        if run.parent != self.workspace / "runs" or not r.RUN_ID.fullmatch(run.name):
            raise ValueError("Native output run must be owned")
        plan = read_plan(run / "plan.json")
        self._require_qualified(plan)
        manifest = r.verify_baseline(self.workspace)
        if r.plan_hash(plan) != self.expected_hash:
            raise ValueError("Native exporter plan changed")
        lease = r.read_json(self.workspace / r.APPLY_LOCK)
        if (
            lease.get("pid") != os.getpid()
            or lease.get("plan_sha256") != self.expected_hash
            or lease.get("run_id") != run.name
        ):
            raise ValueError("Native exporter does not own this parent lease")
        expected_source = (
            Path(manifest["source"]) if self.phase == 0 else run / "candidate.hwp"
        )
        expected_outputs = (
            (
                run / "candidate-buffer.pdf",
                run / "candidate.hwpx",
                run / "candidate.hwp",
            )
            if self.phase == 0
            else (run / "candidate.pdf", None, None)
        )
        if (
            real_path(source) != real_path(expected_source)
            or (pdf, hwpx, hwp) != expected_outputs
        ):
            raise ValueError("Native exporter exact paths differ")
        if self.phase == 0:
            if native_workspace != self.workspace or native_plan != run / "plan.json":
                raise ValueError("Exact owned Native edit request required")
        elif native_workspace is not None or native_plan is not None:
            raise ValueError("Reopen cannot accept an edit request")
        request = {
            "schema": plan["schema"],
            "phase": self.phase,
            "lease": lease,
            "input_sha256": r.file_hash(source),
            "plan_sha256": self.expected_hash,
            "approval_scope_sha256": r.plan_hash(r.native_approval_scope(plan)),
        }
        path = real_path(run / f"contract-export-request-{self.phase}.json")
        import json

        with path.open("x", encoding="utf-8") as stream:
            json.dump(request, stream)
            stream.flush()
            os.fsync(stream.fileno())
        _, _, _, _, reply, _, _, _, _, _ = step_paths(
            self.workspace, run.name, self.phase
        )
        command = [sys.executable]
        if not frozen():
            command.append(str(Path(__file__).with_name("hwp_desktop.py")))
        command += [
            "--contract-export",
            str(self.workspace),
            "--contract-run",
            run.name,
            "--contract-phase",
            str(self.phase),
        ]
        try:
            result = self._launch_worker(command, run, request)
        except subprocess.TimeoutExpired as error:
            raise RuntimeError(
                "Native contract worker timed out; preserve lease/evidence, inspect owned state; no automatic retry/global kill"
            ) from error
        real_path(reply)
        if not reply.is_file() or reply.stat().st_size > 65536:
            raise RuntimeError("Native contract completion response missing")
        data = r.read_json(reply)
        if result.returncode or data.get("ok") is not True:
            raise RuntimeError(
                "Native contract blocked: " + str(data.get("error", "unknown"))
            )
        if (
            data.get("phase") != self.phase
            or data.get("schema") != plan["schema"]
            or data.get("plan_sha256") != self.expected_hash
            or data.get("approval_scope_sha256") != request["approval_scope_sha256"]
            or not isinstance(data.get("native"), dict)
        ):
            raise ValueError("Unbound Native contract reply")
        if r.file_hash(source) != request["input_sha256"]:
            raise ValueError("Native input changed during worker")
        if r.plan_hash(read_plan(run / "plan.json")) != self.expected_hash:
            raise ValueError("Exact Native plan changed during worker")
        selected = r.approved_plan(
            self.workspace,
            read_plan(run / "plan.json"),
            r.verify_baseline(self.workspace),
        )
        if self.phase == 1:
            reopened = real_path(run / "candidate-reopened.hwpx")
            expected_sha = data["native"].get("reopened_hwpx_sha256")
            if not reopened.is_file() or r.file_hash(reopened) != expected_sha:
                raise ValueError(
                    "Saved-HWP reopened structure proof missing or changed"
                )
            data["native"]["parent_saved_hwp_contract"] = contract_gate(
                self.workspace, reopened, plan, selected
            )
            if r.file_hash(reopened) != expected_sha:
                raise ValueError(
                    "Reopened Native structure changed during parent verification"
                )
            if r.plan_hash(read_plan(run / "plan.json")) != self.expected_hash:
                raise ValueError("Exact Native plan changed during parent verification")
            r.approved_plan(
                self.workspace,
                read_plan(run / "plan.json"),
                r.verify_baseline(self.workspace),
            )
        r.assert_apply_lock_owned(self.workspace, lease)
        self.phase += 1
        return data["native"]


@contextmanager
def _native_session(workspace, run_id, phase):
    run = workspace / "runs" / run_id
    paths = [run / f"contract-source-copy-{phase}.hwp"]
    paths += [
        run / name
        for name in (
            "contract-approved-input.hwpx",
            SAVED_EXPORT_COPY,
            "candidate.hwp",
            "candidate.hwpx",
            "candidate-buffer.pdf",
            "candidate.pdf",
            "candidate-reopened.hwpx",
        )
    ]
    with owned_session(paths) as reference:
        yield reference


def export_phase(workspace, run_id, phase):
    return _export_phase(workspace, run_id, phase, require_qualified, _native_session)


def _export_phase(
    workspace,
    run_id,
    phase,
    qualify: Callable[[dict], None],
    session: Callable[[Path, str, int], AbstractContextManager[Any]],
):
    """Code-owned validation seam; no CLI or data-driven admission."""
    if not callable(qualify) or not callable(session):
        raise ValueError("Explicit code-owned guard and Native lifecycle required")
    import hwp_review as r
    import importlib

    workspace, run, source, outputs, _, copied, prepared, request, plan, selected = (
        step_paths(workspace, run_id, phase)
    )
    qualify(plan)
    cells = (
        importlib.import_module("hwp_native_cells")
        if phase == 0 and plan["schema"] in (3, 4)
        else None
    )
    minima = None
    manifest = r.verify_baseline(workspace)
    if cells is not None:
        if plan["schema"] == 3:
            minima = cells.minima_profile(workspace / "baseline.hwpx", plan, manifest)
        else:
            cells.plain_profile(workspace / "baseline.hwpx", selected)
    security = registered_security_module()
    with source.open("rb") as original, copied.open("xb") as target:
        shutil.copyfileobj(original, target, length=1024 * 1024)
    if (
        r.file_hash(copied) != request["input_sha256"]
        or r.file_hash(source) != request["input_sha256"]
    ):
        raise ValueError("Native contract source copy changed")
    prepared_sha = r.file_hash(prepared)
    saved_table = phase == 0 and plan["schema"] == 3
    protected = {
        source: request["input_sha256"],
        copied: request["input_sha256"],
        prepared: prepared_sha,
    }

    def authorized():
        if (
            r.plan_hash(read_plan(run / "plan.json")) != request["plan_sha256"]
            or r.plan_hash(read_plan(workspace / PROPOSALS[plan["schema"]]))
            != request["plan_sha256"]
        ):
            raise ValueError("Exact Native proposal changed during worker")
        if r.approved_plan(workspace, plan, r.verify_baseline(workspace)) != selected:
            raise ValueError("Exact Native selected scope changed during worker")
        r.assert_apply_lock_owned(workspace, request["lease"])
        for path, digest in protected.items():
            if real_path(path) != path or r.file_hash(path) != digest:
                raise ValueError("Protected Native input/output changed")

    def save_outputs(app, entries):
        for target, format in entries:
            if target is None:
                continue
            authorized()
            if app.GetTextFile("TEXT", "") != expected:
                raise ValueError("Unexpected Native buffer input before save")
            real_path(target)
            if (
                target.exists()
                or not app.SaveAs(str(target.resolve()), format, "")
                or not target.is_file()
                or not target.stat().st_size
            ):
                raise RuntimeError("Native contract output failed: " + format)
            if app.GetTextFile("TEXT", "") != expected:
                raise ValueError("Unexpected Native text during save")
            protected[target] = r.file_hash(target)
        authorized()

    authorized()
    with session(workspace, run_id, phase) as app:
        if not app.RegisterModule(MODULE_TYPE, MODULE_ALIAS):
            raise RuntimeError("Approved module rejected; no document opened")
        if not app.Open(
            str(prepared.resolve()),
            "HWPX" if prepared.suffix.lower() == ".hwpx" else "HWP",
            "versionwarning:false",
        ):
            raise RuntimeError("Native approved copy/input rejected; no retry")
        evidence = []
        binding_proof = {}
        if cells is not None and plan["schema"] == 4:
            binding = cells.capture(
                app,
                copied,
                request["input_sha256"],
                workspace / "baseline.hwpx",
                selected,
            )
            binding_proof = {"native_empty_cell_binding": binding}
            expected = app.GetTextFile("TEXT", "")
            import hashlib

            if (
                not isinstance(expected, str)
                or hashlib.sha256(expected.encode("utf-8")).hexdigest()
                != binding["full_text_sha256"]
                or binding["source_sha256"] != request["input_sha256"]
                or binding["baseline_hwpx_sha256"] != plan["baseline_hwpx_sha256"]
            ):
                raise ValueError(
                    "Content-verified Native binding changed before insertion"
                )
            targets = binding["targets"]
            if len(targets) != len(selected) or len(
                {tuple(item["position"]) for item in targets}
            ) != len(targets):
                raise ValueError("Incomplete or duplicate Native field binding")
            primitive = importlib.import_module("hwp_native_buffer")
            for item, target in zip(selected, targets):
                if (
                    target["field_id"] != item["field_id"]
                    or target["cell_path"] != item["cell_path"]
                    or target["row"] != int(item["cell_address"]["rowAddr"])
                    or target["col"] != int(item["cell_address"]["colAddr"])
                ):
                    raise ValueError("Native binding differs from exact selected cell")
                authorized()
                if (
                    app.XHwpDocuments.Count != 1
                    or real_path(Path(app.XHwpDocuments.Item(0).FullName)) != copied
                    or r.file_hash(copied) != request["input_sha256"]
                    or r.file_hash(source) != request["input_sha256"]
                ):
                    raise ValueError(
                        "Native source copy identity changed before insertion"
                    )
                proof, expected = primitive.insert_bound_empty_cell(
                    app, item, target["position"], expected
                )
                evidence.append(proof)
        elif phase == 0 and plan["schema"] in (2, 3):
            primitive = importlib.import_module("hwp_native_buffer")
            authorized()
            if minima is not None and cells is not None:
                import hashlib

                binding = cells.capture_minima(
                    app,
                    copied,
                    request["input_sha256"],
                    workspace / "baseline.hwpx",
                    plan,
                    manifest,
                )
                expected = app.GetTextFile("TEXT", "")
                targets = binding["targets"]
                if (
                    binding.get("schema") != "hwp-native-original-minima-binding/v1"
                    or binding.get("policy") != cells.MINIMA_PROFILE
                    or binding.get("profile_sha256") != minima["profile_sha256"]
                    or binding.get("plan_sha256") != request["plan_sha256"]
                    or binding.get("source_sha256") != request["input_sha256"]
                    or binding.get("baseline_hwpx_sha256")
                    != plan["baseline_hwpx_sha256"]
                    or not isinstance(expected, str)
                    or hashlib.sha256(expected.encode("utf-8")).hexdigest()
                    != binding.get("full_text_sha256")
                    or len(targets) != len(minima["targets"])
                    or len({tuple(item["position"]) for item in targets})
                    != len(targets)
                ):
                    raise ValueError(
                        "Exact content-verified original-minima binding changed"
                    )
                for target, declared, cell in zip(
                    targets, minima["targets"], minima["cells"]
                ):
                    if (
                        set(target) != {"row", "col", "height", "position"}
                        or {key: target[key] for key in ("row", "col", "height")}
                        != declared
                        or (cell["row"], cell["col"]) != (target["row"], target["col"])
                    ):
                        raise ValueError(
                            "Native minima binding differs from exact declared cell"
                        )
                    if cells.position(target["position"])[0] == 0 or target["position"][
                        1:
                    ] != [0, 0]:
                        raise ValueError(
                            "Native minima binding is not an exact cell start"
                        )
                minimum_evidence = []
                for target, cell in zip(targets, minima["cells"]):
                    authorized()
                    diagnostic = {"row": target["row"], "col": target["col"]}
                    proof = primitive.set_bound_cell_minimum(
                        app,
                        copied,
                        request["input_sha256"],
                        expected,
                        target["position"],
                        cell["text"],
                        target["height"],
                        diagnostic,
                    )
                    minimum_evidence.append({**diagnostic, **proof})
                binding_proof = {
                    "native_original_minima_binding": {
                        "schema": binding["schema"],
                        "policy": binding["policy"],
                        "profile_sha256": binding["profile_sha256"],
                        "plan_sha256": binding["plan_sha256"],
                        "binding_sha256": r.plan_hash(binding),
                        "cell_count": len(targets),
                        "writer_qualified": False,
                    },
                    "native_original_minima_evidence": {
                        "cell_count": len(minimum_evidence),
                        "evidence_sha256": r.plan_hash({"cells": minimum_evidence}),
                    },
                }
                authorized()
                if app.GetTextFile("TEXT", "") != expected:
                    raise ValueError(
                        "Unexpected text after original minima before literal edits"
                    )
            evidence, expected = primitive.edit_exact_buffer(app, plan["edits"])
        else:
            expected = app.GetTextFile("TEXT", "")
            if not isinstance(expected, str):
                raise ValueError("Native full-buffer snapshot missing")
        # Match prepare/text page readiness; later preservation gates stay strict.
        if int(app.PageCount) < 1:
            raise ValueError("Native page layout is not ready before export")
        authorized()
        if r.file_hash(prepared) != prepared_sha:
            raise ValueError("Approved Native input changed before saving")
        entries = (
            ((outputs[2], "HWP"),)
            if saved_table
            else ((outputs[2], "HWP"), (outputs[1], "HWPX"), (outputs[0], "PDF"))
        )
        save_outputs(app, entries)
        if (
            r.file_hash(prepared) != prepared_sha
            or r.file_hash(source) != request["input_sha256"]
        ):
            raise ValueError("Native copy/input changed during save")
        authorized()
        reopened_proof = {}
        if phase == 1:
            reopened = real_path(run / "candidate-reopened.hwpx")
            reopened_proof["saved_hwp_contract"] = contract_gate(
                workspace, reopened, plan, selected
            )
            reopened_proof["reopened_hwpx_sha256"] = r.file_hash(reopened)
            if r.plan_hash(read_plan(run / "plan.json")) != request["plan_sha256"]:
                raise ValueError(
                    "Exact Native plan changed during reopened verification"
                )
            r.approved_plan(
                workspace, read_plan(run / "plan.json"), r.verify_baseline(workspace)
            )
            r.assert_apply_lock_owned(workspace, request["lease"])
        native = {
            **reopened_proof,
            **binding_proof,
            "native_pages": int(app.PageCount),
            "hancom_version": str(app.Version),
            "security_module": {**security, "registered": True},
            "native_selected_text_evidence": evidence,
            "full_buffer_exact_replacements_verified": True,
            "owned_session": app.ownership,
        }
    authorized()
    if not saved_table:
        return native

    # Verify the persisted HWP origin, not HWPX->HWPX serialization metadata.
    saved = outputs[2]
    if saved is None:
        raise ValueError("Fixed table export requires its saved HWP")
    saved_copy = real_path(run / SAVED_EXPORT_COPY)
    with saved.open("rb") as original, saved_copy.open("xb") as target:
        shutil.copyfileobj(original, target, length=1024 * 1024)
    if r.file_hash(saved_copy) != protected[saved]:
        raise ValueError("Saved Native HWP copy changed")
    protected[saved_copy] = protected[saved]
    authorized()
    if registered_security_module() != security:
        raise ValueError("Registered module changed before saved-HWP export")
    edit_ownership = native["owned_session"]
    with session(workspace, run_id, phase) as app:
        if not app.RegisterModule(MODULE_TYPE, MODULE_ALIAS):
            raise RuntimeError("Approved module rejected; saved HWP not opened")
        if not app.Open(str(saved_copy), "HWP", "versionwarning:false"):
            raise RuntimeError("Saved Native HWP copy rejected; no retry")
        name, _, modified = _document(app)
        if not name or real_path(Path(name)) != saved_copy or modified != 0:
            raise ValueError("Saved Native HWP identity changed")
        if app.GetTextFile("TEXT", "") != expected:
            raise ValueError("Saved Native HWP text differs from the edited buffer")
        pages = int(app.PageCount)
        if (
            pages < 1
            or pages != native["native_pages"]
            or str(app.Version) != native["hancom_version"]
        ):
            raise ValueError("Saved Native HWP page/version changed")
        save_outputs(app, ((outputs[1], "HWPX"), (outputs[0], "PDF")))
        if int(app.PageCount) != pages:
            raise ValueError("Saved Native HWP page count changed during export")
        authorized()
        native.update(
            native_pages=pages,
            edit_owned_session=edit_ownership,
            owned_session=app.ownership,
            native_verification_origin="saved_hwp_copy",
            saved_export_copy_sha256=protected[saved_copy],
        )
    authorized()
    return native


def run_worker(workspace, run_id, phase):
    _, _, _, _, reply, _, _, request, plan, _ = step_paths(workspace, run_id, phase)
    try:
        data, code = {"ok": True, "native": export_phase(workspace, run_id, phase)}, 0
    except Exception as error:
        data, code = {"ok": False, "error": type(error).__name__ + ": " + str(error)}, 1
    data.update(
        schema=plan["schema"],
        phase=phase,
        plan_sha256=request["plan_sha256"],
        approval_scope_sha256=request["approval_scope_sha256"],
    )
    import json

    with reply.open("x", encoding="utf-8") as stream:
        json.dump(data, stream, ensure_ascii=False, indent=2)
        stream.flush()
        os.fsync(stream.fileno())
    return code
