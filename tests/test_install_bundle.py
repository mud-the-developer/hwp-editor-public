"""Owned synthetic installer boundaries; no registration or real app install."""

from copy import deepcopy
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest
import uuid
import zipfile

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import hwp_build_source as source
import hwp_install_bundle as bundle

ROOT = Path(__file__).resolve().parents[1]
VERSION = "1.2.3-toy"


class InstallerBundleTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve()
        self.release = self.root / "checked-release"
        self.release.mkdir()
        self.snapshot = self.root / "snapshot"
        (self.snapshot / "packaging").mkdir(parents=True)
        for name in bundle.SCRIPTS:
            (self.snapshot / "packaging" / name).write_bytes((ROOT / "packaging" / name).read_bytes())
        (self.snapshot / "packaging/README-release.txt").write_bytes(b"SYNTHETIC README")
        (self.snapshot / "packaging/AI-INSTALL.md").write_bytes(b"SYNTHETIC AI INSTALL GUIDE")
        (self.snapshot / "packaging/Quick-Start-ko.txt").write_bytes(b"SYNTHETIC QUICK START")
        (self.snapshot / "LICENSE").write_bytes(b"SYNTHETIC LICENSE")
        self.legal = self.root / "notices"
        self.legal.mkdir()
        (self.legal / "TOY-NOTICE.txt").write_bytes(b"SYNTHETIC NOTICE")
        (self.legal / "TOY-SECOND-NOTICE.txt").write_bytes(b"SECOND SYNTHETIC NOTICE")
        (self.release / "HWPReview.exe").write_bytes(b"SYNTHETIC NOT AN EXECUTABLE: NEVER RUN")
        archive = self.release / f"HWPReview-source-{VERSION}.zip"
        archive.write_bytes(b"SYNTHETIC SOURCE ARCHIVE SENTINEL")
        self.build = {
            "version": VERSION, "redistribution_ready": False,
            "executable_sha256": source.sha256(self.release / "HWPReview.exe"),
            "source_archive": {"file": archive.name, "sha256": source.sha256(archive), "bytes": archive.stat().st_size},
            "input_sha256": {
                path.relative_to(self.snapshot).as_posix(): source.sha256(path)
                for path in self.snapshot.rglob("*") if path.is_file()
            },
            "licenses_sha256": {path.name: source.sha256(path) for path in self.legal.iterdir()},
        }
        (self.release / "BUILD.json").write_text(json.dumps(self.build), encoding="utf-8")
        self.manifest = bundle.write_payload(self.release, self.snapshot, self.legal, version=VERSION)
        self.transport()

    def transport(self):
        (self.release / bundle.MANIFEST).write_text(json.dumps(self.manifest), encoding="utf-8")
        members = sorted([*self.manifest["files"], bundle.MANIFEST])
        (self.release / "SHA256SUMS.txt").write_text(
            "".join(source.sha256(self.release / name) + "  " + name + "\n" for name in members),
            encoding="utf-8", newline="\n",
        )
        archive = self.release / f"HWPReview-win-x64-{VERSION}.zip"
        with zipfile.ZipFile(archive, "w") as package:
            for name in [*members, "SHA256SUMS.txt"]:
                package.write(self.release / name, name)
        (self.release / (archive.name + ".sha256")).write_text(
            source.sha256(archive) + "  " + archive.name + "\n", encoding="utf-8", newline="\n",
        )

    def snapshot_bytes(self):
        return {str(path.relative_to(self.root)): path.read_bytes() for path in self.root.rglob("*") if path.is_file()}

    def test_closed_payload_verify_is_read_only_and_includes_exact_source_notices(self):
        before = self.snapshot_bytes()
        self.assertEqual(bundle.verify_payload(self.release, version=VERSION), self.manifest)
        self.assertEqual(self.snapshot_bytes(), before)
        self.assertIn(f"HWPReview-source-{VERSION}.zip", self.manifest["files"])
        self.assertIn("licenses/TOY-NOTICE.txt", self.manifest["files"])
        self.assertFalse(self.manifest["redistribution_ready"])
        self.assertFalse(self.manifest["code_signed"])

    def test_payload_tamper_is_not_accepted_even_with_old_transport(self):
        for name in ("HWPReview.exe", "Install-HWPReview.ps1", "Uninstall-HWPReview.ps1", "licenses/TOY-NOTICE.txt", f"HWPReview-source-{VERSION}.zip"):
            path = self.release / name
            original = path.read_bytes()
            path.write_bytes(original + b"MODIFIED")
            with self.subTest(name=name), self.assertRaises(ValueError):
                bundle.verify_payload(self.release, version=VERSION)
            path.write_bytes(original)

    def test_self_consistently_repacked_root_inputs_must_match_captured_source(self):
        for name in ("Install-HWPReview.cmd", "Install-HWPReview.ps1", "Uninstall-HWPReview.ps1",
                     "Setup-Requirements.cmd", "README.txt", "AI-INSTALL.md", "사용법.txt", "LICENSE"):
            with self.subTest(name=name):
                path = self.release / name
                original = path.read_bytes()
                record = self.manifest["files"][name]
                try:
                    path.write_bytes(original + b"\nALTERED SYNTHETIC INPUT")
                    self.manifest["files"][name] = {"sha256": source.sha256(path), "bytes": path.stat().st_size}
                    self.transport()
                    with self.assertRaisesRegex(ValueError, "captured source"):
                        bundle.verify_payload(self.release, version=VERSION)
                finally:
                    path.write_bytes(original)
                    self.manifest["files"][name] = record
                    self.transport()

    def test_repacked_notice_omission_cannot_reduce_the_build_inventory(self):
        name = "licenses/TOY-NOTICE.txt"
        (self.release / name).unlink()
        del self.manifest["files"][name]
        self.transport()
        with self.assertRaisesRegex(ValueError, "closed payload"):
            bundle.verify_payload(self.release, version=VERSION)

    def test_repacked_private_notice_cannot_enter_the_build_inventory(self):
        name = "licenses/personal.hwpx"
        path = self.release / name
        path.write_bytes(b"PRIVATE SYNTHETIC DOCUMENT: NEVER SHIP")
        self.manifest["files"][name] = {"sha256": source.sha256(path), "bytes": path.stat().st_size}
        self.transport()
        with self.assertRaisesRegex(ValueError, "closed payload"):
            bundle.verify_payload(self.release, version=VERSION)
        self.assertEqual(path.read_bytes(), b"PRIVATE SYNTHETIC DOCUMENT: NEVER SHIP")

    def test_repacked_notice_change_cannot_replace_captured_notice_bytes(self):
        name = "licenses/TOY-NOTICE.txt"
        path = self.release / name
        path.write_bytes(b"SUBSTITUTED SYNTHETIC NOTICE")
        self.manifest["files"][name] = {"sha256": source.sha256(path), "bytes": path.stat().st_size}
        self.transport()
        with self.assertRaisesRegex(ValueError, "captured build"):
            bundle.verify_payload(self.release, version=VERSION)

    def test_extra_user_file_or_directory_is_never_part_of_candidate(self):
        extra = self.release / "personal.hwpx"
        extra.write_bytes(b"KEEP PRIVATE")
        with self.assertRaises(ValueError):
            bundle.verify_payload(self.release, version=VERSION)
        self.assertEqual(extra.read_bytes(), b"KEEP PRIVATE")
        extra.unlink()
        (self.release / "profiles").mkdir()
        with self.assertRaises(ValueError):
            bundle.verify_payload(self.release, version=VERSION)
        self.assertTrue((self.release / "profiles").is_dir())

    def test_repacked_zip_with_extra_member_does_not_pass_rewritten_checksum(self):
        archive = self.release / f"HWPReview-win-x64-{VERSION}.zip"
        with zipfile.ZipFile(archive, "a") as package:
            package.writestr("profiles/private.json", b"NEVER SHIP")
        (self.release / (archive.name + ".sha256")).write_text(source.sha256(archive) + "  " + archive.name + "\n", encoding="utf-8")
        with self.assertRaises(ValueError):
            bundle.verify_payload(self.release, version=VERSION)

    def test_payload_writer_never_overwrites_existing_candidate(self):
        before = self.snapshot_bytes()
        with self.assertRaises(FileExistsError):
            bundle.write_payload(self.release, self.snapshot, self.legal, version=VERSION)
        self.assertEqual(before, self.snapshot_bytes())

    def test_unsafe_names_and_case_collisions_are_rejected(self):
        for names in (["../personal.hwpx"], ["NUL.txt"], ["NOTICE.txt", "notice.txt"], ["NOTICE.txt."], []):
            with self.subTest(names=names), self.assertRaises(ValueError):
                bundle.payload_names(VERSION, names)
        with self.assertRaises(ValueError):
            bundle.payload_names("../outside", ["NOTICE.txt"])

    def test_hardlinked_input_is_rejected_without_modifying_either_name(self):
        original = self.root / "ORIGINAL-TOY.txt"
        alias = self.root / "ALIAS-TOY.txt"
        original.write_bytes(b"KEEP")
        os.link(original, alias)
        with self.assertRaises(ValueError):
            bundle.regular(alias)
        self.assertEqual(original.read_bytes(), b"KEEP")
        self.assertEqual(alias.read_bytes(), b"KEEP")

    @unittest.skipUnless(os.name == "nt", "Windows PowerShell 5.1 read-only entrypoint")
    def test_actual_check_entrypoint_never_executes_toy_exe_or_changes_files_registry(self):
        import winreg

        registry = "Software\\HWPReview\\InstallerSmoke\\" + uuid.uuid4().hex
        command = [
            str(Path(os.environ["SystemRoot"]) / "System32/WindowsPowerShell/v1.0/powershell.exe"),
            "-NoLogo", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", str(self.release / "Install-HWPReview.ps1"),
            "-Operation", "Check", "-SourceDirectory", str(self.release),
            "-InstallDirectory", str(self.root / "app"), "-ShortcutDirectory", str(self.root / "start-menu"),
            "-RegistrySubKey", registry, "-LogDirectory", str(self.root / "logs"),
        ]
        before = self.snapshot_bytes()
        completed = subprocess.run(command, capture_output=True, timeout=90, check=False)
        self.assertEqual(completed.returncode, 0, completed.stderr.decode(errors="replace"))
        result = json.loads(completed.stdout.decode("utf-8-sig", errors="replace"))
        self.assertEqual(result["contract"], "hwp-install-operation-v1")
        self.assertEqual(result["status"], "payload_valid_no_mutation")
        self.assertFalse(result["prerequisites_checked"])
        self.assertFalse(result["app_launched"])
        self.assertIsNone(result["log_directory"])
        self.assertEqual(before, self.snapshot_bytes())
        self.assertFalse((self.root / "app").exists())
        self.assertFalse((self.root / "start-menu").exists())
        self.assertFalse((self.root / "logs").exists())
        with self.assertRaises(FileNotFoundError):
            winreg.OpenKey(winreg.HKEY_CURRENT_USER, registry)
        # A colliding user folder is rejected without a repair, log or app execution.
        (self.root / "app").mkdir()
        marker = self.root / "app/PRIVATE.hwpx"
        marker.write_bytes(b"KEEP USER DOCUMENT")
        before = self.snapshot_bytes()
        completed = subprocess.run(command, capture_output=True, timeout=90, check=False)
        self.assertEqual(completed.returncode, 3)
        self.assertEqual(before, self.snapshot_bytes())
        self.assertFalse((self.root / "logs").exists())

    @unittest.skipUnless(os.name == "nt", "Windows case-insensitive directory admission")
    def test_actual_check_accepts_case_variation_without_touching_the_directory(self):
        command = [
            str(Path(os.environ["SystemRoot"]) / "System32/WindowsPowerShell/v1.0/powershell.exe"),
            "-NoLogo", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", str(self.release / "Install-HWPReview.ps1"),
            "-Operation", "Check", "-SourceDirectory", str(self.release).swapcase(),
            "-InstallDirectory", str(self.root / "app"), "-ShortcutDirectory", str(self.root / "start-menu"),
            "-RegistrySubKey", "Software\\HWPReview\\InstallerSmoke\\" + uuid.uuid4().hex,
            "-LogDirectory", str(self.root / "logs"),
        ]
        before = self.snapshot_bytes()
        completed = subprocess.run(command, capture_output=True, timeout=90, check=False)
        self.assertEqual(completed.returncode, 0, completed.stdout.decode("utf-8-sig", errors="replace"))
        self.assertEqual(before, self.snapshot_bytes())
        self.assertFalse((self.root / "logs").exists())

    @unittest.skipUnless(os.name == "nt", "Windows unpacked-kit default directory")
    def test_actual_check_uses_the_unpacked_script_directory_when_source_is_omitted(self):
        command = [
            str(Path(os.environ["SystemRoot"]) / "System32/WindowsPowerShell/v1.0/powershell.exe"),
            "-NoLogo", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", str(self.release / "Install-HWPReview.ps1"),
            "-Operation", "Check", "-InstallDirectory", str(self.root / "app"),
            "-ShortcutDirectory", str(self.root / "start-menu"),
            "-RegistrySubKey", "Software\\HWPReview\\InstallerSmoke\\" + uuid.uuid4().hex,
            "-LogDirectory", str(self.root / "logs"),
        ]
        before = self.snapshot_bytes()
        completed = subprocess.run(command, capture_output=True, timeout=90, check=False)
        self.assertEqual(completed.returncode, 0, completed.stdout.decode("utf-8-sig", errors="replace"))
        self.assertEqual(before, self.snapshot_bytes())
        self.assertFalse((self.root / "logs").exists())

    @unittest.skipUnless(os.name == "nt", "Windows installed uninstaller default directory")
    def test_uninstaller_default_preserves_changed_engine_without_executing_it(self):
        marker = self.root / "UNTRUSTED-ENGINE-EXECUTED.txt"
        (self.release / "Install-HWPReview.ps1").write_text(
            "Set-Content -LiteralPath '" + str(marker).replace("'", "''") + "' -Value 'NEVER RUN'\n",
            encoding="utf-8",
        )
        receipt = {
            "contract": "hwp-user-install-receipt-v2",
            "install_directory": str(self.release),
            "log_directory": str(self.root / "logs"),
            "manifest_sha256": source.sha256(self.release / bundle.MANIFEST),
        }
        (self.release / "INSTALL-RECEIPT.json").write_text(json.dumps(receipt), encoding="utf-8")
        before = self.snapshot_bytes()
        command = [
            str(Path(os.environ["SystemRoot"]) / "System32/WindowsPowerShell/v1.0/powershell.exe"),
            "-NoLogo", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", str(self.release / "Uninstall-HWPReview.ps1"),
            "-LogDirectory", str(self.root / "logs"),
        ]
        completed = subprocess.run(command, capture_output=True, timeout=90, check=False)
        result = json.loads(completed.stdout.decode("utf-8-sig", errors="replace"))
        self.assertEqual(completed.returncode, 3)
        self.assertEqual(result["install_directory"], str(self.release))
        self.assertEqual(result["receipt"], str(self.release / "INSTALL-RECEIPT.json"))
        self.assertFalse(marker.exists())
        self.assertEqual(before, self.snapshot_bytes())
        self.assertFalse((self.root / "logs").exists())

    @unittest.skipUnless(os.name == "nt", "Windows PowerShell actual process I/O contract")
    def test_process_result_keeps_one_integer_exit_and_both_diagnostic_streams(self):
        child = self.root / "bounded-child"
        child.mkdir()
        shutil.copyfile(Path(os.environ["SystemRoot"]) / "System32/cmd.exe", child / "HWPReview.exe")
        run = self.root / "process-logs"
        run.mkdir()
        probe = self.root / "process-contract.ps1"
        probe.write_text(
            """param([string]$Engine,[string]$Child,[string]$Run)
Set-StrictMode -Version Latest
$ErrorActionPreference='Stop'
[Console]::OutputEncoding=New-Object Text.UTF8Encoding($false)
$script:ExitCode=1
$script:DirectoryLocks=@{}
$script:Run=$Run
$SourceDirectory=$Child
$tokens=$null; $errors=$null
$ast=[Management.Automation.Language.Parser]::ParseFile($Engine,[ref]$tokens,[ref]$errors)
$allowed=@('Stop-Install','Safe-Path','Initialize-FileInspection','Assert-HandlePath','Protect-DirectoryPath','Open-Regular','Run-Exe')
foreach($node in $ast.FindAll({param($n) $n -is [Management.Automation.Language.FunctionDefinitionAst]},$false)){
    if($allowed -contains $node.Name){. ([scriptblock]::Create($node.Extent.Text))}
}
try{
    Initialize-FileInspection
    $code=Run-Exe '/d /c "(echo HWP_CHILD_STDOUT)&(echo HWP_CHILD_STDERR 1>&2)&exit /b 7"' 'child'
    [ordered]@{exit_code=$code;is_integer=($code -is [int]);stdout=[IO.File]::ReadAllText((Join-Path $Run 'child.stdout.txt')).Trim();stderr=[IO.File]::ReadAllText((Join-Path $Run 'child.stderr.txt')).Trim()} | ConvertTo-Json -Depth 8
}finally{foreach($handle in $script:DirectoryLocks.Values){$handle.Dispose()}}
""", encoding="utf-8-sig",
        )
        command = [
            str(Path(os.environ["SystemRoot"]) / "System32/WindowsPowerShell/v1.0/powershell.exe"),
            "-NoLogo", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", str(probe),
            "-Engine", str(self.release / "Install-HWPReview.ps1"), "-Child", str(child), "-Run", str(run),
        ]
        completed = subprocess.run(command, capture_output=True, timeout=90, check=False)
        self.assertEqual(completed.returncode, 0, completed.stderr.decode(errors="replace"))
        result = json.loads(completed.stdout.decode("utf-8-sig", errors="replace"))
        self.assertTrue(result["is_integer"], result)
        self.assertEqual(result["exit_code"], 7)
        self.assertEqual(result["stdout"], "HWP_CHILD_STDOUT")
        self.assertEqual(result["stderr"], "HWP_CHILD_STDERR")


