"""Inspect the exact Git index before publication; never print matched secrets."""
from pathlib import PurePosixPath
import re
import subprocess
import sys


def audit():
    paths = subprocess.check_output(['git', 'ls-files', '--cached', '-z']).decode('utf-8').split('\0')
    patterns = [
        rb'sk-(?:proj-)?[A-Za-z0-9_-]{20,}',
        rb'(?:ghp_|gho_|ghu_|ghs_|github_pat_)[A-Za-z0-9_]{20,}',
        rb'AKIA[A-Z0-9]{16}',
        rb'-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----',
        rb'[A-Za-z]:[\\/]Users[\\/](?!Public\b|Default\b)[^\s"<>]+',
    ]
    forbidden = {'.private', '.runtime', '.browser-profile', '.venv', 'artifacts', '.publish', '__pycache__', '.codex', '.agents'}
    issues = []
    count = 0
    for path in filter(None, paths):
        count += 1
        parts = PurePosixPath(path).parts
        if forbidden.intersection(parts) or (parts[-1].startswith('.env') and path != '.env.example') or parts[-1] in {'auth.json', 'credentials.json'}:
            issues.append((path, 'private file or directory'))
            continue
        blob = subprocess.check_output(['git', 'show', ':' + path])
        if any(re.search(pattern, blob) for pattern in patterns):
            issues.append((path, 'possible credential or private local path'))
        if path == '.env.example':
            text = blob.decode('utf-8')
            if re.search(r'^AI_API_KEY[ \t]*=[ \t]*[^\s#]+', text, re.M):
                issues.append((path, 'nonempty example API key'))
    if issues:
        for path, reason in issues:
            print(f'FAIL: {path}: {reason}')
        return 1
    if not count:
        print('FAIL: no files staged for publication.')
        return 1
    print(f'PASS: {count} staged files; no forbidden files or credential patterns found.')
    return 0


if __name__ == '__main__':
    sys.exit(audit())
