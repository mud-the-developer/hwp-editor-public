"""Folder-first tests with SYNTHETIC files/mock COM only. NOT Native proof."""

from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace, ModuleType
import unittest
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import hwp_project as p
import hwp_review as r
import hwp_prepare_native as native
import hwp_security as security
from test_desktop import fixture, snapshot, DesktopWidgetTests
from test_com_cache import cached_com_modules
from test_native_owner import fake_owned_session


def source_path(project) -> Path:
    source = project.source
    if source is None:
        raise AssertionError("Owned staged fixture source missing")
    return source


class ProjectTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve() / "한글 빈 작업 폴더"
        self.root.mkdir()
        self.outside = Path(self.temp.name).resolve() / "SYNTHETIC-INPUT.hwp"
        self.outside.write_bytes(b"SYNTHETIC HWP ONLY; NOT REAL")
        self.audit = patch.object(
            r, "hwp_audit", side_effect=lambda path: {"sha256": r.file_hash(path)}
        )
        self.audit.start()
        self.addCleanup(self.audit.stop)

    def staged(self):
        return p.import_hwp(self.root, self.outside)

    def preparing(self):
        project = self.staged()
        project.workspace.mkdir()
        r.write_json(
            project.workspace / "preparing.json",
            {
                "source": str(source_path(project)),
                "source_audit": {"sha256": r.file_hash(source_path(project))},
            },
        )
        return project

    def test_empty_folder_check_is_read_only_and_has_no_manifest_requirement(self):
        before = snapshot(self.root)
        self.assertEqual(p.scan_folder(self.root).state, "empty")
        self.assertEqual(snapshot(self.root), before)

    def test_import_is_exclusive_exact_copy_not_move_or_native_execution(self):
        original = self.outside.read_bytes()
        with patch.object(p.subprocess, "run") as process:
            project = self.staged()
            process.assert_not_called()
        self.assertEqual(project.state, "staged")
        self.assertEqual(source_path(project).read_bytes(), original)
        self.assertEqual(self.outside.read_bytes(), original)
        self.assertFalse(project.workspace.exists())
        with self.assertRaises(ValueError):
            p.import_hwp(self.root, self.outside)
        self.assertEqual(source_path(project).read_bytes(), original)

    def test_manually_placed_hwp_is_detected_without_conversion(self):
        (self.root / "MANUAL.hwp").write_bytes(b"SYNTHETIC ONLY")
        self.assertEqual(p.scan_folder(self.root).state, "staged")
        self.assertFalse((self.root / p.REVIEW_DIR).exists())

    def test_multiple_hwp_inputs_are_not_guessed_or_deleted(self):
        (self.root / "ONE.hwp").touch()
        (self.root / "TWO.hwp").touch()
        before = snapshot(self.root)
        with self.assertRaises(ValueError):
            p.scan_folder(self.root)
        self.assertEqual(snapshot(self.root), before)

    def test_failed_preparation_directory_is_never_reused_or_cleared(self):
        project = self.staged()
        project.workspace.mkdir()
        (project.workspace / "FAILED-SYNTHETIC.txt").write_text("KEEP")
        before = snapshot(self.root)
        with self.assertRaises(ValueError):
            p.scan_folder(self.root)
        self.assertEqual(snapshot(self.root), before)

    def test_legacy_and_nested_prepared_workspaces_remain_supported(self):
        fixture(self.root / p.REVIEW_DIR)
        project = p.scan_folder(self.root)
        self.assertEqual(project.state, "ready")
        self.assertEqual(project.workspace, self.root / p.REVIEW_DIR)
        self.assertEqual(p.scan_folder(project.workspace).workspace, project.workspace)

    def test_wrong_extension_or_failed_audit_does_not_copy_anything(self):
        wrong = Path(self.temp.name) / "NOT-HWP.hwpx"
        wrong.touch()
        with self.assertRaises(ValueError):
            p.import_hwp(self.root, wrong)
        with patch.object(
            r, "hwp_audit", side_effect=ValueError("SYNTHETIC unsupported flags")
        ):
            with self.assertRaises(ValueError):
                p.import_hwp(self.root, self.outside)
        self.assertEqual(snapshot(self.root), {})

    def test_missing_security_blocks_before_workspace_creation_or_native_call(self):
        project = self.staged()
        before = snapshot(self.root)
        with (
            patch.object(
                security,
                "registered_security_module",
                side_effect=RuntimeError("SYNTHETIC missing module"),
            ),
            patch.object(r, "prepare") as prepare,
        ):
            with self.assertRaises(RuntimeError):
                p.prepare_project(self.root)
            prepare.assert_not_called()
        self.assertFalse(project.workspace.exists())
        self.assertEqual(snapshot(self.root), before)

    def test_preparation_wires_only_the_existing_engine_and_three_phase_exporter(self):
        project = self.staged()

        def prepare(source, workspace, *, exporter):
            self.assertEqual(source, source_path(project))
            self.assertIsInstance(exporter, p.BaselineExporter)
            fixture(workspace)
            exporter.phase = 3

        with (
            patch.object(security, "registered_security_module"),
            patch.object(r, "prepare", side_effect=prepare) as call,
        ):
            result = p.prepare_project(self.root)
            call.assert_called_once()
        self.assertEqual(result.state, "ready")
        self.assertFalse((project.workspace / "approved-plan.json").exists())

    def test_worker_paths_reject_changed_source_existing_outputs_and_ready_manifest(
        self,
    ):
        project = self.preparing()
        source, outputs, reply = p.step_paths(project.workspace, 0)
        self.assertEqual(source, source_path(project))
        self.assertEqual(outputs[0].name, "baseline.pdf")
        outputs[0].touch()
        with self.assertRaises(FileExistsError):
            p.step_paths(project.workspace, 0)
        outputs[0].unlink()
        reply.touch()
        with self.assertRaises(FileExistsError):
            p.step_paths(project.workspace, 0)
        reply.unlink()
        (project.workspace / "manifest.json").write_text("SYNTHETIC TEST ONLY")
        with self.assertRaises(ValueError):
            p.step_paths(project.workspace, 0)
        (project.workspace / "manifest.json").unlink()
        source_path(project).write_bytes(b"CHANGED SYNTHETIC INPUT")
        with self.assertRaises(ValueError):
            p.step_paths(project.workspace, 0)

    def test_worker_rejects_source_outside_project_and_invalid_phase(self):
        project = self.preparing()
        r.write_json(
            project.workspace / "preparing.json",
            {
                "source": str(self.outside),
                "source_audit": {"sha256": r.file_hash(self.outside)},
            },
        )
        with self.assertRaises(ValueError):
            p.step_paths(project.workspace, 0)
        with self.assertRaises(ValueError):
            p.step_paths(project.workspace, 3)

    def test_exporter_rejects_arbitrary_outputs_before_process_launch(self):
        project = self.preparing()
        exporter = p.BaselineExporter(project.workspace)
        with patch.object(p.subprocess, "run") as process:
            with self.assertRaises(ValueError):
                exporter(source_path(project), self.root / "OUTSIDE.pdf")
            process.assert_not_called()

    def test_exporter_frozen_command_has_only_owned_workspace_and_phase(self):
        project = self.preparing()

        def child(command, **kwargs):
            self.assertEqual(
                command[1:3], ["--baseline-export", str(project.workspace)]
            )
            self.assertEqual(command[-2:], ["--baseline-phase", "0"])
            self.assertNotIn("--native-plan", command)
            self.assertEqual(kwargs["stdin"], p.subprocess.DEVNULL)
            self.assertEqual(kwargs["timeout"], 240)
            r.write_json(
                project.workspace / "native-prepare-step-1.json",
                {"ok": True, "phase": 0, "native": {"native_pages": 1}},
            )
            return SimpleNamespace(returncode=0)

        with (
            patch.object(p, "frozen", return_value=True),
            patch.object(p.subprocess, "run", side_effect=child),
        ):
            exporter = p.BaselineExporter(project.workspace)
            result = exporter(
                source_path(project),
                project.workspace / "baseline.pdf",
                hwpx=project.workspace / "baseline.hwpx",
            )
        self.assertEqual(result["native_pages"], 1)
        self.assertEqual(exporter.phase, 1)

    def test_exporter_rejects_an_unbound_phase_reply_and_preserves_counter(self):
        project = self.preparing()

        def child(*args, **kwargs):
            r.write_json(
                project.workspace / "native-prepare-step-1.json",
                {"ok": True, "phase": 2, "native": {"native_pages": 1}},
            )
            return SimpleNamespace(returncode=0)

        exporter = p.BaselineExporter(project.workspace)
        with patch.object(p.subprocess, "run", side_effect=child):
            with self.assertRaises(ValueError):
                exporter(
                    source_path(project),
                    project.workspace / "baseline.pdf",
                    hwpx=project.workspace / "baseline.hwpx",
                )
        self.assertEqual(exporter.phase, 0)

    @patch.object(native, "owned_session", new=fake_owned_session)
    def test_native_worker_opens_only_verified_copy_and_fixed_new_exports(self):
        project = self.preparing()
        app = Mock()
        app.PageCount = 1
        app.Version = "SYNTHETIC COM ONLY"

        def save(path, format, options):
            Path(path).write_bytes(b"SYNTHETIC EXPORT ONLY")
            return True

        app.SaveAs.side_effect = save
        com = ModuleType("pythoncom")
        setattr(com, "CoInitialize", Mock())
        setattr(com, "CoUninitialize", Mock())
        client = ModuleType("win32com.client")
        setattr(client, "DispatchEx", Mock(return_value=app))
        modules = cached_com_modules(com, client, project.workspace.parent)
        before = source_path(project).read_bytes()
        with (
            patch.dict(sys.modules, modules),
            patch.object(
                native,
                "registered_security_module",
                return_value={"alias": "SYNTHETIC"},
            ),
        ):
            result = native.export_phase(project.workspace, 0)
        getattr(client, "DispatchEx").assert_called_once_with("HWPFrame.HwpObject")
        self.assertEqual(
            app.Open.call_args.args[0],
            str(project.workspace / "native-import-copy.hwp"),
        )
        self.assertEqual(source_path(project).read_bytes(), before)
        self.assertFalse(app.XHwpWindows.Item(0).Visible)
        app.Clear.assert_called_once_with(1)
        app.Quit.assert_called_once()
        getattr(com, "CoUninitialize").assert_called_once()
        self.assertEqual(result["native_pages"], 1)
        app.Clear.side_effect = RuntimeError("SYNTHETIC Clear failure")
        with (
            patch.dict(sys.modules, modules),
            patch.object(
                native,
                "registered_security_module",
                return_value={"alias": "SYNTHETIC"},
            ),
        ):
            with self.assertRaisesRegex(RuntimeError, "Clear failure"):
                native.export_phase(project.workspace, 1)
        self.assertEqual(app.Quit.call_count, 2)
        self.assertEqual(getattr(com, "CoUninitialize").call_count, 2)

    def test_registered_security_preflight_never_installs_or_changes_registry(self):
        dll = Path(self.temp.name) / "SYNTHETIC.dll"
        dll.write_bytes(b"MOCK ONLY")
        registry = Mock()
        registry.REG_SZ = 1
        registry.KEY_READ = 2
        registry.KEY_WOW64_32KEY = 4
        registry.QueryValueEx.return_value = (str(dll), 1)
        registry.OpenKey.return_value.__enter__ = Mock(return_value=Mock())
        registry.OpenKey.return_value.__exit__ = Mock(return_value=False)
        with (
            patch.object(security, "hwp_executable"),
            patch.object(security, "checked_dll"),
        ):
            result = security.registered_security_module(registry=registry)
        self.assertFalse(result["registry_updated"])
        registry.CreateKeyEx.assert_not_called()
        registry.SetValueEx.assert_not_called()


