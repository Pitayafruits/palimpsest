"""Mock HTTP and Security.framework ABI checks; never touch the real keychain."""
import ctypes as C
import io
import json
import unittest
import urllib.error
from unittest.mock import Mock, patch
from login import verify, KeychainStore
import sync_engine as engine

class LoginHttpTests(unittest.TestCase):
    def response(self, payload):
        return io.BytesIO(json.dumps(payload).encode())


    def test_anonymous_and_other_account_rejected(self):
        for payload in ({'type': 'anonymous'}, {'type': 'known', 'username': 'other'}):
            opener = Mock()
            opener.open.return_value = self.response(payload)
            with patch('login.urllib.request.build_opener', return_value=opener):
                with self.assertRaises(ValueError): verify('alice_dev', 'secret')

    def test_network_failure_does_not_look_like_success(self):
        opener = Mock()
        opener.open.side_effect = urllib.error.URLError('offline')
        with patch('login.urllib.request.build_opener', return_value=opener):
            with self.assertRaises(urllib.error.URLError): verify('user', 'secret')

    def test_401_and_403_are_distinct_for_sync(self):
        for code in (401, 403):
            client = engine.Client({'base_url': 'https://wiki.example.com', 'page_id': '123'}, 'u', 'p')
            client.opener.open = Mock(side_effect=urllib.error.HTTPError('https://example.test', code, 'error', {}, None))
            expected = engine.AuthenticationExpired if code == 401 else RuntimeError
            with self.assertRaises(expected) as context: client.get()
            if code == 403: self.assertNotIsInstance(context.exception, engine.AuthenticationExpired)

    def test_redirect_refused(self):
        with self.assertRaises(RuntimeError):
            engine.NoRedirect().redirect_request(None, None, 302, '', {}, 'https://other.test')

class NativeFunction:
    def __init__(self, implementation): self.implementation = implementation
    def __call__(self, *args): return self.implementation(*args)

class FakeSecurity:
    def __init__(self):
        self.raw = None
        self.denied = False
        self.freed = self.released = 0
        self.buffer = None
        functions = {
            'SecKeychainFindGenericPassword': self.find,
            'SecKeychainAddGenericPassword': self.add,
            'SecKeychainItemModifyAttributesAndData': self.modify,
            'SecKeychainItemDelete': self.delete,
            'SecKeychainItemFreeContent': self.free,
            'CFRelease': self.release,
        }
        for name, implementation in functions.items(): setattr(self, name, NativeFunction(implementation))

    def find(self, keychain, slen, service, alen, account, length, data, item):
        assert slen == len(service) and alen == len(account)
        if self.denied: return -25293
        if self.raw is None: return -25300
        C.cast(item, C.POINTER(C.c_void_p))[0] = C.c_void_p(123)
        if data is not None:
            self.buffer = C.create_string_buffer(self.raw)
            C.cast(length, C.POINTER(C.c_uint32))[0] = len(self.raw)
            C.cast(data, C.POINTER(C.c_void_p))[0] = C.cast(self.buffer, C.c_void_p)
        return 0

    def add(self, keychain, slen, service, alen, account, length, data, item):
        self.raw = C.string_at(data, length)
        return 0

    def modify(self, item, attributes, length, data):
        assert item.value == 123
        self.raw = C.string_at(data, length)
        return 0

    def delete(self, item):
        self.raw = None
        return 0

    def free(self, attributes, data):
        self.freed += 1
        return 0

    def release(self, item): self.released += 1

class KeychainTests(unittest.TestCase):
    def setUp(self):
        self.native = FakeSecurity()
        with patch('login.sys.platform', 'darwin'), patch('login.C.CDLL', return_value=self.native):
            self.store = KeychainStore()

    def test_create_read_update_delete_and_buffers_released(self):
        self.assertIsNone(self.store.load())
        self.store.save('alice_dev', '密码_one')
        self.assertEqual(self.store.load(), ('alice_dev', '密码_one'))
        self.store.save('new_user', 'two')
        self.assertEqual(self.store.load(), ('new_user', 'two'))
        self.store.clear()
        self.assertIsNone(self.store.load())
        self.store.clear()
        self.assertEqual(self.native.freed, 2)
        self.assertEqual(self.native.released, 4)

    def test_corrupt_entry_releases_item(self):
        self.native.raw = b'broken'
        with self.assertRaises(ValueError): self.store.load()
        self.assertEqual(self.native.freed, 1)
        self.assertEqual(self.native.released, 1)

    def test_bad_structure_is_rejected(self):
        self.native.raw = b'{"username":"u", "password":7}'
        with self.assertRaises(ValueError): self.store.load()

    def test_denied_keychain_never_uses_plaintext_fallback(self):
        self.native.denied = True
        with self.assertRaises(RuntimeError): self.store.save('u', 'p')
        self.assertIsNone(self.native.raw)

if __name__ == '__main__':
    unittest.main(verbosity=2)
