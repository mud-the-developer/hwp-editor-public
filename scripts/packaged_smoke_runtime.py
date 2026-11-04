"""Explicit frozen-EXE self-test; NEVER runs automatically in normal app use.

Creates ONLY a new test directory and synthetic files. This narrow test module is
embedded in the EXE; other tests, source reports and Native harnesses are not.
"""

from pathlib import Path
import http.client
import json
import os
import socket
import sys
import threading
import time
import tempfile
import re
import tkinter as tk
import zipfile

from hwp_resources import APP_VERSION, frozen, resource_root


def fixture(path, review):
    path.mkdir()
    (path / "synthetic-source.hwp").write_bytes(
        b"PACKAGED SELF TEST ONLY; not a real HWP"
    )
    with zipfile.ZipFile(path / "baseline.hwpx", "w") as package:
        package.writestr("mimetype", "application/hwp+zip")
        package.writestr(
            "Contents/section0.xml",
            '<sec xmlns="http://www.hancom.co.kr/hwpml/2011/section"/>',
        )
    with review.fitz.open() as pdf:
        page = pdf.new_page(width=300, height=400)
        page.insert_text((20, 40), "PACKAGED SYNTHETIC SELF TEST ONLY")
        pdf.save(path / "baseline.pdf")
        (path / "pages").mkdir()
        page.get_pixmap(dpi=100).save(path / "pages/page-001.png")
    manifest = {
        "workspace_id": "PACKAGED-SYNTHETIC-ONLY",
        "source": str(path / "synthetic-source.hwp"),
        "source_audit": {"sha256": review.file_hash(path / "synthetic-source.hwp")},
        "baseline_hwpx_sha256": review.file_hash(path / "baseline.hwpx"),
        "baseline_pdf_sha256": review.file_hash(path / "baseline.pdf"),
        "roundtrip_passed": True,
        "anchors": [],
        "protected_pages": [],
        "objects": {"tbl": 0, "pic": 0},
        "pages": [{"page": 1, "width": 300, "height": 400}],
    }
    review.write_json(path / "manifest.json", manifest)
    review.write_json(
        path / "comments.json",
        {"workspace_id": manifest["workspace_id"], "comments": []},
    )


def wait_ui(root, app):
    deadline = time.monotonic() + 10
    while app.busy and time.monotonic() < deadline:
        root.update()
        time.sleep(0.01)
    if app.busy:
        raise RuntimeError("Synthetic Tk task exceeded its deadline")


def debug_checks(root, app, desktop, info):
    checks = {}
    root.report_callback_exception(
        ValueError, ValueError("SYNTHETIC UI FAILURE ONLY"), None
    )
    if "SYNTHETIC UI FAILURE" not in app.last_error:
        raise RuntimeError("Windowed callback error was invisible")
    checks["callback_visible_without_console"] = True
    app.path.set(str(info.path))
    app.project = None
    app.info = info
    app.mode.set(desktop.MODES["native"][0])
    app.mode_picker.event_generate("<MouseWheel>", delta=-120)
    if app.mode.get() != desktop.MODES["native"][0]:
        raise RuntimeError("Wheel changed the proposal contract")
    checks["wheel_preserves_contract"] = True
    original_window, original_inspect = desktop.open_window, desktop.inspect_workspace

    def blocked_window(*args):
        raise OSError("SYNTHETIC WINDOW FAILURE; no browser launched")

    desktop.open_window = blocked_window
    try:
        app.session.start(info.path)
        app.launch(None)
        wait_ui(root, app)
        if app.address.get() != app.session.url("native") or app.info is None:
            raise RuntimeError("Requested proposal URL lost after window failure")
        checks["failed_window_retains_exact_url"] = True

        def blocked_source(*args):
            raise ValueError("SYNTHETIC SOURCE FAILURE; no file was changed")

        desktop.inspect_workspace = blocked_source
        app.launch(None)
        wait_ui(root, app)
        if app.info is not None or str(app.open_button.cget("state")) != "disabled":
            raise RuntimeError("Changed-source error retained stale ready state")
        checks["source_failure_revokes_ready"] = True
    finally:
        desktop.inspect_workspace = original_inspect
        # Never restore the real opener under a still-running test worker.
        if not app.busy:
            desktop.open_window = original_window
        app.session.stop()
    session = desktop.ReviewSession()
    session.start(info.path)
    client = socket.socket()
    client.settimeout(5)
    accepted = threading.Event()
    handler = session._server.RequestHandlerClass
    original = handler.handle_one_request

    def read_request(instance):
        accepted.set()
        return original(instance)

    handler.handle_one_request = read_request
    try:
        client.connect(("127.0.0.1", session._server.server_port))
        client.sendall(b"GET /api/state HTTP/1.1\r\nHost: 127.0.0.1:")
        if not accepted.wait(3):
            raise RuntimeError("Synthetic partial header was not accepted")
        started = time.monotonic()
        try:
            session.stop(timeout=0.05)
        except RuntimeError:
            if not session.stopping or time.monotonic() - started > 2:
                raise RuntimeError("Shutdown wait was not bounded/owned")
        else:
            raise RuntimeError("Partial reader unexpectedly completed")
        checks["shutdown_bounded_without_forced_close"] = True
    finally:
        client.close()
        handler.handle_one_request = original
        session.stop(timeout=10)
    return checks


