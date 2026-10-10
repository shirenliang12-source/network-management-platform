"""Compile the real PG installer script with inert payloads; never install it."""
import os
from pathlib import Path
import re
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
COMPILER = ROOT/'_installer_cache'/'nsis312'/'makensis.exe'


@unittest.skipUnless(os.name == 'nt' and COMPILER.is_file(), 'NSIS compiler unavailable')
class PGInstallerCompileTests(unittest.TestCase):
    def test_pg_branch_compiles(self):
        with tempfile.TemporaryDirectory(prefix='netmgr-nsis-compile-') as tmp:
            root = Path(tmp); payload = root/'payload'; payload.mkdir()
            for name in ('CiscoNetworkManager.exe', 'README.txt', 'INSTALLATION-NOTES.txt',
                         'DEVICE-SUPPORT.txt', 'sample_devices.csv', 'start.bat',
                         'service_probe.ps1', 'stop_instance.ps1', 'nssm.exe'):
                (payload/name).write_bytes(b'compile-only inert fixture')
            pg = payload/'postgresql'/'pgsql'/'bin'; pg.mkdir(parents=True)
            (pg/'compile-only.txt').write_bytes(b'no PostgreSQL executable')
            source = (ROOT/'installer'/'install.nsi').read_text(encoding='utf-8')
            source = re.sub(r'^OutFile .*$', 'OutFile "compile-only.exe"', source, flags=re.M)
            script = root/'compile.nsi'; script.write_text('!define WITH_PG\n' + source, encoding='utf-8')
            env = dict(os.environ, NSISDIR=str(COMPILER.parent))
            result = subprocess.run([str(COMPILER), '/V2', str(script)], cwd=root,
                                    env=env, capture_output=True, timeout=60,
                                    creationflags=subprocess.CREATE_NO_WINDOW)
            self.assertEqual(result.returncode, 0, result.stdout.decode(errors='replace') + result.stderr.decode(errors='replace'))
            self.assertTrue((root/'compile-only.exe').is_file())
