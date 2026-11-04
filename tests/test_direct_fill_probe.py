"""Direct fill diagnostic tests: no real COM, Hwp, profile or model."""

import importlib
from itertools import cycle
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

m = importlib.import_module("native_direct_fill_probe")


class DirectFillProbeTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name).resolve()
        (self.root / "work").mkdir()
        self.source = self.root / "TOY-ONLY-source.hwp"
        self.source.write_bytes(b"SYNTHETIC SOURCE")
        self.grid = {
            f"{row},{col}": [20 + row * 3 + col, 0, 0]
            for row in range(4)
            for col in range(2)
        }
        self.proof = {
            "status": "metadata_observed_not_writer_qualified",
            "owned_process_exit_confirmed": True,
            "source_reopened": True,
            "anchor_navigation": {"all_authored_positions_match": True},
            "reopened_observation": {"source_sha256": m.r.file_hash(self.source)},
            "authored_grid_before_source_save": self.grid,
        }
        self.selected = [
            {
                "field_id": "FAKE-" + str(row),
                "cell_address": {"rowAddr": str(row), "colAddr": "1"},
                "new_text": value,
            }
            for row, value in enumerate(m.VALUES.values(), 1)
        ]
        self.app = SimpleNamespace(
            buffer=m.fixture.MARKER + "\nName\t\nCode\t\nDate\t",
            pos=[0, 0, 0],
            XHwpDocuments=SimpleNamespace(
                Count=1,
                Item=lambda _: SimpleNamespace(
                    FullName=str(self.source), Modified=False
                ),
            ),
            HParameterSet=SimpleNamespace(
                HInsertText=SimpleNamespace(HSet=object(), Text="")
            ),
        )
        scan = cycle(((2, "\r\n"), (1, "")))
        self.app.InitScan = Mock(return_value=True)
        self.app.GetText = Mock(side_effect=lambda: next(scan))
        self.app.ReleaseScan = Mock()
        self.app.GetTextFile = lambda *args: self.app.buffer
        self.app.SetPos = Mock(
            side_effect=lambda *pos: setattr(self.app, "pos", list(pos)) or True
        )
        self.app.GetPos = lambda: tuple(self.app.pos)
        self.app.GetSelectedPos = Mock(
            side_effect=lambda: (True, *self.app.pos, *self.app.pos)
        )

        def insert(*args):
            self.app.buffer += self.app.HParameterSet.HInsertText.Text
            return True

        self.app.HAction = SimpleNamespace(
            GetDefault=Mock(), Execute=Mock(side_effect=insert)
        )
        for patcher in (
            patch.object(m, "ROOT", self.root),
            patch.object(
                m,
                "hwp_inventory",
                side_effect=AssertionError("OFFLINE no real inventory"),
            ),
            patch.object(m, "registered_security_module"),
            patch.object(
                m,
                "export_direct",
                side_effect=AssertionError("OFFLINE no Native export"),
            ),
            patch.object(
                m.cell,
                "native_once",
                side_effect=AssertionError("OFFLINE no Native source"),
            ),
        ):
            patcher.start()
            self.addCleanup(patcher.stop)

    def test_exact_source_and_grid_proof_are_required(self):
        self.assertEqual(m.require_binding(self.source, self.proof), self.grid)
        for field, value in (
            ("status", "blocked"),
            ("source_reopened", False),
            ("owned_process_exit_confirmed", False),
        ):
            with self.assertRaises(ValueError):
                m.require_binding(self.source, {**self.proof, field: value})
        with self.assertRaises(ValueError):
            m.require_binding(
                self.source,
                {**self.proof, "reopened_observation": {"source_sha256": "0" * 64}},
            )
        with self.assertRaises(ValueError):
            m.require_binding(
                self.source,
                {**self.proof, "authored_grid_before_source_save": {"1,1": [5, 0, 0]}},
            )

    def test_insertions_add_only_exact_unique_literals_and_never_write_source(self):
        before = self.app.buffer
        raw = self.source.read_bytes()
        evidence = []
        result = m.insert_own_values(
            self.app, self.source, self.grid, self.selected, evidence
        )
        for value in m.VALUES.values():
            result = result.replace(value, "", 1)
        self.assertEqual(result, before)
        self.assertEqual(self.source.read_bytes(), raw)
        self.assertEqual(len(evidence), 3)
        self.assertEqual(self.app.HAction.Execute.call_count, 3)
        self.assertEqual(
            [call.args for call in self.app.SetPos.call_args_list],
            [tuple(self.grid[f"{row},1"]) for row in (1, 2, 3)],
        )

    def test_noncollapsed_or_unknown_selection_blocks_before_insert(self):
        for selection in (
            (True, 1, 0, 0, 2, 0, 0),
            (False, 1, 0, 0, 0, 0, 0),
            (True, 1, 0),
        ):
            self.app.GetSelectedPos.side_effect = None
            self.app.GetSelectedPos.return_value = selection
            with self.assertRaises(ValueError):
                m.insert_own_values(self.app, self.source, self.grid, self.selected, [])
        self.app.HAction.Execute.assert_not_called()

    def test_calibrated_false_zero_no_block_keeps_known_caret_and_source_guards(self):
        self.app.GetSelectedPos.side_effect = None
        self.app.GetSelectedPos.return_value = (False, 0, 0, 0, 0, 0, 0)
        evidence = []
        m.insert_own_values(self.app, self.source, self.grid, self.selected, evidence)
        self.assertEqual(len(evidence), 3)
        self.assertTrue(
            all(
                proof["selection_metadata"]["diagnostic_classification"]
                == "calibrated_no_block"
                for proof in evidence
            )
        )
        self.assertEqual(self.source.read_bytes(), b"SYNTHETIC SOURCE")

    def test_only_exact_boolean_false_zero_sentinel_is_calibrated(self):
        for value in (
            (0, 0, 0, 0, 0, 0, 0),
            (False, True, 0, 0, 0, 0, 0),
            (True, 0, 0, 0, 0, 0, 0),
            (False, 3, 0, 0, 3, 0, 0),
        ):
            self.app.GetSelectedPos.side_effect = None
            self.app.GetSelectedPos.return_value = value
            with self.assertRaises(ValueError):
                m.insert_own_values(self.app, self.source, self.grid, self.selected, [])
        self.app.HAction.Execute.assert_not_called()

    def test_wrong_buffer_delta_stops_before_second_field(self):
        self.app.HAction.Execute.side_effect = lambda *args: (
            setattr(self.app, "buffer", "UNEXPECTED MODIFICATION") or True
        )
        with self.assertRaises(ValueError):
            m.insert_own_values(self.app, self.source, self.grid, self.selected, [])
        self.app.HAction.Execute.assert_called_once()
        self.assertEqual(self.source.read_bytes(), b"SYNTHETIC SOURCE")

    def test_wrong_addresses_or_ambiguous_value_do_not_insert(self):
        with self.assertRaises(ValueError):
            m.insert_own_values(self.app, self.source, self.grid, self.selected[:1], [])
        self.app.buffer += " " + next(iter(m.VALUES.values()))
        with self.assertRaises(ValueError):
            m.insert_own_values(self.app, self.source, self.grid, self.selected, [])
        self.app.HAction.Execute.assert_not_called()

    def test_pipeline_rechecks_both_xml_exports_with_existing_gates(self):
        fixture = importlib.import_module("test_full_fill_harness")
        r = m.r
        stored = {}
        calls = []

        def create(directory, proof):
            source = directory / "TOY-ONLY-source.hwp"
            source.write_bytes(b"SYNTHETIC DIRECT SOURCE")
            proof.update(self.proof)
            proof["reopened_observation"] = {"source_sha256": r.file_hash(source)}

        def prepare(source, workspace):
            workspace.mkdir()
            fixture.package(workspace / "baseline.hwpx", linear="no")
            fixture.pdf(workspace / "baseline.pdf")
            r.write_json(
                workspace / "manifest.json",
                {
                    "workspace_id": "SYNTHETIC-DIRECT",
                    "source": str(source),
                    "source_audit": {"sha256": r.file_hash(source)},
                    "baseline_hwpx_sha256": r.file_hash(workspace / "baseline.hwpx"),
                    "baseline_pdf_sha256": r.file_hash(workspace / "baseline.pdf"),
                    "roundtrip_passed": True,
                    "protected_pages": [],
                    "pages": [{"page": 1, "width": 300, "height": 400}],
                    "anchors": [],
                },
            )

        def direct(source, proof, selected, output, report):
            stored["xml"] = m.fill._input_parts(
                output / "review/baseline.hwpx", selected
            )
            with __import__("zipfile").ZipFile(
                output / "TOY-ONLY-native-filled.hwpx", "x"
            ) as archive:
                for name, data in stored["xml"].items():
                    archive.writestr(name, data)
            (output / "TOY-ONLY-filled.hwp").write_bytes(b"SYNTHETIC DIRECT CANDIDATE")
            fixture.pdf(output / "TOY-ONLY-buffer.pdf", True)

        def reopen(source, pdf, *, hwpx):
            calls.append((source, pdf, hwpx))
            with __import__("zipfile").ZipFile(hwpx, "x") as archive:
                for name, data in stored["xml"].items():
                    archive.writestr(name, data)
            fixture.pdf(pdf, True)
            return {"SYNTHETIC_ONLY": True}

        old_values = dict(m.VALUES)
        try:
            m.VALUES["Name"] = "Synthetic User"
            with (
                patch.object(m, "hwp_inventory", return_value=[]),
                patch.object(m.cell, "native_once", side_effect=create),
                patch.object(r, "prepare", side_effect=prepare),
                patch.object(m, "export_direct", side_effect=direct),
                patch.object(r, "native_export", side_effect=reopen),
                patch.object(
                    r,
                    "hwp_audit",
                    side_effect=lambda p: {
                        "embedded_binary_hashes": [],
                        "sha256": r.file_hash(p),
                    },
                ),
                patch.object(
                    m.fill, "structural_gate", wraps=m.fill.structural_gate
                ) as gate,
            ):
                report = m.run(
                    self.root / "work/toy-direct-fill-PIPELINE",
                    run_native=True,
                    toy_only=True,
                )
            self.assertEqual(
                report["status"], "checks_passed_needs_human_visual_review", report
            )
            self.assertFalse(report["qualification"])
            self.assertEqual(gate.call_count, 2)
            self.assertEqual(calls[0][2].name, "TOY-ONLY-reopened.hwpx")
            self.assertTrue(report["layout_gate"]["passed"])
            self.assertTrue(report["rendered_page_text_exact"])
            self.assertTrue(report["source_unchanged"])
            self.assertTrue(report["initial_outputs_unchanged"])
        finally:
            m.VALUES.clear()
            m.VALUES.update(old_values)

    def test_flags_frozen_existing_or_outside_do_not_create_source(self):
        target = self.root / "work/toy-direct-fill-SYNTHETIC"
        for args in ({}, {"run_native": True}, {"run_native": 1, "toy_only": True}):
            with self.assertRaises(ValueError):
                m.run(target, **args)
        with (
            patch.object(m.sys, "frozen", True, create=True),
            self.assertRaises(ValueError),
        ):
            m.run(target, run_native=True, toy_only=True)
        for path in (self.root / "outside", self.root / "work/toy-fill-WRONG"):
            with self.assertRaises(ValueError):
                m.run(path, run_native=True, toy_only=True)
        target.mkdir()
        with self.assertRaises(ValueError):
            m.run(target, run_native=True, toy_only=True)
        m.cell.native_once.assert_not_called()


if __name__ == "__main__":
    unittest.main()