def project_checks(root, app, desktop, review, workspace):
    folder = workspace.parent / "빈 프로젝트 폴더"
    folder.mkdir()
    app.path.set(str(folder))
    app.check_workspace()
    wait_ui(root, app)
    if app.project.state != "empty" or str(app.add_button.cget("state")) != "normal":
        raise RuntimeError("Empty-folder startup requires a manifest unexpectedly")
    original_audit = review.hwp_audit
    original_dialog = desktop.filedialog.askopenfilename
    original_consent = desktop.messagebox.askyesno
    original_prepare = desktop.hwp_project.prepare_project
    original_source = workspace / "synthetic-source.hwp"
    before = original_source.read_bytes()
    review.hwp_audit = lambda path: {
        "sha256": review.file_hash(path)
    }  # SYNTHETIC fixture, NOT HWP validity proof
    desktop.filedialog.askopenfilename = lambda **kwargs: str(original_source)
    desktop.messagebox.askyesno = lambda *args, **kwargs: False

    def forbidden_prepare(*args):
        raise AssertionError("Declined preparation MUST NOT invoke Native")

    desktop.hwp_project.prepare_project = forbidden_prepare
    try:
        app.add_hwp()
        wait_ui(root, app)
        if app.project.state != "staged" or app.info is not None:
            raise RuntimeError("Import incorrectly marked data prepared")
        if (
            folder / original_source.name
        ).read_bytes() != before or original_source.read_bytes() != before:
            raise RuntimeError("HWP import was not an unchanged copy")
        app.prepare_hwp()
        if app.busy or (folder / desktop.hwp_project.REVIEW_DIR).exists():
            raise RuntimeError("Cancelled consent still prepared data")
    finally:
        review.hwp_audit = original_audit
        desktop.filedialog.askopenfilename = original_dialog
        desktop.messagebox.askyesno = original_consent
        desktop.hwp_project.prepare_project = original_prepare
    return {
        "empty_folder": True,
        "exclusive_copy": True,
        "import_not_prepared": True,
        "declined_prepare_no_native": True,
    }


def usability_checks(root, app, desktop, review, workspace):
    if app.details.winfo_manager():
        raise RuntimeError("Advanced settings clutter startup")
    original_audit = review.hwp_audit
    original_dialog = desktop.filedialog.askopenfilename
    original_consent = desktop.messagebox.askyesno
    old_location = os.environ.get("LOCALAPPDATA")
    local = workspace.parent / "synthetic-local-app-data"
    os.environ["LOCALAPPDATA"] = str(local)
    review.hwp_audit = lambda path: {
        "sha256": review.file_hash(path)
    }  # explicit SYNTHETIC stub only
    desktop.filedialog.askopenfilename = lambda **kwargs: str(
        workspace / "synthetic-source.hwp"
    )
    before = (workspace / "synthetic-source.hwp").read_bytes()
    try:
        app.new_document()
        wait_ui(root, app)
        if (
            app.project.state != "staged"
            or app.project.root.parent != local / "HwpReview/Projects"
        ):
            raise RuntimeError("One document picker did not create a local staged copy")
        if app.project.source.read_bytes() != before:
            raise RuntimeError("Document-picker copy differs")
        if (
            app.prepare_button.winfo_manager() != "pack"
            or app.new_button.winfo_manager()
        ):
            raise RuntimeError("Next-step primary action was not isolated")
        app.busy = app.preparing = True
        app.events.put(("progress", (2, "SYNTHETIC PROGRESS")))
        app.poll()
        if not app.busy or app.info is not None or float(app.progress["value"]) != 2:
            raise RuntimeError("Progress event prematurely completed the job")
        desktop.messagebox.askyesno = lambda *args, **kwargs: False
        app.cancel_preparation()
        if app.cancel_requested.is_set():
            raise RuntimeError("Declined stop still set cancellation")
        desktop.messagebox.askyesno = lambda *args, **kwargs: True
        app.cancel_preparation()
        if not app.cancel_requested.is_set() or app.project.workspace.exists():
            raise RuntimeError("Safe stop did not remain a no-COM event")
        if app.project.source.read_bytes() != before:
            raise RuntimeError("Stop changed the copied input")
    finally:
        app.busy = app.preparing = False
        app.cancel_requested.clear()
        app.progress.stop()
        app.progress.grid_remove()
        app.update_controls()
        review.hwp_audit = original_audit
        desktop.filedialog.askopenfilename = original_dialog
        desktop.messagebox.askyesno = original_consent
        if old_location is None:
            os.environ.pop("LOCALAPPDATA", None)
        else:
            os.environ["LOCALAPPDATA"] = old_location
    return {
        "advanced_collapsed": True,
        "document_picker_copy": True,
        "single_next_action": True,
        "progress_nonterminal": True,
        "declined_stop_noop": True,
        "safe_stop_no_cleanup": True,
    }


