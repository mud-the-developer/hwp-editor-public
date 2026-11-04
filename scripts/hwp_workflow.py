"""Local note/evidence/visual-review DATA, never HWP execution or submission consent."""
import base64
from datetime import datetime, timezone
import json
import os
import re
import secrets

FILE = "review-workflow.json"
MAX_JSON = 1024 * 1024
MAX_EVIDENCE = 512 * 1024
STATUSES = {"open", "in_progress", "resolved"}


def path(workspace, name):
    from hwp_text_apply import real_path
    return real_path(real_path(workspace).resolve(strict=True) / name)


def read(workspace, manifest):
    import hwp_review as r
    target = path(workspace, FILE)
    if not target.exists():
        return {"revision": "absent", "data": {"schema": 1, "workspace_id": manifest["workspace_id"],
                "baseline_hwpx_sha256": manifest["baseline_hwpx_sha256"], "notes": {}, "evidence": {}, "visual_reviews": {}}}
    if not target.is_file() or target.stat().st_size > MAX_JSON:raise ValueError("Workflow record missing/linked/oversized; no repair")
    with target.open('rb') as stream:raw = stream.read(MAX_JSON + 1)
    if len(raw) > MAX_JSON:raise ValueError("Workflow exceeded bounds while reading")
    data = r.parse_json(raw)
    if (not isinstance(data, dict) or set(data) != {"schema", "workspace_id", "baseline_hwpx_sha256", "notes", "evidence", "visual_reviews"}
            or type(data["schema"]) is not int or data["schema"] != 1
            or data["workspace_id"] != manifest["workspace_id"] or data["baseline_hwpx_sha256"] != manifest["baseline_hwpx_sha256"]
            or any(not isinstance(data[name], dict) for name in ("notes", "evidence", "visual_reviews"))):
        raise ValueError("Workflow belongs to another baseline or has invalid data")
    if len(data["notes"]) > 500 or len(data["evidence"]) > 100 or len(data["visual_reviews"]) > 100:
        raise ValueError("Workflow item count exceeds limits")
    def sha(value):return isinstance(value, str) and re.fullmatch(r'[a-f0-9]{64}', value) is not None
    for key, entry in data['evidence'].items():
        if (not sha(key) or not isinstance(entry, dict) or set(entry) != {'name','bytes','kind'}
                or not isinstance(entry['name'], str) or not 1 <= len(entry['name']) <= 128 or re.search(r'[\\/\x00-\x1f]', entry['name'])
                or type(entry['bytes']) is not int or not 0 < entry['bytes'] <= MAX_EVIDENCE or entry['kind'] not in {'txt','pdf','png','jpg','jpeg'}):
            raise ValueError('Invalid evidence metadata')
    for key, entry in data['notes'].items():
        if (not isinstance(key, str) or not 1 <= len(key) <= 128 or not isinstance(entry, dict)
                or set(entry) != {'comment_sha256','status','evidence_sha256'} or not sha(entry['comment_sha256']) or entry['status'] not in STATUSES
                or not isinstance(entry['evidence_sha256'], list) or len(entry['evidence_sha256']) > 20
                or any(not sha(s) or s not in data['evidence'] for s in entry['evidence_sha256'])):
            raise ValueError('Invalid saved note workflow')
    for key, entry in data['visual_reviews'].items():
        if (not isinstance(key, str) or not r.RUN_ID.fullmatch(key) or not isinstance(entry, dict)
                or set(entry) != {'hwp_sha256','pdf_sha256','checked_at','checks','local_visual_review_only','submission_approved'}
                or not sha(entry['hwp_sha256']) or not sha(entry['pdf_sha256']) or not isinstance(entry['checked_at'], str)
                or entry['checks'] != {'text':True,'layout':True,'images':True,'pages':True}
                or entry['local_visual_review_only'] is not True or entry['submission_approved'] is not False):
            raise ValueError('Invalid local review record, never submission authority')
    return {"revision": r.digest(raw), "data": data}


def commit(workspace, data, expected):
    import hwp_review as r
    target = path(workspace, FILE)
    before = target.read_bytes() if target.exists() else None
    if expected != '"' + (r.digest(before) if before is not None else "absent") + '"':raise ValueError("Workflow changed; read fresh state, no retry")
    encoded = json.dumps(data, ensure_ascii=False, indent=2, allow_nan=False).encode('utf-8')
    if len(encoded) > MAX_JSON:raise ValueError("Workflow exceeds 1 MiB")
    if before is not None:
        history = path(workspace, 'workflow-history');history.mkdir(exist_ok=True)
        old = path(history, r.digest(before) + '.json')
        if old.exists():
            if old.read_bytes() != before:raise ValueError("Workflow history differs")
        else:
            with old.open('xb') as stream:stream.write(before)
    temp = path(workspace, '.workflow-' + secrets.token_hex(16) + '.tmp')
    with temp.open('xb') as stream:stream.write(encoded);stream.flush();os.fsync(stream.fileno())
    current = target.read_bytes() if target.exists() else None
    if current != before:raise ValueError("Workflow changed during save; evidence retained")
    os.replace(temp, target)
    return {"revision": r.digest(encoded), "data": data, "document_changed": False, "submission_approved": False}


