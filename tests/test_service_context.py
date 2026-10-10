import os
from pathlib import Path
import unittest
from unittest.mock import MagicMock, patch
from app.services.service_context import apply_service_context


@unittest.skipUnless(os.name == 'nt', 'Windows service context')
class ServiceContextTests(unittest.TestCase):
    def test_service_environment_parameters_and_workdir(self):
        values = {'AppEnvironment': ['NETMGR_PORT=9000', 'NETMGR_SECRET_KEY=private', 'UNRELATED=ignored'],
                  'AppEnvironmentExtra': ['NETMGR_PORT=9001', r'CISCO_NM_DATA_DIR=D:\Old Data'],
                  'AppParameters': r'--host 0.0.0.0 --port 9633 --data-dir "D:\Net Manager\data"',
                  'AppDirectory': r'D:\Net Manager'}
        def query(_, name):
            if name not in values: raise FileNotFoundError
            return values[name], 1
        with patch('winreg.OpenKey', return_value=MagicMock()), patch('winreg.QueryValueEx', side_effect=query), \
             patch.dict(os.environ, {}, clear=True), patch('os.chdir') as chdir:
            apply_service_context(['--health-check'])
            self.assertEqual(os.environ['NETMGR_PORT'], '9633')
            self.assertEqual(os.environ['CISCO_NM_DATA_DIR'], r'D:\Net Manager\data')
            self.assertNotIn('UNRELATED', os.environ)
            chdir.assert_called_once_with(r'D:\Net Manager')

    def test_missing_service_is_allowed_on_fresh_install(self):
        with patch('winreg.OpenKey', side_effect=FileNotFoundError), patch('os.chdir') as chdir:
            apply_service_context(['--setup-https'])
            chdir.assert_not_called()

    def test_explicit_new_data_directory_not_overwritten_by_old_parameters(self):
        with patch('winreg.OpenKey', return_value=MagicMock()), \
             patch('winreg.QueryValueEx', side_effect=lambda _, name: (r'--data-dir "D:\old"', 1) if name == 'AppParameters' else (None, 1)), \
             patch.dict(os.environ, {}, clear=True):
            apply_service_context(['--data-dir', r'D:\new', '--setup-https'])
            self.assertNotIn('CISCO_NM_DATA_DIR', os.environ)

    def test_health_has_no_ps1_launcher_and_failure_is_persisted(self):
        source = (Path(__file__).resolve().parents[1]/'installer'/'install.nsi').read_text(encoding='utf-8')
        loop = source.split('health_loop:')[1].split('health_ok:')[0]
        self.assertIn('--service-context --health-check', loop)
        self.assertNotIn('service_probe.ps1', loop)
        self.assertIn('upgrade-diagnostics.log', source)
        self.assertIn('FileSeek $DiagnosticHandle 0 END', source)
        self.assertIn('Service start exit=$R1', source)
        self.assertIn('Rollback restart exit=$R1', source)
