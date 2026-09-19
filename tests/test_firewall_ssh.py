import unittest
from types import SimpleNamespace
from unittest.mock import MagicMock, patch
import test_security
from netmiko import NetmikoAuthenticationException
from app.services.ssh_service import SSHService
from app.services.command_config import DEFAULT_COMMANDS, resolve_device_driver


class FirewallSSHTests(unittest.TestCase):
    def device(self, driver):
        return SimpleNamespace(device_type=driver, ip_address='192.0.2.10', port=22,
            username='admin', source_ip=None, status='online', get_password=lambda:'login-secret',
            get_enable_password=lambda:'enable-secret')

    def test_firewall_sessions(self):
        for driver in ['cisco_asa', 'cisco_ftd']:
            conn = MagicMock()
            with patch('app.services.ssh_service.ConnectHandler', return_value=conn) as factory:
                self.assertTrue(SSHService(self.device(driver)).connect())
                self.assertFalse(factory.call_args.kwargs['allow_auto_change'])
                self.assertEqual(factory.call_args.kwargs['secret'], '' if driver == 'cisco_ftd' else 'enable-secret')
                conn.enable.assert_called_once()
                conn.asa_login.assert_not_called()
                conn.send_config_set.assert_not_called()

    def test_failure_preserves_status_and_redacts_secret(self):
        for method, stage in [('establish_connection', 'authentication'), ('enable', 'privilege')]:
            conn = MagicMock(); getattr(conn, method).side_effect = NetmikoAuthenticationException('login-secret')
            device = self.device('cisco_asa')
            with patch('app.services.ssh_service.ConnectHandler', return_value=conn):
                ssh = SSHService(device)
                self.assertFalse(ssh.connect())
                self.assertEqual(ssh.error_stage, stage)
                self.assertNotIn('login-secret', ssh.last_error)
                self.assertEqual(device.status, 'online')
                conn.disconnect.assert_called_once()

    def test_catalog(self):
        self.assertEqual(resolve_device_driver('cisco_router'), 'cisco_ios')
        self.assertEqual(resolve_device_driver('cisco_wlc_xe'), 'cisco_xe')
        self.assertEqual(resolve_device_driver('checkpoint_gaia'), 'checkpoint_gaia')
        self.assertEqual(DEFAULT_COMMANDS['checkpoint_gaia']['running_config']['command'], 'show configuration')
