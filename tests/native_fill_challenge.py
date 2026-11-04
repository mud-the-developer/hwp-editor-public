"""Opt-in harder Native lab: 3 dense pages, repeated labels, selective fill, overflow.
ONE new unsigned toy source. The last deliberately oversized candidate MUST fail
strict gates; it is never a retry/repair/deliverable. Any unexpected failure stops.
"""
from copy import deepcopy
from datetime import datetime,timezone
from pathlib import Path
import argparse
import shutil

import native_fill_matrix as matrix
r,fill,f=matrix.r,matrix.fill,matrix.f
ROOT=matrix.ROOT
OMITTED={2,8,17,24}


def inputs():
    values=[None if index in OMITTED else ("반복 가상 값" if index in (0,9,18) else f'가상 <값&{index+1:02d}>') for index in range(27)]
    return {'values':{'반복항목':values},'sources':{'반복항목':[None if value is None else 'FICTIONAL TEST ONLY / 실제 연구 자료 아님' for value in values]}}


def isolated_input_workspace(workspace,target,bindings,record):
    """Reuse byte-identical proven lab baselines, not a fabricated native roundtrip.
    Never replace the positive input or its manifest/baselines. No COM runs here.
    """
    if target.exists():raise FileExistsError(target)
    if (workspace/'approved-plan.json').exists():raise ValueError('Lab may not clone actual human consent')
    manifest=deepcopy(r.verify_baseline(workspace))
    parent_manifest_hash=r.file_hash(workspace/'manifest.json')
    target.mkdir(parents=True)
    for name in ('baseline.hwpx','baseline.pdf'):
        shutil.copy2(workspace/name,target/name)
        if r.file_hash(target/name)!=r.file_hash(workspace/name):raise ValueError('Derived baseline bytes differ')
    manifest['workspace_id']=r.secrets.token_hex(16)
    manifest['derived_baseline_proof']={'workspace':str(workspace.resolve()),'manifest_sha256':parent_manifest_hash,
                                      'native_roundtrip_repeated':False,'identical_baseline_bytes':True}
    r.write_json(target/'manifest.json',manifest)
    r.write_json(target/'fill-input.json',{'bindings':bindings,'record':record})
    r.verify_baseline(target)
    if r.file_hash(workspace/'manifest.json')!=parent_manifest_hash:raise ValueError('Parent workspace changed during isolation')
    return target


def evaluate(workspace,source,baseline_audit,targets,plan,directory,expected_rejection=False):
    directory.mkdir(exist_ok=False)
    selected=fill.validate(plan,r.verify_baseline(workspace),workspace/'baseline.hwpx')
    r.write_json(directory/'TOY-ONLY-contract.json',plan)
    candidate=directory/'TOY-ONLY-input.hwpx';fill.write_input(workspace/'baseline.hwpx',candidate,selected)
    hwp=directory/'TOY-ONLY-saved.hwp';hwpx=directory/'TOY-ONLY-native.hwpx';pdf=directory/'TOY-ONLY-reopened.pdf'
    evidence={'test_only_not_deliverable':True,'deliberately_oversized':expected_rejection,'status':'blocked',
              'original_utf16_budget_is_not_visual_fit_proof':True}
    # Complete planned exports before evaluating expected rejection: never retry a failed export.
    evidence['native_export']=r.native_export(candidate,directory/'TOY-ONLY-buffer.pdf',hwp=hwp,hwpx=hwpx)
    evidence['reopened_hwp_export']=r.native_export(hwp,pdf)
    try:
        evidence['structural_gate']=fill.structural_gate(workspace/'baseline.hwpx',hwpx,selected,plan['header_spine'])
        evidence['structure_passed']=True
    except ValueError as exc:
        evidence['structure_passed']=False;evidence['structure_error']=str(exc)
        if not expected_rejection:raise
    masks={}
    for p in plan['placements']:masks.setdefault(p['page'],[]).append(p['region'])
    evidence['layout_gate']=r.compare_pdfs(workspace/'baseline.pdf',pdf,masks)
    evidence['rendered_page_text_exact']=fill.rendered_text_gate(workspace/'baseline.pdf',pdf,selected)
    evidence['embedded_binaries_equal']=r.hwp_audit(hwp)['embedded_binary_hashes']==baseline_audit['embedded_binary_hashes']
    evidence['optional_cells_still_empty']=all(matrix.optional_cell_empty(hwpx,targets[index]) for index in OMITTED)
    evidence['source_unchanged']=r.file_hash(source)==baseline_audit['sha256']
    r.verify_baseline(workspace)
    evidence['approval_absent']=not (workspace/'approved-plan.json').exists()
    protections=all(evidence[key] for key in ('embedded_binaries_equal','optional_cells_still_empty','source_unchanged','approval_absent'))
    all_gates=protections and evidence['structure_passed'] and evidence['layout_gate']['passed'] and evidence['rendered_page_text_exact']
    if expected_rejection:
        if not protections:raise ValueError('Unexpected source/image/optional-field mutation in negative case')
        if all_gates:raise ValueError('Oversized native candidate unexpectedly passed every gate')
        evidence['status']='expected_rejection_not_deliverable'
    else:
        if not all_gates:raise ValueError('Positive challenge failed strict gates; stop before negative variant')
        evidence['status']='checks_passed_needs_human_visual_review'
    r.write_json(directory/'TOY-ONLY-verification.json',evidence)
    return evidence