def library_checks(root, app, desktop, review, workspace):
    location = workspace.parent / "synthetic-library-local"
    project = location / "HwpReview/Projects/SYNTHETIC-WORK"
    project.parent.mkdir(parents=True)
    fixture(project, review)
    manifest = review.read_json(project / "manifest.json")
    payload = {
        "workspace_id": manifest["workspace_id"],
        "comments": [
            {
                "id": "SYNTHETIC-EXPORT",
                "page": 1,
                "anchor_id": None,
                "region": [0.2, 0.3],
                "comment": "SYNTHETIC SAVED NOTE ONLY <script>NOT EXECUTABLE</script>",
            }
        ],
    }
    review.write_json(project / "comments.json", payload)
    before = {
        str(path.relative_to(project)): review.file_hash(path)
        for path in project.rglob("*")
        if path.is_file()
    }
    old_location = os.environ.get("LOCALAPPDATA")
    old_dialog = desktop.filedialog.asksaveasfilename
    os.environ["LOCALAPPDATA"] = str(location)
    try:
        app.show_library()
        wait_ui(root, app)
        dialog = app.library_window
        if dialog is None or len(dialog.tree.get_children()) != 1:
            raise RuntimeError("Frozen library missing own synthetic folder")
        after = {
            str(path.relative_to(project)): review.file_hash(path)
            for path in project.rglob("*")
            if path.is_file()
        }
        if before != after:
            raise RuntimeError("Library scan mutated source/metadata")
        dialog.query.set("NO MATCH")
        if dialog.tree.get_children():
            raise RuntimeError("Library search does not filter")
        dialog.query.set("")
        dialog.state.set("미완료·중단")
        dialog.render()
        if dialog.tree.get_children():
            raise RuntimeError("Library state filter does not filter")
        dialog.state.set("전체")
        dialog.render()
        dialog.tree.selection_set(dialog.tree.get_children()[0])
        dialog.open_selected()
        wait_ui(root, app)
        if app.info is None or app.info.path != project:
            raise RuntimeError("Library row did not pass normal inspection")
        target = workspace.parent / "SYNTHETIC-SAVED-NOTES.md"
        desktop.filedialog.asksaveasfilename = lambda **kwargs: str(target)
        app.export_saved_notes()
        wait_ui(root, app)
        text = target.read_text(encoding="utf-8")
        if (
            "SYNTHETIC SAVED NOTE" not in text
            or manifest["source_audit"]["sha256"] not in text
            or "수정 승인" not in text
        ):
            raise RuntimeError(
                "Notes export lacks saved text, identity or not-approval warning"
            )
        sha = review.file_hash(target)
        app.export_saved_notes()
        wait_ui(root, app)
        if "FileExistsError" not in app.last_error or review.file_hash(target) != sha:
            raise RuntimeError("Export overwrote an existing file")
        after = {
            str(path.relative_to(project)): review.file_hash(path)
            for path in project.rglob("*")
            if path.is_file()
        }
        if before != after:
            raise RuntimeError("Export changed workspace/approval")
    finally:
        desktop.filedialog.asksaveasfilename = old_dialog
        if old_location is None:
            os.environ.pop("LOCALAPPDATA", None)
        else:
            os.environ["LOCALAPPDATA"] = old_location
        if app.library_window is not None:
            app.library_window.close()
    return {
        "readonly_listing": True,
        "filtered_states": True,
        "revalidated_open": True,
        "saved_notes_export": True,
        "no_overwrite": True,
    }


def text_proposal_checks(desktop, review, workspace):
    path = workspace.parent / "SYNTHETIC-MANUAL-PROPOSAL"
    fixture(path, review)
    with zipfile.ZipFile(path / "baseline.hwpx", "w") as package:
        package.writestr("mimetype", "application/hwp+zip")
        package.writestr(
            "Contents/section0.xml",
            '<sec xmlns="http://www.hancom.co.kr/hwpml/2011/section" xmlns:hp="http://www.hancom.co.kr/hwpml/2011/paragraph"><hp:p id="0"><hp:run charPrIDRef="1"><hp:t>SYNTHETIC ORIGINAL PHRASE</hp:t></hp:run><hp:linesegarray><hp:lineseg textpos="0"/><hp:lineseg textpos="20"/></hp:linesegarray></hp:p></sec>',
        )
    manifest = review.read_json(path / "manifest.json")
    anchor = review.index_hwpx(path / "baseline.hwpx")[0][0]
    anchor.pop("raw")
    anchor.update(page_hint=1, boxes=[[10, 10, 190, 35]])
    manifest["anchors"] = [anchor]
    manifest["baseline_hwpx_sha256"] = review.file_hash(path / "baseline.hwpx")
    review.write_json(path / "manifest.json", manifest)
    original = {
        name: review.file_hash(path / name)
        for name in (
            "synthetic-source.hwp",
            "baseline.hwpx",
            "baseline.pdf",
            "comments.json",
        )
    }
    session = desktop.ReviewSession()
    session.start(path)
    try:
        connection = http.client.HTTPConnection(
            "127.0.0.1", session._server.server_port, timeout=10
        )
        connection.request("GET", "/")
        response = connection.getresponse()
        ui = response.read().decode("utf-8")
        if response.status != 200 or 'id="draft-save"' not in ui:
            raise RuntimeError("Manual draft UI missing from bundle")
        match = re.search(r"const token='([a-f0-9]+)'", ui)
        if match is None:
            raise RuntimeError("Manual draft token missing")
        token = match.group(1)
        payload = {
            "workspace_id": manifest["workspace_id"],
            "baseline_hwpx_sha256": manifest["baseline_hwpx_sha256"],
            "edits": [
                {
                    "anchor_id": anchor["anchor_id"],
                    "old_text": anchor["old_text"],
                    "new_text": "SYNTHETIC REPLACEMENT PHRASE",
                }
            ],
        }
        headers = {
            "Origin": session.origin,
            "X-Review-Token": token,
            "Content-Type": "application/json",
            "If-Match": '"absent"',
        }
        connection.request("POST", "/api/text-proposal", json.dumps(payload), headers)
        response = connection.getresponse()
        reply = review.parse_json(response.read())
        if (
            response.status != 200
            or reply["approved"] is not False
            or reply["proposal"]["schema"] != 1
        ):
            raise RuntimeError("Frozen manual proposal save failed")
        saved = (path / "proposal.json").read_bytes()
        headers["If-Match"] = '"WRONG"'
        connection.request("POST", "/api/text-proposal", json.dumps(payload), headers)
        response = connection.getresponse()
        response.read()
        if response.status != 409 or (path / "proposal.json").read_bytes() != saved:
            raise RuntimeError("Frozen stale plan write was not blocked")
        headers["If-Match"] = '"' + reply["proposal_revision"] + '"'
        payload["edits"][0]["new_text"] = "short"
        connection.request("POST", "/api/text-proposal", json.dumps(payload), headers)
        response = connection.getresponse()
        response.read()
        if (
            response.status != 400
            or (path / "proposal.json").read_bytes() != saved
            or (path / "proposal-native.json").exists()
        ):
            raise RuntimeError("Strict text failure widened scope or altered proposal")
        if (path / "approved-plan.json").exists():
            raise RuntimeError("Manual proposal created approval")
        if original != {name: review.file_hash(path / name) for name in original}:
            raise RuntimeError("Manual proposal changed original/baseline/comments")
        connection.close()
    finally:
        session.stop()
    return {
        "ui_available": True,
        "unapproved_save": True,
        "strict_block_no_fallback": True,
        "conflict_preserves_plan": True,
        "source_unchanged": True,
    }


