"""Real HTTPS checks on the packaged executable, isolated from installed app."""
import os
from pathlib import Path
import socket
import subprocess
import sys
import tempfile
import time
from app.services.managed_runtime import read_runtime, write_runtime

binary = str(Path(sys.argv[1]).resolve())
flags = getattr(subprocess, 'CREATE_NO_WINDOW', 0)
with tempfile.TemporaryDirectory(prefix='netmgr-https-smoke-') as scratch:
    root = Path(scratch)
    environment = {k:v for k,v in os.environ.items() if not k.startswith('NETMGR_') and k != 'CISCO_NM_DATA_DIR'}
    command = [binary, '--data-dir', scratch]
    for _ in range(2):
        subprocess.run([*command, '--setup-https'], check=True, capture_output=True, env=environment,
                       creationflags=flags, timeout=45)
    settings = read_runtime(root)
    before = Path(settings['SSL_CERTFILE']).read_bytes()
    with socket.socket() as probe:
        probe.bind(('127.0.0.1', 0)); port = probe.getsockname()[1]
    settings['PORT'] = port; write_runtime(root, settings)
    with (root/'runtime.log').open('wb') as log:
        process = subprocess.Popen([*command, '--host', '127.0.0.1'], stdout=log, stderr=log,
                                   env=environment, creationflags=flags)
        try:
            for _ in range(30):
                probe = subprocess.run([*command, '--health-check'], capture_output=True, env=environment,
                                       creationflags=flags, timeout=15)
                if probe.returncode == 0: break
                if process.poll() is not None: raise RuntimeError('HTTPS server exited')
                time.sleep(1)
            else: raise RuntimeError('HTTPS health check did not succeed')
            wrong = subprocess.run([*command, '--health-check', '--expected-version', 'wrong'],
                                   capture_output=True, env=environment, creationflags=flags, timeout=15)
            assert wrong.returncode != 0
            url = subprocess.check_output([*command, '--print-url'], env=environment, creationflags=flags, timeout=15).decode().strip()
            assert url == f'https://localhost:{port}'
            assert Path(settings['SSL_CERTFILE']).read_bytes() == before
            print('PASS frozen HTTPS startup, verified health, wrong-version refusal and HTTPS shortcut URL')
        finally:
            if os.name == 'nt':
                subprocess.run(['taskkill', '/PID', str(process.pid), '/T', '/F'],
                               capture_output=True, creationflags=flags, timeout=15)
            else: process.terminate()
            process.wait(timeout=15)
