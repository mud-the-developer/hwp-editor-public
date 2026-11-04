"""Status inspection uses synthetic JSON only; never opens/applies a document."""
from pathlib import Path
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
import hwp_review as r


class AttemptTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.workspace = Path(self.temp.name)
        self.manifest = {'workspace_id': 'attempt-fixture-only', 'baseline_hwpx_sha256': 'a' * 64}
        self.plan = {'schema': 1, **self.manifest, 'edits': []}
        self.run = self.workspace / 'runs/20261009T144602-d6cd5e4f'
        self.run.mkdir(parents=True)
        r.write_json(self.run / 'plan.json', self.plan)
        r.write_json(self.run / 'verification.json', {'status': 'blocked', 'source_unchanged': True,
                     'error': 'Synthetic locked table changed', 'run': '/untrusted/path/ignored'})

    def test_failed_before_reopen_still_has_read_only_status(self):
        result = r.review_attempts(self.workspace, self.manifest)
        self.assertFalse((self.run / 'candidate.pdf').exists())
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0]['status'], 'blocked')
        self.assertEqual(result[0]['plan_sha256'], r.plan_hash(self.plan))
        self.assertTrue(result[0]['source_unchanged'])
        self.assertNotIn('run', result[0])

    def test_foreign_baseline_or_workspace_is_ignored(self):
        for field in ('workspace_id', 'baseline_hwpx_sha256'):
            r.write_json(self.run / 'plan.json', {**self.plan, field: 'foreign'})
            self.assertEqual(r.review_attempts(self.workspace, self.manifest), [])

    def test_invalid_status_artifact_or_id_is_ignored(self):
        report = self.run / 'verification.json'
        for raw in ('{bad json', '{}', '{"status":"delivered"}', 'x' * (1024 * 1024 + 1)):
            report.write_text(raw, encoding='utf-8')
            self.assertEqual(r.review_attempts(self.workspace, self.manifest), [])
        report.write_text('{"status":"blocked"}', encoding='utf-8')
        self.run.rename(self.run.with_name('unexpected-directory'))
        self.assertEqual(r.review_attempts(self.workspace, self.manifest), [])

    def test_passed_label_without_reopened_pdf_is_not_accepted(self):
        r.write_json(self.run / 'verification.json', {'status':'checks_passed_needs_human_visual_review','source_unchanged':True})
        self.assertEqual(r.review_attempts(self.workspace,self.manifest),[])

    def test_display_errors_are_bounded_and_unknown_source_is_not_success(self):
        r.write_json(self.run / 'verification.json', {'status': 'blocked', 'error': '<img>' * 1000})
        result = r.review_attempts(self.workspace, self.manifest)[0]
        self.assertLessEqual(len(result['error']), 500)
        self.assertFalse(result['source_unchanged'])
