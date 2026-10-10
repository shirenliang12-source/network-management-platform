"""Execute the production stop script with mocked Windows services/processes."""
import os
from pathlib import Path
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]


@unittest.skipUnless(os.name == 'nt', 'PowerShell service mocks')
class UpgradeStopTests(unittest.TestCase):
    def scenario(self, scenario):
        if os.environ.get('NETMGR_RUN_PS_SCRIPT_TEST') != '1':
            self.skipTest('Requires an administrator-approved PowerShell script test host')
        script = (ROOT / 'installer/stop_instance.ps1').read_text(encoding='utf-8')
        script = script.replace('Wait-Stopped 30', 'Wait-Stopped 0').replace('Wait-Stopped 10', 'Wait-Stopped 0').replace('Wait-Stopped 20', 'Wait-Stopped 0')
        mocks = r'''
$global:state = 'Stop Pending'
$global:scenario = 'SCENARIO'
function Start-Sleep { param($Milliseconds) }
function Get-CimInstance {
    param($ClassName, $Filter)
    if ($ClassName -eq 'Win32_Service') {
        if ($global:scenario -eq 'fresh') { return }
        [pscustomobject]@{ State=$global:state; ProcessId=4321 }
    }
}
function Get-ItemProperty {
    param($LiteralPath)
    $app = 'C:\NetMgr\CiscoNetworkManager.exe'
    if ($global:scenario -eq 'wrong-app') { $app='C:\Other\CiscoNetworkManager.exe' }
    [pscustomobject]@{ Application=$app }
}
function Get-Process {
    param($Id)
    $path='C:\NetMgr\nssm.exe'
    if ($global:scenario -eq 'wrong-wrapper') { $path='C:\Other\nssm.exe' }
    $p=[pscustomobject]@{Id=$Id; Path=$path}
    $p | Add-Member ScriptMethod Kill {
        Write-Output 'MOCK_KILL'
        if ($global:scenario -ne 'still-pending') { $global:state='Stopped' }
    }
    return $p
}
'''.replace('SCENARIO', scenario)
        # Keep param as the first script statement, inject mocks afterwards.
        script = script.replace("$ErrorActionPreference = 'Stop'", "$ErrorActionPreference = 'Stop'\n" + mocks)
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / 'mock_stop.ps1'
            path.write_text(script, encoding='utf-8-sig')
            return subprocess.run(['powershell.exe', '-NoProfile', '-NonInteractive', '-File', str(path), '-InstallDir', r'C:\NetMgr'], capture_output=True, text=True, timeout=15)

    def test_recovers_stuck_wrapper(self):
        result = self.scenario('stuck')
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn('verified STOP_PENDING', result.stdout)

    def test_wrong_paths_refuse_termination(self):
        for scenario in ('wrong-app', 'wrong-wrapper'):
            result = self.scenario(scenario)
            self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
            self.assertNotIn('MOCK_KILL', result.stdout)

    def test_failure_to_stop_is_failure(self):
        self.assertEqual(self.scenario('still-pending').returncode, 1)

    def test_fresh_install(self):
        self.assertEqual(self.scenario('fresh').returncode, 0)

    def test_install_and_rollback_are_gated(self):
        source = (ROOT / 'installer/install.nsi').read_text(encoding='utf-8')
        self.assertEqual(source.count('!insertmacro StopForUpgrade'), 3)
        self.assertLess(source.index('!insertmacro StopForUpgrade'), source.index('CopyFiles /SILENT "$INSTDIR\\CiscoNetworkManager.exe"'))
        self.assertIn('IfErrors previous_exe_failed previous_exe_saved', source)
