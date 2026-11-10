"""Public endpoint configuration and same-origin attachment authentication."""
import io
import unittest
from unittest.mock import Mock
from server import normalize_site
from image_sync import AttachmentClient


class ServerTests(unittest.TestCase):
    def test_context_port_and_case_normalization(self):
        self.assertEqual(normalize_site(' https://WIKI.example.com:443/wiki/ '),
                         'https://wiki.example.com/wiki')
        self.assertEqual(normalize_site('https://wiki.example.com:8443/wiki'),
                         'https://wiki.example.com:8443/wiki')

    def test_invalid_endpoints_rejected(self):
        for value in ('http://wiki.example.com', 'https://u:p@wiki.example.com',
                      'https://wiki.example.com?token=x', 'https://wiki.example.com/#x',
                      'https://wiki.example.com/a/../wiki', 'https://wiki.example.com\\wiki'):
            with self.subTest(value=value), self.assertRaises(ValueError):
                normalize_site(value)

    def client(self):
        client = AttachmentClient()
        client.base = 'https://wiki.example.com:8443/wiki'
        client.auth = 'Basic synthetic-test-credential'
        client.opener = Mock()
        client.opener.open.return_value = io.BytesIO(b'{}')
        return client

    def test_context_path_added_once(self):
        for path in ('/rest/api/content', '/wiki/rest/api/content'):
            client = self.client()
            client._image_http(path)
            request = client.opener.open.call_args.args[0]
            self.assertEqual(request.full_url, 'https://wiki.example.com:8443/wiki/rest/api/content')
            self.assertEqual(request.get_header('Authorization'), client.auth)

    def test_credentials_never_sent_to_other_origin_or_userinfo(self):
        for url in ('https://other.example.com/x', 'http://wiki.example.com:8443/x',
                    'https://wiki.example.com/x', 'https://u:p@wiki.example.com:8443/x',
                    '//other.example.com/x'):
            client = self.client()
            with self.subTest(url=url), self.assertRaises(ValueError):
                client._image_http(url)
            client.opener.open.assert_not_called()
