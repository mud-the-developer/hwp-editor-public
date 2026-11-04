"""Two fixed roundtrip arms with TEMP fake bytes/COM only; no Native or approvals."""

from contextlib import contextmanager
import importlib
import json
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch
import zipfile

m = importlib.import_module("native_hpf_roundtrip_probe")
TEXT = m.creator.MARKER + "\n" + m.creator.TABLE_OLD


class HpfRoundtripProbeTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name).resolve()
        (self.root / "work").mkdir()
        self.lab = self.root / "work/toy-hpf-roundtrip-SYNTHETIC"
        self.apps = []
        self.open_ok = self.save_ok = self.exit_ok = self.proof_ok = True
        self.text = TEXT
        self.malformed = self.change_source = self.collision = False
        self.creator = Mock(side_effect=self.create_source)
        self.sessions = Mock(side_effect=self.session)
        for patcher in (
            patch.object(m, "ROOT", self.root),
            patch.object(m, "hwp_inventory", return_value=[]),
            patch.object(m.creator, "create_source", self.creator),
            patch.object(m, "owned_session", self.sessions),
            patch.object(
                m.metadata.security,
                "registered_security_module",
                return_value={"alias": "SYNTHETIC", "dll_sha256": "0" * 64},
            ),
            patch.object(
                m.metadata.security,
                "ensure_security_module",
                side_effect=AssertionError("No installation"),
            ),
            patch.object(
                m.metadata,
                "_native_arm",
                side_effect=AssertionError("No legacy runtime"),
            ),
        ):
            patcher.start()
            self.addCleanup(patcher.stop)

    def create_source(self, directory, schema):
        self.assertEqual(schema, 3)
        directory.mkdir()
        source = directory / "TOY-ONLY-unsigned-contract.hwp"
        source.write_bytes(b"SYNTHETIC OWN SOURCE")
        (directory / "TOY-ONLY-authoring-seed.hwp").write_bytes(b"SYNTHETIC SEED")
        proof = {
            "status": "owned_source_created_not_document_qualified",
            "qualification": False,
            "source_sha256": m.r.file_hash(source),
            "ownership": {
                "status": "owned_exit_confirmed",
                "exit_confirmed": self.proof_ok,
            },
        }
        (directory / "TOY-SOURCE-CREATION.json").write_text(
            json.dumps(proof), encoding="utf-8"
        )
        return source

    @contextmanager
    def session(self, paths):
        copied, target = paths
        index = len(self.apps)
        document = SimpleNamespace(FullName="", Path="", Modified=False)
        app = SimpleNamespace(
            ownership={"status": "owned_blank_verified", "exit_confirmed": False},
            XHwpDocuments=SimpleNamespace(Count=1, Item=lambda _: document),
            PageCount=1,
            RegisterModule=Mock(return_value=True),
            GetTextFile=Mock(side_effect=lambda *args: self.text),
        )

        def open_copy(name, fmt, options):
            self.assertEqual(Path(name), copied)
            self.assertEqual(
                (fmt, options),
                (("HWP" if index == 0 else "HWPX"), "versionwarning:false"),
            )
            document.FullName, document.Path = name, str(copied.parent)
            if self.collision:
                target.write_bytes(b"KEEP COLLISION")
            return self.open_ok

        def save(name, fmt, options):
            self.assertEqual((Path(name), fmt, options), (target, "HWPX", ""))
            if not self.save_ok:
                return False
            linear = "yes" if index == 0 else "no"
            xml = f'<package xmlns="{m.metadata.fixture.native.OPF[1:-1]}"><spine><itemref idref="header" linear="{linear}"/><itemref idref="section0"/></spine></package>'
            with zipfile.ZipFile(target, "x") as package:
                package.writestr(
                    "Contents/content.hpf",
                    b"not XML" if self.malformed else xml.encode(),
                )
            document.FullName, document.Path = name, str(target.parent)
            if self.change_source:
                (self.lab / "source/TOY-ONLY-unsigned-contract.hwp").write_bytes(
                    b"CHANGED SOURCE"
                )
            return True

        app.Open, app.SaveAs = Mock(side_effect=open_copy), Mock(side_effect=save)
        self.apps.append(app)
        try:
            yield app
        finally:
            if not self.exit_ok:
                raise RuntimeError("SYNTHETIC exit unconfirmed")
            app.ownership.update(status="owned_exit_confirmed", exit_confirmed=True)

    def invoke(self):
        return m.run(self.lab, run_native=True, toy_only=True)

    def report(self):
        return json.loads(
            (self.lab / "HPF-ROUNDTRIP-DIAGNOSIS.json").read_text(encoding="utf-8")
        )

    def assert_stopped_first_arm(self):
        self.assertEqual(len(self.apps), 1)
        self.assertEqual(self.report()["status"], "blocked")
        self.assertFalse((self.lab / "arm-2-input.hwpx").exists())

    def test_two_fixed_fresh_inputs_and_semantic_drift_are_observations_not_qualification(
        self,
    ):
        report = self.invoke()
        self.creator.assert_called_once_with(self.lab / "source", 3)
        self.assertEqual([a["open_format"] for a in report["arms"]], ["HWP", "HWPX"])
        self.assertEqual(report["status"], "diagnosis_completed_not_qualified")
        self.assertFalse(report["qualification"])
        self.assertFalse(report["preservation_success_claimed"])
        self.assertFalse(
            report["arms"][1]["hpf"]["baseline_comparison"]["semantic_spine_equal"]
        )
        self.assertEqual(
            (self.lab / "arm-1-output.hwpx").read_bytes(),
            (self.lab / "arm-2-input.hwpx").read_bytes(),
        )
        self.assertEqual(
            (self.lab / "source/TOY-ONLY-unsigned-contract.hwp").read_bytes(),
            b"SYNTHETIC OWN SOURCE",
        )
        self.assertTrue(
            all(a["ownership"]["exit_confirmed"] is True for a in report["arms"])
        )
        self.assertEqual(self.report(), report)

    def test_nonliteral_flags_stop_before_paths_or_creator(self):
        for flags in (
            {},
            {"run_native": True},
            {"toy_only": True},
            {"run_native": 1, "toy_only": True},
            {"run_native": True, "toy_only": "yes"},
        ):
            with (
                self.subTest(flags=flags),
                patch.object(m.metadata.fixture.paths, "real_path") as paths,
            ):
                with self.assertRaises(ValueError):
                    m.run(self.lab, **flags)
                paths.assert_not_called()
        self.creator.assert_not_called()
        self.sessions.assert_not_called()

    def test_frozen_entry_is_denied_before_paths(self):
        with (
            patch.object(m.sys, "frozen", True, create=True),
            patch.object(m.metadata.fixture.paths, "real_path") as paths,
        ):
            with self.assertRaises(ValueError):
                self.invoke()
            paths.assert_not_called()
        self.creator.assert_not_called()

    def test_existing_foreign_nested_or_linked_lab_is_denied(self):
        self.lab.mkdir()
        for path in (
            self.lab,
            self.root / "toy-hpf-roundtrip-FOREIGN",
            self.root / "work/nested/toy-hpf-roundtrip-NESTED",
        ):
            with self.subTest(path=path), self.assertRaises(ValueError):
                m.run(path, run_native=True, toy_only=True)
        self.lab.rmdir()
        original = Path.is_symlink
        with patch.object(
            Path, "is_symlink", lambda path: path == self.lab or original(path)
        ):
            with self.assertRaises(ValueError):
                self.invoke()
        self.creator.assert_not_called()

    def test_nonidle_hwp_blocks_before_source_creation(self):
        m.hwp_inventory.return_value = [{"pid": 99}]
        with self.assertRaises(RuntimeError):
            self.invoke()
        self.creator.assert_not_called()
        self.assertFalse(self.lab.exists())

    def test_creator_exit_proof_is_required_before_arms(self):
        self.proof_ok = False
        with self.assertRaisesRegex(ValueError, "source creation/exit proof"):
            self.invoke()
        self.sessions.assert_not_called()
        self.assertEqual(self.report()["status"], "blocked")

    def test_open_failure_stops_without_export_or_second_arm(self):
        self.open_ok = False
        with self.assertRaises(RuntimeError):
            self.invoke()
        self.assert_stopped_first_arm()
        self.apps[0].SaveAs.assert_not_called()

    def test_save_failure_stops_before_second_arm(self):
        self.save_ok = False
        with self.assertRaises(RuntimeError):
            self.invoke()
        self.assert_stopped_first_arm()
        self.assertFalse((self.lab / "arm-1-output.hwpx").exists())

    def test_unknown_text_stops_before_any_export(self):
        self.text = None
        with self.assertRaisesRegex(ValueError, "marker/text snapshot"):
            self.invoke()
        self.assert_stopped_first_arm()
        self.apps[0].SaveAs.assert_not_called()

    def test_malformed_hpf_stops_after_confirmed_first_exit(self):
        self.malformed = True
        with self.assertRaises(Exception):
            self.invoke()
        self.assert_stopped_first_arm()
        self.assertTrue(self.apps[0].ownership["exit_confirmed"])

    def test_code_drift_stops_before_the_first_arm(self):
        with patch.object(
            m,
            "code_hashes",
            side_effect=[{"SYNTHETIC": "before"}, {"SYNTHETIC": "after"}],
        ):
            with self.assertRaisesRegex(ValueError, "code changed"):
                self.invoke()
        self.sessions.assert_not_called()
        self.assertEqual(self.report()["status"], "blocked")

    def test_source_drift_stops_without_second_arm_or_repair(self):
        self.change_source = True
        with self.assertRaisesRegex(ValueError, "Immutable"):
            self.invoke()
        self.assert_stopped_first_arm()
        self.assertEqual(
            (self.lab / "source/TOY-ONLY-unsigned-contract.hwp").read_bytes(),
            b"CHANGED SOURCE",
        )

    def test_unknown_exit_cannot_start_second_arm(self):
        self.exit_ok = False
        with self.assertRaisesRegex(RuntimeError, "exit unconfirmed"):
            self.invoke()
        self.assert_stopped_first_arm()
        self.assertFalse(self.report()["arms"][0]["ownership"]["exit_confirmed"])

    def test_output_collision_is_preserved_and_never_saved(self):
        self.collision = True
        with self.assertRaises(ValueError):
            self.invoke()
        self.assert_stopped_first_arm()
        self.assertEqual(
            (self.lab / "arm-1-output.hwpx").read_bytes(), b"KEEP COLLISION"
        )
        self.apps[0].SaveAs.assert_not_called()


if __name__ == "__main__":
    unittest.main()
