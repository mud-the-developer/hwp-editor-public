"""Calibration entry is read-only after synthetic source/preparation. No real COM."""

import importlib
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

m = importlib.import_module("native_plain_cell_probe")


class PlainCellProbeTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name).resolve()
        (self.root / "work").mkdir()
        for patcher in (
            patch.object(m, "ROOT", self.root),
            patch.object(m, "registered_security_module"),
            patch.object(m, "hwp_inventory", return_value=[]),
            patch.object(
                m.cell,
                "native_once",
                side_effect=AssertionError("OFFLINE no Native source"),
            ),
            patch.object(
                m, "observe", side_effect=AssertionError("OFFLINE no Native scan")
            ),
        ):
            patcher.start()
            self.addCleanup(patcher.stop)

    def test_flags_frozen_outside_and_reuse_never_start_source(self):
        target = self.root / "work/toy-cell-scan-SYNTHETIC"
        for kw in ({}, {"run_native": True}, {"run_native": 1, "readonly_scan": True}):
            with self.assertRaises(ValueError):
                m.run(target, **kw)
        with (
            patch.object(m.sys, "frozen", True, create=True),
            self.assertRaises(ValueError),
        ):
            m.run(target, run_native=True, readonly_scan=True)
        for path in (self.root / "outside", self.root / "work/toy-direct-fill-WRONG"):
            with self.assertRaises(ValueError):
                m.run(path, run_native=True, readonly_scan=True)
        target.mkdir()
        with self.assertRaises(ValueError):
            m.run(target, run_native=True, readonly_scan=True)
        m.cell.native_once.assert_not_called()

    def test_failed_source_calibration_stops_without_scan_or_retry(self):
        with patch.object(
            m.cell,
            "native_once",
            side_effect=lambda folder, proof: proof.update(status="blocked"),
        ) as source:
            result = m.run(
                self.root / "work/toy-cell-scan-SYNTHETIC",
                run_native=True,
                readonly_scan=True,
            )
        source.assert_called_once()
        m.observe.assert_not_called()
        self.assertEqual(result["status"], "blocked")
        self.assertEqual(result["field_values_inserted"], 0)
        self.assertEqual(result["candidate_exports"], 0)

    def test_scan_audit_records_bounds_metadata_not_original_text(self):
        raw = SimpleNamespace(
            InitScan=Mock(return_value=True),
            GetText=Mock(return_value=(2, "SYNTHETIC PRIVATE TEXT\r\n")),
            ReleaseScan=Mock(return_value=None),
        )
        events = []
        audit = m.ScanAudit(raw, events)
        self.assertTrue(audit.InitScan(0, 0x55))
        self.assertEqual(audit.GetText(), (2, "SYNTHETIC PRIVATE TEXT\r\n"))
        audit.ReleaseScan()
        self.assertEqual(
            [event["api"] for event in events], ["InitScan", "GetText", "ReleaseScan"]
        )
        self.assertNotIn("SYNTHETIC PRIVATE TEXT", str(events))
        self.assertEqual(events[1]["state"], 2)

    def test_successful_mocked_readonly_scan_has_no_candidate_or_approval_writer(self):
        fixture = importlib.import_module("test_full_fill_harness")
        r = m.r

        def create(directory, proof):
            source = directory / "TOY-ONLY-source.hwp"
            source.write_bytes(b"SYNTHETIC SOURCE")
            proof.update(
                status="metadata_observed_not_writer_qualified",
                owned_process_exit_confirmed=True,
                authored_grid_before_source_save={
                    f"{row},1": [row + 10, 0, 0] for row in (1, 2, 3)
                },
            )

        def prepare(source, workspace):
            workspace.mkdir()
            fixture.package(workspace / "baseline.hwpx")
            fixture.pdf(workspace / "baseline.pdf")
            r.write_json(
                workspace / "manifest.json",
                {
                    "workspace_id": "SYNTHETIC-SCAN",
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

        def observe(source, baseline, selected, report):
            report.update(
                status="readonly_scan_observed_not_writer_qualified",
                binding={
                    "cells": [
                        {"row": row, "col": 1, "position": [row + 10, 0, 0]}
                        for row in (1, 2, 3)
                    ]
                },
            )

        folder = self.root / "work/toy-cell-scan-POSITIVE"
        with (
            patch.object(m.cell, "native_once", side_effect=create),
            patch.object(r, "prepare", side_effect=prepare),
            patch.object(
                m.mapper, "plain_profile", return_value={"SYNTHETIC_ONLY": True}
            ),
            patch.object(m, "observe", side_effect=observe),
        ):
            result = m.run(folder, run_native=True, readonly_scan=True)
        self.assertEqual(
            result["status"], "readonly_scan_observed_not_writer_qualified"
        )
        self.assertFalse(result["qualification"])
        self.assertTrue(result["source_unchanged"])
        self.assertEqual(result["candidate_exports"], 0)
        self.assertFalse((folder / "review/approved-plan.json").exists())
        self.assertFalse((folder / "TOY-ONLY-filled.hwp").exists())


if __name__ == "__main__":
    unittest.main()