def note(workspace, manifest, payload, revision):
    import hwp_review as r
    if not isinstance(payload, dict) or set(payload) != {"comment_id", "comment_sha256", "status", "evidence_sha256"}:
        raise ValueError("Closed note workflow contract required")
    comments = r.validate_comments(r.read_json(workspace / 'comments.json'), manifest)['comments']
    target = next((c for c in comments if c['id'] == payload['comment_id']), None)
    if target is None or payload['comment_sha256'] != r.plan_hash(target):raise ValueError("Saved memo changed; recheck status/source")
    if payload['status'] not in STATUSES or not isinstance(payload['evidence_sha256'], list) or len(payload['evidence_sha256']) > 20:
        raise ValueError("Invalid note status/evidence count")
    saved = read(workspace, manifest)
    if revision != '"' + saved['revision'] + '"':raise ValueError("Workflow revision changed")
    refs = payload['evidence_sha256']
    if any(not isinstance(sha, str) or sha not in saved['data']['evidence'] for sha in refs) or len(set(refs)) != len(refs):
        raise ValueError("Unknown/duplicate evidence reference")
    saved['data']['notes'][target['id']] = {"comment_sha256": r.plan_hash(target), "status": payload['status'], "evidence_sha256": refs}
    return commit(workspace, saved['data'], revision)


def evidence(workspace, manifest, payload, revision):
    import hwp_review as r
    if (not isinstance(payload, dict) or set(payload) != {"name", "data_base64"} or not isinstance(payload['name'], str)
            or not 1 <= len(payload['name']) <= 128 or re.search(r'[\\/\x00-\x1f]', payload['name'])
            or not isinstance(payload['data_base64'], str) or len(payload['data_base64']) > MAX_EVIDENCE * 4 // 3 + 4):
        raise ValueError("Evidence must be a bounded local file, not a path/instruction")
    saved = read(workspace, manifest)
    if revision != '"' + saved['revision'] + '"':raise ValueError("Workflow revision changed")
    blob = base64.b64decode(payload['data_base64'], validate=True)
    if not 0 < len(blob) <= MAX_EVIDENCE:raise ValueError("Evidence file limit 512 KiB")
    suffix = payload['name'].lower().rsplit('.', 1)[-1]
    if suffix not in {'txt', 'pdf', 'png', 'jpg', 'jpeg'}:raise ValueError("Only TXT/PDF/PNG/JPEG evidence; never execute/render automatically")
    sha = r.digest(blob)
    if sha not in saved['data']['evidence'] and len(saved['data']['evidence']) >= 100:raise ValueError("Evidence item limit reached")
    folder = path(workspace, 'review-evidence');folder.mkdir(exist_ok=True);target = path(folder, sha + '.bin')
    if target.exists():
        if target.read_bytes() != blob:raise ValueError("Evidence bytes changed; no replacement")
    else:
        with target.open('xb') as stream:stream.write(blob);stream.flush();os.fsync(stream.fileno())
    saved['data']['evidence'][sha] = {"name": payload['name'], "bytes": len(blob), "kind": suffix}
    return commit(workspace, saved['data'], revision)


def evidence_bytes(workspace, manifest, sha):
    import hwp_review as r
    if not isinstance(sha, str) or not re.fullmatch(r'[a-f0-9]{64}', sha):raise ValueError("Invalid evidence id")
    record = read(workspace, manifest)['data']['evidence'].get(sha)
    if not isinstance(record, dict) or type(record.get('bytes')) is not int or not 0 < record['bytes'] <= MAX_EVIDENCE:
        raise ValueError("Evidence is not bound")
    with path(workspace / 'review-evidence', sha + '.bin').open('rb') as stream:blob = stream.read(MAX_EVIDENCE + 1)
    if len(blob) != record['bytes'] or r.digest(blob) != sha:raise ValueError("Evidence changed; download blocked")
    return blob


def visual_review(workspace, manifest, payload, revision):
    import hwp_review as r
    if (not isinstance(payload, dict) or set(payload) != {"run_id", "hwp_sha256", "pdf_sha256", "checks", "review_confirmed"}
            or payload['review_confirmed'] is not True or payload['checks'] != {"text": True, "layout": True, "images": True, "pages": True}
            or any(value is not True for value in payload['checks'].values())):
        raise ValueError("Separate exact local visual-review acknowledgments required")
    directory, version = r.review_version(workspace, manifest, payload['run_id'])
    if version['status'] != 'checks_passed_needs_human_visual_review' or version['artifact_integrity'] != 'verified':
        raise ValueError("Only exact verified result can receive a local review record")
    if payload['hwp_sha256'] != version['downloads'].get('hwp', {}).get('sha256') or payload['pdf_sha256'] != version['downloads'].get('pdf', {}).get('sha256'):
        raise ValueError("Visual-review result bytes changed")
    saved = read(workspace, manifest)
    if revision != '"' + saved['revision'] + '"':raise ValueError("Workflow revision changed")
    saved['data']['visual_reviews'][directory.name] = {"hwp_sha256": payload['hwp_sha256'], "pdf_sha256": payload['pdf_sha256'],
        "checked_at": datetime.now(timezone.utc).isoformat(), "checks": payload['checks'], "local_visual_review_only": True, "submission_approved": False}
    return commit(workspace, saved['data'], revision)
