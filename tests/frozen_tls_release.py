"""Explicit isolated executable TLS smoke; no production service operations."""
from datetime import datetime, timedelta, timezone
import os
from pathlib import Path
import socket
import subprocess
import sys
import tempfile
import time
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.x509.oid import NameOID
from app.services.tls_config import import_certificate

binary=Path(sys.argv[1]).resolve()
with tempfile.TemporaryDirectory(prefix='netmgr-frozen-tls-') as temporary:
    root=Path(temporary)
    key=rsa.generate_private_key(public_exponent=65537,key_size=2048)
    subject=x509.Name([x509.NameAttribute(NameOID.COMMON_NAME,'localhost')])
    now=datetime.now(timezone.utc)
    cert=(x509.CertificateBuilder().subject_name(subject).issuer_name(subject)
          .public_key(key.public_key()).serial_number(x509.random_serial_number())
          .not_valid_before(now-timedelta(minutes=1)).not_valid_after(now+timedelta(days=1))
          .add_extension(x509.SubjectAlternativeName([x509.DNSName('localhost')]),critical=False)
          .sign(key,hashes.SHA256()))
    import_certificate(root,cert.public_bytes(serialization.Encoding.PEM).decode(),
                       key.private_bytes(serialization.Encoding.PEM,serialization.PrivateFormat.PKCS8,
                                         serialization.NoEncryption()).decode())
    before=(root/'tls/active.json').read_bytes()
    with socket.socket() as sock:
        sock.bind(('127.0.0.1',0)); port=sock.getsockname()[1]
    env={k:v for k,v in os.environ.items() if not k.startswith('NETMGR_') and k!='CISCO_NM_DATA_DIR'}
    env['NETMGR_PORT']=str(port)
    base=[str(binary),'--data-dir',str(root)]
    flags=getattr(subprocess,'CREATE_NO_WINDOW',0)
    for run in range(2):
        with (root/f'run-{run}.log').open('wb') as log:
            process=subprocess.Popen([*base,'--host','127.0.0.1'],env=env,stdout=log,stderr=log,creationflags=flags)
            try:
                for attempt in range(20):
                    probe=subprocess.run([*base,'--health-check','--expected-version','1.9.52.1'],env=env,
                                         capture_output=True,creationflags=flags,timeout=20)
                    if probe.returncode==0: break
                    if process.poll() is not None: raise RuntimeError('EXE exited before healthy')
                    time.sleep(1)
                else: raise RuntimeError('HTTPS health check timeout')
                wrong=subprocess.run([*base,'--health-check','--expected-version','wrong'],env=env,
                                     capture_output=True,creationflags=flags,timeout=20)
                assert wrong.returncode!=0
                assert (root/'tls/active.json').read_bytes()==before
                print('PASS HTTPS frozen startup, version gate, certificate retained:',run+1,flush=True)
            finally:
                if process.poll() is None:
                    subprocess.run(['taskkill','/PID',str(process.pid),'/T','/F'],capture_output=True,creationflags=flags)
                process.wait(timeout=15)
