"""Opt-in ONE owned unsigned TOY: cell-position metadata only after source creation.
No field insertion, caller documents, later export, approval or writer qualification.
"""

import argparse
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import secrets
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
import hwp_review as r
from hwp_native_owner import owned_session
from hwp_security import MODULE_ALIAS, MODULE_TYPE, registered_security_module
import native_fill_metadata_probe as fixture
from native_blank_metadata_probe import hwp_inventory

KEYS = (
    "Width",
    "Height",
    "Row",
    "Col",
    "Rows",
    "Cols",
    "RowAddr",
    "ColAddr",
    "InstanceID",
    "ID",
)


def position(value):
    if (
        not isinstance(value, (tuple, list))
        or len(value) != 3
        or any(type(v) is not int or not 0 <= v < 2**31 for v in value)
    ):
        raise ValueError("Native position is not a typed bounded List/Para/Pos tuple")
    return list(value)


def scalar(value):
    if type(value) in (int, bool) or value is None:
        return value
    if (
        isinstance(value, str)
        and len(value) <= 256
        and not any(ord(c) < 32 for c in value)
    ):
        return value
    return {"kind": type(value).__name__[:64]}


def parameters(params):
    if params is None:
        raise ValueError("Required Native parameter set is unavailable")
    count = params.Count
    if type(count) is not int or not 0 <= count <= 512:
        raise ValueError("Unbounded Native parameter set")
    values = {}
    for key in KEYS:
        exists = params.ItemExist(key)
        if type(exists) not in (bool, int) or exists not in (0, 1):
            raise ValueError("Untyped Native ItemExist response")
        values[key] = {
            "present": bool(exists),
            "value": scalar(params.Item(key)) if exists else None,
        }
    return {
        "python_type": type(params).__name__[:64],
        "set_id": scalar(params.SetID),
        "count": count,
        "declared_key_observations": values,
    }


def indicator(value):
    if not isinstance(value, (tuple, list)) or not 1 <= len(value) <= 16:
        raise ValueError("Unexpected KeyIndicator metadata")
    return [scalar(v) for v in value]


def own_saved_document(app, source):
    if type(app.XHwpDocuments.Count) is not int or app.XHwpDocuments.Count != 1:
        return False
    doc = app.XHwpDocuments.Item(0)
    return (
        isinstance(doc.FullName, str)
        and Path(doc.FullName).resolve() == source.resolve()
        and type(doc.Modified) in (bool, int)
        and doc.Modified == 0
    )


def unchanged_source(app, source, expected_text, expected_sha):
    if not own_saved_document(app, source):
        raise ValueError("Own saved document identity/modification state changed")
    if (
        app.GetTextFile("TEXT", "") != expected_text
        or r.file_hash(source) != expected_sha
    ):
        raise ValueError("Read-only observation changed source text or bytes")


def selection_metadata(app):
    caret = position(app.GetPos())
    value = app.GetSelectedPos()
    if not isinstance(value, (tuple, list)) or len(value) > 16:
        raise ValueError("Selection metadata is not a finite tuple/list")
    items = []
    for item in value:
        if (
            type(item) not in (bool, int, type(None))
            or type(item) is int
            and not -(2**63) <= item < 2**63
        ):
            raise ValueError("Unsupported selection metadata scalar")
        items.append({"python_type": type(item).__name__, "value": item})
    if position(app.GetPos()) != caret:
        raise ValueError("Selection metadata read moved the caret")
    return {
        "caret": caret,
        "return_python_type": type(value).__name__,
        "sequence_length": len(value),
        "typed_items": items,
        "interpretation": "unclassified",
        "writer_qualified": False,
    }


