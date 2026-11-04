"""Text/GUI test admission with TEMP fake candidates; never COM, EXE or Tk."""

import importlib
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
text = importlib.import_module("native_text_apply_smoke")
contract = importlib.import_module("native_contract_smoke")
gui = importlib.import_module("gui_workflow_fixture")
build = importlib.import_module("build_exe")


class SyntheticBoundary(Exception):
    pass


class CandidateFixture(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name).resolve()
        (self.root / "work").mkdir()
        self.output = self.root / "work/toy-text-apply-SYNTHETIC"
        self.gui_output = self.root / "work/toy-gui-native-SYNTHETIC"
        self.gui_output.mkdir()
        self.exe = self.root / "candidate/HWPReview.exe"
        self.exe.parent.mkdir()
        self.exe.write_bytes(b"SYNTHETIC NOT AN EXECUTABLE; NEVER RUN")
        self.creator = Mock(side_effect=SyntheticBoundary("NO Native authoring"))
        self.layout_creator = Mock(
            side_effect=SyntheticBoundary("NO Native table authoring")
        )
        for patcher in (
            patch.object(text, "ROOT", self.root),
            patch.object(contract, "ROOT", self.root),
            patch.object(gui, "ROOT", self.root),
            patch.object(text, "create_source", self.creator),
            patch.object(contract, "create_source", self.layout_creator),
            patch.object(
                text, "registered_security_module", return_value={"SYNTHETIC": True}
            ),
            patch.object(text, "hwp_inventory", return_value=[]),
            patch.object(
                text.p, "prepare_project", side_effect=AssertionError("No preparation")
            ),
            patch.object(gui.tk, "Tk", side_effect=AssertionError("No Tk")),
            patch.object(gui.threading, "Thread"),
            patch.object(gui, "OLD", gui.OLD),
            patch.object(gui, "NEW", gui.NEW),
            patch.object(build, "SOURCE_FILES", ("scripts/app.py",)),
            patch.object(build, "ASSETS", ("web/view.html",)),
        ):
            patcher.start()
            self.addCleanup(patcher.stop)
        for name in (*build.SOURCE_FILES, *build.ASSETS):
            path = self.root / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(b"SYNTHETIC INPUT")
        self.metadata = {
            "executable_sha256": text.r.file_hash(self.exe),
            "source_sha256": {
                name: text.r.file_hash(self.root / name) for name in build.SOURCE_FILES
            },
            "assets_sha256": {
                name: text.r.file_hash(self.root / name) for name in build.ASSETS
            },
        }
        self.save_metadata()

    def save_metadata(self):
        (self.exe.parent / "BUILD.json").write_text(
            json.dumps(self.metadata), encoding="utf-8"
        )

    def gui_args(
        self,
        *,
        output=None,
        executable=None,
        layout=False,
        fill=False,
        agent_workflow=False,
        reference=None,
    ):
        args = [
            str(self.gui_output if output is None else output),
            str(self.exe if executable is None else executable),
            "--run-native-test",
            "--toy-only",
        ]
        return (
            args
            + (["--layout"] if layout else [])
            + (["--fill"] if fill else [])
            + (["--agent-workflow"] if agent_workflow else [])
            + (
                ["--reference-toy-source", str(reference)]
                if reference is not None
                else []
            )
        )

    def assert_before_native(self):
        self.creator.assert_not_called()
        self.layout_creator.assert_not_called()
        gui.tk.Tk.assert_not_called()
        gui.threading.Thread.assert_not_called()


