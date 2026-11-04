"""Continuous Tk→HTTP→Edge host. Default SYNTHETIC never starts Hancom.
Explicit Native mode: exact EXE + NEW unsigned TOY only, no real reports/retries.

Own NEW fixture files only. Chooser/confirm and Native/export/security are MOCKS;
app callbacks, XML patch, CAS, exact consent, progress, integrity and PDF gates are
real. Not actual-report or actual-GUI Native certification. Stop via owned stdin.
"""

import argparse
from contextlib import ExitStack
import hashlib
import json
from pathlib import Path
import queue
import sys
import threading
import time
import tkinter as tk
from unittest.mock import patch
import zipfile

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
import hwp_desktop as d
import hwp_project as p
import hwp_review as r
import hwp_security as security
import hwp_text_apply as a

OLD = "SYNTHETIC CODE AAAA"
NEW = "SYNTHETIC CODE BBBB"
AGENT_QUESTION = "SYNTHETIC propose one replacement draft"


class SyntheticPi:
    """Fixed fake agent seam; never opens a profile or starts an SDK/model."""

    def __init__(self, output):
        self.old_text, self.new_text = OLD, NEW
        self.runtime = {
            "cli": str(output / "SYNTHETIC-Pi/bin/pi.cmd"),
            "node": str(output / "SYNTHETIC-Pi/node.exe"),
            "sdk_entry": str(output / "SYNTHETIC-Pi/index.js"),
            "sdk_version": "1.3.0",
            "sha256": "8" * 64,
        }
        self.model = {"provider": "fixture", "id": "NO-MODEL"}
        self.calls = {"discovery": 0, "metadata": 0, "profile": 0, "chat": 0}

    def scan_path(self):
        self.calls["discovery"] += 1
        return {
            "agents": [
                {
                    "id": "pi",
                    "label": "Pi",
                    "documented_protocol": "SYNTHETIC ONLY",
                    "status": "detected",
                    "path": self.runtime["cli"],
                    "runtime_verified": False,
                    "connected": False,
                }
            ],
            "scope": "SYNTHETIC_ONLY",
            "ignored_entries": 0,
            "unreadable_candidates": 0,
        }

    def resolve_runtime(self, cli):
        if cli != self.runtime["cli"] or self.calls["metadata"]:
            raise ValueError("Only the fixed synthetic runtime, once")
        self.calls["metadata"] += 1
        return dict(self.runtime)

    def call_chat(self, runtime, kind, cancel, **kwargs):
        if runtime != self.runtime or cancel.is_set() or self.calls["metadata"] != 1:
            raise ValueError("Exact prepared synthetic runtime required")
        if kind == "profile_info":
            if self.calls["profile"]:
                raise ValueError("Synthetic profile connection cannot retry")
            self.calls["profile"] += 1
            return {"model": dict(self.model)}
        context = kwargs.get("context")
        review = context.get("review_context") if isinstance(context, dict) else None
        anchor = review.get("selected_anchor") if isinstance(review, dict) else None
        if (
            kind != "chat"
            or self.calls["profile"] != 1
            or self.calls["chat"]
            or kwargs.get("model") != self.model
            or kwargs.get("history") != []
            or kwargs.get("proposal_tools") is not True
            or kwargs.get("message") != AGENT_QUESTION
            or not isinstance(anchor, dict)
            or not isinstance(review, dict)
            or review.get("proposal_kind") != "text"
            or anchor.get("old_text") != self.old_text
            or not isinstance(anchor.get("anchor_id"), str)
            or not 0 < len(anchor["anchor_id"]) <= 160
        ):
            raise ValueError(
                "One fixed synthetic draft turn with exact context/consent required"
            )
        self.calls["chat"] += 1
        answer = "SYNTHETIC draft only; not approval or execution"
        return {
            "model": dict(self.model),
            "text": answer,
            "history": [
                {"role": "user", "content": AGENT_QUESTION},
                {"role": "assistant", "content": answer},
            ],
            "outcome": "settled",
            "usage": None,
            "tool_activity": [{"name": "propose_text_edits", "status": "finished"}],
            "artifacts": [
                {
                    "kind": "text_draft",
                    "anchor_id": anchor["anchor_id"],
                    "old_text": self.old_text,
                    "new_text": self.new_text,
                }
            ],
        }


def pdf(path, text):
    with r.fitz.open() as doc:
        page = doc.new_page(width=300, height=400)
        page.insert_text((20, 50), text, fontsize=12, fontname="cour")
        doc.save(path)


