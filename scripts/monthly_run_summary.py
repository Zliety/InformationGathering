"""Summarize last month's completed main-branch digest runs into a repository log.

Counts workflow runs, not individual jobs or attempts. A rerun uses its latest
conclusion. Month boundaries use Asia/Shanghai; failure includes timeout and
startup failure. Other completed conclusions are recorded separately.
"""
from datetime import datetime, timedelta, timezone
import json
import os
from pathlib import Path
import re
import subprocess
from urllib.parse import urlencode
from zoneinfo import ZoneInfo


LOG_PATH = Path('logs/monthly-runs.log')


def main():
    repository = os.environ['GITHUB_REPOSITORY']
    if not re.fullmatch(r'[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+', repository):
        raise ValueError('Invalid repository name')
    now = datetime.now(ZoneInfo('Asia/Shanghai'))
    end = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    start = (end - timedelta(days=1)).replace(day=1)
    month = start.strftime('%Y-%m')
    def utc(value):
        return value.astimezone(timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')
    query = urlencode({'branch': 'main', 'status': 'completed', 'per_page': 100,
                       'created': f'{utc(start)}..{utc(end - timedelta(seconds=1))}'})
    endpoint = f'repos/{repository}/actions/workflows/news.yml/runs?{query}'
    response = subprocess.run(['gh', 'api', '--paginate', '--slurp', endpoint],
                              capture_output=True, check=True, timeout=180)
    pages = json.loads(response.stdout)
    runs = {}
    total = None
    for page in pages:
        if not isinstance(page, dict) or not isinstance(page.get('workflow_runs'), list):
            raise ValueError('Invalid workflow run response')
        if total is None:
            total = page['total_count']
        for run in page['workflow_runs']:
            created = datetime.fromisoformat(run['created_at'].replace('Z', '+00:00'))
            if start <= created < end and run['head_branch'] == 'main' and run['status'] == 'completed':
                runs[run['id']] = run['conclusion']
    # GitHub limits filtered workflow-run searches to 1,000 results. Never write
    # an incomplete monthly statistic if that limit is reached.
    if total is None or total != len(runs):
        raise ValueError('Incomplete or changing workflow run results; retry or split the date range')
    success = sum(value == 'success' for value in runs.values())
    failure = sum(value in ('failure', 'timed_out', 'startup_failure') for value in runs.values())
    other = len(runs) - success - failure
    entry = f'{month} success={success} failure={failure} other={other} total={len(runs)}'
    lines = LOG_PATH.read_text(encoding='utf-8').splitlines() if LOG_PATH.exists() else []
    # Re-running replaces this month's row rather than adding duplicate history.
    lines = [line for line in lines if not line.startswith(month + ' ')]
    lines.append(entry)
    headers = [line for line in lines if line.startswith('#')]
    entries = sorted(line for line in lines if line and not line.startswith('#'))
    LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
    LOG_PATH.write_text('\n'.join(headers + entries) + '\n', encoding='utf-8')
    print(entry)


if __name__ == '__main__':
    try:
        main()
    except Exception:
        raise SystemExit('Monthly run summary failed; log not updated. Check Actions access and API availability.')
