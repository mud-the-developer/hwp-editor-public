"""Explicit unsigned synthetic report probes; never normal startup or Native.

The packaging probe cannot approve or generate. The separate GUI host exposes
only the existing review UI; a human/controller must separately authorize and
perform its approval and creation actions through that UI.
"""

from pathlib import Path
import http.client
import json
import re
import sys
import threading
import tkinter as tk

from hwp_resources import APP_VERSION, frozen


def _request(origin, method, route, *, token=None, body=None, revision=None):
    from urllib.parse import urlsplit

    endpoint = urlsplit(origin)
    headers = {}
    if token is not None:
        headers["X-Review-Token"] = token
    if body is not None:
        body = json.dumps(body).encode("utf-8")
        headers.update({"Origin": origin, "Content-Type": "application/json"})
    if revision is not None:
        headers["If-Match"] = revision
    connection = http.client.HTTPConnection(endpoint.hostname, endpoint.port, timeout=15)
    try:
        connection.request(method, route, body=body, headers=headers)
        response = connection.getresponse()
        payload = response.read(4 * 1024 * 1024 + 1)
        if len(payload) > 4 * 1024 * 1024:
            raise RuntimeError("Synthetic report response exceeded its bound")
        return response.status, payload
    finally:
        connection.close()


def readonly_checks(directory):
    """Actual frozen runtime + owned server, with NO approval/output authority."""
    if not frozen():
        raise RuntimeError("Report packaging probe requires the actual frozen executable")
    import hwp_desktop as desktop
    import hwp_report_project as projects
    import hwp_review as review
    from hwp_synthetic_report import write_inputs

    directory.mkdir(exist_ok=False)
    profile, feedback = write_inputs(directory / "unsigned-inputs")
    info = projects.create(profile, feedback, base=directory / "projects")
    if not info.gate_passed or info.phase != "awaiting_draft_permission":
        raise RuntimeError("Synthetic report preparation blocked: " + "; ".join(info.issues))
    identity = review.read_json(info.root / "run/harness.json")["runtime_identity"]
    if (
        identity.get("origin") != "frozen"
        or identity.get("app_version") != APP_VERSION
        or identity.get("executable_sha256") != review.file_hash(Path(sys.executable))
        or not re.fullmatch(r"[a-f0-9]{64}", identity.get("local_scope_sha256", ""))
        or "source_sha256" in identity
    ):
        raise RuntimeError("Report runtime is not bound to the actual EXE and local scope")
    try:
        unexpected = review.create_template_review_server(info.review, 0)
    except ValueError:
        pass
    else:
        unexpected.server_close()
        raise RuntimeError("Unbound raw template review entered the frozen executable")
    before = {
        str(path.relative_to(info.root)): review.file_hash(path)
        for path in info.root.rglob("*") if path.is_file()
    }
    session = desktop.ReviewSession()
    try:
        reopened = session.start(info.root)
        origin = session.origin
        if reopened.root != info.root or not reopened.gate_passed:
            raise RuntimeError("Desktop opened the wrong synthetic report")
        status, html = _request(origin, "GET", "/")
        token_match = re.search(rb'name="review-token" content="([a-f0-9]{64})"', html)
        if status != 200 or token_match is None:
            raise RuntimeError("Owned report comparison UI did not become ready")
        token = token_match[1].decode("ascii")
        status, raw = _request(origin, "GET", "/api/template-state", token=token)
        if status != 200:
            raise RuntimeError("Owned report state is unavailable")
        state = review.parse_json(raw)
        if state["draft_authorized"] or state["report_harness"]["phase"] != "awaiting_draft_permission":
            raise RuntimeError("Preparation unexpectedly granted draft authority")
        status, _ = _request(
            origin, "POST", "/api/template-draft", token=token,
            revision=state["revision"],
            body={"plan_sha256": state["plan_sha256"], "draft_only_confirmed": False},
        )
        if status != 409:
            raise RuntimeError("Draft creation without permission was not refused")
        try:
            session.url("native")
        except ValueError:
            pass
        else:
            raise RuntimeError("Report session exposed a Native mode")
    finally:
        session.stop()
    after = {
        str(path.relative_to(info.root)): review.file_hash(path)
        for path in info.root.rglob("*") if path.is_file()
    }
    if before != after or projects.inspect(info.root).phase != "awaiting_draft_permission":
        raise RuntimeError("Read-only probe changed permission, acceptance, lease or output state")
    normal = projects.new_document(
        Path(profile["jobs"][0]["source"]), base=directory / "document-projects"
    )
    normal_before = {
        str(path.relative_to(normal.root)): review.file_hash(path)
        for path in normal.root.rglob("*") if path.is_file()
    }
    normal_session = desktop.ReviewSession()
    try:
        normal_session.start(normal.root)
        origin = normal_session.origin
        status, html = _request(origin, "GET", "/")
        match = re.search(rb'name="review-token" content="([a-f0-9]{64})"', html)
        if status != 200 or match is None:
            raise RuntimeError("Document-first workspace did not become ready")
        token = match[1].decode("ascii")
        status, raw = _request(origin, "GET", "/api/report-state", token=token)
        state = review.parse_json(raw)
        if status != 200 or state["versions"] or state["model"]["status"] != "disconnected":
            raise RuntimeError("Document workspace started a model or draft implicitly")
        status, _ = _request(
            origin, "POST", "/api/report-start", token=token,
            revision=state["revision"], body={},
        )
        if status != 409:
            raise RuntimeError("Unpermitted document request was accepted")
    finally:
        normal_session.stop()
    if normal_before != {
        str(path.relative_to(normal.root)): review.file_hash(path)
        for path in normal.root.rglob("*") if path.is_file()
    }:
        raise RuntimeError("Document read-only probe changed persisted authority")
    return {
        "unsigned_inputs_prepared": True,
        "runtime_and_scope_bound": True,
        "raw_frozen_template_blocked": True,
        "owned_desktop_session_ready": True,
        "unpermitted_draft_blocked": True,
        "document_workspace_ready": True,
        "unpermitted_request_blocked": True,
        "no_model_native_or_permission": True,
    }


