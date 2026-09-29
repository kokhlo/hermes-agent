"""TLS trust contract: the OS certificate store, with explicit config on top.

These are behavior contracts, not snapshots — they assert WHERE trust comes
from and that explicit per-provider settings still beat it.
"""

import pytest

from agent.ssl_verify import resolve_httpx_verify


@pytest.fixture
def no_ca_env(monkeypatch):
    """An operator's SSL_CERT_FILE flips resolve_httpx_verify() from
    ``True`` to a shared platform context. Pin the env for this fallback
    assertion rather than inheriting a developer shell's values."""
    monkeypatch.delenv("SSL_CERT_FILE", raising=False)
    monkeypatch.delenv("SSL_CERT_DIR", raising=False)


def test_missing_explicit_bundle_falls_back_to_the_platform_store(tmp_path, caplog, no_ca_env):
    missing = str(tmp_path / "nope.pem")

    assert resolve_httpx_verify(ca_bundle=missing) is True
    assert "does not exist" in caplog.text


def test_missing_bundle_under_a_cert_env_var_shares_the_platform_context(tmp_path, monkeypatch):
    """With SSL_CERT_FILE exported the platform store is handed over as one
    shared context (httpx would otherwise read the env var itself); a missing
    bundle must land on that same object, not a second pool."""
    import certifi

    monkeypatch.setenv("SSL_CERT_FILE", certifi.where())
    platform = resolve_httpx_verify()
    assert platform is not True
    assert resolve_httpx_verify(ca_bundle=str(tmp_path / "nope.pem")) is platform


@pytest.mark.parametrize("value", [False, "false", "0", "no", "off", "FALSE"])
def test_insecure_disables_verification(value):
    assert resolve_httpx_verify(ssl_verify=value) is False


def test_insecure_beats_an_explicit_bundle():
    import certifi

    assert resolve_httpx_verify(ca_bundle=certifi.where(), ssl_verify=False) is False


def test_truststore_failure_degrades_to_openssl_defaults():
    import subprocess
    import sys

    child = subprocess.run([sys.executable, "-c", """
import builtins, ssl
original = ssl.SSLContext
real_import = builtins.__import__
def no_truststore(name, *args, **kwargs):
    if name == 'truststore':
        raise ImportError('unavailable fixture')
    return real_import(name, *args, **kwargs)
builtins.__import__ = no_truststore
from agent.ssl_verify import install_truststore, resolve_httpx_verify
assert install_truststore() is False
assert install_truststore() is False
assert ssl.SSLContext is original
import httpx
with httpx.Client(verify=resolve_httpx_verify()) as client:
    ctx = client._transport._pool._ssl_context
    assert ctx.verify_mode == ssl.CERT_REQUIRED and ctx.check_hostname
"""], capture_output=True, text=True, timeout=30)
    assert child.returncode == 0, child.stderr
    assert "truststore unavailable" in child.stderr


def test_explicit_provider_ca_replaces_platform_trust_on_real_https(tmp_path):
    """A private endpoint trusts only its provider CA, never a global fallback."""
    from datetime import datetime, timedelta, timezone
    from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
    from ipaddress import ip_address
    import os
    import ssl
    import subprocess
    import sys
    import threading

    from cryptography import x509
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import rsa
    from cryptography.x509.oid import NameOID

    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    subject = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "Private provider")])
    now = datetime.now(timezone.utc)
    cert = (x509.CertificateBuilder().subject_name(subject).issuer_name(subject)
            .public_key(key.public_key()).serial_number(x509.random_serial_number())
            .not_valid_before(now - timedelta(days=1)).not_valid_after(now + timedelta(days=1))
            .add_extension(x509.BasicConstraints(ca=True, path_length=None), critical=True)
            .add_extension(x509.SubjectAlternativeName([x509.IPAddress(ip_address("127.0.0.1"))]), critical=False)
            .sign(key, hashes.SHA256()))
    ca = tmp_path / "provider-ca.pem"
    private = tmp_path / "provider.key"
    ca.write_bytes(cert.public_bytes(serialization.Encoding.PEM))
    private.write_bytes(key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8,
                                          serialization.NoEncryption()))

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            self.send_response(200)
            self.end_headers()
            self.wfile.write(b"private provider")

        def log_message(self, format, *args):
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    # Earlier tests may have injected truststore; its wrapper is client-only.
    from agent.ssl_verify import _stdlib_ssl_context_class
    context = _stdlib_ssl_context_class()(ssl.PROTOCOL_TLS_SERVER)
    context.load_cert_chain(ca, private)
    server.socket = context.wrap_socket(server.socket, server_side=True)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    home = tmp_path / "home"
    home.mkdir()
    env = os.environ.copy()
    env.update(HOME=str(tmp_path), HERMES_HOME=str(home), NO_PROXY="127.0.0.1")
    for key in ("SSL_CERT_FILE", "SSL_CERT_DIR", "REQUESTS_CA_BUNDLE", "CURL_CA_BUNDLE"):
        env.pop(key, None)
    script = """
import httpx, sys
from agent.ssl_verify import resolve_httpx_verify
url, ca = sys.argv[1:]
try:
    httpx.get(url, verify=resolve_httpx_verify(), timeout=5)
except httpx.ConnectError:
    pass
else:
    raise AssertionError('unconfigured platform store trusted private provider')
response = httpx.get(url, verify=resolve_httpx_verify(ca_bundle=ca), timeout=5)
assert response.content == b'private provider', response
"""
    try:
        child = subprocess.run(
            [sys.executable, "-c", script, f"https://127.0.0.1:{server.server_port}/", str(ca)],
            env=env, capture_output=True, text=True, timeout=30, check=False,
        )
        assert child.returncode == 0, child.stdout + child.stderr
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


