"""Synthetic Native contract workers ONLY. No Hancom/real doc/actual consent."""

from copy import deepcopy
import importlib
from pathlib import Path
from types import SimpleNamespace, ModuleType
from unittest.mock import Mock, patch
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import test_hwp_native_plan as native_fixture
import test_hwp_fixed_layout as layout_fixture
import test_hwp_fill_plan as fill_fixture

r = importlib.import_module("hwp_review")
n = importlib.import_module("hwp_contract_apply")


class ContractWorkerTests(unittest.TestCase):
    def setUp(self):
        denied = patch.object(
            n.subprocess,
            "run",
            side_effect=AssertionError("OFFLINE TEST: child process forbidden"),
        )
        denied.start()
        self.addCleanup(denied.stop)

    def stage(self, schema=2, *, native_profile=True):
        inner = {
            2: native_fixture.NativePlanTests,
            3: layout_fixture.FixedLayoutTests,
            4: fill_fixture.FillTests,
        }[schema]()
        if schema == 4:
            inner.plain_native = native_profile
        self.addCleanup(inner.doCleanups)
        inner.setUp()
        w = (inner.w if schema == 4 else inner.path).resolve()
        manifest = deepcopy(inner.manifest)
        plan = deepcopy(inner.plan)
        manifest["anchors"] = [
            {key: value for key, value in anchor.items() if key != "raw"}
            for anchor in manifest.get("anchors", [])
        ]
        source = w / "SYNTHETIC-ONLY.hwp"
        source.write_bytes(b"SYNTHETIC NOT HWP; NEVER COM")
        pdf = w / "baseline.pdf"
        if not pdf.exists():
            pdf.write_bytes(b"SYNTHETIC PDF; HASH ONLY")
        manifest.update(
            source=str(source),
            source_audit={"sha256": r.file_hash(source), "embedded_binary_hashes": []},
            baseline_pdf_sha256=r.file_hash(pdf),
            roundtrip_passed=True,
        )
        r.write_json(w / "manifest.json", manifest)
        r.write_json(w / n.PROPOSALS[schema], plan)
        r.write_json(
            w / "approved-plan.json",
            {
                "approved_via": "local-review-ui",
                "plan": plan,
                "plan_sha256": r.plan_hash(plan),
                "approved_scope": r.native_approval_scope(plan),
            },
        )
        run = w / "runs" / "20261013T000000-a1b2c3d4"
        run.mkdir(parents=True)
        r.write_json(run / "plan.json", plan)
        lease = r.acquire_apply_lock(w, manifest, plan, run.name)
        request = {
            "schema": schema,
            "phase": 0,
            "lease": lease,
            "input_sha256": r.file_hash(source),
            "plan_sha256": r.plan_hash(plan),
            "approval_scope_sha256": r.plan_hash(r.native_approval_scope(plan)),
        }
        r.write_json(run / "contract-export-request-0.json", request)
        return w, run, source, plan, request

    def test_all_three_exact_contracts_derive_paths_without_native_or_output(self):
        for schema in (2, 3, 4):
            with self.subTest(schema=schema):
                w, run, source, plan, request = self.stage(schema)
                paths = n.step_paths(w, run.name, 0)
                self.assertEqual(paths[2], source)
                self.assertEqual(paths[7], request)
                self.assertEqual(
                    paths[3],
                    (
                        run / "candidate-buffer.pdf",
                        run / "candidate.hwpx",
                        run / "candidate.hwp",
                    ),
                )
                self.assertFalse(paths[5].exists())
                self.assertFalse(paths[6].exists())
                self.assertFalse(paths[4].exists())

    def test_actual_unsupported_fill_profile_rejects_both_phases_before_worker_or_copy(
        self,
    ):
        for phase in (0, 1):
            w, run, _, plan, _ = self.stage(4, native_profile=False)
            self.assertEqual(len(r.approved_plan(w, plan, r.verify_baseline(w))), 2)
            with (
                patch.object(n, "registered_security_module") as module,
                patch.object(n, "_native_session") as session,
            ):
                with self.assertRaises(ValueError):
                    n.export_phase(w, run.name, phase)
                module.assert_not_called()
                session.assert_not_called()
            self.assertFalse((run / f"contract-source-copy-{phase}.hwp").exists())
            self.assertTrue((w / r.APPLY_LOCK).exists())

    def test_schema_phase_current_policy_lease_and_input_changes_fail_closed(self):
        changes = [
            lambda p: p.update(schema=True),
            lambda p: p.update(phase=True),
            lambda p: p.update(schema=1),
            lambda p: p.update(approval_scope_sha256="0" * 64),
            lambda p: p["lease"].update(owner_nonce="0" * 32),
            lambda p: p.update(input_sha256="0" * 64),
            lambda p: p.update(unexpected=True),
        ]
        for change in changes:
            w, run, _, _, request = self.stage()
            change(request)
            r.write_json(run / "contract-export-request-0.json", request)
            with self.assertRaises((ValueError, RuntimeError)):
                n.step_paths(w, run.name, 0)
            self.assertFalse((run / "contract-source-copy-0.hwp").exists())
        w, run, _, plan, _ = self.stage()
        plan["edits"][0]["new_text"] = "unapproved"
        r.write_json(w / "proposal-native.json", plan)
        with self.assertRaises(ValueError):
            n.step_paths(w, run.name, 0)

    def test_old_schema1_consent_cannot_approve_native_and_reuses_fail(self):
        w, run, _, _, _ = self.stage()
        approval = r.read_json(w / "approved-plan.json")
        approval.pop("approved_scope")
        r.write_json(w / "approved-plan.json", approval)
        with self.assertRaises(ValueError):
            n.step_paths(w, run.name, 0)
        for name in (
            "candidate.hwp",
            "contract-source-copy-0.hwp",
            "contract-export-reply-0.json",
        ):
            w, run, _, _, _ = self.stage()
            (run / name).write_bytes(b"KEEP")
            with self.assertRaises(FileExistsError):
                n.step_paths(w, run.name, 0)
            self.assertEqual((run / name).read_bytes(), b"KEEP")

    def test_missing_module_never_copies_starts_com_or_unsets_lease(self):
        w, run, _, _, _ = self.stage(3)
        with patch.object(
            n,
            "registered_security_module",
            side_effect=ValueError("SYNTHETIC missing module"),
        ):
            with self.assertRaises(ValueError):
                n.export_phase(w, run.name, 0)
        self.assertFalse((run / "contract-source-copy-0.hwp").exists())
        self.assertTrue((w / r.APPLY_LOCK).exists())

    def test_fixed_parent_command_uses_no_stdin_and_reply_binds_scope(self):
        w, run, source, plan, request = self.stage(3)
        (run / "contract-export-request-0.json").unlink()

        def child(command, **kwargs):
            self.assertEqual(
                command[1:],
                [
                    "--contract-export",
                    str(w),
                    "--contract-run",
                    run.name,
                    "--contract-phase",
                    "0",
                ],
            )
            self.assertEqual(kwargs["stdin"], n.subprocess.DEVNULL)
            self.assertEqual(kwargs["timeout"], 240)
            r.write_json(
                run / "contract-export-reply-0.json",
                {
                    "ok": True,
                    "schema": 3,
                    "phase": 0,
                    "plan_sha256": r.plan_hash(plan),
                    "approval_scope_sha256": request["approval_scope_sha256"],
                    "native": {"SYNTHETIC": True},
                },
            )
            return SimpleNamespace(returncode=0)

        exporter = n.NativeContractExporter(w, r.plan_hash(plan))
        with (
            patch.object(n, "frozen", return_value=True),
            patch.object(n.subprocess, "run", side_effect=child),
            patch.object(r, "native_export") as generic,
        ):
            result = exporter(
                source,
                run / "candidate-buffer.pdf",
                hwpx=run / "candidate.hwpx",
                hwp=run / "candidate.hwp",
                native_workspace=w,
                native_plan=run / "plan.json",
            )
            self.assertTrue(result["SYNTHETIC"])
            self.assertEqual(exporter.phase, 1)
            generic.assert_not_called()

    def test_zero_exit_unbound_reply_rejected_and_counter_preserved(self):
        w, run, source, plan, request = self.stage(3)
        (run / "contract-export-request-0.json").unlink()

        def child(*args, **kwargs):
            r.write_json(
                run / "contract-export-reply-0.json",
                {
                    "ok": True,
                    "schema": 4,
                    "phase": 0,
                    "plan_sha256": r.plan_hash(plan),
                    "approval_scope_sha256": request["approval_scope_sha256"],
                    "native": {"SYNTHETIC": True},
                },
            )
            return SimpleNamespace(returncode=0)

        exporter = n.NativeContractExporter(w, r.plan_hash(plan))
        with patch.object(n.subprocess, "run", side_effect=child):
            with self.assertRaises(ValueError):
                exporter(
                    source,
                    run / "candidate-buffer.pdf",
                    hwpx=run / "candidate.hwpx",
                    hwp=run / "candidate.hwp",
                    native_workspace=w,
                    native_plan=run / "plan.json",
                )
        self.assertEqual(exporter.phase, 0)
        self.assertTrue((w / r.APPLY_LOCK).exists())

    def test_unsupported_schema4_profile_before_any_copy_or_com(self):
        w, run, source, _, _ = self.stage(4)
        before = source.read_bytes()
        app = Mock()
        app.PageCount = 1
        app.Version = "SYNTHETIC"
        app.GetTextFile.return_value = "SYNTHETIC FULL BUFFER"
        app.SaveAs.side_effect = lambda path, *args: (
            Path(path).write_bytes(b"SYNTHETIC NATIVE OUTPUT") > 0
        )
        com = ModuleType("pythoncom")
        setattr(com, "CoInitialize", Mock())
        setattr(com, "CoUninitialize", Mock())
        client = ModuleType("win32com.client")
        setattr(client, "DispatchEx", Mock(return_value=app))
        cache = SimpleNamespace(EnsureDispatch=lambda app: app)
        setattr(client, "gencache", cache)
        package = ModuleType("win32com")
        setattr(package, "client", client)
        with (
            patch.dict(
                sys.modules,
                {"pythoncom": com, "win32com": package, "win32com.client": client},
            ),
            patch.object(
                n, "registered_security_module", return_value={"SYNTHETIC": True}
            ),
        ):
            with patch.object(
                importlib.import_module("hwp_native_cells"),
                "plain_profile",
                side_effect=ValueError("SYNTHETIC unsupported fill profile"),
            ):
                with self.assertRaisesRegex(ValueError, "unsupported fill profile"):
                    n.export_phase(w, run.name, 0)
        self.assertEqual(source.read_bytes(), before)
        app.Open.assert_not_called()
        getattr(client, "DispatchEx").assert_not_called()
        getattr(com, "CoInitialize").assert_not_called()
        self.assertFalse((run / "contract-approved-input.hwpx").exists())
        self.assertTrue((w / r.APPLY_LOCK).exists())

    def test_unqualified_schema2_never_launches_parent_worker(self):
        w, run, source, plan, _ = self.stage(2)
        (run / "contract-export-request-0.json").unlink()
        exporter = n.NativeContractExporter(w, r.plan_hash(plan))
        with patch.object(n.subprocess, "run") as child:
            with self.assertRaisesRegex(ValueError, "has not passed"):
                exporter(
                    source,
                    run / "candidate-buffer.pdf",
                    hwpx=run / "candidate.hwpx",
                    hwp=run / "candidate.hwp",
                    native_workspace=w,
                    native_plan=run / "plan.json",
                )
            child.assert_not_called()
        self.assertFalse((run / "contract-export-request-0.json").exists())
        self.assertEqual(exporter.phase, 0)

    def test_typed_hook_rejects_schema1_and_wrong_paths_before_lease(self):
        w, run, _, plan, _ = self.stage()
        (w / r.APPLY_LOCK).unlink()
        exporter = n.NativeContractExporter(w, r.plan_hash(plan))
        r.write_json(w / "proposal.json", plan)
        with self.assertRaises(ValueError):
            r.apply(
                w,
                w / "proposal.json",
                contract_exporter=exporter,
                expected_plan_sha256=r.plan_hash(plan),
            )
        self.assertFalse((w / r.APPLY_LOCK).exists())


if __name__ == "__main__":
    unittest.main()
