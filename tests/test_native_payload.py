"""Synthetic byte/protocol fixtures only; never execute dotnet, WPF or a build."""

import copy
import json
import os
from pathlib import Path
import shutil
import subprocess
from types import SimpleNamespace
import sys
import tempfile
import unittest
from unittest.mock import patch
import zipfile

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import build_native as builder
import hwp_build_source as source_archive
import hwp_native_payload as payload


def write_runtime(directory, source_root=None):
    directory.mkdir(exist_ok=True)
    (directory / "HWPReview.Native.dll").write_bytes(b"SYNTHETIC NOT AN ASSEMBLY")
    for name, source in payload.COPIED_PAYLOAD_SOURCES.items():
        target = directory / name
        target.parent.mkdir(parents=True, exist_ok=True)
        if source_root is None:
            target.write_text("SYNTHETIC SOURCE INPUT: " + source, encoding="utf-8")
        else:
            shutil.copyfile(source_root / source, target)
    (directory / "HWPReview.Native.runtimeconfig.json").write_text(json.dumps({
        "runtimeOptions": {
            "tfm": "net10.0", "rollForward": "LatestPatch",
            "frameworks": [
                {"name": "Microsoft.NETCore.App", "version": "10.0.0"},
                {"name": "Microsoft.WindowsDesktop.App", "version": "10.0.0"},
            ],
        },
    }), encoding="utf-8")
    target = ".NETCoreApp,Version=v10.0/win-x64"
    application = "HWPReview.Native/" + payload.APP_VERSION
    references = {
        "Microsoft.Web.WebView2.Core": "Microsoft.Web.WebView2.Core.dll",
        "Microsoft.Web.WebView2.Wpf": "Microsoft.Web.WebView2.Wpf.dll",
    }
    entries = {
        application: {
            "runtime": {"HWPReview.Native.dll": {}},
            "dependencies": {name: payload.WEBVIEW_VERSION for name in references},
        },
    }
    libraries = {application: {"type": "project"}}
    for name, filename in references.items():
        key = name + "/" + payload.WEBVIEW_VERSION
        entries[key] = {"runtime": {filename: {"assemblyVersion": payload.WEBVIEW_VERSION, "fileVersion": payload.WEBVIEW_VERSION}}}
        libraries[key] = {"type": "reference"}
    (directory / "HWPReview.Native.deps.json").write_text(json.dumps({
        "runtimeTarget": {"name": target}, "targets": {target: entries}, "libraries": libraries,
    }), encoding="utf-8")


class NativePayloadTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name).resolve()
        scripts = Path(payload.__file__).resolve().parent
        for name in payload.NATIVE_SOURCE_FILES:
            file = self.root / name
            file.parent.mkdir(parents=True, exist_ok=True)
            if name.startswith("scripts/"):
                shutil.copyfile(scripts / file.name, file)
            elif name.startswith(payload.ICON_SOURCE) or name == "packaging/icon-dependencies.json":
                shutil.copyfile(scripts.parent / name, file)
            elif name.endswith("NuGet.Config"):
                file.write_text("<configuration><packageSources><clear /></packageSources></configuration>", encoding="utf-8")
            else:
                file.write_text("SYNTHETIC SOURCE INPUT: " + name, encoding="utf-8")
        pins = json.loads((scripts.parent / "packaging/hwpx-renderer-dependencies.json").read_text(encoding="utf-8"))
        package = self.root / (payload.RENDERER_SOURCE + "vendor/rhwp/package.json")
        package.write_text(json.dumps({"name": "@rhwp/core", "version": payload.RENDERER_VERSION, "license": "MIT"}), encoding="utf-8")
        pins["selected_files_sha256"] = {name: payload._sha256(self.root / name) for name in pins["selected_files_sha256"]}
        (self.root / "packaging/hwpx-renderer-dependencies.json").write_text(json.dumps(pins), encoding="utf-8")
        self.directory = self.root / "build" / "fresh" / "native-ui"
        self.directory.parent.mkdir(parents=True)
        write_runtime(self.directory, self.root)
        self.manifest = {
            "contract": payload.CONTRACT, "version": payload.APP_VERSION,
            "framework_dependent": True, "target_framework": "net10.0-windows",
            "runtime_requirement": dict(payload.RUNTIME_REQUIREMENT),
            "files_sha256": {name: payload._sha256(self.directory / name) for name in payload.PAYLOAD_FILES},
            "source_sha256": payload.source_hashes(self.root),
        }
        self.save_manifest()

    def save_manifest(self):
        (self.directory / "manifest.json").write_text(json.dumps(self.manifest), encoding="utf-8")

    def locate_source(self):
        with patch.object(payload, "frozen", return_value=False), patch.object(payload, "resource_root", return_value=self.root):
            return payload.locate_payload()

    def test_source_registration_revalidates_every_compilation_input(self):
        builder._register_source(self.root, self.directory)
        self.assertEqual(self.locate_source(), self.directory)
        for name in payload.NATIVE_SOURCE_FILES:
            file = self.root / name
            previous = file.read_bytes()
            try:
                file.write_bytes(previous + b"\nCHANGED SOURCE")
                with self.subTest(input=name), self.assertRaises(ValueError):
                    self.locate_source()
            finally:
                file.write_bytes(previous)
        self.assertEqual(self.locate_source(), self.directory)

    def test_missing_registration_never_searches_or_builds(self):
        with patch.object(builder, "build_native") as build:
            with self.assertRaises(FileNotFoundError):
                self.locate_source()
            build.assert_not_called()

    def test_frozen_payload_has_no_source_tree_dependency_but_checks_bytes(self):
        frozen_root = self.root / "relocated-extraction"
        frozen_root.mkdir()
        relocated = frozen_root / "native-ui"
        shutil.copytree(self.directory, relocated)
        shutil.rmtree(self.root / "native")
        with patch.object(payload, "frozen", return_value=True), patch.object(payload, "resource_root", return_value=frozen_root):
            self.assertEqual(payload.locate_payload(), relocated)
            (relocated / payload.PAYLOAD_FILES[0]).write_bytes(b"TAMPERED")
            with self.assertRaisesRegex(ValueError, "bytes changed"):
                payload.locate_payload()

    def test_closed_manifest_and_payload_membership(self):
        original = copy.deepcopy(self.manifest)
        for change in (
            lambda m: m.update(extra="unexpected"),
            lambda m: m.update(version="0.0.0"),
            lambda m: m.update(framework_dependent=False),
            lambda m: m["runtime_requirement"].update(roll_forward="Major"),
            lambda m: m["files_sha256"].update({"../outside.exe": "0" * 64}),
            lambda m: m["source_sha256"].pop(payload.NATIVE_SOURCE_FILES[0]),
        ):
            self.manifest = copy.deepcopy(original)
            change(self.manifest)
            self.save_manifest()
            with self.assertRaises(ValueError):
                payload.verify_payload(self.directory)
        self.manifest = original
        self.save_manifest()
        (self.directory / "coreclr.dll").write_bytes(b"NO BUNDLED RUNTIME")
        with self.assertRaisesRegex(ValueError, "unexpected"):
            payload.verify_payload(self.directory)
        (self.directory / "coreclr.dll").unlink()
        raw = (self.directory / "manifest.json").read_text(encoding="utf-8")
        (self.directory / "manifest.json").write_text('{"version":"0.0.0",' + raw[1:], encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "Duplicate"):
            payload.verify_payload(self.directory)

    def test_runtime_change_is_rejected_even_when_rehashed(self):
        file = self.directory / "HWPReview.Native.runtimeconfig.json"
        config = json.loads(file.read_text(encoding="utf-8"))
        config["runtimeOptions"]["rollForward"] = "Major"
        file.write_text(json.dumps(config), encoding="utf-8")
        self.manifest["files_sha256"][file.name] = payload._sha256(file)
        self.save_manifest()
        with self.assertRaisesRegex(ValueError, "installed .NET 10"):
            payload.verify_payload(self.directory)

    def test_dependency_cannot_probe_an_outside_assembly_even_when_rehashed(self):
        file = self.directory / "HWPReview.Native.deps.json"
        dependencies = json.loads(file.read_text(encoding="utf-8"))
        target = dependencies["runtimeTarget"]["name"]
        application = "HWPReview.Native/" + payload.APP_VERSION
        dependencies["targets"][target][application]["runtime"] = {"../outside.dll": {}}
        file.write_text(json.dumps(dependencies), encoding="utf-8")
        self.manifest["files_sha256"][file.name] = payload._sha256(file)
        self.save_manifest()
        with self.assertRaises(ValueError):
            payload.verify_payload(self.directory)

    def test_copied_sdk_or_terminal_bytes_cannot_mix_source_even_with_manifest_rehash(self):
        for name in ("Microsoft.Web.WebView2.Core.dll", "terminal/vendor/xterm/xterm.js", "bridge/omp-bridge.mjs", "viewer/vendor/rhwp/rhwp.js", "viewer/vendor/rhwp/rhwp_bg.wasm", "viewer/svg-safety.js", "hwpx-renderer-dependencies.json"):
            with self.subTest(name=name):
                target = self.directory / name
                original = target.read_bytes()
                try:
                    target.write_bytes(original + b"TAMPERED")
                    self.manifest["files_sha256"][name] = payload._sha256(target)
                    self.save_manifest()
                    with self.assertRaises(ValueError):
                        payload.verify_payload(self.directory)
                finally:
                    target.write_bytes(original)
                    self.manifest["files_sha256"][name] = payload._sha256(target)
                    self.save_manifest()

    def test_unknown_nested_asset_directory_and_reference_library_are_rejected(self):
        unexpected = self.directory / "terminal" / "foreign"
        unexpected.mkdir()
        with self.assertRaises(ValueError):
            payload.verify_payload(self.directory)
        unexpected.rmdir()
        target = self.directory / "HWPReview.Native.deps.json"
        deps = json.loads(target.read_text(encoding="utf-8"))
        deps["libraries"]["Unapproved.Native.Dependency/1.0.0"] = {"type": "reference"}
        target.write_text(json.dumps(deps), encoding="utf-8")
        self.manifest["files_sha256"][target.name] = payload._sha256(target)
        self.save_manifest()
        with self.assertRaises(ValueError):
            payload.verify_payload(self.directory)


    def test_pointer_cannot_escape_owned_build_tree(self):
        pointer = self.root / "build/native-current.json"
        for name in ("../native-ui", "build/../native-ui", "build\\fresh\\native-ui", str(self.directory), "native/native-ui", "build/fresh/native-ui:stream"):
            pointer.write_text(json.dumps({"contract": payload.CURRENT_CONTRACT, "path": name}), encoding="utf-8")
            with self.subTest(path=name), self.assertRaises(ValueError):
                self.locate_source()

    def test_directory_cannot_replace_regular_runtime_file(self):
        file = self.directory / "HWPReview.Native.dll"
        file.unlink()
        file.mkdir()
        with self.assertRaises(ValueError):
            payload.verify_payload(self.directory)

    def test_hardlinked_payload_is_rejected(self):
        original = self.directory / payload.PAYLOAD_FILES[0]
        backup = self.root / "linked-executable"
        os.link(original, backup)
        with self.assertRaisesRegex(ValueError, "hard links"):
            payload.verify_payload(self.directory)

    def test_linked_payload_or_parent_is_rejected(self):
        linked = self.root / "linked-native-ui"
        try:
            linked.symlink_to(self.directory, target_is_directory=True)
        except OSError as exc:
            self.skipTest("Symlink creation is unavailable: " + str(exc))
        with self.assertRaisesRegex(ValueError, "links/reparse"):
            payload.verify_payload(linked)

    def test_offline_builder_compiles_snapshot_and_archives_only_exact_inputs(self):
        destination = self.root / "build/new/native-ui"
        dotnet = self.root / "synthetic-dotnet.exe"
        dotnet.write_bytes(b"SYNTHETIC NEVER EXECUTED")
        private = self.root / "private/confidential.hwpx"
        private.parent.mkdir()
        private.write_bytes(b"PRIVATE SYNTHETIC SENTINEL")

        def fake_run(command, *, work, environment):
            self.assertEqual(payload.source_hashes(work), self.manifest["source_sha256"])
            self.assertFalse((work / "private").exists())
            for key in ("DOTNET_CLI_HOME", "NUGET_PACKAGES", "NUGET_HTTP_CACHE_PATH", "USERPROFILE"):
                self.assertTrue(Path(environment[key]).is_relative_to(work.parent))
            if command[1] == "--version":
                return SimpleNamespace(stdout=builder.SDK_VERSION + "\n")
            if command[1] == "publish":
                write_runtime(Path(command[command.index("--output") + 1]), work)
            return SimpleNamespace(stdout="")

        with patch.object(builder.sys, "platform", "win32"), patch.object(builder.platform, "machine", return_value="AMD64"), patch.object(builder.shutil, "which", return_value=str(dotnet)), patch.object(builder, "_run", side_effect=fake_run):
            manifest = builder.build_native(self.root, destination)
            self.assertEqual(manifest["source_sha256"], self.manifest["source_sha256"])
            with self.assertRaises(FileExistsError):
                builder.build_native(self.root, destination)
        archive = self.root / "source.zip"
        source_archive.write_source_archive(self.root, manifest["source_sha256"], archive, version=payload.APP_VERSION)
        with zipfile.ZipFile(archive) as package:
            self.assertNotIn("private/confidential.hwpx", package.namelist())
            self.assertFalse(any(name.startswith("build/") for name in package.namelist()))
            relocated = self.root / "relocated-source"
            package.extractall(relocated)
        self.assertEqual(payload.source_hashes(relocated), manifest["source_sha256"])
        self.assertEqual(payload.verify_payload(destination, source_root=relocated), manifest)

    def test_failed_compilation_preserves_inputs_and_diagnostics(self):
        destination = self.root / "build/failed/native-ui"
        dotnet = self.root / "synthetic-dotnet.exe"
        dotnet.write_bytes(b"SYNTHETIC NEVER EXECUTED")

        def fail_restore(command, **kwargs):
            if command[1] == "--version":
                return subprocess.CompletedProcess(command, 0, builder.SDK_VERSION + "\n")
            raise subprocess.CalledProcessError(1, command, output="SYNTHETIC COMPILER DIAGNOSTIC")

        with (
            patch.object(builder.shutil, "which", return_value=str(dotnet)),
            patch.object(builder.subprocess, "run", side_effect=fail_restore),
            self.assertRaises(subprocess.CalledProcessError),
        ):
            builder.build_native(self.root, destination)
        workspaces = list(destination.parent.glob(".native-build-*"))
        self.assertEqual(len(workspaces), 1)
        self.assertEqual(payload.source_hashes(workspaces[0] / "source"), self.manifest["source_sha256"])
        logs = list(workspaces[0].glob("*.log"))
        self.assertTrue(any("SYNTHETIC COMPILER DIAGNOSTIC" in item.read_text(encoding="utf-8") for item in logs))

    def test_build_environment_cannot_import_unrecorded_msbuild_inputs(self):
        work = self.root / "isolated-build"
        work.mkdir()
        injected = {
            "CustomBeforeMicrosoftCommonTargets": "UNRECORDED-OUTSIDE.targets",
            "CustomAfterMicrosoftCommonTargets": "UNRECORDED-AFTER.targets",
            "DirectoryBuildPropsPath": "UNRECORDED.props",
            "RoslynTargetsPath": "UNRECORDED-ROSLYN",
            "NUGET_PLUGIN_PATHS": "UNRECORDED-PLUGIN",
        }
        with patch.dict(os.environ, injected):
            environment = builder._owned_environment(work)
        self.assertTrue({key.upper() for key in injected}.isdisjoint(key.upper() for key in environment))
        self.assertTrue(Path(environment["USERPROFILE"]).is_relative_to(work))

    def test_online_nuget_source_is_rejected_before_restore(self):
        config = self.root / "native/NuGet.Config"
        config.write_text('<configuration><packageSources><clear/><add key="network" value="https://example.invalid"/></packageSources></configuration>', encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "clear every"):
            builder._require_offline_config(config)


if __name__ == "__main__":
    unittest.main()