def run(output):
    output=output.resolve()
    if ROOT/'work' not in output.parents or output.exists() or not output.name.startswith('toy-fill-challenge-'):
        raise ValueError('Use only a NEW work/toy-fill-challenge-* directory')
    output.mkdir(parents=True)
    report={'test_only_not_deliverable':True,'status':'blocked','native_automatic_retries':0,
            'scope':'3 explicit pages / 27 repeated-label slots / 23 fills / 4 blanks / 3 own pictures / deliberate overflow',
            'human_visual_review_required':True}
    try:
        report['stage']='source_creation'
        source,_=matrix.create_source(output/'source',page_labels=[['반복 항목(가상)']*9 for _ in range(3)],column_widths=(70,70))
        source_audit=r.hwp_audit(source);report['source_audit']=source_audit
        if source_audit['embedded_binary_count']!=3:raise ValueError('Expected exactly three real embedded toy pictures')
        workspace=output/'review';report['stage']='no_edit_roundtrip';r.prepare(source,workspace)
        manifest=r.verify_baseline(workspace)
        if len(manifest['pages'])!=3:raise ValueError('Expected exactly three baseline pages')
        targets=[item for item in f.inspect_template(workspace/'baseline.hwpx')['fields'] if item['supported']]
        regions=fill.empty_regions(workspace/'baseline.pdf')
        if len(targets)!=27 or len(regions)!=27 or [p['page'] for p in regions]!=[1]*9+[2]*9+[3]*9:
            raise ValueError('Expected exactly 27 blank cells, nine physical interiors per page; no mapping guess')
        bindings={'schema':'hwp-fill-bindings/v1','fields':[
            {'field_id':item['field_id'],'key':'반복항목','kind':'text','required':index not in OMITTED,'max_utf16_units':50,'max_lines':1}
            for index,item in enumerate(targets)]}
        record=inputs();placements=[{'field_id':item['field_id'],**region} for index,(item,region) in enumerate(zip(targets,regions)) if index not in OMITTED]
        r.write_json(workspace/'fill-input.json',{'bindings':bindings,'record':record})
        plan=fill.proposal(workspace,bindings,record,placements)
        if len(fill.validate(plan,manifest,workspace/'baseline.hwpx'))!=23:raise ValueError('Expected 23 exact selective edits')
        r.write_json(workspace/'proposal-fill.json',plan)
        report['stage']='positive_dense_native'
        report['positive']=evaluate(workspace,source,source_audit,targets,plan,output/'positive')
        # An intentional NEW negative variant, not a repair or policy relaxation.
        oversized=deepcopy(record);oversized['values']['반복항목'][0]='가상 긴 문장 공간 부족 시험 '*40
        negative_bindings=deepcopy(bindings);negative_bindings['fields'][0]['max_utf16_units']=2000
        report['stage']='negative_input_isolation'
        negative_workspace=isolated_input_workspace(workspace,output/'negative-review',negative_bindings,oversized)
        negative=fill.proposal(negative_workspace,negative_bindings,oversized,placements)
        report['stage']='planned_oversized_native_rejection'
        report['negative']=evaluate(negative_workspace,source,source_audit,targets,negative,output/'NEGATIVE-OVERFLOW-NOT-DELIVERABLE',True)
        report['status']='challenge_checks_passed_needs_human_visual_review';report['stage']='checks_passed'
    except Exception as exc:
        report['error']=str(exc);r.write_json(output/'TOY-ONLY-challenge-verification.json',report);raise
    r.write_json(output/'TOY-ONLY-challenge-verification.json',report)
    return report


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--run-native-test',action='store_true',required=True)
    parser.add_argument('--toy-only',action='store_true',required=True)
    parser.add_argument('--output',type=Path,default=ROOT/'work'/('toy-fill-challenge-'+datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S')))
    args=parser.parse_args();print(__import__('json').dumps(run(args.output),ensure_ascii=False,indent=2))
