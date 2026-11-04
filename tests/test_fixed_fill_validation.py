"""Own-case adapter seams are code-owned and fail closed; NO real COM or Native."""

import importlib
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch
import test_contract_apply as workers
import test_native_owner as ownership_fixtures

m = importlib.import_module("native_fixed_fill_validation")
approve_handler = m.approve_fixture
n = m.n
r = m.r


class FixedValidationTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name).resolve()
        (self.root / "work").mkdir()
        for patcher in (
            patch.object(m, "ROOT", self.root),
            patch.object(m, "hwp_inventory", return_value=[]),
            patch.object(m, "registered_security_module"),
            patch.object(
                m.cell,
                "native_once",
                side_effect=AssertionError("OFFLINE no Native source"),
            ),
            patch.object(
                m, "approve_fixture", side_effect=AssertionError("OFFLINE no approval")
            ),
        ):
            patcher.start()
            self.addCleanup(patcher.stop)

    def test_entry_needs_literal_flags_new_own_path_and_source_runtime(self):
        target = self.root / "work/toy-fixed-fill-SYNTHETIC"
        for kw in ({}, {"run_native": True}, {"run_native": 1, "toy_only": True}):
            with self.assertRaises(ValueError):
                m.run(target, **kw)
        with (
            patch.object(m.sys, "frozen", True, create=True),
            self.assertRaises(ValueError),
        ):
            m.run(target, run_native=True, toy_only=True)
        for path in (self.root / "OUTSIDE", self.root / "work/toy-direct-fill-WRONG"):
            with self.assertRaises(ValueError):
                m.run(path, run_native=True, toy_only=True)
        target.mkdir()
        with self.assertRaises(ValueError):
            m.run(target, run_native=True, toy_only=True)
        m.cell.native_once.assert_not_called()
        m.approve_fixture.assert_not_called()

    def test_failed_creator_does_not_approve_or_start_worker(self):
        def failed(folder, proof):
            proof.update(status="blocked")

        with (
            patch.object(m.cell, "native_once", side_effect=failed) as source,
            patch.object(m, "OwnToyExporter") as adapter,
        ):
            report = m.run(
                self.root / "work/toy-fixed-fill-FAILED", run_native=True, toy_only=True
            )
        self.assertEqual(report["status"], "blocked")
        source.assert_called_once()
        adapter.assert_not_called()
        m.approve_fixture.assert_not_called()

    def test_private_worker_seam_requires_guard_and_denies_before_security_copy_or_session(
        self,
    ):
        fixture = workers.ContractWorkerTests()
        fixture.setUp()
        self.addCleanup(fixture.doCleanups)
        workspace, run, _, _, _ = fixture.stage(4)
        session = Mock(side_effect=AssertionError("OFFLINE no COM"))
        with self.assertRaises(ValueError):
            n._export_phase(workspace, run.name, 0, None, session)
        guard = Mock(side_effect=ValueError("SYNTHETIC deny"))
        with (
            patch.object(n, "registered_security_module") as security,
            self.assertRaises(ValueError),
        ):
            n._export_phase(workspace, run.name, 0, guard, session)
        guard.assert_called_once()
        security.assert_not_called()
        session.assert_not_called()
        self.assertFalse((run / "contract-source-copy-0.hwp").exists())

    def test_parent_denies_schema2_and_worker_denies_unsupported_fill_profile(self):
        fixture = workers.ContractWorkerTests()
        fixture.setUp()
        self.addCleanup(fixture.doCleanups)
        workspace, run, _, plan, _ = fixture.stage(4)
        with self.assertRaises(ValueError):
            n.NativeContractExporter(workspace, r.plan_hash(plan))._require_qualified(
                {"schema": 2}
            )
        with (
            patch.object(
                importlib.import_module("hwp_native_cells"),
                "plain_profile",
                side_effect=ValueError("SYNTHETIC unsupported profile"),
            ),
            patch.object(n, "_native_session") as session,
            self.assertRaises(ValueError),
        ):
            n.export_phase(workspace, run.name, 0)
        session.assert_not_called()
        self.assertEqual(n.QUALIFIED_SCHEMAS, (3, 4))

    def test_own_guard_rejects_arbitrary_or_restored_case_before_proof_reads(self):
        adapter = object.__new__(m.OwnToyExporter)
        adapter.workspace = self.root / "work/arbitrary/review"
        adapter.source = self.root / "REAL-REPORT.hwp"
        adapter.proof = {}
        adapter.phase = 0
        with (
            patch.object(m.direct, "require_binding") as proof,
            self.assertRaises(ValueError),
        ):
            adapter._require_qualified({})
        proof.assert_not_called()

    def test_own_guard_checks_hash_phase_plan_and_exact_source_binding(self):
        folder = self.root / "work/toy-fixed-fill-SYNTHETIC"
        workspace = folder / "source" / m.project.REVIEW_DIR
        source = folder / "source/TOY-ONLY-source.hwp"
        source.parent.mkdir(parents=True)
        workspace.mkdir()
        source.write_bytes(b"SYNTHETIC")
        (workspace / "baseline.hwpx").write_bytes(b"SYNTHETIC BASE")
        plan = {"schema": 4, "record": {"values": m.direct.VALUES}}
        proof = {"reopened_observation": {"source_sha256": r.file_hash(source)}}
        manifest = {
            "source": str(source),
            "source_audit": {"sha256": r.file_hash(source)},
        }
        selected = [
            {"cell_address": {"rowAddr": str(row), "colAddr": "1"}} for row in (1, 2, 3)
        ]
        with (
            patch.object(m.direct, "require_binding"),
            patch.object(r, "verify_baseline", return_value=manifest),
            patch.object(m.fill, "validate", return_value=selected),
            patch.object(
                importlib.import_module("hwp_native_cells"),
                "plain_profile",
                return_value={"rows": 4, "cols": 2},
            ),
        ):
            adapter = m.OwnToyExporter(workspace, source, proof, plan, {})
            adapter._require_qualified(plan)
            adapter.phase = 2
            with self.assertRaises(ValueError):
                adapter._require_qualified(plan)
            adapter.phase = 0
            with self.assertRaises(ValueError):
                adapter._require_qualified({**plan, "schema": 3})
            source.write_bytes(b"CHANGED")
            with self.assertRaises(ValueError):
                adapter._require_qualified(plan)

    def test_run_uses_real_apply_signature_and_separate_approval_boundary(self):
        package = importlib.import_module("test_full_fill_harness")

        def creator(folder, proof):
            (folder / "TOY-ONLY-source.hwp").write_bytes(b"SYNTHETIC NOT HWP")
            proof["reopened_observation"] = {
                "source_sha256": r.file_hash(folder / "TOY-ONLY-source.hwp")
            }

        def prepare(source, workspace, *, exporter):
            self.assertIsInstance(exporter, m.project.BaselineExporter)
            self.assertEqual(exporter.workspace, workspace)
            self.assertEqual(workspace, source.parent / m.project.REVIEW_DIR)
            self.assertEqual(exporter.phase, 0)
            exporter.phase = 3
            workspace.mkdir()
            package.package(workspace / "baseline.hwpx")
            package.pdf(workspace / "baseline.pdf")
            r.write_json(
                workspace / "manifest.json",
                {"workspace_id": "SYNTHETIC", "source": str(source)},
            )
            r.write_json(workspace / "comments.json", {"schema": 1, "comments": []})

        manifest = {"workspace_id": "SYNTHETIC", "protected_pages": []}
        calls = []

        def apply(
            workspace,
            plan_path,
            *,
            text_exporter=None,
            contract_exporter: SimpleNamespace | None = None,
            expected_plan_sha256=None,
        ):
            self.assertEqual(plan_path, workspace / "proposal-fill.json")
            self.assertEqual(expected_plan_sha256, r.plan_hash(r.read_json(plan_path)))
            calls.append(1)
            if not (workspace / "approved-plan.json").exists():
                raise FileNotFoundError(
                    2, "SYNTHETIC unapproved", str(workspace / "approved-plan.json")
                )
            assert contract_exporter is not None
            contract_exporter.phase = 2
            return {"status": "SYNTHETIC GATES ONLY"}

        def approve(workspace, plan, report):
            r.write_json(
                workspace / "approved-plan.json", {"SYNTHETIC_TEST_ONLY": True}
            )

        plan = {"schema": 4, "record": {"values": m.direct.VALUES}, "placements": []}
        exporter = SimpleNamespace(phase=0)
        attempt = {
            "plan_sha256": r.plan_hash(plan),
            "status": "checks_passed_needs_human_visual_review",
            "artifact_integrity": "verified",
        }
        with (
            patch.object(m.cell, "native_once", side_effect=creator),
            patch.object(m.direct, "require_binding"),
            patch.object(r, "prepare", side_effect=prepare),
            patch.object(r, "verify_baseline", return_value=manifest),
            patch.object(m.fill, "proposal", return_value=plan),
            patch.object(m, "OwnToyExporter", return_value=exporter),
            patch.object(m, "approve_fixture", side_effect=approve),
            patch.object(r, "apply", side_effect=apply),
            patch.object(r, "review_attempts", return_value=[attempt]),
        ):
            result = m.run(
                self.root / "work/toy-fixed-fill-POSITIVE",
                run_native=True,
                toy_only=True,
            )
        self.assertEqual(len(calls), 2)
        self.assertEqual(result["status"], "checks_passed_needs_human_visual_review")
        self.assertFalse(result["qualification"])
        self.assertEqual(
            result["preparation_exporter"], "app_readonly_owned_baseline_exporter"
        )
        self.assertEqual(result["preparation_phases"], 3)

    def test_owned_preparation_layout_passes_real_path_gate_before_any_native(self):
        def creator(folder, proof):
            (folder / "TOY-ONLY-source.hwp").write_bytes(b"SYNTHETIC NOT HWP")

        def prepare(source, workspace, *, exporter):
            self.assertIsInstance(exporter, m.project.BaselineExporter)
            self.assertEqual(workspace, source.parent / m.project.REVIEW_DIR)
            workspace.mkdir()
            r.write_json(
                workspace / "preparing.json",
                {
                    "source": str(source.resolve()),
                    "source_audit": {"sha256": r.file_hash(source)},
                },
            )
            actual, outputs, reply = m.project.step_paths(workspace, 0)
            self.assertEqual(actual, source)
            self.assertEqual(
                outputs, (workspace / "baseline.pdf", workspace / "baseline.hwpx", None)
            )
            self.assertEqual(reply, workspace / "native-prepare-step-1.json")
            raise RuntimeError("SYNTHETIC accepted paths; stop before Native")

        with (
            patch.object(m.cell, "native_once", side_effect=creator),
            patch.object(m.direct, "require_binding"),
            patch.object(r, "prepare", side_effect=prepare) as prepared,
            patch.object(m.project.subprocess, "run") as process,
            patch.object(m, "OwnToyExporter") as adapter,
        ):
            report = m.run(
                self.root / "work/toy-fixed-fill-PATHS", run_native=True, toy_only=True
            )
        self.assertEqual(report["status"], "blocked")
        self.assertIn("accepted paths", report["error"])
        prepared.assert_called_once()
        process.assert_not_called()
        adapter.assert_not_called()
        m.approve_fixture.assert_not_called()

    def test_old_review_folder_shape_is_rejected_before_native_proof(self):
        folder = self.root / "work/toy-fixed-fill-SYNTHETIC"
        adapter = object.__new__(m.OwnToyExporter)
        adapter.source = folder / "source/TOY-ONLY-source.hwp"
        adapter.proof = {}
        for workspace in (folder / "review", folder / "source/review"):
            with (
                self.subTest(workspace=workspace),
                patch.object(m.direct, "require_binding") as proof,
            ):
                adapter.workspace = workspace
                with self.assertRaises(ValueError):
                    adapter._require_qualified({})
                proof.assert_not_called()

    def test_incomplete_app_prepare_blocks_before_manifest_plan_approval_and_worker(
        self,
    ):
        def creator(folder, proof):
            (folder / "TOY-ONLY-source.hwp").write_bytes(b"SYNTHETIC NOT HWP")

        def prepare(source, workspace, *, exporter):
            self.assertIsInstance(exporter, m.project.BaselineExporter)
            self.assertEqual((exporter.workspace, exporter.phase), (workspace, 0))
            exporter.phase = 2

        with (
            patch.object(m.cell, "native_once", side_effect=creator),
            patch.object(m.direct, "require_binding"),
            patch.object(r, "prepare", side_effect=prepare) as prepared,
            patch.object(r, "verify_baseline") as manifest,
            patch.object(m.fill, "proposal") as plan,
            patch.object(m, "OwnToyExporter") as adapter,
        ):
            report = m.run(
                self.root / "work/toy-fixed-fill-INCOMPLETE",
                run_native=True,
                toy_only=True,
            )
        self.assertEqual(report["status"], "blocked")
        self.assertIn("Incomplete owned baseline", report["error"])
        prepared.assert_called_once()
        manifest.assert_not_called()
        plan.assert_not_called()
        adapter.assert_not_called()
        m.approve_fixture.assert_not_called()

    def test_prepare_error_never_falls_back_to_generic_exporter_or_approval(self):
        def creator(folder, proof):
            (folder / "TOY-ONLY-source.hwp").write_bytes(b"SYNTHETIC NOT HWP")

        legacy = importlib.import_module("native_hwp")
        with (
            patch.object(m.cell, "native_once", side_effect=creator),
            patch.object(m.direct, "require_binding"),
            patch.object(
                r, "prepare", side_effect=RuntimeError("SYNTHETIC preparation stopped")
            ) as prepared,
            patch.object(legacy, "export") as fallback,
            patch.object(m, "OwnToyExporter") as adapter,
        ):
            report = m.run(
                self.root / "work/toy-fixed-fill-PREPARE-STOP",
                run_native=True,
                toy_only=True,
            )
        self.assertEqual(report["status"], "blocked")
        prepared.assert_called_once()
        self.assertIsInstance(
            prepared.call_args.kwargs["exporter"], m.project.BaselineExporter
        )
        fallback.assert_not_called()
        adapter.assert_not_called()
        m.approve_fixture.assert_not_called()

    def test_existing_real_http_handler_is_the_only_fixture_approval_writer(self):
        fixture = workers.ContractWorkerTests()
        fixture.setUp()
        self.addCleanup(fixture.doCleanups)
        workspace, _, _, plan, _ = fixture.stage(4)
        # Only this TEMP fixture's synthetic approval is removed to exercise the handler.
        (workspace / "approved-plan.json").unlink()
        (workspace / r.APPLY_LOCK).unlink()  # Only the synthetic TEMP fixture lease.
        r.write_json(
            workspace / "fill-input.json",
            {"bindings": plan["bindings"], "record": plan["record"]},
        )
        report = {}
        approve_handler(workspace, plan, report)
        approved = r.read_json(workspace / "approved-plan.json")
        self.assertEqual(approved["approved_via"], "local-review-ui")
        self.assertEqual(approved["plan_sha256"], r.plan_hash(plan))
        self.assertEqual(
            report["approval_writer"], "existing_local_review_http_handler"
        )
        self.assertFalse((workspace / r.APPLY_LOCK).exists())

    def test_native_reference_cannot_retain_a_closed_lifecycle(self):
        app = SimpleNamespace(Version="SYNTHETIC")
        ref = n._NativeReference(app)
        self.assertEqual(ref.Version, "SYNTHETIC")
        ref.target = None
        with self.assertRaises(ValueError):
            _ = ref.Version


