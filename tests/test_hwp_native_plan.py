"""Synthetic native contracts only. Never approves or applies a real workspace."""
from copy import deepcopy
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
import tempfile
import unittest
import zipfile

import test_hwp_review as fixture
import native_hwp

r = fixture.r


class NativePlanTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name)
        self.base = self.path / "baseline.hwpx"
        self.xml = fixture.XML.replace(b"</hp:run></hp:p>", b'</hp:run><hp:linesegarray><hp:lineseg textpos="0"/><hp:lineseg textpos="16"/></hp:linesegarray></hp:p>', 1)
        fixture.make_hwpx(self.base, self.xml)
        anchors, _ = r.index_hwpx(self.base)
        for a in anchors:
            a.update(page_hint=1, boxes=[[10, 10, 90, 20], [10, 26, 30, 36]])
        self.manifest = {"workspace_id": "native-fixture-only", "baseline_hwpx_sha256": r.file_hash(self.base),
                         "anchors": anchors, "protected_pages": [], "pages": [{"page": 1}]}
        self.legacy = {"schema": 1, "workspace_id": self.manifest["workspace_id"],
                       "baseline_hwpx_sha256": self.manifest["baseline_hwpx_sha256"],
                       "edits": [{"anchor_id": anchors[0]["anchor_id"], "old_text": anchors[0]["old_text"], "new_text": "short"}]}
        self.plan = r.hwp_native_plan.proposal(self.legacy, self.manifest, self.base)
        self.new_xml = self.xml.replace(b"Original unique phrase", b"short").replace(b'<hp:lineseg textpos="16"/>', b"")

    def candidate(self, xml=None, header=b"styles", image=b"locked-image"):
        p = self.path / "candidate.hwpx"
        fixture.make_hwpx(p, self.new_xml if xml is None else xml, image=image, header=header)
        return p

    def test_native_requires_explicit_policy_and_exact_envelope(self):
        selected = r.validate_plan(self.plan, self.manifest, self.base)
        self.assertEqual(len(selected), 1)
        self.assertEqual(self.plan["edits"][0]["region"], [10, 10, 90, 36])
        self.assertEqual(self.legacy["schema"], 1)
        self.assertNotIn("region", self.legacy["edits"][0])
        for change in ("region", "policy"):
            p = deepcopy(self.plan)
            if change == "region": p["edits"][0]["region"][2] += 1
            else: p["native_policy"] = "ignore-all-xml"
            with self.assertRaises(ValueError): r.validate_plan(p, self.manifest, self.base)
        with self.assertRaises(ValueError): r.validate_plan(self.plan, self.manifest)

    def test_native_allows_only_selected_cache_and_exact_text(self):
        target = self.candidate()
        gate = r.hwp_native_plan.structural_gate(self.base, target, self.plan)
        self.assertTrue(gate["locked_table_font_controls_assets"])
        with self.assertRaises(ValueError): r.structural_gate(self.base, target, self.legacy["edits"])
        with self.assertRaises(ValueError): r.validate_plan(self.legacy, self.manifest, self.base)

    def test_native_rejects_unapproved_text_style_assets_or_controls(self):
        cases = [self.new_xml.replace(b"Other protected phrase", b"unapproved"),
                 self.new_xml.replace(b'charPrIDRef="2"', b'charPrIDRef="3"', 1),
                 self.new_xml.replace(b'paraPrIDRef="1"', b'paraPrIDRef="2"', 1),
                 self.new_xml.replace(b"</hp:run>", b"<hp:tab/></hp:run>", 1),
                 self.new_xml.replace(b"</hs:sec>", b"<!-- hidden --> </hs:sec>")]
        for xml in cases:
            with self.subTest(xml=xml):
                with self.assertRaises(ValueError): r.hwp_native_plan.structural_gate(self.base, self.candidate(xml), self.plan)
        for kwargs in ({"header": b"changed-style"}, {"image": b"changed-seal"}):
            with self.assertRaises(ValueError): r.hwp_native_plan.structural_gate(self.base, self.candidate(**kwargs), self.plan)

    def test_native_rejects_auto_shrunk_table_height(self):
        table=b'<hp:tbl id="fixed-fixture"><hp:sz width="41952" height="6426"/></hp:tbl>'
        before=self.xml.replace(b'Other protected phrase</hp:t>',b'Other protected phrase</hp:t>'+table)
        fixture.make_hwpx(self.base,before)
        after=before.replace(b'Original unique phrase',b'short').replace(b'<hp:lineseg textpos="16"/>',b'')
        after=after.replace(b'height="6426"',b'height="3546"')
        with self.assertRaisesRegex(ValueError,'locked structure'):
            r.hwp_native_plan.structural_gate(self.base,self.candidate(after),self.plan)

    def test_native_rejects_unknown_or_invalid_line_cache(self):
        for replacement in (b'<hp:lineseg textpos="9"/>', b'<hp:lineseg textpos="0" fake="3"/>',
                            b'<hp:lineseg textpos="0"><hp:tab/></hp:lineseg>', b''):
            xml = self.new_xml.replace(b'<hp:lineseg textpos="0"/>', replacement)
            with self.assertRaises(ValueError): r.hwp_native_plan.structural_gate(self.base, self.candidate(xml), self.plan)

    def test_native_rejects_unselected_cache_change(self):
        before = self.xml.replace(b"</hp:p>\n</hs:sec>", b'<hp:linesegarray><hp:lineseg textpos="0"/></hp:linesegarray></hp:p>\n</hs:sec>')
        fixture.make_hwpx(self.base, before)
        after = before.replace(b"Original unique phrase", b"short").replace(b'<hp:lineseg textpos="16"/>', b'')
        after = after.replace(b'<hp:lineseg textpos="0"/></hp:linesegarray></hp:p>\n</hs:sec>', b'<hp:lineseg textpos="1"/></hp:linesegarray></hp:p>\n</hs:sec>')
        with self.assertRaises(ValueError): r.hwp_native_plan.structural_gate(self.base, self.candidate(after), self.plan)

    def test_old_text_approval_cannot_approve_native_regions(self):
        # Artificial approval in the TEMP fixture only, never the actual workspace.
        r.write_json(self.path / "approved-plan.json", {"approved_via": "local-review-ui", "plan": self.plan,
                                                      "plan_sha256": r.plan_hash(self.plan)})
        with self.assertRaisesRegex(ValueError, "Explicit UI confirmation"):
            r.approved_plan(self.path, self.plan, self.manifest)
        approval = r.read_json(self.path / "approved-plan.json")
        approval["approved_scope"] = r.native_approval_scope(self.plan)
        r.write_json(self.path / "approved-plan.json", approval)
        r.approved_plan(self.path, self.plan, self.manifest)
        changed = deepcopy(self.plan); changed["edits"][0]["new_text"] = "other"
        with self.assertRaises(ValueError): r.approved_plan(self.path, changed, self.manifest)

    def test_native_apply_routes_only_the_fresh_scope_approved_plan(self):
        source = self.path / 'SYNTHETIC-ONLY.hwp';source.write_bytes(b'synthetic source only')
        manifest = {**self.manifest, 'source':str(source), 'source_audit':{'sha256':r.file_hash(source),'embedded_binary_hashes':[]}}
        r.write_json(self.path/'approved-plan.json', {'approved_via':'local-review-ui','plan':self.plan,
                     'plan_sha256':r.plan_hash(self.plan),'approved_scope':r.native_approval_scope(self.plan)})
        plan_path = self.path/'proposal-native.json';r.write_json(plan_path,self.plan)
        calls=[]
        def export(source, pdf, **kwargs):
            calls.append(kwargs)
            if 'native_plan' in kwargs:
                fixture.make_hwpx(kwargs['hwpx'], self.new_xml)
                kwargs['hwp'].write_bytes(b'synthetic edited source only')
            pdf.write_bytes(b'synthetic PDF - checks are mocked in this routing test')
            return {'native_pages':1}
        with patch.object(r,'verify_baseline',return_value=manifest), patch.object(r,'native_export',side_effect=export), \
             patch.object(r,'compare_pdfs',return_value={'passed':True}) as layout, \
             patch.object(r,'hwp_audit',return_value={'embedded_binary_hashes':[]}), \
             patch.object(r,'rendered_text_gate',return_value=True), patch.object(r,'patch_hwpx',side_effect=AssertionError('Native must not use byte patch')):
            result=r.apply(self.path,plan_path)
        self.assertEqual(result['status'],'checks_passed_needs_human_visual_review')
        self.assertEqual(calls[0]['native_workspace'],self.path.resolve())
        self.assertEqual(layout.call_args.args[2],{1:[self.plan['edits'][0]['region']]})
        self.assertTrue(result['native_contract']['locked_table_font_controls_assets'])
        self.assertEqual(len(calls),2)

    def test_native_scope_failure_never_creates_a_run_or_calls_hancom(self):
        r.write_json(self.path/'approved-plan.json',{'approved_via':'local-review-ui','plan':self.legacy,'plan_sha256':r.plan_hash(self.legacy)})
        plan_path=self.path/'proposal-native.json';r.write_json(plan_path,self.plan)
        with patch.object(r,'verify_baseline',return_value=self.manifest), patch.object(r,'native_export') as export:
            with self.assertRaises(ValueError):r.apply(self.path,plan_path)
        export.assert_not_called();self.assertFalse((self.path/'runs').exists())

    def test_native_derived_metadata_does_not_allow_other_metadata(self):
        settings = b'<ha:HWPApplicationSetting xmlns:ha="http://www.hancom.co.kr/hwpml/2011/app"><ha:CaretPosition listIDRef="0" paraIDRef="0" pos="0"/></ha:HWPApplicationSetting>'
        package = b'<opf:package xmlns:opf="http://www.idpf.org/2007/opf/"><opf:metadata><opf:title>locked</opf:title><opf:meta name="ModifiedDate">2026-10-08T12:00:00Z</opf:meta></opf:metadata><opf:spine><opf:itemref idref="section0"/></opf:spine></opf:package>'
        with zipfile.ZipFile(self.base, 'a') as z:
            z.writestr('settings.xml', settings);z.writestr('Contents/content.hpf', package);z.writestr('Preview/PrvText.txt', b'old preview')
        for tamper in (False, True):
            target = self.candidate()
            with zipfile.ZipFile(target, 'a') as z:
                z.writestr('settings.xml', settings.replace(b'pos="0"', b'pos="5"'))
                updated = package.replace(b'08T12:', b'09T13:').replace(b'idref="section0"', b'idref="section0" linear="yes"')
                if tamper: updated = updated.replace(b'locked', b'unapproved')
                z.writestr('Contents/content.hpf', updated);z.writestr('Preview/PrvText.txt', b'new preview')
            if tamper:
                with self.assertRaises(ValueError): r.hwp_native_plan.structural_gate(self.base, target, self.plan)
            else: r.hwp_native_plan.structural_gate(self.base, target, self.plan)

    def test_comments_cannot_shift_the_native_cache_permission(self):
        before=self.xml.replace(b'<hp:p ', b'<!-- preserved root marker --><hp:p ',1)
        fixture.make_hwpx(self.base,before)
        after=before.replace(b'Original unique phrase',b'short').replace(b'<hp:lineseg textpos="16"/>',b'')
        r.hwp_native_plan.structural_gate(self.base,self.candidate(after),self.plan)
        after=after.replace(b'<!-- preserved root marker -->',b'<!-- changed marker -->')
        with self.assertRaises(ValueError):r.hwp_native_plan.structural_gate(self.base,self.candidate(after),self.plan)

    def test_native_region_change_invalidates_plan_hash(self):
        p = deepcopy(self.plan);p['edits'][0]['region'][3] += 1
        self.assertNotEqual(r.plan_hash(self.plan), r.plan_hash(p))