class TextSmokePreflightTests(CandidateFixture):
    def invoke(self):
        return text.run(self.output, self.exe, run_native=True, toy_only=True)

    def test_missing_or_truthy_nonliteral_flags_stop_before_paths(self):
        for options in (
            {},
            {"run_native": True},
            {"toy_only": True},
            {"run_native": 1, "toy_only": True},
            {"run_native": True, "toy_only": "yes"},
        ):
            with (
                self.subTest(options=options),
                patch.object(text.a, "real_path") as paths,
            ):
                with self.assertRaises(ValueError):
                    text.run(self.output, self.exe, **options)
                paths.assert_not_called()
        self.assert_before_native()
        text.registered_security_module.assert_not_called()
        text.hwp_inventory.assert_not_called()
        self.assertFalse(self.output.exists())

    def test_frozen_run_and_preflight_are_denied_before_paths(self):
        with (
            patch.object(text.sys, "frozen", True, create=True),
            patch.object(text.a, "real_path") as paths,
        ):
            with self.assertRaises(ValueError):
                self.invoke()
            with self.assertRaises(ValueError):
                text.qualification_preflight(self.exe)
            paths.assert_not_called()
        self.assert_before_native()

    def test_exact_preflight_is_readonly_and_never_execution_authority(self):
        before = {
            p.relative_to(self.root): text.r.file_hash(p)
            for p in self.root.rglob("*")
            if p.is_file()
        }
        proof = text.qualification_preflight(self.exe)
        self.assertTrue(proof["source_and_assets_match"])
        self.assertFalse(proof["native_started"])
        self.assertFalse(proof["trial_authorized"])
        self.assertEqual(
            before,
            {
                p.relative_to(self.root): text.r.file_hash(p)
                for p in self.root.rglob("*")
                if p.is_file()
            },
        )
        text.registered_security_module.assert_not_called()
        text.hwp_inventory.assert_not_called()
        self.assert_before_native()

    def test_foreign_nested_existing_or_linked_output_is_rejected(self):
        existing = self.root / "work/toy-text-apply-EXISTS"
        existing.mkdir()
        for output in (
            existing,
            self.root / "toy-text-apply-FOREIGN",
            self.root / "work/nested/toy-text-apply-NESTED",
            self.root / "work/wrong",
        ):
            with self.subTest(output=output), self.assertRaises(ValueError):
                text.run(output, self.exe, run_native=True, toy_only=True)
        linked = self.root / "work/toy-text-apply-LINK"
        original = Path.is_symlink
        with patch.object(
            Path, "is_symlink", lambda path: path == linked or original(path)
        ):
            with self.assertRaises(ValueError):
                text.run(linked, self.exe, run_native=True, toy_only=True)
        self.assert_before_native()
        text.registered_security_module.assert_not_called()

    def test_changed_exe_source_or_asset_blocks_before_security_inventory_and_lab(self):
        for path in (
            self.exe,
            *(self.root / name for name in (*build.SOURCE_FILES, *build.ASSETS)),
        ):
            before = path.read_bytes()
            with self.subTest(path=path):
                path.write_bytes(b"CHANGED")
                with self.assertRaises(ValueError):
                    self.invoke()
                path.write_bytes(before)
                self.assertFalse(self.output.exists())
                text.registered_security_module.assert_not_called()
                text.hwp_inventory.assert_not_called()
        self.assert_before_native()

    def test_snapshot_sets_and_digests_fail_closed_before_unrelated_reads(self):
        original = json.loads(json.dumps(self.metadata))
        for delta in (
            {"source_sha256": {}},
            {"assets_sha256": {}},
            {"source_sha256": {**original["source_sha256"], "../UNRELATED": "0" * 64}},
            {"assets_sha256": {build.ASSETS[0]: "NOT A DIGEST"}},
        ):
            with self.subTest(delta=delta):
                self.metadata = {**original, **delta}
                self.save_metadata()
                with self.assertRaises(ValueError):
                    self.invoke()
        text.registered_security_module.assert_not_called()
        text.hwp_inventory.assert_not_called()
        self.assert_before_native()

    def test_nonidle_process_blocks_before_output_or_authoring(self):
        text.hwp_inventory.return_value = [{"pid": 99, "creation_ms": 1}]
        with self.assertRaises(RuntimeError):
            self.invoke()
        self.assertFalse(self.output.exists())
        self.assert_before_native()

    def test_valid_literal_flags_reach_only_synthetic_authoring_boundary(self):
        with self.assertRaises(SyntheticBoundary):
            self.invoke()
        self.creator.assert_called_once_with(self.output / "source")
        text.p.prepare_project.assert_not_called()
        report = json.loads(
            (self.output / "TOY-ONLY-text-apply-verification.json").read_text(
                encoding="utf-8"
            )
        )
        self.assertEqual(report["status"], "blocked")


