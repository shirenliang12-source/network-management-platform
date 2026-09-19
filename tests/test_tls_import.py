import tempfile
import unittest
from pathlib import Path
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import patch
import test_security
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.x509.oid import NameOID
from fastapi import FastAPI
from fastapi.testclient import TestClient
from app.services import tls_config
from app.routers import tls_settings


class TLSImportTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.key = rsa.generate_private_key(public_exponent=65537, key_size=2048)

    def pair(self, expired=False):
        now = datetime.now(timezone.utc)
        subject = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, 'localhost')])
        cert = (x509.CertificateBuilder().subject_name(subject).issuer_name(subject)
                .public_key(self.key.public_key()).serial_number(x509.random_serial_number())
                .not_valid_before(now-timedelta(days=2))
                .not_valid_after(now+timedelta(days=-1 if expired else 30))
                .add_extension(x509.SubjectAlternativeName([x509.DNSName('localhost')]), critical=False)
                .sign(self.key, hashes.SHA256()))
        return cert.public_bytes(serialization.Encoding.PEM).decode(), self.key.private_bytes(
            serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption()).decode()

    def test_valid_and_failed_import_preserves_previous(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp)
            # ACL process is mocked; crypto, file persistence and OpenSSL are real.
            with patch.object(tls_config, 'protect_directory', side_effect=lambda p: p.mkdir(mode=0o700)):
                meta=tls_config.import_certificate(root, *self.pair())
                before=(root/'tls/active.json').read_bytes()
                self.assertTrue(tls_config.configured_paths(root)['SSL_CERTFILE'])
                self.assertNotIn('private', str(meta))
                for cert,key in (self.pair(True), (self.pair()[0], 'bad key')):
                    with self.assertRaises(ValueError): tls_config.import_certificate(root, cert, key)
                    self.assertEqual(before, (root/'tls/active.json').read_bytes())

    def test_reject_traversal(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp); (root/'tls').mkdir()
            (root/'tls/active.json').write_text('{"id":"../../elsewhere"}')
            with self.assertRaises(ValueError): tls_config.configured_paths(root)

    def test_permissions_transport_and_sanitized_errors(self):
        app=FastAPI(); app.include_router(tls_settings.router)
        user=SimpleNamespace(is_superuser=False)
        @app.middleware('http')
        async def identity(request, call_next):
            request.state.user=user
            return await call_next(request)
        client=TestClient(app)
        payload={'certificate_pem':'bad','private_key_pem':'PRIVATE_SENTINEL'}
        self.assertEqual(client.post('/api/settings/https/certificate',json=payload).status_code,403)
        user.is_superuser=True
        self.assertEqual(client.post('/api/settings/https/certificate',json=payload).status_code,400)
        secure=TestClient(app,base_url='https://testserver')
        with patch.object(tls_settings,'TLS_EXTERNAL_CONFIGURED',False):
            result=secure.post('/api/settings/https/certificate',json=payload)
            self.assertEqual(result.status_code,400)
            self.assertNotIn('PRIVATE_SENTINEL',result.text)
        with patch.object(tls_settings,'TLS_EXTERNAL_CONFIGURED',True):
            self.assertEqual(secure.post('/api/settings/https/certificate',json=payload).status_code,409)
