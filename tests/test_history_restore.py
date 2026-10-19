from contextlib import redirect_stdout
import io
import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch
import zipfile

from scripts import restore_history as history


def archive(content):
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, 'w') as file:
        file.writestr('history.json', content)
    return buffer.getvalue()


class RestoreTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.old_cwd = Path.cwd()
        os.chdir(self.directory.name)
        self.addCleanup(os.chdir, self.old_cwd)
        self.environment = patch.dict(os.environ, {'GITHUB_REPOSITORY': 'owner/repo', 'GITHUB_REF_NAME': 'main'})
        self.environment.start()
        self.addCleanup(self.environment.stop)

    @patch('scripts.restore_history.github_api')
    def test_restores_latest_nonexpired_same_branch_history(self, api):
        artifacts = [
            {'id': 1, 'name': f'{history.NAME}-1', 'created_at': '2026-01-01', 'expired': False,
             'workflow_run': {'head_branch': 'main'}},
            {'id': 2, 'name': f'{history.NAME}-2', 'created_at': '2026-01-02', 'expired': False,
             'workflow_run': {'head_branch': 'main'}},
            {'id': 3, 'name': f'{history.NAME}-3', 'created_at': '2026-01-03', 'expired': False,
             'workflow_run': {'head_branch': 'feature'}},
            {'id': 4, 'name': f'{history.NAME}-4', 'created_at': '2026-01-04', 'expired': True,
             'workflow_run': {'head_branch': 'main'}},
            {'id': 5, 'name': 'unrelated-artifact', 'created_at': '2026-01-05', 'expired': False,
             'workflow_run': {'head_branch': 'main'}},
        ]
        payload = '{"version":1,"reports":[]}'
        api.side_effect = [json.dumps([{'artifacts': artifacts[:2]}, {'artifacts': artifacts[2:]}]).encode(), archive(payload)]
        with redirect_stdout(io.StringIO()):
            history.restore()
        self.assertEqual(Path('.state/history.json').read_text(), payload)
        self.assertEqual(api.call_args.args[0], 'repos/owner/repo/actions/artifacts/2/zip')

    @patch('scripts.restore_history.github_api')
    def test_restores_legacy_artifact_during_name_migration(self, api):
        artifacts = [{'id': 8, 'name': history.NAME, 'created_at': '2026-01-01', 'expired': False,
                      'workflow_run': {'head_branch': 'main'}}]
        api.side_effect = [json.dumps([{'artifacts': artifacts}]).encode(), archive('{"version":1,"reports":[]}')]
        with redirect_stdout(io.StringIO()):
            history.restore()
        self.assertEqual(api.call_args.args[0], 'repos/owner/repo/actions/artifacts/8/zip')

    @patch('scripts.restore_history.github_api', return_value=b'[{"artifacts":[]}]')
    def test_first_run_does_not_create_history(self, api):
        output = io.StringIO()
        with redirect_stdout(output):
            history.restore()
        self.assertFalse(Path('.state/history.json').exists())
        self.assertIn('first report', output.getvalue())

    @patch('scripts.restore_history.github_api', side_effect=subprocess.CalledProcessError(1, 'gh'))
    def test_access_failure_does_not_start_with_empty_history(self, api):
        with self.assertRaises(subprocess.CalledProcessError):
            history.restore()
        self.assertFalse(Path('.state/history.json').exists())

    @patch('scripts.restore_history.github_api')
    def test_invalid_archive_is_rejected(self, api):
        api.side_effect = [b'[{"artifacts":[{"id":1,"name":"information-gathering-history-1","expired":false,"created_at":"2026-01-01","workflow_run":{"head_branch":"main"}}]}]', b'not zip']
        with self.assertRaises(zipfile.BadZipFile):
            history.restore()
        self.assertFalse(Path('.state/history.json').exists())


if __name__ == '__main__':
    unittest.main()
