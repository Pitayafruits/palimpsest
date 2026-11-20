import ssl
import unittest
import urllib.error
from unittest.mock import patch

from confluence_http import NoRedirect, VerifiedHTTPSHandler, create_opener
from login import verify


class ConfluenceHttpsTests(unittest.TestCase):
    def test_certificate_failure_is_reported_with_verification_enabled(self):
        opener = create_opener()
        handler = next(h for h in opener.handlers if isinstance(h, VerifiedHTTPSHandler))
        error = urllib.error.URLError(ssl.SSLCertVerificationError('unable to get local issuer certificate'))
        self.assertTrue(handler._context.check_hostname)
        self.assertEqual(handler._context.verify_mode, ssl.CERT_REQUIRED)
        with patch.object(handler, 'do_open', side_effect=error) as open_request:
            with patch('login.create_opener', return_value=opener):
                with self.assertRaisesRegex(RuntimeError, '无法验证 Confluence 的 HTTPS 证书'):
                    verify('user', 'secret')
        open_request.assert_called_once()

    def test_redirect_refused(self):
        with self.assertRaises(RuntimeError):
            NoRedirect().redirect_request(None, None, 302, '', {}, 'https://other.test')
