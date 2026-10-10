import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
import test_security
from app.config import settings
from app.services.builtin_postgres import install_builtin


@unittest.skipUnless(os.name == 'nt', 'Windows provisioning guards')
class BuiltinPGGuards(unittest.TestCase):
    def test_explicit_database_environment_is_not_silently_replaced(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory); binaries = root/'bin'; binaries.mkdir()
            for name in ('initdb.exe', 'postgres.exe', 'pg_ctl.exe', 'pg_dump.exe'): (binaries/name).touch()
            with patch.dict(os.environ, {'PROGRAMDATA': directory, 'NETMGR_DATABASE_URL': 'sqlite:///retained.db'}), \
                 patch.object(settings, 'DATABASE_URL', 'sqlite:///retained.db'), \
                 patch('app.services.builtin_postgres._command') as command:
                with self.assertRaisesRegex(ValueError, 'Explicit service'):
                    install_builtin(root/'app', binaries, root/'CiscoNetworkManager'/'postgresql', 'test')
                command.assert_not_called()

    def test_cluster_outside_managed_root_is_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            with patch('app.services.builtin_postgres._command') as command:
                with self.assertRaisesRegex(ValueError, 'ProgramData'):
                    install_builtin(directory, directory, directory, 'test')
                command.assert_not_called()
