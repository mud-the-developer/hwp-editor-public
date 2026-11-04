"""Cell-position calibration tests: fake COM/positions only, never a real Hwp."""

import importlib
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch
import test_native_owner as ownership_fixtures

m = importlib.import_module("native_cell_position_probe")
NATIVE_ONCE = m.native_once


class Params:
    def __init__(self, items=None):
        self.items = items or {"Width": 100, "Height": 200}
        self.Count = len(self.items)
        self.SetID = "SYNTHETIC"

    def ItemExist(self, key):
        return key in self.items

    def Item(self, key):
        return self.items[key]


class CellPositionProbeTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name).resolve()
        (self.root / "work").mkdir()
        for patcher in (
            patch.object(m, "ROOT", self.root),
            patch.object(
                m, "registered_security_module", return_value={"SYNTHETIC_ONLY": True}
            ),
            patch.object(
                m, "hwp_inventory", side_effect=AssertionError("OFFLINE inventory only")
            ),
            patch.object(
                m, "native_once", side_effect=AssertionError("OFFLINE no real COM")
            ),
        ):
            patcher.start()
            self.addCleanup(patcher.stop)
        self.source = self.root / "TOY-ONLY-source.hwp"
        self.source.write_bytes(b"SYNTHETIC NOT HWP")
        self.known = {"1,1": [3, 0, 0], "2,1": [5, 0, 0], "3,1": [7, 0, 0]}
        self.grid = {
            "0,0": [1, 0, 0],
            "0,1": [2, 0, 0],
            "1,0": [4, 0, 0],
            "1,1": [3, 0, 0],
            "2,0": [6, 0, 0],
            "2,1": [5, 0, 0],
            "3,0": [8, 0, 0],
            "3,1": [7, 0, 0],
        }
        self.document = SimpleNamespace(FullName=str(self.source), Modified=False)
        anchor = Params({"List": 0, "Para": 2, "Pos": 0})
        self.table = SimpleNamespace(
            CtrlID="tbl",
            Next=None,
            Properties=Params(),
            GetAnchorPos=Mock(return_value=anchor),
        )
        self.app = SimpleNamespace(
            HeadCtrl=self.table,
            CellShape=Params(),
            XHwpDocuments=SimpleNamespace(Count=1, Item=lambda _: self.document),
            pos=[7, 0, 0],
            GetTextFile=Mock(return_value=m.fixture.MARKER + "\nSYNTHETIC LABELS"),
            KeyIndicator=Mock(return_value=(1, 1, 1, 1, 0, 0, False, "(표 1) B2")),
        )
        self.app.GetPos = lambda: tuple(self.app.pos)
        self.app.GetSelectedPos = Mock(
            side_effect=lambda: (True, *self.app.pos, *self.app.pos)
        )
        self.app.SetPos = Mock(
            side_effect=lambda *p: setattr(self.app, "pos", list(p)) or True
        )

        def move(action):
            if action == "MoveRight":
                self.app.pos = list(self.grid["0,0"])
                return True
            address = next(key for key, p in self.grid.items() if p == self.app.pos)
            row, col = map(int, address.split(","))
            if action == "TableRightCell":
                col += 1
            elif action == "TableLeftCell":
                col -= 1
            elif action == "TableLowerCell":
                row += 1
            else:
                raise AssertionError("Unexpected test action")
            self.app.pos = list(self.grid[f"{row},{col}"])
            return True

        self.app.Run = Mock(side_effect=move)

    def test_only_captured_positions_are_read_and_no_writer_claim_is_invented(self):
        before = self.source.read_bytes()
        result = m.observe_saved(self.app, self.source, self.known)
        self.assertEqual(
            [o["captured_position"] for o in result["captured_cell_observations"]],
            list(self.known.values()),
        )
        self.assertFalse(result["writer_qualified"])
        self.assertFalse(result["general_template_binding_proven"])
        self.assertFalse(result["key_indicator_is_binding_proof"])
        self.assertEqual(self.source.read_bytes(), before)
        self.assertEqual(self.app.SetPos.call_count, 3)
        self.assertFalse(
            result["captured_cell_observations"][0]["cell_shape"][
                "declared_key_observations"
            ]["Row"]["present"]
        )

    def test_typed_positions_missing_or_duplicate_targets_stop_before_navigation(self):
        for value in ((True, 0, 0), (0, 0), (-1, 0, 0), (0, 0, 2**32)):
            with self.assertRaises(ValueError):
                m.position(value)
        for known in ({"1,1": [3, 0, 0]}, {**self.known, "3,1": [3, 0, 0]}):
            with self.assertRaises(ValueError):
                m.observe_saved(self.app, self.source, known)
        self.app.SetPos.assert_not_called()

    def test_control_ambiguity_and_cycles_are_bounded(self):
        self.table.Next = self.table
        with self.assertRaises(ValueError):
            m.observe_saved(self.app, self.source, self.known)
        self.app.SetPos.assert_not_called()
        self.table.Next = SimpleNamespace(
            CtrlID="tbl",
            Next=None,
            Properties=Params(),
            GetAnchorPos=self.table.GetAnchorPos,
        )
        with self.assertRaises(ValueError):
            m.observe_saved(self.app, self.source, self.known)

    def test_document_identity_or_text_mutation_never_passes_metadata_check(self):
        for field, value in (
            ("FullName", str(self.root / "OTHER.hwp")),
            ("Modified", True),
        ):
            old = getattr(self.document, field)
            setattr(self.document, field, value)
            try:
                with self.assertRaises(ValueError):
                    m.observe_saved(self.app, self.source, self.known)
            finally:
                setattr(self.document, field, old)
        self.app.SetPos.side_effect = lambda *p: (
            setattr(self.app, "pos", list(p))
            or setattr(self.document, "Modified", True)
            or True
        )
        with self.assertRaises(ValueError):
            m.observe_saved(self.app, self.source, self.known)

    def test_selection_return_metadata_is_recorded_without_collapsed_interpretation(
        self,
    ):
        for value in ((False, 0, 0, 0, 0, 0, 0), (True, 3, 0, 0, 4, 0, 0), ()):
            self.app.GetSelectedPos.side_effect = None
            self.app.GetSelectedPos.return_value = value
            result = m.selection_metadata(self.app)
            self.assertEqual(result["sequence_length"], len(value))
            self.assertEqual(
                [item["value"] for item in result["typed_items"]], list(value)
            )
            self.assertEqual(result["interpretation"], "unclassified")
            self.assertFalse(result["writer_qualified"])

    def test_selection_return_metadata_has_scalar_and_length_bounds(self):
        for value in ("not tuple", (object(),), tuple(range(17)), (2**80,)):
            self.app.GetSelectedPos.side_effect = None
            self.app.GetSelectedPos.return_value = value
            with self.assertRaises(ValueError):
                m.selection_metadata(self.app)

    def test_selection_metadata_does_not_tolerate_caret_mutation(self):
        self.app.GetSelectedPos.side_effect = lambda: (
            setattr(self.app, "pos", [99, 0, 0]) or (False, 0, 0, 0, 0, 0, 0)
        )
        with self.assertRaises(ValueError):
            m.selection_metadata(self.app)

    def test_parameter_and_indicator_metadata_have_finite_bounds(self):
        for bad in (None, SimpleNamespace(Count=10000), SimpleNamespace(Count=True)):
            with self.assertRaises(ValueError):
                m.parameters(bad)
        self.assertEqual(m.scalar(object()), {"kind": "object"})
        with self.assertRaises(ValueError):
            m.indicator("not a tuple")

    def test_authoring_observer_receives_explicit_fixture_cells_not_guessed_data(self):
        char = SimpleNamespace(HSet=object())
        insert = SimpleNamespace(HSet=object())
        table = SimpleNamespace(
            HSet=object(),
            TableProperties=SimpleNamespace(),
            CreateItemArray=Mock(),
            ColWidth=SimpleNamespace(SetItem=Mock()),
        )
        app = SimpleNamespace(
            HParameterSet=SimpleNamespace(
                HCharShape=char, HInsertText=insert, HTableCreation=table
            ),
            HAction=SimpleNamespace(
                GetDefault=Mock(),
                Execute=Mock(return_value=True),
                Run=Mock(return_value=True),
            ),
            MiliToHwpUnit=lambda v: v,
            Run=Mock(return_value=True),
        )
        captured = []
        m.fixture.author(
            app, observe_cell=lambda row, col, app: captured.append((row, col))
        )
        self.assertEqual(captured, [(row, col) for row in range(4) for col in (0, 1)])

    def test_default_frozen_reused_or_outside_paths_do_not_call_native(self):
        output = self.root / "work/toy-cell-position-SYNTHETIC"
        with patch.object(m, "native_once") as native:
            for kw in (
                {},
                {"run_native": True},
                {"run_native": 1, "metadata_only": True},
            ):
                with self.assertRaises(ValueError):
                    m.run(output, **kw)
            with (
                patch.object(m.sys, "frozen", True, create=True),
                self.assertRaises(ValueError),
            ):
                m.run(output, run_native=True, metadata_only=True)
            for target in (self.root / "outside", self.root / "work/toy-fill-OTHER"):
                with self.assertRaises(ValueError):
                    m.run(target, run_native=True, metadata_only=True)
            output.mkdir()
            with self.assertRaises(ValueError):
                m.run(output, run_native=True, metadata_only=True)
            native.assert_not_called()

    def test_native_lifecycle_only_saves_seed_source_and_reads_metadata_afterward(self):
        harness = OwnedCreatorFixture(self)
        report = {}
        NATIVE_ONCE(harness.directory, report)
        self.assertEqual(report["status"], "metadata_observed_not_writer_qualified")
        self.assertTrue(report["owned_process_exit_confirmed"])
        self.assertEqual(report["ownership"]["status"], "owned_exit_confirmed")
        self.assertTrue(report["source_reopened"])
        self.assertTrue(report["anchor_navigation"]["all_authored_positions_match"])
        self.assertEqual(len(report["anchor_navigation"]["steps"]), 8)
        self.assertEqual(report["authored_grid_before_source_save"], harness.cell.grid)
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
        harness.app.Open.assert_called_once_with(
            str(harness.source), "HWP", "versionwarning:false"
        )
        harness.owner.client.DispatchEx.assert_called_once_with("HWPFrame.HwpObject")
        harness.owner.held.wait_for_exit.assert_called_once()
        harness.owner.held.close.assert_called_once()

    def test_anchor_walk_uses_captured_truth_not_arithmetic_list_ids(self):
        before = self.source.read_bytes()
        evidence = {}
        m.observe_anchor_walk(self.app, self.source, self.grid, [0, 2, 0], evidence)
        self.assertTrue(evidence["all_authored_positions_match"])
        self.assertFalse(evidence["writer_qualified"])
        self.assertFalse(evidence["general_template_binding_proven"])
        self.assertEqual(len(evidence["steps"]), 8)
        self.assertEqual(self.source.read_bytes(), before)
        self.assertEqual(
            [step["captured_position"] for step in evidence["steps"]],
            list(self.grid.values()),
        )
        self.assertEqual(
            [call.args[0] for call in self.app.Run.call_args_list],
            [
                "MoveRight",
                "TableRightCell",
                "TableLeftCell",
                "TableLowerCell",
                "TableRightCell",
                "TableLeftCell",
                "TableLowerCell",
                "TableRightCell",
                "TableLeftCell",
                "TableLowerCell",
                "TableRightCell",
            ],
        )

    def test_wrong_anchor_entry_stops_before_another_navigation_action(self):
        self.app.Run.side_effect = lambda action: (
            setattr(self.app, "pos", [999, 0, 0]) or True
        )
        evidence = {}
        with self.assertRaises(ValueError):
            m.observe_anchor_walk(self.app, self.source, self.grid, [0, 2, 0], evidence)
        self.app.Run.assert_called_once_with("MoveRight")
        self.assertEqual(len(evidence["steps"]), 1)
        self.assertNotIn("all_authored_positions_match", evidence)

    def test_incomplete_grid_is_rejected_before_any_setpos_or_run(self):
        with self.assertRaises(ValueError):
            m.observe_anchor_walk(self.app, self.source, self.known, [0, 2, 0], {})
        self.app.SetPos.assert_not_called()
        self.app.Run.assert_not_called()

    def test_caret_navigation_may_not_modify_the_saved_source(self):
        move = self.app.Run.side_effect

        def mutate(action):
            result = move(action)
            self.document.Modified = True
            return result

        self.app.Run.side_effect = mutate
        with self.assertRaises(ValueError):
            m.observe_anchor_walk(self.app, self.source, self.grid, [0, 2, 0], {})
        self.assertEqual(self.source.read_bytes(), b"SYNTHETIC NOT HWP")

    def test_a_single_failure_creates_one_immutable_result_and_never_retries(self):
        output = self.root / "work/toy-cell-position-SYNTHETIC"
        with patch.object(
            m, "native_once", side_effect=RuntimeError("SYNTHETIC API unavailable")
        ) as native:
            result = m.run(output, run_native=True, metadata_only=True)
            native.assert_called_once()
        self.assertEqual(result["status"], "blocked")
        self.assertFalse(result["qualification"])
        self.assertEqual(result["candidate_exports"], 0)
        self.assertFalse(result["field_values_inserted_after_source"])
        original = (output / "CELL-POSITION-RESULT.json").read_bytes()
        with self.assertRaises(ValueError):
            m.run(output, run_native=True, metadata_only=True)
        self.assertEqual((output / "CELL-POSITION-RESULT.json").read_bytes(), original)