def text_apply_checks(desktop, review, workspace):
    """Negative-only frozen API checks. No approval, COM or worker launch possible."""
    import hwp_text_apply as bounded

    path = workspace.parent / "SYNTHETIC-MANUAL-PROPOSAL"
    manifest = review.verify_baseline(path)
    plan = review.read_json(path / "proposal.json")
    before = {
        str(file.relative_to(path)): review.file_hash(file)
        for file in path.rglob("*")
        if file.is_file()
    }
    original_export = bounded.TextExporter.__call__

    def no_native(*args, **kwargs):
        raise RuntimeError("SYNTHETIC ONLY: Native forbidden in packaged self-test")

    bounded.TextExporter.__call__ = (
        no_native  # safety net, no positive/mocked-pass apply
    )
    session = desktop.ReviewSession()
    connection = None
    try:
        session.start(path)
        connection = http.client.HTTPConnection(
            "127.0.0.1", session._server.server_port, timeout=10
        )
        connection.request("GET", "/")
        response = connection.getresponse()
        ui = response.read().decode("utf-8")
        if response.status != 200 or 'id="apply-text"' not in ui:
            raise RuntimeError("Frozen bounded apply UI is missing")
        match = re.search(r"const token='([a-f0-9]+)'", ui)
        if match is None:
            raise RuntimeError("Frozen apply token missing")
        token = match.group(1)
        payload = {
            "workspace_id": manifest["workspace_id"],
            "baseline_hwpx_sha256": manifest["baseline_hwpx_sha256"],
            "plan_sha256": review.plan_hash(plan),
            "text_apply_confirmed": True,
        }
        headers = {
            "Origin": session.origin,
            "X-Review-Token": token,
            "Content-Type": "application/json",
            "If-Match": '"' + review.file_hash(path / "proposal.json") + '"',
        }
        connection.request("POST", "/api/apply-text", json.dumps(payload), headers)
        response = connection.getresponse()
        response.read()
        if (
            response.status != 400
            or session._server.text_apply_controller.thread is not None
        ):
            raise RuntimeError("Frozen unapproved apply must not start a job")
        for altered, expected in (
            ({**headers, "Origin": "http://evil.invalid"}, 403),
            ({**headers, "X-Review-Token": "WRONG"}, 403),
            ({**headers, "If-Match": '"WRONG"'}, 409),
        ):
            connection.request("POST", "/api/apply-text", json.dumps(payload), altered)
            response = connection.getresponse()
            response.read()
            if response.status != expected:
                raise RuntimeError("Frozen apply origin/token/revision gate failed")
        for run_id, phase in (("../OUTSIDE", 0), ("20261012T000000-a1b2c3d4", True)):
            try:
                bounded.run_worker(path, run_id, phase)
            except ValueError:
                pass
            else:
                raise RuntimeError("Unbound frozen worker accepted an invalid request")
        if (
            (path / "approved-plan.json").exists()
            or (path / review.APPLY_LOCK).exists()
            or (path / "runs").exists()
        ):
            raise RuntimeError("Negative-only frozen apply wrote consent/lease/results")
        after = {
            str(file.relative_to(path)): review.file_hash(file)
            for file in path.rglob("*")
            if file.is_file()
        }
        if after != before:
            raise RuntimeError("Frozen denied apply changed synthetic inputs")
    finally:
        if connection is not None:
            connection.close()
        session.stop()
        bounded.TextExporter.__call__ = original_export
    return {
        "ui_apply_control": True,
        "unapproved_never_starts": True,
        "unsafe_request_denied": True,
        "invalid_worker_cannot_export": True,
        "immutable_inputs": True,
    }


