"""Synthetic staged-build gates only. No build, Hancom, real reports or promotion."""

import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch
import zipfile

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from scripts import build_exe as b
import hwp_build_source as build_source
import hwp_install_bundle as install_bundle


def synthetic_frozen_report():
    report = {
        "status": "passed",
        "version": b.APP_VERSION,
        "frozen": True,
        "native_export_blocked": True,
        "workspace_unchanged": True,
        "runtime_modules_bundled": True,
        "SYNTHETIC_TEST_ONLY": True,
        "native_ui_payload": {
            "verified": True, "framework_dependent": True, "runtime_bundled": False,
            "runtime_requirement": dict(b.native_payload.RUNTIME_REQUIREMENT),
            "files_sha256": {name: "a" * 64 for name in b.native_payload.PAYLOAD_FILES},
            "ui_exercised": False,
        },
    }
    keys = {
        "report_checks": (
            "unsigned_inputs_prepared",
            "runtime_and_scope_bound",
            "raw_frozen_template_blocked",
            "owned_desktop_session_ready",
            "unpermitted_draft_blocked",
            "document_workspace_ready",
            "unpermitted_request_blocked",
            "no_model_native_or_permission",
        ),
        "pi_checks": (
            "ui_consent_controls",
            "stream_inactive_without_send",
            "token_origin_required",
            "closed_session_and_scope",
            "no_sdk_profile_or_model",
            "no_document_approval_or_apply",
        ),
        "workflow_checks": (
            "note_hash_bound",
            "evidence_snapshot",
            "changed_evidence_blocked",
            "no_document_or_submission",
        ),
        "debug_checks": (
            "callback_visible_without_console",
            "wheel_preserves_contract",
            "failed_window_retains_exact_url",
            "source_failure_revokes_ready",
            "shutdown_bounded_without_forced_close",
        ),
        "project_checks": (
            "empty_folder",
            "exclusive_copy",
            "import_not_prepared",
            "declined_prepare_no_native",
        ),
        "usability_checks": (
            "advanced_collapsed",
            "document_picker_copy",
            "single_next_action",
            "progress_nonterminal",
            "declined_stop_noop",
            "safe_stop_no_cleanup",
        ),
        "library_checks": (
            "readonly_listing",
            "filtered_states",
            "revalidated_open",
            "saved_notes_export",
            "no_overwrite",
        ),
        "text_proposal_checks": (
            "ui_available",
            "unapproved_save",
            "strict_block_no_fallback",
            "conflict_preserves_plan",
            "source_unchanged",
        ),
        "text_apply_checks": (
            "ui_apply_control",
            "unapproved_never_starts",
            "unsafe_request_denied",
            "invalid_worker_cannot_export",
            "immutable_inputs",
        ),
        "recovery_checks": (
            "roundtrip_data_only",
            "cas_conflict",
            "clear_keeps_history",
            "no_document_or_approval",
            "ui_control",
        ),
        "result_download_checks": (
            "ui_control",
            "token_required",
            "verified_snapshot_matches",
            "drift_blocked_no_reseal",
            "no_approval_or_native",
        ),
    }
    report.update({name: {key: True for key in group} for name, group in keys.items()})
    return report


class CandidateTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve()
        self.release = self.root / "build" / "SYNTHETIC" / "checked-release"
        self.release.mkdir(parents=True)
        self.patches = [
            patch.object(b, "ROOT", self.root),
            patch.object(b, "SOURCE_FILES", ("scripts/app.py", *install_bundle.ROOT_INPUTS.values())),
            patch.object(b, "ASSETS", ("web/view.html",)),
            patch.object(b, "LEGAL_FILES", ()),
        ]
        for item in self.patches:
            item.start()
            self.addCleanup(item.stop)
        for name in (*b.SOURCE_FILES, *b.ASSETS):
            file = self.root / name
            file.parent.mkdir(parents=True, exist_ok=True)
            file.write_bytes(b"SYNTHETIC INPUT ONLY")
        self.exe = self.release / "HWPReview.exe"
        self.exe.write_bytes(b"SYNTHETIC NOT EXE")
        self.meta = {
            "version": b.APP_VERSION,
            "redistribution_ready": False,
            "executable_sha256": build_source.sha256(self.exe),
            "source_sha256": {
                name: build_source.sha256(self.root / name) for name in b.SOURCE_FILES
            },
            "assets_sha256": {
                name: build_source.sha256(self.root / name) for name in b.ASSETS
            },
            "input_sha256": {
                name: build_source.sha256(self.root / name)
                for name in (*b.SOURCE_FILES, *b.ASSETS)
            },
            "self_test": synthetic_frozen_report(),
            "artifact_set": f"releases/{b.APP_VERSION}/SYNTHETIC",
            "qualified_native_schemas": [3],
        }
        self.bundle()
        dist = self.root / "dist"
        dist.mkdir()
        (dist / "HWPReview.exe").write_bytes(b"LAST WORKING SYNTHETIC")

    def bundle(self):
        source_archive = self.release / f"HWPReview-source-{b.APP_VERSION}.zip"
        if not source_archive.exists():
            build_source.write_source_archive(
                self.root, self.meta["input_sha256"], source_archive, version=b.APP_VERSION
            )
        self.meta["source_archive"] = {
            "file": source_archive.name,
            "sha256": build_source.sha256(source_archive),
            "bytes": source_archive.stat().st_size,
        }
        for name, source_name in install_bundle.ROOT_INPUTS.items():
            path = self.release / name
            if not path.exists():
                path.write_bytes((self.root / source_name).read_bytes())
        notices = self.release / "licenses"
        notices.mkdir(exist_ok=True)
        (notices / "TOY-NOTICE.txt").write_bytes(b"SYNTHETIC NOTICE")
        self.meta["licenses_sha256"] = {
            path.name: build_source.sha256(path) for path in notices.iterdir()
        }
        (self.release / "BUILD.json").write_text(json.dumps(self.meta), encoding="utf-8")
        files = {
            name: {"sha256": build_source.sha256(self.release / name), "bytes": (self.release / name).stat().st_size}
            for name in sorted(install_bundle.payload_names(b.APP_VERSION, self.meta["licenses_sha256"]))
        }
        manifest = {
            "contract": install_bundle.CONTRACT, "version": b.APP_VERSION, "architecture": "x64",
            "files": files, "redistribution_ready": False, "code_signed": False,
        }
        (self.release / install_bundle.MANIFEST).write_text(json.dumps(manifest), encoding="utf-8")
        members = sorted([*files, install_bundle.MANIFEST])
        (self.release / "SHA256SUMS.txt").write_text(
            "".join(build_source.sha256(self.release / name) + "  " + name + "\n" for name in members),
            encoding="utf-8",
        )
        archive = self.release / f"HWPReview-win-x64-{b.APP_VERSION}.zip"
        with zipfile.ZipFile(archive, "w") as package:
            for name in [*members, "SHA256SUMS.txt"]:
                package.write(self.release / name, name)
        (self.release / (archive.name + ".sha256")).write_text(
            build_source.sha256(archive) + "  " + archive.name + "\n", encoding="utf-8"
        )

    def blocked(self, expected=Exception):
        with (
            patch.object(b, "validate_native_evidence") as native,
            patch.object(b, "promote_release") as promote,
        ):
            with self.assertRaises(expected):
                b.promote_checked_candidate(self.release, self.root / "NOT-A-PROOF")
            native.assert_not_called()
            promote.assert_not_called()
        self.assertEqual(
            (self.root / "dist/HWPReview.exe").read_bytes(), b"LAST WORKING SYNTHETIC"
        )

    def test_exact_source_archive_relocates_without_private_tree(self):
        archive = self.release / self.meta["source_archive"]["file"]
        private = self.root / "work/private-report.hwpx"
        private.parent.mkdir()
        private.write_bytes(b"PRIVATE SYNTHETIC SENTINEL, NEVER PACKAGE")
        record = build_source.verify_source_archive(
            archive, self.meta["input_sha256"], version=b.APP_VERSION
        )
        self.assertEqual(record["inputs_sha256"], self.meta["input_sha256"])
        relocated = self.root / "다른 PC 공백 경로"
        relocated.mkdir()
        with zipfile.ZipFile(archive) as package:
            self.assertNotIn("work/private-report.hwpx", package.namelist())
            package.extractall(relocated)  # Exact allowlisted members verified above.
        for name, expected in self.meta["input_sha256"].items():
            self.assertEqual(build_source.sha256(relocated / name), expected)
        with self.assertRaises(FileExistsError):
            build_source.write_source_archive(
                self.root, self.meta["input_sha256"], archive, version=b.APP_VERSION
            )

    def test_source_archive_tamper_blocks_before_native_or_promotion(self):
        archive = self.release / self.meta["source_archive"]["file"]
        archive.write_bytes(archive.read_bytes() + b"CHANGED")
        self.blocked(ValueError)

    def test_source_archive_uses_and_checks_the_explicit_version(self):
        archive = self.root / "EXPLICIT-VERSION.zip"
        version = "1.2.3-rc.7"
        build_source.write_source_archive(
            self.root, self.meta["input_sha256"], archive, version=version
        )
        record = build_source.verify_source_archive(
            archive, self.meta["input_sha256"], version=version
        )
        self.assertEqual(record["version"], version)
        with self.assertRaises(ValueError):
            build_source.verify_source_archive(
                archive, self.meta["input_sha256"], version=b.APP_VERSION
            )

    def test_source_archive_rejects_repacked_content_or_extra_members(self):
        archive = self.release / self.meta["source_archive"]["file"]
        with zipfile.ZipFile(archive) as package:
            original = {name: package.read(name) for name in package.namelist()}
        for change in ("content", "extra"):
            members = dict(original)
            if change == "content":
                members[b.SOURCE_FILES[0]] = b"CHANGED WITH A VALID ZIP CRC"
            else:
                members["work/private-report.hwpx"] = b"NOT AN ALLOWED BUILD INPUT"
            repacked = self.root / f"REPACKED-{change}.zip"
            with zipfile.ZipFile(repacked, "x") as package:
                for name, data in members.items():
                    package.writestr(name, data)
            with self.subTest(change=change), self.assertRaises(ValueError):
                build_source.verify_source_archive(
                    repacked, self.meta["input_sha256"], version=b.APP_VERSION
                )

    def test_unsafe_source_member_rejected_before_read(self):
        with patch.object(
            build_source,
            "sha256",
            side_effect=AssertionError("Do not read outside snapshot"),
        ):
            for name in ("../secret", "/absolute", "C:relative", "dir/../secret"):
                with self.subTest(name=name), self.assertRaises(ValueError):
                    build_source.write_source_archive(
                        self.root,
                        {name: "0" * 64},
                        self.root / "NOT-CREATED.zip",
                        version=b.APP_VERSION,
                    )
        self.assertFalse((self.root / "NOT-CREATED.zip").exists())

    def test_incomplete_report_runtime_boundary_cannot_claim_frozen_pass(self):
        for value in ({}, {"unsigned_inputs_prepared": True}):
            record = synthetic_frozen_report()
            record["report_checks"] = value
            with self.assertRaisesRegex(RuntimeError, "report runtime"):
                b.validate_frozen_report(record)

    def test_modified_exe_blocks_before_any_promotion(self):
        self.exe.write_bytes(b"CHANGED")
        self.blocked(ValueError)

    def test_changed_source_or_asset_does_not_replace_working_distribution(self):
        (self.root / "web/view.html").write_bytes(b"CHANGED")
        self.blocked(RuntimeError)

    def test_incoherent_zip_cannot_pass_by_rewriting_its_checksum(self):
        archive = self.release / f"HWPReview-win-x64-{b.APP_VERSION}.zip"
        with zipfile.ZipFile(archive, "w") as package:
            package.writestr("HWPReview.exe", b"DIFFERENT EXE")
            package.writestr(
                "BUILD.json",
                (self.release / "BUILD.json").read_bytes(),
            )
        (self.release / (archive.name + ".sha256")).write_text(
            build_source.sha256(archive) + "  " + archive.name + "\n", encoding="utf-8"
        )
        self.blocked(ValueError)

    def test_incomplete_frozen_guard_report_is_never_promoted(self):
        self.meta["self_test"]["text_apply_checks"].pop("immutable_inputs")
        self.bundle()
        self.blocked(RuntimeError)

    def test_failed_native_evidence_keeps_candidate_and_previous_exe(self):
        with (
            patch.object(
                b,
                "validate_native_evidence",
                side_effect=ValueError("SYNTHETIC FAILED NATIVE"),
            ),
            patch.object(b, "promote_release") as promote,
        ):
            with self.assertRaises(ValueError):
                b.promote_checked_candidate(
                    self.release,
                    self.root / "SYNTHETIC-PROOF",
                    self.root / "SYNTHETIC-LAYOUT",
                )
            promote.assert_not_called()
        self.assertTrue(self.release.exists())
        self.assertEqual(
            (self.root / "dist/HWPReview.exe").read_bytes(), b"LAST WORKING SYNTHETIC"
        )

    def test_fill_candidate_cannot_promote_with_legacy_text_and_layout_evidence_only(
        self,
    ):
        self.meta["qualified_native_schemas"] = [3, 4]
        self.bundle()
        with (
            patch.object(b, "validate_native_evidence") as native,
            patch.object(b, "promote_release") as promote,
        ):
            with self.assertRaises(ValueError):
                b.promote_checked_candidate(
                    self.release,
                    self.root / "SYNTHETIC-TEXT",
                    self.root / "SYNTHETIC-LAYOUT",
                )
            native.assert_not_called()
            promote.assert_not_called()
        self.assertEqual(
            (self.root / "dist/HWPReview.exe").read_bytes(), b"LAST WORKING SYNTHETIC"
        )

    def test_validated_staged_promotion_binds_one_complete_set(self):
        # Native validation is deliberately a MOCK in this isolated BUILD test;
        # actual acceptance uses the new unsigned TOY lab with all real gates.
        with patch.object(
            b, "validate_native_evidence", return_value={"SYNTHETIC_ONLY": True}
        ) as native:
            reply = b.promote_checked_candidate(
                self.release,
                self.root / "SYNTHETIC-PROOF",
                self.root / "SYNTHETIC-LAYOUT",
            )
            self.assertEqual(native.call_count, 2)
            native.assert_called_with(
                self.root / "SYNTHETIC-LAYOUT", self.exe, schema=3
            )
        self.assertTrue(reply["local_promoted"])
        self.assertFalse(reply["published"])
        self.assertEqual(
            (self.root / "dist/HWPReview.exe").read_bytes(), b"SYNTHETIC NOT EXE"
        )
        self.assertEqual(
            (self.root / "dist/HWPReview.exe").read_bytes(),
            (
                self.root / f"dist/releases/{b.APP_VERSION}/SYNTHETIC/HWPReview.exe"
            ).read_bytes(),
        )

    def test_missing_or_failed_layout_evidence_cannot_promote_text_only_pass(self):
        with (
            patch.object(b, "validate_native_evidence", return_value={}) as native,
            patch.object(b, "promote_release") as promote,
        ):
            with self.assertRaisesRegex(ValueError, "Both schema1 and schema3"):
                b.promote_checked_candidate(self.release, self.root / "SYNTHETIC-TEXT")
            native.assert_not_called()
            promote.assert_not_called()
        with (
            patch.object(
                b,
                "validate_native_evidence",
                side_effect=[{}, ValueError("SYNTHETIC LAYOUT BLOCKED")],
            ) as native,
            patch.object(b, "promote_release") as promote,
        ):
            with self.assertRaisesRegex(ValueError, "LAYOUT BLOCKED"):
                b.promote_checked_candidate(
                    self.release,
                    self.root / "SYNTHETIC-TEXT",
                    self.root / "SYNTHETIC-LAYOUT",
                )
            self.assertEqual(native.call_count, 2)
            promote.assert_not_called()
        self.assertTrue(self.release.exists())
        self.assertEqual(
            (self.root / "dist/HWPReview.exe").read_bytes(), b"LAST WORKING SYNTHETIC"
        )

    def test_layout_evidence_requires_real_frozen_schema3_before_workspace_read(self):
        import hwp_review as r

        lab = self.root / "work/toy-contract-SYNTHETIC"
        lab.mkdir(parents=True)
        proof = lab / "TOY-ONLY-contract-verification.json"
        fields = (
            "test_only_not_deliverable",
            "human_visual_review_required",
            "actual_parent_engine_and_http",
            "test_consent_only_new_unsigned_fixture",
            "source_unchanged",
            "approval_and_baselines_unchanged",
            "duplicate_blocked",
            "unapproved_blocked_before_native",
            "stale_plan_blocked_before_native",
            "download_bytes_verified",
        )
        data: dict[str, object] = dict.fromkeys(fields, True)
        data.update(
            real_report_modified=False,
            status="checks_passed_needs_human_visual_review",
            actual_worker="frozen",
            executable_sha256=build_source.sha256(self.exe),
            schema=3,
        )
        for name, value in (
            ("schema", True),
            ("schema", 2),
            ("schema", 4),
            ("actual_worker", "source"),
            ("executable_sha256", "wrong"),
            ("download_bytes_verified", False),
            ("real_report_modified", True),
        ):
            proof.write_text(json.dumps({**data, name: value}), encoding="utf-8")
            with patch.object(r, "verify_baseline") as baseline:
                with self.assertRaises(ValueError):
                    b.validate_native_evidence(proof, self.exe, schema=3)
                baseline.assert_not_called()
        for schema in (True, 2, 4):
            with self.assertRaises(ValueError):
                b.validate_native_evidence(proof, self.exe, schema=schema)

    def test_source_only_failed_or_foreign_native_proof_is_rejected_without_opening_workspace(
        self,
    ):
        lab = self.root / "work/toy-text-apply-SYNTHETIC"
        lab.mkdir(parents=True)
        proof = lab / "TOY-ONLY-text-apply-verification.json"
        data: dict[str, object] = {
            name: True
            for name in (
                "test_only_not_deliverable",
                "human_visual_review_required",
                "actual_parent_engine_and_http",
                "test_consent_only_new_unsigned_fixture",
                "source_unchanged",
                "approval_and_baselines_unchanged",
                "duplicate_blocked",
                "unapproved_blocked_before_native",
                "stale_plan_blocked_before_native",
            )
        }
        data.update(
            real_report_modified=False,
            status="checks_passed_needs_human_visual_review",
            actual_worker="frozen",
            executable_sha256=build_source.sha256(self.exe),
        )
        for field, value in (
            ("status", "blocked"),
            ("actual_worker", "source"),
            ("executable_sha256", "wrong"),
            ("real_report_modified", True),
            ("source_unchanged", False),
        ):
            proof.write_text(json.dumps({**data, field: value}), encoding="utf-8")
            with self.assertRaises(ValueError):
                b.validate_native_evidence(proof, self.exe)

    def test_foreign_lab_path_is_not_a_native_certificate(self):
        lab = self.root / "work/OWNER-REPORT"
        lab.mkdir(parents=True)
        proof = lab / "TOY-ONLY-text-apply-verification.json"
        proof.write_text("{}", encoding="utf-8")
        with self.assertRaises(ValueError):
            b.validate_native_evidence(proof, self.exe)


if __name__ == "__main__":
    unittest.main()
