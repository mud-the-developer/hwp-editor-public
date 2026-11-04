"""Owned Hancom COM instance. Never attaches to the user's existing document."""

from __future__ import annotations
import argparse
import json
from pathlib import Path
import shutil
import sys

from hwp_security import register_security_module
from hwp_native_owner import owned_session


def export(
    source: Path, pdf: Path, hwpx: Path | None = None, hwp: Path | None = None
) -> dict:
    with owned_session((source, pdf, hwpx, hwp)) as app:
        security = register_security_module(app)
        if not app.Open(str(source.resolve()), "", "versionwarning:false"):
            raise RuntimeError(
                "Hancom rejected the input document; check its file-access dialog."
            )
        pages = int(app.PageCount)
        for target, fmt in [(hwpx, "HWPX"), (hwp, "HWP"), (pdf, "PDF")]:
            if target is None:
                continue
            if target.exists():
                raise FileExistsError(f"Refusing to overwrite: {target}")
            target.parent.mkdir(parents=True, exist_ok=True)
            if not app.SaveAs(str(target.resolve()), fmt, ""):
                raise RuntimeError(f"Hancom failed to export {fmt}: {target}")
            if not target.is_file() or target.stat().st_size == 0:
                raise RuntimeError(
                    f"Hancom reported success without a nonempty {fmt} file."
                )
        return {
            "native_pages": pages,
            "hancom_version": str(app.Version),
            "security_module": security,
            "owned_session": app.ownership,
        }


def replace_plain_selection(app, edit: dict) -> dict:
    """One literal, unique, same-paragraph selection; never AllReplace."""
    old = edit["old_text"]
    if app.GetTextFile("TEXT", "").count(old) != 1:
        raise ValueError("Native literal is missing or ambiguous")
    if not app.SetPos(0, 0, 0) or tuple(app.GetPos())[:2] != (0, 0):
        raise ValueError("Native first body paragraph unavailable")
    find = app.HParameterSet.HFindReplace
    app.HAction.GetDefault("RepeatFind", find.HSet)
    flags = {
        "FindString": old,
        "ReplaceString": "",
        "IgnoreFindString": 0,
        "IgnoreReplaceString": 0,
        "Direction": app.FindDir("Forward"),
        "WholeWordOnly": 0,
        "UseWildCards": 0,
        "SeveralWords": 0,
        "AllWordForms": 0,
        "MatchCase": 1,
        "ReplaceMode": 0,
        "FindRegExp": 0,
        "FindJaso": 0,
        "HanjaFromHangul": 0,
        "AutoSpell": 0,
        "IgnoreMessage": 1,
        "FindType": 1,
    }
    for key, value in flags.items():
        setattr(find, key, value)
    if not app.HAction.Execute("RepeatFind", find.HSet):
        raise ValueError("Native exact literal was not selected")
    pos = tuple(app.GetSelectedPos())
    if (
        len(pos) != 7
        or not pos[0]
        or pos[1] != pos[4]
        or pos[2] != pos[5]
        or abs(pos[6] - pos[3]) != len(old.encode("utf-16-le")) // 2
    ):
        raise ValueError("Native selection differs from the approved single paragraph")
    text = app.GetTextFile("TEXT", "saveblock")
    if text not in (old, old + "\r\n", "\ufeff" + old, "\ufeff" + old + "\r\n"):
        raise ValueError("Native selected text is not the approved old text")
    insert = app.HParameterSet.HInsertText
    app.HAction.GetDefault("InsertText", insert.HSet)
    insert.Text = edit["new_text"]
    if not app.HAction.Execute("InsertText", insert.HSet):
        raise ValueError("Native selected-text insertion failed")
    return {
        "anchor_id": edit["anchor_id"],
        "selection_position": list(pos),
        "old_text_exact": True,
    }


def edit_exact_buffer(app, edits: list[dict]) -> tuple[list[dict], str]:
    """Reject unexpected typing/IME/API changes before any candidate is saved."""
    expected = app.GetTextFile("TEXT", "")
    if not isinstance(expected, str):
        raise ValueError("Native full-text snapshot unavailable")
    evidence = []
    for edit in edits:
        if app.GetTextFile("TEXT", "") != expected:
            raise ValueError(
                "Unexpected native input before replacement; no candidate saved"
            )
        proof = replace_plain_selection(app, edit)
        expected = expected.replace(edit["old_text"], edit["new_text"], 1)
        if app.GetTextFile("TEXT", "") != expected:
            raise ValueError(
                "Unexpected native text delta after replacement; no candidate saved"
            )
        evidence.append(proof)
    return evidence, expected


