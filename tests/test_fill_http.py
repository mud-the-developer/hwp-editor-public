"""Fresh UI consent tests on disposable synthetic fixtures, never owner documents."""
from copy import deepcopy
import http.client
import json
from pathlib import Path
import re
import shutil
import subprocess
import sys
import unittest

import test_hwp_fill_plan as fixtures

ROOT = Path(__file__).resolve().parents[1]


class FillHttpTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.fixture = fixtures.FillTests('test_exact_replay_regions_and_unsigned_policy_are_new_contract')
        cls.fixture.setUp();cls.w=cls.fixture.w;cls.r=fixtures.r
        cls.r.write_json(cls.w/'fill-input.json',{'bindings':cls.fixture.bindings,'record':cls.fixture.record})
        (cls.w/'pages').mkdir()
        with cls.r.fitz.open(cls.w/'baseline.pdf') as pdf:pdf[0].get_pixmap(dpi=100).save(cls.w/'pages/page-001.png')
        cls.process=subprocess.Popen([sys.executable,'-X','utf8',str(ROOT/'scripts/hwp_review.py'),'serve',str(cls.w),'--port','0'],
            stdout=subprocess.PIPE,stderr=subprocess.DEVNULL,text=True,encoding='utf-8')  # request logs must not fill an undrained pipe
        line=cls.process.stdout.readline()
        if not line.startswith('Local review: http://127.0.0.1:'):
            cls.process.terminate();raise RuntimeError('Synthetic fill server failed: '+line)
        cls.port=int(line.strip().rsplit(':',1)[-1]);cls.origin=f'http://127.0.0.1:{cls.port}'
        code,body=cls.request('GET','/fill');assert code==200
        cls.token=re.search(r"const token='([a-f0-9]+)'",body.decode()).group(1)

    @classmethod
    def tearDownClass(cls):
        cls.process.terminate();cls.process.communicate(timeout=10);cls.fixture.temp.cleanup()

    @classmethod
    def request(cls,method,path,payload=None,headers=None):
        connection=http.client.HTTPConnection('127.0.0.1',cls.port,timeout=10)
        try:
            connection.request(method,path,body=json.dumps(payload).encode() if payload is not None else None,headers=headers or {})
            response=connection.getresponse();body=response.read();return response.status,body
        finally:
            connection.close()

    def setUp(self):
        if (self.w/'runs').exists():shutil.rmtree(self.w/'runs')  # disposable fixture only
        (self.w/self.r.APPLY_LOCK).unlink(missing_ok=True)  # reset mock lock, never an owner workspace
        for path in ['approved-plan.json','proposal-fill.json']:(self.w/path).unlink(missing_ok=True)
        self.r.write_json(self.w/'fill-input.json',{'bindings':self.fixture.bindings,'record':self.fixture.record})

    def post(self,path,payload,**overrides):
        headers={'Origin':self.origin,'X-Review-Token':self.token,'Content-Type':'application/json'};headers.update(overrides)
        return self.request('POST',path,payload,headers)

    def compile(self):
        code,body=self.request('GET','/api/fill-state');self.assertEqual(code,200)
        state=json.loads(body)
        code,body=self.post('/api/fill-proposal',{'input_sha256':state['input_sha256'],'placements':self.fixture.placements},**{'If-Match':'"'+state['proposal_revision']+'"'})
        self.assertEqual(code,200,body)
        return json.loads(body)['proposal_sha256']

    def current_revision(self):
        code,body=self.request('GET','/api/fill-state');self.assertEqual(code,200)
        return '"'+json.loads(body)['proposal_revision']+'"'

    def consent(self,sha):
        return {'plan_sha256':sha,'proposal_kind':'fill','fill_policy_confirmed':True,
                'unsigned_template_confirmed':True,'header_spine_confirmed':True,'fields_reviewed':[p['field_id'] for p in self.fixture.placements]}

    def test_compile_never_approves_and_same_origin_is_mandatory(self):
        sha=self.compile();self.assertEqual(len(sha),64)
        self.assertFalse((self.w/'approved-plan.json').exists())
        code,_=self.post('/api/approve',self.consent(sha),Origin='http://attacker.invalid')
        self.assertEqual(code,403);self.assertFalse((self.w/'approved-plan.json').exists())
        code,_=self.post('/api/approve',self.consent(sha),**{'X-Review-Token':'wrong'})
        self.assertEqual(code,403)

    def test_exact_fields_policy_unsigned_and_hash_all_required(self):
        sha=self.compile();payload=self.consent(sha)
        for changes in [{'unsigned_template_confirmed':False},{'fill_policy_confirmed':False},{'header_spine_confirmed':False},
                        {'fields_reviewed':[]},{'plan_sha256':'0'*64},{'approved':True}]:
            code,_=self.post('/api/approve',{**payload,**changes})
            self.assertEqual(code,400);self.assertFalse((self.w/'approved-plan.json').exists())
        code,body=self.post('/api/approve',payload);self.assertEqual(code,200,body)
        plan=self.r.read_json(self.w/'proposal-fill.json')
        self.assertEqual(len(self.r.approved_plan(self.w,plan,self.fixture.manifest)),2)
        code,body=self.request('GET','/api/fill-state');self.assertEqual(code,200)
        self.assertEqual(json.loads(body)['approved_sha256'],sha)

    def test_stale_input_and_mask_enlargement_are_not_approved(self):
        sha=self.compile()
        changed=json.loads(json.dumps(self.fixture.record));changed['values']['name'][0]='Changed'
        self.r.write_json(self.w/'fill-input.json',{'bindings':self.fixture.bindings,'record':changed})
        code,_=self.post('/api/approve',self.consent(sha));self.assertEqual(code,400)
        code,_=self.post('/api/fill-proposal',{'input_sha256':'0'*64,'placements':self.fixture.placements},**{'If-Match':self.current_revision()});self.assertEqual(code,400)
        code,body=self.request('GET','/api/fill-state');self.assertEqual(code,200);state=json.loads(body)
        placements=json.loads(json.dumps(self.fixture.placements));placements[0]['region']=[0,0,300,400]
        code,_=self.post('/api/fill-proposal',{'input_sha256':state['input_sha256'],'placements':placements},**{'If-Match':self.current_revision()});self.assertEqual(code,400)
        self.assertFalse((self.w/'approved-plan.json').exists())

    def test_numeric_confirmation_is_not_boolean_consent(self):
        sha=self.compile();payload=self.consent(sha)
        for flag in ('fill_policy_confirmed','unsigned_template_confirmed','header_spine_confirmed'):
            for value in (1,1.0,'true',None):
                with self.subTest(flag=flag,value=value):
                    code,_=self.post('/api/approve',{**payload,flag:value})
                    self.assertEqual(code,400);self.assertFalse((self.w/'approved-plan.json').exists())

    def test_missing_or_stale_proposal_revision_preserves_existing_proposal_and_approval(self):
        code,body=self.request('GET','/api/fill-state');self.assertEqual(code,200);state=json.loads(body)
        body={'input_sha256':state['input_sha256'],'placements':self.fixture.placements}
        code,_=self.post('/api/fill-proposal',body);self.assertEqual(code,428)
        stale='"'+state['proposal_revision']+'"';sha=self.compile()
        code,_=self.post('/api/approve',self.consent(sha));self.assertEqual(code,200)
        paths=[self.w/'proposal-fill.json',self.w/'approved-plan.json'];before=[self.r.file_hash(p) for p in paths]
        code,_=self.post('/api/fill-proposal',body,**{'If-Match':stale});self.assertEqual(code,409)
        self.assertEqual(before,[self.r.file_hash(p) for p in paths])

    def test_duplicate_or_nonfinite_json_request_never_writes_approval(self):
        sha=self.compile();valid=json.dumps(self.consent(sha))
        bodies=[valid[:-1]+',"fill_policy_confirmed":true}', valid.replace('true','NaN',1),
                valid.replace('true','Infinity',1),valid.replace('true','1e309',1)]
        for body in bodies:
            connection=http.client.HTTPConnection('127.0.0.1',self.port,timeout=10)
            connection.request('POST','/api/approve',body=body,headers={'Origin':self.origin,'X-Review-Token':self.token,'Content-Type':'application/json'})
            response=connection.getresponse();response.read();connection.close()
            self.assertEqual(response.status,400);self.assertFalse((self.w/'approved-plan.json').exists())

    def test_failed_render_diagnostics_are_bounded_source_bound_and_do_not_change_consent(self):
        sha=self.compile();code,_=self.post('/api/approve',self.consent(sha));self.assertEqual(code,200)
        preserved=['fill-input.json','proposal-fill.json','approved-plan.json','baseline.hwpx','baseline.pdf']
        before={name:self.r.file_hash(self.w/name) for name in preserved}
        plan=self.r.read_json(self.w/'proposal-fill.json');field_id=plan['placements'][0]['field_id']
        run=self.w/'runs/20261011T000000-a1b2c3d4';run.mkdir(parents=True)
        issue={'code':'glyph_overlap','page':1,'field_id':field_id}
        self.r.write_json(run/'plan.json',plan)
        self.r.write_json(run/'verification.json',{'status':'blocked','stage':'rendered_verification','error':'<script>synthetic</script>',
            'rendered_page_text_exact':False,'rendered_text_verification':{'policy':fixtures.fill.RENDER_POLICY,'passed':False,
                'issues':[issue,{**issue,'field_id':'foreign'},{**issue,'page':True},{**issue,'code':'execute-command'}]}})
        self.fixture.pdf(run/'candidate.pdf',['Value A','Value B'])  # mock artifact, not a native fidelity claim
        code,body=self.request('GET','/api/fill-state');self.assertEqual(code,200);state=json.loads(body)
        self.assertEqual(state['proposal_sha256'],sha);self.assertEqual(state['approved_sha256'],sha)
        self.assertEqual(state['latest_attempt']['rendered_issues'],[issue])
        self.assertEqual(state['latest_attempt']['stage'],'rendered_verification')
        self.assertEqual(state['versions'][0]['status'],'blocked')
        self.assertEqual(before,{name:self.r.file_hash(self.w/name) for name in preserved})

    def test_diagnostics_from_another_proposal_or_fake_boolean_are_not_attached(self):
        sha=self.compile();plan=self.r.read_json(self.w/'proposal-fill.json')
        run=self.w/'runs/20261011T000000-a1b2c3d4';run.mkdir(parents=True)
        changed=deepcopy(plan);changed['record']['values']['name'][0]='Another synthetic value'
        self.r.write_json(run/'plan.json',changed)
        issue={'code':'glyph_overlap','page':1,'field_id':plan['placements'][0]['field_id']}
        self.r.write_json(run/'verification.json',{'status':'blocked','stage':'<script>',
            'rendered_page_text_exact':False,'rendered_text_verification':{'policy':fixtures.fill.RENDER_POLICY,'passed':0,'issues':[issue]}})
        code,body=self.request('GET','/api/fill-state');self.assertEqual(code,200);state=json.loads(body)
        self.assertEqual(state['proposal_sha256'],sha);self.assertIsNone(state['latest_attempt'])
        self.r.write_json(run/'plan.json',plan)
        code,body=self.request('GET','/api/fill-state');self.assertEqual(code,200);attempt=json.loads(body)['latest_attempt']
        self.assertEqual(attempt['status'],'blocked');self.assertIsNone(attempt['stage']);self.assertEqual(attempt['rendered_issues'],[])
        self.assertFalse((run/'candidate.pdf').exists())

    def test_locked_workspace_rejects_proposal_and_approval_writes_without_changing_files(self):
        sha=self.compile();code,_=self.post('/api/approve',self.consent(sha));self.assertEqual(code,200)
        plan=self.r.read_json(self.w/'proposal-fill.json')
        self.r.acquire_apply_lock(self.w,self.fixture.manifest,plan,'20261011T000000-a1b2c3d4')
        files=['fill-input.json','proposal-fill.json','approved-plan.json','baseline.pdf','baseline.hwpx',self.r.APPLY_LOCK]
        before={name:self.r.file_hash(self.w/name) for name in files}
        code,body=self.request('GET','/api/fill-state');self.assertEqual(code,200);state=json.loads(body)
        self.assertTrue(state['apply_state']['locked']);self.assertTrue(state['apply_state']['record_valid'])
        self.assertNotIn('pid',state['apply_state']);self.assertNotIn('owner_nonce',state['apply_state'])
        code,_=self.post('/api/fill-proposal',{'input_sha256':state['input_sha256'],'placements':self.fixture.placements},**{'If-Match':'"'+state['proposal_revision']+'"'});self.assertEqual(code,423)
        code,_=self.post('/api/approve',self.consent(sha));self.assertEqual(code,423)
        self.assertEqual(before,{name:self.r.file_hash(self.w/name) for name in files})

    def test_incomplete_run_is_not_success_or_a_fake_candidate_preview(self):
        sha=self.compile();plan=self.r.read_json(self.w/'proposal-fill.json')
        run=self.w/'runs/20261011T000000-a1b2c3d4';run.mkdir(parents=True)
        self.r.write_json(run/'plan.json',plan);self.r.write_json(run/'verification.json',{'status':'in_progress','stage':'saved_hwp_reopen','source_unchanged':None})
        self.r.acquire_apply_lock(self.w,self.fixture.manifest,plan,run.name)
        code,body=self.request('GET','/api/fill-state');self.assertEqual(code,200);state=json.loads(body)
        self.assertEqual(state['proposal_sha256'],sha);self.assertEqual(state['latest_attempt']['status'],'in_progress')
        self.assertTrue(state['apply_state']['locked']);self.assertEqual(state['versions'],[])
        self.assertFalse(state['latest_attempt']['source_unchanged'])
        code,_=self.request('GET','/comparison/'+run.name+'/after/1.png');self.assertNotEqual(code,200)

    def sealed_mock_run(self):
        sha=self.compile();plan=self.r.read_json(self.w/'proposal-fill.json')
        run=self.w/'runs/20261011T000000-a1b2c3d4';run.mkdir(parents=True)
        self.r.write_json(run/'plan.json',plan)
        self.r.write_json(run/'verification.json',{'status':'checks_passed_needs_human_visual_review','stage':'checks_passed',
                          'result_binding_policy':self.r.RESULT_POLICY,'source_unchanged':True,'layout_gate':{'any_pixel_changed_pages':[]}})
        for name in ('candidate.hwp','candidate.hwpx'):(run/name).write_bytes(b'MOCK-NOT-HWP')
        for name in ('candidate.pdf','candidate-buffer.pdf'):self.fixture.pdf(run/name,['Value A','Value B'])
        self.r.seal_result(run,self.fixture.manifest,plan)
        return sha,run

    def test_changed_bound_pdf_removes_success_and_blocks_all_result_preview_routes(self):
        sha,run=self.sealed_mock_run()
        files=['baseline.pdf','baseline.hwpx','fill-input.json','proposal-fill.json']
        before={name:self.r.file_hash(self.w/name) for name in files};binding=(run/self.r.RESULT_FILE).read_bytes()
        code,body=self.request('GET','/api/fill-state');self.assertEqual(code,200);state=json.loads(body)
        self.assertEqual(state['latest_attempt']['artifact_integrity'],'verified')
        code,_=self.request('GET','/comparison/'+run.name+'/after/1.png');self.assertEqual(code,200)
        (run/'candidate.pdf').unlink();self.fixture.pdf(run/'candidate.pdf',['FOREIGN','Value B'])
        code,body=self.request('GET','/api/fill-state');self.assertEqual(code,200);state=json.loads(body)
        self.assertEqual(state['proposal_sha256'],sha);self.assertEqual(state['latest_attempt']['plan_sha256'],sha)
        self.assertEqual(state['latest_attempt']['status'],'blocked');self.assertEqual(state['latest_attempt']['artifact_integrity'],'mismatch')
        self.assertEqual(state['latest_attempt']['stage'],'result_integrity');self.assertEqual(state['versions'],[])
        self.assertEqual(state['latest_attempt']['rendered_issues'],[])
        for side in ('before','after','diff'):
            code,_=self.request('GET','/comparison/'+run.name+'/'+side+'/1.png');self.assertNotEqual(code,200)
        self.assertEqual(binding,(run/self.r.RESULT_FILE).read_bytes());self.assertEqual(before,{name:self.r.file_hash(self.w/name) for name in files})
        self.assertEqual(self.r.read_json(run/'verification.json')['status'],'checks_passed_needs_human_visual_review')
        self.assertFalse((self.w/'approved-plan.json').exists())

    def test_corrupt_bound_plan_or_report_keeps_the_original_failed_attempt_visible(self):
        sha,run=self.sealed_mock_run()
        for name in ('plan.json','verification.json'):
            path=run/name;before=path.read_bytes();path.write_bytes(b'{PRIVATE BROKEN')
            code,body=self.request('GET','/api/fill-state');self.assertEqual(code,200);state=json.loads(body)
            self.assertEqual(state['latest_attempt']['plan_sha256'],sha);self.assertEqual(state['latest_attempt']['status'],'blocked')
            self.assertEqual(state['latest_attempt']['artifact_integrity'],'mismatch');self.assertNotIn('PRIVATE BROKEN',body.decode())
            code,_=self.request('GET','/comparison/'+run.name+'/after/1.png');self.assertNotEqual(code,200)
            path.write_bytes(before)
        self.assertFalse((self.w/'approved-plan.json').exists())

    def test_old_text_approval_cannot_authorize_fill(self):
        sha=self.compile()
        for kind in ['text','native','layout']:
            code,_=self.post('/api/approve',{'plan_sha256':sha,'proposal_kind':kind})
            self.assertEqual(code,400)
        self.assertFalse((self.w/'approved-plan.json').exists())
