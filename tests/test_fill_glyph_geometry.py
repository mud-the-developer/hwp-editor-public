"""Reject legible-text extraction with unreadable overlapping glyph geometry."""
import unittest
from unittest.mock import patch

import test_hwp_fill_plan as fixtures
r,fill=fixtures.r,fixtures.fill


class GlyphGeometryTests(unittest.TestCase):
    def test_adjacent_glyphs_mild_kerning_and_other_rows_remain_supported(self):
        for chars in [[('A',[0,0,10,10]),('B',[10,0,20,10])],
                      [('A',[0,0,10,10]),('B',[8,0,18,10])],
                      [('A',[0,0,10,10]),('B',[0,12,10,22])]]:
            self.assertTrue(fill._glyphs_readable(chars))

    def test_severe_overlap_rejected_but_combining_marks_are_not_false_positives(self):
        self.assertFalse(fill._glyphs_readable([('A',[0,0,10,10]),('B',[2,0,12,10])]))
        self.assertTrue(fill._glyphs_readable([('e',[0,0,10,10]),('\u0301',[0,0,10,10])]))
        self.assertTrue(fill._glyphs_readable([('A',[0,0,10,10]),(' ',[0,0,10,10])]))

    def test_same_text_and_inside_mask_pixels_do_not_make_overprinted_pdf_valid(self):
        fixture=fixtures.FillTests('test_exact_replay_regions_and_unsigned_policy_are_new_contract')
        fixture.setUp();self.addCleanup(fixture.temp.cleanup)
        record={'values':{'name':['A'*60,'Value B']},'sources':{'name':['FICTIONAL','FICTIONAL']}}
        bindings=fixtures.deepcopy(fixture.bindings);bindings['fields'][0]['max_utf16_units']=100
        plan=fill.proposal(fixture.w,bindings,record,fixture.placements)
        selected=fill.validate(plan,fixture.manifest,fixture.baseline)
        target=fixture.w/'overprinted.pdf'
        with r.fitz.open(fixture.w/'baseline.pdf') as doc:
            for index in range(60):doc[0].insert_text((88+index*1.5,80),'A',fontsize=10)
            doc[0].insert_text((88,110),'Value B',fontsize=10);doc.save(target)
        masks={1:[p['region'] for p in fixture.placements]}
        self.assertTrue(r.compare_pdfs(fixture.w/'baseline.pdf',target,masks)['passed'])
        with patch.object(fill,'_glyphs_readable',return_value=True):
            self.assertTrue(fill.rendered_text_gate(fixture.w/'baseline.pdf',target,selected),'old exact extraction/containment passed')
        self.assertFalse(fill.rendered_text_gate(fixture.w/'baseline.pdf',target,selected))