class ProjectWidgetTests(DesktopWidgetTests):
    def test_empty_folder_enables_add_not_prepare_or_review(self):
        with tempfile.TemporaryDirectory() as temp:
            self.app.path.set(temp)
            self.app.check_workspace()
            self.wait()
            self.assertEqual(str(self.app.add_button.cget("state")), "normal")
            self.assertEqual(str(self.app.prepare_button.cget("state")), "disabled")
            self.assertEqual(str(self.app.open_button.cget("state")), "disabled")
            self.assertEqual(snapshot(Path(temp)), {})

    def test_file_dialog_cancel_never_copies_or_prepares(self):
        with tempfile.TemporaryDirectory() as temp:
            self.app.path.set(temp)
            self.app.check_workspace()
            self.wait()
            with (
                patch("hwp_desktop.filedialog.askopenfilename", return_value=""),
                patch.object(p, "import_hwp") as copy,
            ):
                self.app.add_hwp()
                copy.assert_not_called()
            self.assertEqual(snapshot(Path(temp)), {})

    def test_prepare_requires_separate_human_confirmation_and_cancel_is_noop(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            (root / "SYNTHETIC.hwp").write_bytes(b"NOT REAL")
            self.app.path.set(temp)
            self.app.check_workspace()
            self.wait()
            self.assertEqual(str(self.app.prepare_button.cget("state")), "normal")
            before = snapshot(root)
            with (
                patch("hwp_desktop.messagebox.askyesno", return_value=False),
                patch.object(p, "prepare_project") as prepare,
            ):
                self.app.prepare_hwp()
                prepare.assert_not_called()
            self.assertEqual(snapshot(root), before)

    def test_nested_ready_keeps_user_root_in_picker_and_opens_nested_workspace(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp).resolve()
            fixture(root / p.REVIEW_DIR)
            self.app.path.set(temp)
            self.app.check_workspace()
            self.wait()
            self.assertEqual(self.app.path.get(), str(root))
            assert self.app.info is not None
            self.assertEqual(self.app.info.path, root / p.REVIEW_DIR)
            self.assertTrue(self.app.same_path())
            self.assertEqual(str(self.app.add_button.cget("state")), "disabled")


def load_tests(loader, tests, pattern):
    suite = unittest.TestSuite(loader.loadTestsFromTestCase(ProjectTests))
    for name in ProjectWidgetTests.__dict__:
        if name.startswith("test_"):
            suite.addTest(ProjectWidgetTests(name))
    return suite


if __name__ == "__main__":
    unittest.main()