def run_gui_host(directory):
    """NEW owned TOY host; consent and generation remain separate browser actions."""
    import hwp_desktop as desktop
    import hwp_review as review
    from hwp_synthetic_report import write_inputs

    directory = directory.expanduser().absolute()
    directory.mkdir(exist_ok=False)
    receipt = {
        "contract": "hwp-report-gui-host-v1",
        "purpose": "NEW UNSIGNED SYNTHETIC ONLY; not Native fidelity or submission approval",
        "version": APP_VERSION,
        "frozen": frozen(),
        "status": "starting",
        "no_model_or_hancom": True,
    }
    if frozen():
        import sys

        receipt["executable_sha256"] = review.file_hash(Path(sys.executable))
    report_file = directory / "GUI-HOST.json"
    receipt_lock = threading.Lock()

    def record(status, **fields):
        with receipt_lock:
            first_failure = receipt["status"] == "failed_preserved"
            if first_failure:
                if status in ("closed_by_user", "closed"):
                    receipt["manual_close_observed"] = True
            else:
                receipt.update(status=status, **fields)
            pending = directory / "GUI-HOST.pending.json"
            pending.write_text(json.dumps(receipt, ensure_ascii=False, indent=2), encoding="utf-8")
            pending.replace(report_file)
            return not first_failure

    record("starting")
    root = None
    original_browser = desktop.webbrowser.open
    try:
        profile, feedback = write_inputs(directory / "unsigned-inputs")
        root = tk.Tk()
        app = desktop.DesktopApp(root, report_base=directory / "projects")
        # Exercise the real open_window URL validator, but never spawn a browser
        # implicitly from a synthetic host. Its controller opens the owned URL.
        app.edge = None
        root.title("SYNTHETIC ONLY · HWPX report GUI host")

        def controlled_browser(url, new=0, autoraise=True):
            if (
                not desktop.is_report_info(app.session.info)
                or not app.session.info.root.is_relative_to(directory / "projects")
                or url != app.session.url("report")
            ):
                raise RuntimeError("GUI host refused a non-owned report address")
            if not record("ready", url=url, project=str(app.session.info.root)):
                raise RuntimeError("Synthetic GUI host failed; preserve the first receipt")
            return True

        desktop.webbrowser.open = controlled_browser

        def observe():
            try:
                if app.last_error:
                    raise RuntimeError(app.last_error)
                stop = directory / "STOP-REQUEST.json"
                if stop.exists():
                    if stop.is_symlink() or stop.stat().st_size > 4096:
                        raise RuntimeError("Invalid owned GUI stop request")
                    if review.read_json(stop) != {"contract": "hwp-report-gui-stop-v1"}:
                        raise RuntimeError("Invalid owned GUI stop contract")
                    if app.busy or app.closing:
                        raise RuntimeError("GUI stop requested during an active hub task; preserve host")
                    app.reports.close_dialog()
                    app.session.stop()
                    record("closing", stop_requested=True)
                    app.close()
                    return
                root.after(100, observe)
            except Exception as error:
                # Preserve the owned process, server and files. No cleanup/retry.
                record("failed_preserved", error=type(error).__name__ + ": " + str(error))

        root.after(100, observe)
        root.after_idle(lambda: app.reports.prepare(profile, feedback))
        root.mainloop()
        if receipt["status"] != "closing":
            record("closed_by_user")
        else:
            record("closed", owned_server_stopped=not app.session.running)
    except Exception as error:
        record("failed_preserved", error=type(error).__name__ + ": " + str(error))
        raise
    finally:
        desktop.webbrowser.open = original_browser
