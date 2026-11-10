"""macOS Keychain credentials, with no plaintext fallback or shell arguments."""
import base64
import ctypes as C
import json
import sys
import urllib.error
import urllib.request

from associations import SITE
from sync_engine import NoRedirect


def verify(username, password):
    auth = base64.b64encode((username + ':' + password).encode()).decode()
    request = urllib.request.Request(SITE + '/rest/api/user/current', headers={
        'Authorization': 'Basic ' + auth, 'Accept': 'application/json'})
    try:
        with urllib.request.build_opener(NoRedirect).open(request, timeout=25) as response:
            identity = json.load(response)
    except urllib.error.HTTPError as error:
        reason = error.headers.get('X-Seraph-LoginReason', '')
        if reason in ('AUTHENTICATION_DENIED', 'AUTHENTICATED_FAILED'):
            raise ValueError('服务器拒绝认证（HTTP ' + str(error.code) + '，' + reason + '）。请先在浏览器登录并完成验证码，仍失败请联系管理员。') from None
        if error.code == 401:
            raise ValueError('认证失败（401）：请检查用户名、密码，以及站点是否允许 Basic 认证。') from None
        if error.code == 403:
            raise ValueError('服务器拒绝访问登录接口（403），请检查访问策略或 API 权限。') from None
        raise RuntimeError('登录接口错误：HTTP ' + str(error.code)) from None
    if (identity.get('type') != 'known'
            or str(identity.get('username', '')).casefold() != username.casefold()):
        raise ValueError('服务器未确认当前账号，请重新登录。')


class KeychainStore:
    SERVICE = b'io.palimpsest.sync'
    ACCOUNT = SITE.encode('utf-8')
    NOT_FOUND = -25300

    def __init__(self):
        if sys.platform != 'darwin':
            raise RuntimeError('登录记忆需要 macOS 钥匙串。')
        self.sec = C.CDLL('/System/Library/Frameworks/Security.framework/Security')
        self.cf = C.CDLL('/System/Library/Frameworks/CoreFoundation.framework/CoreFoundation')
        self.cf.CFRelease.argtypes = [C.c_void_p]
        self.cf.CFRelease.restype = None
        signatures = {
            'SecKeychainFindGenericPassword': [C.c_void_p, C.c_uint32, C.c_char_p,
                C.c_uint32, C.c_char_p, C.POINTER(C.c_uint32), C.POINTER(C.c_void_p), C.POINTER(C.c_void_p)],
            'SecKeychainAddGenericPassword': [C.c_void_p, C.c_uint32, C.c_char_p,
                C.c_uint32, C.c_char_p, C.c_uint32, C.c_void_p, C.POINTER(C.c_void_p)],
            'SecKeychainItemModifyAttributesAndData': [C.c_void_p, C.c_void_p, C.c_uint32, C.c_void_p],
            'SecKeychainItemDelete': [C.c_void_p],
            'SecKeychainItemFreeContent': [C.c_void_p, C.c_void_p],
        }
        for name, args in signatures.items():
            fn = getattr(self.sec, name)
            fn.argtypes, fn.restype = args, C.c_int32

    def check(self, status):
        if status:
            raise RuntimeError('钥匙串访问失败（' + str(status) + '），请检查系统授权。')

    def find(self, read=False):
        length, data, item = C.c_uint32(), C.c_void_p(), C.c_void_p()
        status = self.sec.SecKeychainFindGenericPassword(None,
            len(self.SERVICE), self.SERVICE, len(self.ACCOUNT), self.ACCOUNT,
            C.byref(length) if read else None, C.byref(data) if read else None, C.byref(item))
        if status == self.NOT_FOUND:
            return None, None
        self.check(status)
        try:
            raw = C.string_at(data, length.value) if read else None
        except Exception:
            self.cf.CFRelease(item)
            raise
        finally:
            if data.value:
                self.sec.SecKeychainItemFreeContent(None, data)
        return raw, item

    def load(self):
        raw, item = self.find(True)
        if item is None:
            return None
        try:
            value = json.loads(raw)
            if (not isinstance(value, dict) or not isinstance(value.get('username'), str)
                    or not isinstance(value.get('password'), str)
                    or not value['username'] or not value['password']):
                raise ValueError('已保存的登录信息无法读取，请重新登录。')
            return value['username'], value['password']
        finally:
            self.cf.CFRelease(item)

    def save(self, username, password):
        raw = json.dumps(dict(username=username, password=password)).encode()
        buffer = C.create_string_buffer(raw)
        _, item = self.find()
        try:
            if item is not None:
                status = self.sec.SecKeychainItemModifyAttributesAndData(item, None, len(raw), buffer)
            else:
                status = self.sec.SecKeychainAddGenericPassword(None,
                    len(self.SERVICE), self.SERVICE, len(self.ACCOUNT), self.ACCOUNT,
                    len(raw), buffer, None)
            self.check(status)
        finally:
            if item is not None:
                self.cf.CFRelease(item)

    def clear(self):
        _, item = self.find()
        if item is not None:
            try:
                self.check(self.sec.SecKeychainItemDelete(item))
            finally:
                self.cf.CFRelease(item)
