"""Strict direct-HWP schema4 plumbing: fake COM and TEMP consent only."""

import hashlib
import importlib
from pathlib import Path
from types import ModuleType, SimpleNamespace
import sys
import unittest
from unittest.mock import Mock, patch
import test_contract_apply as suite
from test_com_cache import cached_com_modules
from test_native_owner import fake_owned_session

n = suite.n
r = suite.r
cells = importlib.import_module("hwp_native_cells")


class DirectContractTests(unittest.TestCase):
    def setUp(self):
        self.fixture = suite.ContractWorkerTests()
        self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)
        self.w, self.run_dir, self.source, self.plan, self.request = self.fixture.stage(
            4
        )
        self.selected = n.step_paths(self.w, self.run_dir.name, 0)[-1]
        self.doc = SimpleNamespace(FullName="", Modified=False)
        self.app = SimpleNamespace(
            text="SYNTHETIC ORIGINAL TABLE",
            pos=[0, 0, 0],
            PageCount=1,
            Version="SYNTHETIC",
            XHwpWindows=SimpleNamespace(Item=lambda _: SimpleNamespace(Visible=False)),
            XHwpDocuments=SimpleNamespace(Count=1, Item=lambda _: self.doc),
            HParameterSet=SimpleNamespace(
                HInsertText=SimpleNamespace(HSet=object(), Text="")
            ),
            RegisterModule=Mock(return_value=True),
            Clear=Mock(),
            Quit=Mock(),
            GetSelectedPos=lambda: (False, 0, 0, 0, 0, 0, 0),
        )
        self.app.GetTextFile = lambda *args: self.app.text
        self.app.GetPos = lambda: tuple(self.app.pos)
        self.app.SetPos = lambda *pos: setattr(self.app, "pos", list(pos)) or True
        self.app.Open = Mock(
            side_effect=lambda path, *args: setattr(self.doc, "FullName", path) or True
        )
        self.app.InitScan = Mock(return_value=True)

        def scan(*args):
            self.scan = iter(((2, "\r\n"), (1, "")))
            return True

        self.scan = iter(((1, ""),))
        self.app.InitScan.side_effect = scan
        self.app.GetText = lambda: next(self.scan)
        self.app.ReleaseScan = Mock()

        def insert(*args):
            self.app.text += self.app.HParameterSet.HInsertText.Text
            return True

        self.app.HAction = SimpleNamespace(
            GetDefault=Mock(), Execute=Mock(side_effect=insert)
        )
        self.app.SaveAs = Mock(
            side_effect=lambda path, *args: (
                Path(path).write_bytes(b"SYNTHETIC OUTPUT") > 0
            )
        )
        com = ModuleType("pythoncom")
        setattr(com, "CoInitialize", Mock())
        setattr(com, "CoUninitialize", Mock())
        client = ModuleType("win32com.client")
        setattr(client, "DispatchEx", Mock(return_value=self.app))
        setattr(client, "gencache", SimpleNamespace(EnsureDispatch=lambda app: app))
        modules = cached_com_modules(com, client, self.w)
        self.com = com
        for patcher in (
            patch.dict(sys.modules, modules),
            patch.object(n, "owned_session", new=fake_owned_session),
            patch.object(
                n, "registered_security_module", return_value={"SYNTHETIC": True}
            ),
            patch.object(cells, "plain_profile", return_value={"SYNTHETIC": True}),
            patch.object(cells, "capture", side_effect=self.binding),
            patch.object(
                r.hwp_fill_plan,
                "write_input",
                side_effect=AssertionError(
                    "Never create/import prepared HWPX for direct fill"
                ),
            ),
        ):
            patcher.start()
            self.addCleanup(patcher.stop)

    def binding(self, app, copied, sha, baseline, selected):
        self.assertEqual(Path(app.Open.call_args.args[0]), copied)
        self.assertEqual(app.Open.call_args.args[1], "HWP")
        self.assertEqual(copied.read_bytes(), self.source.read_bytes())
        return {
            "source_sha256": sha,
            "baseline_hwpx_sha256": r.file_hash(baseline),
            "full_text_sha256": hashlib.sha256(app.text.encode("utf-8")).hexdigest(),
            "targets": [
                {
                    "field_id": item["field_id"],
                    "cell_path": item["cell_path"],
                    "row": int(item["cell_address"]["rowAddr"]),
                    "col": int(item["cell_address"]["colAddr"]),
                    "position": [41 + index * 8, 0, 0],
                }
                for index, item in enumerate(selected)
            ],
        }

    def execute_fake_qualified(self):
        return n.export_phase(self.w, self.run_dir.name, 0)

    def test_paths_derive_original_hwp_copy_and_old_prepared_file_is_not_reused(self):
        paths = n.step_paths(self.w, self.run_dir.name, 0)
        self.assertEqual(paths[6], paths[5])
        self.assertEqual(paths[6].suffix, ".hwp")
        (self.run_dir / "contract-approved-input.hwpx").write_bytes(
            b"KEEP FAILED EVIDENCE"
        )
        with self.assertRaises(FileExistsError):
            n.step_paths(self.w, self.run_dir.name, 0)

    def test_unsupported_schema4_profile_still_denies_before_copy_or_com(self):
        cells.plain_profile.side_effect = ValueError("SYNTHETIC unsupported profile")
        with self.assertRaisesRegex(ValueError, "unsupported profile"):
            n.export_phase(self.w, self.run_dir.name, 0)
        getattr(self.com, "CoInitialize").assert_not_called()
        self.app.Open.assert_not_called()
        self.app.HAction.Execute.assert_not_called()
        self.assertFalse((self.run_dir / "contract-source-copy-0.hwp").exists())

    def test_unsupported_profile_stops_before_security_copy_outputs_or_native(self):
        before = {
            str(path.relative_to(self.run_dir)): r.file_hash(path)
            for path in self.run_dir.rglob("*")
            if path.is_file()
        }
        cells.plain_profile.side_effect = ValueError(
            "Reading-order preservation unverified"
        )
        guard = Mock()
        session = Mock(side_effect=AssertionError("No Native session"))
        with self.assertRaisesRegex(
            ValueError, "Reading-order preservation unverified"
        ):
            n._export_phase(self.w, self.run_dir.name, 0, guard, session)
        guard.assert_not_called()
        n.registered_security_module.assert_not_called()
        session.assert_not_called()
        getattr(self.com, "CoInitialize").assert_not_called()
        self.app.Open.assert_not_called()
        self.app.HAction.Execute.assert_not_called()
        self.app.SaveAs.assert_not_called()
        self.assertEqual(
            before,
            {
                str(path.relative_to(self.run_dir)): r.file_hash(path)
                for path in self.run_dir.rglob("*")
                if path.is_file()
            },
        )
        self.assertFalse((self.run_dir / "contract-source-copy-0.hwp").exists())
        self.assertEqual(n.QUALIFIED_SCHEMAS, (3, 4))

    def test_fake_direct_fill_uses_original_copy_no_hwp_x_import_and_exact_targets(
        self,
    ):
        result = self.execute_fake_qualified()
        self.assertEqual(
            len(result["native_selected_text_evidence"]), len(self.selected)
        )
        self.assertIn("native_empty_cell_binding", result)
        self.assertEqual(self.app.HAction.Execute.call_count, len(self.selected))
        self.assertEqual(self.app.SaveAs.call_count, 3)
        self.assertEqual(self.source.read_bytes(), b"SYNTHETIC NOT HWP; NEVER COM")
        self.assertFalse((self.run_dir / "contract-approved-input.hwpx").exists())
        self.assertTrue((self.w / r.APPLY_LOCK).exists())
        self.app.Clear.assert_called_once()
        self.app.Quit.assert_called_once()
        getattr(self.com, "CoUninitialize").assert_called_once()

    def test_binding_drift_fails_before_insert_and_save(self):
        original = self.binding

        def drift(*args):
            proof = original(*args)
            proof["targets"][0]["cell_path"] = [999]
            return proof

        with (
            patch.object(cells, "capture", side_effect=drift),
            self.assertRaises(ValueError),
        ):
            self.execute_fake_qualified()
        self.app.HAction.Execute.assert_not_called()
        self.app.SaveAs.assert_not_called()

    def test_approval_revocation_between_fields_stops_before_next_insert(self):
        def revoke(*args):
            self.app.text += self.app.HParameterSet.HInsertText.Text
            approval = r.read_json(self.w / "approved-plan.json")
            approval["approved_scope"] = {}
            r.write_json(self.w / "approved-plan.json", approval)
            return True

        self.app.HAction.Execute.side_effect = revoke
        with self.assertRaises(ValueError):
            self.execute_fake_qualified()
        self.app.HAction.Execute.assert_called_once()
        self.app.SaveAs.assert_not_called()
        self.assertTrue((self.w / r.APPLY_LOCK).exists())

    def test_lease_revocation_between_fields_stops_before_next_insert(self):
        def revoke(*args):
            self.app.text += self.app.HParameterSet.HInsertText.Text
            lease = r.read_json(self.w / r.APPLY_LOCK)
            lease["owner_nonce"] = "0" * 32
            r.write_json(self.w / r.APPLY_LOCK, lease)
            return True

        self.app.HAction.Execute.side_effect = revoke
        with self.assertRaises((ValueError, RuntimeError)):
            self.execute_fake_qualified()
        self.app.HAction.Execute.assert_called_once()
        self.app.SaveAs.assert_not_called()
        self.assertTrue((self.w / r.APPLY_LOCK).exists())

    def test_run_plan_drift_between_fields_stops_before_next_insert(self):
        def drift(*args):
            self.app.text += self.app.HParameterSet.HInsertText.Text
            plan = r.read_json(self.run_dir / "plan.json")
            plan["unapproved_change"] = True
            r.write_json(self.run_dir / "plan.json", plan)
            return True

        self.app.HAction.Execute.side_effect = drift
        with self.assertRaises(ValueError):
            self.execute_fake_qualified()
        self.app.HAction.Execute.assert_called_once()
        self.app.SaveAs.assert_not_called()

    def test_revocation_during_mapping_prevents_first_insert(self):
        original = self.binding

        def capture(*args):
            proof = original(*args)
            approval = r.read_json(self.w / "approved-plan.json")
            approval["approved_scope"] = {}
            r.write_json(self.w / "approved-plan.json", approval)
            return proof

        with (
            patch.object(cells, "capture", side_effect=capture),
            self.assertRaises(ValueError),
        ):
            self.execute_fake_qualified()
        self.app.HAction.Execute.assert_not_called()
        self.app.SaveAs.assert_not_called()

    def test_revocation_during_first_save_prevents_remaining_outputs(self):
        def save(path, *args):
            Path(path).write_bytes(b"SYNTHETIC FIRST OUTPUT")
            approval = r.read_json(self.w / "approved-plan.json")
            approval["approved_scope"] = {}
            r.write_json(self.w / "approved-plan.json", approval)
            return True

        self.app.SaveAs.side_effect = save
        with self.assertRaises(ValueError):
            self.execute_fake_qualified()
        self.app.SaveAs.assert_called_once()
        self.assertTrue((self.run_dir / "candidate.hwp").exists())
        self.assertFalse((self.run_dir / "candidate.hwpx").exists())
        self.assertTrue((self.w / r.APPLY_LOCK).exists())

    def test_mapper_or_insertion_error_retains_failure_evidence_and_lease(self):
        with (
            patch.object(
                cells, "capture", side_effect=ValueError("SYNTHETIC cell text mismatch")
            ),
            self.assertRaises(ValueError),
        ):
            self.execute_fake_qualified()
        self.assertTrue((self.run_dir / "contract-source-copy-0.hwp").exists())
        self.assertTrue((self.w / r.APPLY_LOCK).exists())
        self.app.SaveAs.assert_not_called()
        self.app.Quit.assert_called_once()

    def test_source_copy_drift_after_insert_never_saves(self):
        def drift(*args):
            self.app.text += self.app.HParameterSet.HInsertText.Text
            (self.run_dir / "contract-source-copy-0.hwp").write_bytes(b"CHANGED")
            return True

        self.app.HAction.Execute.side_effect = drift
        with self.assertRaises(ValueError):
            self.execute_fake_qualified()
        self.app.HAction.Execute.assert_called_once()
        self.app.SaveAs.assert_not_called()


if __name__ == "__main__":
    unittest.main()