def result_download_checks(desktop, review, workspace):
    """Synthetic bound bytes ONLY, no consent or Native apply in this test."""
    path = workspace.parent / "SYNTHETIC-RESULT-DOWNLOAD"
    fixture(path, review)
    manifest = review.verify_baseline(path)
    run_id = "20261013T000000-a1b2c3d4"
    run_path = path / "runs" / run_id
    run_path.mkdir(parents=True)
    plan = {
        "schema": 1,
        "workspace_id": manifest["workspace_id"],
        "baseline_hwpx_sha256": manifest["baseline_hwpx_sha256"],
        "edits": [],
    }
    report = {
        "status": "checks_passed_needs_human_visual_review",
        "stage": "checks_passed",
        "result_binding_policy": review.RESULT_POLICY,
        "source_unchanged": True,
        "layout_gate": {"any_pixel_changed_pages": []},
        "SYNTHETIC_ONLY_NOT_NATIVE": True,
    }
    review.write_json(run_path / "plan.json", plan)
    review.write_json(run_path / "verification.json", report)
    (run_path / "candidate.hwp").write_bytes(b"SYNTHETIC DOWNLOAD NOT HWP")
    (run_path / "candidate.hwpx").write_bytes((path / "baseline.hwpx").read_bytes())
    for name in ("candidate.pdf", "candidate-buffer.pdf"):
        (run_path / name).write_bytes((path / "baseline.pdf").read_bytes())
    review.seal_result(run_path, manifest, plan)
    before = {
        str(p.relative_to(path)): review.file_hash(p)
        for p in path.rglob("*")
        if p.is_file()
    }
    session = desktop.ReviewSession()
    session.start(path)
    try:
        connection = http.client.HTTPConnection(
            "127.0.0.1", session._server.server_port, timeout=10
        )
        connection.request("GET", "/")
        response = connection.getresponse()
        ui = response.read().decode("utf-8")
        if response.status != 200 or 'id="download-hwp"' not in ui:
            raise RuntimeError("Frozen result download controls missing")
        match = re.search(r"const token='([a-f0-9]+)'", ui)
        if match is None:
            raise RuntimeError("Frozen download token missing")
        route = f"/result/{run_id}/hwp"
        connection.request("GET", route)
        response = connection.getresponse()
        response.read()
        if response.status != 403:
            raise RuntimeError("Frozen result download token gate failed")
        for kind, name in review.DOWNLOAD_FILES.items():
            connection.request(
                "GET",
                f"/result/{run_id}/{kind}",
                headers={"X-Review-Token": match.group(1)},
            )
            response = connection.getresponse()
            blob = response.read()
            if (
                response.status != 200
                or blob != (run_path / name).read_bytes()
                or response.getheader("X-Artifact-Sha256") != review.digest(blob)
            ):
                raise RuntimeError("Frozen result download snapshot differs")
        old = (run_path / "candidate.hwp").read_bytes()
        binding = (run_path / review.RESULT_FILE).read_bytes()
        (run_path / "candidate.hwp").write_bytes(b"CHANGED SYNTHETIC")
        connection.request("GET", route, headers={"X-Review-Token": match.group(1)})
        response = connection.getresponse()
        response.read()
        if (
            response.status != 400
            or (run_path / review.RESULT_FILE).read_bytes() != binding
        ):
            raise RuntimeError("Frozen changed result was served/resealed")
        (run_path / "candidate.hwp").write_bytes(
            old
        )  # restore OWN synthetic fixture only
        if (
            before
            != {
                str(p.relative_to(path)): review.file_hash(p)
                for p in path.rglob("*")
                if p.is_file()
            }
            or (path / "approved-plan.json").exists()
        ):
            raise RuntimeError(
                "Frozen read-only retrieval altered synthetic inputs/consent"
            )
        if session._server.text_apply_controller.thread is not None:
            raise RuntimeError("Download started an apply")
        connection.close()
    finally:
        session.stop()
    return {
        "ui_control": True,
        "token_required": True,
        "verified_snapshot_matches": True,
        "drift_blocked_no_reseal": True,
        "no_approval_or_native": True,
    }


def pi_boundary_checks(desktop, review, workspace):
    """Closed Pi routes in a synthetic workspace; NEVER executes SDK/auth/Native."""
    import hwp_pi_probe as probe
    import hwp_pi_chat as chat

    attempts = []
    original, original_chat = probe.run_sdk_command, chat.run_sdk_command

    def denied_sdk(*args, **kwargs):
        attempts.append(True)
        raise RuntimeError("Frozen self-test forbids SDK/profile/model processes")

    probe.run_sdk_command = chat.run_sdk_command = denied_sdk
    session = desktop.ReviewSession()
    connection = None
    try:
        session.start(workspace)
        server = session._server
        if server is None:
            raise RuntimeError("Pi boundary test server missing")
        connection = http.client.HTTPConnection(
            "127.0.0.1", server.server_port, timeout=10
        )
        origin = f"http://127.0.0.1:{server.server_port}"

        def request(method, route, body=None, headers=None):
            connection.request(
                method,
                route,
                body=json.dumps(body) if body is not None else None,
                headers=headers or {},
            )
            response = connection.getresponse()
            return response.status, response.read()

        status, page = request("GET", "/")
        ui = page.decode("utf-8")
        match = re.search(r"const token='([a-f0-9]+)'", ui)
        if (
            status != 200
            or match is None
            or any(
                'id="' + key + '"' not in ui
                for key in (
                    "pi-chat-connect",
                    "pi-chat-consent",
                    "pi-context-confirm",
                    "pi-context-actions",
                    "pi-chat-artifacts",
                    "pi-chat-progress",
                    "pi-chat-progress-text",
                )
            )
        ):
            raise RuntimeError("Frozen Pi consent/review controls missing")
        headers = {
            "Origin": origin,
            "Content-Type": "application/json",
            "X-Review-Token": match[1],
        }
        if request("GET", "/api/pi/chat")[0] != 403:
            raise RuntimeError("Pi chat token guard missing")
        status, blob = request("GET", "/api/pi/chat", headers=headers)
        data = json.loads(blob)
        if (
            status != 200
            or data["status"] != "disconnected"
            or data["turns"]
            or data["artifacts"]
            or data["progress"] is not None
            or any(
                data[key] is not False
                for key in (
                    "tools_enabled",
                    "write_tools_enabled",
                    "proposal_tools_enabled",
                    "document_shared",
                    "session_persisted",
                    "profile_loaded",
                )
            )
        ):
            raise RuntimeError("Unconnected frozen Pi chat received authority/data")
        invalid = {
            "prepare": {"prepare_confirmed": 1},
            "connect": {"reuse_pi_confirmed": True},
            "send": {
                "session_id": "foreign",
                "send_confirmed": True,
                "message": "SYNTHETIC",
            },
            "context": {"session_id": "foreign", "selection": {}},
            "artifact-review": {
                "session_id": "foreign",
                "artifact_id": "foreign",
                "review_confirmed": True,
            },
        }
        for route, payload in invalid.items():
            path = "/api/pi/chat/" + route
            if (
                request("POST", path, payload)[0] != 403
                or request(
                    "POST",
                    path,
                    payload,
                    {**headers, "Origin": "https://example.invalid"},
                )[0]
                != 403
                or request("POST", path, payload, headers)[0] != 400
            ):
                raise RuntimeError(
                    "Frozen Pi origin/closed consent/session boundary failed: " + route
                )
        controller = server.pi_chat_controller
        if (
            attempts
            or controller.thread is not None
            or controller.snapshot()["status"] != "disconnected"
            or server.text_apply_controller.thread is not None
            or server.contract_apply_controller.thread is not None
            or (workspace / "approved-plan.json").exists()
        ):
            raise RuntimeError(
                "Frozen Pi boundary test started a process or document action"
            )
        return {
            "ui_consent_controls": True,
            "stream_inactive_without_send": True,
            "token_origin_required": True,
            "closed_session_and_scope": True,
            "no_sdk_profile_or_model": True,
            "no_document_approval_or_apply": True,
        }
    finally:
        if connection is not None:
            connection.close()
        try:
            session.stop()
        finally:
            probe.run_sdk_command, chat.run_sdk_command = original, original_chat


