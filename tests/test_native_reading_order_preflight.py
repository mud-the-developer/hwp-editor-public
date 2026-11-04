"""Reading-order execution eligibility only: TEMP bytes, no Native or real approval."""

import importlib
import shutil
import unittest
from unittest.mock import patch
import xml.etree.ElementTree as ET

worker = importlib.import_module("test_contract_apply")
layout = importlib.import_module("test_hwp_fixed_layout")
control = importlib.import_module("hwp_contract_control")
n, r = worker.n, worker.r
native = r.hwp_native_plan
fixed = r.hwp_fixed_layout


def hpf(linear=None):
    attribute = "" if linear is None else f' linear="{linear}"'
    return f'<package xmlns="{native.OPF[1:-1]}"><spine><itemref idref="header"{attribute}/><itemref idref="section0"/></spine></package>'.encode()


class ReadingOrderPreflightTests(unittest.TestCase):
    def stage(self, raw: bytes | None = hpf("yes"), *, controller=False):
        harness = worker.ContractWorkerTests()
        harness.setUp()
        self.addCleanup(harness.doCleanups)
        with patch.object(layout, "SYNTHETIC_HPF", raw):
            w, run, source, plan, request = harness.stage(3)
        if controller:
            (w / r.APPLY_LOCK).unlink()
            shutil.rmtree(run)
            r.write_json(
                w / "comments.json",
                {"workspace_id": plan["workspace_id"], "comments": []},
            )
        payload = {
            "workspace_id": plan["workspace_id"],
            "baseline_hwpx_sha256": plan["baseline_hwpx_sha256"],
            "proposal_kind": "layout",
            "plan_sha256": r.plan_hash(plan),
            "approval_scope_sha256": r.plan_hash(r.native_approval_scope(plan)),
            "contract_apply_confirmed": True,
        }
        return (
            w,
            run,
            source,
            plan,
            payload,
            '"' + r.file_hash(w / "proposal-layout.json") + '"',
        )

    def hashes(self, w):
        return {
            str(p.relative_to(w)): r.file_hash(p) for p in w.rglob("*") if p.is_file()
        }

    def test_explicit_and_default_yes_are_readonly_not_a_grant(self):
        for raw in (hpf("yes"), hpf()):
            with self.subTest(raw=raw):
                self.assertIsNone(native.require_yes_spine(raw))
                w, _, _, _, _, _ = self.stage(raw)
                before = self.hashes(w)
                self.assertIsNone(fixed.require_native_profile(w / "baseline.hwpx"))
                self.assertEqual(self.hashes(w), before)

    def test_missing_no_unknown_duplicate_empty_and_malformed_spines_are_rejected(self):
        cases = (
            None,
            hpf("no"),
            hpf("NO"),
            hpf("unknown"),
            hpf(""),
            b"not XML",
            hpf().replace(b"<spine>", b"<spine/><spine>"),
            hpf().replace(b'<itemref idref="header"/>', b'<itemref idref="section0"/>'),
            hpf().replace(b'<itemref idref="header"/>', b"<itemref/>"),
            f'<package xmlns="{native.OPF[1:-1]}"><spine/></package>'.encode(),
            b'<!DOCTYPE package [<!ENTITY x "bad">]><package>&x;</package>',
        )
        for raw in cases:
            with self.subTest(raw=raw), self.assertRaises((ValueError, ET.ParseError)):
                native.require_yes_spine(raw)

    def test_no_profile_still_allows_readonly_proposal_validation(self):
        w, _, _, plan, _, _ = self.stage(hpf("no"))
        before = self.hashes(w)
        selected = r.validate_plan(
            plan, r.read_json(w / "manifest.json"), w / "baseline.hwpx"
        )
        self.assertEqual(len(selected), 1)
        self.assertEqual(self.hashes(w), before)

    def test_worker_blocks_bad_profiles_before_security_session_or_copy(self):
        for raw in (hpf("no"), None, b"not XML"):
            with self.subTest(raw=raw):
                w, run, _, _, _, _ = self.stage(raw)
                before = self.hashes(w)
                with (
                    patch.object(n, "registered_security_module") as module,
                    patch.object(n, "_native_session") as session,
                ):
                    with self.assertRaises((ValueError, ET.ParseError)):
                        n.export_phase(w, run.name, 0)
                    module.assert_not_called()
                    session.assert_not_called()
                self.assertFalse((run / "contract-source-copy-0.hwp").exists())
                self.assertTrue((w / r.APPLY_LOCK).exists())
                self.assertEqual(self.hashes(w), before)

    def test_no_profile_snapshot_is_unavailable_without_registry_or_writes(self):
        w, _, _, _, _, _ = self.stage(hpf("no"), controller=True)
        before = self.hashes(w)
        c = control.NativeApplyController(w, enabled=True)
        with patch.object(control, "registered_security_module") as module:
            state = c.snapshot()
            module.assert_not_called()
        self.assertFalse(state["available"])
        self.assertIn("읽기 순서 보존", state["reason"])
        self.assertIn("읽기 전용 검토", state["reason"])
        self.assertEqual(self.hashes(w), before)

    def test_start_blocks_bad_profiles_before_thread_apply_lease_or_source_copy(self):
        for raw in (hpf("no"), None, b"not XML"):
            with self.subTest(raw=raw):
                w, _, _, _, payload, revision = self.stage(raw, controller=True)
                before = self.hashes(w)
                c = control.NativeApplyController(w, enabled=True)
                with (
                    patch.object(control, "registered_security_module") as module,
                    patch.object(control.threading, "Thread") as thread,
                    patch.object(r, "apply") as apply,
                ):
                    with self.assertRaises((ValueError, ET.ParseError)):
                        c.start(payload, revision)
                    module.assert_not_called()
                    thread.assert_not_called()
                    apply.assert_not_called()
                self.assertEqual(c.status, "idle")
                self.assertFalse((w / r.APPLY_LOCK).exists())
                self.assertEqual(self.hashes(w), before)

    def test_availability_snapshot_cannot_authorize_a_later_start(self):
        w, _, _, _, payload, revision = self.stage(controller=True)
        c = control.NativeApplyController(w, enabled=True)
        with patch.object(control, "registered_security_module") as module:
            self.assertTrue(c.snapshot()["available"])
            module.reset_mock()
            with (
                patch.object(
                    control,
                    "require_native_profile",
                    side_effect=ValueError("SYNTHETIC eligibility changed"),
                ) as profile,
                patch.object(control.threading, "Thread") as thread,
            ):
                with self.assertRaisesRegex(ValueError, "eligibility changed"):
                    c.start(payload, revision)
                profile.assert_called_once_with(w / "baseline.hwpx")
                thread.assert_not_called()
            module.assert_not_called()
        self.assertFalse((w / r.APPLY_LOCK).exists())

    def test_start_requires_minima_profile_before_security_thread_or_lease(self):
        w, _, _, _, payload, revision = self.stage(controller=True)
        c = control.NativeApplyController(w, enabled=True)
        before = self.hashes(w)
        with (
            patch.object(
                control,
                "minima_profile",
                side_effect=ValueError("SYNTHETIC table profile changed"),
            ) as profile,
            patch.object(control, "registered_security_module") as module,
            patch.object(control.threading, "Thread") as thread,
        ):
            with self.assertRaisesRegex(ValueError, "table profile changed"):
                c.start(payload, revision)
            profile.assert_called_once()
            module.assert_not_called()
            thread.assert_not_called()
        self.assertFalse((w / r.APPLY_LOCK).exists())
        self.assertEqual(c.status, "idle")
        self.assertEqual(self.hashes(w), before)

    def test_disabled_controller_does_not_probe_profile_or_registry(self):
        w, _, _, _, payload, revision = self.stage(controller=True)
        c = control.NativeApplyController(w)
        with (
            patch.object(control, "require_native_profile") as profile,
            patch.object(control, "registered_security_module") as module,
        ):
            self.assertFalse(c.snapshot()["available"])
            with self.assertRaisesRegex(ValueError, "disabled"):
                c.start(payload, revision)
            profile.assert_not_called()
            module.assert_not_called()

    def test_supported_snapshot_does_not_write_or_claim_document_success(self):
        w, _, _, _, _, _ = self.stage(controller=True)
        before = self.hashes(w)
        c = control.NativeApplyController(w, enabled=True)
        with patch.object(control, "registered_security_module"):
            state = c.snapshot()
        self.assertTrue(state["available"])
        self.assertEqual(state["status"], "idle")
        self.assertFalse(state["active"])
        self.assertEqual(self.hashes(w), before)

    def test_baseline_mutation_during_readonly_profile_is_not_accepted(self):
        w, _, _, _, _, _ = self.stage()
        baseline = w / "baseline.hwpx"
        with patch.object(
            native,
            "require_yes_spine",
            side_effect=lambda _: baseline.write_bytes(b"CHANGED TEMP BASELINE"),
        ):
            with self.assertRaisesRegex(ValueError, "changed during Native preflight"):
                fixed.require_native_profile(baseline)
        self.assertEqual(baseline.read_bytes(), b"CHANGED TEMP BASELINE")


if __name__ == "__main__":
    unittest.main()