class FakeApp:
    def __init__(self, old="Old unique phrase"):
        self.old = old;self.calls = [];self.inserted = False
        self.HParameterSet = SimpleNamespace(HFindReplace=SimpleNamespace(HSet=object()), HInsertText=SimpleNamespace(HSet=object()))
        self.HAction = SimpleNamespace(GetDefault=lambda *args: None, Execute=self.execute)
    def GetTextFile(self, fmt, options): return self.old
    def SetPos(self, *args): return True
    def GetPos(self): return (0, 0, 16)
    def FindDir(self, name): return 0
    def GetSelectedPos(self): return (True, 4, 0, 0, 4, 0, len(self.old.encode('utf-16-le'))//2)
    def execute(self, name, params): self.calls.append(name);self.inserted |= name=='InsertText';return True


class NativeSelectionTests(unittest.TestCase):
    def test_native_one_exact_selection_and_literal_flags(self):
        app = FakeApp();edit = {'anchor_id':'synthetic', 'old_text':app.old, 'new_text':'new'}
        result = native_hwp.replace_plain_selection(app, edit)
        self.assertTrue(result['old_text_exact']);self.assertEqual(app.calls, ['RepeatFind','InsertText'])
        self.assertEqual(app.HParameterSet.HFindReplace.FindRegExp, 0)
        self.assertEqual(app.HParameterSet.HFindReplace.UseWildCards, 0)
        self.assertEqual(app.HParameterSet.HInsertText.Text, 'new')

    def test_bad_native_selection_stops_before_insertion(self):
        app = FakeApp();app.GetSelectedPos=lambda:(True,4,0,0,4,1,17)
        with self.assertRaises(ValueError): native_hwp.replace_plain_selection(app, {'anchor_id':'x','old_text':app.old,'new_text':'new'})
        self.assertFalse(app.inserted)

    def test_ambiguous_native_literal_stops(self):
        app = FakeApp();app.GetTextFile=lambda *args:app.old+' '+app.old
        with self.assertRaises(ValueError): native_hwp.replace_plain_selection(app, {'anchor_id':'x','old_text':app.old,'new_text':'new'})
        self.assertEqual(app.calls, [])
