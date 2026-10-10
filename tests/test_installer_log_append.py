"""Execute only a user-level log fixture, never the service installer."""
import os
from pathlib import Path
import re
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
COMPILER = ROOT/'_installer_cache'/'nsis312'/'makensis.exe'


@unittest.skipUnless(os.name == 'nt' and COMPILER.is_file() and os.environ.get('NETMGR_RUN_NSIS_EXEC_TEST') == '1',
                    'Opt-in on an approved test host; local App Control blocked the fixture, do not bypass')
class InstallerLogTests(unittest.TestCase):
    def test_repeated_writes_retain_all_diagnostics(self):
        source = (ROOT/'installer'/'install.nsi').read_text(encoding='utf-8')
        macro = re.search(r'!macro Diagnostic TEXT.*?!macroend', source, re.S).group()
        with tempfile.TemporaryDirectory(prefix='netmgr-log-fixture-') as tmp:
            directory = Path(tmp)
            script = directory/'fixture.nsi'
            script.write_text('Name "Log fixture"\nOutFile "fixture.exe"\nRequestExecutionLevel user\nSilentInstall silent\nVar DiagnosticHandle\n' + macro + '\nSection\nStrCpy $INSTDIR "$EXEDIR"\n!insertmacro Diagnostic "FIRST longer diagnostic record"\n!insertmacro Diagnostic "SECOND"\n!insertmacro Diagnostic "THIRD"\nSectionEnd\n', encoding='utf-8')
            env = dict(os.environ, NSISDIR=str(COMPILER.parent))
            result = subprocess.run([str(COMPILER), '/V2', str(script)], cwd=directory, env=env,
                                    capture_output=True, timeout=60, creationflags=subprocess.CREATE_NO_WINDOW)
            self.assertEqual(result.returncode, 0, result.stdout.decode(errors='replace'))
            subprocess.run([str(directory/'fixture.exe'), '/S'], cwd=directory, check=True,
                           timeout=30, creationflags=subprocess.CREATE_NO_WINDOW)
            lines = (directory/'upgrade-diagnostics.log').read_text().splitlines()
            self.assertEqual(lines, ['FIRST longer diagnostic record', 'SECOND', 'THIRD'])
