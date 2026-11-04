"""Read-only external runtime detection boundaries; never installs or probes a process."""
import os
from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import hwp_setup as setup
import hwp_setup_runtime as runtime


class RuntimeDetectionTests(unittest.TestCase):
    def test_webview_metadata_rejects_missing_zero_malformed_and_wrong_value_type(self):
        registry = SimpleNamespace(HKEY_LOCAL_MACHINE=1, HKEY_CURRENT_USER=2,
                                   KEY_WOW64_32KEY=32, REG_SZ=1)
        for value, kind in ((None, 1), ("", 1), ("0.0.0.0", 1), ("not-a-runtime", 1),
                            ("156.0.4314", 1), ("156.0.4314.58", 4)):
            with self.subTest(value=value, kind=kind), patch.dict(sys.modules, {"winreg": registry}), patch.object(setup, "_registry_values", return_value=[("pv", value, kind)]), patch("subprocess.Popen", side_effect=AssertionError("Detection cannot launch")):
                self.assertFalse(runtime.component_status({"id": "webview2"})["installed"])

    def test_webview_per_user_runtime_satisfies_missing_machine_runtime(self):
        registry = SimpleNamespace(HKEY_LOCAL_MACHINE=1, HKEY_CURRENT_USER=2,
                                   KEY_WOW64_32KEY=32, REG_SZ=1)
        def values(hive, _key, _view):
            return [("pv", "0.0.0.0" if hive == 1 else "156.0.4314.58", 1)]
        with patch.dict(sys.modules, {"winreg": registry}), patch.object(setup, "_registry_values", side_effect=values), patch("subprocess.Popen", side_effect=AssertionError("Do not probe installed runtime")):
            status = runtime.component_status({"id": "webview2"})
        self.assertTrue(status["installed"])

    def test_explicit_invalid_dotnet_root_blocks_instead_of_installing_elsewhere(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            before = dict(os.environ)
            with patch.dict(os.environ, {"DOTNET_ROOT_X64": str(root)}, clear=True), patch("subprocess.Popen", side_effect=AssertionError("No installer/probe")):
                with self.assertRaises(ValueError):
                    runtime.component_status({"id": "dotnet10"})
            self.assertEqual(dict(os.environ), before)
            self.assertEqual(list(root.iterdir()), [])


if __name__ == "__main__":
    unittest.main()