def package(path):
    with zipfile.ZipFile(path, "w") as z:
        z.writestr("mimetype", "application/hwp+zip")
        z.writestr(
            "Contents/section0.xml",
            '<sec xmlns="http://www.hancom.co.kr/hwpml/2011/section" xmlns:hp="http://www.hancom.co.kr/hwpml/2011/paragraph"><hp:p id="0"><hp:run charPrIDRef="1"><hp:t>'
            + OLD
            + '</hp:t></hp:run><hp:linesegarray><hp:lineseg textpos="0"/></hp:linesegarray></hp:p></sec>',
        )


class FakeBaseline:
    def __init__(self, workspace):
        self.workspace = workspace
        self.phase = 0

    def __call__(self, source, target, *, hwpx=None, hwp=None):
        if self.phase == 0:
            package(hwpx)
        if hwp is not None:
            hwp.write_bytes(b"SYNTHETIC ROUNDTRIP ONLY")
        pdf(target, OLD)
        self.phase += 1
        return {
            "native_pages": 1,
            "hancom_version": "MOCK ONLY",
            "security_module": {"SYNTHETIC": True},
        }


def fake_text_export(self, source, target, *, hwpx=None, hwp=None):
    if self.phase == 0:
        if hwp is None:
            raise ValueError("Synthetic first phase requires the exact new HWP output")
        nodes, _ = r.index_hwpx(source)
        self.fixture_text = nodes[0]["old_text"]
        if self.fixture_text != NEW:
            raise ValueError("Synthetic exact patch differs")
        hwp.write_bytes(b"SYNTHETIC APPLIED NOT REAL HWP")
    pdf(target, self.fixture_text)
    self.phase += 1
    return {"native_pages": 1, "MOCK_ONLY_NOT_HANCOM": True}


def wait(root, app):
    deadline = time.monotonic() + getattr(app, "fixture_timeout", 15)
    while app.busy and time.monotonic() < deadline:
        root.update()
        time.sleep(0.01)
    if app.busy or app.last_error:
        raise RuntimeError("Synthetic Tk action failed: " + app.last_error)


def layout_plan(workspace, old_text, new_text):
    """Compile an exact proposal ONLY; test consent still comes from browser UI."""
    manifest = r.verify_baseline(workspace)
    matches = [a for a in manifest["anchors"] if a["old_text"] == old_text]
    if len(matches) != 1 or not matches[0]["simple"] or not matches[0]["boxes"]:
        raise ValueError("Unique simple original table target required")
    legacy = {
        "schema": 1,
        "workspace_id": manifest["workspace_id"],
        "baseline_hwpx_sha256": manifest["baseline_hwpx_sha256"],
        "edits": [
            {
                "anchor_id": matches[0]["anchor_id"],
                "old_text": old_text,
                "new_text": new_text,
            }
        ],
    }
    native_plan = r.hwp_native_plan.proposal(
        legacy, manifest, workspace / "baseline.hwpx"
    )
    plan = r.hwp_fixed_layout.proposal(
        native_plan, manifest, workspace / "baseline.hwpx"
    )
    r.validate_plan(plan, manifest, workspace / "baseline.hwpx")
    return plan


