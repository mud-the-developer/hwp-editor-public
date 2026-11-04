"""Real receipt-loader regressions on owned files only; never install/register an app."""

import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest
import uuid

ROOT = Path(__file__).resolve().parents[1]
POWERSHELL = shutil.which("powershell.exe")


def digest(data):
    return hashlib.sha256(data).hexdigest()


@unittest.skipUnless(os.name == "nt" and POWERSHELL, "Windows PowerShell 5.1 required")
class InstallReceiptPathTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve()
        self.install = self.root / "Owned App"
        self.install.mkdir()
        self.logs = self.root / "Custom Run Logs"
        self.shortcut = self.root / "Owned Shortcuts"
        self.registry = "Software\\HWPReview\\InstallerSmoke\\" + uuid.uuid4().hex
        names = ["HWPReview.exe", "BUILD.json", "README.txt", "AI-INSTALL.md", "사용법.txt", "LICENSE",
                 "Install-HWPReview.cmd", "Install-HWPReview.ps1", "Uninstall-HWPReview.ps1",
                 "Setup-Requirements.cmd", "HWPReview-source-1.2.3.zip", "licenses/NOTICE.txt"]
        manifest = {"contract": "hwp-install-payload-v1", "version": "1.2.3",
                    "architecture": "x64", "redistribution_ready": False, "code_signed": False,
                    "files": {name: {"sha256": digest(b"owned"), "bytes": 5} for name in names}}
        manifest_bytes = json.dumps(manifest).encode()
        (self.install / "INSTALL-MANIFEST.json").write_bytes(manifest_bytes)
        self.receipt = {"contract": "hwp-user-install-receipt-v2", "install_id": uuid.uuid4().hex,
                        "version": "1.2.3", "install_directory": str(self.install),
                        "log_directory": str(self.logs), "manifest_sha256": digest(manifest_bytes),
                        "shortcut_directory": str(self.shortcut), "shortcut_sha256": digest(b"link"),
                        "shortcut_bytes": 4, "registry_subkey": self.registry,
                        "app_launch_enabled": False}
        # Retain the actual shipped function bodies and initialization, not copies or mocks.
        text = (ROOT / "packaging/Install-HWPReview.ps1").read_text(encoding="utf-8-sig")
        self.preamble = text.rsplit("\ntry {", 1)[0]

    def invoke_loader(self, *, requested=None, explicit=False, log=None):
        (self.install / "INSTALL-RECEIPT.json").write_text(json.dumps(self.receipt), encoding="utf-8")
        config = {"install": str(requested or self.install), "explicit": explicit,
                  "log": str(log or (self.install / "Old Default Inside Install"))}
        config_path = self.root / "config.json"
        config_path.write_text(json.dumps(config), encoding="utf-8")
        driver = r'''
try {
    $config = Get-Content -LiteralPath (Join-Path $PSScriptRoot 'config.json') -Raw | ConvertFrom-Json
    $InstallDirectory = Safe-Path $config.install
    $LogDirectory = $config.log
    $script:LogDirectoryExplicit = $config.explicit
    Initialize-FileInspection
    $owned = Load-Receipt
    if (Registry-Exists) { throw 'Synthetic smoke registry key must be absent.' }
    New-Run
    [ordered]@{ok=$true; install=$InstallDirectory; log=$LogDirectory; run=$script:Run; registry=$owned.registry} | ConvertTo-Json -Depth 8
} catch {
    [ordered]@{ok=$false; detail=$_.Exception.Message} | ConvertTo-Json
} finally {
    foreach ($lock in $script:Locks) { $lock.Dispose() }
    foreach ($lock in $script:OwnershipLocks) { $lock.Dispose() }
    foreach ($handle in $script:DirectoryLocks.Values) { $handle.Dispose() }
}
'''
        harness = self.root / "load-owned-receipt.ps1"
        harness.write_text(self.preamble + driver, encoding="utf-8-sig")
        completed = subprocess.run([POWERSHELL, "-NoLogo", "-NoProfile", "-NonInteractive",
                                    "-ExecutionPolicy", "Bypass", "-File", str(harness)],
                                   capture_output=True, text=True, encoding="utf-8", timeout=30)
        self.assertEqual(completed.returncode, 0, completed.stderr)
        return json.loads(completed.stdout)

    def test_case_alias_uses_canonical_registry_paths_and_receipted_custom_logs(self):
        first = self.invoke_loader(requested=str(self.install).swapcase())
        self.assertTrue(first["ok"], first)
        self.assertEqual(first["install"], str(self.install))
        self.assertEqual(first["registry"]["InstallLocation"], str(self.install))
        self.assertEqual(first["registry"]["DisplayIcon"], str(self.install / "HWPReview.exe"))
        self.assertEqual(first["log"], str(self.logs))
        self.assertTrue(Path(first["run"]).is_dir())
        second = self.invoke_loader()
        self.assertTrue(second["ok"], second)
        self.assertNotEqual(first["run"], second["run"])
        self.assertEqual(first["registry"], second["registry"])

    def test_explicit_log_override_does_not_change_registered_ownership(self):
        override = self.root / "Explicit Logs"
        result = self.invoke_loader(explicit=True, log=override)
        self.assertTrue(result["ok"], result)
        self.assertEqual(result["log"], str(override))
        self.assertEqual(Path(result["run"]).parent, override)
        self.assertFalse(self.logs.exists())

    def test_foreign_install_directory_is_rejected_before_creating_logs(self):
        self.receipt["install_directory"] = str(self.root / "Unrelated")
        self.assertFalse(self.invoke_loader()["ok"])
        self.assertFalse(self.logs.exists())

    def test_v1_missing_invalid_or_overlapping_log_config_has_no_fallback(self):
        original = dict(self.receipt)
        variants = [{"contract": "hwp-user-install-receipt-v1"}, {"log_directory": None},
                    {"log_directory": "relative"}, {"log_directory": str(self.install / "Inside")},
                    {"log_directory": str(self.shortcut / "Inside")}]
        for changes in variants:
            with self.subTest(changes=changes):
                self.receipt = {**original, **changes}
                self.assertFalse(self.invoke_loader()["ok"])
                self.assertFalse(self.logs.exists())
        self.receipt = dict(original)
        del self.receipt["log_directory"]
        self.assertFalse(self.invoke_loader()["ok"])
        self.assertFalse(self.logs.exists())

    def test_explicit_overlapping_logs_remain_rejected(self):
        self.assertFalse(self.invoke_loader(explicit=True, log=self.install / "Inside")["ok"])
        self.assertFalse(self.logs.exists())


if __name__ == "__main__":
    unittest.main()