def recovery_checks(desktop, review, workspace):
    """Explicit synthetic checkpoint only; no formal memo/proposal/consent writes."""
    path = workspace.parent / "SYNTHETIC-MANUAL-PROPOSAL"
    manifest = review.verify_baseline(path)
    before = {
        str(p.relative_to(path)): review.file_hash(p)
        for p in path.rglob("*")
        if p.is_file()
    }
    snapshot = {
        "schema": 1,
        "workspace_id": manifest["workspace_id"],
        "baseline_hwpx_sha256": manifest["baseline_hwpx_sha256"],
        "comments_revision": review.file_hash(path / "comments.json"),
        "proposal_revision": review.file_hash(path / "proposal.json"),
        "cursor": {"page": 1, "view_mode": "original", "version_id": None},
        "memo": {
            "page": 1,
            "text": "SYNTHETIC UNSAVED RECOVERY",
            "anchor_id": None,
            "region": None,
            "editing_id": None,
        },
        "text_draft": None,
    }
    session = desktop.ReviewSession()
    session.start(path)
    connection = None
    try:
        server = session._server
        if server is None:
            raise RuntimeError("Synthetic recovery server missing")
        connection = http.client.HTTPConnection(
            "127.0.0.1", server.server_port, timeout=10
        )
        connection.request("GET", "/")
        response = connection.getresponse()
        ui = response.read().decode("utf-8")
        if response.status != 200 or 'id="recovery-restore"' not in ui:
            raise RuntimeError("Frozen recovery UI missing")
        match = re.search(r"const token='([a-f0-9]+)'", ui)
        if match is None:
            raise RuntimeError("Frozen recovery token missing")
        headers = {
            "Origin": session.origin,
            "X-Review-Token": match.group(1),
            "Content-Type": "application/json",
            "If-Match": '"absent"',
        }
        connection.request("POST", "/api/session", json.dumps(snapshot), headers)
        response = connection.getresponse()
        saved = review.parse_json(response.read())
        if (
            response.status != 200
            or saved.get("document_changed") is not False
            or saved.get("approval_changed") is not False
        ):
            raise RuntimeError("Frozen recovery write changed authority")
        connection.request("GET", "/api/session")
        response = connection.getresponse()
        data = review.parse_json(response.read())
        if response.status != 200 or data["snapshot"] != snapshot:
            raise RuntimeError("Frozen recovery roundtrip failed")
        raw = (path / "review-session.json").read_bytes()
        connection.request("POST", "/api/session", json.dumps(snapshot), headers)
        response = connection.getresponse()
        response.read()
        if response.status != 409 or (path / "review-session.json").read_bytes() != raw:
            raise RuntimeError("Frozen recovery CAS failed")
        headers["If-Match"] = '"' + saved["revision"] + '"'
        connection.request(
            "POST", "/api/session-clear", json.dumps({"clear_confirmed": True}), headers
        )
        response = connection.getresponse()
        response.read()
        if (
            response.status != 200
            or (path / "session-history" / (review.digest(raw) + ".json")).read_bytes()
            != raw
        ):
            raise RuntimeError("Frozen recovery clear lost history")
        if (
            any(review.file_hash(path / name) != sha for name, sha in before.items())
            or (path / "approved-plan.json").exists()
            or (path / review.APPLY_LOCK).exists()
        ):
            raise RuntimeError("Frozen recovery altered source/proposal/consent/lease")
    finally:
        if connection is not None:
            connection.close()
        session.stop()
    return {
        "roundtrip_data_only": True,
        "cas_conflict": True,
        "clear_keeps_history": True,
        "no_document_or_approval": True,
        "ui_control": True,
    }