class FixedValidationSessionTests(unittest.TestCase):
    def setUp(self):
        self.harness = ownership_fixtures.OwnedSessionTests()
        self.harness.setUp()
        self.addCleanup(self.harness.doCleanups)
        self.adapter = object.__new__(m.OwnToyExporter)
        self.adapter.workspace = self.harness.root / m.project.REVIEW_DIR
        self.adapter.phase = 0
        self.adapter.report = {}
        self.run_id = "20261021T000000-a1b2c3d4"
        self.run_dir = self.adapter.workspace / "runs" / self.run_id
        patcher = patch.object(m, "hwp_inventory", return_value=[])
        patcher.start()
        self.addCleanup(patcher.stop)

    def session(self, *, workspace=None, phase=None):
        return self.adapter._session(
            self.adapter.workspace if workspace is None else workspace,
            self.run_id,
            self.adapter.phase if phase is None else phase,
        )

    def named(self, name):
        self.harness.input = self.run_dir / name
        self.harness.named_copy()

    def event(self):
        return self.adapter.report["worker_sessions"][-1]

    def test_both_phases_use_fixed_paths_and_invalidate_before_same_handle_wait(self):
        for phase, name in (
            (0, "contract-source-copy-0.hwp"),
            (1, "candidate-reopened.hwpx"),
        ):
            self.adapter.phase = phase
            with self.session() as reference:
                proof = reference.ownership
                self.assertIs(self.event()["ownership"], proof)
                self.harness.held.wait_for_exit.side_effect = lambda: (
                    self.assertIsNone(reference.target) or True
                )
                self.named(name)
            self.assertTrue(self.event()["owned_exit_confirmed"])
            self.assertEqual(self.event()["cleanup"], "owned_exit_confirmed")
            self.assertEqual(proof["status"], "owned_exit_confirmed")
            self.assertEqual(self.event()["phase"], phase)
            self.assertEqual(self.event()["after_hwp_processes"], [])
        self.assertEqual(self.harness.app.Clear.call_count, 2)
        self.assertEqual(self.harness.app.Quit.call_count, 2)
        self.assertEqual(self.harness.held.close.call_count, 2)
        self.assertEqual(n.QUALIFIED_SCHEMAS, (3, 4))

    def test_additional_production_template_and_buffer_paths_are_not_unknown(self):
        for name in ("contract-approved-input.hwpx", "candidate-buffer.pdf"):
            with self.session():
                self.named(name)
            self.assertTrue(self.event()["owned_exit_confirmed"])

    def test_workspace_phase_and_nonidle_preflight_stop_before_com(self):
        with self.assertRaises(ValueError):
            with self.session(workspace=self.harness.root / "OTHER"):
                self.fail("Wrong workspace entered")
        with self.assertRaises(ValueError):
            with self.session(phase=1):
                self.fail("Wrong phase entered")
        m.hwp_inventory.return_value = [{"pid": 99, "creation_ms": 1}]
        with self.assertRaises(ValueError):
            with self.session():
                self.fail("Nonidle context entered")
        self.harness.client.DispatchEx.assert_not_called()
        self.assertEqual(self.adapter.report, {})

    def test_unowned_entry_records_failure_without_clear_quit_or_retry(self):
        self.harness.metadata.bind_window_process.side_effect = RuntimeError(
            "SYNTHETIC unowned"
        )
        with self.assertRaisesRegex(RuntimeError, "unconfirmed"):
            with self.session():
                self.fail("Unowned entry")
        self.harness.app.Clear.assert_not_called()
        self.harness.app.Quit.assert_not_called()
        self.harness.client.DispatchEx.assert_called_once()
        self.assertFalse(self.event()["owned_exit_confirmed"])
        self.assertEqual(self.event()["error_type"], "RuntimeError")

    def test_unknown_document_is_not_cleared_or_quit(self):
        with self.assertRaisesRegex(RuntimeError, "unconfirmed"):
            with self.session():
                self.harness.doc.FullName = str(self.harness.root / "USER.hwp")
        self.harness.app.Clear.assert_not_called()
        self.harness.app.Quit.assert_not_called()
        self.assertFalse(self.event()["owned_exit_confirmed"])

    def test_dirty_unnamed_document_is_not_treated_as_pristine_blank(self):
        with self.assertRaisesRegex(RuntimeError, "unconfirmed"):
            with self.session():
                self.harness.doc.Modified = True
        self.harness.app.Clear.assert_not_called()
        self.harness.app.Quit.assert_not_called()
        self.assertFalse(self.event()["owned_exit_confirmed"])

    def test_pid_drift_does_not_cleanup_or_retarget(self):
        with self.assertRaisesRegex(RuntimeError, "unconfirmed"):
            with self.session():
                self.harness.metadata.window_process_id.return_value = 9999
        self.harness.app.Clear.assert_not_called()
        self.harness.app.Quit.assert_not_called()
        self.harness.metadata.bind_window_process.assert_called_once()
        self.assertFalse(self.event()["owned_exit_confirmed"])

    def test_exit_timeout_is_failure_even_with_empty_inventory(self):
        self.harness.held.wait_for_exit.side_effect = RuntimeError("SYNTHETIC timeout")
        with self.assertRaisesRegex(RuntimeError, "timeout"):
            with self.session():
                self.named("candidate.hwp")
        self.harness.app.Clear.assert_called_once()
        self.harness.app.Quit.assert_called_once()
        self.harness.held.wait_for_exit.assert_called_once()
        self.assertFalse(self.event()["owned_exit_confirmed"])
        self.assertEqual(self.event()["after_hwp_processes"], [])

    def test_post_inventory_new_process_is_not_success_or_a_termination_grant(self):
        m.hwp_inventory.side_effect = [[], [{"pid": 99, "creation_ms": 1}]]
        with self.assertRaisesRegex(RuntimeError, "unconfirmed"):
            with self.session():
                self.named("candidate.hwp")
        self.assertTrue(self.event()["ownership"]["exit_confirmed"])
        self.assertFalse(self.event()["owned_exit_confirmed"])
        self.harness.client.DispatchEx.assert_called_once()
        self.harness.held.close.assert_called_once()

    def test_body_failure_is_preserved_when_followup_inventory_also_fails(self):
        m.hwp_inventory.side_effect = [
            [],
            RuntimeError("SYNTHETIC inventory unavailable"),
        ]
        with self.assertRaisesRegex(LookupError, "body failed"):
            with self.session():
                self.named("candidate.hwp")
                raise LookupError("SYNTHETIC body failed")
        self.assertTrue(self.event()["ownership"]["exit_confirmed"])
        self.assertFalse(self.event()["owned_exit_confirmed"])
        self.assertEqual(self.event()["error_type"], "LookupError")
        self.assertIn("inventory unavailable", self.event()["inventory_error"])

    def test_followup_inventory_failure_alone_blocks_normal_return(self):
        m.hwp_inventory.side_effect = [
            [],
            RuntimeError("SYNTHETIC inventory unavailable"),
        ]
        with self.assertRaisesRegex(RuntimeError, "Validation owned exit unconfirmed"):
            with self.session():
                self.named("candidate.hwp")
        self.assertTrue(self.event()["ownership"]["exit_confirmed"])
        self.assertFalse(self.event()["owned_exit_confirmed"])


if __name__ == "__main__":
    unittest.main()
