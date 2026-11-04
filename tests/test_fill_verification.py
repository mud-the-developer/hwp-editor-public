"""Detailed diagnostics and mocked apply on disposable fixtures ONLY; no Hancom."""
from copy import deepcopy
import json
from unittest.mock import patch
import unittest

import test_hwp_fill_plan as fixtures
r,fill=fixtures.r,fixtures.fill


class FillVerificationTests(unittest.TestCase):
    def setUp(self):
        self.fixture=fixtures.FillTests('test_exact_replay_regions_and_unsigned_policy_are_new_contract')
        self.fixture.setUp();self.addCleanup(self.fixture.temp.cleanup)
        self.w=self.fixture.w

    def verify(self,path,selected=None):
        return fill.rendered_text_verification(self.w/'baseline.pdf',path,selected or self.fixture.selected())

    def test_valid_pdf_has_no_failure_details(self):
        path=self.w/'normal.pdf';self.fixture.pdf(path,['Value A','Value B'])
        report=self.verify(path)
        self.assertEqual(report,{'policy':fill.RENDER_POLICY,'passed':True,'issues':[],'issue_count':0,'truncated':False})
        self.assertTrue(fill.rendered_text_gate(self.w/'baseline.pdf',path,self.fixture.selected()))

    def test_missing_value_and_outside_change_identify_field_and_page_without_values(self):
        path=self.w/'missing.pdf';self.fixture.pdf(path,['','Value B'],'PRIVATE EXTRA')
        report=self.verify(path);self.assertFalse(report['passed'])
        self.assertIn({'code':'rendered_text_mismatch','page':1,'field_id':self.fixture.targets[0]['field_id']},report['issues'])
        self.assertIn({'code':'outside_text_changed','page':1,'field_id':None},report['issues'])
        for text in ('Value A','Value B','PRIVATE EXTRA'):self.assertNotIn(text,json.dumps(report))

    def test_border_crossing_has_a_cell_specific_reason(self):
        path=self.w/'border.pdf'
        with r.fitz.open(self.w/'baseline.pdf') as doc:
            doc[0].insert_text((215,80),'Value A',fontsize=10)
            doc[0].insert_text((88,110),'Value B',fontsize=10);doc.save(path)
        self.assertIn({'code':'glyph_outside_cell','page':1,'field_id':self.fixture.targets[0]['field_id']},self.verify(path)['issues'])

    def test_page_count_change_is_not_a_guessed_field_navigation(self):
        path=self.w/'pages.pdf'
        with r.fitz.open(self.w/'baseline.pdf') as doc:doc.new_page();doc.save(path)
        self.assertEqual(self.verify(path)['issues'],[{'code':'page_count_changed','page':None,'field_id':None}])

    def test_many_failures_are_bounded_but_never_truncated_into_a_pass(self):
        selected=[{**self.fixture.selected()[0],'field_id':f'fixture-{number}'} for number in range(80)]
        report=self.verify(self.w/'baseline.pdf',selected)
        self.assertFalse(report['passed']);self.assertEqual(report['issue_count'],80)
        self.assertEqual(len(report['issues']),fill.RENDER_ISSUE_LIMIT);self.assertTrue(report['truncated'])

    def prepare_mocked_apply(self):
        manifest=deepcopy(self.fixture.manifest);manifest['source_audit']['embedded_binary_hashes']=['fixture-picture']
        r.write_json(self.w/'manifest.json',manifest)
        record=deepcopy(self.fixture.record);record['values']['name'][0]='A'*25
        r.write_json(self.w/'fill-input.json',{'bindings':self.fixture.bindings,'record':record})
        plan=fill.proposal(self.w,self.fixture.bindings,record,self.fixture.placements)
        path=self.w/'proposal-fill.json';r.write_json(path,plan)
        # Synthetic temporary consent fixture; never an owner workspace/actual approval.
        r.write_json(self.w/'approved-plan.json',{'approved_via':'local-review-ui','plan_sha256':r.plan_hash(plan),
                                                'plan':plan,'approved_scope':fill.approval_scope(plan)})
        return path,plan,fill.validate(plan,manifest,self.fixture.baseline),manifest

    def test_mocked_apply_persists_overprint_reason_and_preserves_input_and_consent(self):
        path,plan,selected,manifest=self.prepare_mocked_apply()
        files=['baseline.hwpx','baseline.pdf','manifest.json','fill-input.json','proposal-fill.json','approved-plan.json','fixture.hwp']
        before={name:r.file_hash(self.w/name) for name in files}
        def export(source,pdf,**kwargs):
            if 'hwpx' in kwargs:
                fill.write_input(self.fixture.baseline,kwargs['hwpx'],selected)
                kwargs['hwp'].write_bytes(b'SYNTHETIC-SAVED-NOT-HWP')
            with r.fitz.open(self.w/'baseline.pdf') as doc:
                for index in range(25):doc[0].insert_text((88+index*1.5,80),'A',fontsize=10)
                doc[0].insert_text((88,110),'Value B',fontsize=10);doc.save(pdf)
            return {'native_pages':1,'synthetic_mock_only':True}
        with patch.object(r,'native_export',side_effect=export) as native,patch.object(r,'hwp_audit',return_value={'embedded_binary_hashes':['fixture-picture']}):
            with self.assertRaisesRegex(ValueError,'NOT a deliverable'):r.apply(self.w,path)
            self.assertEqual(native.call_count,2,'one mocked save and one mocked reopen, no automatic retry')
        self.assertEqual(before,{name:r.file_hash(self.w/name) for name in files})
        runs=list((self.w/'runs').iterdir());self.assertEqual(len(runs),1)
        report=r.read_json(runs[0]/'verification.json')
        self.assertEqual(report['status'],'blocked');self.assertEqual(report['stage'],'rendered_verification')
        self.assertFalse(report['rendered_page_text_exact']);self.assertTrue(report['source_unchanged'])
        issue={'code':'glyph_overlap','page':1,'field_id':selected[0]['field_id']}
        self.assertEqual(report['rendered_text_verification']['issues'],[issue])
        attempt=r.review_attempts(self.w,manifest)[0];self.assertEqual(attempt['rendered_issues'],[issue])
        self.assertEqual(attempt['plan_sha256'],r.plan_hash(plan))
        self.assertEqual(r.review_versions(self.w,manifest)[0]['status'],'blocked')

    def test_mocked_export_failure_records_stage_without_reopen_or_fake_diagnostics(self):
        path,_,_,manifest=self.prepare_mocked_apply()
        with patch.object(r,'native_export',side_effect=RuntimeError('MOCK export failed')) as native:
            with self.assertRaisesRegex(RuntimeError,'MOCK export failed'):r.apply(self.w,path)
            self.assertEqual(native.call_count,1)
        attempt=r.review_attempts(self.w,manifest)[0]
        self.assertEqual(attempt['stage'],'native_edit_export');self.assertEqual(attempt['rendered_issues'],[])
        self.assertTrue(attempt['source_unchanged']);self.assertEqual(r.review_versions(self.w,manifest),[])

    def test_stored_untrusted_diagnostics_cannot_invent_fields_pages_or_status(self):
        field_id=self.fixture.targets[0]['field_id']
        valid={'code':'glyph_overlap','page':1,'field_id':field_id}
        report={'status':'blocked','rendered_page_text_exact':False,'rendered_text_verification':
                {'policy':fill.RENDER_POLICY,'passed':False,'issues':[valid]}}
        self.assertEqual(fill.review_render_issues(report,self.fixture.plan,1),[valid])
        for item in [{**valid,'page':True},{**valid,'page':2},{**valid,'field_id':'foreign'},
                     {**valid,'code':'<script>'},{**valid,'value':'PRIVATE'},
                     {'code':'outside_text_changed','page':999,'field_id':None},
                     {'code':'glyph_overlap','page':1,'field_id':None}]:
            changed=deepcopy(report);changed['rendered_text_verification']['issues']=[item]
            self.assertEqual(fill.review_render_issues(changed,self.fixture.plan,1),[])
        for value in (1,'false',True):
            changed=deepcopy(report);changed['rendered_text_verification']['passed']=value
            self.assertEqual(fill.review_render_issues(changed,self.fixture.plan,1),[])
        changed=deepcopy(report);changed['status']='checks_passed_needs_human_visual_review'
        self.assertEqual(fill.review_render_issues(changed,self.fixture.plan,1),[])
        changed=deepcopy(report);changed['rendered_text_verification']['issues']=[valid]*21
        self.assertEqual(fill.review_render_issues(changed,self.fixture.plan,1),[])
