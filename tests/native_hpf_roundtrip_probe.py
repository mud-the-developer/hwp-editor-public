"""SOURCE-only NEW unsigned TOY: two fixed no-edit HPF provenance arms.

HWP->HWPX, then a NEW copy of that HWPX->HWPX. Observations are NOT
preservation/engine qualification, document approval, repair or retry.
"""

import argparse
from datetime import datetime, timezone
from pathlib import Path
import secrets
import shutil
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
import native_contract_smoke as creator
import native_save_order_probe as metadata
from hwp_native_owner import owned_session
from native_blank_metadata_probe import hwp_inventory

r = metadata.r


def code_hashes():
    return {
        **metadata.code_hashes(),
        "tests/native_hpf_roundtrip_probe.py": r.file_hash(Path(__file__)),
        "tests/native_contract_smoke.py": r.file_hash(Path(creator.__file__)),
    }


def unchanged(lab, ledger, code):
    metadata.unchanged(lab, ledger)
    if code_hashes() != code:
        raise ValueError("Diagnostic code changed; no continuation")


def snapshot(app, path, expected=None):
    metadata.identity(app, path)
    text = app.GetTextFile("TEXT", "")
    if (
        type(text) is not str
        or text.count(creator.MARKER) != 1
        or text.count(creator.TABLE_OLD) != 1
        or expected is not None
        and text != expected
    ):
        raise ValueError("Exact own TOY marker/text snapshot changed")
    pages = app.PageCount
    if type(pages) is not int or pages != 1 or app.GetTextFile("TEXT", "") != text:
        raise ValueError("Typed own TOY page/text snapshot changed")
    metadata.identity(app, path)
    return text, {
        "page_count": pages,
        "text_sha256": r.digest(text.encode("utf-8")),
        "modified": False,
        "document": path.name,
    }


def run(output, *, run_native=False, toy_only=False):
    if run_native is not True or toy_only is not True or getattr(sys, "frozen", False):
        raise ValueError("Literal SOURCE-only --run-native-test --toy-only required")
    lab = metadata.fixture.paths.real_path(output)
    if (
        lab != lab.resolve()
        or lab.parent != ROOT / "work"
        or not lab.name.startswith("toy-hpf-roundtrip-")
        or lab.exists()
    ):
        raise ValueError("Only a NEW direct owned work/toy-hpf-roundtrip-* lab")
    if hwp_inventory() != []:
        raise RuntimeError("Existing or unverified Hwp processes; no diagnostic")
    code = code_hashes()
    metadata.security.registered_security_module()  # READ ONLY; no installation.
    lab.mkdir()
    report = {
        "kind": "fixed-own-TOY-HWP-HWPX-no-edit-provenance",
        "status": "blocked",
        "qualification": False,
        "preservation_success_claimed": False,
        "actual_report_used": False,
        "approval_or_apply_invoked": False,
        "automatic_retries": 0,
        "maximum_fresh_instances": 3,
        "code_sha256": code,
        "arms": [],
    }
    ledger = {}
    try:
        source_dir = lab / "source"
        source = creator.create_source(source_dir, 3)
        if source != source_dir / "TOY-ONLY-unsigned-contract.hwp":
            raise ValueError("Creator returned an unexpected source")
        source = metadata.local_path(lab, source)
        proof_path = metadata.local_path(lab, source_dir / "TOY-SOURCE-CREATION.json")
        proof = r.read_json(proof_path)
        ownership = proof.get("ownership", {})
        if (
            proof.get("status") != "owned_source_created_not_document_qualified"
            or proof.get("qualification") is not False
            or proof.get("source_sha256") != r.file_hash(source)
            or ownership.get("status") != "owned_exit_confirmed"
            or ownership.get("exit_confirmed") is not True
        ):
            raise ValueError("Exact owned source creation/exit proof required")
        for path in (source, proof_path, source_dir / "TOY-ONLY-authoring-seed.hwp"):
            path = metadata.local_path(lab, path)
            ledger[path] = r.file_hash(path)
        report["source_creation_proof_sha256"] = ledger[proof_path]
        baseline = None
        expected_text = None
        for index, fmt in enumerate(("HWP", "HWPX"), 1):
            unchanged(lab, ledger, code)
            if hwp_inventory() != []:
                raise RuntimeError("Hwp process state prevents the next fixed arm")
            copied = metadata.local_path(
                lab, lab / f"arm-{index}-input.{fmt.lower()}", fresh=True
            )
            target = metadata.local_path(
                lab, lab / f"arm-{index}-output.hwpx", fresh=True
            )
            with source.open("rb") as original, copied.open("xb") as stream:
                shutil.copyfileobj(original, stream, length=1024 * 1024)
            if r.file_hash(copied) != ledger[source]:
                raise ValueError("Exact diagnostic input copy changed")
            ledger[copied] = r.file_hash(copied)
            arm = {
                "arm": index,
                "open_format": fmt,
                "save_format": "HWPX",
                "input_sha256": ledger[copied],
            }
            report["arms"].append(arm)
            with owned_session((copied, target)) as app:
                arm["ownership"] = app.ownership
                arm["security_module"] = metadata.register_existing(app)
                if not app.Open(str(copied), fmt, "versionwarning:false"):
                    raise RuntimeError("Own diagnostic copy rejected; no fallback")
                text, arm["before"] = snapshot(app, copied, expected_text)
                unchanged(lab, ledger, code)
                metadata.local_path(lab, target, fresh=True)
                if not app.SaveAs(str(target), "HWPX", ""):
                    raise RuntimeError("Fixed NEW HWPX export failed")
                metadata.local_path(lab, target)
                _, arm["after"] = snapshot(app, target, text)
                unchanged(lab, ledger, code)
            if (
                arm["ownership"].get("status") != "owned_exit_confirmed"
                or arm["ownership"].get("exit_confirmed") is not True
                or hwp_inventory() != []
            ):
                raise RuntimeError("Own exit unconfirmed; no next arm")
            arm["output_sha256"] = r.file_hash(target)
            arm["hpf"] = metadata.observe_spine(target, baseline)
            baseline = baseline or arm["hpf"]
            ledger[target] = arm["output_sha256"]
            source, expected_text = target, text
        unchanged(lab, ledger, code)
        report["status"] = "diagnosis_completed_not_qualified"
        return report
    except BaseException as error:
        report["error"] = type(error).__name__ + ": " + str(error)[:1500]
        raise
    finally:
        report["immutable_file_sha256"] = {
            str(path.relative_to(lab)): digest for path, digest in ledger.items()
        }
        metadata.new_json(lab, "HPF-ROUNDTRIP-DIAGNOSIS.json", report)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run-native-test", action="store_true", required=True)
    parser.add_argument("--toy-only", action="store_true", required=True)
    parser.add_argument(
        "--output",
        type=Path,
        default=ROOT
        / "work"
        / (
            "toy-hpf-roundtrip-"
            + datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
            + "-"
            + secrets.token_hex(4)
        ),
    )
    args = parser.parse_args()
    run(args.output, run_native=args.run_native_test, toy_only=args.toy_only)