def main(argv=None):
    if getattr(sys, "frozen", False):
        raise ValueError("SOURCE-only GUI test host required")
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("output", type=Path)
    parser.add_argument("executable", type=Path, nargs="?")
    parser.add_argument("--layout", action="store_true")
    parser.add_argument("--fill", action="store_true")
    parser.add_argument("--agent-workflow", action="store_true")
    parser.add_argument("--reference-toy-source", type=Path)
    parser.add_argument("--run-native-test", action="store_true")
    parser.add_argument("--toy-only", action="store_true")
    args = parser.parse_args(argv)
    if args.executable is not None and (
        args.run_native_test is not True or args.toy_only is not True
    ):
        raise ValueError("Explicit Native TOY flags required")
    if args.executable is None and (
        args.layout
        or args.fill
        or args.run_native_test
        or args.toy_only
        or args.agent_workflow
    ):
        raise ValueError("Native/layout flags need an exact candidate EXE")
    if args.layout and args.fill:
        raise ValueError("Select one exact Native GUI family")
    if args.agent_workflow and (args.layout or args.fill):
        raise ValueError(
            "Synthetic agent workflow supports the exact Native schema1 TOY only"
        )
    if args.reference_toy_source is not None and (
        args.executable is None or not (args.layout or args.fill) or args.agent_workflow
    ):
        raise ValueError("Reference TOY requires exact Native layout/fill mode only")
    output = a.real_path(args.output).resolve(strict=True)
    native_exe = (
        a.real_path(args.executable).resolve(strict=True)
        if args.executable is not None
        else None
    )
    layout, fill = args.layout, args.fill
    schema = 4 if fill else 3 if layout else 1
    if (
        not output.is_dir()
        or (
            native_exe is not None
            and (
                output.parent != ROOT / "work"
                or not output.name.startswith("toy-gui-native-")
            )
        )
        or (native_exe is None and not output.name.startswith("hwp-gui-SYNTHETIC-"))
    ):
        raise ValueError(
            "Only the matching owned synthetic or direct Native lab is permitted"
        )

    def check_candidate():
        if native_exe is None:
            raise ValueError("Exact Native candidate required")
        if layout or fill:
            import native_contract_smoke

            return native_contract_smoke.qualification_preflight(native_exe, schema)
        import native_text_apply_smoke

        return native_text_apply_smoke.qualification_preflight(native_exe)

    if native_exe is not None:
        check_candidate()
        global OLD, NEW
        if layout or fill:
            import native_contract_smoke as native_lab

            source = (
                native_lab.copy_reference_toy(
                    args.reference_toy_source, output / "source", schema
                )
                if args.reference_toy_source is not None
                else native_lab.create_source(output / "source", schema)
            )
            if layout:
                OLD, NEW = native_lab.TABLE_OLD, native_lab.NEW
        else:
            import native_text_apply_smoke as native_lab

            source = native_lab.create_source(output / "source")
            OLD, NEW = native_lab.OLD, native_lab.NEW
    else:
        source = output / "SYNTHETIC-NOT-REAL.hwp"
        with source.open("xb") as stream:
            stream.write(b"SYNTHETIC SOURCE NOT HWP")
    source_sha = hashlib.sha256(source.read_bytes()).hexdigest()
    synthetic_pi = (
        SyntheticPi(output) if native_exe is None or args.agent_workflow else None
    )
    events = []
    commands = queue.Queue()

    def read_commands():
        for line in sys.stdin:
            if line.strip() == "STOP":
                commands.put("STOP")
                break

    def audit(path):
        return {
            "sha256": r.file_hash(path),
            "embedded_binary_hashes": [],
            "flags": 1,
            "embedded_binary_count": 0,
            "MOCK_ONLY": True,
        }

    with ExitStack() as stack:
        if native_exe is not None:
            from types import SimpleNamespace

            check_candidate()
            stack.enter_context(patch.object(p, "frozen", return_value=True))
            stack.enter_context(
                patch.object(p, "sys", SimpleNamespace(executable=str(native_exe)))
            )
            stack.enter_context(patch.object(a, "frozen", return_value=True))
            stack.enter_context(
                patch.object(a, "sys", SimpleNamespace(executable=str(native_exe)))
            )
            if layout or fill:
                import hwp_contract_apply as contracts

                stack.enter_context(
                    patch.object(contracts, "frozen", return_value=True)
                )
                stack.enter_context(
                    patch.object(
                        contracts, "sys", SimpleNamespace(executable=str(native_exe))
                    )
                )
        else:
            stack.enter_context(patch.object(r, "hwp_audit", side_effect=audit))
        if synthetic_pi is not None:
            import hwp_agents as agents
            import hwp_pi_chat as chat
            import hwp_pi_probe as probe

            assert synthetic_pi is not None
            stack.enter_context(
                patch.object(agents, "scan_path", side_effect=synthetic_pi.scan_path)
            )
            stack.enter_context(
                patch.object(
                    chat,
                    "resolve_chat_runtime",
                    side_effect=synthetic_pi.resolve_runtime,
                )
            )
            stack.enter_context(
                patch.object(chat, "call_chat", side_effect=synthetic_pi.call_chat)
            )
            for module in (probe, chat):
                stack.enter_context(
                    patch.object(
                        module,
                        "run_sdk_command",
                        side_effect=AssertionError(
                            "No actual SDK/profile/model process in synthetic GUI"
                        ),
                    )
                )
        if native_exe is None:
            stack.enter_context(
                patch.object(
                    security,
                    "registered_security_module",
                    return_value={"SYNTHETIC": True},
                )
            )
            stack.enter_context(
                patch.object(
                    a, "registered_security_module", return_value={"SYNTHETIC": True}
                )
            )
            stack.enter_context(patch.object(p, "BaselineExporter", FakeBaseline))
            stack.enter_context(
                patch.object(a.TextExporter, "__call__", fake_text_export)
            )
        stack.enter_context(
            patch.object(d.filedialog, "askopenfilename", return_value=str(source))
        )
        stack.enter_context(patch.object(d.messagebox, "askyesno", return_value=True))
        stack.enter_context(
            patch.object(d, "open_window", return_value="SYNTHETIC: test owns browser")
        )
        original = p.new_project

        def project(selected, **kwargs):
            if selected.resolve() != source:
                raise ValueError("Foreign source forbidden")
            return original(selected, base=output / "owned-projects")

        stack.enter_context(patch.object(p, "new_project", side_effect=project))
        threading.Thread(target=read_commands, daemon=True).start()
        root = tk.Tk()
        root.withdraw()
        app = d.DesktopApp(root)
        setattr(app, "fixture_timeout", 850 if native_exe else 15)
        try:
            root.update()
            app.new_button.invoke()
            wait(root, app)
            events.append("Tk document picker callback → exclusive owned copy")
            app.prepare_button.invoke()
            wait(root, app)
            events.append(
                "Tk separate prepare confirmation → real engine baseline/roundtrip gates"
                + (
                    " with MOCK export"
                    if native_exe is None
                    else " with actual exact EXE Native export"
                )
            )
            if layout or fill:
                if app.info is None:
                    raise ValueError("Prepared contract TOY workspace missing")
                r.hwp_fixed_layout.require_native_profile(
                    app.info.path / "baseline.hwpx"
                )
            if layout:
                if app.info is None:
                    raise ValueError("Prepared table TOY workspace missing")
                r.write_json(
                    app.info.path / "proposal-layout.json",
                    layout_plan(app.info.path, OLD, NEW),
                )
                app.mode.set(d.MODES["layout"][0])
            if fill:
                if app.info is None:
                    raise ValueError("Prepared unsigned fill TOY workspace missing")
                import native_contract_smoke as native_lab

                r.write_json(
                    app.info.path / "fill-input.json",
                    native_lab.fill_input(app.info.path),
                )
                app.check.invoke()
                wait(root, app)
                if app.info is None or not app.info.has_fill_input:
                    raise ValueError("Actual fill input was not revalidated by Tk")
                app.mode.set(d.MODES["fill"][0])
                app.update_controls()
            app.open_button.invoke()
            wait(root, app)
            events.append("Tk review launch callback → exact owned HTTP server")
            if app.info is None:
                raise ValueError("Prepared GUI workspace missing")
            w = app.info.path
            if (w / "approved-plan.json").exists():
                raise ValueError("GUI must not auto-approve")
            print(
                json.dumps(
                    {
                        "origin": app.session.origin,
                        "workspace": str(w),
                        "source_sha256": source_sha,
                        "events": events,
                        "native_mocked": native_exe is None,
                        "agent_model_mocked": synthetic_pi is not None,
                        "agent_flow_requested": args.agent_workflow,
                        "source_reference_copy": args.reference_toy_source is not None,
                        "proposal_kind": "fill"
                        if fill
                        else "layout"
                        if layout
                        else "text",
                        "schema": schema,
                        "old_text": None if fill else OLD,
                        "new_text": None if fill else NEW,
                    }
                ),
                flush=True,
            )
            deadline = time.monotonic() + (850 if native_exe else 180)
            while time.monotonic() < deadline:
                root.update()
                time.sleep(0.01)
                try:
                    command = commands.get_nowait()
                except queue.Empty:
                    continue
                if command == "STOP":
                    break
            else:
                raise TimeoutError("Owned synthetic browser test did not stop")
            app.session.stop()
            root.update()
            if r.file_hash(source) != source_sha:
                raise ValueError("Synthetic original changed")
            summary = {
                "fixture_only": True,
                "schema": schema,
                "native_mocked": native_exe is None,
                "agent_model_mocked": synthetic_pi is not None,
                "agent_flow_requested": args.agent_workflow,
                "source_reference_copy": args.reference_toy_source is not None,
                "agent_flow_exercised": synthetic_pi is not None
                and synthetic_pi.calls["chat"] == 1,
                "actual_SDK_or_model_called": False,
                "agent_calls": dict(synthetic_pi.calls) if synthetic_pi else None,
                "executable_sha256": r.file_hash(native_exe) if native_exe else None,
                "source_unchanged": True,
                "events": events,
                "workspace": str(w),
                "attempts": r.review_attempts(w, r.verify_baseline(w)),
            }
            (output / "SYNTHETIC-TK-WORKFLOW.json").write_text(
                json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8"
            )
        finally:
            app.session.stop()
            for identifier in root.tk.call("after", "info"):
                root.after_cancel(identifier)
            root.destroy()


if __name__ == "__main__":
    main()
