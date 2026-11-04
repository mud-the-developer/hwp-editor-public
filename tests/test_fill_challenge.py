"""Harder offline boundary cases; no COM, actual reports, approval or gate weakening."""
from copy import deepcopy
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import native_fill_matrix as matrix
import native_fill_challenge as challenge
import test_hwp_fill_plan as fixtures
from test_hwp_form_draft import cell,package
r,fill,f=fixtures.r,fixtures.fill,fixtures.f


class ChallengeTests(unittest.TestCase):
    def setUp(self):
        self.fixture=fixtures.FillTests('test_exact_replay_regions_and_unsigned_policy_are_new_contract')
        self.fixture.setUp();self.addCleanup(self.fixture.temp.cleanup)
        self.w=self.fixture.w

    def test_unsupported_multiline_checkbox_and_merged_shapes_reject_before_any_export(self):
        for kind,value in [('multiline','가상 첫 줄\n가상 둘째 줄'),('checkbox','checked')]:
            bindings=deepcopy(self.fixture.bindings);bindings['fields'][0].update(kind=kind,max_lines=2 if kind=='multiline' else 1)
            record=deepcopy(self.fixture.record);record['values']['name'][0]=value
            with self.subTest(kind=kind),patch.object(r,'native_export') as native,self.assertRaises(ValueError):
                fill.proposal(self.w,bindings,record,self.fixture.placements)
            native.assert_not_called()
        path=self.w/'merged.hwpx';package(path,cells=cell(0,0,'label')+cell(0,1,span='2'))
        mapped=f.inspect_template(path);merged=next(item for item in mapped['fields'] if item['cell_span']['rowSpan']=='2')
        self.assertFalse(merged['supported']);self.assertIn('merged_or_unknown_span_needs_separate_contract',merged['issues'])

    def test_long_and_line_break_values_are_not_silently_truncated_or_normalized(self):
        for text in ['한'*31,'first\nsecond','\r\n','😀'*16]:
            record=deepcopy(self.fixture.record);record['values']['name'][0]=text
            draft=f.draft(self.fixture.baseline,f.inspect_template(self.fixture.baseline),self.fixture.bindings,record)
            self.assertEqual(draft['assignments'][0]['value'],text);self.assertEqual(draft['status'],'blocked')
            with self.assertRaises(ValueError):fill.proposal(self.w,self.fixture.bindings,record,self.fixture.placements)

    def test_exact_utf16_budget_boundary_for_supplementary_unicode(self):
        bindings=deepcopy(self.fixture.bindings);bindings['fields'][0]['max_utf16_units']=30
        for count,blocked in [(15,False),(16,True)]:
            record=deepcopy(self.fixture.record);record['values']['name'][0]='😀'*count
            draft=f.draft(self.fixture.baseline,f.inspect_template(self.fixture.baseline),bindings,record)
            self.assertEqual('content_budget_exceeded' in draft['assignments'][0]['issues'],blocked)

    def test_picture_only_cell_is_not_offered_as_blank_even_without_pdf_text(self):
        path=self.w/'picture-in-cell.pdf'
        with r.fitz.open(self.w/'baseline.pdf') as doc:
            pix=r.fitz.Pixmap(r.fitz.csRGB,10,10,bytes([15,120,240])*100,False)
            doc[0].insert_image(r.fitz.Rect(100,65,140,85),stream=pix.tobytes('png'));doc.save(path)
        regions=fill.empty_regions(path)
        self.assertEqual(len(regions),1);self.assertGreater(regions[0]['region'][1],80)

    def test_picture_crossing_cell_edge_blocks_every_intersecting_cell(self):
        path=self.w/'picture-across-rows.pdf'
        with r.fitz.open(self.w/'baseline.pdf') as doc:
            pix=r.fitz.Pixmap(r.fitz.csRGB,10,10,bytes([15,120,240])*100,False)
            doc[0].insert_image(r.fitz.Rect(100,85,110,95),stream=pix.tobytes('png'));doc.save(path)
        self.assertEqual(fill.empty_regions(path),[])

    def test_repeated_labels_and_values_are_addressed_by_path_not_broadcast(self):
        path=self.w/'many-slots.hwpx'
        extra='<hp:p><hp:run><hp:tbl><hp:tr>'+''.join(cell(row,0,'반복')+cell(row,1) for row in range(9))+'</hp:tr></hp:tbl></hp:run></hp:p>'
        package(path,extra=extra+extra)
        mapped=f.inspect_template(path);targets=[item for item in mapped['fields'] if item['supported']]
        self.assertEqual(len(targets),20);self.assertEqual(len({item['field_id'] for item in targets}),20)
        bindings={'schema':'hwp-fill-bindings/v1','fields':[{'field_id':item['field_id'],'key':'same','kind':'text','required':True,'max_utf16_units':30,'max_lines':1} for item in targets]}
        record={'values':{'same':['동일 가상 값']*20},'sources':{'same':['FICTIONAL']*20}}
        draft=f.draft(path,mapped,bindings,record);self.assertEqual(len(draft['assignments']),20)
        record['values']['same']='동일 가상 값'
        with self.assertRaises(ValueError):f.draft(path,mapped,bindings,record)

    def test_page_boundary_and_border_invasion_are_rejected_instead_of_clipped(self):
        for region in [[81,61,301,89],[-1,61,219,89],[80,60,220,90]]:
            placements=deepcopy(self.fixture.placements);placements[0]['region']=region
            with self.subTest(region=region),self.assertRaises(ValueError):
                fill.proposal(self.w,self.fixture.bindings,self.fixture.record,placements)

    def test_challenge_inputs_have_27_items_four_explicit_unfilled_and_repeated_values(self):
        values=challenge.inputs()['values']['반복항목']
        self.assertEqual(len(values),27);self.assertEqual([i for i,value in enumerate(values) if value is None],sorted(challenge.OMITTED))
        self.assertEqual(values[0],values[9]);self.assertEqual(values[9],values[18]);self.assertIn('<값&',values[1])

    def test_expected_overflow_requires_a_failed_gate_not_just_a_large_input(self):
        audit={'sha256':r.file_hash(self.fixture.baseline),'embedded_binary_hashes':['fixture']}
        for passed in (False,True):
            with patch.object(r,'native_export',return_value={}) as native,patch.object(r,'hwp_audit',return_value=audit),\
                    patch.object(fill,'structural_gate',return_value={}),patch.object(r,'compare_pdfs',return_value={'passed':passed}),\
                    patch.object(fill,'rendered_text_gate',return_value=True),patch.object(matrix,'optional_cell_empty',return_value=True),\
                    patch.object(challenge,'OMITTED',{1}):
                directory=self.w/('expected-fail' if not passed else 'unexpected-pass')
                if passed:
                    with self.assertRaisesRegex(ValueError,'unexpectedly passed every gate'):
                        challenge.evaluate(self.w,self.fixture.baseline,audit,self.fixture.targets,self.fixture.plan,directory,True)
                else:
                    evidence=challenge.evaluate(self.w,self.fixture.baseline,audit,self.fixture.targets,self.fixture.plan,directory,True)
                    self.assertEqual(evidence['status'],'expected_rejection_not_deliverable')
                self.assertEqual(native.call_count,2,'one save and one planned reopen, never retry')

    def test_negative_input_isolation_preserves_parent_and_requires_its_exact_record(self):
        r.write_json(self.w/'fill-input.json',{'bindings':self.fixture.bindings,'record':self.fixture.record})
        record=deepcopy(self.fixture.record);record['values']['name'][0]='different fixture value'
        before={name:r.file_hash(self.w/name) for name in ('baseline.hwpx','baseline.pdf','manifest.json','fill-input.json')}
        target=self.w/'isolated-negative'
        with patch.object(r,'native_export') as native:
            child=challenge.isolated_input_workspace(self.w,target,self.fixture.bindings,record)
            native.assert_not_called()
        self.assertNotEqual(r.read_json(child/'manifest.json')['workspace_id'],self.fixture.manifest['workspace_id'])
        self.assertEqual(before,{name:r.file_hash(self.w/name) for name in before})
        self.assertEqual(fill.proposal(child,self.fixture.bindings,record,self.fixture.placements)['record'],record)
        with self.assertRaises(ValueError):fill.proposal(self.w,self.fixture.bindings,record,self.fixture.placements)
        with self.assertRaises(FileExistsError):challenge.isolated_input_workspace(self.w,target,self.fixture.bindings,record)
        r.write_json(self.w/'approved-plan.json',{'fixture':'not real consent'})
        with self.assertRaises(ValueError):challenge.isolated_input_workspace(self.w,self.w/'refuse-consent',self.fixture.bindings,record)

    def test_positive_export_exception_stops_without_reopen_or_retry(self):
        with patch.object(r,'native_export',side_effect=RuntimeError('fixture export failed')) as native:
            with self.assertRaisesRegex(RuntimeError,'fixture export failed'):
                challenge.evaluate(self.w,self.fixture.baseline,{},self.fixture.targets,self.fixture.plan,self.w/'export-fails')
            self.assertEqual(native.call_count,1)

    def test_invalid_source_specs_and_challenge_paths_never_launch_native(self):
        with tempfile.TemporaryDirectory() as directory:
            for labels,widths in [([], (70,70)),([['x']*11],(70,70)),([['x']],(1,139))]:
                with self.subTest(labels=labels,widths=widths),self.assertRaises(ValueError):matrix.create_source(Path(directory)/'source',labels,widths)
            with patch.object(matrix,'create_source') as native,self.assertRaises(ValueError):
                challenge.run(Path(directory)/'toy-fill-challenge-fixture')
            native.assert_not_called()