def observe_saved(app, source, known):
    if (
        set(known) != {"1,1", "2,1", "3,1"}
        or len({tuple(v) for v in known.values()}) != 3
    ):
        raise ValueError("Authored target positions are incomplete or ambiguous")
    if not own_saved_document(app, source):
        raise ValueError(
            "Own saved document identity unavailable before text observation"
        )
    expected = app.GetTextFile("TEXT", "")
    if not isinstance(expected, str) or expected.count(fixture.MARKER) != 1:
        raise ValueError("Own test source text identity unavailable")
    digest = r.file_hash(source)
    unchanged_source(app, source, expected, digest)
    ctrl = app.HeadCtrl
    controls = []
    tables = []
    while ctrl is not None:
        if len(controls) >= 32:
            raise ValueError("Native control enumeration exceeded its finite bound")
        cid = ctrl.CtrlID
        if not isinstance(cid, str) or not 0 < len(cid) <= 8:
            raise ValueError("Untyped Native control identifier")
        controls.append(cid)
        if cid == "tbl":
            anchor = ctrl.GetAnchorPos(0)
            tables.append(
                {
                    "anchor": position(
                        [anchor.Item(k) for k in ("List", "Para", "Pos")]
                    ),
                    "properties": parameters(ctrl.Properties),
                }
            )
        ctrl = ctrl.Next
    if len(tables) != 1:
        raise ValueError("Expected exactly the one authored table control")
    observed = []
    for address, pos in sorted(known.items()):
        if not app.SetPos(*pos) or position(app.GetPos()) != pos:
            raise ValueError("Captured Native position did not roundtrip")
        data = {
            "authored_cell_address": address,
            "captured_position": pos,
            "observed_position": position(app.GetPos()),
            "selection_metadata": selection_metadata(app),
            "cell_shape": parameters(app.CellShape),
            "key_indicator": indicator(app.KeyIndicator()),
        }
        unchanged_source(app, source, expected, digest)
        observed.append(data)
    return {
        "control_kinds": controls,
        "one_table": tables[0],
        "captured_cell_observations": observed,
        "source_text_sha256": hashlib.sha256(expected.encode("utf-8")).hexdigest(),
        "source_sha256": digest,
        "source_unchanged": True,
        "key_indicator_is_binding_proof": False,
        "general_template_binding_proven": False,
        "writer_qualified": False,
    }


def navigation(app, name):
    if name not in ("MoveRight", "TableRightCell", "TableLeftCell", "TableLowerCell"):
        raise ValueError("Unknown read-only navigation action")
    if app.Run(name) is False:
        raise RuntimeError("Read-only caret navigation failed: " + name)


def observe_anchor_walk(app, source, grid, anchor, evidence):
    addresses = {f"{row},{col}" for row in range(4) for col in range(2)}
    if set(grid) != addresses or len({tuple(position(v)) for v in grid.values()}) != 8:
        raise ValueError(
            "Complete unique authored 4x2 grid required; never infer list IDs"
        )
    if not own_saved_document(app, source):
        raise ValueError("Own saved source identity unavailable for anchor walk")
    expected = app.GetTextFile("TEXT", "")
    digest = r.file_hash(source)
    if not isinstance(expected, str):
        raise ValueError("Own source text snapshot unavailable")
    anchor = position(anchor)
    if not app.SetPos(*anchor) or position(app.GetPos()) != anchor:
        raise ValueError("Exact table anchor did not roundtrip")
    unchanged_source(app, source, expected, digest)
    evidence["anchor"] = anchor
    evidence["steps"] = []
    navigation(app, "MoveRight")
    for row in range(4):
        for col in range(2):
            if col:
                navigation(app, "TableRightCell")
            actual = position(app.GetPos())
            known = position(grid[f"{row},{col}"])
            evidence["steps"].append(
                {
                    "authored_cell_address": f"{row},{col}",
                    "captured_position": known,
                    "observed_position": actual,
                }
            )
            if actual != known:
                raise ValueError(
                    f"Anchor navigation differs from authored cell {row},{col}; no fallback"
                )
            unchanged_source(app, source, expected, digest)
        if row < 3:
            navigation(app, "TableLeftCell")
            navigation(app, "TableLowerCell")
    evidence.update(
        all_authored_positions_match=True,
        source_unchanged=True,
        writer_qualified=False,
        general_template_binding_proven=False,
    )


