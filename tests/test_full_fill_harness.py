"""Full-save source harness with fake exports and REAL XML/PDF gates; no COM/HWP."""

import importlib
import io
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
import zipfile

m = importlib.import_module("native_fill_smoke")
form_fixture = importlib.import_module("test_hwp_form_draft")


def pdf(path, filled=False):
    with m.r.fitz.open() as doc:
        page = doc.new_page(width=300, height=400)
        page.insert_text((20, 30), "SYNTHETIC FULL FILL ONLY")
        for index, (key, value) in enumerate(
            (("Name", "Synthetic User"), ("Code", "7"), ("Date", "2026-10-10"))
        ):
            y = 60 + index * 30
            for left, right in ((20, 80), (80, 220)):
                page.draw_rect(m.r.fitz.Rect(left, y, right, y + 30), color=(0, 0, 0))
            page.insert_text((25, y + 20), key, fontsize=10)
            if filled:
                page.insert_text((88, y + 20), value, fontsize=10)
        doc.save(path)


def package(path, linear="yes"):
    cells = form_fixture.cell(0, 0, "TEST LABEL") + form_fixture.cell(
        0, 1, "INPUTS BELOW"
    )
    for row, key in enumerate(("Name", "Code", "Date"), 1):
        cells += form_fixture.cell(row, 0, key) + form_fixture.cell(row, 1)
    form_fixture.package(path, cells=cells)
    with zipfile.ZipFile(path, "a") as archive:
        archive.writestr(
            "Contents/content.hpf",
            f'<opf:package xmlns:opf="http://www.idpf.org/2007/opf/"><opf:metadata><opf:meta name="ModifiedDate">2026-10-10T00:00:00Z</opf:meta></opf:metadata><opf:manifest><opf:item id="header" href="Contents/header.xml"/></opf:manifest><opf:spine><opf:itemref idref="header" linear="yes"/><opf:itemref idref="section0" linear="{linear}"/></opf:spine></opf:package>',
        )


class FullFillHarnessTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name).resolve()
        (self.root / "work").mkdir()
        for patcher in (
            patch.object(m, "ROOT", self.root),
            patch.object(m, "hwp_inventory", return_value=[]),
            patch.object(
                m,
                "create_source",
                side_effect=AssertionError("OFFLINE: source authoring forbidden"),
            ),
            patch.object(
                m.r,
                "prepare",
                side_effect=AssertionError("OFFLINE: Native prepare forbidden"),
            ),
            patch.object(
                m.r,
                "native_export",
                side_effect=AssertionError("OFFLINE: Native export forbidden"),
            ),
        ):
            patcher.start()
            self.addCleanup(patcher.stop)

    def output(self):
        return self.root / "work/toy-fill-full-SYNTHETIC"

    def fake_run(self, *, bad_reopened=False, baseline_linear="yes"):
        calls = []
        stored = {}

        def create(directory):
            directory.mkdir()
            source = directory / "TOY-ONLY-empty-template.hwp"
            source.write_bytes(b"SYNTHETIC NOT HWP")
            return source

        def prepare(source, workspace):
            workspace.mkdir()
            package(workspace / "baseline.hwpx", baseline_linear)
            pdf(workspace / "baseline.pdf")
            m.r.write_json(
                workspace / "manifest.json",
                {
                    "workspace_id": "SYNTHETIC-FULL-FILL",
                    "source": str(source),
                    "source_audit": {"sha256": m.r.file_hash(source)},
                    "baseline_hwpx_sha256": m.r.file_hash(workspace / "baseline.hwpx"),
                    "baseline_pdf_sha256": m.r.file_hash(workspace / "baseline.pdf"),
                    "roundtrip_passed": True,
                    "protected_pages": [],
                    "pages": [{"page": 1, "width": 300, "height": 400}],
                    "anchors": [],
                },
            )

        def export(source, target, *, hwpx=None, hwp=None):
            calls.append((source, target, hwpx, hwp))
            if hwpx is None:
                raise AssertionError(
                    "Synthetic source export requires exact HWPX evidence"
                )
            if hwp is not None:
                stored["xml"] = source.read_bytes()
                hwp.write_bytes(b"SYNTHETIC SAVED HWP")
            else:
                self.assertEqual(source.name, "TOY-ONLY-filled.hwp")
                self.assertEqual(hwpx.name, "TOY-ONLY-reopened.hwpx")
            raw = stored["xml"]
            if bad_reopened and hwp is None:
                with zipfile.ZipFile(io.BytesIO(raw)) as archive:
                    parts = {name: archive.read(name) for name in archive.namelist()}
                parts["Contents/content.hpf"] = parts["Contents/content.hpf"].replace(
                    b'idref="section0" linear="yes"', b'idref="section0" linear="no"'
                )
                with zipfile.ZipFile(hwpx, "x") as archive:
                    for name, data in parts.items():
                        archive.writestr(name, data)
            else:
                hwpx.write_bytes(raw)
            pdf(target, True)
            return {"native_pages": 1, "SYNTHETIC_EXPORTER_NOT_NATIVE": True}

        with (
            patch.object(m, "create_source", side_effect=create),
            patch.object(m.r, "prepare", side_effect=prepare),
            patch.object(m.r, "native_export", side_effect=export),
            patch.object(
                m.r,
                "hwp_audit",
                side_effect=lambda p: {
                    "sha256": m.r.file_hash(p),
                    "embedded_binary_hashes": [],
                },
            ),
        ):
            result = m.run(self.output(), run_native=True, toy_only=True)
        return result, calls

    def test_both_initial_and_reopened_hwp_xml_gates_are_required(self):
        actual = m.fill.structural_gate
        with patch.object(m.fill, "structural_gate", wraps=actual) as gate:
            result, calls = self.fake_run()
        self.assertEqual(
            result["native_status"], "checks_passed_needs_human_visual_review"
        )
        self.assertFalse(result["qualification"])
        self.assertTrue(result["source_unchanged"])
        self.assertTrue(result["baseline_verified_again"])
        self.assertTrue(result["initial_artifacts_unchanged"])
        self.assertTrue(result["layout_gate"]["passed"])
        self.assertTrue(result["rendered_page_text_exact"])
        self.assertEqual(len(calls), 2)
        self.assertEqual(
            [call.args[1].name for call in gate.call_args_list],
            ["TOY-ONLY-native-filled.hwpx", "TOY-ONLY-reopened.hwpx"],
        )
        self.assertFalse((self.output() / "review/approved-plan.json").exists())

    def test_a_good_intermediate_cannot_hide_bad_saved_hwp_metadata(self):
        with self.assertRaises(ValueError):
            self.fake_run(bad_reopened=True)
        report = m.r.read_json(self.output() / "TOY-ONLY-fill-verification.json")
        self.assertEqual(report["native_status"], "blocked")
        self.assertEqual(report["stage"], "saved_hwp_structural_gate")
        self.assertNotIn("layout_gate", report)
        self.assertFalse(report["qualification"])
        self.assertEqual(
            (self.output() / "source/TOY-ONLY-empty-template.hwp").read_bytes(),
            b"SYNTHETIC NOT HWP",
        )

    def test_noneligible_body_profile_stops_before_input_or_export(self):
        with self.assertRaises(ValueError):
            self.fake_run(baseline_linear="no")
        report = m.r.read_json(self.output() / "TOY-ONLY-fill-verification.json")
        self.assertEqual(report["stage"], "original_eligibility")
        self.assertFalse((self.output() / "TOY-ONLY-filled-input.hwpx").exists())

    def test_flags_frozen_existing_and_outside_never_start_native(self):
        with patch.object(m, "create_source") as create:
            for kwargs in (
                {},
                {"run_native": True},
                {"toy_only": True},
                {"run_native": 1, "toy_only": True},
            ):
                with self.assertRaises(ValueError):
                    m.run(self.output(), **kwargs)
            with (
                patch.object(m.sys, "frozen", True, create=True),
                self.assertRaises(ValueError),
            ):
                m.run(self.output(), run_native=True, toy_only=True)
            for path in (
                self.root / "outside",
                self.root / "work/toy-fill-OLD-PURPOSE",
            ):
                with self.assertRaises(ValueError):
                    m.run(path, run_native=True, toy_only=True)
            self.output().mkdir()
            with self.assertRaises(ValueError):
                m.run(self.output(), run_native=True, toy_only=True)
            create.assert_not_called()

    def test_explicit_addresses_and_labels_are_not_guessed(self):
        path = self.root / "SOURCE-SYNTHETIC.hwpx"
        package(path)
        mapped = m.f.inspect_template(path)
        regions = [{"page": 1, "region": [0, 0, 1, 1]}] * 3
        fields = m.own_targets(mapped, regions)
        self.assertEqual(
            [f["cell_address"]["rowAddr"] for f in fields], ["1", "2", "3"]
        )
        with self.assertRaises(ValueError):
            m.own_targets({"fields": mapped["fields"] + [fields[0]]}, regions)
        with self.assertRaises(ValueError):
            m.own_targets(mapped, [{**r, "page": 2} for r in regions])


if __name__ == "__main__":
    unittest.main()
