"""Strict JSON and persisted consent typing; disposable synthetic files only."""
from copy import deepcopy
from pathlib import Path
import sys
import tempfile
import unittest

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
import hwp_review as r
import hwp_fill_plan as fill
import test_hwp_fill_plan as fixtures


class StrictJsonTests(unittest.TestCase):
    def test_regular_unicode_bools_and_finite_coordinates_still_parse(self):
        self.assertEqual(r.parse_json('{"name":"한글", "ok":true, "region":[1,2.5,3e1,40]}'),
                         {'name':'한글','ok':True,'region':[1,2.5,30.0,40]})

    def test_duplicate_keys_are_rejected_at_every_depth(self):
        for blob in ['{"x":1,"x":2}','{"outer":{"ok":true,"ok":true}}','[{"a":1,"a":1}]']:
            with self.subTest(blob=blob),self.assertRaisesRegex(ValueError,'Duplicate JSON key'):r.parse_json(blob)

    def test_nonfinite_constants_and_numeric_overflow_are_rejected(self):
        for number in ['NaN','Infinity','-Infinity','1e309','-1e309']:
            with self.subTest(number=number),self.assertRaises(ValueError):r.parse_json('{"value":'+number+'}')

    def test_disk_reader_and_writers_are_not_looser_than_http_parser(self):
        with tempfile.TemporaryDirectory() as directory:
            path=Path(directory)/'fixture.json';path.write_text('{"x":1,"x":2}',encoding='utf-8')
            with self.assertRaises(ValueError):r.read_json(path)
            original=path.read_bytes()
            for value in (float('inf'),float('-inf'),float('nan')):
                with self.assertRaises(ValueError):r.write_json(path,{'value':value})
                self.assertEqual(path.read_bytes(),original)
                with self.assertRaises(ValueError):r.plan_hash({'value':value})

    def test_scope_numeric_one_and_embedded_plan_bool_coercion_are_not_exact_consent(self):
        fixture=fixtures.FillTests('test_exact_replay_regions_and_unsigned_policy_are_new_contract')
        fixture.setUp();self.addCleanup(fixture.temp.cleanup)
        approval={'approved_via':'local-review-ui','plan_sha256':r.plan_hash(fixture.plan),'plan':fixture.plan,
                  'approved_scope':fill.approval_scope(fixture.plan)}
        for flag in ('fill_policy_confirmed','unsigned_template_confirmed','header_spine_confirmed'):
            wrong=deepcopy(approval);wrong['approved_scope'][flag]=1
            r.write_json(fixture.w/'approved-plan.json',wrong)
            with self.subTest(flag=flag),self.assertRaises(ValueError):r.approved_plan(fixture.w,fixture.plan,fixture.manifest)
        wrong=deepcopy(approval);wrong['plan']['bindings']['fields'][0]['required']=1
        r.write_json(fixture.w/'approved-plan.json',wrong)
        with self.assertRaises(ValueError):r.approved_plan(fixture.w,fixture.plan,fixture.manifest)
        r.write_json(fixture.w/'approved-plan.json',approval)
        self.assertEqual(len(r.approved_plan(fixture.w,fixture.plan,fixture.manifest)),2)

    def test_legacy_schema_number_cannot_be_bool_or_float(self):
        plan={'schema':1,'workspace_id':'fixture','baseline_hwpx_sha256':'0'*64,'edits':[]}
        for schema in (True,1.0):
            with self.subTest(schema=schema),self.assertRaises(ValueError):r.validate_plan({**plan,'schema':schema},{})
