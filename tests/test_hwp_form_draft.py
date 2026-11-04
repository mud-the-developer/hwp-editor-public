"""Offline synthetic empty-cell contracts only; never native apply or approval."""

from copy import deepcopy
import json
from pathlib import Path
import subprocess
import sys
import tempfile
from typing import Any
import unittest
import zipfile

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import hwp_form_draft as f


def cell(row, col, text="", span="1", extra=""):
    return (
        f'<hp:tc><hp:subList><hp:p id="7" paraPrIDRef="2">'
        f'<hp:run charPrIDRef="3"><hp:t>{text}</hp:t>{extra}</hp:run>'
        '<hp:linesegarray><hp:lineseg textpos="0"/></hp:linesegarray></hp:p></hp:subList>'
        f'<hp:cellAddr rowAddr="{row}" colAddr="{col}"/><hp:cellSpan rowSpan="{span}" colSpan="1"/></hp:tc>'
    )


def package(path, cells=None, extra="", declaration="", *, plain_native=False):
    if plain_native and cells is not None:
        raise ValueError("Explicit Native fixture uses its fixed 2x2 cells")
    cells = (
        cells
        if cells is not None
        else cell(0, 0, "성명") + cell(0, 1) + cell(1, 0, "성명") + cell(1, 1)
    )
    table = "<hp:tbl><hp:tr>" + cells + "</hp:tr></hp:tbl>"
    if plain_native:
        table = (
            '<hp:tbl rowCnt="2" colCnt="2"><hp:tr>'
            + cell(0, 0, "성명")
            + cell(0, 1)
            + "</hp:tr><hp:tr>"
            + cell(1, 0, "성명")
            + cell(1, 1)
            + "</hp:tr></hp:tbl>"
        )
    raw = (
        '<?xml version="1.0" encoding="UTF-8"?>'
        + declaration
        + '<hs:sec xmlns:hs="http://www.hancom.co.kr/hwpml/2011/section" '
        + 'xmlns:hp="http://www.hancom.co.kr/hwpml/2011/paragraph">'
        + "<hp:p><hp:run>"
        + table
        + "</hp:run></hp:p>"
        + extra
        + "</hs:sec>"
    )
    with zipfile.ZipFile(path, "w") as z:
        z.writestr("mimetype", "application/hwp+zip")
        if plain_native:
            z.writestr(
                "Contents/content.hpf",
                b'<opf:package xmlns:opf="http://www.idpf.org/2007/opf/"><opf:manifest><opf:item id="header" href="Contents/header.xml"/><opf:item id="section0" href="Contents/section0.xml"/></opf:manifest><opf:spine><opf:itemref idref="header" linear="yes"/><opf:itemref idref="section0" linear="yes"/></opf:spine></opf:package>',
            )
        z.writestr("Contents/section0.xml", raw.encode())
        z.writestr("Contents/header.xml", b"LOCKED-STYLES")
        z.writestr("BinData/locked.png", b"FAKE-IMAGE-TEST-ONLY")


class DraftTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.source = self.root / "synthetic.hwpx"
        package(self.source)
        self.before = self.source.read_bytes()
        self.mapped = f.inspect_template(self.source)
        self.targets = [item for item in self.mapped["fields"] if item["supported"]]
        self.bindings = {
            "schema": "hwp-fill-bindings/v1",
            "fields": [
                {
                    "field_id": item["field_id"],
                    "key": "성명",
                    "kind": "text",
                    "required": True,
                    "max_utf16_units": 20,
                    "max_lines": 1,
                }
                for item in self.targets
            ],
        }
        self.record: dict[str, dict[str, Any]] = {
            "values": {"성명": ["가상 시험자 A", "가상 시험자 B"]},
            "sources": {"성명": ["synthetic row A", "synthetic row B"]},
        }

    def build(self):
        return f.draft(self.source, self.mapped, self.bindings, self.record)

    def test_blank_targets_are_distinct_despite_repeated_paragraph_ids_and_labels(self):
        self.assertEqual(len(self.targets), 2)
        self.assertEqual(len({item["field_id"] for item in self.targets}), 2)
        self.assertEqual(self.targets[0]["label_hints"], ["성명"])
        self.assertEqual(self.targets[0]["character_style"], "3")
        self.assertFalse(self.mapped["fields"][0]["supported"])
        self.assertEqual(self.mapped["render_mapping"], "not_performed")

    def test_full_array_draft_has_no_execution_or_approval_and_source_is_unchanged(
        self,
    ):
        value = self.build()
        self.assertEqual(value["status"], "needs_review")
        self.assertEqual(value["approval_status"], "not_approved")
        self.assertFalse(value["execution_enabled"])
        self.assertFalse(value["layout_budget_verified"])
        self.assertEqual(value["native_verification"], "not_run")
        self.assertEqual(self.source.read_bytes(), self.before)
        self.assertEqual(value, self.build(), "deterministic idempotent drafts")
        self.record["sources"]["성명"][0] = "different source"
        self.assertNotEqual(value["draft_sha256"], self.build()["draft_sha256"])

    def test_existing_document_apply_contract_rejects_an_unapproved_field_draft(self):
        with self.assertRaises(ValueError):
            f.r.validate_plan(self.build(), {"workspace_id": "synthetic-only"})

    def test_repeated_fields_never_broadcast_or_ignore_cardinality(self):
        for values in (
            "one scalar",
            ["one"],
            ["a", "b", "extra"],
            ["a", "b", "c", "d"],
        ):
            with self.subTest(values=values):
                self.record["values"]["성명"] = values
                with self.assertRaises(ValueError):
                    self.build()

    def test_sources_need_same_cardinality(self):
        self.record["sources"]["성명"] = ["only one"]
        with self.assertRaises(ValueError):
            self.build()

    def test_missing_required_data_is_blocked_not_invented(self):
        self.record["values"] = {}
        self.record["sources"] = {}
        value = self.build()
        self.assertEqual(value["status"], "blocked")
        self.assertTrue(all(item["value"] is None for item in value["assignments"]))
        self.assertEqual(
            [e["code"] for e in value["issues"]], ["required_value_missing"] * 2
        )

    def test_optional_missing_values_stay_unfilled(self):
        for item in self.bindings["fields"]:
            item["required"] = False
        self.record = {"values": {}, "sources": {}}
        self.assertTrue(
            all(item["status"] == "unfilled" for item in self.build()["assignments"])
        )

    def test_unknown_keys_and_duplicate_binding_never_silently_pass(self):
        self.record["values"]["hidden-extra"] = "x"
        with self.assertRaises(ValueError):
            self.build()
        del self.record["values"]["hidden-extra"]
        self.bindings["fields"][1] = deepcopy(self.bindings["fields"][0])
        with self.assertRaises(ValueError):
            self.build()

    def test_budget_or_control_characters_block_without_normalizing(self):
        for value, expected in [
            ("가" * 21, "content_budget_exceeded"),
            ("😀" * 11, "content_budget_exceeded"),
            ("a\nb", "content_budget_exceeded"),
            ("a\rb", "unsupported_control_character"),
            ("a\x00b", "unsupported_control_character"),
        ]:
            with self.subTest(value=value):
                self.record["values"]["성명"][0] = value
                draft = self.build()
                self.assertIn(expected, draft["assignments"][0]["issues"])
                self.assertEqual(draft["assignments"][0]["value"], value)
        self.record["values"]["성명"][0] = "\ud800"
        with self.assertRaises(ValueError):
            self.build()

    def test_duplicate_logical_coordinates_are_not_disambiguated_by_xml_path(self):
        package(self.source, cells=cell(0, 0) + cell(0, 0))
        mapped = f.inspect_template(self.source)
        self.assertTrue(all(not item["supported"] for item in mapped["fields"]))
        self.assertTrue(
            all(
                "ambiguous_cell_coordinate" in item["issues"]
                for item in mapped["fields"]
            )
        )

    def test_native_slot_markers_and_structural_controls_are_discovery_only(self):
        package(
            self.source,
            cells=cell(
                0,
                0,
                extra='<hp:ctrl><hp:fieldBegin name="NAME" type="CLICK_HERE"/></hp:ctrl>',
            ),
        )
        self.assertFalse(f.inspect_template(self.source)["fields"][0]["supported"])

    def test_map_must_match_types_not_just_python_equal_values(self):
        bad = deepcopy(self.mapped)
        bad["fields"][1]["supported"] = 1
        with self.assertRaises(ValueError):
            f.draft(self.source, bad, self.bindings, self.record)

    def test_sources_are_required_but_not_claimed_verified(self):
        self.record["sources"]["성명"][0] = ""
        self.assertIn("source_required", self.build()["assignments"][0]["issues"])

    def test_field_types_use_literal_dates_numbers_and_checkbox_values(self):
        for kind, bad, good in [
            ("date", "2026-02-30", "2026-02-28"),
            ("integer", "one", "1"),
            ("decimal", "1,234", "1234.5"),
            ("checkbox", "yes", "checked"),
        ]:
            with self.subTest(kind=kind):
                self.bindings["fields"][0]["kind"] = kind
                self.record["values"]["성명"][0] = bad
                self.assertTrue(self.build()["assignments"][0]["issues"])
                self.record["values"]["성명"][0] = good
                self.assertFalse(self.build()["assignments"][0]["issues"])

    def test_stale_source_and_tampered_map_or_unsupported_cells_are_rejected(self):
        bad = deepcopy(self.mapped)
        bad["fields"][0]["supported"] = True
        with self.assertRaises(ValueError):
            f.draft(self.source, bad, self.bindings, self.record)
        self.bindings["fields"][0]["field_id"] = self.mapped["fields"][0]["field_id"]
        with self.assertRaises(ValueError):
            self.build()
        package(self.source, extra="<hp:p><hp:run><hp:t>changed</hp:t></hp:run></hp:p>")
        with self.assertRaises(ValueError):
            self.build()

    def test_merged_nested_control_whitespace_and_multiple_runs_are_not_auto_filled(
        self,
    ):
        for cells in [
            cell(0, 0, span="2"),
            cell(0, 0, " "),
            cell(0, 0, extra="<hp:ctrl/>"),
            cell(0, 0).replace("</hp:run>", "</hp:run><hp:run><hp:t/></hp:run>"),
        ]:
            with self.subTest(cells=cells):
                package(self.source, cells=cells)
                self.assertFalse(
                    any(
                        item["supported"]
                        for item in f.inspect_template(self.source)["fields"]
                    )
                )
        package(
            self.source,
            cells=cell(
                0, 0, extra="<hp:tbl><hp:tr>" + cell(0, 0) + "</hp:tr></hp:tbl>"
            ),
        )
        mapped = f.inspect_template(self.source)
        self.assertEqual(len(mapped["fields"]), 2)
        self.assertFalse(mapped["fields"][0]["supported"])
        self.assertNotEqual(
            mapped["fields"][0]["table_path"], mapped["fields"][1]["table_path"]
        )

    def test_xml_entities_and_duplicate_json_keys_are_rejected(self):
        package(self.source, declaration='<!DOCTYPE sec [<!ENTITY x "injected">]>')
        with self.assertRaises(ValueError):
            f.inspect_template(self.source)
        data = self.root / "record.json"
        for raw in ['{"values":{},"values":{"x":"y"}}', '{"value":NaN}', "[]"]:
            data.write_text(raw)
            with self.assertRaises(ValueError):
                f.read_data(data)

    def test_review_is_escaped_static_and_never_writes_approval(self):
        self.record["values"]["성명"][0] = "<script>alert(1)</script>"
        text = f.review_html(self.build())
        self.assertNotIn("<script>", text)
        self.assertIn("&lt;script&gt;", text)
        self.assertIn("default-src 'none'", text)
        self.assertIn("미승인", text)
        self.assertNotIn("<button", text)
        self.assertFalse((self.root / "approved-plan.json").exists())

    def test_binding_schema_closed_and_budgets_require_non_bool_integers(self):
        for change in [
            {"max_lines": True},
            {"max_utf16_units": 0},
            {"required": 1},
            {"kind": {}},
            {"approved": True},
            {"field_id": []},
        ]:
            original = deepcopy(self.bindings)
            self.bindings["fields"][0].update(change)
            with self.assertRaises(ValueError):
                self.build()
            self.bindings = original

    def test_cli_draft_emits_only_unapproved_review_and_blocked_input_has_nonzero_exit(
        self,
    ):
        script = Path(__file__).resolve().parents[1] / "scripts/hwp_form_draft.py"
        data = [
            ("map", self.mapped),
            ("bindings", self.bindings),
            ("record", self.record),
        ]
        for name, value in data:
            (self.root / (name + ".json")).write_text(
                json.dumps(value), encoding="utf-8"
            )
        command = [sys.executable, "-X", "utf8", str(script), "draft", str(self.source)]
        command += [str(self.root / (name + ".json")) for name, _ in data]
        result = subprocess.run(
            command + [str(self.root / "preview")], capture_output=True
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        value = f.read_data(self.root / "preview/fill-draft.json")
        self.assertFalse(value["execution_enabled"])
        self.assertTrue((self.root / "preview/review.html").is_file())
        self.record["values"] = {}
        self.record["sources"] = {}
        (self.root / "record.json").write_text(json.dumps(self.record))
        blocked = subprocess.run(
            command + [str(self.root / "blocked")], capture_output=True
        )
        self.assertEqual(blocked.returncode, 1, blocked.stderr)
        self.assertEqual(
            f.read_data(self.root / "blocked/fill-draft.json")["status"], "blocked"
        )
        self.assertFalse(list(self.root.rglob("approved-plan.json")))
        self.assertEqual(self.source.read_bytes(), self.before)

    def test_cli_is_offline_and_refuses_overwriting_output(self):
        script = Path(__file__).resolve().parents[1] / "scripts/hwp_form_draft.py"
        output = self.root / "map-output"
        result = subprocess.run(
            [
                sys.executable,
                "-X",
                "utf8",
                str(script),
                "map",
                str(self.source),
                str(output),
            ],
            capture_output=True,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(
            json.loads((output / "template-map.json").read_text(encoding="utf-8")),
            self.mapped,
        )
        self.assertFalse((output / "approved-plan.json").exists())
        again = subprocess.run(
            [sys.executable, str(script), "map", str(self.source), str(output)],
            capture_output=True,
        )
        self.assertNotEqual(again.returncode, 0)
        self.assertEqual(self.source.read_bytes(), self.before)
