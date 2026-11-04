"""Default-deny Stage A harness checks; fake COM/archives only, not fidelity proof."""

import importlib
import json
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch
import zipfile

m = importlib.import_module("native_fill_metadata_probe")
fixtures = importlib.import_module("test_hwp_form_draft")


def package(path, linear="no", with_cache=True):
    cells = (
        fixtures.cell(0, 0, "TEST LABEL")
        + fixtures.cell(0, 1, "TEST INPUT CELLS BELOW")
        + fixtures.cell(1, 0, "Name")
        + fixtures.cell(1, 1)
    )
    if not with_cache:
        cells = cells.replace(
            '<hp:linesegarray><hp:lineseg textpos="0"/></hp:linesegarray>', ""
        )  # Synthetic fixture only, not a candidate repair.
    fixtures.package(path, cells=cells)
    with zipfile.ZipFile(path, "a") as archive:
        archive.writestr(
            "Contents/content.hpf",
            f'<opf:package xmlns:opf="http://www.idpf.org/2007/opf/"><opf:metadata><opf:meta name="ModifiedDate">2026-10-10T00:00:00Z</opf:meta></opf:metadata><opf:manifest><opf:item id="header" href="Contents/header.xml"/></opf:manifest><opf:spine><opf:itemref idref="header" linear="yes"/><opf:itemref idref="section0" linear="{linear}"/></opf:spine></opf:package>',
        )


class MetadataProbeTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        (self.root / "work").mkdir()
        root = patch.object(m, "ROOT", self.root)
        root.start()
        self.addCleanup(root.stop)
        security = patch.object(
            m.security,
            "registered_security_module",
            return_value={"alias": "SYNTHETIC", "dll_sha256": "a" * 64},
        )
        security.start()
        self.addCleanup(security.stop)
        kernel_denied = patch.object(
            m.ctypes,
            "WinDLL",
            side_effect=AssertionError("OFFLINE: real OS process handle forbidden"),
        )
        kernel_denied.start()
        self.addCleanup(kernel_denied.stop)
        for denied in ("hwp_inventory", "window_pid"):
            patcher = patch.object(
                m,
                denied,
                side_effect=AssertionError(
                    "OFFLINE: real process/window observation forbidden"
                ),
            )
            patcher.start()
            self.addCleanup(patcher.stop)

    def target(self):
        return self.root / "work/toy-fill-metadata-SYNTHETIC"

    def test_no_native_without_both_explicit_flags_or_for_frozen(self):
        with patch.object(m, "native_stage") as native:
            for flags in (
                {},
                {"run_native": True},
                {"toy_only": True},
                {"run_native": 1, "toy_only": True},
            ):
                with self.assertRaises(ValueError):
                    m.run(self.target(), **flags)
            with (
                patch.object(m.sys, "frozen", True, create=True),
                self.assertRaises(ValueError),
            ):
                m.run(self.target(), run_native=True, toy_only=True)
            native.assert_not_called()
            self.assertFalse(self.target().exists())

    def test_outside_existing_or_wrong_name_rejected_before_native(self):
        targets = [self.root / "outside", self.root / "work/wrong-name", self.target()]
        self.target().mkdir()
        with patch.object(m, "native_stage") as native:
            for target in targets:
                with self.assertRaises(ValueError):
                    m.run(target, run_native=True, toy_only=True)
            native.assert_not_called()

    def test_save_helper_cannot_write_hwp_pdf_existing_or_external(self):
        app = SimpleNamespace(
            SaveAs=Mock(
                side_effect=lambda path, fmt, opts: Path(path).write_bytes(
                    b"SYNTHETIC HWPX"
                )
            )
        )
        for path in (
            self.root / "x.hwp",
            self.root / "x.pdf",
            self.root / "outside/x.hwpx",
        ):
            with self.assertRaises(ValueError):
                m.save_hwpx(app, path, self.root)
        app.SaveAs.assert_not_called()
        target = self.root / "x.hwpx"
        m.save_hwpx(app, target, self.root)
        self.assertEqual(app.SaveAs.call_args.args[1], "HWPX")
        with self.assertRaises(ValueError):
            m.save_hwpx(app, target, self.root)
        self.assertEqual(app.SaveAs.call_count, 1)

    def test_void_actions_and_real_exceptions_are_not_retried(self):
        app = SimpleNamespace(Run=Mock(return_value=None))
        m.action(app, "BreakPara")
        app.Run.assert_called_once()
        app.Run = Mock(return_value=False)
        with self.assertRaises(RuntimeError):
            m.action(app, "BreakPara")
        app.Run.assert_called_once()
        with self.assertRaises(ValueError):
            m.action(app, "ARBITRARY ACTION")

    def test_exact_precondition_and_target_input_do_not_repair_opf(self):
        source = self.root / "source.hwpx"
        prepared = self.root / "input.hwpx"
        package(source)
        raw, items = m.spine(source)
        self.assertTrue(m.has_precondition(items))
        self.assertFalse(m.has_precondition([*items, *items]))
        selected = m.selected_cell(source)
        self.assertEqual(selected[0]["cell_address"], {"rowAddr": "1", "colAddr": "1"})
        before = m.r.file_hash(source)
        m.fill.write_input(source, prepared, selected)
        self.assertEqual(m.spine(prepared)[0], raw)
        self.assertEqual(m.r.file_hash(source), before)
        self.assertIn(
            m.VALUE, m.r.archive(prepared)["Contents/section0.xml"].decode("utf-8")
        )

    def fake_com(
        self, linear="yes", blank: str | None = "", lingering=False, with_cache=True
    ):
        import pythoncom
        import win32com.client
        from win32com.client import gencache

        document = SimpleNamespace(Path="", FullName="", Modified=False)
        window = SimpleNamespace(Visible=False, WindowHandle=123)

        class FakeApp(SimpleNamespace):
            @property
            def PageCount(self):
                self.operations.append("layout")
                return (
                    self.page_counts.pop(0)
                    if len(self.page_counts) > 1
                    else self.page_counts[0]
                )

        app = FakeApp(
            operations=[],
            page_counts=[1, 1],
            XHwpWindows=SimpleNamespace(Item=lambda index: window),
            XHwpDocuments=SimpleNamespace(Count=1, Item=lambda index: document),
            GetTextFile=Mock(return_value=blank),
            RegisterModule=Mock(return_value=True),
            Version="SYNTHETIC",
            Clear=Mock(),
            Quit=Mock(),
            Open=Mock(return_value=True),
        )

        def save(path, fmt, options):
            app.operations.append("save:" + Path(path).name)
            self.assertEqual(fmt, "HWPX")
            package(Path(path), linear, with_cache=with_cache)
            return True

        app.SaveAs = Mock(side_effect=save)
        app.Open.side_effect = lambda *args: app.operations.append("open") or True
        calls = []
        row = {"pid": 73, "creation_ms": 0}

        def inventory():
            calls.append(True)
            if len(calls) == 2:
                row["creation_ms"] = int(
                    m.datetime.now(m.timezone.utc).timestamp() * 1000
                )
                return [dict(row)]
            return [dict(row)] if lingering and len(calls) >= 3 else []

        kernel = Mock()
        kernel.OpenProcess.return_value = 1
        kernel.WaitForSingleObject.return_value = 0
        kernel.CloseHandle.return_value = True
        app.exit_monitor = kernel
        for patcher in (
            patch.object(m.ctypes, "WinDLL", return_value=kernel),
            patch.object(pythoncom, "CoInitialize"),
            patch.object(pythoncom, "CoUninitialize"),
            patch.object(win32com.client, "DispatchEx", return_value=app),
            patch.object(gencache, "EnsureDispatch", side_effect=lambda value: value),
            patch.object(m, "author"),
            patch.object(m, "window_pid", return_value=73),
            patch.object(m, "hwp_inventory", side_effect=inventory),
        ):
            patcher.start()
            self.addCleanup(patcher.stop)
        return app

    def test_missing_precondition_stops_after_source_and_closes_only_owned_instance(
        self,
    ):
        app = self.fake_com(linear="yes")
        report = m.run(self.target(), run_native=True, toy_only=True)
        self.assertEqual(report["status"], "not_reproduced")
        self.assertEqual(app.SaveAs.call_count, 1)
        app.Open.assert_not_called()
        app.Quit.assert_called_once()
        self.assertEqual(report["cleanup"], "owned_clear_quit_completed")
        self.assertFalse((self.target() / "TOY-ONLY-filled-input.hwpx").exists())
        self.assertFalse(report["qualification"])
        with self.assertRaises(ValueError):
            m.run(self.target(), run_native=True, toy_only=True)
        self.assertEqual(app.SaveAs.call_count, 1)

    def test_unknown_blank_or_multiple_documents_never_edit_clear_or_quit(self):
        for index, value in enumerate(("OWNER NONEMPTY TEXT", "\ufeff", 0, b"")):
            app = self.fake_com()
            app.GetTextFile.return_value = value
            target = self.target().with_name(self.target().name + "-" + str(index))
            report = m.run(target, run_native=True, toy_only=True)
            self.assertEqual(report["status"], "blocked")
            self.assertEqual(report["failure_stage"], "verify_blank_ownership")
            app.SaveAs.assert_not_called()
            app.Clear.assert_not_called()
            app.Quit.assert_not_called()
            self.assertEqual(
                report["cleanup"], "ownership_unconfirmed_owner_inspection_required"
            )

    def test_none_blank_is_allowed_only_after_independent_ownership_checks(self):
        app = self.fake_com(blank=None)
        report = m.run(self.target(), run_native=True, toy_only=True)
        self.assertEqual(report["status"], "not_reproduced")
        self.assertTrue(report["window_pid_binding_verified"])
        self.assertTrue(report["owned_blank_verified"])
        self.assertEqual(report["blank_text_return_type"], "NoneType")
        self.assertTrue(report["owned_process_exit_confirmed"])
        app.Quit.assert_called_once()
        self.assertEqual(app.SaveAs.call_count, 1)
        app.Open.assert_not_called()

    def test_unknown_or_old_pid_even_with_none_never_reaches_document_read(self):
        app = self.fake_com(blank=None)
        now = int(m.datetime.now(m.timezone.utc).timestamp() * 1000)
        for rows in (
            [],
            [{"pid": 74, "creation_ms": now}],
            [{"pid": 73, "creation_ms": 1}],
            [{"pid": 73, "creation_ms": now}] * 2,
        ):
            with (
                patch.object(m, "hwp_inventory", return_value=rows),
                self.assertRaises(RuntimeError),
            ):
                m.verify_owned_blank(app, [], now - 100, {})
        with self.assertRaises(RuntimeError):
            m.verify_owned_blank(app, [{"pid": 99, "creation_ms": 1}], now, {})
        app.GetTextFile.assert_not_called()
        app.SaveAs.assert_not_called()
        app.Clear.assert_not_called()
        app.Quit.assert_not_called()

    def test_document_metadata_is_checked_before_accepting_any_none_return(self):
        app = self.fake_com(blank=None)
        doc = app.XHwpDocuments.Item(0)
        now = int(m.datetime.now(m.timezone.utc).timestamp() * 1000)
        for field, value in (
            ("Path", "PRIVATE PATH"),
            ("Path", None),
            ("FullName", "PRIVATE NAME"),
            ("Modified", True),
            ("Modified", None),
            ("Count", 2),
            ("Count", True),
        ):
            target = app.XHwpDocuments if field == "Count" else doc
            old = getattr(target, field)
            setattr(target, field, value)
            try:
                with (
                    patch.object(
                        m,
                        "hwp_inventory",
                        return_value=[{"pid": 73, "creation_ms": now}],
                    ),
                    self.assertRaises(RuntimeError),
                ):
                    m.verify_owned_blank(app, [], now - 100, {})
            finally:
                setattr(target, field, old)
        app.GetTextFile.assert_not_called()
        app.SaveAs.assert_not_called()
        app.Clear.assert_not_called()
        app.Quit.assert_not_called()

    def test_preexisting_hwp_stops_before_dispatch_or_blank_read(self):
        import win32com.client

        app = self.fake_com(blank=None)
        with (
            patch.object(
                m, "hwp_inventory", return_value=[{"pid": 99, "creation_ms": 1}]
            ),
            patch.object(win32com.client, "DispatchEx") as dispatch,
        ):
            report = m.run(self.target(), run_native=True, toy_only=True)
            dispatch.assert_not_called()
        self.assertEqual(report["status"], "blocked")
        self.assertEqual(report["failure_stage"], "require_no_preexisting_hwp")
        app.GetTextFile.assert_not_called()
        app.Quit.assert_not_called()

    def test_change_during_blank_read_does_not_grant_authoring_or_cleanup(self):
        app = self.fake_com(blank=None)
        document = app.XHwpDocuments.Item(0)

        def change(*args):
            document.Modified = True
            return None

        app.GetTextFile.side_effect = change
        report = m.run(self.target(), run_native=True, toy_only=True)
        self.assertEqual(report["status"], "blocked")
        app.SaveAs.assert_not_called()
        app.Clear.assert_not_called()
        app.Quit.assert_not_called()

    def test_lingering_owned_process_blocks_without_kill_or_retry(self):
        app = self.fake_com(blank=None, lingering=True)
        report = m.run(self.target(), run_native=True, toy_only=True)
        self.assertEqual(report["status"], "blocked")
        self.assertFalse(report["owned_process_exit_confirmed"])
        self.assertEqual(
            report["cleanup"], "owned_exit_unconfirmed_owner_inspection_required"
        )
        app.Quit.assert_called_once()
        self.assertEqual(app.SaveAs.call_count, 1)

    def test_compatible_purpose_is_explicit_and_does_not_change_legacy_eligibility(
        self,
    ):
        yes = [{"idref": "section0", "linear": "yes"}]
        omitted = [{"idref": "section0", "linear": None}]
        no = [{"idref": "section0", "linear": "no"}]
        self.assertFalse(m.has_precondition(yes))
        self.assertFalse(m.has_precondition(omitted))
        self.assertTrue(m.has_precondition(no))
        self.assertTrue(m.has_precondition(yes, "preserve-generated-yes"))
        self.assertTrue(m.has_precondition(omitted, "preserve-generated-yes"))
        self.assertFalse(m.has_precondition(no, "preserve-generated-yes"))
        with patch.object(m, "native_stage") as native:
            with self.assertRaises(ValueError):
                m.run(
                    self.target(),
                    run_native=True,
                    toy_only=True,
                    purpose="preserve-generated-yes",
                )
            with self.assertRaises(ValueError):
                m.run(
                    self.target(),
                    run_native=True,
                    toy_only=True,
                    purpose="unknown-purpose",
                )
            native.assert_not_called()

    def test_compatible_fake_insertion_uses_existing_gate_without_changing_opf(self):
        app = self.fake_com(linear="yes", blank=None)
        target = self.root / "work/toy-fill-compatible-SYNTHETIC"
        original_save = app.SaveAs.side_effect

        def save(path, fmt, options):
            if Path(path).name == "TOY-ONLY-reexport.hwpx":
                app.operations.append("save:" + Path(path).name)
                self.assertEqual(fmt, "HWPX")
                Path(path).write_bytes(
                    (target / "TOY-ONLY-filled-input.hwpx").read_bytes()
                )
                return True
            return original_save(path, fmt, options)

        app.SaveAs.side_effect = save
        app.GetTextFile.side_effect = [
            None,
            m.MARKER + " " + m.VALUE,
            m.MARKER + " " + m.VALUE,
            m.MARKER + " " + m.VALUE,
        ]
        report = m.run(
            target, run_native=True, toy_only=True, purpose="preserve-generated-yes"
        )
        self.assertEqual(
            report["status"], "stage_a_metadata_preserved_not_qualified", report
        )
        self.assertFalse(report["qualification"])
        self.assertFalse(report["is_legacy_no_reproduction_purpose"])
        self.assertFalse(report["legacy_no_condition_present"])
        self.assertFalse(report["preservation_contract_changed"])
        self.assertTrue(report["input_hpf_exact_source"])
        self.assertTrue(report["owned_process_exit_confirmed"])
        self.assertEqual(
            m.spine(target / "TOY-ONLY-source.hwpx")[0],
            m.spine(target / "TOY-ONLY-filled-input.hwpx")[0],
        )
        self.assertEqual(app.SaveAs.call_count, 2)
        self.assertEqual(
            app.operations,
            [
                "layout",
                "save:TOY-ONLY-source.hwpx",
                "open",
                "layout",
                "save:TOY-ONLY-reexport.hwpx",
            ],
        )
        self.assertTrue(report["source_selected_cache_valid"])
        self.assertEqual(report["source_native_pages"], report["filled_native_pages"])
        app.Open.assert_called_once()
        app.Quit.assert_called_once()
        app.exit_monitor.OpenProcess.assert_called_once_with(0x00100000, False, 73)
        app.exit_monitor.WaitForSingleObject.assert_called_once_with(1, 5000)
        app.exit_monitor.CloseHandle.assert_called_once_with(1)

    def test_exit_handle_timeout_is_blocking_even_if_inventory_no_longer_lists_pid(
        self,
    ):
        app = self.fake_com(blank=None)
        app.exit_monitor.WaitForSingleObject.return_value = 258
        report = m.run(self.target(), run_native=True, toy_only=True)
        self.assertEqual(report["status"], "blocked")
        self.assertFalse(report["owned_process_exit_confirmed"])
        app.exit_monitor.CloseHandle.assert_called_once_with(1)
        app.Quit.assert_called_once()

    def test_uncached_source_stops_before_filled_input_or_native_import(self):
        app = self.fake_com(blank=None, with_cache=False)
        target = self.root / "work/toy-fill-compatible-NO-CACHE"
        report = m.run(
            target, run_native=True, toy_only=True, purpose="preserve-generated-yes"
        )
        self.assertEqual(report["status"], "blocked")
        self.assertEqual(report["failure_stage"], "original_cache_preflight")
        self.assertFalse((target / "TOY-ONLY-filled-input.hwpx").exists())
        app.Open.assert_not_called()
        self.assertEqual(app.SaveAs.call_count, 1)
        app.Quit.assert_called_once()

    def test_layout_read_must_return_positive_typed_count_before_saving(self):
        app = self.fake_com(blank=None)
        app.page_counts = [False]
        report = m.run(self.target(), run_native=True, toy_only=True)
        self.assertEqual(report["status"], "blocked")
        self.assertEqual(report["failure_stage"], "read_source_native_layout")
        app.SaveAs.assert_not_called()
        app.Open.assert_not_called()
        app.Quit.assert_called_once()

    def test_page_change_after_insertion_blocks_reexport_instead_of_resizing(self):
        app = self.fake_com(blank=None)
        app.page_counts = [1, 2]
        app.GetTextFile.side_effect = [None, m.MARKER + " " + m.VALUE]
        target = self.root / "work/toy-fill-compatible-PAGE-CHANGE"
        report = m.run(
            target, run_native=True, toy_only=True, purpose="preserve-generated-yes"
        )
        self.assertEqual(report["status"], "blocked")
        self.assertEqual(report["failure_stage"], "read_filled_native_layout")
        self.assertEqual(app.SaveAs.call_count, 1)
        app.Open.assert_called_once()
        self.assertFalse((target / "TOY-ONLY-reexport.hwpx").exists())

    def test_source_cache_validation_never_writes_or_synthesizes_cache(self):
        source = self.root / "CACHE-SYNTHETIC.hwpx"
        package(source)
        selected = m.selected_cell(source)
        before = source.read_bytes()
        m.require_source_cache(source, selected)
        self.assertEqual(source.read_bytes(), before)

    def test_native_exception_preserves_one_report_and_no_retry_or_other_format(self):
        app = self.fake_com()
        app.SaveAs.side_effect = RuntimeError("SYNTHETIC COM FAILURE")
        report = m.run(self.target(), run_native=True, toy_only=True)
        self.assertEqual(report["status"], "blocked")
        self.assertEqual(report["failure_stage"], "save_new_source_hwpx_only")
        self.assertEqual(app.SaveAs.call_count, 1)
        app.Quit.assert_called_once()
        saved = json.loads(
            (self.target() / "STAGE-A-TEST-ONLY.json").read_text(encoding="utf-8")
        )
        self.assertFalse(saved["hwp_or_pdf_saved"])
        self.assertFalse(saved["qualification"])
        self.assertFalse(saved["approval_record_used"])


if __name__ == "__main__":
    unittest.main()