class GuiSmokePreflightTests(CandidateFixture):
    def test_native_flags_and_layout_are_denied_before_paths_without_full_scope(self):
        cases = (
            [str(self.gui_output), str(self.exe)],
            [str(self.gui_output), str(self.exe), "--run-native-test"],
            [str(self.gui_output), "--run-native-test", "--toy-only"],
            [str(self.gui_output), "--layout"],
            [str(self.gui_output), "--fill"],
            [str(self.gui_output), str(self.exe), "--fill"],
            self.gui_args(layout=True, fill=True),
            self.gui_args(fill=True, agent_workflow=True),
            [str(self.gui_output), "--agent-workflow"],
            [str(self.gui_output), str(self.exe), "--agent-workflow"],
            self.gui_args(layout=True, agent_workflow=True),
            self.gui_args(reference=self.root / "NOT-READ.hwp"),
            self.gui_args(agent_workflow=True, reference=self.root / "NOT-READ.hwp"),
            [
                str(self.gui_output),
                "--reference-toy-source",
                str(self.root / "NOT-READ.hwp"),
            ],
        )
        for args in cases:
            with self.subTest(args=args), patch.object(gui.a, "real_path") as paths:
                with self.assertRaises(ValueError):
                    gui.main(args)
                paths.assert_not_called()
        self.assert_before_native()

    def test_frozen_gui_host_is_rejected_before_argument_or_path_processing(self):
        with (
            patch.object(gui.sys, "frozen", True, create=True),
            patch.object(gui.a, "real_path") as paths,
        ):
            with self.assertRaises(ValueError):
                gui.main(self.gui_args())
            paths.assert_not_called()
        self.assert_before_native()

    def test_native_cannot_use_synthetic_foreign_nested_or_linked_lab(self):
        for path in (
            self.root / "hwp-gui-SYNTHETIC-FOREIGN",
            self.root / "toy-gui-native-FOREIGN",
            self.root / "work/nested/toy-gui-native-NESTED",
        ):
            path.mkdir(parents=True)
            with self.subTest(path=path), self.assertRaises(ValueError):
                gui.main(self.gui_args(output=path))
        original = Path.is_symlink
        with patch.object(
            Path, "is_symlink", lambda path: path == self.gui_output or original(path)
        ):
            with self.assertRaises(ValueError):
                gui.main(self.gui_args())
        self.assert_before_native()

    def test_synthetic_mode_cannot_use_native_lab_name(self):
        with self.assertRaises(ValueError):
            gui.main([str(self.gui_output)])
        self.assert_before_native()

    def test_exact_text_candidate_reaches_only_fake_creator_even_with_edge_profile(
        self,
    ):
        (self.gui_output / "edge-profile").mkdir()
        with self.assertRaises(SyntheticBoundary):
            gui.main(self.gui_args())
        self.creator.assert_called_once_with(self.gui_output / "source")
        self.layout_creator.assert_not_called()
        gui.tk.Tk.assert_not_called()
        gui.threading.Thread.assert_not_called()

    def test_explicit_agent_mode_keeps_real_native_exports_and_only_mocks_pi(self):
        agents = importlib.import_module("hwp_agents")
        chat = importlib.import_module("hwp_pi_chat")
        probe = importlib.import_module("hwp_pi_probe")
        baseline_exporter, text_exporter = (
            gui.p.BaselineExporter,
            gui.a.TextExporter.__call__,
        )
        module, text_module, audit = (
            gui.security.registered_security_module,
            gui.a.registered_security_module,
            gui.r.hwp_audit,
        )

        def creator(directory):
            directory.mkdir()
            path = directory / "SYNTHETIC.hwp"
            path.write_bytes(b"SYNTHETIC NOT HWP")
            return path

        def tk_boundary():
            self.assertIs(gui.p.BaselineExporter, baseline_exporter)
            self.assertIs(gui.a.TextExporter.__call__, text_exporter)
            self.assertIs(gui.security.registered_security_module, module)
            self.assertIs(gui.a.registered_security_module, text_module)
            self.assertIs(gui.r.hwp_audit, audit)
            presence = agents.scan_path()
            runtime = chat.resolve_chat_runtime(presence["agents"][0]["path"])
            self.assertEqual(runtime["sdk_version"], "1.3.0")
            self.assertEqual(chat.call_chat.side_effect.__self__.old_text, text.OLD)
            self.assertEqual(chat.call_chat.side_effect.__self__.new_text, text.NEW)
            for sdk in (probe, chat):
                with self.assertRaisesRegex(
                    AssertionError, "No actual SDK/profile/model"
                ):
                    sdk.run_sdk_command()
            raise SyntheticBoundary("Before Tk; no Native or SDK started")

        self.creator.side_effect = creator
        gui.tk.Tk.side_effect = tk_boundary
        with self.assertRaises(SyntheticBoundary):
            gui.main(self.gui_args(agent_workflow=True))
        self.creator.assert_called_once()
        self.layout_creator.assert_not_called()
        text.p.prepare_project.assert_not_called()

    def test_mixed_agent_native_mode_rejects_drift_before_authoring(self):
        (self.root / build.ASSETS[0]).write_bytes(b"CHANGED")
        with self.assertRaises(ValueError):
            gui.main(self.gui_args(agent_workflow=True))
        self.assert_before_native()

    def test_exact_layout_candidate_uses_only_qualified_schema3_creator(self):
        with self.assertRaises(SyntheticBoundary):
            gui.main(self.gui_args(layout=True))
        self.layout_creator.assert_called_once_with(self.gui_output / "source", 3)
        self.creator.assert_not_called()
        gui.tk.Tk.assert_not_called()
        gui.threading.Thread.assert_not_called()

    def test_exact_fill_candidate_uses_only_the_qualified_schema4_creator(self):
        with self.assertRaises(SyntheticBoundary):
            gui.main(self.gui_args(fill=True))
        self.layout_creator.assert_called_once_with(self.gui_output / "source", 4)
        self.creator.assert_not_called()
        gui.tk.Tk.assert_not_called()
        gui.threading.Thread.assert_not_called()

    def test_contract_reference_mode_uses_only_the_fixed_copy_boundary(self):
        reference = self.root / "SYNTHETIC-REFERENCE.hwp"
        for schema, flags in ((3, {"layout": True}), (4, {"fill": True})):
            with patch.object(
                contract,
                "copy_reference_toy",
                side_effect=SyntheticBoundary("No actual reference copy/Native"),
            ) as copied:
                with self.assertRaises(SyntheticBoundary):
                    gui.main(self.gui_args(reference=reference, **flags))
                copied.assert_called_once_with(
                    reference, self.gui_output / "source", schema
                )
            self.creator.assert_not_called()
            self.layout_creator.assert_not_called()
            gui.tk.Tk.assert_not_called()
            gui.threading.Thread.assert_not_called()

    def test_arbitrary_reference_bytes_stop_before_authoring_or_tk(self):
        reference = self.root / "ARBITRARY.hwp"
        reference.write_bytes(b"NOT A PINNED PURE TOY")
        with self.assertRaises(ValueError):
            gui.main(self.gui_args(layout=True, reference=reference))
        self.assert_before_native()
        self.assertFalse((self.gui_output / "source").exists())

    def test_fill_pin_is_rechecked_after_creator_before_threads_or_tk(self):
        def creator(directory, schema):
            self.assertEqual(schema, 4)
            directory.mkdir()
            source = directory / "SYNTHETIC.hwp"
            source.write_bytes(b"SYNTHETIC NOT HWP")
            (self.root / build.ASSETS[0]).write_bytes(b"CHANGED DURING FILL AUTHORING")
            return source

        self.layout_creator.side_effect = creator
        with self.assertRaisesRegex(
            ValueError, "Parent differs from candidate snapshot"
        ):
            gui.main(self.gui_args(fill=True))
        self.layout_creator.assert_called_once()
        self.creator.assert_not_called()
        gui.tk.Tk.assert_not_called()
        gui.threading.Thread.assert_not_called()
        text.p.prepare_project.assert_not_called()

    def test_wrong_exe_name_or_mismatched_closed_snapshot_never_reaches_authoring(self):
        wrong = self.exe.with_name("OTHER.exe")
        wrong.write_bytes(self.exe.read_bytes())
        with self.assertRaises(ValueError):
            gui.main(self.gui_args(executable=wrong))
        for field in ("source_sha256", "assets_sha256"):
            original = self.metadata[field]
            self.metadata[field] = {**original, "../UNRELATED": "0" * 64}
            self.save_metadata()
            with self.subTest(field=field), self.assertRaises(ValueError):
                gui.main(self.gui_args())
            self.metadata[field] = original
            self.save_metadata()
        self.assert_before_native()

    def test_current_asset_and_source_drift_are_checked_in_both_families(self):
        for layout in (False, True):
            for name in (*build.SOURCE_FILES, *build.ASSETS):
                path = self.root / name
                before = path.read_bytes()
                with self.subTest(layout=layout, name=name):
                    path.write_bytes(b"CHANGED")
                    with self.assertRaises(ValueError):
                        gui.main(self.gui_args(layout=layout))
                    path.write_bytes(before)
        self.assert_before_native()

    def test_pin_is_rechecked_after_creator_before_any_worker_selection_thread_or_tk(
        self,
    ):
        def fake_creator(directory):
            directory.mkdir()
            source = directory / "SYNTHETIC.hwp"
            source.write_bytes(b"SYNTHETIC NOT HWP")
            (self.root / build.ASSETS[0]).write_bytes(b"CHANGED DURING TEST AUTHORING")
            return source

        self.creator.side_effect = fake_creator
        with self.assertRaisesRegex(ValueError, "source/assets differ"):
            gui.main(self.gui_args())
        self.creator.assert_called_once()
        gui.tk.Tk.assert_not_called()
        gui.threading.Thread.assert_not_called()
        text.p.prepare_project.assert_not_called()


if __name__ == "__main__":
    unittest.main()