class OwnedCreatorFixture:
    """Actual shared cache/session and observation logic; fake OS/COM only."""

    def __init__(self, test):
        self.test = test
        self.cell = CellPositionProbeTests()
        self.cell.setUp()
        test.addCleanup(self.cell.doCleanups)
        self.owner = ownership_fixtures.OwnedSessionTests()
        self.owner.setUp()
        test.addCleanup(self.owner.doCleanups)
        self.directory = self.owner.root / "source"
        self.directory.mkdir()
        self.seed = self.directory / "TOY-ONLY-authoring-seed.hwp"
        self.source = self.directory / "TOY-ONLY-source.hwp"
        self.app = self.cell.app
        self.doc = self.owner.doc
        self.app.XHwpWindows = self.owner.app.XHwpWindows
        self.app.XHwpDocuments = self.owner.app.XHwpDocuments
        self.app.RegisterModule = Mock(return_value=True)
        self.app.PageCount = 1
        self.text = None
        self.saved_text = {}
        self.log = []
        self.app.GetTextFile = Mock(side_effect=lambda fmt, options: self.text)
        self.app.SaveAs = Mock(side_effect=self.save)
        self.app.Open = Mock(side_effect=self.open)
        self.app.Clear = Mock(side_effect=self.clear)
        self.app.Quit = Mock(side_effect=lambda: self.log.append(("Quit",)))
        self.owner.client.DispatchEx.return_value = self.app
        self.author = Mock(side_effect=self.author_source)
        for patcher in (
            patch.object(m, "hwp_inventory", return_value=[]),
            patch.object(m.fixture, "author", self.author),
        ):
            patcher.start()
            test.addCleanup(patcher.stop)

    def save(self, name, fmt, options):
        self.test.assertEqual((fmt, options), ("HWP", ""))
        path = Path(name)
        with path.open("xb") as stream:
            stream.write(
                b"SYNTHETIC SEED" if path == self.seed else b"SYNTHETIC SOURCE"
            )
        self.doc.FullName, self.doc.Path, self.doc.Modified = (
            str(path),
            str(path.parent),
            False,
        )
        self.saved_text[path] = self.text
        self.log.append(("SaveAs", fmt))
        return True

    def author_source(self, app, observe_cell):
        self.test.assertEqual(self.doc.FullName, str(self.seed))
        self.test.assertTrue(self.seed.is_file())
        self.test.assertFalse(self.source.exists())
        self.doc.Modified = True
        self.text = m.fixture.MARKER + "\nSYNTHETIC LABELS"
        for row in range(4):
            for col in range(2):
                self.app.pos = list(self.cell.grid[f"{row},{col}"])
                observe_cell(row, col, app)

    def open(self, name, fmt, options):
        self.test.assertEqual((fmt, options), ("HWP", "versionwarning:false"))
        path = Path(name)
        self.doc.FullName, self.doc.Path, self.doc.Modified = (
            str(path),
            str(path.parent),
            False,
        )
        self.text = self.saved_text[path]
        self.log.append(("Open", fmt))
        return True

    def clear(self, mode):
        self.owner.clear(mode)
        self.text = None
        self.log.append(("Clear", mode))


