"""Synthetic pinned-archive boundaries; no network, OS installs, or real tools."""
import hashlib
import io
import json
import os
from pathlib import Path
import stat
import struct
import sys
import tempfile
import time
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch
import zipfile

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import hwp_source_sdk as sdk


class Response(io.BytesIO):
    def __init__(self, raw, *, url=None, headers=None, status=200):
        super().__init__(raw)
        self.status = status
        self.headers = {"Content-Length": str(len(raw)), **(headers or {})}
        self.url = sdk.SDK_URL if url is None else url

    def geturl(self):
        return self.url


class SourceSdkTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name).resolve()
        self.source = self.root / "source"
        self.source.mkdir()
        self.pin("_windows_x64", return_value=True)
        self.selection = self.pin("_installed_host", return_value=None)
        self.process = self.pin("subprocess.run", side_effect=AssertionError("No real executable"))
        self.network = self.pin("urllib.request.build_opener", side_effect=AssertionError("No real network"))
        self.members = {
            "dotnet.exe": self.pe(),
            "LICENSE.txt": b"Original SDK license\r\n",
            "ThirdPartyNotices.txt": b"Original third party notices\r\n",
            "sdk/10.0.401/.version": b"synthetic-commit\n10.0.401\n",
            "sdk/10.0.401/dotnet.dll": b"INERT SDK fixture; never executable",
            "sdk/10.0.401/dotnet.runtimeconfig.json": b'{"runtimeOptions":{"tfm":"net10.0"}}',
            "sdk/10.0.401/subdir/payload.txt": b"closed SDK payload",
        }
        self.bind(self.zip_bytes(self.members))

    def pin(self, name, **kwargs):
        current = sdk
        parts = name.split(".")
        for part in parts[:-1]:
            current = getattr(current, part)
        replacement = patch.object(current, parts[-1], **kwargs)
        result = replacement.start()
        self.addCleanup(replacement.stop)
        return result

    @staticmethod
    def pe(machine=0x8664):
        # Deliberately not runnable: enough inert header bytes for architecture checks.
        data = bytearray(90)
        data[:2] = b"MZ"
        struct.pack_into("<I", data, 60, 64)
        data[64:68] = b"PE\0\0"
        struct.pack_into("<H", data, 68, machine)
        struct.pack_into("<H", data, 88, 0x20B)
        return bytes(data)

    @staticmethod
    def zip_bytes(members, extras=()):
        output = io.BytesIO()
        with zipfile.ZipFile(output, "w", compression=zipfile.ZIP_DEFLATED) as archive:
            for name, raw in members.items():
                archive.writestr(name, raw)
            for info, raw in extras:
                if isinstance(info, str):
                    # Preserve malformed bytes rather than Windows ZipInfo normalization.
                    original_name = info
                    info = zipfile.ZipInfo()
                    info.filename = original_name
                    info.orig_filename = original_name
                archive.writestr(info, raw)
        return output.getvalue()

    def bind(self, raw):
        self.raw = raw
        self.pin("SDK_SIZE", new=len(raw))
        self.pin("SDK_SHA512", new=hashlib.sha512(raw).hexdigest())

    def allow_response(self, raw=None, **kwargs):
        opener = Mock()
        opener.open.side_effect = lambda *args, **unused: Response(self.raw if raw is None else raw, **kwargs)
        self.network.side_effect = None
        self.network.return_value = opener
        return opener

    def prepare(self):
        self.allow_response()
        return sdk.ensure_sdk(self.source)

    def paths(self):
        return sdk._cache_paths(self.source)

    def rejected_archive(self, raw):
        self.bind(raw)
        self.allow_response()
        with self.assertRaises((sdk.SourceSdkError, ValueError)):
            sdk.ensure_sdk(self.source)
        base, cache, archive, pending, complete = self.paths()
        self.assertEqual(archive.read_bytes(), raw)
        self.assertTrue(pending.is_file())
        self.assertFalse(complete.exists())
        self.assertTrue((base / (sdk._CACHE_NAME + ".failure.txt")).is_file())
        self.process.assert_not_called()

    def test_downloaded_sdk_is_not_executed_and_ready_state_has_no_pending_owner(self):
        executable = self.prepare()
        base, cache, archive, pending, complete = self.paths()
        self.assertEqual(executable, cache / "dotnet.exe")
        self.assertTrue(complete.is_file())
        self.assertFalse(pending.exists())
        self.process.assert_not_called()

    def test_valid_cache_reuse_has_no_network_execution_or_writes(self):
        executable = self.prepare()
        self.network.reset_mock()
        self.network.side_effect = AssertionError("Cache must not download")
        self.pin("_new_file", side_effect=AssertionError("Cache must not mutate"))
        self.pin("_write_new", side_effect=AssertionError("Cache must not mutate"))
        self.assertEqual(sdk.ensure_sdk(self.source), executable)
        self.network.assert_not_called()
        self.process.assert_not_called()

    def test_unrelated_build_artifacts_are_preserved(self):
        build = self.source / "build"
        build.mkdir()
        original = build / "unrelated.bin"
        original.write_bytes(b"user-owned")
        self.prepare()
        self.assertEqual(original.read_bytes(), b"user-owned")

    def test_changed_archive_is_rejected_without_network_or_repair(self):
        self.prepare()
        archive = self.paths()[2]
        changed = bytearray(archive.read_bytes())
        changed[len(changed) // 2] ^= 1
        archive.write_bytes(changed)
        self.network.reset_mock()
        with self.assertRaises(sdk.SourceSdkError):
            sdk.ensure_sdk(self.source)
        self.assertEqual(archive.read_bytes(), changed)
        self.network.assert_not_called()

    def test_changed_extracted_bytes_rejected_even_with_same_size(self):
        self.prepare()
        target = self.paths()[1] / "LICENSE.txt"
        replacement = b"X" * target.stat().st_size
        target.write_bytes(replacement)
        self.network.reset_mock()
        with self.assertRaises(sdk.SourceSdkError):
            sdk.ensure_sdk(self.source)
        self.assertEqual(target.read_bytes(), replacement)
        self.network.assert_not_called()

    def test_extra_file_missing_file_and_empty_directory_are_rejected(self):
        self.prepare()
        cache = self.paths()[1]
        extra = cache / "unregistered.txt"
        extra.write_bytes(b"unexpected")
        with self.assertRaises(sdk.SourceSdkError):
            sdk.ensure_sdk(self.source)
        extra.unlink()  # Fixture mutation only, never production recovery.
        directory = cache / "unexpected-empty-folder"
        directory.mkdir()
        with self.assertRaises(sdk.SourceSdkError):
            sdk.ensure_sdk(self.source)
        directory.rmdir()
        (cache / "LICENSE.txt").unlink()
        with self.assertRaises(sdk.SourceSdkError):
            sdk.ensure_sdk(self.source)

    def test_partial_cache_stops_before_selection_download_or_mutation(self):
        base, cache, archive, pending, complete = self.paths()
        base.mkdir(parents=True)
        archive.write_bytes(b"retained incomplete bytes")
        with self.assertRaises(sdk.SourceSdkError):
            sdk.ensure_sdk(self.source)
        self.assertEqual(archive.read_bytes(), b"retained incomplete bytes")
        self.assertFalse(pending.exists())
        self.assertFalse(cache.exists())
        self.selection.assert_not_called()
        self.network.assert_not_called()

    def test_pending_marker_stops_even_with_complete_cache(self):
        self.prepare()
        self.paths()[3].write_bytes(b"retained pending evidence")
        self.network.reset_mock()
        with self.assertRaises(sdk.SourceSdkError):
            sdk.ensure_sdk(self.source)
        self.assertEqual(self.paths()[3].read_bytes(), b"retained pending evidence")
        self.network.assert_not_called()

    def test_download_truncation_keeps_partial_first_failure_and_never_retries(self):
        opener = self.allow_response(self.raw[:17], headers={"Content-Length": str(len(self.raw))})
        with self.assertRaises(sdk.SourceSdkError):
            sdk.ensure_sdk(self.source)
        base, cache, archive, pending, complete = self.paths()
        self.assertEqual(archive.read_bytes(), self.raw[:17])
        self.assertTrue(pending.exists())
        self.assertFalse(cache.exists())
        self.assertFalse(complete.exists())
        self.assertTrue((base / (sdk._CACHE_NAME + ".failure.txt")).exists())
        opener.open.assert_called_once()
        with self.assertRaises(sdk.SourceSdkError):
            sdk.ensure_sdk(self.source)
        opener.open.assert_called_once()

    def test_wrong_sha512_never_extracts_or_executes(self):
        self.pin("SDK_SHA512", new="0" * 128)
        self.allow_response()
        with self.assertRaises(sdk.SourceSdkError):
            sdk.ensure_sdk(self.source)
        self.assertFalse(self.paths()[1].exists())
        self.assertEqual(self.paths()[2].read_bytes(), self.raw)
        self.process.assert_not_called()

    def test_oversized_response_stops_at_one_extra_byte(self):
        self.allow_response(self.raw + b"EXTRA", headers={"Content-Length": str(len(self.raw))})
        with self.assertRaises(sdk.SourceSdkError):
            sdk.ensure_sdk(self.source)
        self.assertEqual(self.paths()[2].stat().st_size, len(self.raw) + 1)
        self.assertFalse(self.paths()[1].exists())

    def test_response_rejects_wrong_origin_length_encoding_range_and_status(self):
        cases = [
            {"url": "https://example.invalid/tool.zip"},
            {"headers": {"Content-Length": "1"}},
            {"headers": {"Content-Encoding": "gzip"}},
            {"headers": {"Content-Range": "bytes 0-1/100"}},
            {"status": 206},
        ]
        for index, case in enumerate(cases):
            with self.subTest(case=case):
                destination = self.root / ("bad-response-" + str(index))
                self.allow_response(**case)
                with self.assertRaises(sdk.SourceSdkError):
                    sdk._download(destination, time.monotonic() + 60)
                self.assertEqual(destination.read_bytes(), b"")
        self.process.assert_not_called()

    def test_redirects_are_rejected_before_following(self):
        redirect = sdk._PinnedRedirect()
        for target in (sdk.SDK_URL, "http://builds.dotnet.microsoft.com/x", "https://evil.invalid/x"):
            with self.subTest(target=target), self.assertRaises(sdk.SourceSdkError):
                redirect.redirect_request(None, None, 302, "redirect", {}, target)

    def test_expired_download_has_no_network_request_and_retains_empty_evidence(self):
        self.allow_response()
        destination = self.root / "expired.zip"
        with self.assertRaises(sdk.SourceSdkError):
            sdk._download(destination, time.monotonic() - 1)
        self.assertEqual(destination.read_bytes(), b"")
        self.network.return_value.open.assert_not_called()

    def test_zip_traversal_absolute_backslash_reserved_and_trailing_dot(self):
        names = ("../escape", "/absolute", "a\\escape", "C:/drive", "a/../escape",
                 "a/NUL.txt", "a/COM¹.txt", "trailing./file", "a//file", "a/file:stream")
        for index, name in enumerate(names):
            with self.subTest(name=name):
                raw = self.zip_bytes(self.members, [(name, b"bad")])
                with zipfile.ZipFile(io.BytesIO(raw)) as archive:
                    with self.assertRaises((sdk.SourceSdkError, ValueError)):
                        sdk._entries(archive, time.monotonic() + 60)
        self.assertFalse((self.root / "escape").exists())

    def test_zip_link_rejected_before_any_extraction(self):
        info = zipfile.ZipInfo("sdk/link")
        info.create_system = 3
        info.external_attr = (stat.S_IFLNK | 0o777) << 16
        self.rejected_archive(self.zip_bytes(self.members, [(info, b"../../escape")]))
        self.assertFalse(self.paths()[1].exists())

    def test_zip_case_collision_in_implicit_directory_rejected(self):
        self.rejected_archive(self.zip_bytes(self.members, [("SDK/other.txt", b"collision")]))
        self.assertFalse(self.paths()[1].exists())

    def test_zip_duplicate_unicode_collision_and_file_parent_conflict(self):
        variants = [
            [("LICENSE.txt", b"duplicate")],
            [("extra/\u00e9.txt", b"one"), ("extra/e\u0301.txt", b"two")],
            [("extra/parent", b"file"), ("extra/parent/child", b"child")],
        ]
        for extras in variants:
            with self.subTest(extras=extras):
                with zipfile.ZipFile(io.BytesIO(self.zip_bytes(self.members, extras))) as archive:
                    with self.assertRaises(sdk.SourceSdkError):
                        sdk._entries(archive, time.monotonic() + 60)

    def test_zip_expansion_and_member_count_bounds(self):
        with zipfile.ZipFile(io.BytesIO(self.raw)) as archive:
            with patch.object(sdk, "_MAX_EXPANDED", 1), self.assertRaises(sdk.SourceSdkError):
                sdk._entries(archive, time.monotonic() + 60)
        with zipfile.ZipFile(io.BytesIO(self.raw)) as archive:
            with patch.object(sdk, "_MAX_MEMBERS", 1), self.assertRaises(sdk.SourceSdkError):
                sdk._entries(archive, time.monotonic() + 60)

    def test_wrong_architecture_is_rejected_before_execution(self):
        changed = {**self.members, "dotnet.exe": self.pe(0x14C)}
        self.rejected_archive(self.zip_bytes(changed))

    def test_wrong_sdk_metadata_is_rejected_before_execution(self):
        changed = {**self.members, "sdk/10.0.401/.version": b"10.0.400\n"}
        self.rejected_archive(self.zip_bytes(changed))

    def installed(self):
        root = self.root / "installed"
        root.mkdir()
        for name, raw in self.members.items():
            target = root / name
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(raw)
        self.selection.return_value = root / "dotnet.exe"
        return root

    def test_exact_installed_sdk_uses_isolated_profile_without_cache_or_network(self):
        installed = self.installed()
        snapshot = dict(os.environ)
        observed = {}

        def version(command, **kwargs):
            observed.update(kwargs)
            environment = kwargs["env"]
            self.assertNotIn("DOTNET_ROOT", environment)
            self.assertNotIn("DOTNET_ROOT_X64", environment)
            for key in ("HOME", "USERPROFILE", "TEMP", "NUGET_PACKAGES", "DOTNET_CLI_HOME"):
                self.assertTrue(Path(environment[key]).is_relative_to(kwargs["cwd"]))
            self.assertEqual(json.loads((kwargs["cwd"] / "global.json").read_text())["sdk"],
                             {"version": "10.0.401", "rollForward": "disable", "allowPrerelease": False})
            kwargs["stdout"].write(b"10.0.401\n")
            return SimpleNamespace(returncode=0)

        self.process.side_effect = version
        self.assertEqual(sdk.ensure_sdk(self.source), installed / "dotnet.exe")
        self.assertFalse((self.source / "build").exists())
        self.assertFalse(observed["cwd"].exists())
        self.assertEqual(dict(os.environ), snapshot)
        self.network.assert_not_called()

    def test_other_version_is_not_repaired_only_missing_pin_is_prepared(self):
        installed = self.root / "other-version"
        installed.mkdir()
        (installed / "dotnet.exe").write_bytes(self.pe())
        old = installed / "sdk/9.0.999/keep.txt"
        old.parent.mkdir(parents=True)
        old.write_bytes(b"untouched installed SDK")
        self.selection.return_value = installed / "dotnet.exe"
        result = self.prepare()
        self.assertEqual(result, self.paths()[1] / "dotnet.exe")
        self.assertEqual(old.read_bytes(), b"untouched installed SDK")
        self.assertFalse((installed / "sdk/10.0.401").exists())
        self.process.assert_not_called()

    def test_corrupt_selected_installation_is_not_hidden_by_valid_cache(self):
        self.prepare()
        installed = self.installed()
        (installed / "sdk/10.0.401/.version").write_bytes(b"corrupt")
        self.network.reset_mock()
        with self.assertRaises(sdk.SourceSdkError):
            sdk.ensure_sdk(self.source)
        self.network.assert_not_called()
        self.process.assert_not_called()

    def test_explicit_missing_or_relative_root_is_not_replaced_by_path_sdk(self):
        # Exercise actual selection rather than the general test fixture selector.
        for value in ("", "relative-root", str(self.root / "missing-root")):
            with self.subTest(value=value), patch.dict(os.environ, {"DOTNET_ROOT_X64": value}):
                with self.assertRaises((sdk.SourceSdkError, OSError)):
                    _REAL_INSTALLED_HOST()
        self.network.assert_not_called()

    def test_retained_failure_record_blocks_even_without_pending_marker(self):
        base = self.paths()[0]
        base.mkdir(parents=True)
        failure = base / (sdk._CACHE_NAME + ".failure.txt")
        failure.write_bytes(b"first failure evidence")
        with self.assertRaises(sdk.SourceSdkError):
            sdk.ensure_sdk(self.source)
        self.assertEqual(failure.read_bytes(), b"first failure evidence")
        self.network.assert_not_called()

    def test_bad_zip_crc_cannot_produce_a_completed_sdk(self):
        output = io.BytesIO()
        with zipfile.ZipFile(output, "w", compression=zipfile.ZIP_STORED) as archive:
            for name, raw in self.members.items():
                archive.writestr(name, raw)
        raw = output.getvalue()
        changed = raw.replace(b"closed SDK payload", b"broken SDK payload", 1)
        self.assertNotEqual(raw, changed)
        self.rejected_archive(changed)

    def test_failed_installed_probe_retains_isolated_evidence_without_download(self):
        installed = self.installed()
        work = self.root / "failed-probe"
        work.mkdir()
        self.pin("tempfile.mkdtemp", return_value=str(work))

        def version(command, **kwargs):
            kwargs["stdout"].write(b"10.0.400\n")
            return SimpleNamespace(returncode=0)

        self.process.side_effect = version
        with self.assertRaises(sdk.SourceSdkError):
            sdk.ensure_sdk(self.source)
        self.assertEqual((work / "version.log").read_bytes(), b"10.0.400\n")
        self.assertTrue((installed / "dotnet.exe").exists())
        self.assertFalse((self.source / "build").exists())
        self.network.assert_not_called()

    def test_extracted_hardlink_is_rejected(self):
        self.prepare()
        target = self.paths()[1] / "LICENSE.txt"
        alias = self.root / "hardlink"
        try:
            os.link(target, alias)
        except OSError as exc:
            self.skipTest("Hard links unavailable: " + str(exc))
        with self.assertRaises(ValueError):
            sdk.ensure_sdk(self.source)
        self.assertTrue(alias.exists())

    def test_extracted_symlink_is_rejected_without_following(self):
        self.prepare()
        cache = self.paths()[1]
        target = cache / "LICENSE.txt"
        target.unlink()
        outside = self.root / "outside-license"
        outside.write_bytes(self.members["LICENSE.txt"])
        try:
            target.symlink_to(outside)
        except OSError as exc:
            self.skipTest("Symlinks unavailable: " + str(exc))
        with self.assertRaises(ValueError):
            sdk.ensure_sdk(self.source)
        self.assertEqual(outside.read_bytes(), self.members["LICENSE.txt"])


_REAL_INSTALLED_HOST = sdk._installed_host

if __name__ == "__main__":
    unittest.main()
