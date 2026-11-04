"""Opt-in native CJK/two-page/picture/optional-blank lab; NEW unsigned toy only.
Creates its OWN pictures/source. No real apply/approval; failures STOP without retry.
"""
from __future__ import annotations
import argparse
from datetime import datetime, timezone
from pathlib import Path
import sys

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'scripts'))
import hwp_review as r
import hwp_fill_plan as fill
import hwp_form_draft as f
from hwp_security import register_security_module

MARKER='TOY ONLY - CJK MULTIPAGE PICTURE TEST - NOT FOR DELIVERY'


def navigate(app,name):
    # Cached IHwpObject.Run uses VT_VOID (24): successful calls return None.
    # Do not manufacture a failed native action from a void return value.
    app.Run(name)


def optional_cell_empty(path,target):
    fields=f.inspect_template(path)['fields']
    matches=[item for item in fields if item['part']==target['part'] and item['cell_path']==target['cell_path']]
    if len(matches)!=1:raise ValueError('Optional cell address missing or ambiguous after native save')
    return matches[0]['expected_text']==''


def chart(path,number):
    with r.fitz.open() as doc:
        page=doc.new_page(width=300,height=90)
        page.insert_text((10,17),f'TOY ONLY - FICTIONAL PICTURE {number} - NOT EVIDENCE',fontsize=9)
        for row,(width,color) in enumerate([(185+number*4,(.15,.45,.75)),(130+number*5,(.2,.65,.4)),(65+number*7,(.85,.5,.15))]):
            rect=r.fitz.Rect(12,28+row*17,12+width,39+row*17)
            page.draw_rect(rect,color=color,fill=color)
        page.get_pixmap(dpi=144,alpha=False).save(path)


def create_source(directory,page_labels=None,column_widths=(45,95)):
    if page_labels is None:page_labels=[['성명(가상)','번호(가상)','미입력(선택)'],['기관(가상)','날짜(가상)','기록(가상)']]
    if (not 1<=len(page_labels)<=6 or any(not 1<=len(labels)<=10 for labels in page_labels)
            or len(column_widths)!=2 or any(type(width) is not int or not 30<=width<=110 for width in column_widths)
            or sum(column_widths)!=140):raise ValueError('Invalid bounded toy source specification')
    import pythoncom
    import win32com.client
    directory.mkdir(parents=True,exist_ok=False)
    pictures=[]
    for number in range(1,len(page_labels)+1):
        path=directory/f'TOY-ONLY-picture-{number}.png';chart(path,number);pictures.append(path)
    pythoncom.CoInitialize();app=None
    try:
        app=win32com.client.gencache.EnsureDispatch(win32com.client.DispatchEx('HWPFrame.HwpObject'))
        app.XHwpWindows.Item(0).Visible=False
        security=register_security_module(app)
        def action(name,parameter):
            if not app.HAction.Execute(name,parameter.HSet):raise RuntimeError('Toy native action failed: '+name)
        def run(name):
            navigate(app,name)
        def font():
            p=app.HParameterSet.HCharShape;app.HAction.GetDefault('CharShape',p.HSet)
            for language in ('Hangul','Latin','Hanja','Japanese','Other','Symbol','User'):
                setattr(p,'FaceName'+language,'함초롬바탕')
            p.Height=1000;p.Bold=0;p.Italic=0;p.UnderlineType=0;action('CharShape',p)
        def text(value):
            p=app.HParameterSet.HInsertText;app.HAction.GetDefault('InsertText',p.HSet);p.Text=value;action('InsertText',p)
        for page_number,labels in enumerate(page_labels,1):
            if page_number>1:run('MoveDocEnd');run('BreakPage')
            font();text(MARKER);run('BreakPara');text(f'가상 양식 {page_number}쪽 / 직인·서명 없음 / 그림은 자체 생성 시험 자료');run('BreakPara')
            picture=app.InsertPicture(str(pictures[page_number-1].resolve()),True,1,False,False,0,100,30)
            if picture is None:raise RuntimeError('Native embedded toy picture insertion failed')
            properties=picture.Properties;properties.SetItem('TreatAsChar',1);picture.Properties=properties
            run('MoveDocEnd');run('BreakPara');font()
            p=app.HParameterSet.HTableCreation;app.HAction.GetDefault('TableCreate',p.HSet)
            p.Rows=len(labels)+1;p.Cols=2;p.WidthType=1;p.HeightType=1;p.WidthValue=app.MiliToHwpUnit(140);p.HeightValue=app.MiliToHwpUnit(12*p.Rows)
            p.TableProperties.TreatAsChar=1;p.CreateItemArray('ColWidth',2)
            for index,width in enumerate(column_widths):p.ColWidth.SetItem(index,app.MiliToHwpUnit(width))
            action('TableCreate',p)
            for index,label in enumerate(['항목 / TEST LABEL',*labels]):
                text(label);run('TableRightCell')
                if index==0:text('입력 칸 / EMPTY CELLS BELOW')
                if index<len(labels):run('TableLeftCell');run('TableLowerCell')
            run('MoveDocEnd');run('BreakPara');text('TOY ONLY - 새 제출 문서 아님. 시험 자료 외 실제 연구 결과 없음.');run('BreakPara')
        source=directory/'TOY-ONLY-CJK-picture-template.hwp'
        if not app.SaveAs(str(source.resolve()),'HWP','') or not source.is_file():raise RuntimeError('Native matrix source save failed')
        if int(app.PageCount)!=len(page_labels):raise ValueError('Toy creation did not produce the declared page count; no layout repair')
        return source,security
    finally:
        if app is not None:
            try:app.Clear(1);app.Quit()
            finally:del app
        pythoncom.CoUninitialize()


