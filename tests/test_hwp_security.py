from __future__ import annotations
from contextlib import contextmanager
import hashlib
import importlib
import os
from pathlib import Path
import stat
import struct
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch
import zipfile

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
s = importlib.import_module("hwp_security")
setup = importlib.import_module("hwp_setup")
_MISSING = object()


def fake_pe(machine=0x14C, dll=True, executable=True):
    data = bytearray(128)
    data[:2] = b"MZ"
    struct.pack_into("<I", data, 60, 64)
    data[64:68] = b"PE\0\0"
    struct.pack_into("<H", data, 68, machine)
    characteristics = (0x0002 if executable else 0) | (0x2000 if dll else 0)
    struct.pack_into("<H", data, 86, characteristics)
    return bytes(data)


class RegistryKey:
    def __init__(self, hive, name, view):
        self.hive, self.name, self.view = hive, name, view

    def __enter__(self):
        return self

    def __exit__(self, *args):
        pass


class FakeRegistry:
    HKEY_CURRENT_USER = "HKCU"
    HKEY_CLASSES_ROOT = "HKCR"
    KEY_READ = 1
    KEY_SET_VALUE = 2
    KEY_WOW64_32KEY = 32
    KEY_WOW64_64KEY = 64
    REG_SZ = 1
    CLSID = "{00000000-0000-0000-0000-000000000001}"
    PROGID_KEY = "HWPFrame.HwpObject\\CLSID"
    SERVER_KEY = "CLSID\\" + CLSID + "\\LocalServer32"

    def __init__(self, hwp=None, value=_MISSING, kind=1, view=32):
        self.value, self.kind = value, kind
        self.module_key = value is not _MISSING
        self.writes, self.creates = [], []
        self.com = {}
        self.before_create = None
        self.before_set = None
        if hwp is not None:
            self.com[(view, self.PROGID_KEY)] = (self.CLSID, self.REG_SZ)
            self.com[(view, self.SERVER_KEY)] = ('"' + str(hwp) + '" /Automation', self.REG_SZ)

    def OpenKey(self, hive, name, _, access):
        view = access & (self.KEY_WOW64_32KEY | self.KEY_WOW64_64KEY)
        if hive == self.HKEY_CURRENT_USER:
            assert name == s.REGISTRY_KEY and view == self.KEY_WOW64_32KEY
            if not self.module_key:
                raise FileNotFoundError(name)
        else:
            assert hive == self.HKEY_CLASSES_ROOT
            if (view, name) not in self.com:
                raise FileNotFoundError(name)
        return RegistryKey(hive, name, view)

    def QueryValueEx(self, key, alias):
        if key.hive == self.HKEY_CURRENT_USER:
            assert alias == s.MODULE_ALIAS
            if self.value is _MISSING:
                raise FileNotFoundError(alias)
            return self.value, self.kind
        assert alias is None
        return self.com[(key.view, key.name)]

    def CreateKeyEx(self, hive, name, _, access):
        assert hive == self.HKEY_CURRENT_USER and name == s.REGISTRY_KEY
        assert access & self.KEY_SET_VALUE and access & self.KEY_READ
        assert access & self.KEY_WOW64_32KEY
        self.creates.append(name)
        self.module_key = True
        if self.before_create is not None:
            self.before_create()
        return RegistryKey(hive, name, self.KEY_WOW64_32KEY)

    def SetValueEx(self, key, alias, _, kind, value):
        assert key.hive == self.HKEY_CURRENT_USER and key.name == s.REGISTRY_KEY
        assert alias == s.MODULE_ALIAS and kind == self.REG_SZ
        if self.before_set is not None:
            self.before_set()
        self.writes.append((alias, value))
        self.value, self.kind = value, kind

    def declare(self, value, kind=1):
        self.module_key, self.value, self.kind = True, value, kind


class SecurityTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.directory = Path(self.temp.name).resolve()
        self.local = self.directory / "local"
        self.hwp = self.directory / "hwp.exe"
        self.hwp.write_bytes(fake_pe(dll=False))
        self.dll = fake_pe()
        self.package = self.directory / "provided.zip"
        pin = patch.object(s, "DLL_SHA256", hashlib.sha256(self.dll).hexdigest())
        pin.start()
        self.addCleanup(pin.stop)
        location = patch.object(setup, "_local_app_data", return_value=self.local)
        location.start()
        self.addCleanup(location.stop)
        self.make_zip([(s.DLL_NAME, self.dll)])

    def make_zip(self, entries):
        with zipfile.ZipFile(self.package, "w", zipfile.ZIP_DEFLATED) as archive:
            for name, data in entries:
                archive.writestr(name, data)
        pin = patch.object(s, "ZIP_SHA256", hashlib.sha256(self.package.read_bytes()).hexdigest())
        pin.start()
        self.addCleanup(pin.stop)

    def registry(self, value=_MISSING, kind=1, **kwargs):
        return FakeRegistry(self.hwp, value, kind, **kwargs)

    def ensure(self, registry):
        return s.ensure_security_module(
            registry=registry, local_appdata=self.local, package=self.package
        )

    def target(self):
        return self.local / "HwpReview" / "SecurityModules" / s.DLL_SHA256 / s.DLL_NAME

    def existing(self, name=s.DLL_NAME):
        path = self.directory / name
        path.write_bytes(self.dll)
        return path

    def test_status_and_native_preflight_preserve_existing_registration(self):
        existing = self.existing()
        registry = self.registry(str(existing))
        before = existing.stat()
        self.package.unlink()
        status = s.automation_status(registry=registry)
        self.assertIs(status["required"], True)
        self.assertIs(status["installed"], True)
        for result in (s.registered_security_module(registry=registry), self.ensure(registry)):
            self.assertEqual(result["dll"], str(existing))
            self.assertFalse(result["auto_installed"])
            self.assertFalse(result["registry_updated"])
        self.assertEqual(existing.read_bytes(), self.dll)
        self.assertEqual(existing.stat().st_mtime_ns, before.st_mtime_ns)
        self.assertEqual(registry.writes, [])
        self.assertEqual(registry.creates, [])
        self.assertEqual(registry.value, str(existing))
        self.assertFalse(self.local.exists())

    def test_missing_status_and_native_rejection_do_not_prepare_module(self):
        registry = self.registry()
        status = s.automation_status(registry=registry)
        self.assertIs(status["required"], True)
        self.assertIs(status["installed"], False)
        with self.assertRaises(RuntimeError):
            s.registered_security_module(registry=registry)
        self.assertEqual(registry.creates, [])
        self.assertEqual(registry.writes, [])
        self.assertFalse(self.local.exists())

    def test_absent_com_is_not_applicable_without_faking_installed(self):
        registry = FakeRegistry()
        status = s.automation_status(registry=registry)
        self.assertIs(status["required"], False)
        self.assertIs(status["installed"], False)
        with self.assertRaises(FileNotFoundError):
            self.ensure(registry)
        self.assertFalse(self.local.exists())
        self.assertEqual(registry.creates, [])

    def test_known_unsupported_hosts_are_not_applicable_but_native_rejects(self):
        for machine in (0x8664, 0xAA64):
            with self.subTest(machine=machine):
                self.hwp.write_bytes(fake_pe(machine=machine, dll=False))
                registry = self.registry()
                status = s.automation_status(registry=registry)
                self.assertIs(status["required"], False)
                self.assertIs(status["installed"], False)
                with self.assertRaises(s.UnsupportedHwpArchitecture):
                    s.registered_security_module(registry=registry)
                with self.assertRaises(s.UnsupportedHwpArchitecture):
                    self.ensure(registry)
                self.assertEqual(registry.creates, [])
                self.assertFalse(self.local.exists())

    def test_corrupt_or_unknown_host_never_becomes_not_applicable(self):
        for data in (
            b"corrupt executable",
            fake_pe(0xFFFF, dll=False),
            fake_pe(),
            fake_pe(0x8664, dll=False, executable=False),
        ):
            with self.subTest(data=data):
                self.hwp.write_bytes(data)
                registry = self.registry()
                with self.assertRaises(ValueError) as caught:
                    s.automation_status(registry=registry)
                self.assertNotIsInstance(caught.exception, s.UnsupportedHwpArchitecture)
                self.assertEqual(registry.creates, [])
        self.hwp.unlink()
        with self.assertRaises(ValueError):
            s.automation_status(registry=self.registry())
        self.assertFalse(self.local.exists())

    def test_incomplete_or_wrong_type_com_registration_is_not_absence(self):
        cases = (
            (FakeRegistry.PROGID_KEY, ("not-a-clsid", 1)),
            (FakeRegistry.PROGID_KEY, (FakeRegistry.CLSID, 2)),
            (FakeRegistry.SERVER_KEY, ("relative.exe", 1)),
            (FakeRegistry.SERVER_KEY, (123, 1)),
            (FakeRegistry.SERVER_KEY, ('"' + str(self.hwp) + '"', 2)),
            (FakeRegistry.SERVER_KEY, None),
        )
        for name, value in cases:
            with self.subTest(name=name, value=value):
                registry = self.registry()
                if value is None:
                    del registry.com[(32, name)]
                else:
                    registry.com[(32, name)] = value
                with self.assertRaises(ValueError):
                    s.automation_status(registry=registry)
                self.assertEqual(registry.creates, [])
        self.assertFalse(self.local.exists())

    def test_other_com_view_is_used_only_when_primary_registration_is_absent(self):
        registry = self.registry(view=64)
        self.assertEqual(s.hwp_executable(registry), self.hwp)
        registry.com[(32, registry.PROGID_KEY)] = (registry.CLSID, registry.REG_SZ)
        with self.assertRaises(ValueError):
            s.automation_status(registry=registry)
        self.assertEqual(registry.creates, [])

    def test_missing_registry_installs_only_approved_root_dll_then_reports_ready(self):
        self.make_zip([(s.DLL_NAME, self.dll), ("src/do-not-run.cmd", b"untrusted data")])
        registry = self.registry()
        self.assertFalse(s.automation_status(registry=registry)["installed"])
        result = self.ensure(registry)
        self.assertTrue(result["auto_installed"])
        self.assertTrue(result["registry_updated"])
        installed = Path(result["dll"])
        self.assertEqual(installed, self.target())
        self.assertEqual(installed.read_bytes(), self.dll)
        self.assertEqual(list(installed.parent.iterdir()), [installed])
        self.assertEqual(registry.writes, [(s.MODULE_ALIAS, str(installed))])
        self.assertTrue(s.automation_status(registry=registry)["installed"])
        self.assertEqual(s.registered_security_module(registry=registry)["dll"], str(installed))
        self.assertFalse(self.ensure(registry)["registry_updated"])
        self.assertEqual(len(registry.writes), 1)

    def test_missing_declared_dll_is_preserved_not_repaired_from_zip_or_cache(self):
        target = self.target()
        target.parent.mkdir(parents=True)
        target.write_bytes(self.dll)
        missing = self.directory / "removed.dll"
        registry = self.registry(str(missing))
        for operation in (s.automation_status, s.registered_security_module, self.ensure):
            with self.subTest(operation=operation):
                with self.assertRaises(FileNotFoundError):
                    operation(registry=registry)
        self.assertEqual(registry.value, str(missing))
        self.assertEqual(registry.writes, [])
        self.assertEqual(registry.creates, [])
        self.assertEqual(target.read_bytes(), self.dll)
        self.assertFalse(missing.exists())

    def test_valid_unregistered_managed_cache_can_be_registered_without_package(self):
        target = self.target()
        target.parent.mkdir(parents=True)
        target.write_bytes(self.dll)
        before = target.stat()
        self.package.unlink()
        registry = self.registry()
        self.assertFalse(s.automation_status(registry=registry)["installed"])
        result = self.ensure(registry)
        self.assertEqual(result["dll"], str(target))
        self.assertFalse(result["auto_installed"])
        self.assertTrue(result["registry_updated"])
        self.assertEqual(target.stat().st_mtime_ns, before.st_mtime_ns)
        self.assertEqual(registry.writes, [(s.MODULE_ALIAS, str(target))])

    def test_unknown_registered_binary_is_never_replaced_or_hidden(self):
        unknown = self.directory / "unknown.dll"
        unknown.write_bytes(b"unknown code")
        for registry in (self.registry(str(unknown)), FakeRegistry(value=str(unknown))):
            operations = (s.automation_status, self.ensure) if registry.com else (s.automation_status,)
            for operation in operations:
                with self.assertRaises(ValueError):
                    operation(registry=registry)
            self.assertEqual(registry.writes, [])
            self.assertEqual(registry.creates, [])
            self.assertEqual(registry.value, str(unknown))
        self.assertEqual(unknown.read_bytes(), b"unknown code")
        self.assertFalse(self.local.exists())

    def test_wrong_type_empty_or_relative_module_registration_stops_unchanged(self):
        for value, kind in (("relative.dll", 1), ("", 1), (123, 1), (str(self.hwp), 2)):
            with self.subTest(value=value, kind=kind):
                registry = self.registry(value, kind)
                for operation in (s.automation_status, s.registered_security_module, self.ensure):
                    with self.assertRaises(ValueError):
                        operation(registry=registry)
                self.assertEqual(registry.value, value)
                self.assertEqual(registry.writes, [])
                self.assertEqual(registry.creates, [])
        self.assertFalse(self.local.exists())

    def test_stale_managed_target_is_not_repaired_or_registered(self):
        target = self.target()
        target.parent.mkdir(parents=True)
        target.write_bytes(b"partial previous failure")
        registry = self.registry()
        for operation in (s.automation_status, self.ensure):
            with self.assertRaises(ValueError):
                operation(registry=registry)
        self.assertEqual(target.read_bytes(), b"partial previous failure")
        self.assertEqual(registry.writes, [])
        self.assertEqual(registry.creates, [])

    def test_hardlinked_registered_and_managed_files_are_preserved_but_rejected(self):
        existing = self.existing()
        alias = self.directory / "hardlink.dll"
        os.link(existing, alias)
        registry = self.registry(str(existing))
        for operation in (s.automation_status, s.registered_security_module, self.ensure):
            with self.assertRaises(ValueError):
                operation(registry=registry)
        target = self.target()
        target.parent.mkdir(parents=True)
        os.link(existing, target)
        with self.assertRaises(ValueError):
            self.ensure(self.registry())
        self.assertEqual(existing.read_bytes(), self.dll)
        self.assertEqual(alias.read_bytes(), self.dll)
        self.assertEqual(target.read_bytes(), self.dll)
        self.assertEqual(registry.writes, [])

    def test_reparse_ancestry_is_rejected_before_install_or_registration(self):
        self.local.mkdir()
        original_lstat = Path.lstat

        def reparse_info(path):
            info = original_lstat(path)
            if path == self.local:
                return SimpleNamespace(st_mode=info.st_mode, st_file_attributes=0x400)
            return info

        registry = self.registry()
        with patch.object(Path, "lstat", reparse_info):
            with self.assertRaises(ValueError):
                s.automation_status(registry=registry)
            with self.assertRaises(ValueError):
                self.ensure(registry)
        self.assertEqual(list(self.local.iterdir()), [])
        self.assertEqual(registry.creates, [])

    def test_linked_segment_cannot_be_hidden_by_parent_normalization(self):
        real = self.directory / "real"
        real.mkdir()
        link = self.directory / "link"
        try:
            link.symlink_to(real, target_is_directory=True)
        except OSError as error:
            self.skipTest("Synthetic symlink creation unavailable: " + str(error))
        with self.assertRaises(ValueError):
            s._canonical_path(link / ".." / s.DLL_NAME)
        existing = self.existing()
        linked_dll = link / ".." / existing.name
        registry = self.registry(str(linked_dll))
        with self.assertRaises(ValueError):
            self.ensure(registry)
        self.assertEqual(existing.read_bytes(), self.dll)
        self.assertEqual(registry.creates, [])

    def test_canonical_registration_preserves_original_compatible_alias_value(self):
        existing = self.existing()
        unused = self.directory / "unused"
        unused.mkdir()
        original = str(unused / ".." / existing.name)
        registry = self.registry(original)
        result = self.ensure(registry)
        self.assertEqual(result["dll"], str(existing))
        self.assertEqual(registry.value, original)
        self.assertEqual(registry.writes, [])
        self.assertFalse(self.local.exists())

    def test_registration_race_after_install_preserves_both_modules(self):
        other = self.existing("other.dll")
        registry = self.registry()
        install = s.install_package

        def competing_registration(target, package):
            result = install(target, package)
            registry.declare(str(other))
            return result

        with patch.object(s, "install_package", side_effect=competing_registration):
            with self.assertRaises(ValueError):
                self.ensure(registry)
        self.assertEqual(registry.value, str(other))
        self.assertEqual(registry.writes, [])
        self.assertEqual(registry.creates, [])
        self.assertEqual(self.target().read_bytes(), self.dll)
        self.assertEqual(other.read_bytes(), self.dll)

    def test_registration_is_rechecked_on_write_handle_without_overwriting_race(self):
        for value in (str(self.directory / "conflicting.dll"), "invalid-relative.dll"):
            registry = self.registry()
            registry.before_create = lambda: registry.declare(value)
            with self.assertRaises(ValueError):
                self.ensure(registry)
            self.assertEqual(registry.value, value)
            self.assertEqual(registry.writes, [])
            self.assertEqual(self.target().read_bytes(), self.dll)

    def test_host_is_rechecked_after_registry_key_open_before_alias_write(self):
        registry = self.registry()

        def incompatible_host():
            self.hwp.write_bytes(fake_pe(machine=0x8664, dll=False))

        registry.before_create = incompatible_host
        with self.assertRaises(s.UnsupportedHwpArchitecture):
            self.ensure(registry)
        self.assertEqual(registry.writes, [])
        self.assertIs(registry.value, _MISSING)
        self.assertEqual(self.target().read_bytes(), self.dll)

    def test_host_change_after_file_install_stops_before_registry_mutation(self):
        registry = self.registry()
        install = s.install_package

        def changed_host(target, package):
            result = install(target, package)
            self.hwp.write_bytes(fake_pe(machine=0x8664, dll=False))
            return result

        with patch.object(s, "install_package", side_effect=changed_host):
            with self.assertRaises(s.UnsupportedHwpArchitecture):
                self.ensure(registry)
        self.assertEqual(registry.creates, [])
        self.assertEqual(self.target().read_bytes(), self.dll)

    def test_new_hardlink_before_registration_stops_without_mutating_registry(self):
        registry = self.registry()
        install = s.install_package

        def hardlinked_target(target, package):
            result = install(target, package)
            os.link(target, self.directory / "raced-hardlink.dll")
            return result

        with patch.object(s, "install_package", side_effect=hardlinked_target):
            with self.assertRaises(ValueError):
                self.ensure(registry)
        self.assertEqual(registry.creates, [])
        self.assertEqual(self.target().read_bytes(), self.dll)

    def test_partial_file_failure_is_retained_and_next_attempt_refuses_repair(self):
        registry = self.registry()
        new_file = setup._new_file

        @contextmanager
        def partial_file(path):
            with new_file(path) as stream:
                def fail_write(data):
                    stream.write(data[:19])
                    raise OSError("synthetic interrupted write")

                yield SimpleNamespace(write=fail_write)

        with patch.object(setup, "_new_file", partial_file):
            with self.assertRaises(OSError):
                self.ensure(registry)
        self.assertEqual(self.target().read_bytes(), self.dll[:19])
        with self.assertRaises(ValueError):
            self.ensure(registry)
        self.assertEqual(self.target().read_bytes(), self.dll[:19])
        self.assertEqual(registry.creates, [])
        self.assertEqual(registry.writes, [])

    def test_registry_failure_retains_valid_file_for_missing_only_retry(self):
        registry = self.registry()

        def fail_set():
            raise OSError("synthetic registry denial")

        registry.before_set = fail_set
        with self.assertRaises(OSError):
            self.ensure(registry)
        self.assertEqual(self.target().read_bytes(), self.dll)
        self.assertIs(registry.value, _MISSING)
        self.assertEqual(registry.writes, [])
        registry.before_set = None
        self.package.unlink()
        result = self.ensure(registry)
        self.assertFalse(result["auto_installed"])
        self.assertTrue(result["registry_updated"])
        self.assertTrue(s.automation_status(registry=registry)["installed"])

    def test_committed_registry_value_is_not_rolled_back_after_reported_failure(self):
        registry = self.registry()
        set_value = registry.SetValueEx

        def committed_then_failed(*args):
            set_value(*args)
            raise OSError("synthetic failure after registry commit")

        with patch.object(registry, "SetValueEx", side_effect=committed_then_failed):
            with self.assertRaises(OSError):
                self.ensure(registry)
        self.assertEqual(registry.value, str(self.target()))
        self.assertEqual(self.target().read_bytes(), self.dll)
        self.assertTrue(s.automation_status(registry=registry)["installed"])
        self.package.unlink()
        self.assertFalse(self.ensure(registry)["registry_updated"])
        self.assertEqual(registry.writes, [(s.MODULE_ALIAS, str(self.target()))])

    def test_no_package_stops_before_directory_or_registry_changes(self):
        self.package.unlink()
        registry = self.registry()
        with self.assertRaises(FileNotFoundError):
            self.ensure(registry)
        self.assertEqual(registry.creates, [])
        self.assertFalse(self.local.exists())

    def test_changed_zip_rejected_before_installation(self):
        self.package.write_bytes(b"replaced archive")
        registry = self.registry()
        with self.assertRaises(ValueError):
            self.ensure(registry)
        self.assertEqual(registry.creates, [])
        self.assertFalse(self.local.exists())

    def test_hardlinked_package_is_not_accepted_as_install_input(self):
        os.link(self.package, self.directory / "linked-package.zip")
        registry = self.registry()
        with self.assertRaises(ValueError):
            self.ensure(registry)
        self.assertEqual(registry.creates, [])
        self.assertFalse(self.local.exists())

    def test_archive_traversal_absolute_and_duplicates_rejected(self):
        for bad in ("../escape.dll", "/escape.dll", "C:/escape.dll", "src/../../escape.dll", s.DLL_NAME.upper()):
            with self.subTest(member=bad):
                self.make_zip([(s.DLL_NAME, self.dll), (bad, b"bad")])
                with self.assertRaises(ValueError):
                    s.read_package(self.package)

    def test_archive_symlink_rejected(self):
        link = zipfile.ZipInfo("src/link")
        link.create_system = 3
        link.external_attr = (stat.S_IFLNK | 0o777) << 16
        self.make_zip([(s.DLL_NAME, self.dll), (link, b"../escape")])
        with self.assertRaises(ValueError):
            s.read_package(self.package)

    def test_no_generic_or_nested_dll_selection(self):
        self.make_zip([("src/" + s.DLL_NAME, self.dll)])
        with self.assertRaises(ValueError):
            s.read_package(self.package)

    def test_dll_machine_and_dll_flag_checked_even_with_matching_digest(self):
        for data in (fake_pe(machine=0x8664), fake_pe(dll=False)):
            with patch.object(s, "DLL_SHA256", s.digest(data)):
                with self.assertRaises(ValueError):
                    s.checked_dll(data)

    def test_rejected_native_registration_never_prepares_or_opens_a_document(self):
        existing = self.existing()
        registry = self.registry(str(existing))
        self.package.unlink()
        with patch.object(s, "_registry", return_value=registry):
            app = Mock()
            app.RegisterModule.return_value = False
            with self.assertRaises(RuntimeError):
                s.register_security_module(app)
            app.Open.assert_not_called()
        self.assertEqual(registry.writes, [])
        self.assertEqual(registry.creates, [])
        self.assertFalse(self.local.exists())
        self.assertEqual(existing.read_bytes(), self.dll)

    def test_native_missing_module_never_registers_or_creates_files(self):
        registry = self.registry()
        app = Mock()
        with patch.object(s, "_registry", return_value=registry):
            with self.assertRaises(RuntimeError):
                s.register_security_module(app)
        app.RegisterModule.assert_not_called()
        self.assertEqual(registry.creates, [])
        self.assertFalse(self.local.exists())
