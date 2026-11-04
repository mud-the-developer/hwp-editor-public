from __future__ import annotations
import hashlib
import importlib
from pathlib import Path
import stat
import struct
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch
import zipfile

from test_com_cache import cached_com_modules
from test_native_owner import fake_owned_session

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
s = importlib.import_module("hwp_security")


def fake_pe(machine=0x14C, dll=True):
    data = bytearray(128)
    data[:2] = b"MZ"
    struct.pack_into("<I", data, 60, 64)
    data[64:68] = b"PE\0\0"
    struct.pack_into("<H", data, 68, machine)
    struct.pack_into("<H", data, 86, 0x2000 if dll else 0)
    return bytes(data)


class FakeRegistry:
    HKEY_CURRENT_USER = "HKCU"
    HKEY_CLASSES_ROOT = "HKCR"
    KEY_READ = 1
    KEY_SET_VALUE = 2
    KEY_WOW64_32KEY = 32
    KEY_WOW64_64KEY = 64
    REG_SZ = 1

    def __init__(self, value=None, kind=1):
        self.value, self.kind, self.writes = value, kind, []

    def OpenKey(self, hive, name, _, access):
        assert hive == self.HKEY_CURRENT_USER and name == s.REGISTRY_KEY
        if self.value is None:
            raise FileNotFoundError()
        return self

    def QueryValueEx(self, key, alias):
        assert alias == s.MODULE_ALIAS
        return self.value, self.kind

    def CreateKeyEx(self, hive, name, _, access):
        assert hive == self.HKEY_CURRENT_USER and name == s.REGISTRY_KEY
        assert access & self.KEY_SET_VALUE
        return self

    def SetValueEx(self, key, alias, _, kind, value):
        assert alias == s.MODULE_ALIAS and kind == self.REG_SZ
        self.writes.append((alias, value))
        self.value = value

    def __enter__(self):
        return self

    def __exit__(self, *args):
        pass


class SecurityTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.directory = Path(self.temp.name)
        self.dll = fake_pe()
        self.package = self.directory / "provided.zip"
        self.pin = patch.object(s, "DLL_SHA256", hashlib.sha256(self.dll).hexdigest())
        self.pin.start()
        self.addCleanup(self.pin.stop)
        self.host = patch.object(
            s, "hwp_executable", return_value=self.directory / "hwp.exe"
        )
        self.host.start()
        self.addCleanup(self.host.stop)
        self.make_zip([(s.DLL_NAME, self.dll)])

    def make_zip(self, entries):
        with zipfile.ZipFile(self.package, "w", zipfile.ZIP_DEFLATED) as archive:
            for name, data in entries:
                archive.writestr(name, data)
        self.zip_pin = patch.object(
            s, "ZIP_SHA256", hashlib.sha256(self.package.read_bytes()).hexdigest()
        )
        self.zip_pin.start()
        self.addCleanup(self.zip_pin.stop)

    def ensure(self, registry):
        return s.ensure_security_module(
            registry=registry,
            local_appdata=self.directory / "local",
            package=self.package,
        )

    def test_existing_approved_module_reused_without_zip_or_registry_change(self):
        existing = self.directory / s.DLL_NAME
        existing.write_bytes(self.dll)
        self.package.unlink()
        registry = FakeRegistry(str(existing))
        result = self.ensure(registry)
        self.assertEqual(result["dll"], str(existing))
        self.assertFalse(result["auto_installed"])
        self.assertFalse(result["registry_updated"])
        self.assertEqual(registry.writes, [])

    def test_missing_registry_installs_only_approved_dll_in_user_cache(self):
        self.make_zip(
            [(s.DLL_NAME, self.dll), ("src/do-not-run.cmd", b"untrusted data")]
        )
        registry = FakeRegistry()
        result = self.ensure(registry)
        self.assertTrue(result["auto_installed"])
        self.assertTrue(result["registry_updated"])
        installed = Path(result["dll"])
        self.assertTrue(installed.is_relative_to(self.directory / "local"))
        self.assertEqual(installed.read_bytes(), self.dll)
        self.assertEqual(list(installed.parent.iterdir()), [installed])
        self.assertEqual(registry.writes, [(s.MODULE_ALIAS, str(installed))])

    def test_missing_registered_dll_repaired_from_local_zip(self):
        registry = FakeRegistry(str(self.directory / "removed.dll"))
        self.assertTrue(self.ensure(registry)["auto_installed"])
        registered = registry.value
        assert isinstance(registered, str)
        self.assertTrue(Path(registered).is_file())

    def test_valid_managed_cache_reused_when_zip_absent(self):
        first = self.ensure(FakeRegistry())
        self.package.unlink()
        result = self.ensure(FakeRegistry(str(self.directory / "deleted-original.dll")))
        self.assertEqual(result["dll"], first["dll"])
        self.assertFalse(result["auto_installed"])

    def test_unknown_registered_binary_not_loaded_replaced_or_installed(self):
        unknown = self.directory / "unknown.dll"
        unknown.write_bytes(b"unknown code")
        registry = FakeRegistry(str(unknown))
        with self.assertRaisesRegex(ValueError, "explicitly authorized"):
            self.ensure(registry)
        self.assertEqual(registry.writes, [])
        self.assertEqual(unknown.read_bytes(), b"unknown code")

    def test_registry_wrong_type_or_relative_path_stops(self):
        for registry in [FakeRegistry("relative.dll"), FakeRegistry("bad", kind=2)]:
            with self.assertRaises(ValueError):
                self.ensure(registry)
            self.assertEqual(registry.writes, [])

    def test_no_dll_and_no_zip_stops_without_registry_changes(self):
        self.package.unlink()
        registry = FakeRegistry()
        with self.assertRaises(FileNotFoundError):
            self.ensure(registry)
        self.assertEqual(registry.writes, [])

    def test_changed_zip_rejected_before_installation(self):
        self.package.write_bytes(b"replaced archive")
        registry = FakeRegistry()
        with self.assertRaisesRegex(ValueError, "owner-authorized archive"):
            self.ensure(registry)
        self.assertEqual(registry.writes, [])

    def test_archive_traversal_absolute_and_duplicates_rejected(self):
        for bad in [
            "../escape.dll",
            "/escape.dll",
            "C:/escape.dll",
            "src/../../escape.dll",
            s.DLL_NAME.upper(),
        ]:
            self.make_zip([(s.DLL_NAME, self.dll), (bad, b"bad")])
            with self.assertRaises(ValueError):
                s.read_package(self.package)

    def test_archive_symlink_rejected(self):
        link = zipfile.ZipInfo("src/link")
        link.create_system = 3
        link.external_attr = (stat.S_IFLNK | 0o777) << 16
        self.make_zip([(s.DLL_NAME, self.dll), (link, b"../escape")])
        with self.assertRaisesRegex(ValueError, "Unsafe"):
            s.read_package(self.package)

    def test_no_generic_or_nested_dll_selection(self):
        self.make_zip([("src/" + s.DLL_NAME, self.dll)])
        with self.assertRaisesRegex(ValueError, "root-level"):
            s.read_package(self.package)

    def test_dll_machine_and_dll_flag_checked(self):
        for data in [fake_pe(machine=0x8664), fake_pe(dll=False)]:
            with patch.object(s, "DLL_SHA256", s.digest(data)):
                with self.assertRaisesRegex(ValueError, "32-bit DLL"):
                    s.checked_dll(data)

    def test_host_bitness_failure_prevents_installation_and_registry_writes(self):
        registry = FakeRegistry()
        with patch.object(
            s, "hwp_executable", side_effect=ValueError("Wrong Hwp bitness")
        ):
            with self.assertRaisesRegex(ValueError, "bitness"):
                self.ensure(registry)
        self.assertEqual(registry.writes, [])
        self.assertFalse((self.directory / "local").exists())

    def test_invalid_cache_never_overwritten(self):
        target = self.directory / "cache" / s.DLL_NAME
        target.parent.mkdir()
        target.write_bytes(b"unexpected content")
        with self.assertRaises(ValueError):
            s.install_package(target, self.package)
        self.assertEqual(target.read_bytes(), b"unexpected content")

    def test_register_exact_alias_on_every_instance_and_stop_on_false(self):
        with patch.object(
            s, "ensure_security_module", return_value={"dll": "approved"}
        ):
            for _ in range(2):
                app = Mock()
                app.RegisterModule.return_value = True
                self.assertTrue(s.register_security_module(app)["registered"])
                app.RegisterModule.assert_called_once_with(
                    "FilePathCheckDLL", s.MODULE_ALIAS
                )
            app = Mock()
            app.RegisterModule.return_value = False
            with self.assertRaisesRegex(RuntimeError, "no document opened"):
                s.register_security_module(app)
            app.Open.assert_not_called()

    @patch("native_hwp.owned_session", new=fake_owned_session)
    def test_native_export_registers_before_open_and_keeps_owned_cleanup(self):
        native = importlib.import_module("native_hwp")
        app = Mock()
        app.PageCount, app.Version = 5, "fake"
        events = []
        app.Open.side_effect = lambda *args: events.append("open") or True
        target = self.directory / "out.pdf"
        app.SaveAs.side_effect = lambda *args: target.write_bytes(b"TEST ONLY") or True
        com = SimpleNamespace(CoInitialize=Mock(), CoUninitialize=Mock())
        client = SimpleNamespace(DispatchEx=Mock(return_value=app))
        with patch.dict(sys.modules, cached_com_modules(com, client, self.directory)):
            with patch.object(
                native,
                "register_security_module",
                side_effect=lambda a: events.append("register") or {"registered": True},
            ):
                result = native.export(self.directory / "input.hwp", target)
        self.assertEqual(events, ["register", "open"])
        self.assertTrue(result["security_module"]["registered"])
        client.DispatchEx.assert_called_once_with("HWPFrame.HwpObject")
        app.Clear.assert_called_once_with(1)
        app.Quit.assert_called_once()
        com.CoUninitialize.assert_called_once()

    @patch("native_hwp.owned_session", new=fake_owned_session)
    def test_native_register_failure_never_opens_input(self):
        native = importlib.import_module("native_hwp")
        app = Mock()
        com = SimpleNamespace(CoInitialize=Mock(), CoUninitialize=Mock())
        client = SimpleNamespace(DispatchEx=Mock(return_value=app))
        with patch.dict(sys.modules, cached_com_modules(com, client, self.directory)):
            with patch.object(
                native,
                "register_security_module",
                side_effect=RuntimeError("registration failed"),
            ):
                with self.assertRaisesRegex(RuntimeError, "registration failed"):
                    native.export(
                        self.directory / "input.hwp", self.directory / "out.pdf"
                    )
        app.Open.assert_not_called()
        app.Quit.assert_called_once()