@unittest.skipUnless(os.name == "nt", "Windows PowerShell requirements consumer")
class RequirementsCheckTests(unittest.TestCase):
    DLLS = ("msvcp140.dll", "vcruntime140.dll", "vcruntime140_1.dll")
    MINIMUM = "14.51.36247.0"

    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name).resolve()
        self.report = {
            "contract": "hwp-requirements-status-v2", "ready": True, "reboot_required": False,
            "components": {
                identifier: {"required": True, "installed": True, "detail": "INERT inspected metadata"}
                for identifier in ("powershell7", "d2coding_propo", "dotnet10", "webview2",
                                   "omp_runtime", "hwp_security")
            },
        }

    def consume(self, cases, runtime_cases=None):
        if runtime_cases is None:
            runtime_cases = [{"registry": self.MINIMUM, "dlls": dict.fromkeys(self.DLLS, self.MINIMUM)}] * len(cases)
        inputs = []
        for index, ((report, code), runtime) in enumerate(zip(cases, runtime_cases, strict=True)):
            tag = f"probe-{index}"
            path = self.root / (f"fixture-{index}.json")
            path.write_text(json.dumps(report), encoding="utf-8")
            inputs.append({"tag": tag, "code": code, "fixture": str(path), "runtime": runtime})
        case_path = self.root / "cases.json"
        case_path.write_text(json.dumps(inputs), encoding="utf-8")
        probe = self.root / "consume-requirements.ps1"
        probe.write_text(
            r"""param([string]$Engine,[string]$Run,[string]$Cases)
Set-StrictMode -Version Latest
$ErrorActionPreference='Stop'
[Console]::OutputEncoding=New-Object Text.UTF8Encoding($false)
$script:Run=$Run
function Read-VCX64RegistryVersion {
    if ($null -ne $script:Runtime.registry) { return [version]$script:Runtime.registry }
}
function Read-VCX64SystemDllVersion([string]$Name) {
    $property = $script:Runtime.dlls.PSObject.Properties[$Name]
    if ($null -eq $property -or $null -eq $property.Value) { return $null }
    if ($property.Value -eq 'DENIED') { throw 'inert fixture read failure' }
    return [version]$property.Value
}
function Run-Exe([string]$Arguments,[string]$Tag){
    $script:Launched = $true
    [IO.File]::Copy($script:FixtureReport,(Join-Path $script:Run ($Tag + '.json')))
    return $script:RequestedCode
}
function Read-Json([string]$Path){return ([IO.File]::ReadAllText($Path) | ConvertFrom-Json)}
$tokens=$null; $errors=$null
$ast=[Management.Automation.Language.Parser]::ParseFile($Engine,[ref]$tokens,[ref]$errors)
if($errors.Count -ne 0){throw 'The actual installer script could not be parsed.'}
foreach($node in $ast.FindAll({param($n) $n -is [Management.Automation.Language.FunctionDefinitionAst]},$false)){
    if($node.Name -in @('Stop-Install','Assert-ExternalVCRuntime','Requirements-Check')){. ([scriptblock]::Create($node.Extent.Text))}
}
$answers=New-Object 'Collections.Generic.List[object]'
foreach($case in (Read-Json $Cases)){
    $script:Runtime = $case.runtime
    $script:Launched = $false
    $script:RequestedCode=[int]$case.code
    $script:FixtureReport=[string]$case.fixture
    $script:ExitCode=1
    $result=[ordered]@{prerequisites_checked=$false}
    try{
        $ready=Requirements-Check $case.tag
        $answers.Add([ordered]@{accepted=$true;ready=$ready;prerequisites_checked=$result.prerequisites_checked;exit_code=0;launched=$script:Launched})
    }catch{
        $answers.Add([ordered]@{accepted=$false;ready=$false;prerequisites_checked=$result.prerequisites_checked;exit_code=$script:ExitCode;detail=$_.Exception.Message;launched=$script:Launched})
    }
}
ConvertTo-Json -InputObject @($answers.ToArray()) -Depth 8
""", encoding="utf-8-sig",
        )
        before = {path.name: path.read_bytes() for path in self.root.iterdir()}
        command = [
            str(Path(os.environ["SystemRoot"]) / "System32/WindowsPowerShell/v1.0/powershell.exe"),
            "-NoLogo", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", str(probe),
            "-Engine", str(ROOT / "packaging/Install-HWPReview.ps1"),
            "-Run", str(self.root), "-Cases", str(case_path),
        ]
        completed = subprocess.run(command, capture_output=True, timeout=90, check=False)
        self.assertEqual(completed.returncode, 0, completed.stderr.decode(errors="replace"))
        self.assertEqual(before, {name: (self.root / name).read_bytes() for name in before})
        results = json.loads(completed.stdout.decode("utf-8-sig", errors="replace"))
        self.assertEqual({path.name for path in self.root.iterdir()}, set(before) | {
            case["tag"] + ".json" for case, result in zip(inputs, results, strict=True) if result["launched"]
        })
        return results

    def test_runtime_evidence_must_be_current_before_launching_any_exe(self):
        ready = {"registry": self.MINIMUM, "dlls": dict.fromkeys(self.DLLS, self.MINIMUM)}
        missing_registry = {**ready, "registry": None}
        old_registry = {**ready, "registry": "14.51.36246.65535"}
        missing_dll = {**ready, "dlls": {name: self.MINIMUM for name in self.DLLS[:-1]}}
        old_dll = {**ready, "dlls": {**ready["dlls"], self.DLLS[0]: "14.50.65535.0"}}
        denied_dll = {**ready, "dlls": {**ready["dlls"], self.DLLS[1]: "DENIED"}}
        newer = {"registry": "14.52.0.0", "dlls": dict.fromkeys(self.DLLS, "14.52.0.0")}
        runtime = [ready, newer, missing_registry, old_registry, missing_dll, old_dll, denied_dll]
        results = self.consume([(self.report, 0)] * len(runtime), runtime)
        self.assertTrue(all(result["accepted"] and result["ready"] and result["launched"]
                            for result in results[:2]))
        for result in results[2:]:
            self.assertEqual(result["exit_code"], 5)
            self.assertFalse(result["accepted"] or result["launched"] or result["prerequisites_checked"])
            self.assertIn(self.MINIMUM, result["detail"])
            self.assertIn("https://aka.ms/vc14/vc_redist.x64.exe", result["detail"])

    def test_actual_consumer_calculates_readiness_from_applicable_requirements(self):
        optional = deepcopy(self.report)
        optional["components"]["hwp_security"].update(required=False, installed=False)
        missing = deepcopy(self.report)
        missing["components"]["hwp_security"]["installed"] = False
        missing["ready"] = False
        other_missing = deepcopy(optional)
        other_missing["components"]["powershell7"]["installed"] = False
        other_missing["ready"] = False
        reports = [self.report, optional, missing, other_missing]
        results = self.consume([(report, 0 if report["ready"] else 2) for report in reports])
        self.assertEqual([result["ready"] for result in results], [True, True, False, False])
        self.assertTrue(all(result["accepted"] and result["prerequisites_checked"] for result in results))

    def test_actual_consumer_rejects_incomplete_or_untrusted_required_fields(self):
        reports = []
        legacy = deepcopy(self.report)
        legacy["contract"] = "hwp-requirements-status-v1"
        reports.append(legacy)
        missing = deepcopy(self.report)
        del missing["components"]["hwp_security"]
        reports.append(missing)
        extra = deepcopy(self.report)
        extra["components"]["unexpected"] = deepcopy(extra["components"]["hwp_security"])
        reports.append(extra)
        for field in ("required", "installed", "detail"):
            missing_field = deepcopy(self.report)
            del missing_field["components"]["hwp_security"][field]
            reports.append(missing_field)
        for identifier, field, value in (
                ("hwp_security", "required", 0), ("hwp_security", "required", "false"),
                ("hwp_security", "installed", 1), ("hwp_security", "installed", "true"),
                ("hwp_security", "detail", None), ("powershell7", "required", False)):
            changed = deepcopy(self.report)
            changed["components"][identifier][field] = value
            reports.append(changed)
        false_installed = deepcopy(self.report)
        false_installed["components"]["hwp_security"]["required"] = False
        reports.append(false_installed)
        results = self.consume([(report, 0) for report in reports])
        self.assertTrue(all(not result["accepted"] and not result["prerequisites_checked"]
                            and result["exit_code"] == 5 for result in results), results)

    def test_actual_consumer_rejects_claimed_ready_or_exit_code_disagreement(self):
        false_ready = deepcopy(self.report)
        false_ready["components"]["hwp_security"]["installed"] = False
        false_not_ready = deepcopy(self.report)
        false_not_ready["ready"] = False
        results = self.consume([(false_ready, 0), (false_not_ready, 2), (self.report, 2)])
        self.assertTrue(all(not result["accepted"] and result["exit_code"] == 5
                            for result in results), results)


if __name__ == "__main__":
    unittest.main()
