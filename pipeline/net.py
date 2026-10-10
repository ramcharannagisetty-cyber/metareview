"""HTTPS GET with certificate fallbacks: Python's bundle, then the OS bundle, then curl (uses the OS keychain)."""
import os
import ssl
import subprocess
import urllib.request

_ctx = [None]
CANDIDATES = [os.environ.get('SSL_CERT_FILE'), os.environ.get('MR_CA_BUNDLE'), '/etc/ssl/cert.pem', '/etc/ssl/certs/ca-certificates.crt']


def _contexts():
    if _ctx[0] is not None:
        return [_ctx[0]]
    out = [ssl.create_default_context()]
    for path in CANDIDATES:
        if path and os.path.isfile(path):
            try:
                out.append(ssl.create_default_context(cafile=path))
            except ssl.SSLError:
                pass
    return out


def get(url, timeout=60, headers=None):
    """Return response bytes or raise the last error."""
    req = urllib.request.Request(url, headers={'User-Agent': 'metareview/0.1', **(headers or {})})
    last = None
    for ctx in _contexts():
        try:
            with urllib.request.urlopen(req, timeout=timeout, context=ctx) as r:
                _ctx[0] = ctx  # remember what worked
                return r.read()
        except ssl.SSLError as exc:
            last = exc
        except urllib.error.URLError as exc:
            if 'CERTIFICATE_VERIFY_FAILED' in str(exc):
                last = exc
                continue
            raise
    try:
        proc = subprocess.run(['curl', '-sSL', '--max-time', str(timeout), '-A', 'metareview/0.1', url], capture_output=True, timeout=timeout + 5)
        if proc.returncode == 0 and proc.stdout:
            return proc.stdout
    except (OSError, subprocess.TimeoutExpired):
        pass
    raise last or OSError(f'request failed: {url}')
