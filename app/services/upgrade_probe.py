"""Loopback-only health probe with exact certificate pinning for HTTPS."""
import hashlib
import http.client
import json
from pathlib import Path
import ssl
from cryptography import x509
from cryptography.hazmat.primitives import serialization


def check_health(settings, expected):
    connection = None
    try:
        if settings.SSL_CERTFILE:
            certificate = x509.load_pem_x509_certificate(Path(settings.SSL_CERTFILE).read_bytes())
            expected_der = certificate.public_bytes(serialization.Encoding.DER)
            # Loopback probe pins the exact installed certificate, including self-signed
            # certificates without localhost SAN. No request is sent before pinning.
            context = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
            context.check_hostname = False
            context.verify_mode = ssl.CERT_NONE
            context.minimum_version = ssl.TLSVersion.TLSv1_2
            connection = http.client.HTTPSConnection('127.0.0.1', settings.PORT, context=context, timeout=3)
            connection.connect()
            actual = connection.sock.getpeercert(binary_form=True)
            if hashlib.sha256(actual).digest() != hashlib.sha256(expected_der).digest():
                return False
        else:
            connection = http.client.HTTPConnection('127.0.0.1', settings.PORT, timeout=3)
        connection.request('GET', '/health')
        response = connection.getresponse()
        body = response.read(16385)
        if response.status != 200 or len(body) > 16384:
            return False
        value = json.loads(body)
        return value.get('status') == 'ok' and value.get('version') == expected
    except Exception:
        return False
    finally:
        if connection:
            connection.close()
