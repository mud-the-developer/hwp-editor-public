"""Separate Native consent/controller and peer races in synthetic fixtures only."""

import importlib
import shutil
import threading
from unittest.mock import patch
import unittest
import zipfile

fixture = importlib.import_module("test_contract_apply")
r = importlib.import_module("hwp_review")
control = importlib.import_module("hwp_contract_control")
text = importlib.import_module("hwp_text_apply")


class ContractControlTests(unittest.TestCase):
    def setUp(self):
        # Default-deny process launch even if a future mock/module identity drifts.
        worker = importlib.import_module("hwp_contract_apply")
        denied = patch.object(
            worker.subprocess,
            "run",
            side_effect=AssertionError("OFFLINE TEST: child process forbidden"),
        )
        denied.start()
        self.addCleanup(denied.stop)

    def stage(self, schema=3, *, native_profile=True):
        owner = fixture.ContractWorkerTests(
            "test_all_three_exact_contracts_derive_paths_without_native_or_output"
        )
        self.addCleanup(owner.doCleanups)
        w, run, _, plan, _ = owner.stage(schema, native_profile=native_profile)
        (w / r.APPLY_LOCK).unlink()
        shutil.rmtree(run)
        r.write_json(
            w / "comments.json", {"workspace_id": plan["workspace_id"], "comments": []}
        )
        payload = {
            "workspace_id": plan["workspace_id"],
            "baseline_hwpx_sha256": plan["baseline_hwpx_sha256"],
            "plan_sha256": r.plan_hash(plan),
            "proposal_kind": {3: "layout", 4: "fill"}[schema],
            "approval_scope_sha256": r.plan_hash(r.native_approval_scope(plan)),
            "contract_apply_confirmed": True,
        }
        return (
            w,
            plan,
            payload,
            '"' + r.file_hash(w / fixture.n.PROPOSALS[schema]) + '"',
        )

    def test_default_disabled_never_launches_or_changes_approval(self):
        w, _, payload, revision = self.stage()
        before = (w / "approved-plan.json").read_bytes()
        c = control.NativeApplyController(w)
        with patch.object(r, "apply") as apply:
            with self.assertRaises(ValueError):
                c.start(payload, revision)
            apply.assert_not_called()
        self.assertFalse(c.snapshot()["enabled"])
        self.assertEqual((w / "approved-plan.json").read_bytes(), before)

    def test_confirmation_policy_kind_revision_all_bind_exact_proposal(self):
        for key, value in [
            ("contract_apply_confirmed", 1),
            ("proposal_kind", "text"),
            ("approval_scope_sha256", "0" * 64),
            ("plan_sha256", "0" * 64),
        ]:
            w, _, payload, revision = self.stage()
            c = control.NativeApplyController(w, enabled=True)
            payload[key] = value
            with (
                patch.object(control, "registered_security_module"),
                patch.object(r, "apply") as apply,
            ):
                with self.assertRaises(ValueError):
                    c.start(payload, revision)
                apply.assert_not_called()
        w, _, payload, _ = self.stage()
        c = control.NativeApplyController(w, enabled=True)
        with self.assertRaises(ValueError):
            c.start(payload, '"stale"')
        self.assertFalse((w / r.APPLY_LOCK).exists())

    def test_exact_start_is_not_pass_and_blocks_peer_close_duplicate_or_text_start(
        self,
    ):
        w, plan, payload, revision = self.stage()
        peer = text.TextApplyController(w, enabled=True)
        c = control.NativeApplyController(
            w, enabled=True, guard=peer.guard, peers=(peer,)
        )
        peer.peers = (c,)
        entered = threading.Event()
        release = threading.Event()

        def slow(*args, **kwargs):
            entered.set()
            release.wait(5)
            kwargs["contract_exporter"].phase = 2

        with (
            patch.object(control, "registered_security_module"),
            patch.object(text, "registered_security_module"),
            patch.object(r, "apply", side_effect=slow) as apply,
        ):
            reply = c.start(payload, revision)
            self.assertTrue(reply["accepted"])
            self.assertTrue(entered.wait(2))
            try:
                self.assertTrue(c.snapshot()["active"])
                self.assertNotEqual(c.snapshot()["status"], "finished")
                with self.assertRaises(RuntimeError):
                    peer.require_idle()
                with self.assertRaises(RuntimeError):
                    peer.start({}, '"absent"')
                with self.assertRaises(RuntimeError):
                    c.start(payload, revision)
                self.assertEqual(
                    apply.call_args.kwargs["expected_plan_sha256"], r.plan_hash(plan)
                )
                self.assertNotIn("text_exporter", apply.call_args.kwargs)
            finally:
                release.set()
                assert c.thread is not None
                c.thread.join(5)
            self.assertEqual(c.snapshot()["status"], "finished")
            with self.assertRaises(ValueError):
                c.start(payload, revision)
            self.assertEqual(apply.call_count, 1)

    def test_unqualified_kinds_block_before_document_lock_or_apply(self):
        for kind in ("native",):
            w, _, payload, revision = self.stage()
            c = control.NativeApplyController(w, enabled=True)
            before = (w / "approved-plan.json").read_bytes()
            payload["proposal_kind"] = kind
            with (
                patch.object(r, "verify_baseline") as baseline,
                patch.object(r, "apply") as apply,
            ):
                with self.assertRaisesRegex(ValueError, "unqualified"):
                    c.start(payload, revision)
                baseline.assert_not_called()
                apply.assert_not_called()
            self.assertEqual((w / "approved-plan.json").read_bytes(), before)
            self.assertFalse((w / r.APPLY_LOCK).exists())
            self.assertEqual(c.snapshot()["supported_kinds"], ["layout", "fill"])

    def test_exact_fill_start_preserves_default_consent_and_same_shared_exporter(self):
        w, plan, payload, revision = self.stage(4)
        before = (w / "approved-plan.json").read_bytes()
        disabled = control.NativeApplyController(w)
        with (
            patch.object(control, "plain_profile") as profile,
            patch.object(control, "registered_security_module") as module,
            patch.object(control.threading, "Thread") as thread,
        ):
            with self.assertRaises(ValueError):
                disabled.start(payload, revision)
            profile.assert_not_called()
            module.assert_not_called()
            thread.assert_not_called()
        job = control.NativeApplyController(w, enabled=True)

        def applied(*args, **kwargs):
            self.assertIsInstance(
                kwargs["contract_exporter"], fixture.n.NativeContractExporter
            )
            self.assertEqual(kwargs["expected_plan_sha256"], r.plan_hash(plan))
            kwargs["contract_exporter"].phase = 2

        with (
            patch.object(control, "registered_security_module"),
            patch.object(r, "apply", side_effect=applied) as apply,
        ):
            self.assertTrue(job.start(payload, revision)["accepted"])
            job.thread.join(5)
            self.assertEqual(job.snapshot()["status"], "finished")
            with self.assertRaises(ValueError):
                job.start(payload, revision)
            apply.assert_called_once()
        self.assertFalse((w / r.APPLY_LOCK).exists())
        self.assertEqual((w / "approved-plan.json").read_bytes(), before)

    def test_fill_profile_refusal_precedes_registry_thread_lease_and_apply(self):
        w, _, payload, revision = self.stage(4)
        job = control.NativeApplyController(w, enabled=True)
        with (
            patch.object(
                control,
                "plain_profile",
                side_effect=ValueError("SYNTHETIC unsupported profile"),
            ) as profile,
            patch.object(control, "registered_security_module") as module,
            patch.object(control.threading, "Thread") as thread,
            patch.object(r, "apply") as apply,
        ):
            with self.assertRaisesRegex(ValueError, "unsupported profile"):
                job.start(payload, revision)
            profile.assert_called_once()
            module.assert_not_called()
            thread.assert_not_called()
            apply.assert_not_called()
        self.assertEqual(job.status, "idle")
        self.assertFalse((w / r.APPLY_LOCK).exists())

    def test_actual_no_or_unknown_spine_fill_stays_readonly_before_registry_or_thread(
        self,
    ):
        base = fixture.fill_fixture.package
        for linear in (b"no", b"unknown"):

            def package(path, *args, **kwargs):
                base(path, *args, **kwargs)
                parts = r.archive(path)
                parts["Contents/content.hpf"] = parts["Contents/content.hpf"].replace(
                    b'idref="header" linear="yes"',
                    b'idref="header" linear="' + linear + b'"',
                )
                with zipfile.ZipFile(path, "w") as z:
                    for name, data in parts.items():
                        z.writestr(name, data)

            with patch.object(fixture.fill_fixture, "package", side_effect=package):
                if linear == b"unknown":
                    with (
                        patch.object(control, "registered_security_module") as module,
                        patch.object(control.threading, "Thread") as thread,
                    ):
                        with self.assertRaisesRegex(
                            ValueError, "Unsupported header reading-order"
                        ):
                            self.stage(4)
                        module.assert_not_called()
                        thread.assert_not_called()
                    continue
                w, plan, payload, revision = self.stage(4)
            self.assertEqual(len(r.approved_plan(w, plan, r.verify_baseline(w))), 2)
            job = control.NativeApplyController(w, enabled=True)
            with (
                patch.object(control, "registered_security_module") as module,
                patch.object(control.threading, "Thread") as thread,
            ):
                with self.assertRaises(ValueError):
                    job.start(payload, revision)
                module.assert_not_called()
                thread.assert_not_called()
            self.assertFalse((w / r.APPLY_LOCK).exists())

    def test_fill_requires_exact_typed_confirmation_revision_and_scope(self):
        for key, value in (
            ("contract_apply_confirmed", 1),
            ("approval_scope_sha256", "0" * 64),
            ("proposal_kind", "layout"),
        ):
            w, _, payload, revision = self.stage(4)
            payload[key] = value
            job = control.NativeApplyController(w, enabled=True)
            with (
                patch.object(control, "registered_security_module") as module,
                patch.object(control.threading, "Thread") as thread,
            ):
                with self.assertRaises((ValueError, OSError)):
                    job.start(payload, revision)
                module.assert_not_called()
                thread.assert_not_called()
            self.assertFalse((w / r.APPLY_LOCK).exists())
        w, _, payload, _ = self.stage(4)
        with self.assertRaises(ValueError):
            control.NativeApplyController(w, enabled=True).start(payload, '"stale"')

    def test_thread_failure_preserves_same_hash_no_repeat(self):
        w, _, payload, revision = self.stage()
        c = control.NativeApplyController(w, enabled=True)
        with (
            patch.object(control, "registered_security_module"),
            patch.object(
                r, "apply", side_effect=RuntimeError("SYNTHETIC no repeat")
            ) as apply,
        ):
            c.start(payload, revision)
            assert c.thread is not None
            c.thread.join(5)
            self.assertEqual(c.snapshot()["status"], "blocked")
            self.assertIn("SYNTHETIC", c.snapshot()["error"])
            with self.assertRaises(ValueError):
                c.start(payload, revision)
            self.assertEqual(apply.call_count, 1)


if __name__ == "__main__":
    unittest.main()
