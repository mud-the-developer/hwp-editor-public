"""No network, registry, desktop, real installer, release or Native actions."""
from contextlib import ExitStack
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
import release_host as host


class ReleaseHostTests(unittest.TestCase):
    def test_failed_install_is_not_repeated_or_published_even_after_restart(self):
        sha = 'b' * 40
        with tempfile.TemporaryDirectory() as folder, ExitStack() as stack:
            home = Path(folder)
            host.save(home / 'since.json', '2026-01-01T00:00:00Z')
            row = {'created_at': '2026-01-02T00:00:00Z', 'id': 5, 'head_sha': sha,
                   'status': 'completed', 'conclusion': 'success'}
            stack.enter_context(patch.object(host, 'main_sha', return_value=sha))
            stack.enter_context(patch.object(host, 'accepted', return_value=True))
            stack.enter_context(patch.object(host, 'gh', return_value=json.dumps([{'workflow_runs': [row]}])))
            published = stack.enter_context(patch.object(host, 'publish'))

            def fail(c, run, commit):
                run.mkdir()
                host.state(run, commit, 'installing')
                raise ValueError('GUI not rendered')

            started = stack.enter_context(patch.object(host, 'prepare', side_effect=fail))
            with self.assertRaisesRegex(ValueError, 'GUI not rendered'):
                host.tick({}, home)
            host.tick({}, home)
            self.assertEqual(started.call_count, 1)
            self.assertEqual(host.load(home / sha[:12] / 'state.json')['failed_stage'], 'installing')
            published.assert_not_called()

    def test_unaccepted_remote_and_obsolete_qualified_builds_cannot_publish(self):
        old, new = 'a' * 40, 'b' * 40
        with tempfile.TemporaryDirectory() as folder, ExitStack() as stack:
            home = Path(folder)
            host.save(home / 'since.json', '2026-01-01T00:00:00Z')
            (home / old[:12]).mkdir()
            host.state(home / old[:12], old, 'qualified')
            rows = [{'created_at': f'2026-01-0{i}T00:00:00Z', 'id': i, 'head_sha': sha,
                     'status': 'completed', 'conclusion': 'success'} for i, sha in enumerate([old, new], 2)]
            stack.enter_context(patch.object(host, 'main_sha', return_value=new))
            stack.enter_context(patch.object(host, 'accepted', side_effect=lambda c, sha: sha == old))
            stack.enter_context(patch.object(host, 'gh', return_value=json.dumps([{'workflow_runs': rows}])))
            with patch.object(host, 'publish') as publish:
                host.tick({}, home)
                publish.assert_not_called()
            self.assertFalse((home / new[:12]).exists())

    def test_remote_asset_substitution_and_draft_status_block_release(self):
        sha, tag, digest = 'b' * 40, 'test-tag', 'a' * 64
        hashes = {'kit': {'bytes': 99, 'sha256': digest}}
        remote = {'tag_name': tag, 'target_commitish': sha, 'draft': True, 'prerelease': True,
                  'assets': [{'state': 'uploaded', 'name': 'kit', 'digest': 'sha256:' + '0' * 64, 'size': 99}]}
        with self.assertRaisesRegex(ValueError, 'digests'):
            host.check_remote(remote, sha, tag, hashes, draft=True)
        remote['assets'][0]['digest'] = 'sha256:' + digest
        with self.assertRaisesRegex(ValueError, 'identity'):
            host.check_remote(remote, sha, tag, hashes, draft=False)

    def test_prepared_only_normal_launch_never_opens_setup_on_drift(self):
        import hwp_setup_ui as ui
        backend = Mock()
        with (patch.dict('os.environ', HWP_REVIEW_REQUIRE_PREPARED='1'),
              patch.object(ui.setup, 'statuses_ready', return_value=False),
              patch.object(ui, 'run', side_effect=AssertionError('opened setup')),
              patch.object(ui, '_show_preparation_error', side_effect=AssertionError('opened a dialog'))):
            self.assertFalse(ui.ensure_ready(backend=backend))
            backend.installed_status.side_effect = OSError('prerequisite unavailable')
            self.assertFalse(ui.ensure_ready(backend=backend))
