"""Create server-only login credentials, keeping plaintext out of command lines."""
import argparse
import ipaddress
import os
from pathlib import Path
import re
import secrets
import subprocess


def initialize(root, host, bind='127.0.0.1', port=18765, runner=subprocess.run):
    if not re.fullmatch(r'(?=.{1,253}$)[a-z0-9](?:[a-z0-9.-]*[a-z0-9])?\.[a-z]{2,}', host) or '..' in host:
        raise ValueError('Provide a plain public DNS hostname, without protocol or path.')
    address = ipaddress.IPv4Address(bind)
    if not address.is_private or address.is_unspecified or address.is_multicast or not 1024 <= port <= 65535:
        raise ValueError('Bind to a specific private/loopback IPv4 address and an unprivileged port.')
    root = Path(root).resolve()
    folder = root / '.secrets'
    if folder.exists() or (root / '.env').exists():
        raise ValueError('Existing private settings found; initialization never overwrites them.')
    password = secrets.token_urlsafe(24)
    result = runner(['docker', 'run', '--rm', '-i', '--entrypoint', 'htpasswd', 'httpd:2.4-alpine', '-niB', '-C', '10', 'owner'],
                    input=password + '\n', text=True, capture_output=True, timeout=180)
    hashed = result.stdout.strip()
    if result.returncode or not re.fullmatch(r'owner:\$2[aby]\$10\$[./A-Za-z0-9]{53}', hashed):
        raise ValueError('Could not create the login hash; Docker must be running. No credentials were printed.')
    folder.mkdir(mode=0o700)
    (folder / 'htpasswd').write_text(hashed + '\n', encoding='utf-8')
    os.chmod(folder / 'htpasswd', 0o644)  # Parent directory is private; gateway uid 101 must read this mounted hash.
    with (folder / 'initial-login.txt').open('x', encoding='utf-8') as output:
        output.write('username: owner\npassword: ' + password + '\n')
    os.chmod(folder / 'initial-login.txt', 0o600)
    with (root / '.env').open('x', encoding='utf-8') as output:
        output.write(f'PUBLIC_HOST={host}\nBIND_IP={bind}\nGATEWAY_PORT={port}\nAI_BASE_URL=\nAI_API_KEY=\nAI_MODEL=\nAI_API_STYLE=chat_completions\nAI_RESPONSE_FORMAT=json_schema\nAI_TIMEOUT_SECONDS=180\nAI_VISION_ENABLED=true\n')
    os.chmod(root / '.env', 0o600)
    password = None
    return {'created': True, 'ai_configured': False, 'login_file': '.secrets/initial-login.txt'}


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--host', required=True)
    parser.add_argument('--bind', default='127.0.0.1')
    parser.add_argument('--port', type=int, default=18765)
    args = parser.parse_args()
    try:
        result = initialize(Path(__file__).resolve().parent.parent, args.host, args.bind, args.port)
        print('Private login created. Read .secrets/initial-login.txt in your own server terminal. AI remains disabled.')
    except (ValueError, OSError, subprocess.SubprocessError) as exc:
        print('Initialization failed: ' + (str(exc) if isinstance(exc, ValueError) else type(exc).__name__))
        raise SystemExit(1)