def workflow_checks(desktop, review, workspace):
    import importlib

    workflow = importlib.import_module("hwp_workflow")
    path = workspace.parent / "SYNTHETIC-BATCH-WORKFLOW"
    fixture(path, review)
    manifest = review.verify_baseline(path)
    memo = {
        "id": "SYNTHETIC-NOTE",
        "page": 1,
        "anchor_id": None,
        "region": None,
        "comment": "DATA ONLY",
    }
    review.write_json(
        path / "comments.json",
        {"workspace_id": manifest["workspace_id"], "comments": [memo]},
    )
    before = {
        str(p.relative_to(path)): review.file_hash(p)
        for p in path.rglob("*")
        if p.is_file()
    }
    import base64

    saved = workflow.evidence(
        path,
        manifest,
        {
            "name": "source.txt",
            "data_base64": base64.b64encode(b"SYNTHETIC EVIDENCE ONLY").decode(),
        },
        '"absent"',
    )
    sha = next(iter(saved["data"]["evidence"]))
    saved = workflow.note(
        path,
        manifest,
        {
            "comment_id": memo["id"],
            "comment_sha256": review.plan_hash(memo),
            "status": "resolved",
            "evidence_sha256": [sha],
        },
        '"' + saved["revision"] + '"',
    )
    if (
        saved["submission_approved"] is not False
        or workflow.evidence_bytes(path, manifest, sha) != b"SYNTHETIC EVIDENCE ONLY"
    ):
        raise RuntimeError("Frozen local workflow altered authority/bytes")
    if workflow.read(path, manifest)["data"]["notes"][memo["id"]][
        "comment_sha256"
    ] != review.plan_hash(memo):
        raise RuntimeError("Frozen note status not bound")
    raw = (path / workflow.FILE).read_bytes()
    (path / "review-evidence" / (sha + ".bin")).write_bytes(b"CHANGED")
    try:
        workflow.evidence_bytes(path, manifest, sha)
    except ValueError:
        pass
    else:
        raise RuntimeError("Changed frozen evidence was served")
    if (
        (path / workflow.FILE).read_bytes() != raw
        or any(
            review.file_hash(path / name) != digest for name, digest in before.items()
        )
        or (path / "approved-plan.json").exists()
    ):
        raise RuntimeError("Workflow altered document/consent")
    return {
        "note_hash_bound": True,
        "evidence_snapshot": True,
        "changed_evidence_blocked": True,
        "no_document_or_submission": True,
    }


