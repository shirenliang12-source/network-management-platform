"""Atomic TLS configuration; independent of database/installer configuration."""
import json
import os
from pathlib import Path
import ssl
import uuid
from datetime import datetime, timezone
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization


def configured_paths(data_dir):
    root = Path(data_dir) / 'tls'
    active = root / 'active.json'
    if not active.exists():
        return {}
    value = json.loads(active.read_text(encoding='utf-8'))
    identifier = value.get('id', '')
    if not isinstance(identifier, str) or len(identifier) != 32 or any(c not in '0123456789abcdef' for c in identifier):
        raise ValueError('Invalid TLS configuration')
    directory = root / identifier
    cert, key = directory / 'certificate.pem', directory / 'private.key'
    if not cert.is_file() or not key.is_file():
        raise ValueError('Configured TLS certificate files are missing')
    return {'SSL_CERTFILE': str(cert.resolve()), 'SSL_KEYFILE': str(key.resolve())}


def protect_directory(directory):
    directory.mkdir(mode=0o700, parents=True, exist_ok=False)
    if os.name != 'nt':
        directory.chmod(0o700)
        return
    import csv
    import subprocess
    flags = subprocess.CREATE_NO_WINDOW
    identity = subprocess.run(['whoami', '/user', '/fo', 'csv', '/nh'],
                              check=True, capture_output=True, text=True, creationflags=flags)
    sid = next(csv.reader([identity.stdout.strip()]))[-1]
    if not sid.startswith('S-1-'):
        raise ValueError('Cannot resolve service account')
    subprocess.run(['icacls', str(directory), '/inheritance:r', '/grant:r',
                    '*S-1-5-18:(OI)(CI)F', '*S-1-5-32-544:(OI)(CI)F', f'*{sid}:(OI)(CI)F'],
                   check=True, capture_output=True, creationflags=flags)


def import_certificate(data_dir, certificate_pem, private_key_pem, password=None):
    if len(certificate_pem.encode()) > 65536 or len(private_key_pem.encode()) > 32768:
        raise ValueError('证书或私钥超过大小限制')
    try:
        certificates = x509.load_pem_x509_certificates(certificate_pem.encode())
        leaf = certificates[0]
        key = serialization.load_pem_private_key(private_key_pem.encode(), password=password.encode() if password else None)
        def public_bytes(public):
            return public.public_bytes(serialization.Encoding.DER, serialization.PublicFormat.SubjectPublicKeyInfo)
        if public_bytes(leaf.public_key()) != public_bytes(key.public_key()):
            raise ValueError('mismatch')
        now = datetime.now(timezone.utc)
        if not leaf.not_valid_before_utc <= now < leaf.not_valid_after_utc:
            raise ValueError('invalid dates')
        try:
            usage = leaf.extensions.get_extension_for_class(x509.ExtendedKeyUsage).value
            if x509.oid.ExtendedKeyUsageOID.SERVER_AUTH not in usage:
                raise ValueError('not a server certificate')
        except x509.ExtensionNotFound:
            pass
    except Exception as exc:
        raise ValueError('证书或私钥无效、不匹配、密码错误、已过期或不适用于服务器') from exc
    root = Path(data_dir) / 'tls'
    root.mkdir(parents=True, exist_ok=True)
    identifier = uuid.uuid4().hex
    directory = root / identifier
    protect_directory(directory)
    cert_file, key_file = directory / 'certificate.pem', directory / 'private.key'
    private_bytes = key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption())
    for path, content in ((cert_file, certificate_pem.encode()), (key_file, private_bytes)):
        descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(descriptor, 'wb') as handle:
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
    # OpenSSL also validates chain/key compatibility before activation.
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    context.load_cert_chain(str(cert_file), str(key_file))
    metadata = {'id': identifier, 'expires': leaf.not_valid_after_utc.isoformat(),
                'subject': leaf.subject.rfc4514_string(), 'sha256': leaf.fingerprint(hashes.SHA256()).hex()}
    temporary = root / (identifier + '.tmp')
    try:
        with temporary.open('x', encoding='utf-8') as handle:
            json.dump(metadata, handle, ensure_ascii=False)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, root / 'active.json')
    finally:
        temporary.unlink(missing_ok=True)
    return {k: v for k, v in metadata.items() if k != 'id'}
