import json
import ssl
import tempfile
import threading
import unittest
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from types import SimpleNamespace
from app.services.managed_runtime import read_runtime, write_runtime, setup_https, check_health


class ManagedRuntimeTests(unittest.TestCase):
    def test_live_https_and_preserved_certificate(self):
        with tempfile.TemporaryDirectory() as directory:
            values = setup_https(directory, ['192.0.2.10'])
            certificate = Path(values['SSL_CERTFILE']).read_bytes()
            self.assertEqual(setup_https(directory), values)
            self.assertEqual(Path(values['SSL_CERTFILE']).read_bytes(), certificate)
            class Handler(BaseHTTPRequestHandler):
                def do_GET(self):
                    self.send_response(200); self.end_headers()
                    self.wfile.write(json.dumps({'status':'ok', 'version':'test'}).encode())
                def log_message(self, *args): pass
            server = HTTPServer(('127.0.0.1', 0), Handler)
            context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
            context.load_cert_chain(values['SSL_CERTFILE'], values['SSL_KEYFILE'])
            server.socket = context.wrap_socket(server.socket, server_side=True)
            thread = threading.Thread(target=server.serve_forever, daemon=True); thread.start()
            try:
                settings = SimpleNamespace(SSL_CERTFILE=values['SSL_CERTFILE'], PORT=server.server_port)
                self.assertTrue(check_health(settings, 'test'))
                with self.assertRaises(ValueError): check_health(settings, 'wrong')
            finally:
                server.shutdown(); server.server_close(); thread.join()

    def test_settings_preserve_database_and_reject_unknown_fields(self):
        with tempfile.TemporaryDirectory() as directory:
            write_runtime(directory, {'DATABASE_URL':'sqlite:///retained.db'})
            values = setup_https(directory)
            self.assertEqual(values['DATABASE_URL'], 'sqlite:///retained.db')
            self.assertEqual(read_runtime(directory), values)
            with self.assertRaises(ValueError): write_runtime(directory, {'SECRET_KEY':'wrong'})

    def test_corrupt_configuration_never_falls_back(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory)/'managed'; path.mkdir()
            (path/'runtime.json').write_text('{', encoding='utf-8')
            with self.assertRaises(ValueError): read_runtime(directory)
