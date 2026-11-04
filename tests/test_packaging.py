"""Build recipe/resource tests only. NO build, download, private docs or Hancom."""

import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import build_exe as build
import hwp_build_source as build_source
import hwp_resources as resources
import hwp_review as review
import packaged_smoke_runtime as smoke

ROOT = Path(__file__).resolve().parents[1]


class PackagingTests(unittest.TestCase):
    def test_source_resource_root_never_uses_current_working_directory(self):
        with (
            patch.object(sys, "frozen", False, create=True),
            patch.object(os, "getcwd", return_value="NOT-RESOURCE-ROOT"),
        ):
            self.assertEqual(resources.resource_root(), ROOT)

    def test_frozen_root_uses_only_the_existing_bundle(self):
        with (
            tempfile.TemporaryDirectory() as temp,
            patch.object(sys, "frozen", True, create=True),
            patch.object(sys, "_MEIPASS", temp, create=True),
        ):
            self.assertEqual(resources.resource_root(), Path(temp).resolve())

    def test_missing_frozen_bundle_is_not_replaced_by_repo_or_cwd(self):
        with (
            patch.object(sys, "frozen", True, create=True),
            patch.object(sys, "_MEIPASS", None, create=True),
        ):
            with self.assertRaises(RuntimeError):
                resources.resource_root()

    def test_frozen_bundle_must_be_a_directory(self):
        with tempfile.TemporaryDirectory() as temp:
            file = Path(temp) / "NOT-a-directory"
            file.touch()
            with (
                patch.object(sys, "frozen", True, create=True),
                patch.object(sys, "_MEIPASS", str(file), create=True),
            ):
                with self.assertRaises(RuntimeError):
                    resources.resource_root()

    def test_frozen_native_export_cannot_spawn_itself_or_hancom(self):
        with (
            patch.object(sys, "frozen", True, create=True),
            patch.object(review.subprocess, "run") as spawn,
        ):
            with self.assertRaisesRegex(RuntimeError, "EXE"):
                review.native_export(Path("NOT-OPENED.hwp"), Path("NOT-CREATED.pdf"))
            spawn.assert_not_called()

    def test_asset_allowlist_never_contains_documents_workspaces_or_credentials(self):
        selected = build.assets(ROOT)
        self.assertIn(ROOT / "LICENSE", [source for source, _ in selected])
        for source, destination in selected:
            self.assertTrue(source.is_relative_to(ROOT))
            self.assertNotIn(destination, ("work", "example", ".venv"))
            self.assertNotIn(
                source.suffix.lower(),
                (".hwp", ".hwpx", ".pdf", ".dll", ".zip", ".key", ".pfx"),
            )

    def test_project_license_is_unmodified_agpl_only(self):
        self.assertEqual(resources.APP_LICENSE, "AGPL-3.0-only")
        self.assertEqual(
            build_source.sha256(ROOT / "LICENSE"),
            "0d96a4ff68ad6d4b6f1f30f713b18d5184912ba8dd389f86aa7710db079abcb0",
        )

    def test_renderer_source_pins_match_installed_engine_and_notice(self):
        data = json.loads(
            (ROOT / "packaging/third-party-sources.json").read_text(encoding="utf-8")
        )
        import pymupdf

        self.assertEqual(pymupdf.VersionBind, data["pymupdf"]["version"])
        self.assertEqual(pymupdf.VersionFitz, data["mupdf"]["version"])
        for name in ("pymupdf", "mupdf"):
            self.assertRegex(data[name]["sha256"], r"^[a-f0-9]{64}$")
            self.assertTrue(data[name]["url"].startswith("https://"))
        self.assertEqual(
            build_source.sha256(ROOT / data["mupdf_notices"]["path"]),
            data["mupdf_notices"]["sha256"],
        )

    def test_missing_allowed_asset_blocks_build_instead_of_collecting_other_files(self):
        with tempfile.TemporaryDirectory() as temp:
            with self.assertRaises(ValueError):
                build.assets(Path(temp))

    def test_clean_execution_environment_removes_python_tk_and_venv_paths(self):
        with patch.dict(
            os.environ,
            {
                "PYTHONHOME": "SECRET-PYTHON",
                "PYTHONPATH": "SECRET-REPO",
                "TCL_LIBRARY": "SECRET-TCL",
                "TK_LIBRARY": "SECRET-TK",
                "VIRTUAL_ENV": "SECRET-VENV",
                "PATH": "SECRET-PYTHON-PATH",
            },
            clear=False,
        ):
            cleaned = build.clean_environment()
        for name in (
            "PYTHONHOME",
            "PYTHONPATH",
            "TCL_LIBRARY",
            "TK_LIBRARY",
            "VIRTUAL_ENV",
        ):
            self.assertNotIn(name, cleaned)
        self.assertNotIn("SECRET", cleaned["PATH"])

    def test_packaged_self_test_never_overwrites_an_existing_owner_directory(self):
        with tempfile.TemporaryDirectory() as temp:
            folder = Path(temp)
            sentinel = folder / "OWNER-DATA.txt"
            sentinel.write_text("NEVER TOUCH", encoding="utf-8")
            with self.assertRaises(FileExistsError):
                smoke.run(folder)
            self.assertEqual(sentinel.read_text(encoding="utf-8"), "NEVER TOUCH")
            self.assertFalse((folder / "SELF-TEST-ONLY.json").exists())

    def test_incomplete_frozen_self_test_is_not_promoted_as_pass(self):
        with tempfile.TemporaryDirectory() as temp:
            exe = Path(temp) / "SYNTHETIC-NOT-AN-EXE"
            exe.touch()
            target = Path(temp) / "copy"

            def fake_run(*args, **kwargs):
                output = target / "새 가상 시험 결과"
                output.mkdir()
                (output / "SELF-TEST-ONLY.json").write_text(
                    json.dumps({"status": "passed", "frozen": False}), encoding="utf-8"
                )
                return type("Completed", (), {"returncode": 0})()

            with patch.object(build.subprocess, "run", side_effect=fake_run):
                with self.assertRaisesRegex(RuntimeError, "Incomplete"):
                    build.frozen_self_test(exe, target)

    def test_frozen_pi_boundaries_never_start_sdk_or_document_process(self):
        import hwp_desktop as desktop
        import hwp_review as engine

        with tempfile.TemporaryDirectory() as temp:
            workspace = Path(temp).resolve() / "SYNTHETIC-PI"
            smoke.fixture(workspace, engine)
            before = {
                str(p.relative_to(workspace)): build_source.sha256(p)
                for p in workspace.rglob("*")
                if p.is_file()
            }
            with patch.object(
                engine.subprocess,
                "Popen",
                side_effect=AssertionError("No SDK/Native child permitted"),
            ):
                result = smoke.pi_boundary_checks(desktop, engine, workspace)
            self.assertTrue(all(value is True for value in result.values()))
            self.assertEqual(
                before,
                {
                    str(p.relative_to(workspace)): build_source.sha256(p)
                    for p in workspace.rglob("*")
                    if p.is_file()
                },
            )

    def test_frozen_report_cannot_omit_or_forge_pi_boundary_receipt(self):
        from test_build_candidate import synthetic_frozen_report

        for payload in ({}, {"no_sdk_profile_or_model": True}):
            report = synthetic_frozen_report()
            report["pi_checks"] = payload
            with self.assertRaisesRegex(RuntimeError, "Pi consent"):
                build.validate_frozen_report(report)
        report = synthetic_frozen_report()
        report["pi_checks"]["no_sdk_profile_or_model"] = 1
        with self.assertRaisesRegex(RuntimeError, "Pi consent"):
            build.validate_frozen_report(report)

    def test_frozen_native_ui_closure_cannot_be_omitted_or_forged(self):
        from test_build_candidate import synthetic_frozen_report

        for change in (
            lambda report: report.pop("native_ui_payload"),
            lambda report: report["native_ui_payload"].update(verified=1),
            lambda report: report["native_ui_payload"].update(runtime_bundled=True),
            lambda report: report["native_ui_payload"].update(ui_exercised=True),
            lambda report: report["native_ui_payload"]["files_sha256"].update({"outside.dll": "b" * 64}),
            lambda report: report["native_ui_payload"]["runtime_requirement"].update(roll_forward="Major"),
        ):
            report = synthetic_frozen_report()
            change(report)
            with self.assertRaises(RuntimeError):
                build.validate_frozen_report(report)

    def test_sha256_hashes_file_bytes(self):
        with tempfile.TemporaryDirectory() as temp:
            file = Path(temp) / "fixture.txt"
            file.write_bytes(b"abc")
            self.assertEqual(
                build_source.sha256(file),
                "ba7816bf8f01cfea414140de5dae2223b00361a396177a9cb410ff61f20015ad",
            )

    def test_windows_version_data_does_not_request_admin_or_invent_a_publisher(self):
        with tempfile.TemporaryDirectory() as temp:
            file = Path(temp) / "version.txt"
            build.version_file(file)
            text = file.read_text(encoding="utf-8")
            self.assertIn(resources.APP_VERSION, text)
            self.assertNotIn("CompanyName", text)
            self.assertNotIn("Administrator", text)


if __name__ == "__main__":
    unittest.main()
