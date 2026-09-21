import json
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
import ssl
import tempfile
import threading
from types import SimpleNamespace
import unittest
from unittest.mock import patch
import test_tls_import as tls_fixture
from app.services.upgrade_probe import check_health


class UpgradeReleaseTests(unittest.TestCase):
    def probe_server(self, tls=False):
        class Handler(BaseHTTPRequestHandler):
            def do_GET(self):
                self.send_response(200); self.end_headers()
                self.wfile.write(json.dumps({'status':'ok','version':'1.9.52.1'}).encode())
            def log_message(self, *args): pass
        with tempfile.TemporaryDirectory() as tmp:
            server=ThreadingHTTPServer(('127.0.0.1',0), Handler)
            certificate=''
            if tls:
                tls_fixture.TLSImportTests.setUpClass()
                cert,key=tls_fixture.TLSImportTests().pair()
                certificate=str(Path(tmp)/'cert.pem'); keypath=Path(tmp)/'key.pem'
                Path(certificate).write_text(cert); keypath.write_text(key)
                context=ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
                context.load_cert_chain(certificate,str(keypath))
                server.socket=context.wrap_socket(server.socket,server_side=True)
            thread=threading.Thread(target=server.serve_forever,daemon=True); thread.start()
            settings=SimpleNamespace(PORT=server.server_port,SSL_CERTFILE=certificate)
            try:
                self.assertTrue(check_health(settings,'1.9.52.1'))
                self.assertFalse(check_health(settings,'other-version'))
                if tls:
                    tls_fixture.TLSImportTests.setUpClass()
                    other,_=tls_fixture.TLSImportTests().pair()
                    Path(certificate).write_text(other)
                    self.assertFalse(check_health(settings,'1.9.52.1'))
            finally:
                server.shutdown(); server.server_close(); thread.join(timeout=5)

    def test_http(self): self.probe_server()
    def test_https_pinning(self): self.probe_server(tls=True)

    def test_installer_guardrails(self):
        root=Path(__file__).resolve().parents[1]
        source=(root/'installer/install.nsi').read_text(encoding='utf-8')
        install=source.split('Section "Uninstall"')[0]
        self.assertNotIn('taskkill',install)
        self.assertNotIn('ExecutionPolicy',install)
        self.assertNotIn('remove ${SERVICE}',install)
        self.assertIn('--service-context --health-check --expected-version',install)
        self.assertEqual(install.count('!insertmacro RequireStopped'),3)
        self.assertIn('IfErrors previous_backup_failed previous_exe_saved',install)
        self.assertIn('Existing service parameters retained unchanged',install)
        self.assertIn('FileSeek $DiagnosticHandle 0 END',install)

    def test_service_context(self):
        from app.services.service_context import apply_service_context
        import os
        from unittest.mock import MagicMock
        values={'AppDirectory':r'D:\Old Program', 'AppParameters':r'--port 9633 --data-dir "D:\Old Data"'}
        def read(_, name):
            if name not in values: raise FileNotFoundError
            return values[name],1
        with patch('winreg.OpenKey',return_value=MagicMock()), patch('winreg.QueryValueEx',side_effect=read), patch.dict(os.environ,{},clear=True), patch('os.chdir') as chdir:
            apply_service_context()
            self.assertEqual(os.environ['CISCO_NM_DATA_DIR'],r'D:\Old Data')
            self.assertEqual(os.environ['NETMGR_PORT'],'9633')
            chdir.assert_called_once_with(r'D:\Old Program')
