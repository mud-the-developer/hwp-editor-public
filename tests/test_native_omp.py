"""Deterministic native launch/session boundaries; never starts an executable."""
import hashlib
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import PropertyMock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import hwp_omp_tui as omp


class NativeOmpTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name).resolve()
        self.bun = self.root / "bun"
        self.exe = self.bun / "bin/omp.exe"
        self.manifest = self.root / "omp-runtime.json"
        metadata = {
            omp.CLI_ROOT: {"name": "@oh-my-pi/pi-coding-agent", "version": omp.VERSION,
                           "type": "module", "bin": {"omp": "dist/cli.js"},
                           "dependencies": {"@oh-my-pi/pi-natives": omp.VERSION}},
            omp.NATIVES_ROOT: {"name": "@oh-my-pi/pi-natives", "version": omp.VERSION,
                               "type": "module", "main": "./native/index.js",
                               "exports": {".": {"types": "./native/index.d.ts", "import": "./native/index.js",
                                                 "default": "./native/index.js"}},
                               "optionalDependencies": {"@oh-my-pi/pi-natives-win32-x64": omp.VERSION}},
            omp.LEAF_ROOT: {"name": "@oh-my-pi/pi-natives-win32-x64", "version": omp.VERSION,
                            "main": "./" + omp.ADDON_NAME, "os": ["win32"], "cpu": ["x64"]},
        }
        for name in omp.RUNTIME_FILES:
            path = self.bun / name
            path.parent.mkdir(parents=True, exist_ok=True)
            package = metadata.get(name.removesuffix("/package.json"))
            path.write_bytes(json.dumps(package).encode() if package else b"SYNTHETIC-NOT-EXECUTABLE:" + name.encode())
        for root in (omp.CLI_ROOT, omp.NATIVES_ROOT, omp.LEAF_ROOT):
            for name in ("LICENSE", "README.md", "THIRD-PARTY-NOTICES.txt"):
                (self.bun / root / name).write_text("SYNTHETIC inert notice", encoding="utf-8")
        (self.bun / omp.CLI_ROOT / "CHANGELOG.md").write_text("SYNTHETIC", encoding="utf-8")
        for name in ("src", "scripts", "examples", "dist/types"):
            (self.bun / omp.CLI_ROOT / name).mkdir(parents=True, exist_ok=True)
        for name in omp.NATIVE_TYPES:
            (self.bun / omp.NATIVES_ROOT / "native" / name).write_text("SYNTHETIC type only", encoding="utf-8")
        for name, value in (("LAUNCHER_SHA256", hashlib.sha256(self.exe.read_bytes()).hexdigest()),
                            ("RUNTIME_MANIFEST", self.manifest)):
            pin = patch.object(omp, name, value)
            pin.start()
            self.addCleanup(pin.stop)
        self.manifest_pin = patch.object(omp, "RUNTIME_MANIFEST_SHA256", "")
        self.manifest_pin.start()
        self.addCleanup(self.manifest_pin.stop)
        self.rebaseline()
        deny = patch("subprocess.Popen", side_effect=AssertionError("No process/model startup"))
        deny.start()
        self.addCleanup(deny.stop)
        self.helper = omp.OmpTui({"synthetic": True}, root=self.root / "owned", executable=self.exe)

    def rebaseline(self):
        """Test-only reviewed fixture; never accept installed bytes in production."""
        self.baseline = {
            "schema": "hwp-omp-runtime-baseline-v1", "package_version": omp.VERSION,
            "files": [{"relative_path": name, "size": (self.bun / name).stat().st_size,
                       "sha256": hashlib.sha256((self.bun / name).read_bytes()).hexdigest()}
                      for name in omp.RUNTIME_FILES],
            "native_addon_relative_path": omp.ADDON_RELATIVE,
        }
        self.pin_manifest(json.dumps(self.baseline).encode())

    def pin_manifest(self, raw):
        self.manifest.write_bytes(raw)
        omp.RUNTIME_MANIFEST_SHA256 = hashlib.sha256(raw).hexdigest()

    def prepare(self):
        self.launch = self.helper.prepare()
        self.identifier = self.launch["launch_id"]
        return self.launch

    

    

    


    def test_managed_runtime_does_not_adopt_user_bun_or_copy_user_auth(self):
        user = self.root / "user-home"
        launcher = user / ".bun/bin/omp.exe"
        launcher.parent.mkdir(parents=True)
        launcher.write_bytes(b"USER OWNED DIFFERENT CLI")
        auth = user / ".omp/auth.json"
        auth.parent.mkdir()
        auth.write_bytes(b"SYNTHETIC USER AUTH; NEVER COPY")
        private = self.root / "managed-owned"
        with patch.object(omp, "managed_runtime_root", return_value=self.bun), patch.object(Path, "home", return_value=user):
            helper = omp.OmpTui({"synthetic": True}, root=private)
            launch = helper.prepare()
        self.assertEqual(Path(launch["runtime"]["bun_root"]), self.bun)
        self.assertEqual(launcher.read_bytes(), b"USER OWNED DIFFERENT CLI")
        self.assertEqual(auth.read_bytes(), b"SYNTHETIC USER AUTH; NEVER COPY")
        self.assertFalse(list(private.rglob("auth*")))
        self.assertEqual(helper.release(launch["launch_id"]), {"released": True})

    def test_corrupt_managed_runtime_is_preserved_without_global_fallback(self):
        damaged = self.bun / "bin/bun.exe"
        damaged.write_bytes(b"CORRUPT MANAGED RUNTIME")
        private = self.root / "managed-invalid"
        with patch.object(omp, "managed_runtime_root", return_value=self.bun), patch.object(Path, "home", side_effect=AssertionError("No fallback after managed admission failure")):
            helper = omp.OmpTui({"synthetic": True}, root=private)
            with self.assertRaises(ValueError):
                helper.prepare()
        self.assertEqual(damaged.read_bytes(), b"CORRUPT MANAGED RUNTIME")
        self.assertFalse((private / "launches").exists())

    def test_absent_managed_runtime_reuses_valid_existing_user_installation(self):
        user = self.root / "global-user"
        user.mkdir()
        global_root = user / ".bun"
        self.bun.rename(global_root)
        managed = self.root / "not-installed-managed"
        with patch.object(omp, "managed_runtime_root", return_value=managed), patch.object(Path, "home", return_value=user):
            helper = omp.OmpTui({"synthetic": True}, root=self.root / "global-owned")
            launch = helper.prepare()
        self.assertEqual(Path(launch["runtime"]["bun_root"]), global_root)
        self.assertFalse(managed.exists())
        self.assertTrue((global_root / omp.ADDON_RELATIVE).is_file())
        helper.release(launch["launch_id"])

    def test_selected_workspace_is_actual_cwd_with_only_project_guidance_added(self):
        workspace = self.root / "selected"
        workspace.mkdir()
        original = workspace / "original.txt"
        original.write_bytes(b"USER ORIGINAL")
        before = {p.relative_to(workspace): p.read_bytes() for p in workspace.rglob("*") if p.is_file()}
        launch = self.helper.prepare(workspace=workspace)
        self.assertEqual(Path(launch["working_directory"]), workspace)
        args = launch["arguments"]
        self.assertEqual(args[args.index("--cwd") + 1], str(workspace))
        private = self.root / "owned"
        launch_root = Path(launch["launch_directory"])
        self.assertEqual(launch_root, private / "launches" / launch["launch_id"])
        for key in ("profile_directory", "session_directory", "webview_user_data_directory"):
            self.assertTrue(Path(launch[key]).is_relative_to(private))
        for key in ("HOME", "USERPROFILE", "APPDATA", "LOCALAPPDATA", "XDG_CONFIG_HOME", "TEMP", "TMP"):
            self.assertTrue(Path(launch["environment"][key]).is_relative_to(private))
        self.assertTrue(Path(launch["bridge"]["extension_path"]).is_relative_to(launch_root))
        after = {p.relative_to(workspace): p.read_bytes() for p in workspace.rglob("*") if p.is_file()}
        self.assertEqual({key: after[key] for key in before}, before)
        self.assertEqual(set(workspace.iterdir()), {original, workspace / "HWPReview.md"})
        self.helper.release(launch["launch_id"])
        other = self.root / "other-selected"
        other.mkdir()
        second = self.helper.prepare(workspace=other)
        self.assertEqual(Path(second["working_directory"]), other)
        self.assertNotEqual(launch["runtime_sha256"], second["runtime_sha256"])
        self.assertTrue(Path(launch["session_directory"]).is_dir())

    def test_project_guidance_is_created_once_and_user_edits_survive_release(self):
        workspace = self.root / "selected"
        workspace.mkdir()
        guide = workspace / "HWPReview.md"
        self.assertFalse(guide.exists())
        first = self.helper.prepare(workspace=workspace)
        user_bytes = b"\xef\xbb\xbf# User guidance\r\nKeep my edits.\r\n"
        guide.write_bytes(user_bytes)
        identity = guide.stat().st_ino
        self.helper.release(first["launch_id"])
        with patch.object(omp, "PROJECT_GUIDANCE", "Changed release defaults"):
            second = self.helper.prepare(workspace=workspace)
        self.assertEqual(guide.read_bytes(), user_bytes)
        self.assertEqual(guide.stat().st_ino, identity)
        self.assertNotEqual(first["launch_id"], second["launch_id"])

    def test_existing_project_guidance_is_preserved_on_first_prepare(self):
        workspace = self.root / "selected"
        workspace.mkdir()
        guide = workspace / "HWPReview.md"
        contents = b"# Existing\r\nUser-owned markdown.\r\n"
        guide.write_bytes(contents)
        self.helper.prepare(workspace=workspace)
        self.assertEqual(guide.read_bytes(), contents)

    def test_linked_project_guidance_is_rejected_without_changing_target(self):
        workspace = self.root / "selected"
        workspace.mkdir()
        original = self.root / "original-guidance.md"
        original.write_bytes(b"USER ORIGINAL")
        try:
            (workspace / "HWPReview.md").symlink_to(original)
        except OSError as error:
            self.skipTest("Symlink unavailable: " + str(error))
        with self.assertRaises(ValueError):
            self.helper.prepare(workspace=workspace)
        self.assertEqual(original.read_bytes(), b"USER ORIGINAL")

    def test_hardlinked_project_guidance_is_rejected_without_changes(self):
        workspace = self.root / "selected"
        workspace.mkdir()
        original = self.root / "original-guidance.md"
        original.write_bytes(b"USER ORIGINAL")
        try:
            os.link(original, workspace / "HWPReview.md")
        except OSError as error:
            self.skipTest("Hardlink unavailable: " + str(error))
        with self.assertRaises(ValueError):
            self.helper.prepare(workspace=workspace)
        self.assertEqual(original.read_bytes(), b"USER ORIGINAL")

    def test_rootless_login_uses_only_private_fallback(self):
        launch = self.prepare()
        self.assertEqual(Path(launch["working_directory"]), Path(launch["launch_directory"]) / "workspace")

    def test_unsafe_selected_roots_are_rejected_without_creation(self):
        missing = self.root / "missing"
        file = self.root / "not-folder"
        file.write_bytes(b"UNCHANGED")
        managed = self.root / ".hwpreview"
        managed.mkdir()
        report = self.root / "report"
        report.mkdir()
        (report / "report-project.json").write_text("{}", encoding="utf-8")
        owned = self.root / "owned"
        owned.mkdir()
        for root in (missing, file, managed, report, owned, self.bun, self.root, Path("relative")):
            with self.subTest(root=root), self.assertRaises((ValueError, OSError)):
                self.helper.prepare(workspace=root)
        self.assertFalse(missing.exists())
        self.assertFalse((owned / "launches").exists())
        self.assertEqual(file.read_bytes(), b"UNCHANGED")

    def test_linked_selected_root_is_rejected_without_launch(self):
        workspace = self.root / "selected"
        workspace.mkdir()
        alias = self.root / "linked-selected"
        try:
            alias.symlink_to(workspace, target_is_directory=True)
        except OSError as error:
            self.skipTest("Symlink unavailable: " + str(error))
        with self.assertRaises(ValueError):
            self.helper.prepare(workspace=alias)
        self.assertFalse((self.root / "owned/launches").exists())

    def test_bridge_asset_identity_and_fresh_launch_secrets_are_not_reused(self):
        first = self.prepare()
        self.helper.release(self.identifier)
        second = self.prepare()
        self.assertNotEqual(first["bridge"]["pipe_name"], second["bridge"]["pipe_name"])
        self.assertNotEqual(first["bridge"]["token"], second["bridge"]["token"])
        self.assertNotEqual(first["runtime_sha256"], second["runtime_sha256"])
        self.helper.release(self.identifier)
        asset = self.root / "changed-application-bridge.mjs"
        asset.write_bytes(b"export default function changed() {}")
        with patch.object(omp, "BRIDGE_SOURCE", asset), self.assertRaises(ValueError):
            self.helper.prepare()
        self.assertTrue(Path(first["session_directory"]).is_dir())

    def test_bridge_source_link_and_empty_asset_are_rejected_before_launch(self):
        asset = self.root / "untrusted-bridge.mjs"
        asset.write_bytes(b"")
        with patch.object(omp, "BRIDGE_SOURCE", asset), self.assertRaises(ValueError):
            self.helper.prepare()
        asset.write_bytes(b"export default function inert() {}")
        alias = self.root / "bridge-hardlink.mjs"
        try:
            os.link(asset, alias)
        except OSError as error:
            self.skipTest("Hardlink unavailable: " + str(error))
        with patch.object(omp, "BRIDGE_SOURCE", asset), self.assertRaises(ValueError):
            self.helper.prepare()
        self.assertFalse((self.root / "owned/launches").exists())

    def test_executable_change_unknown_launch_and_duplicate_prepare_are_rejected(self):
        self.prepare()
        with self.assertRaises(ValueError):
            self.helper.prepare()
        with self.assertRaises(ValueError):
            self.helper.release("f" * 32)
        self.assertEqual(self.helper.release(self.identifier), {"released": True})
        self.exe.write_bytes(b"CHANGED")
        with self.assertRaises(ValueError):
            self.helper.prepare()
        self.assertTrue(Path(self.launch["session_directory"]).is_dir())


    

    def test_frozen_module_file_is_not_an_identity_input(self):
        self.helper.source_identity = {"kind": "frozen", "exe_sha256": "a" * 64}
        with patch.object(omp, "__file__", str(self.root / "missing-pyz-module.py")):
            first = self.prepare()
            self.helper.release(self.identifier)
            self.helper.source_identity = {"kind": "frozen", "exe_sha256": "b" * 64}
            with self.assertRaises(ValueError):
                self.helper.prepare()
        self.assertTrue(Path(first["session_directory"]).is_dir())

    def test_every_underlying_runtime_member_drift_is_independently_rejected(self):
        self.prepare()
        self.helper.release(self.identifier)
        for name in omp.RUNTIME_FILES:
            with self.subTest(member=name):
                path = self.bun / name
                original = path.read_bytes()
                path.write_bytes(bytes((original[0] ^ 1,)) + original[1:])
                try:
                    with self.assertRaises(ValueError):
                        self.helper.prepare()
                finally:
                    path.write_bytes(original)
        self.assertTrue(Path(self.launch["session_directory"]).is_dir())

    def test_missing_runtime_members_and_extra_routing_members_are_rejected(self):
        for name in omp.RUNTIME_FILES:
            with self.subTest(missing=name):
                path = self.bun / name
                raw = path.read_bytes()
                path.unlink()
                try:
                    with self.assertRaises((OSError, ValueError)):
                        self.helper.prepare()
                finally:
                    path.write_bytes(raw)
        for root in (omp.CLI_ROOT, omp.CLI_ROOT + "/dist", omp.NATIVES_ROOT,
                     omp.NATIVES_ROOT + "/native", omp.LEAF_ROOT):
            with self.subTest(extra=root):
                extra = self.bun / root / "unexpected-runtime.js"
                extra.write_bytes(b"SYNTHETIC")
                try:
                    with self.assertRaises(ValueError):
                        self.helper.prepare()
                finally:
                    extra.unlink()
        shadow = self.bun / "install/global/node_modules/@oh-my-pi/node_modules"
        shadow.mkdir()
        with self.assertRaises(ValueError):
            self.helper.prepare()

    def test_unknown_empty_directories_and_missing_known_members_are_rejected(self):
        extra = self.bun / omp.CLI_ROOT / "dist/unknown-empty-directory"
        extra.mkdir()
        with self.assertRaises(ValueError):
            self.helper.prepare()
        extra.rmdir()
        (self.bun / omp.NATIVES_ROOT / "native" / omp.NATIVE_TYPES[0]).unlink()
        with self.assertRaises(ValueError):
            self.helper.prepare()

    def test_package_metadata_is_strict_json_even_with_matching_test_hashes(self):
        path = self.bun / omp.NATIVES_ROOT / "package.json"
        original = path.read_bytes()
        invalid = (b'{"name":"x","name":"y"}', b'{"version":NaN}', b'[]', b'{',
                   b'{"x":' * 30 + b'{}' + b'}' * 30, b'\xef\xbb\xbf{}', b'"\xff"',
                   b'{"padding":"' + b"x" * omp.METADATA_LIMIT + b'"}')
        for raw in invalid:
            with self.subTest(raw=raw[:50]):
                path.write_bytes(raw)
                self.rebaseline()
                with self.assertRaises((ValueError, UnicodeError, RecursionError)):
                    self.helper.prepare()
        path.write_bytes(original)
        self.rebaseline()

    def test_package_names_versions_bins_and_platform_must_match(self):
        mutations = (
            (omp.CLI_ROOT, "name", "@other/agent"), (omp.CLI_ROOT, "version", "18.14.1"),
            (omp.CLI_ROOT, "type", "commonjs"), (omp.CLI_ROOT, "bin", {"omp": "src/cli.ts"}),
            (omp.CLI_ROOT, "bin", {"omp": "dist/cli.js", "other": "dist/cli.js"}),
            (omp.CLI_ROOT, "dependencies", None),
            (omp.CLI_ROOT, "dependencies", {"@oh-my-pi/pi-natives": "other"}),
            (omp.NATIVES_ROOT, "name", "@other/natives"),
            (omp.NATIVES_ROOT, "version", "18.14.1"), (omp.NATIVES_ROOT, "main", "./other.js"),
            (omp.NATIVES_ROOT, "type", "commonjs"),
            (omp.NATIVES_ROOT, "optionalDependencies", {"@oh-my-pi/pi-natives-win32-x64": "other"}),
            (omp.NATIVES_ROOT, "optionalDependencies", []),
            (omp.NATIVES_ROOT, "exports", None),
            (omp.NATIVES_ROOT, "exports", {".": {"import": "./other.js"}}),
            (omp.LEAF_ROOT, "name", "@other/native-leaf"),
            (omp.LEAF_ROOT, "version", "18.14.1"), (omp.LEAF_ROOT, "main", "./other.node"),
            (omp.LEAF_ROOT, "os", ["linux"]), (omp.LEAF_ROOT, "cpu", ["arm64"]),
        )
        for root, field, value in mutations:
            with self.subTest(package=root, field=field):
                path = self.bun / root / "package.json"
                original = path.read_bytes()
                metadata = json.loads(original)
                metadata[field] = value
                path.write_text(json.dumps(metadata), encoding="utf-8")
                self.rebaseline()
                try:
                    with self.assertRaises(ValueError):
                        self.helper.prepare()
                finally:
                    path.write_bytes(original)
                    self.rebaseline()

    def test_manifest_exact_bytes_and_closed_schema_are_required(self):
        original = self.manifest.read_bytes()
        self.manifest.write_bytes(original + b"\n")
        with self.assertRaises(ValueError):
            self.helper.prepare()
        malformed = (
            b'{"schema":"one","schema":"two"}', b'{"x":Infinity}', b'[]', b'{',
            b'{"x":' * 30 + b'{}' + b'}' * 30,
        )
        for raw in malformed:
            with self.subTest(raw=raw[:50]):
                self.pin_manifest(raw)
                with self.assertRaises((ValueError, RecursionError)):
                    self.helper.prepare()
        for mutation in ("extra", "version", "schema", "addon", "missing", "duplicate", "reorder",
                         "relative", "extra-row-field", "size-bool", "size-large", "hash"):
            with self.subTest(mutation=mutation):
                value = json.loads(original)
                if mutation == "extra":
                    value["extra"] = True
                elif mutation == "version":
                    value["package_version"] = "18.14.1"
                elif mutation == "schema":
                    value["schema"] = "old"
                elif mutation == "addon":
                    value["native_addon_relative_path"] = "other.node"
                elif mutation == "missing":
                    value["files"].pop()
                elif mutation == "duplicate":
                    value["files"][1] = value["files"][0]
                elif mutation == "reorder":
                    value["files"].reverse()
                elif mutation == "relative":
                    value["files"][1]["relative_path"] = "../other"
                elif mutation == "extra-row-field":
                    value["files"][1]["path"] = "other"
                elif mutation == "size-bool":
                    value["files"][1]["size"] = True
                elif mutation == "size-large":
                    value["files"][1]["size"] = omp.RUNTIME_FILE_LIMIT + 1
                else:
                    value["files"][1]["sha256"] = "not-a-hash"
                self.pin_manifest(json.dumps(value).encode())
                with self.assertRaises(ValueError):
                    self.helper.prepare()
        self.pin_manifest(original)

    def test_trusted_first_staging_preserves_runtime_members_across_launches(self):
        first = self.prepare()
        staged = Path(first["runtime"]["native_candidates"][0]["path"])
        self.assertFalse(staged.exists())
        self.helper.release(self.identifier)
        staged.parent.mkdir(parents=True)
        staged.write_bytes((self.bun / omp.ADDON_RELATIVE).read_bytes())
        second = self.prepare()
        self.assertTrue(second["runtime"]["native_candidates"][0]["present"])
        self.assertNotEqual(first["runtime_sha256"], second["runtime_sha256"])
        self.assertEqual(first["runtime"]["files"], second["runtime"]["files"])

    def test_existing_staged_addon_mismatch_is_preserved_and_rejected(self):
        staged = self.root / "owned/home/.omp/natives" / omp.VERSION / omp.ADDON_NAME
        staged.parent.mkdir(parents=True)
        staged.write_bytes(b"CHANGED")
        with self.assertRaises(ValueError):
            self.helper.prepare()
        self.assertEqual(staged.read_bytes(), b"CHANGED")
        self.assertFalse((self.root / "owned/launches").exists())

    def test_unsupported_native_candidates_are_rejected_without_repair(self):
        directories = (self.root / "owned/home/.omp/natives" / omp.VERSION,
                       self.bun / omp.LEAF_ROOT, self.bun / omp.NATIVES_ROOT / "native", self.bun / "bin")
        for index, directory in enumerate(directories):
            for name in ("pi_natives.win32-x64-modern.node", "pi_natives.win32-x64.node"):
                with self.subTest(directory=index, name=name):
                    directory.mkdir(parents=True, exist_ok=True)
                    path = directory / name
                    path.write_bytes(b"SYNTHETIC-UNSUPPORTED")
                    try:
                        with self.assertRaises(ValueError):
                            self.helper.prepare()
                        self.assertEqual(path.read_bytes(), b"SYNTHETIC-UNSUPPORTED")
                    finally:
                        path.unlink()

    def test_installed_fallback_baselines_require_the_same_exact_addon_bytes(self):
        original = (self.bun / omp.ADDON_RELATIVE).read_bytes()
        for directory, candidate_index in ((self.bun / omp.NATIVES_ROOT / "native", 6),
                                            (self.bun / "bin", 9)):
            with self.subTest(directory=directory):
                path = directory / omp.ADDON_NAME
                path.write_bytes(original)
                launch = self.prepare()
                self.assertTrue(launch["runtime"]["native_candidates"][candidate_index]["present"])
                self.helper.release(self.identifier)
                path.write_bytes(bytes((original[0] ^ 1,)) + original[1:])
                with self.assertRaises(ValueError):
                    self.helper.prepare()
                self.assertTrue(path.exists())
                path.unlink()

    def test_installed_hardlinks_are_allowed_but_owned_staging_is_not(self):
        alias = self.root / "installed-bun-alias"
        try:
            os.link(self.bun / "bin/bun.exe", alias)
        except OSError as error:
            self.skipTest("Hardlink unavailable: " + str(error))
        self.prepare()
        with self.assertRaises(ValueError):
            omp._regular(alias)
        self.helper.release(self.identifier)
        staged = self.root / "owned/home/.omp/natives" / omp.VERSION / omp.ADDON_NAME
        staged.parent.mkdir(parents=True)
        os.link(self.bun / omp.ADDON_RELATIVE, staged)
        with self.assertRaises(ValueError):
            self.helper.prepare()

    def test_installed_reparse_file_and_ancestor_are_rejected(self):
        path = self.bun / "bin/bun.exe"
        original = path.read_bytes()
        target = self.root / "untrusted-runtime"
        target.write_bytes(original)
        path.unlink()
        try:
            path.symlink_to(target)
        except OSError:
            path.write_bytes(original)
            self.skipTest("Symlink unavailable")
        with self.assertRaises(ValueError):
            self.helper.prepare()
        path.unlink()
        path.write_bytes(original)
        alias = self.root / "linked-bun"
        alias.symlink_to(self.bun, target_is_directory=True)
        with self.assertRaises(ValueError):
            omp.OmpTui({}, root=self.root / "other-owned", executable=alias / "bin/omp.exe")

    

    


    

    

    

    def test_linked_owned_root_is_rejected_before_workspace_preparation(self):
        self.prepare()
        linked = self.root / "linked"
        try:
            linked.symlink_to(self.root / "owned", target_is_directory=True)
        except OSError as error:
            self.skipTest("Symlink unavailable: " + str(error))
        with self.assertRaises(ValueError):
            omp.OmpTui({}, root=linked, executable=self.exe)

    def test_native_protocol_is_closed_and_release_does_not_wait_for_report_compile(self):
        import hwp_native_host as native
        from hwp_synthetic_report import write_inputs
        from types import SimpleNamespace

        profile, _ = write_inputs(self.root / "unsigned")
        host = native.NativeHost("a" * 64, base=self.root / "projects")
        self.addCleanup(host.close_after_ui_exit)
        with self.assertRaises(ValueError):
            host.dispatch("prepare_omp", {})
        host.dispatch("hello", {"protocol": native.PROTOCOL, "nonce": "a" * 64})
        with patch.object(omp, "OmpTui", return_value=self.helper) as constructor:
            launch = host.dispatch("prepare_omp", {})
            self.assertIsNone(host.session.info)
            self.assertFalse(host.session.running)
            self.assertFalse((self.root / "projects").exists())
            with self.assertRaises(ValueError):
                host.dispatch("prepare_omp", {})
            constructor.assert_called_once_with(host.runtime_identity)
        try:
            for method in ("close_report", "shutdown", "setup_requirements", "legacy_tools"):
                with self.subTest(rootless=method), self.assertRaises(ValueError):
                    host.dispatch(method, {})
            descriptor = host.dispatch("new_report", {"source": profile["jobs"][0]["source"]})
            self.assertIs(host.omp, self.helper)
            self.assertEqual(host.omp.launch["launch_id"], launch["launch_id"])
            for method, payload in (
                ("prepare_omp", {}), ("release_omp", {"launch_id": "f" * 32}),
                ("close_report", {}), ("shutdown", {}), ("setup_requirements", {}),
                ("legacy_tools", {}), ("open_report", {"root": descriptor["root"]}),
                ("new_report", {"source": profile["jobs"][0]["source"]}),
            ):
                with self.subTest(method=method), self.assertRaises(ValueError):
                    host.dispatch(method, payload)
        except BaseException:
            self.helper.release(launch["launch_id"])
            raise
        control = host.session._server.report_control
        control.thread = SimpleNamespace(is_alive=lambda: True)
        try:
            self.assertEqual(host.dispatch("release_omp", {"launch_id": launch["launch_id"]}), {"released": True})
            with self.assertRaises(ValueError):
                host.dispatch("prepare_omp", {})
        finally:
            control.thread = None
        self.assertTrue(Path(launch["session_directory"]).is_dir())
        host.dispatch("shutdown", {})

    def test_rootless_preparation_and_intake_keep_runtime_and_partial_session_guards(self):
        import hwp_native_host as native
        from types import SimpleNamespace
        from hwp_synthetic_report import write_inputs

        profile, _ = write_inputs(self.root / "guard-input")
        source = profile["jobs"][0]["source"]
        info = native.projects.new_document(Path(source), base=self.root / "projects")
        before_projects = set(info.root.parent.iterdir())

        host = native.NativeHost("a" * 64, base=self.root / "projects")
        self.addCleanup(host.close_after_ui_exit)
        host.dispatch("hello", {"protocol": native.PROTOCOL, "nonce": "a" * 64})
        with patch.object(native.projects.harness.runtime, "identity", return_value={"changed": True}):
            with self.assertRaises(ValueError):
                host.dispatch("prepare_omp", {})
        self.assertIsNone(host.omp)
        for property_name in ("running", "stopping"):
            with self.subTest(property=property_name), patch.object(
                type(host.session), property_name, new_callable=PropertyMock, return_value=True,
            ):
                # Running with no info is partial ownership, never initial intake.
                host.omp = self.helper
                with self.assertRaises(ValueError):
                    host.dispatch("prepare_omp", {})
                launch = self.helper.prepare()
                try:
                    for method, payload in (
                        ("new_report", {"source": source}),
                        ("open_report", {"root": str(info.root)}),
                    ):
                        with self.assertRaises(ValueError):
                            host.dispatch(method, payload)
                finally:
                    self.helper.release(launch["launch_id"])
        host.omp = self.helper
        launch = self.helper.prepare()
        try:
            with patch.object(native.projects.harness.runtime, "identity", return_value={"changed": True}):
                for method, payload in (
                    ("new_report", {"source": source}),
                    ("open_report", {"root": str(info.root)}),
                ):
                    with self.assertRaises(ValueError):
                        host.dispatch(method, payload)
            for partial in ("_server", "pending_session"):
                owner = host.session if partial == "_server" else host
                with patch.object(owner, partial, SimpleNamespace()):
                    for method, payload in (
                        ("new_report", {"source": source}),
                        ("open_report", {"root": str(info.root)}),
                    ):
                        with self.subTest(partial=partial), self.assertRaises(ValueError):
                            host.dispatch(method, payload)
            host.ownership_error = "SYNTHETIC UNCERTAIN CLEANUP"
            with self.assertRaises(RuntimeError):
                host.dispatch("open_report", {"root": str(info.root)})
        finally:
            host.ownership_error = ""
            self.helper.release(launch["launch_id"])
        self.assertEqual(set(info.root.parent.iterdir()), before_projects)

    def test_first_existing_library_intake_preserves_exact_rootless_launch(self):
        import hwp_native_host as native
        from hwp_synthetic_report import write_inputs

        profile, _ = write_inputs(self.root / "unsigned")
        info = native.projects.new_document(Path(profile["jobs"][0]["source"]), base=self.root / "projects")
        host = native.NativeHost("a" * 64, base=self.root / "projects")
        self.addCleanup(host.close_after_ui_exit)
        host.dispatch("hello", {"protocol": native.PROTOCOL, "nonce": "a" * 64})
        with patch.object(omp, "OmpTui", return_value=self.helper):
            launch = host.dispatch("prepare_omp", {})
        try:
            descriptor = host.dispatch("open_report", {"root": str(info.root)})
            self.assertEqual(descriptor["root"], str(info.root))
            self.assertIs(host.omp, self.helper)
            self.assertEqual(host.omp.launch, launch)
            with self.assertRaises(ValueError):
                host.dispatch("open_report", {"root": str(info.root)})
        finally:
            host.dispatch("release_omp", {"launch_id": launch["launch_id"]})


if __name__ == "__main__":
    unittest.main()
