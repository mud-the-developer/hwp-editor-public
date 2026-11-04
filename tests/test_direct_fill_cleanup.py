"""Future direct-probe cleanup metadata follows owned exit evidence; no Native."""

import importlib
from pathlib import Path
from types import SimpleNamespace
import sys
import tempfile
import unittest
from unittest.mock import Mock, patch

m = importlib.import_module("native_direct_fill_probe")


class DirectCleanupTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name).resolve()
        self.source = self.root / "OWN-SYNTHETIC.hwp"
        self.source.write_bytes(b"SYNTHETIC")
        self.app = SimpleNamespace(
            Open=Mock(return_value=True), Clear=Mock(), Quit=Mock()
        )
        self.kernel = SimpleNamespace(
            OpenProcess=Mock(return_value=1),
            WaitForSingleObject=Mock(return_value=0),
            CloseHandle=Mock(),
        )
        self.com = SimpleNamespace(CoInitialize=Mock(), CoUninitialize=Mock())
        client = SimpleNamespace(
            DispatchEx=Mock(return_value=self.app),
            gencache=SimpleNamespace(EnsureDispatch=lambda app: app),
        )

        def own(app, before, started, report):
            report.update(
                cleanup="ownership_unconfirmed_owner_inspection_required",
                owned_hwp_pid=1234,
                owned_process_creation_ms=100,
            )
            return SimpleNamespace(Visible=True)

        for patcher in (
            patch.dict(
                sys.modules,
                {
                    "pythoncom": self.com,
                    "win32com": SimpleNamespace(client=client),
                    "win32com.client": client,
                },
            ),
            patch.object(
                m,
                "require_binding",
                return_value={
                    key: [i + 2, 0, 0] for i, key in enumerate(("1,1", "2,1", "3,1"))
                },
            ),
            patch.object(m, "hwp_inventory", return_value=[]),
            patch.object(m.fixture, "verify_owned_blank", side_effect=own),
            patch.object(m.ctypes, "WinDLL", return_value=self.kernel),
            patch.object(m, "register_security_module"),
            patch.object(
                m.cell,
                "observe_saved",
                side_effect=ValueError("SYNTHETIC stop before any write"),
            ),
        ):
            patcher.start()
            self.addCleanup(patcher.stop)

    def test_confirmed_owned_exit_overwrites_only_future_in_memory_initial_label(self):
        report = {}
        with self.assertRaisesRegex(ValueError, "SYNTHETIC"):
            m.export_direct(self.source, {}, [], self.root, report)
        self.assertTrue(report["direct_owned_exit_confirmed"])
        self.assertEqual(report["cleanup"], "owned_exit_confirmed")
        self.app.Clear.assert_called_once()
        self.app.Quit.assert_called_once()
        self.kernel.CloseHandle.assert_called_once()
        self.com.CoUninitialize.assert_called_once()

    def test_unconfirmed_exit_stays_blocking_and_never_claims_clean(self):
        self.kernel.WaitForSingleObject.return_value = 258
        report = {}
        with self.assertRaisesRegex(RuntimeError, "exit unconfirmed"):
            m.export_direct(self.source, {}, [], self.root, report)
        self.assertFalse(report["direct_owned_exit_confirmed"])
        self.assertEqual(
            report["cleanup"], "owned_exit_unconfirmed_owner_inspection_required"
        )
        self.kernel.CloseHandle.assert_called_once()


if __name__ == "__main__":
    unittest.main()
