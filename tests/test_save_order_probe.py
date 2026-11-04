"""Synthetic bytes/fake COM only. No Native, registry, existing lab or document access."""

import base64
from contextlib import redirect_stdout
import importlib
import io
import json
from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch
import zipfile
import test_cell_position_probe as creator_fixtures

m = importlib.import_module("native_save_order_probe")
NATIVE_ONCE = m.cell.native_once
TEXT = m.fixture.MARKER + "\nTEST LABEL\tTEST INPUT CELLS BELOW\nName\t\nCode\t\nDate\t"


def hpf(linear):
    attribute = "" if linear is None else f' linear="{linear}"'
    return (
        f'<package xmlns="{m.fixture.native.OPF[1:-1]}"><spine>'
        '<itemref idref="header"/><itemref idref="section0"'
        f"{attribute}/></spine></package>"
    ).encode()


class FakeApp:
    def __init__(self, runtime, number):
        self.runtime, self.number = runtime, number
        self.pid = 100 + number
        self.creation = int(m.datetime.now(m.timezone.utc).timestamp() * 1000)
        self.document = SimpleNamespace(Path="", FullName="", Modified=False)
        self.XHwpDocuments = SimpleNamespace(Count=1, Item=lambda _: self.document)
        self.window = SimpleNamespace(WindowHandle=self.pid, Visible=False)
        self.XHwpWindows = SimpleNamespace(Item=lambda _: self.window)
        self.text, self.PageCount = None, 1
        self.pos: tuple[int, int, int] = (0, 0, 0)
        self.log = []
        self.text_reads = []
        self.RegisterModule = Mock(return_value=True)
        self.Open = Mock(side_effect=self.open)
        self.SaveAs = Mock(side_effect=self.save)
        self.Clear = Mock(side_effect=self.clear)
        self.Quit = Mock(side_effect=self.quit)

    def GetTextFile(self, fmt, options):
        assert (fmt, options) == ("TEXT", "")
        self.text_reads.append(tuple(self.log))
        return self.text

    def GetPos(self):
        return self.pos

    def open(self, path, fmt, options):
        assert options == "versionwarning:false"
        self.log.append(("Open", fmt))
        self.document.FullName, self.document.Path = path, str(Path(path).parent)
        self.text = TEXT
        self.runtime.hook(self, "open")
        return True

    def save(self, path, fmt, options):
        assert options == ""
        self.log.append(("SaveAs", fmt))
        if fmt == "HWPX":
            with zipfile.ZipFile(path, "x") as archive:
                archive.writestr(
                    "Contents/content.hpf", self.runtime.xml[self.number - 1]
                )
        elif fmt == "HWP":
            with Path(path).open("xb") as stream:
                stream.write(b"SYNTHETIC DERIVED HWP - NOT A DOCUMENT")
        else:
            raise AssertionError("Unexpected export format")
        self.document.FullName, self.document.Path = path, str(Path(path).parent)
        self.runtime.hook(self, "save")
        return True

    def clear(self, mode):
        assert mode == 1
        self.log.append(("Clear", mode))
        self.document.FullName = self.document.Path = ""
        self.text = None
        self.runtime.hook(self, "clear")

    def quit(self):
        self.log.append(("Quit",))
        self.runtime.hook(self, "quit")
        self.runtime.active = None


class SaveOrderProbeTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name).resolve()
        (self.root / "work").mkdir()
        self.serial = 0
        self.apps, self.active = [], None
        self.xml = [hpf("no"), hpf("yes"), hpf(None), hpf("yes"), hpf("no"), hpf(None)]
        self.hook = lambda app, stage: None
        self.proof_hook = lambda proof: None
        self.pythoncom = SimpleNamespace(CoInitialize=Mock(), CoUninitialize=Mock())
        win32 = SimpleNamespace()
        client = SimpleNamespace()
        cache = SimpleNamespace()
        win32.client, client.gencache = client, cache
        client.DispatchEx = Mock(side_effect=self.dispatch)
        cache.EnsureDispatch = Mock(side_effect=lambda app: app)
        self.client, self.cache = client, cache
        self.kernel = Mock()
        self.kernel.OpenProcess.side_effect = lambda access, inherit, pid: pid + 1000
        self.kernel.WaitForSingleObject.return_value = 0
        self.kernel.CloseHandle.return_value = True
        for patcher in (
            patch.dict(
                sys.modules,
                {
                    "pythoncom": self.pythoncom,
                    "win32com": win32,
                    "win32com.client": client,
                    "win32com.client.gencache": cache,
                },
            ),
            patch.object(m, "ROOT", self.root),
            patch.object(m, "hwp_inventory", side_effect=self.inventory),
            patch.object(m.fixture, "hwp_inventory", side_effect=self.inventory),
            patch.object(
                m.fixture, "window_pid", side_effect=lambda window: window.WindowHandle
            ),
            patch.object(m.ctypes, "WinDLL", return_value=self.kernel, create=True),
            patch.object(
                m.security,
                "registered_security_module",
                return_value={"alias": "SYNTHETIC", "dll_sha256": "a" * 64},
            ),
            patch.object(
                m.security,
                "ensure_security_module",
                side_effect=AssertionError("No installation path"),
            ),
            patch.object(m.cell, "native_once", side_effect=self.create),
            patch.object(
                m.r, "prepare", side_effect=AssertionError("No prepare/import")
            ),
            patch.object(m.r, "apply", side_effect=AssertionError("No apply")),
            patch.object(
                m.fixture, "author", side_effect=AssertionError("No arm authoring")
            ),
        ):
            patcher.start()
            self.addCleanup(patcher.stop)

    def inventory(self):
        return (
            []
            if self.active is None
            else [{"pid": self.active.pid, "creation_ms": self.active.creation}]
        )

    def dispatch(self, progid):
        self.assertEqual(progid, "HWPFrame.HwpObject")
        self.assertIsNone(self.active)
        app = FakeApp(self, len(self.apps) + 1)
        self.apps.append(app)
        self.active = app
        return app

    def create(self, directory, proof):
        # In-memory evidence from this synthetic invocation, never read from JSON.
        source = directory / "TOY-ONLY-source.hwp"
        with source.open("xb") as stream:
            stream.write(b"SYNTHETIC OWNED SOURCE - NOT A DOCUMENT")
        proof.update(
            status="metadata_observed_not_writer_qualified",
            owned_process_exit_confirmed=True,
            source_reopened=True,
            owned_blank_verified=True,
            window_pid_binding_verified=True,
            anchor_navigation={"all_authored_positions_match": True},
            authored_grid_before_source_save={
                f"{row},{col}": [row * 2 + col + 1, 0, 0]
                for row in range(4)
                for col in range(2)
            },
            observation={
                "source_sha256": m.r.file_hash(source),
                "source_text_sha256": m.r.digest(TEXT.encode()),
            },
            reopened_observation={
                "source_sha256": m.r.file_hash(source),
                "source_text_sha256": m.r.digest(TEXT.encode()),
            },
        )
        self.proof_hook(proof)

    def run_probe(self):
        self.serial += 1
        self.lab = self.root / f"work/toy-save-order-SYNTHETIC-{self.serial}"
        return m.run(self.lab, run_native=True, toy_only=True)

    def test_fixed_independent_order_hashes_snapshots_and_observations(self):
        report = self.run_probe()
        self.assertEqual(report["status"], "diagnosis_completed_not_qualified", report)
        self.assertFalse(report["qualification"])
        self.assertFalse(report["production_enabled"])
        self.assertFalse(report["preservation_success_claimed"])
        self.assertEqual(report["baseline_NO_idrefs"], ["section0"])
        self.assertEqual(len(self.apps), 6)
        for number, (app, arm, (fmt, saves)) in enumerate(
            zip(self.apps, report["arms"], m.ARMS), 1
        ):
            self.assertEqual(
                app.log,
                [("Open", fmt)]
                + [("SaveAs", f) for f in saves]
                + [("Clear", 1), ("Quit",)],
            )
            self.assertTrue(arm["owned_process_exit_confirmed"])
            self.assertEqual(arm["cleanup"], "owned_exit_confirmed")
            self.assertEqual(arm["automatic_retries"], 0)
            self.assertEqual(arm["source_sha256"], report["source_sha256"])
            expected = (
                m.r.file_hash(self.lab / "arm3.hwp")
                if number == 4
                else report["source_sha256"]
            )
            self.assertEqual(arm["copy_sha256"], expected)
            self.assertEqual(
                m.r.file_hash(self.lab / f"arm{number}-source.hwp"), expected
            )
            self.assertEqual(arm["code_sha256"], report["code_sha256"])
            self.assertEqual(arm["python_process_id"], m.os.getpid())
            self.assertEqual(
                arm["dispatch_path"],
                "DispatchEx" if number == 6 else "DispatchEx+EnsureDispatch",
            )
            wrapper = f"{FakeApp.__module__}.{FakeApp.__qualname__}"
            self.assertEqual(arm["dispatch_wrapper"], wrapper)
            self.assertEqual(arm["active_wrapper"], wrapper)
            self.assertEqual(
                base64.b64decode(arm["hpf"]["raw_hpf_base64"]), self.xml[number - 1]
            )
            for op in arm["operations"][1:]:
                keys = ("page_count", "modified")
                if number in (5, 6):
                    self.assertEqual(
                        op["before"]["text_snapshot"], "deferred_until_after_first_save"
                    )
                    self.assertNotIn("text_type", op["before"])
                    self.assertNotIn("text_sha256", op["before"])
                else:
                    keys += ("text_type", "text_sha256")
                for key in keys:
                    self.assertEqual(op["before"][key], op["after"][key])
                self.assertEqual(op["after"]["text_type"], "str")
                self.assertEqual(op["after"]["text_sha256"], m.r.digest(TEXT.encode()))
                self.assertEqual(
                    op["output_sha256"], m.r.file_hash(self.lab / op["path"])
                )
            app.RegisterModule.assert_called_once_with(
                m.security.MODULE_TYPE, m.security.MODULE_ALIAS
            )
            self.assertTrue((self.lab / f"ARM-{number}.json").is_file())
        self.assertFalse(
            report["arms"][1]["hpf"]["baseline_comparison"]["semantic_spine_equal"]
        )
        self.assertEqual(report["arms"][3]["copy_of"], "arm3.hwp")
        self.assertEqual(report["arms"][4]["copy_of"], "source/TOY-ONLY-source.hwp")
        self.assertEqual(report["arms"][5]["copy_of"], "source/TOY-ONLY-source.hwp")
        self.assertEqual(
            [c.args for c in self.kernel.OpenProcess.call_args_list],
            [(0x00100000, False, app.pid) for app in self.apps],
        )
        self.assertEqual(
            [c.args[1] for c in self.kernel.WaitForSingleObject.call_args_list],
            [5000] * 6,
        )
        self.assertEqual(self.kernel.CloseHandle.call_count, 6)
        self.assertEqual(self.cache.EnsureDispatch.call_count, 5)
        m.cell.native_once.assert_called_once()
        m.security.ensure_security_module.assert_not_called()
        self.assertEqual(
            json.loads((self.lab / "SAVE-ORDER-RESULT.json").read_text()), report
        )

    def test_export_first_defers_document_text_not_blank_or_post_save_checks(self):
        report = self.run_probe()
        self.assertEqual(report["status"], "diagnosis_completed_not_qualified", report)
        normal, control = self.apps[0], self.apps[4]

        def before_first_save(app):
            return [
                trace
                for trace in app.text_reads
                if any(op[0] == "Open" for op in trace)
                and not any(op[0] == "SaveAs" for op in trace)
            ]

        self.assertTrue(before_first_save(normal))
        self.assertEqual(before_first_save(control), [])
        self.assertTrue(any(not trace for trace in control.text_reads))
        self.assertTrue(
            any(
                any(op[0] == "SaveAs" for op in trace)
                and not any(op[0] == "Clear" for op in trace)
                for trace in control.text_reads
            )
        )
        arm = report["arms"][4]
        self.assertEqual(arm["operations"][0]["format"], "")
        self.assertEqual(
            arm["operations"][0]["after"]["text_snapshot"],
            "deferred_until_after_first_save",
        )
        self.assertEqual(
            arm["operations"][1]["after"]["text_sha256"], m.r.digest(TEXT.encode())
        )
        self.assertTrue(arm["owned_blank_verified"])
        self.assertFalse(arm["qualification"])

    def test_export_first_bad_post_save_text_blocks_without_retry_or_repair(self):
        for number in (5, 6):
            with self.subTest(arm=number):
                self.apps, self.active = [], None

                def drift(app, stage):
                    if app.number == number and stage == "save":
                        app.text += "SYNTHETIC TEXT DRIFT"

                self.hook = drift
                report = self.run_probe()
                self.assertEqual(report["status"], "blocked")
                self.assertEqual(len(self.apps), number)
                control = self.apps[number - 1]
                self.assertEqual(control.SaveAs.call_count, 1)
                control.Clear.assert_not_called()
                control.Quit.assert_not_called()
                arm = report["arms"][number - 1]
                self.assertEqual(arm["status"], "blocked")
                self.assertIn("TEXT snapshot", arm["error"])
                self.assertEqual(arm["automatic_retries"], 0)
                self.assertNotIn("after", arm["operations"][1])
                path = self.lab / f"arm{number}.hwpx"
                self.assertEqual(
                    m.r.file_hash(path), arm["operations"][1]["output_sha256"]
                )
                with zipfile.ZipFile(path) as archive:
                    self.assertEqual(
                        archive.read("Contents/content.hpf"), self.xml[number - 1]
                    )
                self.assertTrue((self.lab / f"ARM-{number}.json").is_file())
                self.assertEqual(
                    m.r.file_hash(self.lab / "source/TOY-ONLY-source.hwp"),
                    report["source_sha256"],
                )

    def test_direct_dispatch_control_records_actual_wrapper_without_ensure(self):
        class Proxy:
            def __init__(self, app):
                self.target = app

            def __getattr__(self, name):
                return getattr(self.target, name)

        self.cache.EnsureDispatch.side_effect = Proxy
        report = self.run_probe()
        self.assertEqual(report["status"], "diagnosis_completed_not_qualified", report)
        ensured, direct = report["arms"][4:6]
        raw_name = f"{FakeApp.__module__}.{FakeApp.__qualname__}"
        self.assertEqual(ensured["dispatch_wrapper"], raw_name)
        self.assertEqual(
            ensured["active_wrapper"], f"{Proxy.__module__}.{Proxy.__qualname__}"
        )
        self.assertEqual(direct["dispatch_wrapper"], raw_name)
        self.assertEqual(direct["active_wrapper"], raw_name)
        self.assertEqual(direct["dispatch_path"], "DispatchEx")
        self.assertEqual(self.cache.EnsureDispatch.call_count, 5)
        self.assertTrue(
            all(
                call.args[0] is not self.apps[5]
                for call in self.cache.EnsureDispatch.call_args_list
            )
        )
        self.assertEqual(direct["python_process_id"], ensured["python_process_id"])
        self.assertTrue(direct["owned_blank_verified"])
        self.assertTrue(any(not trace for trace in self.apps[5].text_reads))
        self.assertFalse(
            any(
                any(op[0] == "Open" for op in trace)
                and not any(op[0] == "SaveAs" for op in trace)
                for trace in self.apps[5].text_reads
            )
        )
        self.assertEqual(
            direct["operations"][1]["after"]["text_sha256"], m.r.digest(TEXT.encode())
        )
        self.assertTrue(direct["owned_process_exit_confirmed"])

    def test_direct_dispatch_still_rejects_unverified_ownership_without_fallback(self):
        with patch.object(
            m.fixture,
            "window_pid",
            side_effect=lambda window: (
                107 if window.WindowHandle == 106 else window.WindowHandle
            ),
        ):
            report = self.run_probe()
        self.assertEqual(report["status"], "blocked")
        self.assertEqual(len(self.apps), 6)
        app = self.apps[5]
        app.RegisterModule.assert_not_called()
        app.Open.assert_not_called()
        app.SaveAs.assert_not_called()
        app.Clear.assert_not_called()
        app.Quit.assert_not_called()
        self.assertEqual(self.cache.EnsureDispatch.call_count, 5)
        self.assertEqual(report["arms"][5]["dispatch_path"], "DispatchEx")
        self.assertEqual(report["arms"][5]["automatic_retries"], 0)
        self.assertFalse((self.lab / "arm6.hwpx").exists())

    def test_export_first_still_checks_identity_pages_and_copy_before_save(self):
        for kind in ("identity", "pages", "copy"):
            with self.subTest(kind=kind):
                self.apps, self.active = [], None

                def drift(app, stage):
                    if app.number == 5 and stage == "open":
                        if kind == "identity":
                            app.document.FullName = str(self.root / "OTHER.hwp")
                        elif kind == "pages":
                            app.PageCount += 1
                        else:
                            Path(app.document.FullName).write_bytes(b"SYNTHETIC DRIFT")

                self.hook = drift
                report = self.run_probe()
                self.assertEqual(report["status"], "blocked")
                self.assertEqual(len(self.apps), 5)
                self.apps[4].SaveAs.assert_not_called()
                self.assertFalse((self.lab / "arm5.hwpx").exists())
                self.assertEqual(report["arms"][4]["automatic_retries"], 0)

    def test_default_yes_is_inconclusive_not_preservation_or_qualification(self):
        self.xml = [hpf(None), hpf("yes"), hpf(None), hpf("yes"), hpf(None), hpf(None)]
        report = self.run_probe()
        self.assertEqual(report["status"], "diagnosis_completed_not_qualified")
        self.assertEqual(
            report["original_defect_conclusion"], "inconclusive_without_NO_baseline"
        )
        comparison = report["arms"][1]["hpf"]["baseline_comparison"]
        self.assertFalse(comparison["raw_hpf_equal"])
        self.assertTrue(comparison["semantic_spine_equal"])
        self.assertFalse(report["qualification"])
        self.assertFalse(report["preservation_success_claimed"])

    def test_literal_opt_ins_and_frozen_block_before_paths_or_side_effects(self):
        with patch.object(
            m.fixture.paths, "real_path", side_effect=AssertionError("Too early")
        ):
            for args in (
                {},
                {"run_native": True},
                {"toy_only": True},
                {"run_native": 1, "toy_only": True},
                {"run_native": True, "toy_only": 1},
            ):
                with self.assertRaises(ValueError):
                    m.run(self.root / "work/toy-save-order-FLAGS", **args)
            with (
                patch.object(m.sys, "frozen", True, create=True),
                self.assertRaises(ValueError),
            ):
                m.run(
                    self.root / "work/toy-save-order-FROZEN",
                    run_native=True,
                    toy_only=True,
                )
        m.security.registered_security_module.assert_not_called()
        m.hwp_inventory.assert_not_called()
        m.cell.native_once.assert_not_called()
        self.assertEqual(list((self.root / "work").iterdir()), [])

    def test_nonfresh_foreign_noncanonical_and_linked_paths_block_early(self):
        existing = self.root / "work/toy-save-order-EXISTS"
        existing.mkdir()
        for target in (
            existing,
            self.root / "toy-save-order-FOREIGN",
            self.root / "work/wrong-prefix",
            existing / "toy-save-order-NESTED",
            self.root / "work/other/../toy-save-order-ALIAS",
        ):
            with self.assertRaises(ValueError):
                m.run(target, run_native=True, toy_only=True)
        original = Path.is_symlink
        for linked in (self.root / "work/toy-save-order-LINK", self.root / "work"):
            with (
                patch.object(Path, "is_symlink", lambda p: p == linked or original(p)),
                self.assertRaises(ValueError),
            ):
                m.run(
                    self.root / "work/toy-save-order-LINK",
                    run_native=True,
                    toy_only=True,
                )
        with self.assertRaises(TypeError):
            m.run(
                existing,
                run_native=True,
                toy_only=True,
                source=self.root / "source.hwp",
            )
        m.security.registered_security_module.assert_not_called()
        m.hwp_inventory.assert_not_called()
        self.client.DispatchEx.assert_not_called()

    def test_incomplete_creation_proof_stops_before_any_arm_and_keeps_partial_evidence(
        self,
    ):
        for field, value in (
            ("source_reopened", False),
            ("owned_process_exit_confirmed", False),
            ("authored_grid_before_source_save", {}),
            ("reopened_observation", {"source_sha256": "0" * 64}),
        ):
            self.proof_hook = lambda proof, f=field, v=value: proof.update({f: v})
            report = self.run_probe()
            self.assertEqual(report["status"], "blocked")
            self.assertEqual(report["arms"], [])
            self.assertTrue((self.lab / "SOURCE-CREATION.json").is_file())
            self.assertIn("source/TOY-ONLY-source.hwp", report["observed_files_sha256"])
        self.client.DispatchEx.assert_not_called()

    def test_source_and_copy_drift_stop_all_later_arms(self):
        for name in ("source/TOY-ONLY-source.hwp", "arm1-source.hwp"):
            self.apps, self.active = [], None

            def mutate(app, stage):
                if stage == "save":
                    (self.lab / name).write_bytes(b"SYNTHETIC DRIFT")

            self.hook = mutate
            report = self.run_probe()
            self.assertEqual(report["status"], "blocked")
            self.assertEqual(len(report["arms"]), 1)
            self.assertEqual(len(self.apps), 1)
            self.apps[0].Clear.assert_not_called()
            self.apps[0].Quit.assert_not_called()
            self.assertTrue((self.lab / "ARM-1.json").is_file())
            self.assertNotEqual(
                report["recorded_sha256"][name], report["observed_files_sha256"][name]
            )

    def test_typed_text_pages_and_identity_drift_stop_before_another_save(self):
        mutations = (
            lambda app: setattr(app, "text", None),
            lambda app: setattr(app, "text", TEXT + "DRIFT"),
            lambda app: setattr(app, "PageCount", True),
            lambda app: setattr(app, "PageCount", 2),
            lambda app: setattr(app.XHwpDocuments, "Count", True),
            lambda app: setattr(app.XHwpDocuments, "Count", 2),
            lambda app: setattr(
                app.document, "FullName", str(self.root / "FOREIGN.hwp")
            ),
            lambda app: setattr(app.document, "Modified", True),
        )
        for mutate in mutations:
            self.apps, self.active = [], None
            self.hook = lambda app, stage, change=mutate: (
                change(app) if stage == "save" else None
            )
            report = self.run_probe()
            self.assertEqual(report["status"], "blocked")
            self.assertEqual(len(self.apps), 1)
            self.apps[0].SaveAs.assert_called_once()
            self.apps[0].Clear.assert_not_called()
            self.apps[0].Quit.assert_not_called()

    def test_failed_save_keeps_bytes_and_operations_without_retry(self):
        def fail(app, stage):
            if stage == "save":
                raise RuntimeError("SYNTHETIC save error after partial output")

        self.hook = fail
        report = self.run_probe()
        self.assertEqual(report["status"], "blocked")
        self.assertEqual(len(self.apps), 1)
        op = report["arms"][0]["operations"][-1]
        self.assertTrue(op["attempted"])
        self.assertIn("before", op)
        self.assertNotIn("after", op)
        self.assertIn("arm1.hwpx", report["observed_files_sha256"])
        with zipfile.ZipFile(self.lab / "arm1.hwpx") as archive:
            self.assertEqual(archive.read("Contents/content.hpf"), self.xml[0])

    def test_missing_text_or_strict_hpf_snapshot_blocks_followup(self):
        self.hook = lambda app, stage: (
            setattr(app, "text", None) if stage == "open" else None
        )
        report = self.run_probe()
        self.assertEqual(report["status"], "blocked")
        self.apps[0].SaveAs.assert_not_called()
        self.hook = lambda app, stage: None
        for raw in (
            b"<package/>",
            b"<!DOCTYPE package><package/>",
            hpf("UNKNOWN"),
            hpf("no").replace(b"section0", b"header"),
        ):
            self.apps, self.active = [], None
            self.xml[0] = raw
            report = self.run_probe()
            self.assertEqual(report["status"], "blocked")
            self.assertEqual(len(self.apps), 1)
            self.assertNotIn("hpf", report["arms"][0])
            with zipfile.ZipFile(self.lab / "arm1.hwpx") as archive:
                self.assertEqual(archive.read("Contents/content.hpf"), raw)

    def test_unknown_or_unconfirmed_cleanup_is_not_success_and_never_continues(self):
        def unknown(app, stage):
            if stage == "clear":
                app.document.FullName = str(self.root / "UNKNOWN.hwp")

        self.hook = unknown
        report = self.run_probe()
        self.assertEqual(report["status"], "blocked")
        self.assertEqual(len(self.apps), 1)
        self.apps[0].Quit.assert_not_called()
        self.assertFalse(report["arms"][0]["owned_process_exit_confirmed"])
        self.apps, self.active = [], None
        self.hook = lambda app, stage: None
        self.kernel.WaitForSingleObject.return_value = 258
        report = self.run_probe()
        self.assertEqual(report["status"], "blocked")
        self.assertEqual(len(self.apps), 1)
        self.apps[0].Quit.assert_called_once()
        self.assertFalse(report["arms"][0]["owned_process_exit_confirmed"])
        self.assertEqual(report["arms"][0]["exit_wait_result"], 258)
        self.apps, self.active = [], None
        self.kernel.WaitForSingleObject.return_value = 0

        def foreign_after_exit():
            if self.apps and self.active is None:
                return [{"pid": 9999, "creation_ms": 1}]
            return self.inventory()

        with patch.object(m, "hwp_inventory", side_effect=foreign_after_exit):
            report = self.run_probe()
        self.assertEqual(report["status"], "blocked")
        self.assertEqual(len(self.apps), 1)
        self.assertTrue(report["arms"][0]["owned_process_exit_confirmed"])
        self.assertTrue(report["arms"][0]["remaining_hwp_requires_owner_inspection"])

    def test_unverified_ownership_never_registers_opens_or_cleans_up(self):
        with patch.object(m.fixture, "window_pid", return_value=9999):
            report = self.run_probe()
        self.assertEqual(report["status"], "blocked")
        self.assertEqual(len(self.apps), 1)
        app = self.apps[0]
        for method in (app.RegisterModule, app.Open, app.SaveAs, app.Clear, app.Quit):
            method.assert_not_called()
        self.kernel.OpenProcess.assert_not_called()

    def test_creator_delegate_preserves_failure_proof_without_global_bindings(self):
        original_dispatch = self.cache.EnsureDispatch
        original_register = m.cell.registered_security_module

        def fail(directory, proof):
            self.assertIs(self.cache.EnsureDispatch, original_dispatch)
            self.assertIs(m.cell.registered_security_module, original_register)
            proof["partial"] = True
            raise RuntimeError("SYNTHETIC creation failure")

        m.cell.native_once.side_effect = fail
        report = self.run_probe()
        self.assertEqual(report["status"], "blocked")
        self.assertTrue(report["creation_metadata"]["partial"])
        self.assertIs(self.cache.EnsureDispatch, original_dispatch)
        self.assertIs(m.cell.registered_security_module, original_register)
        m.cell.native_once.assert_called_once()
        m.security.ensure_security_module.assert_not_called()

    def test_existing_creator_lifecycle_uses_shared_owned_session_without_live_patches(
        self,
    ):
        harness = creator_fixtures.OwnedCreatorFixture(self)
        with patch.object(m.cell, "native_once", side_effect=NATIVE_ONCE):
            proof = {}
            m._create_source(harness.directory, proof)
        self.assertEqual(len(m.direct.require_binding(harness.source, proof)), 8)
        self.assertTrue(proof["owned_process_exit_confirmed"])
        self.assertEqual(proof["ownership"]["status"], "owned_exit_confirmed")
        self.assertEqual(
            harness.log,
            [
                ("SaveAs", "HWP"),
                ("SaveAs", "HWP"),
                ("Clear", 1),
                ("Open", "HWP"),
                ("Clear", 1),
                ("Quit",),
            ],
        )
        harness.app.RegisterModule.assert_called_once()
        harness.owner.client.DispatchEx.assert_called_once()
        harness.owner.held.wait_for_exit.assert_called_once()
        self.cache.EnsureDispatch.assert_not_called()
        m.security.ensure_security_module.assert_not_called()

    def test_creation_only_evidence_cannot_be_reused_or_overwritten(self):
        self.run_probe()
        path = self.lab / "SAVE-ORDER-RESULT.json"
        original = path.read_bytes()
        with self.assertRaises(ValueError):
            m.run(self.lab, run_native=True, toy_only=True)
        with self.assertRaises(ValueError):
            m.new_json(self.lab, path.name, {"replacement": True})
        self.assertEqual(path.read_bytes(), original)
        m.cell.native_once.assert_called_once()

    def test_cli_is_compact_and_blocked_is_failure(self):
        for result, code in (
            ({"status": "blocked"}, 1),
            ({"status": "diagnosis_completed_not_qualified"}, 0),
        ):
            out = io.StringIO()
            with patch.object(m, "run", return_value=result), redirect_stdout(out):
                self.assertEqual(m.main(["--run-native-test", "--toy-only"]), code)
            self.assertLess(len(out.getvalue()), 200)
            self.assertEqual(
                json.loads(out.getvalue()),
                {**result, "qualification": False, "production_enabled": False},
            )
        out = io.StringIO()
        with (
            patch.object(m, "run", side_effect=ValueError("PRIVATE PATH")),
            redirect_stdout(out),
        ):
            self.assertEqual(m.main(["--run-native-test", "--toy-only"]), 1)
        self.assertNotIn("PRIVATE PATH", out.getvalue())


if __name__ == "__main__":
    unittest.main()
