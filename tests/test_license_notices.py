"""Synthetic license inputs only; no build, download, real document or Native."""

import hashlib
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

import pymupdf

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import hwp_licenses as licenses
import hwp_native_payload as payload


class FakeDistribution:
    def __init__(self, root, name, notices):
        self.root = root
        self.metadata = {"Name": name, "License": "SYNTHETIC LICENSE ONLY"}
        self.version = "1.26.7" if name == "PyMuPDF" else "0.0-SYNTHETIC"
        self.files = []
        for basename in notices:
            self.add_notice(name.lower() + ".dist-info/licenses/" + basename)

    def add_notice(self, relative):
        path = self.root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"SYNTHETIC UPSTREAM NOTICE\n")
        self.files.append(Path(relative))

    def locate_file(self, relative):
        return self.root / relative


class LicenseNoticeTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.source = self.root / "source"
        self.source.mkdir()
        self.environment = self.root / "PRIVATE-ENVIRONMENT-SENTINEL"
        self.environment.mkdir()
        (self.environment / "LICENSE.txt").write_bytes(b"SYNTHETIC PYTHON NOTICE\n")
        (self.source / "LICENSE").write_bytes(b"SYNTHETIC PROJECT NOTICE\n")
        self.pinned = (
            "Tcl-9.0.4-license.terms",
            "Tk-9.0.4-license.terms",
            "MuPDF-1.26.12-NOTICES.txt",
            *licenses.SUPPLEMENTAL_NOTICE_FILES,
        )
        legal = self.source / "packaging/licenses"
        legal.mkdir(parents=True)
        for name in self.pinned:
            (legal / name).write_bytes(b"SYNTHETIC PINNED NOTICE\n")
        self.pins = {
            "pymupdf": {"version": "1.26.7"},
            "mupdf": {"version": "1.26.12"},
            "mupdf_notices": {
                "path": "packaging/licenses/MuPDF-1.26.12-NOTICES.txt",
                "sha256": hashlib.sha256(b"SYNTHETIC PINNED NOTICE\n").hexdigest(),
            },
            "supplemental_notice_files_sha256": {
                name: hashlib.sha256(b"SYNTHETIC PINNED NOTICE\n").hexdigest()
                for name in licenses.SUPPLEMENTAL_NOTICE_FILES
            },
        }
        self.pins_path = self.source / "packaging/third-party-sources.json"
        self.pins_path.write_text(json.dumps(self.pins), encoding="utf-8")
        self.terminal_pins = {"schema": "hwp-native-terminal-dependencies-v1", "selected_files_sha256": {}}
        for key, _, version, _, notices in licenses.TERMINAL_NOTICES:
            self.terminal_pins[key] = {"version": version}
        self.terminal_inputs = tuple(
            relative for relative in payload.COPIED_PAYLOAD_SOURCES.values()
            if relative.startswith((payload.WEBVIEW_SOURCE, payload.TERMINAL_SOURCE + "vendor/"))
        )
        for relative in self.terminal_inputs:
            path = self.source / relative
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(b"SYNTHETIC PINNED TERMINAL INPUT\n")
            self.terminal_pins["selected_files_sha256"][relative] = hashlib.sha256(path.read_bytes()).hexdigest()
        (self.source / "packaging/terminal-dependencies.json").write_text(json.dumps(self.terminal_pins), encoding="utf-8")
        self.renderer_pins = json.loads((Path(payload.__file__).resolve().parents[1] / "packaging/hwpx-renderer-dependencies.json").read_text(encoding="utf-8"))
        for relative in self.renderer_pins["selected_files_sha256"]:
            path = self.source / relative
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(b"SYNTHETIC PINNED HWPX RENDERER INPUT\n")
            if path.name == "package.json":
                path.write_text(json.dumps({"name": "@rhwp/core", "version": payload.RENDERER_VERSION, "license": "MIT"}), encoding="utf-8")
            self.renderer_pins["selected_files_sha256"][relative] = hashlib.sha256(path.read_bytes()).hexdigest()
        (self.source / "packaging/hwpx-renderer-dependencies.json").write_text(json.dumps(self.renderer_pins), encoding="utf-8")
        repository = Path(payload.__file__).resolve().parents[1]
        for relative in (*payload.ICON_ORIGINAL_SHA256, "packaging/icon-dependencies.json"):
            target = self.source / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes((repository / relative).read_bytes())
        self.dependencies = {"contract": "hwp-windows-dependencies-v2", "components": [], "license_files_sha256": {}}
        for key, name, version, _, notices in licenses.SETUP_NOTICES:
            self.dependencies["components"].append({
                "id": key, "name": name, "version": version,
                "license_files": [relative for relative, _ in notices],
            })
            for relative, _ in notices:
                path = self.source / relative
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_bytes(b"SYNTHETIC ORIGINAL SETUP NOTICE\n")
                self.dependencies["license_files_sha256"][relative] = hashlib.sha256(path.read_bytes()).hexdigest()
        (self.source / "packaging/windows-dependencies.json").write_text(json.dumps(self.dependencies), encoding="utf-8")
        (self.source / "packaging/font-glyph-notices.json").write_text('{"contract":"hwp-font-glyph-notices-v1"}', encoding="utf-8")
        self.packages = {
            name: FakeDistribution(self.environment, name, [item for item, _ in notices])
            for name, _, notices in licenses.PACKAGE_NOTICES
        }
        win32 = FakeDistribution(self.environment, "pywin32", ())
        win32.add_notice("win32/license.txt")
        win32.add_notice("win32com/License.txt")
        self.packages["pywin32"] = win32
        for current in (
            patch.object(licenses, "distribution", side_effect=self.packages.__getitem__),
            patch.object(licenses.sys, "base_prefix", str(self.environment)),
            patch.object(pymupdf, "VersionBind", "1.26.7"),
            patch.object(pymupdf, "VersionFitz", "1.26.12"),
        ):
            current.start()
            self.addCleanup(current.stop)
        self.output = self.root / "NEW-notices"

    def collect(self):
        return licenses.write_licenses(
            self.source, self.output, self.pinned, app_version="0.0.1-SYNTHETIC"
        )

    def test_vendored_notices_do_not_block_the_privacy_safe_unverified_index(self):
        self.packages["setuptools"].add_notice(
            "setuptools/_vendor/vendor.dist-info/LICENSE"
        )
        self.collect()
        raw = (self.output / "THIRD-PARTY-LICENSES.json").read_text(encoding="utf-8")
        index = json.loads(raw)
        self.assertNotIn("PRIVATE-ENVIRONMENT-SENTINEL", raw)
        self.assertNotIn(str(self.root), raw)
        self.assertFalse(index["redistribution_ready"])
        self.assertEqual(index["corresponding_source_provision"], "not_verified")

    def test_lucide_missing_original_blocks_notice_index(self):
        (self.source / (payload.ICON_SOURCE + "files.svg")).unlink()
        with self.assertRaises(FileNotFoundError):
            self.collect()
        self.assertFalse((self.output / "THIRD-PARTY-LICENSES.json").exists())

    def test_ambiguous_installed_license_cannot_select_an_arbitrary_copy(self):
        self.packages["PyMuPDF"].add_notice("other.dist-info/COPYING")
        with self.assertRaises(RuntimeError):
            self.collect()
        self.assertTrue(self.output.is_dir())
        self.assertFalse((self.output / "THIRD-PARTY-LICENSES.json").exists())

    def test_renderer_version_drift_cannot_emit_a_license_index(self):
        with patch.object(pymupdf, "VersionFitz", "1.26.13"):
            with self.assertRaises(RuntimeError):
                self.collect()
        self.assertFalse((self.output / "THIRD-PARTY-LICENSES.json").exists())

    def test_changed_pinned_notice_cannot_emit_a_license_index(self):
        (self.source / self.pins["mupdf_notices"]["path"]).write_bytes(b"CHANGED NOTICE")
        with self.assertRaises(RuntimeError):
            self.collect()
        self.assertFalse((self.output / "THIRD-PARTY-LICENSES.json").exists())

    def test_changed_terminal_notice_cannot_emit_an_index(self):
        relative = licenses.TERMINAL_NOTICES[0][4][0][0]
        (self.source / relative).write_bytes(b"CHANGED SDK NOTICE")
        with self.assertRaises(RuntimeError):
            self.collect()
        self.assertFalse((self.output / "THIRD-PARTY-LICENSES.json").exists())

    def test_changed_terminal_component_version_cannot_emit_an_index(self):
        self.terminal_pins["webview2"]["version"] = "0.0-UNKNOWN"
        (self.source / "packaging/terminal-dependencies.json").write_text(json.dumps(self.terminal_pins), encoding="utf-8")
        with self.assertRaises(RuntimeError):
            self.collect()
        self.assertFalse((self.output / "THIRD-PARTY-LICENSES.json").exists())

    def test_changed_terminal_dependency_cannot_be_packaged_as_pinned(self):
        names = ("Microsoft.Web.WebView2.Core.dll", "vendor/xterm/xterm.js",
                 "vendor/xterm/xterm.css", "vendor/xterm/package.json")
        for index, name in enumerate(names):
            relative = next(path for path in self.terminal_inputs if path.endswith("/" + name))
            path = self.source / relative
            original = path.read_bytes()
            self.output = self.root / ("changed-terminal-" + str(index))
            with self.subTest(component=name):
                path.write_bytes(original + b"ALTERED UPSTREAM BYTES")
                with self.assertRaises(RuntimeError):
                    self.collect()
                self.assertFalse((self.output / "THIRD-PARTY-LICENSES.json").exists())
            path.write_bytes(original)

    def test_missing_dependency_pin_cannot_emit_a_license_index(self):
        relative = next(path for path in self.terminal_inputs if path.endswith("/vendor/xterm/xterm.js"))
        del self.terminal_pins["selected_files_sha256"][relative]
        (self.source / "packaging/terminal-dependencies.json").write_text(json.dumps(self.terminal_pins), encoding="utf-8")
        with self.assertRaises(RuntimeError):
            self.collect()
        self.assertFalse((self.output / "THIRD-PARTY-LICENSES.json").exists())



    def test_changed_hwpx_renderer_dependency_cannot_emit_a_license_index(self):
        for index, relative in enumerate(self.renderer_pins["selected_files_sha256"]):
            path = self.source / relative
            original = path.read_bytes()
            self.output = self.root / ("changed-renderer-" + str(index))
            with self.subTest(component=relative):
                path.write_bytes(original + b"ALTERED UPSTREAM BYTES")
                with self.assertRaises(ValueError):
                    self.collect()
                self.assertFalse((self.output / "THIRD-PARTY-LICENSES.json").exists())
            path.write_bytes(original)

    def test_changed_required_original_notice_cannot_emit_an_index(self):
        relative = licenses.SETUP_NOTICES[1][4][1][0]
        (self.source / relative).write_bytes(b"CHANGED ORIGINAL ICON TERMS")
        with self.assertRaises(RuntimeError):
            self.collect()
        self.assertFalse((self.output / "THIRD-PARTY-LICENSES.json").exists())

    def test_changed_supplemental_runtime_notice_cannot_emit_an_index(self):
        name = licenses.SUPPLEMENTAL_NOTICE_FILES[0]
        (self.source / "packaging/licenses" / name).write_bytes(b"CHANGED RUNTIME TERMS")
        with self.assertRaises(RuntimeError):
            self.collect()
        self.assertFalse((self.output / "THIRD-PARTY-LICENSES.json").exists())

    def test_missing_supplemental_notice_pin_cannot_emit_an_index(self):
        del self.pins["supplemental_notice_files_sha256"][licenses.SUPPLEMENTAL_NOTICE_FILES[0]]
        self.pins_path.write_text(json.dumps(self.pins), encoding="utf-8")
        with self.assertRaises(RuntimeError):
            self.collect()
        self.assertFalse((self.output / "THIRD-PARTY-LICENSES.json").exists())

    def test_existing_owner_directory_is_never_overwritten_or_reused(self):
        self.output.mkdir()
        sentinel = self.output / "OWNER.txt"
        sentinel.write_bytes(b"PRESERVE OWNER FILE")
        with self.assertRaises(FileExistsError):
            self.collect()
        self.assertEqual(sentinel.read_bytes(), b"PRESERVE OWNER FILE")
        self.assertEqual({path.name for path in self.output.iterdir()}, {"OWNER.txt"})


if __name__ == "__main__":
    unittest.main()