class CellCreatorOwnershipTests(unittest.TestCase):
    def setUp(self):
        self.h = OwnedCreatorFixture(self)
        self.report = {}

    def invoke(self):
        NATIVE_ONCE(self.h.directory, self.report)

    def test_seed_or_source_collision_preserved_before_com(self):
        for path in (self.h.seed, self.h.source):
            path.write_bytes(b"KEEP COLLISION")
            with self.assertRaises(FileExistsError):
                self.invoke()
            self.assertEqual(path.read_bytes(), b"KEEP COLLISION")
            path.unlink()
        self.h.owner.client.DispatchEx.assert_not_called()

    def test_existing_process_stops_before_source_or_seed(self):
        m.hwp_inventory.return_value = [{"pid": 99, "creation_ms": 1}]
        with self.assertRaises(RuntimeError):
            self.invoke()
        self.h.owner.client.DispatchEx.assert_not_called()
        self.assertFalse(self.h.seed.exists())

    def test_unowned_entry_cannot_register_save_author_or_cleanup(self):
        self.h.owner.metadata.bind_window_process.side_effect = RuntimeError(
            "SYNTHETIC unowned"
        )
        with self.assertRaisesRegex(RuntimeError, "unconfirmed"):
            self.invoke()
        self.h.app.RegisterModule.assert_not_called()
        self.h.app.SaveAs.assert_not_called()
        self.h.author.assert_not_called()
        self.h.app.Clear.assert_not_called()
        self.h.app.Quit.assert_not_called()
        self.assertFalse(self.report["owned_process_exit_confirmed"])

    def test_registration_failure_stops_before_mutation(self):
        self.h.app.RegisterModule.return_value = False
        with self.assertRaisesRegex(RuntimeError, "module rejected"):
            self.invoke()
        self.h.author.assert_not_called()
        self.h.app.SaveAs.assert_not_called()
        self.h.app.Clear.assert_called_once()
        self.h.app.Quit.assert_called_once()

    def test_seed_failure_stops_before_authoring_and_confirms_pristine_cleanup(self):
        self.h.app.SaveAs.side_effect = None
        self.h.app.SaveAs.return_value = False
        with self.assertRaisesRegex(RuntimeError, "seed save failed"):
            self.invoke()
        self.h.author.assert_not_called()
        self.h.app.SaveAs.assert_called_once()
        self.h.app.Quit.assert_called_once()
        self.assertTrue(self.report["owned_process_exit_confirmed"])

    def test_inventory_failure_does_not_replace_original_author_failure(self):
        m.hwp_inventory.side_effect = [
            [],
            RuntimeError("SYNTHETIC inventory unavailable"),
        ]

        def fail(app, observe_cell):
            self.h.author_source(app, observe_cell)
            raise LookupError("SYNTHETIC author failure")

        self.h.author.side_effect = fail
        with self.assertRaisesRegex(LookupError, "author failure"):
            self.invoke()
        self.assertTrue(self.report["ownership"]["exit_confirmed"])
        self.assertFalse(self.report["owned_process_exit_confirmed"])
        self.assertIn("inventory unavailable", self.report["inventory_error"])

    def test_new_post_inventory_process_cannot_make_the_trial_successful(self):
        m.hwp_inventory.side_effect = [[], [{"pid": 99, "creation_ms": 1}]]
        with self.assertRaisesRegex(RuntimeError, "exit unconfirmed"):
            self.invoke()
        self.assertTrue(self.report["ownership"]["exit_confirmed"])
        self.assertFalse(self.report["owned_process_exit_confirmed"])
        self.assertEqual(self.report["status"], "blocked")

    def test_author_failure_cleans_only_named_seed_not_unnamed_dirty_blank(self):
        def fail(app, observe_cell):
            self.h.author_source(app, observe_cell)
            raise LookupError("SYNTHETIC author failure")

        self.h.author.side_effect = fail
        with self.assertRaisesRegex(LookupError, "author failure"):
            self.invoke()
        self.assertTrue(self.h.seed.exists())
        self.assertFalse(self.h.source.exists())
        self.h.app.SaveAs.assert_called_once()
        self.h.app.Quit.assert_called_once()
        self.assertTrue(self.report["owned_process_exit_confirmed"])
        self.assertEqual(self.report["status"], "blocked")

    def test_final_file_appearing_after_authoring_is_never_overwritten(self):
        def collide(app, observe_cell):
            self.h.author_source(app, observe_cell)
            self.h.source.write_bytes(b"KEEP COLLISION")

        self.h.author.side_effect = collide
        with self.assertRaisesRegex(RuntimeError, "source save failed"):
            self.invoke()
        self.assertEqual(self.h.source.read_bytes(), b"KEEP COLLISION")
        self.h.app.SaveAs.assert_called_once()
        self.assertTrue(self.report["owned_process_exit_confirmed"])

    def test_reopen_failure_stops_without_retry_and_cleans_verified_blank(self):
        self.h.app.Open.side_effect = None
        self.h.app.Open.return_value = False
        with self.assertRaisesRegex(RuntimeError, "reopen failed"):
            self.invoke()
        self.h.app.Open.assert_called_once()
        self.h.app.Quit.assert_called_once()
        self.assertTrue(self.report["owned_process_exit_confirmed"])

    def test_unknown_document_after_authoring_is_not_cleared_or_quit(self):
        def unknown(app, observe_cell):
            self.h.author_source(app, observe_cell)
            self.h.doc.FullName = str(self.h.owner.root / "USER.hwp")
            raise LookupError("SYNTHETIC unknown state")

        self.h.author.side_effect = unknown
        with self.assertRaisesRegex(RuntimeError, "unconfirmed"):
            self.invoke()
        self.h.app.Clear.assert_not_called()
        self.h.app.Quit.assert_not_called()
        self.assertFalse(self.report["owned_process_exit_confirmed"])

    def test_same_handle_exit_timeout_never_claims_ownership_success(self):
        self.h.owner.held.wait_for_exit.side_effect = RuntimeError("SYNTHETIC timeout")
        with self.assertRaisesRegex(RuntimeError, "timeout"):
            self.invoke()
        self.h.app.Quit.assert_called_once()
        self.h.owner.held.wait_for_exit.assert_called_once()
        self.assertFalse(self.report["owned_process_exit_confirmed"])
        self.assertEqual(self.report["status"], "blocked")


if __name__ == "__main__":
    unittest.main()
