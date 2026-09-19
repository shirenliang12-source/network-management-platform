import contextlib
import io
import unittest
from types import SimpleNamespace
from unittest.mock import patch, MagicMock
import test_security
import run


class HttpsStartupTests(unittest.TestCase):
    def test_https_passes_certificates_to_server(self):
        settings = SimpleNamespace(HOST='127.0.0.1', PORT=9632, APP_VERSION='test',
                                   SSL_CERTFILE='certificate.pem', SSL_KEYFILE='private.pem')
        with patch.object(run, 'settings', settings, create=True), \
             patch.object(run, 'app', object(), create=True), \
             patch('sys.argv', ['run.py']), patch('ssl.SSLContext') as context, \
             patch('uvicorn.run') as server, contextlib.redirect_stdout(io.StringIO()) as output:
            run.main()
            context.return_value.load_cert_chain.assert_called_once_with('certificate.pem', 'private.pem')
            self.assertEqual(server.call_args.kwargs['ssl_certfile'], 'certificate.pem')
            self.assertEqual(server.call_args.kwargs['ssl_keyfile'], 'private.pem')
            self.assertIn('https://127.0.0.1:9632', output.getvalue())

    def test_bad_certificate_does_not_fall_back_to_http(self):
        settings = SimpleNamespace(HOST='127.0.0.1', PORT=9632,
                                   SSL_CERTFILE='missing.pem', SSL_KEYFILE='missing.key')
        with patch.object(run, 'settings', settings, create=True), \
             patch('sys.argv', ['run.py']), patch('uvicorn.run') as server:
            with self.assertRaises(OSError):
                run.main()
            server.assert_not_called()