def test_explicit_bundle_works_without_truststore():
    """A provider ``ssl_ca_cert`` on an interpreter without truststore
    (3.11–3.13 bridge installs) must still yield a verifying context built
    on that bundle, not an import error at client construction."""
    import subprocess
    import sys

    child = subprocess.run([sys.executable, "-c", """
import sys, ssl, certifi
sys.modules['truststore'] = None
from agent.ssl_verify import resolve_httpx_verify
ctx = resolve_httpx_verify(ca_bundle=certifi.where())
assert isinstance(ctx, ssl.SSLContext), ctx
assert ctx.verify_mode == ssl.CERT_REQUIRED and ctx.check_hostname
assert ctx.cert_store_stats()['x509_ca'] > 0
"""], capture_output=True, text=True, timeout=30)
    assert child.returncode == 0, child.stderr


def test_injected_context_keeps_stdlib_introspection():
    """truststore 0.10.x answers TLS from a private inner context and ships
    cert_store_stats()/get_ca_certs() as raising stubs; once install_truststore()
    rebinds ssl.SSLContext, libraries probing a context must still get
    stdlib-shaped answers instead of an empty NotImplementedError."""
    import subprocess
    import sys

    child = subprocess.run([sys.executable, "-c", """
import ssl
from agent.ssl_verify import install_truststore

assert install_truststore() is True
for ctx in (ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT), ssl.create_default_context()):
    stats = ctx.cert_store_stats()
    assert set(stats) == {"x509", "crl", "x509_ca"}, stats
    assert isinstance(ctx.get_ca_certs(), list)
    assert isinstance(ctx.get_ca_certs(binary_form=True), list)
"""], capture_output=True, text=True, timeout=30)
    assert child.returncode == 0, child.stderr


def test_ca_probing_library_survives_injection():
    """The crash class this guards against: a library probes the default
    context's CA store to decide whether to load its own bundle; the probe
    must answer instead of dying on an opaque empty-message failure."""
    import subprocess
    import sys

    child = subprocess.run([sys.executable, "-c", """
import ssl
from agent.ssl_verify import install_truststore


def ca_source_like_a_probing_library() -> str:
    ctx = ssl.create_default_context()
    try:
        stats = ctx.cert_store_stats()
    except NotImplementedError as exc:
        raise RuntimeError("Failed to initialize CA loading: " + str(exc)) from exc
    if stats.get("x509_ca", 0):
        return "platform store"
    return "certifi fallback"


assert install_truststore() is True
assert ca_source_like_a_probing_library() in ("platform store", "certifi fallback")
"""], capture_output=True, text=True, timeout=30)
    assert child.returncode == 0, child.stderr


def test_broken_delegation_probe_rolls_back_to_stdlib():
    """If a future truststore build changes shape and the delegation probe
    fails, the install degrades to stdlib defaults instead of shipping a
    poisoned context: ssl.SSLContext is the real class again and truststore's
    own class is restored exactly as it shipped."""
    import subprocess
    import sys

    child = subprocess.run([sys.executable, "-c", """
import ssl
import truststore

stdlib_context = ssl.SSLContext
real_init = truststore.SSLContext.__init__


class BrokenInner:
    def cert_store_stats(self):
        raise RuntimeError("truststore shape changed")

    def get_ca_certs(self, binary_form=False):
        raise RuntimeError("truststore shape changed")


def sabotaged_init(self, protocol=None):
    real_init(self, protocol)
    self._ctx = BrokenInner()


truststore.SSLContext.__init__ = sabotaged_init
from agent.ssl_verify import install_truststore

assert install_truststore() is False
assert ssl.SSLContext is stdlib_context
try:
    truststore.SSLContext(ssl.PROTOCOL_TLS_CLIENT).cert_store_stats()
except NotImplementedError:
    pass
else:
    raise AssertionError("truststore stubs were not restored")
stats = ssl.create_default_context().cert_store_stats()
assert set(stats) == {"x509", "crl", "x509_ca"}, stats
"""], capture_output=True, text=True, timeout=30)
    assert child.returncode == 0, child.stderr