def run(output):
    output=output.resolve()
    if ROOT/'work' not in output.parents or output.exists() or not output.name.startswith('toy-fill-matrix-'):
        raise ValueError('Use only a NEW work/toy-fill-matrix-* directory')
    output.mkdir(parents=True)
    report={'test_only_not_deliverable':True,'native_status':'blocked','human_visual_review_required':True,
            'scope':['CJK','two-pages','two-self-generated-embedded-pictures','optional-unfilled-cell'],'native_retry_performed':False}
    source=None
    try:
        report['stage']='source_creation'
        source,security=create_source(output/'source');report['security_module']=security
        report['source_sha256_before']=r.file_hash(source)
        audit=r.hwp_audit(source);report['original_embedded_binary_count']=len(audit['embedded_binary_hashes'])
        if report['original_embedded_binary_count']<2:raise ValueError('Toy source must really embed both pictures')
        workspace=output/'review';report['review_workspace']=str(workspace);report['stage']='no_edit_roundtrip';r.prepare(source,workspace)
        manifest=r.verify_baseline(workspace)
        if len(manifest['pages'])!=2:raise ValueError('Native baseline is not exactly two pages')
        report['stage']='field_map'
        mapped=f.inspect_template(workspace/'baseline.hwpx');targets=[item for item in mapped['fields'] if item['supported']]
        regions=fill.empty_regions(workspace/'baseline.pdf')
        if len(targets)!=6 or len(regions)!=6 or [p['page'] for p in regions]!=[1,1,1,2,2,2]:
            raise ValueError(f'Expected six blank cells and three original interiors per page: {len(targets)}/{len(regions)}')
        keys=['성명','번호','미입력','기관','날짜','기록'];kinds=['text','integer','text','text','date','text']
        bindings={'schema':'hwp-fill-bindings/v1','fields':[
            {'field_id':item['field_id'],'key':key,'kind':kind,'required':index!=2,'max_utf16_units':30,'max_lines':1}
            for index,(item,key,kind) in enumerate(zip(targets,keys,kinds))]}
        record={'values':{'성명':'홍길동(가상)','번호':'7','기관':'가상 시험팀','날짜':'2026-10-10','기록':'한글 그림 시험'},
                'sources':{key:'FICTIONAL TEST INPUT / 실제 자료 아님' for key in keys if key!='미입력'}}
        r.write_json(workspace/'fill-input.json',{'bindings':bindings,'record':record})
        placements=[{'field_id':item['field_id'],**region} for index,(item,region) in enumerate(zip(targets,regions)) if index!=2]
        plan=fill.proposal(workspace,bindings,record,placements);selected=fill.validate(plan,manifest,workspace/'baseline.hwpx')
        r.write_json(workspace/'proposal-fill.json',plan)
        report['supplied_fields']=len(selected);report['optional_unfilled_fields']=1
        candidate=output/'TOY-ONLY-matrix-input.hwpx';fill.write_input(workspace/'baseline.hwpx',candidate,selected)
        hwp=output/'TOY-ONLY-matrix-filled.hwp';hwpx=output/'TOY-ONLY-matrix-native.hwpx';pdf=output/'TOY-ONLY-matrix-reopened.pdf'
        report['stage']='native_save'
        report['native_export']=r.native_export(candidate,output/'TOY-ONLY-matrix-buffer.pdf',hwp=hwp,hwpx=hwpx)
        report['stage']='structural_gate'
        report['structural_gate']=fill.structural_gate(workspace/'baseline.hwpx',hwpx,selected,plan['header_spine'])
        report['optional_cell_still_empty']=optional_cell_empty(hwpx,targets[2])
        report['stage']='saved_hwp_reopen'
        report['reopen_export']=r.native_export(hwp,pdf)
        report['stage']='rendered_gates'
        masks={}
        for p in placements:masks.setdefault(p['page'],[]).append(p['region'])
        report['layout_gate']=r.compare_pdfs(workspace/'baseline.pdf',pdf,masks)
        report['rendered_page_text_exact']=fill.rendered_text_gate(workspace/'baseline.pdf',pdf,selected)
        final_audit=r.hwp_audit(hwp);report['candidate_embedded_binary_count']=len(final_audit['embedded_binary_hashes'])
        report['embedded_binaries_equal']=final_audit['embedded_binary_hashes']==audit['embedded_binary_hashes']
        report['source_unchanged']=r.file_hash(source)==report['source_sha256_before']
        report['baseline_verified_again']=r.verify_baseline(workspace)['workspace_id']==manifest['workspace_id']
        report['approval_file_absent']=not (workspace/'approved-plan.json').exists()
        if not all([report['layout_gate']['passed'],report['rendered_page_text_exact'],report['embedded_binaries_equal'],
                    report['optional_cell_still_empty'],report['source_unchanged'],report['baseline_verified_again'],report['approval_file_absent']]):
            raise ValueError('Native matrix failed strict gates; no retry, mask enlargement or repair')
        report['native_status']='checks_passed_needs_human_visual_review';report['stage']='checks_passed';report['reopened_pdf']=str(pdf)
    except Exception as exc:
        report['error']=str(exc)
        if source is not None and 'source_sha256_before' in report:
            report['source_unchanged']=r.file_hash(source)==report['source_sha256_before']
        r.write_json(output/'TOY-ONLY-matrix-verification.json',report);raise
    r.write_json(output/'TOY-ONLY-matrix-verification.json',report)
    return report


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--run-native-test',action='store_true',required=True)
    parser.add_argument('--toy-only',action='store_true',required=True)
    parser.add_argument('--output',type=Path,default=ROOT/'work'/('toy-fill-matrix-'+datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S')))
    args=parser.parse_args()
    print(__import__('json').dumps(run(args.output),ensure_ascii=False,indent=2))
