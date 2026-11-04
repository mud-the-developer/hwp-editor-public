"""PDF-only partial/CJK/multipage/image matrix; NOT Hancom integration evidence."""
from copy import deepcopy
import unittest

import test_hwp_fill_plan as fixtures
r=fixtures.r
fill=fixtures.fill


class RenderMatrixTests(unittest.TestCase):
    def setUp(self):
        self.fixture=fixtures.FillTests('test_exact_replay_regions_and_unsigned_policy_are_new_contract')
        self.fixture.setUp();self.addCleanup(self.fixture.temp.cleanup)
        self.w=self.fixture.w

    def pdf(self,path,values=None,image_changed=False,missing_second_page=False):
        with r.fitz.open() as doc:
            for number in range(1 if missing_second_page else 2):
                page=doc.new_page(width=300,height=400)
                page.insert_text((20,30),'SYNTHETIC PAGE '+str(number+1))
                for y in [60,90]:
                    for x0,x1 in [(20,80),(80,220)]:page.draw_rect(r.fitz.Rect(x0,y,x1,y+30),color=(0,0,0))
                    page.insert_text((25,y+20),'Label',fontsize=10)
                if values and number in values:
                    row,text=values[number];page.insert_text((88,60+row*30+20),text,fontsize=10,fontname='korea')
                color=bytes([0,0,255] if image_changed and number==0 else [255,0,0])
                pix=r.fitz.Pixmap(r.fitz.csRGB,12,12,color*144,False)
                page.insert_image(r.fitz.Rect(25,180,55,210),stream=pix.tobytes('png'))
            doc.save(path)

    def prepare(self):
        before=self.w/'matrix.pdf';self.pdf(before)
        # Isolated fixture baseline PDF replacement only, never a real report baseline.
        (self.w/'baseline.pdf').write_bytes(before.read_bytes())
        manifest={**self.fixture.manifest,'baseline_pdf_sha256':r.file_hash(self.w/'baseline.pdf'),
                  'pages':[{'page':1,'width':300,'height':400},{'page':2,'width':300,'height':400}]}
        r.write_json(self.w/'manifest.json',manifest)
        regions=fill.empty_regions(self.w/'baseline.pdf')
        placements=[{'field_id':self.fixture.targets[0]['field_id'],**next(p for p in regions if p['page']==1 and p['region'][1]<80)},
                    {'field_id':self.fixture.targets[1]['field_id'],**next(p for p in regions if p['page']==2 and p['region'][1]>80)}]
        record={'values':{'name':['홍길동','한글 시험']},'sources':{'name':['synthetic CJK 1','synthetic CJK 2']}}
        plan=fill.proposal(self.w,self.fixture.bindings,record,placements)
        return plan,fill.validate(plan,manifest,self.fixture.baseline)

    def test_cjk_values_on_distinct_pages_with_original_picture_pixels_preserved(self):
        plan,selected=self.prepare();after=self.w/'matrix-after.pdf';self.pdf(after,{0:(0,'홍길동'),1:(1,'한글 시험')})
        self.assertTrue(fill.rendered_text_gate(self.w/'baseline.pdf',after,selected))
        masks={p['page']:[p['region']] for p in plan['placements']}
        self.assertTrue(r.compare_pdfs(self.w/'baseline.pdf',after,masks)['passed'])

    def test_missing_page_stray_glyph_wrong_page_and_changed_picture_fail(self):
        plan,selected=self.prepare()
        for name,values,changed,missing in [('extra',{0:(0,'홍길동ㅇ'),1:(1,'한글 시험')},False,False),
                 ('wrong-page',{0:(0,'한글 시험'),1:(1,'홍길동')},False,False),
                 ('missing-field',{0:(0,'홍길동')},False,False),
                 ('missing-page',{0:(0,'홍길동')},False,True)]:
            path=self.w/(name+'.pdf');self.pdf(path,values,changed,missing)
            with self.subTest(name=name):self.assertFalse(fill.rendered_text_gate(self.w/'baseline.pdf',path,selected))
        path=self.w/'changed-picture.pdf';self.pdf(path,{0:(0,'홍길동'),1:(1,'한글 시험')},True)
        self.assertTrue(fill.rendered_text_gate(self.w/'baseline.pdf',path,selected))
        masks={p['page']:[p['region']] for p in plan['placements']}
        self.assertFalse(r.compare_pdfs(self.w/'baseline.pdf',path,masks)['passed'])

    def test_optional_unfilled_field_stays_out_of_native_edit_scope(self):
        bindings=deepcopy(self.fixture.bindings);bindings['fields'][1]['required']=False
        record={'values':{'name':['Value A',None]},'sources':{'name':['synthetic first',None]}}
        plan=fill.proposal(self.w,bindings,record,[self.fixture.placements[0]])
        selected=fill.validate(plan,self.fixture.manifest,self.fixture.baseline)
        self.assertEqual(len(selected),1)
        target=self.w/'partial.hwpx';fill.write_input(self.fixture.baseline,target,selected)
        proof=fill.structural_gate(self.fixture.baseline,target,selected,plan['header_spine'])
        self.assertEqual(proof['exact_empty_cells_filled'],1)
        fields=fixtures.f.inspect_template(target)['fields']
        self.assertEqual(sum(item['supported'] for item in fields),1)

    def test_mixed_run_content_is_not_a_plain_empty_field(self):
        parts=r.archive(self.fixture.baseline)
        raw=parts['Contents/section0.xml'].replace(b'<hp:run charPrIDRef="3"><hp:t></hp:t>',b'<hp:run charPrIDRef="3">UNDECLARED<hp:t></hp:t>',1)
        path=self.fixture.change(self.fixture.baseline,lambda _:raw)
        mapped=fixtures.f.inspect_template(path)
        field=next(item for item in mapped['fields'] if item['field_id']==self.fixture.targets[0]['field_id'])
        self.assertFalse(field['supported'])
