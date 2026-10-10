"""Installation-owned settings and HTTPS maintenance, outside program files."""
import json
import os
import ssl
import uuid
from pathlib import Path

ALLOWED = {'DATABASE_URL', 'SSL_CERTFILE', 'SSL_KEYFILE', 'PG_BIN_DIR', 'PORT'}


def read_runtime(data_dir):
    path = Path(data_dir) / 'managed' / 'runtime.json'
    if not path.exists():
        return {}
    value = json.loads(path.read_text(encoding='utf-8'))
    if not isinstance(value, dict) or set(value) - ALLOWED:
        raise ValueError('Invalid managed runtime settings; refusing silent fallback')
    return value


def protect_directory(path):
    """Restrict newly created managed data to admins, SYSTEM and the installer."""
    path = Path(path)
    path.mkdir(parents=True, exist_ok=True)
    if os.name != 'nt':
        path.chmod(0o700)
        return
    import csv
    import subprocess
    flags = subprocess.CREATE_NO_WINDOW
    result = subprocess.run(['whoami', '/user', '/fo', 'csv', '/nh'], check=True,
                            capture_output=True, text=True, creationflags=flags)
    sid = next(csv.reader([result.stdout.strip()]))[-1]
    if not sid.startswith('S-1-'):
        raise RuntimeError('Cannot resolve installer SID')
    subprocess.run(['icacls', str(path), '/inheritance:r', '/grant:r',
                    '*S-1-5-18:(OI)(CI)F', '*S-1-5-32-544:(OI)(CI)F',
                    f'*{sid}:(OI)(CI)F'], check=True, capture_output=True, creationflags=flags)


def write_runtime(data_dir, values):
    if set(values) - ALLOWED:
        raise ValueError('Unknown runtime settings')
    directory = Path(data_dir) / 'managed'
    protect_directory(directory)
    destination = directory / 'runtime.json'
    temporary = directory / (uuid.uuid4().hex + '.tmp')
    descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        with os.fdopen(descriptor, 'w', encoding='utf-8') as handle:
            json.dump(values, handle, ensure_ascii=False, indent=2)
            handle.flush(); os.fsync(handle.fileno())
        os.replace(temporary, destination)
    finally:
        if temporary.exists(): temporary.unlink()


def setup_https(data_dir, names=()):
    from cryptography import x509
    from cryptography.x509.oid import NameOID
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import rsa
    from datetime import datetime, timedelta, timezone
    import ipaddress
    import socket
    values = read_runtime(data_dir)
    if values.get('SSL_CERTFILE') or values.get('SSL_KEYFILE'):
        # Preserve certificate identity on upgrades, never silently regenerate.
        context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        context.load_cert_chain(values['SSL_CERTFILE'], values['SSL_KEYFILE'])
        cert = x509.load_pem_x509_certificate(Path(values['SSL_CERTFILE']).read_bytes())
        if cert.not_valid_after_utc <= datetime.now(timezone.utc):
            raise ValueError('HTTPS certificate expired; renew before upgrade')
        return values
    alternative_names = []
    for name in sorted(set(['localhost', '127.0.0.1', '::1', socket.gethostname(), *names])):
        try:
            alternative_names.append(x509.IPAddress(ipaddress.ip_address(name)))
        except ValueError:
            alternative_names.append(x509.DNSName(name.encode('idna').decode('ascii')))
    directory = Path(data_dir) / 'managed'
    protect_directory(directory)
    tls_dir = directory / ('tls-' + uuid.uuid4().hex)
    tls_dir.mkdir(mode=0o700)
    key = rsa.generate_private_key(public_exponent=65537, key_size=3072)
    subject = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, 'Network Management Platform')])
    now = datetime.now(timezone.utc)
    cert = (x509.CertificateBuilder().subject_name(subject).issuer_name(subject)
            .public_key(key.public_key()).serial_number(x509.random_serial_number())
            .not_valid_before(now - timedelta(minutes=5)).not_valid_after(now + timedelta(days=365))
            .add_extension(x509.SubjectAlternativeName(alternative_names), critical=False)
            .add_extension(x509.BasicConstraints(ca=False, path_length=None), critical=True)
            .sign(key, hashes.SHA256()))
    private = tls_dir / 'server.key'
    descriptor = os.open(private, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(descriptor, 'wb') as handle:
        handle.write(key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8,
                                       serialization.NoEncryption()))
    public = tls_dir / 'server.crt'
    public.write_bytes(cert.public_bytes(serialization.Encoding.PEM))
    values.update(SSL_CERTFILE=str(public.resolve()), SSL_KEYFILE=str(private.resolve()))
    write_runtime(data_dir, values)
    return values


def check_health(settings, expected_version):
    import urllib.request
    secure = bool(settings.SSL_CERTFILE)
    context = ssl.create_default_context(cafile=settings.SSL_CERTFILE) if secure else None
    scheme = 'https' if secure else 'http'
    handler = urllib.request.HTTPSHandler(context=context) if secure else urllib.request.HTTPHandler()
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), handler)
    with opener.open(f'{scheme}://127.0.0.1:{settings.PORT}/health', timeout=3) as response:
        result = json.loads(response.read(65536))
    if result.get('status') != 'ok' or result.get('version') != expected_version:
        raise ValueError('Health response version/status mismatch')
    return True
