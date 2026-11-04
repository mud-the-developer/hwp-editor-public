"""Temporary fake apply lifecycle/atomic writes; NEVER launches Hancom or real approval."""
from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

import test_hwp_fill_plan as fixtures
r,fill=fixtures.r,fixtures.fill


class AtomicJsonTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.path=Path(self.temp.name)/'fixture.json';r.write_json(self.path,{'old':True})

    def test_invalid_json_and_flush_failure_leave_previous_complete_file(self):
        before=self.path.read_bytes()
        with self.assertRaises(ValueError):r.write_json(self.path,{'not-finite':float('nan')})
        with patch.object(r.os,'fsync',side_effect=OSError('MOCK sync failure')):
            with self.assertRaises(OSError):r.write_json(self.path,{'new':True})
        self.assertEqual(self.path.read_bytes(),before)
        self.assertEqual(list(self.path.parent.glob('*.tmp')),[])

    def test_exclusive_temp_collision_never_deletes_another_writers_file(self):
        foreign=self.path.with_name(self.path.name+'.fixed.tmp');foreign.write_text('OTHER WRITER',encoding='utf-8')
        with patch.object(r.secrets,'token_hex',return_value='fixed'):
            with self.assertRaises(FileExistsError):r.write_json(self.path,{'new':True})
        self.assertEqual(foreign.read_text(encoding='utf-8'),'OTHER WRITER')
        self.assertEqual(r.read_json(self.path),{'old':True})

    def test_parallel_writers_use_unique_complete_replacements_not_shared_tmp(self):
        def write(number):
            try:
                r.write_json(self.path,{'number':number,'padding':'x'*500})
                return True
            except PermissionError:
                return False  # Windows sharing conflicts are explicit failures, never silent retries.
        with ThreadPoolExecutor(max_workers=8) as pool:
            outcomes=list(pool.map(write,range(40)))
        self.assertTrue(any(outcomes));self.assertIn(r.read_json(self.path)['number'],range(40))
        self.assertEqual(list(self.path.parent.glob('*.tmp')),[])


