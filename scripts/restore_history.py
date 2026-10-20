"""Restore the latest same-branch report history from GitHub Actions artifacts."""

from datetime import datetime
import io
import json
import os
from pathlib import Path
import subprocess
import sys
import zipfile

NAME = 'information-gathering-history'
MAX_BYTES = 1024 * 1024


def github_api(endpoint, paginate=False):
    command = ['gh', 'api']
    if paginate:
        command += ['--paginate', '--slurp']
    result = subprocess.run(command + [endpoint], capture_output=True, check=True, timeout=120)
    return result.stdout


def restore():
    repository = os.environ['GITHUB_REPOSITORY']
    branch = os.environ['GITHUB_REF_NAME']
    reset_path = Path('config/history_reset_after.txt')
    reset_after = reset_path.read_text(encoding='utf-8').strip() if reset_path.exists() else ''
    if reset_after:
        parsed = datetime.fromisoformat(reset_after.replace('Z', '+00:00'))
        if parsed.tzinfo is None:
            raise ValueError('History reset time must include a timezone')
    pages = json.loads(github_api(f'repos/{repository}/actions/artifacts?per_page=100', True))
    matches = [artifact for page in pages for artifact in page['artifacts']
               if (artifact.get('name') == NAME or
                   (isinstance(artifact.get('name'), str) and artifact['name'].startswith(NAME + '-')))
               and not artifact.get('expired', True)
               and (not reset_after or datetime.fromisoformat(artifact['created_at'].replace('Z', '+00:00')) >= parsed)
               and (artifact.get('workflow_run') or {}).get('head_branch') == branch]
    if not matches:
        if reset_after:
            print('History reset applied; no reports retained since reset time')
        print('No retained history for this branch; starting the first report')
        return
    latest = max(matches, key=lambda item: (item['created_at'], item['id']))
    data = github_api(f'repos/{repository}/actions/artifacts/{int(latest["id"])}/zip')
    if len(data) > MAX_BYTES:
        raise ValueError('History archive exceeds size limit')
    with zipfile.ZipFile(io.BytesIO(data)) as archive:
        entry = archive.getinfo('history.json')
        if entry.file_size > MAX_BYTES:
            raise ValueError('History file exceeds size limit')
        contents = archive.read(entry)
    # The application checks the complete schema before calling the model.
    json.loads(contents)
    path = Path('.state/history.json')
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(contents)
    print('Restored history from the latest retained same-branch artifact')


if __name__ == '__main__':
    try:
        restore()
    except Exception:
        print('Failed to restore history; stopping to preserve deduplication. Check Actions permissions and artifact availability.', file=sys.stderr)
        sys.exit(1)
