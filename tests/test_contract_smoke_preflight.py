"""Exact synthetic candidate preflight; no EXE launch, COM, real approval or lab."""

import importlib
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

m = importlib.import_module("native_contract_smoke")
b = importlib.import_module("build_exe")


class ContractSmokePreflightTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name).resolve()
        self.output = self.root / "work/toy-contract-SYNTHETIC"
        self.exe = self.root / "candidate/HWPReview.exe"
        self.exe.parent.mkdir()
        self.exe.write_bytes(b"SYNTHETIC NOT EXE; NEVER RUN")
        for name in set(b.SOURCE_FILES) | set(b.ASSETS):
            file = self.root / name
            file.parent.mkdir(parents=True, exist_ok=True)
            file.write_text("SYNTHETIC " + name, encoding="utf-8")
        self.metadata = {
            "executable_sha256": m.r.file_hash(self.exe),
            "source_sha256": {
                name: m.r.file_hash(self.root / name) for name in b.SOURCE_FILES
            },
            "assets_sha256": {
                name: m.r.file_hash(self.root / name) for name in b.ASSETS
            },
        }
        self.build = self.exe.parent / "BUILD.json"
        m.r.write_json(self.build, self.metadata)
        for patcher in (
            patch.object(m, "ROOT", self.root),
            patch.object(
                m,
                "create_source",
                side_effect=AssertionError("NO Native source in preflight"),
            ),
            patch.object(
                m.security,
                "registered_security_module",
                side_effect=AssertionError("NO security/registry in preflight"),
            ),
            patch.object(
                m.p,
                "prepare_project",
                side_effect=AssertionError("NO Native preparation in preflight"),
            ),
        ):
            patcher.start()
            self.addCleanup(patcher.stop)

    def assert_no_effects(self):
        m.create_source.assert_not_called()
        m.security.registered_security_module.assert_not_called()
        m.p.prepare_project.assert_not_called()
        self.assertFalse(self.output.exists())

    def test_missing_false_or_truthy_nonliteral_flags_never_reach_preflight(self):
        for flags in (
            {},
            {"run_native": True},
            {"run_native": 1, "toy_only": True},
            {"run_native": True, "toy_only": "yes"},
            {"run_native": False, "toy_only": True},
        ):
            with (
                self.subTest(flags=flags),
                patch.object(m, "qualification_preflight") as preflight,
                self.assertRaises(ValueError),
            ):
                m.run(self.output, self.exe, 3, **flags)
            preflight.assert_not_called()
        self.assert_no_effects()

    def test_frozen_run_or_readonly_preflight_is_denied(self):
        with patch.object(m.sys, "frozen", True, create=True):
            with self.assertRaises(ValueError):
                m.run(self.output, self.exe, 3, run_native=True, toy_only=True)
            with self.assertRaises(ValueError):
                m.qualification_preflight(self.exe, 3)
        self.assert_no_effects()

    def test_unqualified_families_stop_before_paths_and_executable_reads(self):
        for schema in (2,):
            with (
                self.subTest(schema=schema),
                patch.object(m.n, "real_path") as paths,
                self.assertRaises(ValueError),
            ):
                m.run(
                    self.output,
                    self.root / "MISSING.exe",
                    schema,
                    run_native=True,
                    toy_only=True,
                )
            paths.assert_not_called()
        self.assert_no_effects()

    def test_exact_qualified_snapshot_preflight_is_readonly_not_authority(self):
        before = {
            p.relative_to(self.root).as_posix(): m.r.file_hash(p)
            for p in self.root.rglob("*")
            if p.is_file()
        }
        proof = m.qualification_preflight(self.exe, 3)
        self.assertTrue(proof["source_and_assets_match"])
        self.assertFalse(proof["native_started"])
        self.assertFalse(proof["trial_authorized"])
        self.assertEqual(proof["executable_sha256"], self.metadata["executable_sha256"])
        after = {
            p.relative_to(self.root).as_posix(): m.r.file_hash(p)
            for p in self.root.rglob("*")
            if p.is_file()
        }
        self.assertEqual(before, after)
        self.assert_no_effects()

    def test_exact_fill_preflight_is_readonly_and_not_runtime_authority(self):
        proof = m.qualification_preflight(self.exe, 4)
        self.assertTrue(proof["source_and_assets_match"])
        self.assertFalse(proof["native_started"])
        self.assertFalse(proof["trial_authorized"])
        self.assert_no_effects()

    def test_fixed_fill_input_uses_real_mapping_ids_but_never_positions_or_consent(
        self,
    ):
        fields = [{"field_id": f"synthetic-{i}", "supported": True} for i in range(3)]
        form = importlib.import_module("hwp_form_draft")
        with (
            patch.object(form, "inspect_template", return_value={"fields": fields}),
            patch.object(m.r.hwp_fill_plan, "empty_regions", return_value=[{}, {}, {}]),
        ):
            data = m.fill_input(self.root)
        self.assertEqual(set(data), {"bindings", "record"})
        self.assertEqual(
            [f["field_id"] for f in data["bindings"]["fields"]],
            [f["field_id"] for f in fields],
        )
        self.assertEqual(
            [f["kind"] for f in data["bindings"]["fields"]], ["text", "integer", "date"]
        )
        self.assertTrue(
            all(
                source == "FICTIONAL TEST ONLY"
                for source in data["record"]["sources"].values()
            )
        )
        self.assertNotIn("placements", data)
        self.assertNotIn("approval", data)
        self.assert_no_effects()

    def test_fill_input_rejects_any_nonexact_field_or_region_count(self):
        form = importlib.import_module("hwp_form_draft")
        for field_count, region_count in ((2, 3), (4, 3), (3, 2), (3, 4)):
            with (
                patch.object(
                    form,
                    "inspect_template",
                    return_value={
                        "fields": [
                            {"field_id": str(i), "supported": True}
                            for i in range(field_count)
                        ]
                    },
                ),
                patch.object(
                    m.r.hwp_fill_plan, "empty_regions", return_value=[{}] * region_count
                ),
            ):
                with self.assertRaises(ValueError):
                    m.fill_input(self.root)
        self.assert_no_effects()

    def test_exe_or_source_hash_drift_blocks_run_before_lab_creation(self):
        for file in (self.exe, self.root / b.SOURCE_FILES[0], self.root / b.ASSETS[0]):
            original = file.read_bytes()
            file.write_bytes(b"CHANGED")
            with self.assertRaises(ValueError):
                m.run(self.output, self.exe, 3, run_native=True, toy_only=True)
            file.write_bytes(original)
            self.assert_no_effects()

    def test_snapshot_path_set_cannot_inject_unrelated_reads(self):
        for key in ("source_sha256", "assets_sha256"):
            original = self.metadata[key]
            self.metadata[key] = {**original, "../UNRELATED-DO-NOT-READ": "0" * 64}
            m.r.write_json(self.build, self.metadata)
            with self.assertRaisesRegex(ValueError, "exact allowed"):
                m.qualification_preflight(self.exe, 3)
            self.metadata[key] = original
            m.r.write_json(self.build, self.metadata)
        self.assert_no_effects()

    def test_missing_allowed_file_or_malformed_schema_is_not_qualified(self):
        for schema in (True, "3", 1, None):
            with self.assertRaises(ValueError):
                m.qualification_preflight(self.exe, schema)
        self.metadata["source_sha256"].pop(b.SOURCE_FILES[0])
        m.r.write_json(self.build, self.metadata)
        with self.assertRaises(ValueError):
            m.qualification_preflight(self.exe, 3)
        self.assert_no_effects()


if __name__ == "__main__":
    unittest.main()
