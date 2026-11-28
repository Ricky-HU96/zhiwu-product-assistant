"""Run on the deployment host; report checks without printing credentials or tokens."""
import argparse
import base64
import json
from pathlib import Path
import sys
import urllib.error
import urllib.request

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from app.config import read_env


def main(public=False):
    settings = read_env(ROOT / '.env')
    login = dict(line.split(': ', 1) for line in (ROOT / '.secrets/initial-login.txt').read_text().splitlines())
    authorization = 'Basic ' + base64.b64encode((login['username'] + ':' + login['password']).encode()).decode()
    base = 'https://' + settings['PUBLIC_HOST'] if public else 'http://' + settings['BIND_IP'] + ':' + settings['GATEWAY_PORT']

    class NoRedirect(urllib.request.HTTPRedirectHandler):
        def redirect_request(self, *args, **kwargs):
            return None

    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), NoRedirect())

    def request(path, authenticated=True, data=None, extra=None):
        headers = {'Host': settings['PUBLIC_HOST']}
        if authenticated:
            headers['Authorization'] = authorization
        if data is not None:
            headers['Content-Type'] = 'application/json'
            data = json.dumps(data).encode()
        headers.update(extra or {})
        req = urllib.request.Request(base + path, headers=headers, data=data)
        try:
            response = opener.open(req, timeout=10)
        except urllib.error.HTTPError as exc:
            response = exc
        with response:
            return response.status, response.read()

    checks = {}
    checks['login_required_page'] = request('/', False)[0] == 401
    checks['login_required_api'] = request('/api/health', False)[0] == 401
    checks['authenticated_page'] = request('/')[0] == 200
    checks['authenticated_assets'] = all(request(path)[0] == 200 for path in ('/app.js', '/sku-session.js', '/style.css', '/product.css'))
    code, body = request('/api/health')
    health = json.loads(body) if code == 200 else {}
    checks['ai_disabled'] = code == 200 and health.get('ai', {}).get('ready') is False
    # The configuration help text may name required fields, but never their values.
    checks['health_has_no_provider_settings'] = not any(key in health.get('ai', {}) for key in ('api_key', 'base_url', 'model'))
    checks['private_files_hidden'] = all(request(path)[0] == 404 for path in ('/.env', '/.secrets/initial-login.txt', '/.git/config', '/server.py'))
    if not public:  # A different Host at Cloudflare reaches a different route before our gateway.
        checks['unknown_host_rejected'] = request('/', extra={'Host': 'untrusted.invalid'})[0] == 421
    checks['cross_site_rejected'] = request('/api/analyze', data={}, extra={'Origin': 'https://untrusted.invalid'})[0] == 403
    checks['token_required'] = request('/api/analyze', data={})[0] == 403
    checks['ai_generation_unavailable'] = request('/api/analyze', data={'demo': True}, extra={'X-App-Token': health.get('token', ''), 'Origin': 'https://' + settings['PUBLIC_HOST']})[0] == 503
    for name, passed in checks.items():
        print(('PASS ' if passed else 'FAIL ') + name)
    return 0 if all(checks.values()) else 1


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--public', action='store_true', help='Check the configured HTTPS hostname, through its public ingress.')
    args = parser.parse_args()
    try:
        raise SystemExit(main(args.public))
    except Exception as exc:
        print('Smoke checks stopped: ' + type(exc).__name__ + '; private diagnostics suppressed.')
        raise SystemExit(1)