class ApplyLifecycleTests(unittest.TestCase):
    def setUp(self):
        self.fixture=fixtures.FillTests('test_exact_replay_regions_and_unsigned_policy_are_new_contract')
        self.fixture.setUp();self.addCleanup(self.fixture.temp.cleanup);self.w=self.fixture.w
        self.manifest=deepcopy(self.fixture.manifest);self.manifest['source_audit']['embedded_binary_hashes']=['mock-picture']
        r.write_json(self.w/'manifest.json',self.manifest)
        r.write_json(self.w/'fill-input.json',{'bindings':self.fixture.bindings,'record':self.fixture.record})
        self.plan=self.fixture.plan;self.path=self.w/'proposal-fill.json';r.write_json(self.path,self.plan)
        # Temporary synthetic consent only; not an owner report or approval.
        r.write_json(self.w/'approved-plan.json',{'approved_via':'local-review-ui','plan_sha256':r.plan_hash(self.plan),
                                                'plan':self.plan,'approved_scope':fill.approval_scope(self.plan)})
        self.selected=self.fixture.selected()
        self.hashes={name:r.file_hash(self.w/name) for name in ('baseline.pdf','baseline.hwpx','fixture.hwp','manifest.json','fill-input.json','proposal-fill.json','approved-plan.json')}

    def assert_inputs_preserved(self):
        self.assertEqual(self.hashes,{name:r.file_hash(self.w/name) for name in self.hashes})

    def normal_export(self,source,pdf,**kwargs):
        if 'hwpx' in kwargs:
            fill.write_input(self.fixture.baseline,kwargs['hwpx'],self.selected)
            kwargs['hwp'].write_bytes(b'SYNTHETIC-NOT-HWP')
        self.fixture.pdf(pdf,['Value A','Value B'])
        return {'native_pages':1,'MOCK_ONLY':True}

    def test_exclusive_acquisition_blocks_a_second_owner_and_never_discloses_nonce(self):
        lease=r.acquire_apply_lock(self.w,self.manifest,self.plan,'20261011T000000-a1b2c3d4')
        with self.assertRaises(FileExistsError):r.acquire_apply_lock(self.w,self.manifest,self.plan,'20261011T000001-a1b2c3d4')
        state=r.apply_state(self.w,self.manifest)
        self.assertTrue(state['locked']);self.assertTrue(state['record_valid'])
        self.assertNotIn('pid',state);self.assertNotIn('owner_nonce',state)
        self.assertEqual(r.read_json(self.w/r.APPLY_LOCK),lease)
        self.assert_inputs_preserved()

    def test_second_process_cannot_apply_or_overwrite_a_live_owned_lock(self):
        lease=r.acquire_apply_lock(self.w,self.manifest,self.plan,'20261011T000000-a1b2c3d4')
        script="""import sys\nfrom pathlib import Path\nsys.path.insert(0,sys.argv[1])\nimport hwp_review as r\nr.native_export=lambda *a,**kw:(_ for _ in ()).throw(AssertionError('Native MUST NOT launch'))\ntry:r.apply(Path(sys.argv[2]),Path(sys.argv[3]))\nexcept ValueError as e:\n assert 'locked' in str(e)\nelse:raise AssertionError('second process was accepted')\n"""
        child=subprocess.run([sys.executable,'-c',script,str(r.ROOT/'scripts'),str(self.w),str(self.path)],capture_output=True,text=True,timeout=30)
        self.assertEqual(child.returncode,0,child.stderr)
        self.assertEqual(r.read_json(self.w/r.APPLY_LOCK),lease);self.assertFalse((self.w/'runs').exists())
        self.assert_inputs_preserved()

    def test_progress_is_persisted_before_export_and_nested_apply_never_exports(self):
        def export(source,pdf,**kwargs):
            state=r.apply_state(self.w,self.manifest);self.assertTrue(state['locked'])
            attempt=r.review_attempts(self.w,self.manifest)[0]
            self.assertEqual(attempt['status'],'in_progress')
            self.assertEqual(r.review_versions(self.w,self.manifest),[])
            with self.assertRaisesRegex(ValueError,'locked'):r.apply(self.w,self.path)
            return self.normal_export(source,pdf,**kwargs)
        with patch.object(r,'native_export',side_effect=export) as native,patch.object(r,'hwp_audit',return_value={'embedded_binary_hashes':['mock-picture']}):
            report=r.apply(self.w,self.path);self.assertEqual(native.call_count,2)
        self.assertEqual(report['status'],'checks_passed_needs_human_visual_review')
        self.assertEqual(r.apply_state(self.w,self.manifest),{'locked':False})
        self.assert_inputs_preserved()

    def test_bytes_changed_during_reopen_cannot_be_bound_as_a_verified_pass(self):
        def export(source,pdf,**kwargs):
            result=self.normal_export(source,pdf,**kwargs)
            if 'hwpx' not in kwargs:source.write_bytes(b'MOCK-CHANGED-AFTER-STRUCTURE-CHECK')
            return result
        with patch.object(r,'native_export',side_effect=export) as native,patch.object(r,'hwp_audit',return_value={'embedded_binary_hashes':['mock-picture']}):
            with self.assertRaisesRegex(ValueError,'changed during verification'):r.apply(self.w,self.path)
            self.assertEqual(native.call_count,2)
        run=next((self.w/'runs').iterdir());report=r.read_json(run/'verification.json')
        self.assertEqual(report['status'],'blocked');self.assertEqual(report['stage'],'result_integrity')
        self.assertTrue(r.apply_state(self.w,self.manifest)['locked'])
        self.assertEqual(r.review_versions(self.w,self.manifest)[0]['status'],'blocked')
        self.assert_inputs_preserved()

    def test_binding_write_failure_stays_blocked_locked_and_never_looks_like_legacy_success(self):
        with patch.object(r,'native_export',side_effect=self.normal_export) as native,patch.object(r,'hwp_audit',return_value={'embedded_binary_hashes':['mock-picture']}),patch.object(r,'seal_result',side_effect=OSError('MOCK binding failure')):
            with self.assertRaisesRegex(OSError,'MOCK binding failure'):r.apply(self.w,self.path)
            self.assertEqual(native.call_count,2)
        attempt=r.review_attempts(self.w,self.manifest)[0]
        self.assertEqual(attempt['status'],'blocked');self.assertEqual(attempt['artifact_integrity'],'missing')
        self.assertEqual(r.review_versions(self.w,self.manifest),[])
        self.assertTrue(r.apply_state(self.w,self.manifest)['locked']);self.assert_inputs_preserved()

    def test_handled_failure_keeps_lock_and_blocks_retry_without_another_export(self):
        with patch.object(r,'native_export',side_effect=RuntimeError('MOCK export failed')) as native:
            with self.assertRaisesRegex(RuntimeError,'MOCK export failed'):r.apply(self.w,self.path)
            with self.assertRaisesRegex(ValueError,'locked'):r.apply(self.w,self.path)
            self.assertEqual(native.call_count,1)
        self.assertTrue(r.apply_state(self.w,self.manifest)['locked'])
        self.assertEqual(r.review_attempts(self.w,self.manifest)[0]['status'],'blocked')
        self.assert_inputs_preserved()

    def test_keyboard_interrupt_keeps_lock_and_marks_only_the_attempt_blocked(self):
        with patch.object(r,'native_export',side_effect=KeyboardInterrupt) as native:
            with self.assertRaises(KeyboardInterrupt):r.apply(self.w,self.path)
            with self.assertRaises(ValueError):r.apply(self.w,self.path)
            self.assertEqual(native.call_count,1)
        run=next((self.w/'runs').iterdir());report=r.read_json(run/'verification.json')
        self.assertEqual(report['status'],'blocked');self.assertEqual(report['stage'],'interrupted')
        self.assertEqual(report['interrupted_at_stage'],'native_edit_export')
        self.assertTrue(report['source_unchanged']);self.assertTrue(r.apply_state(self.w,self.manifest)['locked'])
        self.assertEqual(r.review_versions(self.w,self.manifest),[])
        self.assert_inputs_preserved()

    def test_hard_stopped_mock_process_leaves_visible_progress_and_lock_without_guessing_pid_liveness(self):
        script="""import os,sys\nfrom pathlib import Path\nsys.path.insert(0,sys.argv[1])\nimport hwp_review as r\nr.native_export=lambda *a,**kw:os._exit(42)\nr.apply(Path(sys.argv[2]),Path(sys.argv[3]))\n"""
        child=subprocess.run([sys.executable,'-c',script,str(r.ROOT/'scripts'),str(self.w),str(self.path)],capture_output=True,text=True,timeout=30)
        self.assertEqual(child.returncode,42,child.stderr)
        state=r.apply_state(self.w,self.manifest);self.assertTrue(state['locked']);self.assertTrue(state['record_valid'])
        attempt=r.review_attempts(self.w,self.manifest)[0]
        self.assertEqual(attempt['status'],'in_progress');self.assertEqual(attempt['stage'],'native_edit_export')
        self.assertFalse(attempt['source_unchanged']);self.assertEqual(r.review_versions(self.w,self.manifest),[])
        with patch.object(r,'native_export') as native,self.assertRaisesRegex(ValueError,'locked'):r.apply(self.w,self.path)
        native.assert_not_called();self.assert_inputs_preserved()

    def test_invalid_old_foreign_and_bool_lock_records_all_remain_locked(self):
        lease=r.acquire_apply_lock(self.w,self.manifest,self.plan,'20000101T000000-a1b2c3d4')
        for value in [{}, {**lease,'schema':True},{**lease,'pid':True},{**lease,'workspace_id':'foreign'},
                      {**lease,'run_id':'../escape'}, {**lease,'owner_nonce':[]}, {**lease,'plan_sha256':'fake'}]:
            r.write_json(self.w/r.APPLY_LOCK,value)
            state=r.apply_state(self.w,self.manifest)
            self.assertTrue(state['locked']);self.assertFalse(state['record_valid'])
            with patch.object(r,'native_export') as native,self.assertRaises(ValueError):r.apply(self.w,self.path)
            native.assert_not_called()
        (self.w/r.APPLY_LOCK).write_text('{invalid',encoding='utf-8')
        self.assertTrue(r.apply_state(self.w,self.manifest)['locked'])
        self.assert_inputs_preserved()

    def test_ownership_change_cannot_be_unlinked_or_labeled_success(self):
        def export(source,pdf,**kwargs):
            data=r.read_json(self.w/r.APPLY_LOCK);data['owner_nonce']='0'*32;r.write_json(self.w/r.APPLY_LOCK,data)
            return self.normal_export(source,pdf,**kwargs)
        with patch.object(r,'native_export',side_effect=export),patch.object(r,'hwp_audit',return_value={'embedded_binary_hashes':['mock-picture']}):
            with self.assertRaisesRegex(ValueError,'ownership changed'):r.apply(self.w,self.path)
        self.assertEqual(r.read_json(self.w/r.APPLY_LOCK)['owner_nonce'],'0'*32)
        self.assertEqual(r.review_attempts(self.w,self.manifest)[0]['status'],'blocked')
        self.assert_inputs_preserved()

    def test_existing_lock_prevents_cli_fill_input_rewrite(self):
        r.acquire_apply_lock(self.w,self.manifest,self.plan,'20261011T000000-a1b2c3d4')
        argv=['hwp_review.py','fill-draft',str(self.w),str(self.w/'not-read-bindings.json'),str(self.w/'not-read-record.json')]
        with patch.object(r.sys,'argv',argv),self.assertRaisesRegex(ValueError,'locked'):r.main()
        self.assert_inputs_preserved()
