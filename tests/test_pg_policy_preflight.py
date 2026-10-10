"""Policy handling uses mocks only: never re-execute a policy-blocked binary."""
import os
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch
import test_security
from app.config import settings
from app.services import builtin_postgres as pg


class PGPolicyTests(unittest.TestCase):
    def test_policy_errors_are_safe_and_have_no_credentials(self):
        for code in (4551, 577, 1260):
            error = OSError('sensitive command output'); error.winerror = code
            with patch.object(pg.subprocess, 'run', side_effect=error):
                with self.assertRaises(pg.PGExecutionBlocked) as result:
                    pg._command(['initdb.exe', '--pwfile=secret-path'])
            self.assertIn(str(code), str(result.exception))
            self.assertNotIn('secret', str(result.exception))
            self.assertEqual(result.exception.exit_code, 1)

    def test_preflight_only_versions_and_stops_at_first_rejection(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            for name in pg.PG_EXECUTABLES: (root/name).touch()
            with patch.object(pg, '_command') as command:
                pg.preflight_binaries(root)
                self.assertEqual(len(command.call_args_list), 4)
                self.assertTrue(all(c.args[0][1:] == ['--version'] for c in command.call_args_list))
            with patch.object(pg, '_command', side_effect=pg.PGExecutionBlocked('blocked')) as command:
                with self.assertRaises(pg.PGPreflightBlocked) as result:
                    pg.preflight_binaries(root)
                self.assertEqual(result.exception.exit_code, 20)
                command.assert_called_once()

    def test_missing_dependency_and_timeout_are_not_policy_fallback(self):
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaises(pg.PGSetupError) as result:
                pg.preflight_binaries(Path(tmp))
            self.assertEqual(result.exception.exit_code, 1)
        with patch.object(pg.subprocess, 'run', side_effect=subprocess.TimeoutExpired(['secret'], 20)):
            with self.assertRaises(pg.PGSetupError) as result:
                pg._command(['postgres.exe', '--version'])
            self.assertNotIn('secret', str(result.exception))
            self.assertEqual(result.exception.exit_code, 1)

    def test_recovery_is_archived_not_deleted_or_logged(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp); recovery = root/'recovery.json'
            recovery.write_bytes(b'previous-private-credentials')
            pg._archive_uninitialized_recovery(root)
            self.assertFalse(recovery.exists())
            archives = list(root.glob('recovery-uninitialized-*.json'))
            self.assertEqual(len(archives), 1)
            self.assertEqual(archives[0].read_bytes(), b'previous-private-credentials')
            pg._archive_uninitialized_recovery(root)
            self.assertEqual(len(list(root.glob('recovery-uninitialized-*.json'))), 1)

    def test_even_empty_cluster_blocks_recovery_rotation(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp); (root/'15'/'data').mkdir(parents=True)
            (root/'recovery.json').write_bytes(b'keep')
            with self.assertRaises(pg.PGSetupError):
                pg._archive_uninitialized_recovery(root)
            self.assertEqual((root/'recovery.json').read_bytes(), b'keep')

    @unittest.skipUnless(os.name == 'nt', 'Windows provisioning guards')
    def test_block_before_passwords_acl_cluster_or_runtime_writes(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp); binaries = root/'bin'; binaries.mkdir()
            for name in pg.PG_EXECUTABLES: (binaries/name).touch()
            original = root/'source.db'; original.write_bytes(b'original SQLite sentinel')
            with patch.dict(os.environ, {'PROGRAMDATA': tmp, 'NETMGR_DATABASE_URL': ''}), \
                 patch.object(settings, 'DATABASE_URL', 'sqlite:///' + str(original)), \
                 patch('winreg.OpenKey', side_effect=FileNotFoundError), \
                 patch.object(pg, 'preflight_binaries', side_effect=pg.PGPreflightBlocked('blocked')), \
                 patch.object(pg, 'protect_directory') as protect, \
                 patch.object(pg, 'write_runtime') as write:
                with self.assertRaises(pg.PGPreflightBlocked):
                    pg.install_builtin(root/'app', binaries, root/'CiscoNetworkManager'/'postgresql', 'test')
                protect.assert_not_called(); write.assert_not_called()
                self.assertFalse((root/'CiscoNetworkManager').exists())
                self.assertFalse((root/'app').exists())
                self.assertEqual(original.read_bytes(), b'original SQLite sentinel')

    def test_installer_requires_consent_not_silent_fallback(self):
        root = Path(__file__).resolve().parents[1]
        installer = (root/'installer'/'install.nsi').read_text(encoding='utf-8')
        self.assertIn('$R1 == "20"', installer)
        self.assertIn('/SD IDNO IDYES pg_continue_sqlite', installer)
        runner = (root/'run.py').read_text(encoding='utf-8')
        self.assertIn('sys.exit(e.exit_code)', runner)
