"""Schema3 verification must derive from saved HWP, using TEMP fake sessions only."""

from contextlib import contextmanager
import importlib
import json
from pathlib import Path
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch
import zipfile

old = importlib.import_module("test_contract_apply")
n, r = old.n, old.r
primitive = importlib.import_module("hwp_native_buffer")
cells = importlib.import_module("hwp_native_cells")
TEXT = "SYNTHETIC EXACT EDITED BUFFER"
SAVED = b"SYNTHETIC SAVED HWP; NOT A DOCUMENT"


class SavedTableExportTests(unittest.TestCase):
    def setUp(self):
        self.harness = old.ContractWorkerTests()
        self.harness.setUp()
        self.addCleanup(self.harness.doCleanups)
        self.w, self.run_dir, self.source, self.plan, self.request = self.harness.stage(
            3
        )
        self.selected = r.validate_plan(
            self.plan, r.read_json(self.w / "manifest.json"), self.w / "baseline.hwpx"
        )
        expected = self.w / "expected-fixture"
        expected.mkdir()
        fixture = old.layout_fixture.FixedLayoutTests()
        fixture.base, fixture.plan, fixture.path = (
            self.w / "baseline.hwpx",
            self.plan,
            expected,
        )
        self.hwpx = fixture.candidate().read_bytes()
        self.apps, self.events = [], []
        self.first_save_ok = self.first_exit_ok = True
        self.second_open_ok = self.second_save_ok = self.second_register_ok = True
        self.second_text, self.second_pages, self.second_version = TEXT, 1, "SYNTHETIC"
        self.after_first_exit = lambda: None
        self.after_second_exit = lambda: None
        self.second_open_hook = lambda app, path: None
        self.security = Mock(return_value={"SYNTHETIC": True})
        self.edit = Mock(side_effect=self.literal_edit)
        self.binding = Mock(side_effect=self.bind_minima)
        self.minimum = Mock(side_effect=self.minimum_cell)
        for patcher in (
            patch.object(n, "registered_security_module", self.security),
            patch.object(primitive, "edit_exact_buffer", self.edit),
            patch.object(cells, "capture_minima", self.binding),
            patch.object(primitive, "set_bound_cell_minimum", self.minimum),
        ):
            patcher.start()
            self.addCleanup(patcher.stop)
        self.protected = {
            path: r.file_hash(path)
            for path in (
                self.source,
                self.w / "baseline.hwpx",
                self.w / "approved-plan.json",
                self.w / r.APPLY_LOCK,
                self.run_dir / "plan.json",
            )
        }

    def bind_minima(self, app, copied, digest, baseline, plan, manifest):
        import hashlib

        self.events.append(("binding", 0))
        profile = cells.minima_profile(baseline, plan, manifest)
        return {
            "schema": "hwp-native-original-minima-binding/v1",
            "policy": cells.MINIMA_PROFILE,
            "profile_sha256": profile["profile_sha256"],
            "plan_sha256": r.plan_hash(plan),
            "source_sha256": digest,
            "baseline_hwpx_sha256": r.file_hash(baseline),
            "full_text_sha256": hashlib.sha256(TEXT.encode("utf-8")).hexdigest(),
            "targets": [
                {**target, "position": [index + 2, 0, 0]}
                for index, target in enumerate(profile["targets"])
            ],
            "writer_qualified": False,
        }

    def minimum_cell(
        self, app, source, digest, full, pos, expected, height, diagnostic
    ):
        self.events.append(("minimum", pos[0], height))
        self.assertEqual(
            (source.name, digest, full, height),
            ("contract-source-copy-0.hwp", r.file_hash(self.source), TEXT, 300),
        )
        return {
            "synthetic_only": True,
            "position": pos,
            "height_written_and_read_back": height,
        }

    def literal_edit(self, app, edits):
        self.events.append(("literal-edit", 0))
        return [{"SYNTHETIC": True}], TEXT

    @contextmanager
    def session(self, workspace, run_id, phase):
        self.assertEqual((workspace, run_id, phase), (self.w, self.run_dir.name, 0))
        index = len(self.apps)
        self.assertLess(index, 2, "Only edit + saved-HWP export sessions")
        if index:
            self.assertIn(("exit", 0), self.events)
        doc = SimpleNamespace(FullName="", Path="", Modified=False)
        app = SimpleNamespace(
            RegisterModule=Mock(return_value=index == 0 or self.second_register_ok),
            PageCount=1 if index == 0 else self.second_pages,
            Version="SYNTHETIC" if index == 0 else self.second_version,
            GetTextFile=Mock(return_value=TEXT if index == 0 else self.second_text),
            XHwpDocuments=SimpleNamespace(Count=1, Item=lambda _: doc),
            ownership={"synthetic_only": True, "exit_confirmed": False},
        )

        def open_copy(name, fmt, options):
            path = Path(name)
            self.events.append(("Open", index, fmt, path.name))
            self.assertEqual(options, "versionwarning:false")
            if index == 0:
                self.assertEqual(
                    (path.name, fmt), ("contract-source-copy-0.hwp", "HWP")
                )
            else:
                self.assertEqual((path.name, fmt), (n.SAVED_EXPORT_COPY, "HWP"))
                self.assertEqual(path.read_bytes(), SAVED)
                self.assertEqual(
                    path.read_bytes(), (self.run_dir / "candidate.hwp").read_bytes()
                )
            doc.FullName, doc.Path, doc.Modified = name, str(path.parent), False
            if index:
                self.second_open_hook(app, path)
            return True if index == 0 else self.second_open_ok

        def save(name, fmt, options):
            target = Path(name)
            self.events.append(("SaveAs", index, fmt, target.name))
            self.assertEqual(options, "")
            if index == 0:
                self.assertEqual(
                    (target.name, fmt),
                    ("candidate.hwp", "HWP"),
                    "Never serialize verification HWPX from the edited buffer",
                )
                if not self.first_save_ok:
                    return False
                data = SAVED
            else:
                self.assertIn(
                    (target.name, fmt),
                    (("candidate.hwpx", "HWPX"), ("candidate-buffer.pdf", "PDF")),
                )
                if not self.second_save_ok:
                    return False
                data = self.hwpx if fmt == "HWPX" else b"SYNTHETIC PDF"
            with target.open("xb") as stream:
                stream.write(data)
            doc.FullName, doc.Path, doc.Modified = name, str(target.parent), False
            return True

        app.Open, app.SaveAs = Mock(side_effect=open_copy), Mock(side_effect=save)
        self.apps.append(app)
        self.events.append(("enter", index))
        try:
            yield app
        finally:
            if index == 0 and not self.first_exit_ok:
                raise RuntimeError("SYNTHETIC edit exit unconfirmed")
            app.ownership["exit_confirmed"] = True
            self.events.append(("exit", index))
            if index == 0:
                self.after_first_exit()
            else:
                self.after_second_exit()

    def invoke(self):
        return n._export_phase(
            self.w, self.run_dir.name, 0, n.require_qualified, self.session
        )

    def assert_inputs_unchanged(self):
        self.assertEqual(
            {path: r.file_hash(path) for path in self.protected}, self.protected
        )

    def assert_no_verification_exports(self):
        self.assertFalse((self.run_dir / "candidate.hwpx").exists())
        self.assertFalse((self.run_dir / "candidate-buffer.pdf").exists())
        self.assertTrue((self.w / r.APPLY_LOCK).exists())

    def test_saved_hwp_origin_preserves_public_outputs_and_all_structural_gates(self):
        with patch.object(n, "step_paths", wraps=n.step_paths) as paths:
            result = self.invoke()
        paths.assert_called_once()
        self.assertEqual(
            [event[2] for event in self.events if event[0] == "SaveAs"],
            ["HWP", "HWPX", "PDF"],
        )
        self.assertEqual(len(self.apps), 2)
        self.binding.assert_called_once()
        self.assertEqual(self.minimum.call_count, 2)
        self.assertEqual(
            [
                event[0]
                for event in self.events
                if event[0] in ("binding", "minimum", "literal-edit")
            ],
            ["binding", "minimum", "minimum", "literal-edit"],
        )
        self.assertFalse((self.run_dir / "contract-approved-input.hwpx").exists())
        self.assertEqual(result["native_original_minima_binding"]["cell_count"], 2)
        self.assertFalse(result["native_original_minima_binding"]["writer_qualified"])
        self.assertLess(len(json.dumps(result)), 10000)
        self.edit.assert_called_once_with(self.apps[0], self.plan["edits"])
        self.assertEqual(result["native_verification_origin"], "saved_hwp_copy")
        self.assertTrue(result["edit_owned_session"]["exit_confirmed"])
        self.assertTrue(result["owned_session"]["exit_confirmed"])
        self.assertIsNot(result["edit_owned_session"], result["owned_session"])
        self.assertEqual((self.run_dir / n.SAVED_EXPORT_COPY).read_bytes(), SAVED)
        self.assertEqual(
            result["saved_export_copy_sha256"],
            r.file_hash(self.run_dir / "candidate.hwp"),
        )
        self.assertEqual(
            n.contract_gate(
                self.w, self.run_dir / "candidate.hwpx", self.plan, self.selected
            )["policy"],
            r.hwp_fixed_layout.POLICY,
        )
        self.assert_inputs_unchanged()

    def test_generic_source_schema3_is_not_a_fixed_worker_alternative(self):
        legacy = importlib.import_module("native_hwp")
        with (
            patch.object(legacy, "owned_session") as session,
            patch.object(legacy, "register_security_module") as module,
            patch.object(legacy.shutil, "copy2") as copied,
        ):
            with self.assertRaisesRegex(
                ValueError, "fixed approved contract controller"
            ):
                legacy.edit_export(
                    self.source,
                    self.run_dir / "generic.pdf",
                    self.run_dir / "generic.hwpx",
                    self.run_dir / "generic.hwp",
                    self.w,
                    self.run_dir / "plan.json",
                )
            session.assert_not_called()
            module.assert_not_called()
            copied.assert_not_called()
        self.assertEqual(self.apps, [])
        self.assert_inputs_unchanged()

    def test_worker_minima_profile_refusal_precedes_security_copy_or_session(self):
        with patch.object(
            cells,
            "minima_profile",
            side_effect=ValueError("SYNTHETIC shape unqualified"),
        ):
            with self.assertRaisesRegex(ValueError, "shape unqualified"):
                self.invoke()
        self.security.assert_not_called()
        self.assertEqual(self.apps, [])
        self.assertFalse((self.run_dir / "contract-source-copy-0.hwp").exists())
        self.assert_inputs_unchanged()

    def test_legacy_prepared_hwpx_collision_never_falls_back_to_hwp(self):
        path = self.run_dir / "contract-approved-input.hwpx"
        path.write_bytes(b"KEEP OLD FAILED INPUT")
        with self.assertRaises(FileExistsError):
            self.invoke()
        self.assertEqual(path.read_bytes(), b"KEEP OLD FAILED INPUT")
        self.assertEqual(self.apps, [])
        self.minimum.assert_not_called()
        self.assert_no_verification_exports()

    def test_changed_approval_after_first_minimum_blocks_second_and_literal_save(self):
        def minimum(*args):
            result = self.minimum_cell(*args)
            approval = r.read_json(self.w / "approved-plan.json")
            approval["approved_scope"] = {}
            r.write_json(self.w / "approved-plan.json", approval)
            return result

        self.minimum.side_effect = minimum
        with self.assertRaises(ValueError):
            self.invoke()
        self.assertEqual(self.minimum.call_count, 1)
        self.edit.assert_not_called()
        self.apps[0].SaveAs.assert_not_called()
        self.assert_no_verification_exports()

    def test_changed_lease_after_first_minimum_blocks_second_and_literal_save(self):
        def minimum(*args):
            result = self.minimum_cell(*args)
            lease = r.read_json(self.w / r.APPLY_LOCK)
            lease["owner_nonce"] = "0" * 32
            r.write_json(self.w / r.APPLY_LOCK, lease)
            return result

        self.minimum.side_effect = minimum
        with self.assertRaises((ValueError, RuntimeError)):
            self.invoke()
        self.assertEqual(self.minimum.call_count, 1)
        self.edit.assert_not_called()
        self.apps[0].SaveAs.assert_not_called()

    def test_changed_proposal_after_binding_blocks_every_minimum_and_save(self):
        def binding(*args):
            result = self.bind_minima(*args)
            plan = r.read_json(self.w / "proposal-layout.json")
            plan["edits"][0]["new_text"] = "foreign"
            r.write_json(self.w / "proposal-layout.json", plan)
            return result

        self.binding.side_effect = binding
        with self.assertRaisesRegex(ValueError, "proposal changed"):
            self.invoke()
        self.minimum.assert_not_called()
        self.edit.assert_not_called()
        self.apps[0].SaveAs.assert_not_called()

    def test_foreign_binding_hash_height_or_duplicate_position_blocks_put(self):
        def binding(*args):
            result = self.bind_minima(*args)
            result["profile_sha256"] = "0" * 64
            return result

        self.binding.side_effect = binding
        with self.assertRaisesRegex(ValueError, "binding changed"):
            self.invoke()
        self.minimum.assert_not_called()
        self.edit.assert_not_called()
        self.apps[0].SaveAs.assert_not_called()
        self.assert_inputs_unchanged()

    def test_later_invalid_cell_binding_blocks_all_minima_before_any_put(self):
        def binding(*args):
            result = self.bind_minima(*args)
            result["targets"][-1]["height"] += 1
            return result

        self.binding.side_effect = binding
        with self.assertRaisesRegex(ValueError, "exact declared cell"):
            self.invoke()
        self.minimum.assert_not_called()
        self.edit.assert_not_called()
        self.apps[0].SaveAs.assert_not_called()

    def test_binding_height_different_from_declared_minima_blocks_put(self):
        def binding(*args):
            result = self.bind_minima(*args)
            result["targets"][0]["height"] += 1
            return result

        self.binding.side_effect = binding
        with self.assertRaisesRegex(ValueError, "exact declared cell"):
            self.invoke()
        self.minimum.assert_not_called()
        self.edit.assert_not_called()
        self.apps[0].SaveAs.assert_not_called()

    def test_buffer_drift_after_minima_blocks_literal_and_save(self):
        def minimum(*args):
            result = self.minimum_cell(*args)
            if self.minimum.call_count == 2:
                self.apps[0].GetTextFile.return_value = "UNEXPECTED INPUT"
            return result

        self.minimum.side_effect = minimum
        with self.assertRaisesRegex(
            ValueError, "Unexpected text after original minima"
        ):
            self.invoke()
        self.edit.assert_not_called()
        self.apps[0].SaveAs.assert_not_called()

    def test_saved_origin_still_rejects_changed_header_without_policy_repair(self):
        parts = r.archive(self.w / "expected-fixture/candidate.hwpx")
        parts["Contents/header.xml"] += b" "
        changed = self.w / "changed-header.hwpx"
        with zipfile.ZipFile(changed, "x") as package:
            for name, data in parts.items():
                package.writestr(name, data)
        self.hwpx = changed.read_bytes()
        self.invoke()
        with self.assertRaisesRegex(ValueError, "Contents/header.xml"):
            n.contract_gate(
                self.w, self.run_dir / "candidate.hwpx", self.plan, self.selected
            )
        self.assert_inputs_unchanged()

    def test_saved_origin_still_rejects_unselected_structure_change(self):
        parts = r.archive(self.w / "expected-fixture/candidate.hwpx")
        parts["Contents/section0.xml"] = parts["Contents/section0.xml"].replace(
            b"Protected synthetic label", b"UNAPPROVED label"
        )
        invalid = self.w / "invalid-fixture.hwpx"
        with zipfile.ZipFile(invalid, "x") as package:
            for name, raw in parts.items():
                package.writestr(name, raw)
        self.hwpx = invalid.read_bytes()
        self.invoke()
        with self.assertRaises(ValueError):
            n.contract_gate(
                self.w, self.run_dir / "candidate.hwpx", self.plan, self.selected
            )
        self.assert_inputs_unchanged()

    def test_second_exit_mutation_cannot_get_a_success_reply(self):
        self.after_second_exit = lambda: (self.run_dir / "candidate.hwpx").write_bytes(
            b"CHANGED DURING EXIT"
        )
        with self.assertRaisesRegex(ValueError, "Protected Native"):
            self.invoke()
        self.assertEqual(
            (self.run_dir / "candidate.hwpx").read_bytes(), b"CHANGED DURING EXIT"
        )
        self.assert_inputs_unchanged()

    def test_internal_saved_copy_collision_blocks_before_any_session(self):
        path = self.run_dir / n.SAVED_EXPORT_COPY
        path.write_bytes(b"KEEP COLLISION")
        with self.assertRaises(FileExistsError):
            self.invoke()
        self.assertEqual(self.apps, [])
        self.assertEqual(path.read_bytes(), b"KEEP COLLISION")
        self.assert_no_verification_exports()

    def test_first_save_failure_never_enters_saved_export_session(self):
        self.first_save_ok = False
        with self.assertRaisesRegex(RuntimeError, "output failed: HWP"):
            self.invoke()
        self.assertEqual(len(self.apps), 1)
        self.assert_no_verification_exports()
        self.assert_inputs_unchanged()

    def test_unconfirmed_first_exit_never_enters_second_session(self):
        self.first_exit_ok = False
        with self.assertRaisesRegex(RuntimeError, "exit unconfirmed"):
            self.invoke()
        self.assertEqual(len(self.apps), 1)
        self.assertFalse((self.run_dir / n.SAVED_EXPORT_COPY).exists())
        self.assert_no_verification_exports()

    def test_saved_hwp_mutation_during_first_exit_is_not_copied_or_repaired(self):
        self.after_first_exit = lambda: (self.run_dir / "candidate.hwp").write_bytes(
            b"CHANGED"
        )
        with self.assertRaisesRegex(ValueError, "Protected Native"):
            self.invoke()
        self.assertEqual(len(self.apps), 1)
        self.assertFalse((self.run_dir / n.SAVED_EXPORT_COPY).exists())
        self.assertEqual((self.run_dir / "candidate.hwp").read_bytes(), b"CHANGED")
        self.assert_no_verification_exports()

    def test_copy_mutation_on_second_open_blocks_before_verification_exports(self):
        self.second_open_hook = lambda app, path: path.write_bytes(b"CHANGED COPY")
        with self.assertRaisesRegex(ValueError, "Protected Native"):
            self.invoke()
        self.assertEqual(len(self.apps), 2)
        self.apps[1].SaveAs.assert_not_called()
        self.assert_no_verification_exports()

    def test_changed_approval_between_sessions_stops_before_reopen(self):
        def change():
            approval = r.read_json(self.w / "approved-plan.json")
            approval["approved_scope"] = {}
            r.write_json(self.w / "approved-plan.json", approval)

        self.after_first_exit = change
        with self.assertRaises(ValueError):
            self.invoke()
        self.assertEqual(len(self.apps), 1)
        self.assert_no_verification_exports()

    def test_changed_registered_module_stops_before_reopen(self):
        self.security.side_effect = [{"SYNTHETIC": True}, {"SYNTHETIC": "CHANGED"}]
        with self.assertRaisesRegex(ValueError, "Registered module changed"):
            self.invoke()
        self.assertEqual(len(self.apps), 1)
        self.assert_no_verification_exports()

    def test_changed_lease_between_sessions_blocks_reopen(self):
        def change():
            lease = r.read_json(self.w / r.APPLY_LOCK)
            lease["owner_nonce"] = "0" * 32
            r.write_json(self.w / r.APPLY_LOCK, lease)

        self.after_first_exit = change
        with self.assertRaises((ValueError, RuntimeError)):
            self.invoke()
        self.assertEqual(len(self.apps), 1)
        self.assert_no_verification_exports()

    def test_second_registration_rejection_never_opens_saved_copy(self):
        self.second_register_ok = False
        with self.assertRaisesRegex(RuntimeError, "module rejected"):
            self.invoke()
        self.apps[1].Open.assert_not_called()
        self.assert_no_verification_exports()

    def test_changed_saved_version_stops_before_exports(self):
        self.second_version = "DIFFERENT VERSION"
        with self.assertRaisesRegex(ValueError, "page/version changed"):
            self.invoke()
        self.apps[1].SaveAs.assert_not_called()
        self.assert_no_verification_exports()

    def test_changed_saved_text_stops_before_exports(self):
        self.second_text = "UNEXPECTED TEXT"
        with self.assertRaisesRegex(ValueError, "text differs"):
            self.invoke()
        self.apps[1].SaveAs.assert_not_called()
        self.assert_no_verification_exports()

    def test_unknown_saved_document_identity_stops_before_exports(self):
        self.second_open_hook = lambda app, path: setattr(
            app.XHwpDocuments.Item(0), "FullName", str(self.w / "UNKNOWN.hwp")
        )
        with self.assertRaisesRegex(ValueError, "identity changed"):
            self.invoke()
        self.apps[1].SaveAs.assert_not_called()
        self.assert_no_verification_exports()

    def test_changed_saved_page_count_stops_before_exports(self):
        self.second_pages = 0
        with self.assertRaisesRegex(ValueError, "page/version changed"):
            self.invoke()
        self.apps[1].SaveAs.assert_not_called()
        self.assert_no_verification_exports()

    def test_second_open_failure_preserves_saved_hwp_without_other_exports(self):
        self.second_open_ok = False
        with self.assertRaisesRegex(RuntimeError, "copy rejected"):
            self.invoke()
        self.assertEqual((self.run_dir / "candidate.hwp").read_bytes(), SAVED)
        self.assert_no_verification_exports()
        self.assert_inputs_unchanged()

    def test_second_export_failure_does_not_attest_or_clear_lease(self):
        self.second_save_ok = False
        with self.assertRaisesRegex(RuntimeError, "output failed: HWPX"):
            self.invoke()
        self.assert_no_verification_exports()
        self.assert_inputs_unchanged()

    def test_production_session_knows_only_the_fixed_additional_copy_path(self):
        @contextmanager
        def fake(paths):
            self.assertIn(self.run_dir / n.SAVED_EXPORT_COPY, paths)
            yield "SYNTHETIC"

        with patch.object(n, "owned_session", side_effect=fake):
            with n._native_session(self.w, self.run_dir.name, 0) as app:
                self.assertEqual(app, "SYNTHETIC")


if __name__ == "__main__":
    unittest.main()
