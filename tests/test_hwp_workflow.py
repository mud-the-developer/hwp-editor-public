"""Synthetic local workflow/evidence records, never real HWP/approval/submission."""
import base64
import binascii
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
from scripts import hwp_workflow as w, hwp_review as r
from scripts.packaged_smoke_runtime import fixture


class WorkflowTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.p=Path(self.temp.name)/'SYNTHETIC';fixture(self.p,r);self.m=r.verify_baseline(self.p)
        self.comment={'id':'synthetic-note','page':1,'anchor_id':None,'region':None,'comment':'DATA ONLY'}
        r.write_json(self.p/'comments.json',{'workspace_id':self.m['workspace_id'],'comments':[self.comment]})
        self.before={name:r.file_hash(self.p/name) for name in ('manifest.json','comments.json','baseline.pdf','baseline.hwpx','synthetic-source.hwp')}

    def preserved(self):
        self.assertEqual(self.before,{name:r.file_hash(self.p/name) for name in self.before});self.assertFalse((self.p/'approved-plan.json').exists());self.assertFalse((self.p/'proposal.json').exists())

    def test_empty_status_is_not_approval_and_note_record_is_exact_hash_bound(self):
        self.assertEqual(w.read(self.p,self.m)['revision'],'absent')
        result=w.note(self.p,self.m,{'comment_id':self.comment['id'],'comment_sha256':r.plan_hash(self.comment),'status':'resolved','evidence_sha256':[]},'"absent"')
        self.assertFalse(result['submission_approved']);self.assertEqual(result['data']['notes']['synthetic-note']['status'],'resolved');self.preserved()

    def test_evidence_snapshot_dedup_fixed_path_hash_and_no_html_execution(self):
        payload={'name':'source.txt','data_base64':base64.b64encode(b'<script>DATA ONLY</script>').decode()}
        result=w.evidence(self.p,self.m,payload,'"absent"');sha=r.digest(b'<script>DATA ONLY</script>')
        self.assertEqual(w.evidence_bytes(self.p,self.m,sha),b'<script>DATA ONLY</script>')
        self.assertEqual(result['data']['evidence'][sha]['bytes'],len(b'<script>DATA ONLY</script>'))
        w.note(self.p,self.m,{'comment_id':self.comment['id'],'comment_sha256':r.plan_hash(self.comment),'status':'in_progress','evidence_sha256':[sha]},'"'+result['revision']+'"');self.preserved()

    def test_changed_evidence_blocks_without_resealing(self):
        result=w.evidence(self.p,self.m,{'name':'x.txt','data_base64':base64.b64encode(b'FIRST').decode()},'"absent"');sha=next(iter(result['data']['evidence']))
        before=(self.p/w.FILE).read_bytes();(self.p/'review-evidence'/(sha+'.bin')).write_bytes(b'OTHER')
        with self.assertRaises(ValueError):w.evidence_bytes(self.p,self.m,sha)
        self.assertEqual((self.p/w.FILE).read_bytes(),before);self.preserved()

    def test_unknown_comment_old_hash_authority_and_unknown_attachment_are_denied(self):
        normal={'comment_id':self.comment['id'],'comment_sha256':r.plan_hash(self.comment),'status':'open','evidence_sha256':[]}
        for payload in ({**normal,'comment_id':'unknown'},{**normal,'comment_sha256':'wrong'},{**normal,'status':'approved'},{**normal,'approved':True},{**normal,'evidence_sha256':['a'*64]}):
            with self.assertRaises(ValueError):w.note(self.p,self.m,payload,'"absent"')
        self.assertFalse((self.p/w.FILE).exists());self.preserved()

    def test_evidence_path_executable_big_invalid_base64_or_stale_revision_are_denied(self):
        normal={'name':'x.txt','data_base64':base64.b64encode(b'DATA').decode()}
        for payload in ({**normal,'name':'../x.txt'},{**normal,'name':'x.exe'},{**normal,'data_base64':'??'},{**normal,'command':'EXEC'}):
            with self.assertRaises((ValueError,binascii.Error)):w.evidence(self.p,self.m,payload,'"absent"')
        with self.assertRaises(ValueError):w.evidence(self.p,self.m,normal,'"wrong"')
        with patch.object(w,'MAX_EVIDENCE',2):
            with self.assertRaises(ValueError):w.evidence(self.p,self.m,normal,'"absent"')
        self.preserved()

    def test_closed_baseline_corrupt_workflow_not_automatically_repaired(self):
        (self.p/w.FILE).write_bytes(b'{INVALID')
        with self.assertRaises(ValueError):w.read(self.p,self.m)
        self.assertEqual((self.p/w.FILE).read_bytes(),b'{INVALID');self.preserved()

    def test_local_visual_record_binds_current_result_hashes_never_institutional_consent(self):
        from test_result_download import ResultDownloadTests
        source=ResultDownloadTests('test_exact_hwp_and_reopened_pdf_metadata_bytes_and_hashes_are_read_only');source.setUp()
        try:
            _, version=r.review_version(source.w,source.m,source.run_id)
            payload={'run_id':source.run_id,'hwp_sha256':version['downloads']['hwp']['sha256'],'pdf_sha256':version['downloads']['pdf']['sha256'],
                     'checks':{'text':True,'layout':True,'images':True,'pages':True},'review_confirmed':True}
            result=w.visual_review(source.w,source.m,payload,'"absent"')
            record=result['data']['visual_reviews'][source.run_id]
            self.assertTrue(record['local_visual_review_only']);self.assertFalse(record['submission_approved'])
            self.assertFalse((source.w/'approved-plan.json').exists());self.assertEqual(w.read(source.w,source.m)['data'],result['data'])
            (source.run_dir/'candidate.hwp').write_bytes(b'CHANGED')
            with self.assertRaises(ValueError):w.visual_review(source.w,source.m,payload,'"'+result['revision']+'"')
        finally:source.temp.cleanup()

    def test_source_metadata_cannot_embed_submission_authority_or_path_data(self):
        result=w.note(self.p,self.m,{'comment_id':self.comment['id'],'comment_sha256':r.plan_hash(self.comment),'status':'open','evidence_sha256':[]},'"absent"')
        data=result['data'];data['notes'][self.comment['id']]['approved']=True;r.write_json(self.p/w.FILE,data)
        with self.assertRaises(ValueError):w.read(self.p,self.m)
        self.preserved()

    def test_visual_review_requires_exact_separate_checks_and_not_submission(self):
        for payload in ({},{'review_confirmed':True},{'run_id':'../OUTSIDE','hwp_sha256':'a'*64,'pdf_sha256':'b'*64,'checks':{'text':True,'layout':True,'images':True,'pages':True},'review_confirmed':True}):
            with self.assertRaises(ValueError):w.visual_review(self.p,self.m,payload,'"absent"')
        self.assertFalse((self.p/w.FILE).exists());self.preserved()


if __name__=='__main__':unittest.main()