def native_once(directory, report):
    directory = fixture.paths.real_path(directory)
    seed = directory / "TOY-ONLY-authoring-seed.hwp"
    source = directory / "TOY-ONLY-source.hwp"
    if not directory.is_dir() or seed.exists() or source.exists():
        raise FileExistsError("Fresh owned seed/source paths required")
    report["stage"] = "require_idle_hwp"
    report["before_hwp_processes"] = hwp_inventory()
    if report["before_hwp_processes"] != []:
        raise RuntimeError("Existing Hwp process; no owned probe start")
    module = registered_security_module()  # READ ONLY; never ensure/install.
    completed = False
    try:
        report["stage"] = "dispatch_new"
        with owned_session((seed, source)) as app:
            report["ownership"] = app.ownership
            report.update(owned_blank_verified=True, window_pid_binding_verified=True)
            if not app.RegisterModule(MODULE_TYPE, MODULE_ALIAS):
                raise RuntimeError("Existing approved module rejected")
            report["security_module"] = {**module, "registered": True}
            report["stage"] = "save_pristine_authoring_seed"
            if (
                seed.exists()
                or not app.SaveAs(str(seed), "HWP", "")
                or not seed.is_file()
                or not seed.stat().st_size
            ):
                raise RuntimeError("Own pristine seed save failed; no retry")
            report["authoring_seed_sha256"] = r.file_hash(seed)
            grid = {}

            def capture(row, col, app):
                grid[f"{row},{col}"] = position(app.GetPos())

            report["stage"] = "author_new_known_table"
            fixture.author(app, observe_cell=capture)
            fixture.native_pages(app)
            known = {key: grid[key] for key in ("1,1", "2,1", "3,1")}
            report["authored_grid_before_source_save"] = grid
            report["authored_positions_before_source_save"] = known
            report["stage"] = "save_own_source_once"
            if (
                source.exists()
                or not app.SaveAs(str(source), "HWP", "")
                or not source.is_file()
                or not source.stat().st_size
            ):
                raise RuntimeError("Own unsigned source save failed")
            report["stage"] = "observe_saved_source_metadata_only"
            report["observation"] = observe_saved(app, source, known)
            report["stage"] = "reopen_own_unmodified_source_once"
            if not own_saved_document(app, source):
                raise ValueError("Source identity changed before reopening")
            app.Clear(1)
            if not app.Open(str(source), "HWP", "versionwarning:false"):
                raise RuntimeError("Own source reopen failed; no retry")
            report["source_reopened"] = True
            report["stage"] = "observe_reopened_source_metadata_only"
            report["reopened_observation"] = observe_saved(app, source, known)
            if any(
                report["reopened_observation"][key] != report["observation"][key]
                for key in ("source_sha256", "source_text_sha256")
            ):
                raise ValueError("Reopened source text or bytes changed")
            report["stage"] = "readonly_anchor_navigation"
            report["anchor_navigation"] = {}
            observe_anchor_walk(
                app,
                source,
                grid,
                report["reopened_observation"]["one_table"]["anchor"],
                report["anchor_navigation"],
            )
            report["status"] = "metadata_observed_not_writer_qualified"
        completed = True
    except BaseException as error:
        report.update(
            status="blocked", error_type=type(error).__name__, error=str(error)[:1000]
        )
        raise
    finally:
        try:
            report["after_hwp_processes"] = hwp_inventory()
        except BaseException as error:
            report["inventory_error"] = type(error).__name__ + ": " + str(error)[:500]
        ownership = report.get("ownership", {})
        report["owned_process_exit_confirmed"] = (
            ownership.get("status") == "owned_exit_confirmed"
            and ownership.get("exit_confirmed") is True
            and report.get("after_hwp_processes") == []
        )
        report["cleanup"] = (
            "owned_exit_confirmed"
            if report["owned_process_exit_confirmed"]
            else "owned_exit_unconfirmed_owner_inspection_required"
        )
        if not report["owned_process_exit_confirmed"]:
            report["status"] = "blocked"
            if completed:
                raise RuntimeError("Owned creator exit unconfirmed; no retry")


def run(output, *, run_native=False, metadata_only=False):
    if (
        run_native is not True
        or metadata_only is not True
        or getattr(sys, "frozen", False)
    ):
        raise ValueError(
            "Explicit SOURCE-only --run-native-test --metadata-only required"
        )
    folder = fixture.paths.real_path(output)
    if (
        folder.parent != ROOT / "work"
        or not folder.name.startswith("toy-cell-position-")
        or folder.exists()
    ):
        raise ValueError("Only a fresh direct work/toy-cell-position-* lab")
    registered_security_module()
    folder.mkdir()
    report = {
        "kind": "owned-unsigned-TOY-cell-position-metadata",
        "status": "blocked",
        "stage": "entry",
        "qualification": False,
        "actual_report_used": False,
        "field_values_inserted_after_source": False,
        "candidate_exports": 0,
        "automatic_retries": 0,
        "owner_nonce": secrets.token_hex(16),
    }
    with (folder / "OWNER-TEST-ONLY.json").open("x", encoding="utf-8") as stream:
        json.dump(
            {
                "test_only": True,
                "nonce": report["owner_nonce"],
                "purpose": report["kind"],
            },
            stream,
            indent=2,
        )
    try:
        native_once(folder, report)
    except BaseException as error:
        report.update(
            status="blocked", error_type=type(error).__name__, error=str(error)[:1000]
        )
    finally:
        report["files_sha256"] = {
            p.name: r.file_hash(p) for p in folder.iterdir() if p.is_file()
        }
        with (folder / "CELL-POSITION-RESULT.json").open(
            "x", encoding="utf-8"
        ) as stream:
            json.dump(report, stream, ensure_ascii=False, indent=2)
    return report


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run-native-test", action="store_true", required=True)
    parser.add_argument("--metadata-only", action="store_true", required=True)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    target = args.output or ROOT / "work" / (
        "toy-cell-position-"
        + datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        + "-"
        + secrets.token_hex(3)
    )
    result = run(
        target, run_native=args.run_native_test, metadata_only=args.metadata_only
    )
    print(json.dumps(result, ensure_ascii=False, indent=2))
    sys.exit(1 if result["status"] == "blocked" else 0)
