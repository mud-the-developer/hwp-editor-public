"""Native1/3/GUI TOY authoring: real shared session with fake OS/COM only."""

from contextlib import contextmanager
import importlib
import json
from pathlib import Path
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch
import test_native_owner as fixtures

text_lab = importlib.import_module("native_text_apply_smoke")
contract_lab = importlib.import_module("native_contract_smoke")


class NativeLabSourceOwnershipTests(unittest.TestCase):
    @contextmanager
    def case(self, module, schema=None):
        h = fixtures.OwnedSessionTests()
        h.setUp()
        try:
            directory = h.root / "source"
            seed = directory / "TOY-ONLY-authoring-seed.hwp"
            source = directory / (
                "TOY-ONLY-unsigned-text.hwp"
                if module is text_lab
                else "TOY-ONLY-unsigned-contract.hwp"
            )
            app = h.app
            shape = SimpleNamespace(HSet=object(), Height=0, FaceNameLatin="")
            insert = SimpleNamespace(HSet=object(), Text="")
            table = SimpleNamespace(
                HSet=object(),
                TableProperties=SimpleNamespace(TreatAsChar=0),
                CreateItemArray=Mock(),
                ColWidth=SimpleNamespace(SetItem=Mock()),
            )
            app.HParameterSet = SimpleNamespace(
                HCharShape=shape, HInsertText=insert, HTableCreation=table
            )
            app.RegisterModule = Mock(return_value=True)
            app.Run = Mock(return_value=True)
            app.MiliToHwpUnit = Mock(side_effect=lambda value: value * 100)
            literals = []

            def author_action(action, params):
                self.assertTrue(seed.is_file())
                self.assertEqual(h.doc.FullName, str(seed))
                self.assertFalse(source.exists())
                h.doc.Modified = True
                app.GetTextFile.return_value = "SYNTHETIC AUTHORED BUFFER"
                if action == "InsertText":
                    literals.append(insert.Text)
                return True

            def save(name, fmt, options):
                self.assertEqual((fmt, options), ("HWP", ""))
                path = Path(name)
                with path.open("xb") as stream:
                    stream.write(
                        b"SYNTHETIC SEED" if path == seed else b"SYNTHETIC SOURCE"
                    )
                h.doc.FullName, h.doc.Path, h.doc.Modified = (
                    str(path),
                    str(path.parent),
                    False,
                )
                return True

            app.HAction = SimpleNamespace(
                GetDefault=Mock(), Execute=Mock(side_effect=author_action)
            )
            app.SaveAs = Mock(side_effect=save)
            inventory = Mock(return_value=[])
            registration_target = module if module is text_lab else module.security
            with (
                patch.object(module, "hwp_inventory", inventory),
                patch.object(
                    registration_target,
                    "registered_security_module",
                    return_value={"SYNTHETIC": True},
                ) as registration,
                patch.object(
                    module.security,
                    "ensure_security_module",
                    side_effect=AssertionError("No installation"),
                ),
            ):

                def invoke():
                    return (
                        module.create_source(directory)
                        if schema is None
                        else module.create_source(directory, schema)
                    )

                def proof():
                    return json.loads(
                        (directory / "TOY-SOURCE-CREATION.json").read_text(
                            encoding="utf-8"
                        )
                    )

                yield SimpleNamespace(
                    h=h,
                    app=app,
                    directory=directory,
                    seed=seed,
                    source=source,
                    shape=shape,
                    table=table,
                    literals=literals,
                    inventory=inventory,
                    registration=registration,
                    invoke=invoke,
                    proof=proof,
                    author_action=author_action,
                )
        finally:
            h.doCleanups()

    def cases(self):
        return ((text_lab, None), (contract_lab, 3))

    def test_text_payload_font_and_return_path_remain_exact(self):
        with self.case(text_lab) as c:
            self.assertEqual(c.invoke(), c.source)
            self.assertEqual(
                c.literals,
                [
                    text_lab.MARKER,
                    "Fictional equal-length monospace code. No seals, signatures, tables or images.",
                    text_lab.OLD,
                ],
            )
            self.assertEqual(
                (c.shape.Height, c.shape.FaceNameLatin), (1000, "Courier New")
            )
            self.assertEqual(
                [call.args[0] for call in c.app.Run.call_args_list],
                ["BreakPara", "BreakPara"],
            )
            self.assertEqual(
                [Path(call.args[0]) for call in c.app.SaveAs.call_args_list],
                [c.seed, c.source],
            )
            self.assertEqual(c.proof()["ownership"]["status"], "owned_exit_confirmed")
            self.assertFalse(c.proof()["qualification"])
            c.app.Quit.assert_called_once()
            c.h.held.wait_for_exit.assert_called_once()

    def test_contract_schema_specific_fixed_shapes_and_literals_are_preserved(self):
        for schema in (2, 3, 4):
            with self.subTest(schema=schema), self.case(contract_lab, schema) as c:
                self.assertEqual(c.invoke(), c.source)
                self.assertEqual(
                    c.literals[:2],
                    [
                        contract_lab.MARKER,
                        "FICTIONAL ONLY; own source; no seals/signatures/images.",
                    ],
                )
                if schema == 2:
                    self.assertEqual(c.literals[-1], contract_lab.OLD)
                    c.app.MiliToHwpUnit.assert_not_called()
                else:
                    self.assertEqual(
                        (
                            c.table.Rows,
                            c.table.Cols,
                            c.table.TableProperties.TreatAsChar,
                        ),
                        (4, 2, 1),
                    )
                    self.assertEqual(
                        [call.args[0] for call in c.app.MiliToHwpUnit.call_args_list],
                        [140, 48, 45, 95],
                    )
                    self.assertEqual(
                        c.literals[2:],
                        [
                            "TEST LABEL",
                            "TEST INPUT CELLS BELOW",
                            "Name",
                            *([contract_lab.TABLE_OLD] if schema == 3 else []),
                            "Code",
                            "Date",
                        ],
                    )
                self.assertEqual(
                    c.proof()["events"], ["blank_seed_saved", "source_saved"]
                )
                self.assertTrue(c.proof()["ownership"]["exit_confirmed"])

    def test_existing_directory_or_process_stops_before_com(self):
        for module, schema in self.cases():
            with self.subTest(module=module.__name__), self.case(module, schema) as c:
                c.directory.mkdir()
                with self.assertRaises(FileExistsError):
                    c.invoke()
                c.registration.assert_not_called()
                c.directory.rmdir()
                c.inventory.return_value = [{"pid": 99, "creation_ms": 1}]
                with self.assertRaises(RuntimeError):
                    c.invoke()
                self.assertFalse(c.directory.exists())
                c.h.client.DispatchEx.assert_not_called()

    def test_unowned_instance_cannot_register_save_mutate_or_cleanup(self):
        for module, schema in self.cases():
            with self.subTest(module=module.__name__), self.case(module, schema) as c:
                c.h.metadata.bind_window_process.side_effect = RuntimeError(
                    "SYNTHETIC unowned"
                )
                with self.assertRaisesRegex(RuntimeError, "unconfirmed"):
                    c.invoke()
                for method in (
                    c.app.RegisterModule,
                    c.app.SaveAs,
                    c.app.HAction.Execute,
                    c.app.Clear,
                    c.app.Quit,
                ):
                    method.assert_not_called()
                self.assertEqual(c.proof()["status"], "blocked")

    def test_registration_rejection_does_not_name_or_author_a_document(self):
        for module, schema in self.cases():
            with self.subTest(module=module.__name__), self.case(module, schema) as c:
                c.app.RegisterModule.return_value = False
                with self.assertRaisesRegex(RuntimeError, "module rejected"):
                    c.invoke()
                c.app.SaveAs.assert_not_called()
                c.app.HAction.Execute.assert_not_called()
                c.app.Quit.assert_called_once()
                self.assertTrue(c.proof()["ownership"]["exit_confirmed"])

    def test_seed_failure_cleans_pristine_blank_without_authoring(self):
        for module, schema in self.cases():
            with self.subTest(module=module.__name__), self.case(module, schema) as c:
                c.app.SaveAs.side_effect = None
                c.app.SaveAs.return_value = False
                with self.assertRaisesRegex(RuntimeError, "seed save failed"):
                    c.invoke()
                c.app.SaveAs.assert_called_once()
                c.app.HAction.Execute.assert_not_called()
                c.app.Quit.assert_called_once()
                self.assertFalse(c.source.exists())

    def test_authoring_failure_can_only_discard_the_verified_named_seed(self):
        for module, schema in self.cases():
            with self.subTest(module=module.__name__), self.case(module, schema) as c:
                c.app.HAction.Execute.return_value = False
                c.app.HAction.Execute.side_effect = None
                with self.assertRaisesRegex(RuntimeError, "font creation failed"):
                    c.invoke()
                self.assertTrue(c.seed.is_file())
                self.assertFalse(c.source.exists())
                c.app.SaveAs.assert_called_once()
                c.app.Quit.assert_called_once()
                self.assertEqual(c.proof()["status"], "blocked")

    def test_final_source_collision_is_preserved(self):
        for module, schema in self.cases():
            with self.subTest(module=module.__name__), self.case(module, schema) as c:

                def collide(action, params):
                    result = c.author_action(action, params)
                    if action == "InsertText" and c.literals[-1] in (
                        module.OLD,
                        "Date",
                    ):
                        c.source.write_bytes(b"KEEP COLLISION")
                    return result

                c.app.HAction.Execute.side_effect = collide
                with self.assertRaises(FileExistsError):
                    c.invoke()
                self.assertEqual(c.source.read_bytes(), b"KEEP COLLISION")
                c.app.SaveAs.assert_called_once()
                c.app.Quit.assert_called_once()

    def test_unknown_document_after_mutation_does_not_get_clear_or_quit(self):
        for module, schema in self.cases():
            with self.subTest(module=module.__name__), self.case(module, schema) as c:

                def unknown(action, params):
                    c.author_action(action, params)
                    c.h.doc.FullName = str(c.h.root / "USER.hwp")
                    raise LookupError("SYNTHETIC unknown state")

                c.app.HAction.Execute.side_effect = unknown
                with self.assertRaisesRegex(RuntimeError, "unconfirmed"):
                    c.invoke()
                c.app.Clear.assert_not_called()
                c.app.Quit.assert_not_called()
                self.assertEqual(c.proof()["status"], "blocked")

    def test_same_handle_exit_failure_is_never_a_success(self):
        for module, schema in self.cases():
            with self.subTest(module=module.__name__), self.case(module, schema) as c:
                c.h.held.wait_for_exit.side_effect = RuntimeError("SYNTHETIC timeout")
                with self.assertRaisesRegex(RuntimeError, "timeout"):
                    c.invoke()
                c.h.held.wait_for_exit.assert_called_once()
                c.app.Quit.assert_called_once()
                self.assertEqual(c.proof()["status"], "blocked")


if __name__ == "__main__":
    unittest.main()
