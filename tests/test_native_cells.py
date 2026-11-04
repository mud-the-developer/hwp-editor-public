"""Read-only Native mapper against synthetic packages/fake scan APIs; no Hwp."""

import importlib
from pathlib import Path
import tempfile
import sys
from types import SimpleNamespace
import unittest
from unittest.mock import Mock
import xml.etree.ElementTree as ET
import zipfile

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
m = importlib.import_module("hwp_native_cells")
f = importlib.import_module("hwp_form_draft")
fixture = importlib.import_module("test_hwp_form_draft")
r = f.r
OPF = b"""<opf:package xmlns:opf="http://www.idpf.org/2007/opf/">
<opf:manifest><opf:item id="header" href="Contents/header.xml"/><opf:item id="section0" href="Contents/section0.xml"/></opf:manifest>
<opf:spine><opf:itemref idref="header" linear="yes"/><opf:itemref idref="section0" linear="yes"/></opf:spine></opf:package>"""


class NativeCellBindingTests(unittest.TestCase):
    def setUp(self):
        self.scan_items = iter([(1, "")])
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name).resolve()
        self.source = self.root / "SYNTHETIC-source-copy.hwp"
        self.source.write_bytes(b"SYNTHETIC NOT HWP")
        self.baseline = self.root / "baseline.hwpx"
        self.texts = ["성명", "", "성명", ""]
        self.package()
        self.selected = [
            field
            for field in f.inspect_template(self.baseline)["fields"]
            if field["supported"]
        ]
        self.positions = [[41, 0, 0], [9, 0, 0], [105, 0, 0], [3, 0, 0]]
        self.doc = SimpleNamespace(FullName=str(self.source), Modified=False)
        anchor = SimpleNamespace(Item=lambda key: {"List": 0, "Para": 2, "Pos": 0}[key])
        self.ctrl = SimpleNamespace(
            CtrlID="tbl", GetAnchorPos=lambda _: anchor, Next=None
        )
        self.app = SimpleNamespace(
            HeadCtrl=self.ctrl,
            PageCount=1,
            XHwpDocuments=SimpleNamespace(Count=1, Item=lambda _: self.doc),
            pos=[0, 0, 0],
            index=-1,
            buffer="SYNTHETIC WHOLE DOCUMENT",
            SaveAs=Mock(),
            Clear=Mock(),
            Quit=Mock(),
        )
        self.app.GetTextFile = Mock(side_effect=lambda *args: self.app.buffer)
        self.app.GetPos = lambda: tuple(self.app.pos)

        def set_pos(*pos):
            self.app.pos = list(pos)
            self.app.index = -1
            return True

        def move(action):
            if action == "MoveRight":
                self.app.index = 0
            elif action == "TableRightCell":
                self.app.index += 1
            elif action == "TableLeftCell":
                self.app.index -= 1
            elif action == "TableLowerCell":
                self.app.index += 2
            else:
                raise AssertionError("Unexpected action")
            self.app.pos = list(self.positions[self.app.index])
            return True

        def scan(*args):
            self.scan_items = iter(((2, self.texts[self.app.index] + "\r\n"), (1, "")))
            return True

        self.app.SetPos = Mock(side_effect=set_pos)
        self.app.Run = Mock(side_effect=move)
        self.app.InitScan = Mock(side_effect=scan)
        self.app.GetText = Mock(side_effect=lambda: next(self.scan_items))
        self.app.ReleaseScan = Mock()

    def package(
        self,
        *,
        table_attrs='rowCnt="2" colCnt="2"',
        extra="",
        cell_extra="",
        span="1",
        opf: bytes | str | None = OPF,
    ):
        rows = "".join(
            "<hp:tr>"
            + "".join(
                fixture.cell(
                    row,
                    col,
                    self.texts[row * 2 + col],
                    span=span if (row, col) == (0, 1) else "1",
                    extra=cell_extra if (row, col) == (0, 1) else "",
                )
                for col in range(2)
            )
            + "</hp:tr>"
            for row in range(2)
        )
        raw = (
            '<hs:sec xmlns:hs="http://www.hancom.co.kr/hwpml/2011/section" xmlns:hp="http://www.hancom.co.kr/hwpml/2011/paragraph"><hp:p><hp:run><hp:tbl '
            + table_attrs
            + ">"
            + rows
            + "</hp:tbl></hp:run></hp:p>"
            + extra
            + "</hs:sec>"
        )
        with zipfile.ZipFile(self.baseline, "w") as archive:
            archive.writestr("mimetype", "application/hwp+zip")
            archive.writestr("Contents/section0.xml", raw)
            archive.writestr("Contents/header.xml", "LOCKED SYNTHETIC STYLES")
            if opf is not None:
                archive.writestr("Contents/content.hpf", opf)

    def capture(self):
        return m.capture(
            self.app,
            self.source,
            r.file_hash(self.source),
            self.baseline,
            self.selected,
        )

    def test_profile_is_hash_bound_and_repeated_labels_do_not_alias_fields(self):
        profile = m.plain_profile(self.baseline, self.selected)
        self.assertEqual((profile["rows"], profile["cols"]), (2, 2))
        self.assertEqual(len(profile["targets"]), 2)
        self.assertNotEqual(
            profile["targets"][0]["field_id"], profile["targets"][1]["field_id"]
        )
        self.assertEqual(profile["baseline_hwpx_sha256"], r.file_hash(self.baseline))
        self.assertEqual(profile, m.plain_profile(self.baseline, self.selected))

    def test_explicit_and_default_yes_spines_remain_readonly_and_unqualified(self):
        source = self.source.read_bytes()
        for opf in (OPF, OPF.replace(b' linear="yes"', b"")):
            with self.subTest(opf=opf):
                self.package(opf=opf)
                baseline = self.baseline.read_bytes()
                result = self.capture()
                self.assertEqual(
                    [item["position"] for item in result["cells"]], self.positions
                )
                self.assertEqual(self.baseline.read_bytes(), baseline)
                self.assertEqual(self.source.read_bytes(), source)
        self.app.SaveAs.assert_not_called()

    def test_no_or_unknown_spine_stops_before_any_com_observation_or_navigation(self):
        for marker in (
            b'idref="header" linear="yes"',
            b'idref="section0" linear="yes"',
        ):
            for linear in (b"no", b"unknown", b"YES"):
                with self.subTest(marker=marker, linear=linear):
                    self.package(
                        opf=OPF.replace(
                            marker, marker.replace(b'"yes"', b'"' + linear + b'"')
                        )
                    )
                    before = self.baseline.read_bytes(), self.source.read_bytes()
                    with self.assertRaisesRegex(
                        ValueError, "Reading-order preservation unverified"
                    ):
                        self.capture()
                    self.assertEqual(
                        before, (self.baseline.read_bytes(), self.source.read_bytes())
                    )
        for method in (
            self.app.GetTextFile,
            self.app.SetPos,
            self.app.Run,
            self.app.InitScan,
            self.app.SaveAs,
            self.app.Clear,
            self.app.Quit,
        ):
            method.assert_not_called()

    def test_missing_or_ambiguous_spine_never_returns_a_profile(self):
        for opf in (
            None,
            b'<opf:package xmlns:opf="http://www.idpf.org/2007/opf/"/>',
            b'<opf:package xmlns:opf="http://www.idpf.org/2007/opf/"><opf:spine/></opf:package>',
            OPF.replace(
                b"</opf:package>",
                b'<opf:spine><opf:itemref idref="section0"/></opf:spine></opf:package>',
            ),
            OPF.replace(
                b"</opf:spine>", b'<opf:itemref idref="section0"/></opf:spine>'
            ),
            OPF.replace(b'idref="section0"', b'idref=""'),
            OPF.replace(b'"http://www.idpf.org/2007/opf/"', b'"urn:WRONG"'),
            OPF.replace(
                b'<opf:itemref idref="section0" linear="yes"/>', b"<opf:unknown/>"
            ),
        ):
            with self.subTest(opf=opf):
                self.package(opf=opf)
                with self.assertRaisesRegex(
                    ValueError, "Reading-order preservation unverified"
                ):
                    self.capture()
        self.app.GetTextFile.assert_not_called()
        self.app.SetPos.assert_not_called()

    def test_malformed_or_entity_opf_is_rejected_before_native_access(self):
        for opf in (
            b"<package>",
            b'<!DOCTYPE package [<!ENTITY x "VALUE">]><package>&x;</package>',
        ):
            with self.subTest(opf=opf):
                self.package(opf=opf)
                with self.assertRaises((ValueError, ET.ParseError)):
                    self.capture()
        self.app.GetTextFile.assert_not_called()
        self.app.SetPos.assert_not_called()

    def test_every_cell_text_is_verified_before_any_position_binding_is_returned(self):
        source = self.source.read_bytes()
        baseline = self.baseline.read_bytes()
        result = self.capture()
        self.assertEqual([item["position"] for item in result["cells"]], self.positions)
        self.assertEqual(
            [field["position"] for field in result["targets"]],
            [self.positions[1], self.positions[3]],
        )
        self.assertFalse(result["writer_qualified"])
        self.assertTrue(result["original_caret_restored"])
        self.assertEqual(self.app.pos, [0, 0, 0])
        self.assertEqual(self.app.ReleaseScan.call_count, 4)
        self.assertEqual(
            [call.args for call in self.app.InitScan.call_args_list], [(0, 0x55)] * 4
        )
        self.app.SaveAs.assert_not_called()
        self.app.Clear.assert_not_called()
        self.app.Quit.assert_not_called()
        self.assertEqual(self.source.read_bytes(), source)
        self.assertEqual(self.baseline.read_bytes(), baseline)

    def test_merged_inline_control_multiple_tables_and_bad_counts_are_rejected(self):
        for kwargs in (
            {"span": "2"},
            {"cell_extra": "<hp:ctrl/>"},
            {"extra": "<hp:p><hp:run><hp:tbl/></hp:run></hp:p>"},
            {"table_attrs": 'rowCnt="3" colCnt="2"'},
            {"table_attrs": 'rowCnt="99999" colCnt="2"'},
        ):
            with self.subTest(kwargs=kwargs):
                self.package(**kwargs)
                with self.assertRaises(ValueError):
                    m.plain_profile(self.baseline, self.selected)
        self.app.InitScan.assert_not_called()

    def test_whitespace_is_not_reinterpreted_as_an_empty_target(self):
        self.texts[1] = " "
        self.package()
        with self.assertRaises(ValueError):
            m.plain_profile(self.baseline, self.selected)

    def test_changed_document_is_not_navigated_during_cleanup(self):
        self.doc.FullName = str(self.root / "FOREIGN.hwp")
        with self.assertRaises(ValueError):
            self.capture()
        self.app.SetPos.assert_not_called()
        self.app.InitScan.assert_not_called()

    def test_foreign_selected_address_cannot_use_a_valid_field_id(self):
        changed = [
            {**field, "cell_address": {"rowAddr": "0", "colAddr": "0"}}
            for field in self.selected
        ]
        with self.assertRaises(ValueError):
            m.plain_profile(self.baseline, changed)

    def test_second_section_and_reordered_cells_are_not_guessed(self):
        with zipfile.ZipFile(self.baseline, "a") as archive:
            archive.writestr("Contents/section1.xml", "<sec/>")
        with self.assertRaises(ValueError):
            m.plain_profile(self.baseline, self.selected)
        self.package()
        parts = r.archive(self.baseline)
        parts["Contents/section0.xml"] = parts["Contents/section0.xml"].replace(
            b'rowAddr="0" colAddr="1"', b'rowAddr="1" colAddr="1"'
        )
        with zipfile.ZipFile(self.baseline, "w") as archive:
            for name, raw in parts.items():
                archive.writestr(name, raw)
        with self.assertRaises(ValueError):
            m.plain_profile(self.baseline, self.selected)

    def test_source_hash_identity_or_modified_state_block_before_scan(self):
        with self.assertRaises(ValueError):
            m.capture(self.app, self.source, "0" * 64, self.baseline, self.selected)
        self.doc.Modified = True
        with self.assertRaises(ValueError):
            self.capture()
        self.app.InitScan.assert_not_called()

    def test_text_mismatch_restores_caret_and_releases_scan(self):
        self.app.GetText.side_effect = iter(((2, "FOREIGN CELL\r\n"), (1, "")))
        with self.assertRaisesRegex(ValueError, "differs from baseline"):
            self.capture()
        self.assertEqual(self.app.pos, [0, 0, 0])
        self.app.ReleaseScan.assert_called_once()
        self.app.SaveAs.assert_not_called()

    def test_control_error_unknown_and_excessive_scan_calls_fail_closed(self):
        for responses in (
            ((4, ""),),
            ((101, ""),),
            ((False, ""),),
            tuple((2, "a") for _ in range(8)),
        ):
            self.app.GetText.side_effect = iter(responses)
            self.app.ReleaseScan.reset_mock()
            with self.assertRaises(ValueError):
                self.capture()
            self.assertEqual(self.app.pos, [0, 0, 0])
            self.app.ReleaseScan.assert_called_once()

    def test_scan_size_is_bounded_and_no_text_is_normalized_to_a_match(self):
        for text in ("a" * 4099, " 성명\r\n", "성명\n"):
            self.app.GetText.side_effect = iter(((2, text), (1, "")))
            with self.assertRaises(ValueError):
                self.capture()
        self.assertEqual(self.app.pos, [0, 0, 0])

    def test_duplicate_native_positions_and_multiple_controls_are_rejected(self):
        self.positions[1] = self.positions[0]
        with self.assertRaisesRegex(ValueError, "ambiguous"):
            self.capture()
        self.ctrl.Next = self.ctrl
        with self.assertRaises(ValueError):
            self.capture()

    def test_readonly_buffer_drift_never_returns_a_binding(self):
        def drift(*args):
            self.app.buffer = "MODIFIED"
            return (1, "")

        self.app.GetText.side_effect = drift
        with self.assertRaises(ValueError):
            self.capture()
        self.app.SaveAs.assert_not_called()


if __name__ == "__main__":
    unittest.main()
