"""Read-only frozen report probes; never normal startup, approval or Native."""

from pathlib import Path
import http.client
import json
import re
import sys

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
        unauth_status, _ = _request(origin, "GET", "/")
        token = getattr(session._server, "template_review_token", "")
        status, _ = _request(origin, "GET", "/", token=token)
        if unauth_status != 403 or status != 404 or not re.fullmatch(r"[a-f0-9]{64}", token):
            raise RuntimeError("Owned report server did not retire legacy HTML route cleanly")
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
            session.legacy_client()
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
        unauth_status, _ = _request(origin, "GET", "/")
        token = getattr(normal_session._server, "template_review_token", "")
        status, _ = _request(origin, "GET", "/", token=token)
        if unauth_status != 403 or status != 404 or not re.fullmatch(r"[a-f0-9]{64}", token):
            raise RuntimeError("Document-first server did not retire legacy HTML route cleanly")
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


