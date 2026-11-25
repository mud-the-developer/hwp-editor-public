"""Fixed-worker saved-HWP structure tests. TEMP fake documents; no real COM."""

import importlib
from pathlib import Path
from types import SimpleNamespace, ModuleType
import sys
import unittest
from unittest.mock import Mock, PropertyMock, patch
import zipfile
from test_com_cache import cached_com_modules
from test_native_owner import fake_owned_session

old = importlib.import_module("test_contract_apply")
r = old.r
n = old.n


class ReopenedContractTests(unittest.TestCase):
    def setUp(self):
        self.harness = old.ContractWorkerTests()
        self.harness.setUp()
        self.addCleanup(self.harness.doCleanups)

    def stage(self):
        w, run, _, plan, request = self.harness.stage(4)
        baseline = w / "baseline.hwpx"
        selected = r.approved_plan(w, plan, r.verify_baseline(w))
        r.hwp_fill_plan.write_input(baseline, run / "candidate.hwpx", selected)
        source = run / "candidate.hwp"
        source.write_bytes(b"SYNTHETIC SAVED HWP")
        request = {**request, "phase": 1, "input_sha256": r.file_hash(source)}
        r.write_json(run / "contract-export-request-1.json", request)
        return w, run, source, plan, request

    def change_export(self, path, *, wrong_text):
        parts = r.archive(path)
        part, before, after = (
            ("Contents/section0.xml", b"Value B", b"Wrong B")
            if wrong_text
            else (
                "Contents/content.hpf",
                b'idref="section0" linear="yes"',
                b'idref="section0" linear="no"',
            )
        )
        self.assertIn(before, parts[part])
        parts[part] = parts[part].replace(before, after)
        with zipfile.ZipFile(path, "w") as archive:
            for name, raw in parts.items():
                archive.writestr(name, raw)

    def parent(self, *, mode="valid", gate=None):
        w, run, source, plan, request = self.stage()
        (run / "contract-export-request-1.json").unlink()
        candidate_bytes = (run / "candidate.hwpx").read_bytes()
        protected = {
            path: r.file_hash(path)
            for path in (
                source,
                w / "baseline.hwpx",
                w / "approved-plan.json",
                w / r.APPLY_LOCK,
                run / "plan.json",
            )
        }

        def child(*args, **kwargs):
            reopened = run / "candidate-reopened.hwpx"
            if mode != "missing":
                reopened.write_bytes(candidate_bytes)
                if mode in ("drift", "reading_order"):
                    self.change_export(reopened, wrong_text=(mode == "drift"))
            (run / "candidate.pdf").write_bytes(b"SYNTHETIC REOPEN PDF")
            native = {
                "reopened_hwpx_sha256": r.file_hash(reopened)
                if reopened.exists()
                else "0" * 64,
                "saved_hwp_contract": {"SYNTHETIC_WORKER_CLAIM_ONLY": True},
            }
            if mode == "hash":
                native["reopened_hwpx_sha256"] = "0" * 64
            if mode == "approval":
                approval = r.read_json(w / "approved-plan.json")
                approval["approved_scope"] = {}
                r.write_json(w / "approved-plan.json", approval)
            if mode == "plan":
                changed = r.read_json(run / "plan.json")
                changed["unapproved_metadata"] = "CHANGED"
                r.write_json(run / "plan.json", changed)
            if mode == "lease":
                lease = r.read_json(w / r.APPLY_LOCK)
                lease["owner_nonce"] = "0" * 32
                r.write_json(w / r.APPLY_LOCK, lease)
            r.write_json(
                run / "contract-export-reply-1.json",
                {
                    "ok": True,
                    "schema": 4,
                    "phase": 1,
                    "plan_sha256": r.plan_hash(plan),
                    "approval_scope_sha256": request["approval_scope_sha256"],
                    "native": native,
                },
            )
            return SimpleNamespace(wait=lambda timeout: 0)

        exporter = n.NativeContractExporter(w, r.plan_hash(plan))
        exporter.phase = 1
        context = (
            patch.object(n, "contract_gate", side_effect=gate)
            if gate
            else patch.object(n, "contract_gate", wraps=n.contract_gate)
        )
        with patch.object(n.subprocess, "Popen", side_effect=child), context as checked:
            try:
                result = exporter(source, run / "candidate.pdf")
            except (ValueError, RuntimeError):
                self.assertEqual(exporter.phase, 1)
                self.assertTrue((w / r.APPLY_LOCK).exists())
                self.assertEqual((run / "candidate.hwpx").read_bytes(), candidate_bytes)
                if mode == "reading_order":
                    self.assertEqual(
                        {path: r.file_hash(path) for path in protected}, protected
                    )
                raise
        return result, exporter, w, run, checked

    def test_phase1_internal_hwpx_is_unique_but_public_call_stays_pdf_only(self):
        w, run, _, _, _ = self.stage()
        paths = n.step_paths(w, run.name, 1)
        self.assertEqual(
            paths[3], (run / "candidate.pdf", run / "candidate-reopened.hwpx", None)
        )
        (run / "candidate-reopened.hwpx").write_bytes(b"KEEP FAILED EVIDENCE")
        with self.assertRaises(FileExistsError):
            n.step_paths(w, run.name, 1)
        self.assertEqual(
            (run / "candidate-reopened.hwpx").read_bytes(), b"KEEP FAILED EVIDENCE"
        )

    def test_parent_independently_checks_reopened_bytes_and_contract(self):
        result, exporter, w, run, checked = self.parent()
        self.assertEqual(exporter.phase, 2)
        self.assertEqual(
            result["parent_saved_hwp_contract"]["exact_empty_cells_filled"], 2
        )
        self.assertEqual(
            result["reopened_hwpx_sha256"], r.file_hash(run / "candidate-reopened.hwpx")
        )
        self.assertTrue(
            any(
                call.args[1] == run / "candidate-reopened.hwpx"
                for call in checked.call_args_list
            )
        )
        self.assertTrue((w / r.APPLY_LOCK).exists())

    def test_missing_or_wrong_hash_never_advances_parent_phase(self):
        for mode in ("missing", "hash"):
            with (
                self.subTest(mode=mode),
                self.assertRaisesRegex(ValueError, "proof missing or changed"),
            ):
                self.parent(mode=mode)

    def test_bound_reply_cannot_hide_reopened_structural_drift(self):
        with self.assertRaises(ValueError):
            self.parent(mode="drift")

    def test_bound_positive_reply_cannot_hide_changed_reading_order(self):
        with self.assertRaises(ValueError):
            self.parent(mode="reading_order")

    def test_current_approval_and_owned_lease_are_still_rechecked(self):
        for mode in ("approval", "lease", "plan"):
            with self.subTest(mode=mode), self.assertRaises((ValueError, RuntimeError)):
                self.parent(mode=mode)

    def test_reopened_mutation_during_parent_gate_is_rejected(self):
        actual = n.contract_gate

        def gate(workspace, candidate, plan, selected):
            result = actual(workspace, candidate, plan, selected)
            if candidate.name == "candidate-reopened.hwpx":
                candidate.write_bytes(b"CHANGED DURING PARENT CHECK")
            return result

        with self.assertRaisesRegex(ValueError, "changed during parent verification"):
            self.parent(gate=gate)

    @patch.object(n, "owned_session", new=fake_owned_session)
    def fake_worker(
        self, *, drift=False, reading_order=False, page_count: int | None = 1
    ):
        w, run, _, _, _ = self.stage()
        valid = (run / "candidate.hwpx").read_bytes()
        app = Mock()
        app.Version = "SYNTHETIC"
        app.GetTextFile.return_value = "UNCHANGED SYNTHETIC BUFFER"
        page_reads = []

        def pages():
            page_reads.append(app.SaveAs.call_count)
            if page_count is None:
                raise RuntimeError("SYNTHETIC PageCount unavailable")
            return page_count

        def save(path, fmt, options):
            self.assertEqual(
                page_reads, [0], "Page readiness must precede every export"
            )
            target = Path(path)
            if fmt == "HWPX":
                target.write_bytes(valid)
                if drift or reading_order:
                    self.change_export(target, wrong_text=drift)
            elif fmt == "PDF":
                target.write_bytes(b"SYNTHETIC PDF")
            else:
                raise AssertionError("Phase1 must not save another HWP")
            return True

        app.SaveAs.side_effect = save
        com = ModuleType("pythoncom")
        setattr(com, "CoInitialize", Mock())
        setattr(com, "CoUninitialize", Mock())
        client = ModuleType("win32com.client")
        setattr(client, "DispatchEx", Mock(return_value=app))
        setattr(client, "gencache", SimpleNamespace(EnsureDispatch=lambda value: value))
        with (
            patch.object(
                type(app),
                "PageCount",
                new_callable=PropertyMock,
                side_effect=pages,
                create=True,
            ),
            patch.dict(sys.modules, cached_com_modules(com, client, w)),
            patch.object(
                n, "registered_security_module", return_value={"SYNTHETIC": True}
            ),
        ):
            try:
                result = n.export_phase(w, run.name, 1)
            finally:
                app.Clear.assert_called_once_with(1)
                app.Quit.assert_called_once()
                getattr(com, "CoUninitialize").assert_called_once()
                self.assertTrue((w / r.APPLY_LOCK).exists())
                if page_count is None or page_count < 1:
                    app.SaveAs.assert_not_called()
        self.assertEqual(
            page_reads,
            [0, 2],
            "Keep the final page-count observation after strict gates",
        )
        self.assertEqual(
            [call.args[1] for call in app.SaveAs.call_args_list], ["HWPX", "PDF"]
        )
        self.assertEqual(
            result["reopened_hwpx_sha256"], r.file_hash(run / "candidate-reopened.hwpx")
        )
        return result

    def test_worker_exports_and_checks_reopened_hwpx_before_returning(self):
        result = self.fake_worker()
        self.assertEqual(result["saved_hwp_contract"]["exact_empty_cells_filled"], 2)

    def test_nonpositive_pages_stop_before_any_export(self):
        for count in (0, -1):
            with (
                self.subTest(count=count),
                self.assertRaisesRegex(ValueError, "not ready before export"),
            ):
                self.fake_worker(page_count=count)

    def test_page_readiness_error_stops_before_any_export(self):
        with self.assertRaisesRegex(RuntimeError, "PageCount unavailable"):
            self.fake_worker(page_count=None)

    def test_worker_does_not_attest_when_saved_structure_drifted(self):
        with self.assertRaises(ValueError):
            self.fake_worker(drift=True)

    def test_worker_does_not_attest_when_saved_reading_order_changes(self):
        with self.assertRaises(ValueError):
            self.fake_worker(reading_order=True)


if __name__ == "__main__":
    unittest.main()
