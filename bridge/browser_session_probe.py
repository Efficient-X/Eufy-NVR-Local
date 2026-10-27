"""Explicit temporary browser-session import; no credentials on argv or in logs."""
import hashlib
import hmac
import ipaddress
import json
import os
import sys
import urllib.parse
import urllib.request
from pathlib import Path


def validate_session(old, incoming):
    if not isinstance(incoming, dict):
        raise ValueError('invalid session')
    for key in ('token', 'gtoken', 'sn', 'region'):
        if not isinstance(incoming.get(key), str) or not incoming[key]:
            raise ValueError('missing session field')
    expected = hashlib.md5(old['userId'].encode()).hexdigest()
    if not hmac.compare_digest(expected, incoming['gtoken']):
        raise ValueError('account mismatch')
    if incoming['sn'] != old['stationSn'] or incoming['region'] != old['region']:
        raise ValueError('station mismatch' if incoming['sn'] != old['stationSn'] else 'region mismatch')
    return {**old, 'authToken': incoming['token'], 'gtoken': incoming['gtoken']}


def private_write(path, value):
    temp = path.with_suffix('.probe-tmp')
    fd = os.open(temp, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        with os.fdopen(fd, 'w') as stream:
            json.dump(value, stream)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temp, path)
        os.chmod(path, 0o600)
    finally:
        if temp.exists():
            temp.unlink()


def main():
    path = Path(os.environ['EUFY_AUTH'])
    original = path.with_suffix('.before-browser-probe')
    if sys.argv[1] == 'restore':
        if original.exists():
            os.replace(original, path)
            print('Temporary browser session removed; original auth restored.')
        return
    if original.exists():
        raise ValueError('original must be restored before another probe')
    url = os.environ['EUFY_BROWSER_SESSION_URL']
    parsed = urllib.parse.urlsplit(url)
    address = ipaddress.ip_address(parsed.hostname)
    if parsed.scheme != 'http' or address not in ipaddress.ip_network('100.64.0.0/10') or parsed.username or parsed.password:
        raise ValueError('probe source must be a tailnet IP')
    # No proxy or redirect: credentials must come only from the approved PC endpoint.
    class NoRedirect(urllib.request.HTTPRedirectHandler):
        def redirect_request(self, *args, **kwargs):
            return None
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), NoRedirect())
    with opener.open(url, timeout=15) as response:
        payload = response.read(8193)
    if len(payload) > 8192:
        raise ValueError('oversized session')
    old = json.loads(path.read_text())
    replacement = validate_session(old, json.loads(payload))
    private_write(original, old)
    private_write(path, replacement)
    print('Temporary browser session imported; account, station and region matched.')


if __name__ == '__main__':
    try:
        main()
    except Exception as exc:
        safe_reasons = {'invalid session', 'missing session field', 'account mismatch',
                        'station mismatch', 'region mismatch', 'oversized session',
                        'probe source must be a tailnet IP',
                        'original must be restored before another probe'}
        reason = str(exc) if type(exc) is ValueError and str(exc) in safe_reasons else type(exc).__name__
        print('Browser-session probe failed: ' + reason, file=sys.stderr)
        sys.exit(1)
