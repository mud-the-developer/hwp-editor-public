"""Synthetic owned worker diagnostics/probe ONLY. No Native, real docs or consent."""

from pathlib import Path
import importlib
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from scripts import hwp_prepare_native as n, hwp_review as r

t = importlib.import_module("hwp_worker_trace")


class WorkerTraceTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.w = Path(self.temp.name) / "owned" / "_review"
        self.w.mkdir(parents=True)
        r.write_json(self.w / "preparing.json", {"SYNTHETIC_ONLY": True})

    def test_bounded_stage_events_and_one_owner_never_reuses_old_trace(self):
        trace = t.WorkerTrace(self.w, 0)
        trace.event("probe")
        data = [
            r.parse_json(line)
            for line in trace.path.read_text(encoding="utf-8").splitlines()
        ]
        self.assertEqual([row["stage"] for row in data], ["entry", "probe"])
        self.assertTrue(all(row["diagnostic_only"] is True for row in data))
        self.assertGreaterEqual(data[1]["elapsed_seconds"], data[0]["elapsed_seconds"])
        with self.assertRaises(FileExistsError):
            t.WorkerTrace(self.w, 0)
        self.assertFalse((self.w / "manifest.json").exists())

    def test_unsafe_or_prepared_trace_rejected(self):
        for phase in (True, 3, -1):
            with self.assertRaises(ValueError):
                t.WorkerTrace(self.w, phase)
        r.write_json(self.w / "manifest.json", {"SYNTHETIC": True})
        with self.assertRaises(ValueError):
            t.WorkerTrace(self.w, 0)

    def test_diagnostic_io_failure_cannot_interrupt_owned_com_cleanup(self):
        trace = t.WorkerTrace(self.w, 0)
        with patch.object(t.os, "fsync", side_effect=OSError("SYNTHETIC DISK FAILURE")):
            trace.event("quit_begin")
        self.assertTrue(trace.failed)
        trace.event("quit_done")
        self.assertFalse((self.w / "approved-plan.json").exists())

    def test_probe_only_runs_path_and_readonly_security_not_exports(self):
        trace = t.WorkerTrace(self.w, 0)
        with (
            patch.object(n, "step_paths") as paths,
            patch.object(n, "registered_security_module") as security,
            patch.object(n, "export_phase") as native,
        ):
            self.assertEqual(n.probe(self.w, 0, trace), 0)
            paths.assert_called_once_with(self.w, 0)
            security.assert_called_once()
            native.assert_not_called()
        self.assertFalse((self.w / "native-import-copy.hwp").exists())
        self.assertFalse((self.w / "baseline.pdf").exists())


if __name__ == "__main__":
    unittest.main()