def edit_export(
    source: Path, pdf: Path, hwpx: Path, hwp: Path, workspace: Path, plan_path: Path
) -> dict:
    import hwp_review as r

    workspace = workspace.resolve()
    manifest = r.verify_baseline(workspace)
    plan = r.read_json(plan_path)
    if plan.get("schema") == 3:
        raise ValueError(
            "Schema3 requires the fixed approved contract controller; generic Source CLI is not an alternative"
        )
    if (
        plan.get("schema") not in (2, 4)
        or source.resolve() != Path(manifest["source"]).resolve()
        or source.suffix.lower() != ".hwp"
    ):
        raise ValueError(
            "Generic Source diagnostics require an exact schema-2/4 HWP baseline contract"
        )
    selected = r.approved_plan(workspace, plan, manifest)
    if (
        hwpx is None
        or hwp is None
        or any(target.exists() for target in (pdf, hwpx, hwp))
    ):
        raise ValueError("Native export needs NEW PDF/HWPX/HWP evidence paths")
    run = hwp.resolve().parent
    if run.parent != workspace / "runs" or any(
        target.resolve().parent != run for target in (pdf, hwpx, hwp)
    ):
        raise ValueError(
            "Native editing is restricted to one owned review run directory"
        )
    copied = run / "native-source-copy.hwp"
    if copied.exists():
        raise FileExistsError(copied)
    shutil.copy2(source, copied)
    if r.file_hash(copied) != manifest["source_audit"]["sha256"]:
        raise ValueError("Native copy differs from the immutable source")
    paths = (
        copied,
        pdf,
        hwpx,
        hwp,
        run / "native-original-row-minima.hwpx",
        run / "native-approved-blank-fill.hwpx",
    )
    with owned_session(paths) as app:
        security = register_security_module(app)
        input_path, input_format = copied, "HWP"
        if plan["schema"] == 4:
            input_path, input_format = run / "native-approved-blank-fill.hwpx", "HWPX"
            r.hwp_fill_plan.write_input(
                workspace / "baseline.hwpx", input_path, selected
            )
        if not app.Open(str(input_path), input_format, "versionwarning:false"):
            raise RuntimeError("Native source copy/input rejected; no retry performed")
        if plan["schema"] == 4:
            expected_text = app.GetTextFile("TEXT", "")
            if not isinstance(expected_text, str):
                raise ValueError("Native full-text snapshot unavailable")
            evidence = [
                {"field_id": item["field_id"], "empty_cell_path_verified": True}
                for item in selected
            ]
        else:
            evidence, expected_text = edit_exact_buffer(app, plan["edits"])
        r.approved_plan(workspace, plan, r.verify_baseline(workspace))
        for target, fmt in [(hwp, "HWP"), (hwpx, "HWPX"), (pdf, "PDF")]:
            if app.GetTextFile("TEXT", "") != expected_text:
                raise ValueError("Unexpected native input before save; export stopped")
            if (
                target.exists()
                or not app.SaveAs(str(target.resolve()), fmt, "")
                or not target.is_file()
                or not target.stat().st_size
            ):
                raise RuntimeError("Native diagnostic export failed: " + fmt)
            if app.GetTextFile("TEXT", "") != expected_text:
                raise ValueError(
                    "Unexpected native text during save; candidate is not a deliverable"
                )
        return {
            "native_pages": int(app.PageCount),
            "hancom_version": str(app.Version),
            "security_module": security,
            "native_selected_text_evidence": evidence,
            "full_buffer_exact_replacements_verified": True,
            "owned_session": app.ownership,
        }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("source", type=Path)
    parser.add_argument("--pdf", required=True, type=Path)
    parser.add_argument("--hwpx", type=Path)
    parser.add_argument("--hwp", type=Path)
    parser.add_argument("--native-workspace", type=Path)
    parser.add_argument("--native-plan", type=Path)
    args = parser.parse_args()
    if (args.native_workspace is None) != (args.native_plan is None):
        parser.error("Both native workspace and exact plan are required")
    if args.native_plan is not None:
        result = edit_export(
            args.source,
            args.pdf,
            args.hwpx,
            args.hwp,
            args.native_workspace,
            args.native_plan,
        )
    else:
        result = export(args.source, args.pdf, args.hwpx, args.hwp)
    print(json.dumps(result, ensure_ascii=False))


if __name__ == "__main__":
    reconfigure = getattr(sys.stdout, "reconfigure", None)
    if reconfigure is not None:
        reconfigure(encoding="utf-8")
    main()
