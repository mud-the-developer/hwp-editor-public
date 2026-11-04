"""Explicit UI recovery checkpoint, not comments/proposal/approval/Native authority.

Fixed workspace file, baseline bound, closed bounded data, CAS and prior-byte
history. Never auto-restores, publishes, approves, edits HWP, clears a lease or
reads arbitrary paths. Plain local draft text: user must choose to save/restore.
"""

import json
import os
import re
import secrets

FILE = "review-session.json"
LIMIT = 1024 * 1024


def real_file(workspace, name):
    from hwp_text_apply import real_path

    root = real_path(workspace).resolve(strict=True)
    return real_path(root / name)


def read_raw(workspace):
    path = real_file(workspace, FILE)
    if not path.exists():
        return None
    if not path.is_file() or path.stat().st_size > LIMIT:
        raise ValueError(
            "복구용 초안 파일이 비정상/과대 크기입니다. 소유자가 확인하세요."
        )
    with path.open("rb") as stream:
        raw = stream.read(LIMIT + 1)
    if len(raw) > LIMIT:
        raise ValueError("Recovery snapshot exceeded its bound during read")
    return raw


def validate(data, manifest):
    import hwp_review as r

    keys = {
        "schema",
        "workspace_id",
        "baseline_hwpx_sha256",
        "comments_revision",
        "proposal_revision",
        "cursor",
        "memo",
        "text_draft",
    }
    if (
        not isinstance(data, dict)
        or set(data) != keys
        or type(data["schema"]) is not int
        or data["schema"] != 1
        or data["workspace_id"] != manifest["workspace_id"]
        or data["baseline_hwpx_sha256"] != manifest["baseline_hwpx_sha256"]
    ):
        raise ValueError(
            "다른 작업/기준 문서의 복구용 초안입니다. 자동 연결하거나 덮어쓰지 않습니다."
        )

    def revision(value):
        if not isinstance(value, str) or not (
            value == "absent" or re.fullmatch(r"[a-f0-9]{64}", value)
        ):
            raise ValueError("Recovery snapshot revision must be an exact file digest")

    revision(data["comments_revision"])
    revision(data["proposal_revision"])
    cursor, memo, draft = data["cursor"], data["memo"], data["text_draft"]
    if (
        not isinstance(cursor, dict)
        or set(cursor) != {"page", "view_mode", "version_id"}
        or type(cursor["page"]) is not int
        or not 1 <= cursor["page"] <= len(manifest["pages"])
        or cursor["view_mode"] not in ("original", "split", "diff")
        or cursor["version_id"] is not None
        and (
            not isinstance(cursor["version_id"], str)
            or not r.RUN_ID.fullmatch(cursor["version_id"])
        )
    ):
        raise ValueError("Invalid recovery cursor")
    if (
        not isinstance(memo, dict)
        or set(memo) != {"page", "text", "anchor_id", "region", "editing_id"}
        or not isinstance(memo["text"], str)
        or len(memo["text"]) > 10000
        or memo["editing_id"] is not None
        and (
            not isinstance(memo["editing_id"], str)
            or not memo["editing_id"].strip()
            or len(memo["editing_id"]) > 128
        )
    ):
        raise ValueError("Invalid recovery memo")
    # Even an empty editor preserves its exact target/page/point; placeholder
    # text is used ONLY for shape validation, never written as an actual note.
    r.validate_comments(
        {
            "workspace_id": manifest["workspace_id"],
            "comments": [
                {
                    "id": "RECOVERY-SHAPE-ONLY",
                    "page": memo["page"],
                    "anchor_id": memo["anchor_id"],
                    "region": memo["region"],
                    "comment": memo["text"]
                    if memo["text"].strip()
                    else "EMPTY EDITOR SHAPE",
                }
            ],
        },
        manifest,
    )
    if draft is None:
        return data
    if (
        not isinstance(draft, dict)
        or set(draft)
        != {"revision", "base_edits", "edits", "target_id", "pending_text", "open"}
        or not isinstance(draft["pending_text"], str)
        or len(draft["pending_text"]) > 20000
        or type(draft["open"]) is not bool
    ):
        raise ValueError("Invalid recovery text draft")
    revision(draft["revision"])
    anchors = {a["anchor_id"]: a for a in manifest["anchors"]}
    if draft["target_id"] is not None and (
        not isinstance(draft["target_id"], str) or draft["target_id"] not in anchors
    ):
        raise ValueError("Unknown recovery text target")
    for field in ("base_edits", "edits"):
        entries = draft[field]
        if not isinstance(entries, list) or len(entries) > 100:
            raise ValueError("Recovery edits exceed the bounded count")
        seen = set()
        for edit in entries:
            if (
                not isinstance(edit, dict)
                or set(edit) != {"anchor_id", "old_text", "new_text"}
                or not isinstance(edit["anchor_id"], str)
                or edit["anchor_id"] not in anchors
                or edit["anchor_id"] in seen
                or not isinstance(edit["old_text"], str)
                or not isinstance(edit["new_text"], str)
                or len(edit["new_text"]) > 20000
                or edit["old_text"] != anchors[edit["anchor_id"]]["old_text"]
            ):
                raise ValueError("Invalid/stale recovery edit data")
            seen.add(edit["anchor_id"])
    # Not validate_plan: unfinished/pending text is DATA, never execution-ready.
    return data


