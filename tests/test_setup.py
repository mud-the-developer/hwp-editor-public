"""Inert consumer boundaries for required startup preparation; no OS installation."""
from copy import deepcopy
import hashlib
import io
import json
from pathlib import Path
import stat
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch
import zipfile

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import hwp_setup as setup


class Response(io.BytesIO):
    def __init__(self, data, url, *, length=None):
        super().__init__(data)
        self.status = 200
        self.headers = {"Content-Length": str(len(data) if length is None else length)}
        self.url = url

    def geturl(self):
        return self.url


class SetupTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name).resolve()
        self.runs = self.root / "runs"
        self.local = self.root / "local"
        self.assets = deepcopy(setup._ASSETS)
        self.pin("resource_root", return_value=self.root)
        self.pin("_ASSETS", self.assets)
        self.pin("_windows_x64", return_value=True)
        self.local_lookup = self.pin("_local_app_data", return_value=self.local)
        self.present = {"dotnet10", "webview2", "omp_runtime"}
        self.status = self.pin("_component_status", side_effect=self.component_status)
        self.pin("_check_font_registry_absent", return_value=None)
        self.pin("_set_font_registry", return_value=None)
        self.pin("_add_font", return_value=None)
        self.pin("_font_change", return_value=None)
        self.process = self.pin("subprocess.Popen", side_effect=AssertionError("No real process execution"), module=setup)
        self.network = self.pin("urllib.request.build_opener", side_effect=AssertionError("No real network"), module=setup)
        self.fonts = {
            "D2KodingLigatureNerdFontPropo-Regular.ttf": b"INERT regular font bytes; never a loadable font",
            "D2KodingLigatureNerdFontPropo-Bold.ttf": b"INERT bold font bytes; never a loadable font",
        }
        self.notices = {"LICENSE": b"INERT original archive notice\r\n"}
        font_rows = [self.member(name, data, registry_name="D2KodingLigature Nerd Font Propo"
                                 + (" Bold" if "Bold" in name else "") + " (TrueType)")
                     for name, data in self.fonts.items()]
        self.manifest = {"contract": setup.CONTRACT, "components": [
            {"id": "powershell7", **self.assets["powershell7"],
             "license_files": ["packaging/licenses/PowerShell-7.6.9-LICENSE.txt"]},
            {"id": "d2coding_propo", **self.assets["d2coding_propo"],
             "license_files": ["packaging/licenses/D2Coding-Nerd-Font-3.5.1-LICENSE.txt"],
             "font_family": "D2KodingLigature Nerd Font Propo", "font_files": font_rows,
             "notice_files": [self.member(name, data) for name, data in self.notices.items()]},
        ]}
        for identifier in ("dotnet10", "webview2", "omp_runtime"):
            self.manifest["components"].append({
                "id": identifier, **self.assets[identifier],
                "license_files": [f"packaging/licenses/{identifier}-INERT-LICENSE.txt"],
            })
        self.manifest["license_files_sha256"] = {}
        for component in self.manifest["components"]:
            for name in component["license_files"]:
                path = self.root / name
                path.parent.mkdir(parents=True, exist_ok=True)
                raw = ("INERT bundled original notice: " + name).encode("utf-8")
                path.write_bytes(raw)
                self.manifest["license_files_sha256"][name] = hashlib.sha256(raw).hexdigest()
        self.msi = b"INERT MSI fixture: not executable"
        self.bind_payload("powershell7", self.msi)
        self.archive = self.zip_bytes({**self.fonts, **self.notices,
                                      "D2CodingNerdFontMono-Regular.ttf": b"INERT never install Mono"})
        self.bind_payload("d2coding_propo", self.archive)
        (self.root / "packaging/windows-dependencies.json").write_text(json.dumps(self.manifest), encoding="utf-8")

    def pin(self, name, value=None, *, module=setup, **kwargs):
        if "." in name:
            target, attribute = name.rsplit(".", 1)
            current = module
            for segment in target.split("."):
                current = getattr(current, segment)
        else:
            current, attribute = module, name
        replacement = patch.object(current, attribute, value, **kwargs) if value is not None else patch.object(current, attribute, **kwargs)
        mocked = replacement.start()
        self.addCleanup(replacement.stop)
        return mocked

    @staticmethod
    def member(name, data, **extra):
        return {"member": name, "size": len(data), "sha256": hashlib.sha256(data).hexdigest(), **extra}

    @staticmethod
    def zip_bytes(entries):
        output = io.BytesIO()
        with zipfile.ZipFile(output, "w", compression=zipfile.ZIP_STORED) as archive:
            for name, value in entries.items():
                archive.writestr(name, value)
        return output.getvalue()

    def component(self, identifier):
        return next(row for row in self.manifest["components"] if row["id"] == identifier)

    def component_status(self, component):
        present = component["id"] in self.present
        return {"installed": present, "detail": "synthetic existing metadata; preserve" if present else "synthetic absent metadata"}

    def bind_payload(self, identifier, data):
        updates = {"size": len(data), "sha256": hashlib.sha256(data).hexdigest()}
        self.assets[identifier].update(updates)
        self.component(identifier).update(updates)

    def serve(self, identifier, data, *, length=None):
        response = Response(data, self.component(identifier)["url"], length=length)
        self.opener = SimpleNamespace(open=Mock(return_value=response))
        self.network.side_effect = None
        self.network.return_value = self.opener

    def install(self):
        return setup.install_missing(manifest=self.manifest, download_root=self.runs)

    def failed(self, exception=setup.SetupError):
        with self.assertRaises(exception) as caught:
            self.install()
        error = caught.exception
        run = Path(error.run_directory)
        self.assertTrue((run / "failure.txt").is_file())
        self.assertEqual(json.loads((run / "receipt.json").read_text(encoding="utf-8")), error.receipt)
        return error, run

    def test_hash_mismatch_retains_download_and_never_inspects_or_executes_it(self):
        tampered = bytes([self.msi[0] ^ 1]) + self.msi[1:]
        self.serve("powershell7", tampered)
        with patch.object(setup, "_verify_microsoft_signature") as signature, patch.object(setup, "_install_msi") as msi:
            error, run = self.failed()
        signature.assert_not_called()
        msi.assert_not_called()
        self.assertEqual((run / "PowerShell-7.6.9-win-x64.msi").read_bytes(), tampered)
        self.assertEqual([row["id"] for row in error.receipt["components"]], ["powershell7"])
        self.assertEqual(self.opener.open.call_count, 1)
        self.process.assert_not_called()

    def test_short_and_oversize_responses_preserve_bounded_partial_evidence(self):
        for data in (self.msi[:-1], self.msi + b"EXCESS"):
            with self.subTest(size=len(data)):
                self.serve("powershell7", data, length=len(self.msi))
                _, run = self.failed()
                kept = (run / "PowerShell-7.6.9-win-x64.msi").read_bytes()
                self.assertEqual(kept, data[:len(self.msi) + 1])
                self.assertEqual(self.opener.open.call_count, 1)
        self.process.assert_not_called()

    def test_all_present_skips_without_disk_creation_network_or_processes(self):
        sentinel = self.local / "user-owned-font.ttf"
        sentinel.parent.mkdir()
        sentinel.write_bytes(b"USER OWNED")
        self.present.update(setup.COMPONENT_IDS)
        before = sorted(str(path.relative_to(self.root)) for path in self.root.rglob("*"))
        self.pin("_directory", side_effect=AssertionError("No directory creation when already ready"))
        self.pin("_new_file", side_effect=AssertionError("No file creation when already ready"))
        for overrides in ({}, {"download_root": self.runs}):
            with self.subTest(overrides=overrides):
                result = setup.install_missing(manifest=self.manifest, **overrides)
                self.assertIsNone(result["run_directory"])
                self.assertFalse(result["reboot_required"])
        self.assertEqual(sorted(str(path.relative_to(self.root)) for path in self.root.rglob("*")), before)
        self.assertEqual(sentinel.read_bytes(), b"USER OWNED")
        self.assertFalse(self.runs.exists())
        self.local_lookup.assert_not_called()
        self.network.assert_not_called()
        self.process.assert_not_called()
        setup._set_font_registry.assert_not_called()
        setup._add_font.assert_not_called()

    def test_missing_font_is_prepared_without_touching_existing_powershell(self):
        self.present.add("powershell7")
        existing = self.root / "existing-powershell/pwsh.exe"
        existing.parent.mkdir()
        existing.write_bytes(b"USER OWNED POWERSHELL VERSION")
        self.serve("d2coding_propo", self.archive)
        setup._font_change.side_effect = lambda: self.present.add("d2coding_propo")

        receipt = self.install()

        self.assertTrue(receipt["components"][1]["metadata_confirmed"])
        self.assertEqual(existing.read_bytes(), b"USER OWNED POWERSHELL VERSION")
        self.assertEqual(self.opener.open.call_count, 1)
        self.process.assert_not_called()

    def test_missing_powershell_is_prepared_without_touching_existing_font(self):
        self.present.add("d2coding_propo")
        existing = self.local / "Microsoft/Windows/Fonts" / next(iter(self.fonts))
        existing.parent.mkdir(parents=True)
        existing.write_bytes(b"USER OWNED FONT VERSION")
        self.serve("powershell7", self.msi)
        system = self.root / "Windows/System32"
        system.mkdir(parents=True)
        (system / "msiexec.exe").write_bytes(b"INERT never executed")

        def finish_msi():
            self.present.add("powershell7")
            return 0

        child = Mock(pid=201, wait=Mock(side_effect=finish_msi))
        self.process.side_effect = None
        self.process.return_value = child
        with patch.object(setup, "_system_directory", return_value=system), patch.object(setup, "_verify_microsoft_signature"):
            receipt = self.install()

        self.assertTrue(receipt["components"][0]["metadata_confirmed"])
        self.assertFalse(receipt["reboot_required"])
        self.assertEqual(existing.read_bytes(), b"USER OWNED FONT VERSION")
        self.assertEqual(self.opener.open.call_count, 1)
        self.assertEqual(self.process.call_count, 1)
        child.wait.assert_called_once_with()
        child.kill.assert_not_called()
        child.terminate.assert_not_called()
        setup._set_font_registry.assert_not_called()
        setup._add_font.assert_not_called()

    def test_installation_appearing_during_download_is_preserved(self):
        self.serve("powershell7", self.msi)
        self.present.add("d2coding_propo")
        powershell_checks = 0

        def appear_after_download(component):
            nonlocal powershell_checks
            if component["id"] == "powershell7":
                powershell_checks += 1
                if powershell_checks == 3:
                    self.present.add("powershell7")
            return self.component_status(component)

        self.status.side_effect = appear_after_download
        with patch.object(setup, "_verify_microsoft_signature") as signature:
            receipt = self.install()
        self.assertEqual(receipt["components"][0]["status"], "skipped")
        self.assertTrue(Path(receipt["components"][0]["download"]).is_file())
        signature.assert_not_called()
        self.process.assert_not_called()

    def test_redirect_to_untrusted_host_or_http_is_rejected_without_following(self):
        original = self.component("powershell7")["url"]
        for url in ("http://release-assets.githubusercontent.com/file", "https://example.com/file",
                    "https://github.com/attacker/project/releases/download/a/file",
                    "https://github.com@evil.invalid/file", "https://github.com:444/file"):
            with self.subTest(url=url):
                redirect = setup._ReleaseRedirect(original)
                with self.assertRaises(ValueError):
                    redirect.redirect_request(setup.urllib.request.Request(original), None, 302, "redirect", {}, url)
        redirect = setup._ReleaseRedirect(original)
        for index in range(4):
            redirect.redirect_request(setup.urllib.request.Request(original), None, 302, "redirect", {},
                                      f"https://release-assets.githubusercontent.com/file?step={index}")
        with self.assertRaises(ValueError):
            redirect.redirect_request(setup.urllib.request.Request(original), None, 302, "redirect", {},
                                      "https://release-assets.githubusercontent.com/fifth")
        self.network.assert_not_called()

    def test_traversal_duplicate_symlink_and_member_tamper_never_mutate_fonts(self):
        safe = {**self.fonts, **self.notices}
        self.present.add("powershell7")
        symlink = zipfile.ZipInfo("linked.ttf")
        symlink.create_system = 3
        symlink.external_attr = (stat.S_IFLNK | 0o777) << 16
        cases = {
            "traversal": self.zip_bytes({**safe, "../escape.ttf": b"escape"}),
            "case duplicate": self.zip_bytes({**safe, next(iter(self.fonts)).upper(): b"duplicate"}),
            "symlink": self.zip_bytes({**safe, symlink: b"target"}),
            "member hash": self.zip_bytes({**safe, next(iter(self.fonts)): b"X" * len(next(iter(self.fonts.values())))}),
            "missing member": self.zip_bytes({name: data for name, data in safe.items() if "Bold" not in name}),
        }
        with patch.object(setup, "_install_fonts") as installer:
            for label, data in cases.items():
                with self.subTest(failure=label):
                    self.bind_payload("d2coding_propo", data)
                    self.serve("d2coding_propo", data)
                    _, run = self.failed()
                    self.assertEqual((run / "D2Coding.zip").read_bytes(), data)
                    self.assertFalse((self.root / "escape.ttf").exists())
        installer.assert_not_called()
        self.assertFalse((self.local / "Microsoft/Windows/Fonts").exists())

    def test_crc_failure_in_unused_mono_member_stops_before_registration(self):
        self.present.add("powershell7")
        data = self.zip_bytes({**self.fonts, **self.notices, "D2CodingNerdFontMono-Regular.ttf": b"INERT-mono-payload"})
        corrupted = data.replace(b"INERT-mono-payload", b"INERT-MONO-payload", 1)
        self.assertEqual(len(corrupted), len(data))
        self.bind_payload("d2coding_propo", corrupted)
        self.serve("d2coding_propo", corrupted)
        with patch.object(setup, "_install_fonts") as installer:
            self.failed()
        installer.assert_not_called()


    def test_existing_font_path_collision_refuses_before_download_and_preserves_bytes(self):
        self.present.add("powershell7")
        fonts = self.local / "Microsoft/Windows/Fonts"
        fonts.mkdir(parents=True)
        target = fonts / next(iter(self.fonts))
        target.write_bytes(b"USER'S EXISTING DIFFERENT FONT")
        self.failed()
        self.assertEqual(target.read_bytes(), b"USER'S EXISTING DIFFERENT FONT")
        self.network.assert_not_called()
        setup._set_font_registry.assert_not_called()
        setup._add_font.assert_not_called()

    def test_reparse_font_directory_is_rejected_without_writing_through_it(self):
        self.present.add("powershell7")
        foreign = self.root / "foreign"
        foreign.mkdir()
        sentinel = foreign / "untouched.txt"
        sentinel.write_bytes(b"USER OWNED")
        fonts = self.local / "Microsoft/Windows/Fonts"
        fonts.parent.mkdir(parents=True)
        try:
            fonts.symlink_to(foreign, target_is_directory=True)
        except OSError as exc:
            self.skipTest(f"Creating an isolated directory symlink is unavailable: {exc}")
        self.failed()
        self.assertEqual(list(foreign.iterdir()), [sentinel])
        self.network.assert_not_called()

    def test_font_registration_failure_preserves_partial_copies_and_stops(self):
        self.present.add("powershell7")
        self.serve("d2coding_propo", self.archive)
        setup._set_font_registry.side_effect = [None, OSError("synthetic registry permission failure")]
        error, run = self.failed()
        record = error.receipt["components"][-1]
        self.assertEqual(record["status"], "failed")
        self.assertEqual(len(record["font_files"]), 2)
        self.assertEqual(len(record["registry_values"]), 1)
        self.assertEqual(len(record["session_fonts"]), 1)
        for row in record["font_files"]:
            self.assertTrue(Path(row["path"]).is_file())
        self.assertTrue((run / "notices/LICENSE").is_file())
        setup._font_change.assert_not_called()

    def test_msi_user_cancel_stops_font_chain_and_retains_exact_exit_code(self):
        self.serve("powershell7", self.msi)
        system = self.root / "Windows/System32"
        system.mkdir(parents=True)
        (system / "msiexec.exe").write_bytes(b"INERT never executed")
        child = Mock(pid=198, wait=Mock(return_value=1602))
        self.process.side_effect = None
        self.process.return_value = child
        with patch.object(setup, "_system_directory", return_value=system), patch.object(setup, "_verify_microsoft_signature"):
            error, _ = self.failed(setup.SetupCancelled)
        self.assertEqual([row["id"] for row in error.receipt["components"]], ["powershell7"])
        self.assertEqual(error.receipt["components"][0]["exit_code"], 1602)
        child.wait.assert_called_once_with()
        child.kill.assert_not_called()
        child.terminate.assert_not_called()
        self.assertEqual(self.opener.open.call_count, 1)

    def test_msi_reboot_request_is_recorded_without_restarting_or_followup_processes(self):
        self.present.add("d2coding_propo")
        self.serve("powershell7", self.msi)
        system = self.root / "Windows/System32"
        system.mkdir(parents=True)
        (system / "msiexec.exe").write_bytes(b"INERT never executed")
        child = Mock(pid=197, wait=Mock(return_value=3010))
        self.process.side_effect = None
        self.process.return_value = child
        with patch.object(setup, "_system_directory", return_value=system), patch.object(setup, "_verify_microsoft_signature"):
            receipt = self.install()
        self.assertTrue(receipt["reboot_required"])
        self.assertEqual(receipt["components"][0]["exit_code"], 3010)
        self.assertEqual(receipt["components"][0]["status"], "reboot_required")
        self.assertFalse(receipt["components"][0]["metadata_confirmed"])
        self.assertEqual(self.process.call_count, 1)
        child.wait.assert_called_once_with()
        child.kill.assert_not_called()

    def test_uncertain_installer_wait_failure_is_not_killed_or_retried(self):
        self.serve("powershell7", self.msi)
        system = self.root / "Windows/System32"
        system.mkdir(parents=True)
        (system / "msiexec.exe").write_bytes(b"INERT never executed")
        child = Mock(pid=199, wait=Mock(side_effect=OSError("synthetic wait failure")))
        self.process.side_effect = None
        self.process.return_value = child
        with patch.object(setup, "_system_directory", return_value=system), patch.object(setup, "_verify_microsoft_signature"):
            error, _ = self.failed()
        self.assertEqual(error.receipt["components"][0]["installer_process_id"], 199)
        self.assertNotIn("exit_code", error.receipt["components"][0])
        child.kill.assert_not_called()
        child.terminate.assert_not_called()
        self.assertEqual(self.process.call_count, 1)
        self.assertEqual(self.opener.open.call_count, 1)

    def test_signature_failure_preserves_logs_and_never_starts_msi(self):
        self.serve("powershell7", self.msi)
        system = self.root / "Windows/System32"
        powershell = system / "WindowsPowerShell/v1.0/powershell.exe"
        powershell.parent.mkdir(parents=True)
        powershell.write_bytes(b"INERT system fixture")
        child = Mock(pid=200, wait=Mock(return_value=1))
        self.process.side_effect = None
        self.process.return_value = child
        with patch.object(setup, "_system_directory", return_value=system), patch.object(setup, "_install_msi") as installer:
            error, _ = self.failed()
        installer.assert_not_called()
        self.assertEqual(error.receipt["components"][0]["signature_exit_code"], 1)
        child.wait.assert_called_once_with()
        child.kill.assert_not_called()

    def test_modified_manifest_url_and_duplicate_json_are_rejected_before_run_creation(self):
        changed = deepcopy(self.manifest)
        changed["components"][0]["url"] = "https://evil.invalid/installer.msi"
        with self.assertRaises(ValueError):
            setup.install_missing(manifest=changed, download_root=self.runs)
        self.assertFalse(self.runs.exists())
        path = self.root / "duplicate.json"
        path.write_text('{"contract":"x","contract":"x","components":[]}', encoding="utf-8")
        with self.assertRaises(ValueError):
            setup.load_manifest(path)
        self.network.assert_not_called()
        self.process.assert_not_called()

    def test_changed_bundled_original_notice_is_rejected_before_network_or_run_creation(self):
        name = self.component("d2coding_propo")["license_files"][0]
        path = self.root / name
        path.write_bytes(b"UNREVIEWED MODIFIED NOTICE")
        with self.assertRaises(ValueError):
            self.install()
        self.assertEqual(path.read_bytes(), b"UNREVIEWED MODIFIED NOTICE")
        self.assertFalse(self.runs.exists())
        self.network.assert_not_called()
        self.process.assert_not_called()

    def test_success_exit_without_installed_metadata_is_not_reported_as_installed(self):
        self.serve("powershell7", self.msi)
        with patch.object(setup, "_verify_microsoft_signature"), patch.object(setup, "_install_msi", return_value=False):
            error, _ = self.failed()
        self.assertEqual(error.receipt["components"][0]["status"], "failed")
        self.assertFalse(error.receipt["reboot_required"])
        self.assertEqual(self.opener.open.call_count, 1)
        self.process.assert_not_called()

    def test_font_registration_without_installed_metadata_is_not_reported_as_installed(self):
        self.present.add("powershell7")
        self.serve("d2coding_propo", self.archive)

        error, run = self.failed()

        self.assertEqual([row["status"] for row in error.receipt["components"]], ["skipped", "failed"])
        record = error.receipt["components"][-1]
        self.assertFalse(record["metadata_confirmed"])
        self.assertEqual(len(record["font_files"]), 2)
        self.assertEqual(len(record["registry_values"]), 2)
        self.assertEqual(len(record["session_fonts"]), 2)
        self.assertTrue((run / "notices/LICENSE").is_file())
        self.assertFalse(error.receipt["reboot_required"])
        self.assertEqual(self.opener.open.call_count, 1)
        self.process.assert_not_called()

    def test_readonly_report_records_missing_then_ready_without_installing(self):
        missing = self.root / "missing-status.json"
        self.assertEqual(setup.write_status_report(missing), 2)
        first = json.loads(missing.read_text(encoding="utf-8"))
        self.assertFalse(first["ready"])
        self.assertFalse(first["components"]["powershell7"]["installed"])
        self.present.update(setup.COMPONENT_IDS)
        ready = self.root / "ready-status.json"
        self.assertEqual(setup.write_status_report(ready), 0)
        self.assertTrue(json.loads(ready.read_text(encoding="utf-8"))["ready"])
        self.assertFalse(self.runs.exists())
        self.network.assert_not_called()
        self.process.assert_not_called()
        setup._set_font_registry.assert_not_called()
        setup._add_font.assert_not_called()

    def test_readonly_report_never_replaces_existing_or_writes_through_a_link(self):
        existing = self.root / "existing.json"
        existing.write_bytes(b"USER OWNED REPORT")
        with self.assertRaises(FileExistsError):
            setup.write_status_report(existing)
        self.assertEqual(existing.read_bytes(), b"USER OWNED REPORT")
        foreign = self.root / "foreign"
        foreign.mkdir()
        link = self.root / "linked-report-dir"
        try:
            link.symlink_to(foreign, target_is_directory=True)
        except OSError as error:
            self.skipTest("Directory links unavailable: " + str(error))
        with self.assertRaises(ValueError):
            setup.write_status_report(link / "status.json")
        self.assertEqual(list(foreign.iterdir()), [])

    def test_readonly_report_preserves_inspection_failure_as_an_error(self):
        target = self.root / "failed-status.json"
        self.status.side_effect = ValueError("INERT invalid installed runtime")
        self.assertEqual(setup.write_status_report(target), 1)
        report = json.loads(target.read_text(encoding="utf-8"))
        self.assertFalse(report["ready"])
        self.assertEqual(report["error"]["type"], "ValueError")
        self.assertIn("invalid installed runtime", report["error"]["detail"])
        self.network.assert_not_called()
        self.process.assert_not_called()


    def test_microsoft_runtime_hash_failure_never_launches_the_installer(self):
        self.present.update(("powershell7", "d2coding_propo"))
        self.present.remove("dotnet10")
        expected = b"INERT .NET installer; never executed"
        self.bind_payload("dotnet10", expected)
        self.serve("dotnet10", b"TAMPERED" + expected)
        with patch.object(setup, "_verify_microsoft_signature") as signature:
            error, run = self.failed()
        self.assertEqual(error.receipt["components"][-1]["id"], "dotnet10")
        self.assertEqual(error.receipt["components"][-1]["status"], "failed")
        self.assertTrue((run / "windowsdesktop-runtime-10.0.14-win-x64.exe").exists())
        signature.assert_not_called()
        self.process.assert_not_called()

    def test_two_missing_microsoft_runtimes_finish_without_overwriting_first_signature_logs(self):
        import hwp_setup_runtime as runtime
        self.present = {"powershell7", "d2coding_propo", "omp_runtime"}
        bodies = {}
        for identifier in ("dotnet10", "webview2"):
            body = ("INERT Microsoft installer: " + identifier).encode("ascii")
            self.bind_payload(identifier, body)
            bodies[self.component(identifier)["url"]] = body
        self.network.side_effect = None
        self.network.return_value = SimpleNamespace(
            open=lambda request, timeout: Response(bodies[request.full_url], request.full_url))
        system = self.root / "Windows/System32"
        powershell = system / "WindowsPowerShell/v1.0/powershell.exe"
        powershell.parent.mkdir(parents=True)
        powershell.write_bytes(b"INERT system signature checker; never executed")

        def signature_child(*_args, **kwargs):
            kwargs["stdout"].write(b"MICROSOFT-AUTHENTICODE-VALID")
            return SimpleNamespace(pid=200, wait=lambda: 0)

        def finish(component, _payloads, _run, record):
            self.present.add(component["id"])
            record["exit_code"] = 0
            return False

        self.process.side_effect = signature_child
        with patch.object(setup, "_system_directory", return_value=system), patch.object(runtime, "install_component", side_effect=finish):
            receipt = self.install()
        installed = {row["id"]: row for row in receipt["components"] if row["status"] == "installed"}
        self.assertEqual(set(installed), {"dotnet10", "webview2"})
        self.assertTrue(all(row["metadata_confirmed"] for row in installed.values()))
        self.assertFalse(receipt["reboot_required"])

if __name__ == "__main__":
    unittest.main()
