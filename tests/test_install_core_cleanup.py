"""Exercise production core deletion helpers on disposable files, never install."""
import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
POWERSHELL = shutil.which("powershell.exe")


@unittest.skipUnless(os.name == "nt" and POWERSHELL, "Windows PowerShell 5.1 required")
class CoreCleanupTests(unittest.TestCase):
    def run_case(self, mode):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            driver = root / "driver.ps1"
            driver.write_text(r'''
param($Source, $Root, $Mode)
$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest
$tokens = $null; $errors = $null
$ast = [Management.Automation.Language.Parser]::ParseFile($Source, [ref]$tokens, [ref]$errors)
if ($errors.Count) { throw 'Source parse failed' }
# Import declarations only; never run installer top-level code.
foreach ($statement in $ast.EndBlock.Statements) {
    if ($statement -is [Management.Automation.Language.FunctionDefinitionAst]) {
        . ([scriptblock]::Create($statement.Extent.Text))
    }
}
$script:Retained = New-Object 'Collections.Generic.List[string]'
$script:DirectoryLocks = @{}
$script:OwnershipLocks = New-Object 'Collections.Generic.List[IDisposable]'
$script:WrapperGuard = $null
Initialize-FileInspection
$core = [ordered]@{}
$names = @('Install-HWPReview.ps1', 'Uninstall-HWPReview.ps1', 'INSTALL-MANIFEST.json', 'INSTALL-RECEIPT.json')
$held = $null
try {
    foreach ($name in $names) {
        $path = Join-Path $Root $name
        [IO.File]::WriteAllText($path, ('SYNTHETIC ' + $name))
        $core[$path] = @{ bytes=([IO.FileInfo]$path).Length; sha256=(File-Hash $path) }
    }
    $target = Join-Path $Root 'INSTALL-RECEIPT.json'
    if ($Mode -eq 'readonly_manifest') { $target = Join-Path $Root 'INSTALL-MANIFEST.json' }
    if ($Mode -like 'readonly_*') { [IO.File]::SetAttributes($target, [IO.FileAttributes]::ReadOnly) }
    if ($Mode -eq 'changed') { [IO.File]::WriteAllText($target, 'FOREIGN CHANGED BYTES') }
    if ($Mode -eq 'locked') { $held = [IO.File]::Open($target, [IO.FileMode]::Open, [IO.FileAccess]::Read, [IO.FileShare]::Read) }
    if ($Mode -eq 'nonregular') { [IO.File]::Delete($target); $null = [IO.Directory]::CreateDirectory($target) }
    # Existing ownership readers must be released before acquiring DELETE handles.
    foreach ($name in $names[0..2]) { $script:OwnershipLocks.Add((Open-Regular (Join-Path $Root $name))) }
    Remove-ExactCore $core
    $remaining = @($names | Where-Object { Test-Path -LiteralPath (Join-Path $Root $_) })
    $foreign = if ($Mode -eq 'changed') { [IO.File]::ReadAllText($target) } else { '' }
    $readonly = if ($Mode -like 'readonly_*') { ([IO.File]::GetAttributes($target) -band [IO.FileAttributes]::ReadOnly) -ne 0 } else { $false }
    @{remaining=$remaining; retained=@($script:Retained.ToArray()); foreign=$foreign; readonly=$readonly} | ConvertTo-Json -Compress
} finally {
    if ($null -ne $held) { $held.Dispose() }
    foreach ($stream in $script:OwnershipLocks) { $stream.Dispose() }
    foreach ($handle in $script:DirectoryLocks.Values) { $handle.Dispose() }
    # Only restore attributes on the fresh owned test files for temporary cleanup.
    foreach ($name in $names) {
        $path = Join-Path $Root $name
        if ([IO.File]::Exists($path)) { [IO.File]::SetAttributes($path, [IO.FileAttributes]::Normal) }
    }
}
''', encoding="utf-8-sig")
            completed = subprocess.run(
                [POWERSHELL, "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass",
                 "-File", str(driver), "-Source", str(ROOT / "packaging/Install-HWPReview.ps1"),
                 "-Root", str(root), "-Mode", mode], capture_output=True, text=True, timeout=30,
            )
            self.assertEqual(completed.returncode, 0, completed.stdout + completed.stderr)
            return json.loads(completed.stdout)

    def test_exact_core_removes_all_four(self):
        result = self.run_case("ordinary")
        self.assertEqual(result["remaining"], [])
        self.assertEqual(result["retained"], [])

    def test_preflight_failure_preserves_entire_core(self):
        expected = {"Install-HWPReview.ps1", "Uninstall-HWPReview.ps1", "INSTALL-MANIFEST.json", "INSTALL-RECEIPT.json"}
        for mode in ("readonly_receipt", "readonly_manifest", "changed", "locked", "nonregular"):
            with self.subTest(mode=mode):
                result = self.run_case(mode)
                self.assertEqual(set(result["remaining"]), expected)
                self.assertEqual({Path(path).name for path in result["retained"]}, expected)
                if mode.startswith("readonly_"):
                    self.assertTrue(result["readonly"])
                if mode == "changed":
                    self.assertEqual(result["foreign"], "FOREIGN CHANGED BYTES")