def read_snapshot(workspace, manifest):
    import hwp_review as r

    raw = read_raw(workspace)
    if raw is None:
        return {"revision": "absent", "snapshot": None, "issue": None}
    revision = r.digest(raw)
    try:
        data = r.parse_json(raw)
        if (
            isinstance(data, dict)
            and type(data.get("schema")) is int
            and data == {"schema": 1, "cleared": True}
        ):
            return {"revision": revision, "snapshot": None, "issue": None}
        validate(data, manifest)
        return {"revision": revision, "snapshot": data, "issue": None}
    except (ValueError, TypeError, KeyError) as error:
        return {"revision": revision, "snapshot": None, "issue": str(error)}


def save_snapshot(workspace, manifest, data, expected):
    validate(data, manifest)
    return replace_snapshot(workspace, data, expected)


def clear_snapshot(workspace, expected):
    # Explicit UI confirmation only. Retain previous bytes, don't delete history.
    return replace_snapshot(workspace, {"schema": 1, "cleared": True}, expected)


def list_history(workspace):
    import hwp_review as r
    folder = real_file(workspace, 'session-history')
    if not folder.exists():return []
    if not folder.is_dir():raise ValueError('Recovery history must be an actual directory')
    result=[]
    for item in sorted(folder.iterdir(), key=lambda p:p.name)[:500]:
        if not re.fullmatch(r'[a-f0-9]{64}\.json',item.name):continue
        actual=real_file(folder,item.name)
        if not actual.is_file() or actual.stat().st_size>LIMIT:raise ValueError('Unsafe recovery history')
        raw=actual.read_bytes()
        if r.digest(raw)!=item.stem:raise ValueError('Recovery history changed; no automatic repair')
        result.append({'sha256':item.stem,'bytes':len(raw)})
    return result


def delete_history(workspace, payload, expected):
    import hwp_review as r
    if not isinstance(payload,dict) or set(payload)!={'sha256','delete_confirmed'} or payload['delete_confirmed'] is not True or not isinstance(payload['sha256'],str):
        raise ValueError('Exact explicit recovery-history deletion confirmation required')
    raw=read_raw(workspace)
    if expected!='"'+(r.digest(raw) if raw is not None else 'absent')+'"':raise ValueError('Recovery changed before history deletion')
    entries=list_history(workspace)
    if payload['sha256'] not in {e['sha256'] for e in entries}:raise ValueError('Unknown recovery history entry')
    target=real_file(real_file(workspace,'session-history'),payload['sha256']+'.json')
    if r.file_hash(target)!=payload['sha256']:raise ValueError('History changed before deletion')
    target.unlink()  # ONLY one confirmed checkpoint-history entry, never source/consent/lease.
    return {'deleted':True,'document_changed':False,'approval_changed':False}


def replace_snapshot(workspace, data, expected):
    import hwp_review as r

    before = read_raw(workspace)
    actual = r.digest(before) if before is not None else "absent"
    if expected != '"' + actual + '"':
        raise ValueError("Recovery checkpoint changed; read latest before replacing")
    encoded = json.dumps(data, ensure_ascii=False, indent=2, allow_nan=False).encode(
        "utf-8"
    )
    if len(encoded) > LIMIT:
        raise ValueError("복구용 초안이 1 MiB를 넘습니다. 내용을 별도로 보존하세요.")
    if before == encoded:
        raise ValueError("동일한 복구용 초안입니다. 다시 저장하지 않습니다.")
    if before is not None:
        archive = real_file(workspace, "session-history")
        archive.mkdir(exist_ok=True)
        path = real_file(archive, actual + ".json")
        if path.exists():
            if path.read_bytes() != before:
                raise ValueError("Recovery history bytes differ; no repair")
        else:
            with path.open("xb") as stream:
                stream.write(before)
                stream.flush()
                os.fsync(stream.fileno())
    target = real_file(workspace, FILE)
    temp = real_file(workspace, ".session-" + secrets.token_hex(16) + ".tmp")
    with temp.open("xb") as stream:
        stream.write(encoded)
        stream.flush()
        os.fsync(stream.fileno())
    now = read_raw(workspace)
    if (r.digest(now) if now is not None else "absent") != actual:
        raise ValueError("Recovery changed during save; retained evidence, no retry")
    os.replace(temp, target)
    return {
        "revision": r.digest(encoded),
        "saved": True,
        "document_changed": False,
        "approval_changed": False,
    }