def run(directory):
    # Reject existing directories/files BEFORE creating anything: no owner folder,
    # approval, manifest or report can be overwritten by a supplied test path.
    directory = directory.expanduser().absolute()
    directory.mkdir(exist_ok=False)
    report = {
        "purpose": "EXE SYNTHETIC SELF TEST ONLY; NOT HWP fidelity or approval",
        "version": APP_VERSION,
        "frozen": frozen(),
        "status": "failed",
    }
    try:
        import hwp_desktop as desktop

        review = desktop.engine()
        root_assets = resource_root()
        expected = (
            "web/review.html",
            "web/fill-review.html",
            "web/template-review.html",
            "web/report-workspace.html",
            "docs/template-review-draft.md",
            "docs/report-harness.md",
            "docs/user-guide.md",
            "docs/desktop-app.md",
            "docs/releases.md",
            "docs/licensing.md",
            "packaging/README-release.txt",
            "LICENSE",
            "scripts/pi_probe.mjs",
            "scripts/pi_chat.mjs",
            "docs/hwp-agent-runtime.md",
        )
        for name in expected:
            if not (root_assets / name).is_file():
                raise RuntimeError("Missing packaged asset: " + name)
        report["assets"] = list(expected)
        report["shared_resource_root"] = desktop.ROOT == review.ROOT == root_assets
        if not report["shared_resource_root"]:
            raise RuntimeError("Resource roots do not agree")
        if frozen():
            import importlib.util
            import _tkinter
            import olefile
            import pymupdf
            import hwp_resources
            import hwp_project
            import hwp_desktop_ui
            import hwp_workspace
            import hwp_library_ui
            import hwp_text_proposal
            import hwp_text_apply
            import hwp_review_session
            import hwp_workflow
            import hwp_prepare_native
            import hwp_security
            import hwp_process_identity
            import hwp_native_owner
            import hwp_agents
            import hwp_pi_probe
            import hwp_pi_chat
            import hwp_pi_context
            import hwp_template_draft
            import hwp_object_captions
            import hwp_report_harness
            import hwp_report_runtime
            import hwp_report_project
            import hwp_report_ui
            import hwp_desktop_reports
            import hwp_synthetic_report
            import hwp_report_smoke
            import hwp_report_control
            import hwp_report_writer
            import hwp_native_host
            import hwp_native_payload
            import hwp_native_smoke
            import hwp_omp_tui
            import hwp_setup
            import hwp_setup_runtime
            import hwp_setup_ui
            import pythoncom
            import win32com

            modules = (
                desktop,
                review,
                _tkinter,
                olefile,
                pymupdf,
                hwp_resources,
                hwp_project,
                hwp_desktop_ui,
                hwp_workspace,
                hwp_library_ui,
                hwp_text_proposal,
                hwp_text_apply,
                hwp_review_session,
                hwp_workflow,
                hwp_prepare_native,
                hwp_security,
                hwp_process_identity,
                hwp_native_owner,
                hwp_agents,
                hwp_pi_probe,
                hwp_pi_chat,
                hwp_pi_context,
                hwp_template_draft,
                hwp_object_captions,
                hwp_report_harness,
                hwp_report_runtime,
                hwp_report_project,
                hwp_report_ui,
                hwp_desktop_reports,
                hwp_synthetic_report,
                hwp_report_smoke,
                hwp_report_control,
                hwp_report_writer,
                hwp_native_host,
                hwp_native_payload,
                hwp_native_smoke,
                hwp_omp_tui,
                hwp_setup,
                hwp_setup_runtime,
                hwp_setup_ui,
                pythoncom,
                win32com,
            )
            module_paths = [getattr(module, "__file__", None) for module in modules]
            # Resolving the bundled client must not initialize or rebuild its cache.
            client_spec = importlib.util.find_spec("win32com.client")
            if client_spec is None or not isinstance(client_spec.origin, str):
                raise RuntimeError("Packaged COM client metadata missing")
            module_paths.append(client_spec.origin)
            report["runtime_modules_bundled"] = all(
                isinstance(path, str)
                and Path(path).resolve().is_relative_to(root_assets)
                for path in module_paths
            )
            if not report["runtime_modules_bundled"]:
                raise RuntimeError("Runtime module loaded outside frozen bundle")
            hwp_setup.load_manifest()  # Read-only self-test; never auto prepare requirements.
            native_ui = hwp_native_payload.locate_payload()
            native_manifest = hwp_native_payload.verify_payload(native_ui)
            report["native_ui_payload"] = {
                "verified": True,
                "framework_dependent": native_manifest["framework_dependent"],
                "runtime_bundled": False,
                "runtime_requirement": native_manifest["runtime_requirement"],
                "files_sha256": native_manifest["files_sha256"],
                "ui_exercised": False,
            }
        report["runtime"] = {
            "python": sys.version.split()[0],
            "tk": str(tk.TkVersion),
            "pymupdf": review.fitz.VersionBind,
        }
        with tempfile.TemporaryDirectory(prefix="synthetic-", dir=directory) as temp:
            workspace = Path(temp) / "한글 공백 가상 자료"
            fixture(workspace, review)
            before = {
                str(path.relative_to(workspace)): review.file_hash(path)
                for path in workspace.rglob("*")
                if path.is_file()
            }
            info = desktop.inspect_workspace(workspace)
            if info.workspace_id != "PACKAGED-SYNTHETIC-ONLY":
                raise RuntimeError("Wrong synthetic fixture")
            report["unicode_workspace"] = True
            if frozen():
                try:
                    review.native_export(
                        workspace / "synthetic-source.hwp",
                        workspace / "NOT-CREATED.pdf",
                    )
                except RuntimeError as error:
                    if "EXE" not in str(error):
                        raise
                else:
                    raise RuntimeError(
                        "Frozen Native export must be blocked before spawning"
                    )
                report["native_export_blocked"] = True
                from hwp_report_smoke import readonly_checks

                report["report_checks"] = readonly_checks(directory / "synthetic-report")
            session = desktop.ReviewSession()
            try:
                session.start(workspace)
                server = session._server
                if server is None:
                    raise RuntimeError("Synthetic owned server did not start")
                for route in (
                    "/",
                    "/fill",
                    "/api/state",
                    "/api/state?proposal=native",
                    "/api/state?proposal=layout",
                    "/api/versions",
                    "/api/search?q=PACKAGED",
                    "/page/1.png?dpi=144",
                ):
                    connection = http.client.HTTPConnection(
                        "127.0.0.1", server.server_port, timeout=10
                    )
                    try:
                        connection.request("GET", route)
                        response = connection.getresponse()
                        data = response.read()
                        if response.status != 200:
                            raise RuntimeError("Packaged route failed: " + route)
                        if route == "/" and b"__TOKEN__" in data:
                            raise RuntimeError("Review token missing")
                        if route == "/page/1.png?dpi=144" and not data.startswith(
                            b"\x89PNG"
                        ):
                            raise RuntimeError("PDF renderer failed")
                    finally:
                        connection.close()
                report["http_routes"] = 8
            finally:
                session.stop()
            report["pi_checks"] = pi_boundary_checks(desktop, review, workspace)
            root = tk.Tk()
            root.withdraw()
            try:
                app = desktop.DesktopApp(root, workspace)
                root.update_idletasks()
                report["runtime"]["tcl_patch"] = root.eval("info patchlevel")
                report["runtime"]["tk_patch"] = root.eval("set tk_patchLevel")
                if str(app.open_button.cget("state")) != "disabled":
                    raise RuntimeError("App must not auto-open data")
                app.show_help()
                root.update_idletasks()
                app.show_error()
                root.update_idletasks()
                report["tk_app_and_help"] = True
                report["project_checks"] = project_checks(
                    root, app, desktop, review, workspace
                )
                report["usability_checks"] = usability_checks(
                    root, app, desktop, review, workspace
                )
                report["library_checks"] = library_checks(
                    root, app, desktop, review, workspace
                )
                report["text_proposal_checks"] = text_proposal_checks(
                    desktop, review, workspace
                )
                report["text_apply_checks"] = text_apply_checks(
                    desktop, review, workspace
                )
                report["recovery_checks"] = recovery_checks(desktop, review, workspace)
                report["workflow_checks"] = workflow_checks(desktop, review, workspace)
                report["result_download_checks"] = result_download_checks(
                    desktop, review, workspace
                )
                report["debug_checks"] = debug_checks(root, app, desktop, info)
            finally:
                for identifier in root.tk.call("after", "info"):
                    root.after_cancel(identifier)
                root.destroy()
            after = {
                str(path.relative_to(workspace)): review.file_hash(path)
                for path in workspace.rglob("*")
                if path.is_file()
            }
            if after != before:
                raise RuntimeError("Synthetic workspace was changed by read-only probe")
            report["workspace_unchanged"] = True
            report["no_browser_hancom_or_approval"] = True
        report["status"] = "passed"
    except Exception as error:
        report["error"] = type(error).__name__ + ": " + str(error)
        raise
    finally:
        (directory / "SELF-TEST-ONLY.json").write_text(
            json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"
        )
