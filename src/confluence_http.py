"""HTTPS transport shared by login and document sync."""
import ssl
import urllib.error
import urllib.request

import truststore


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        raise RuntimeError('接口发生重定向，请检查登录方式。')


class VerifiedHTTPSHandler(urllib.request.HTTPSHandler):
    def https_open(self, request):
        try:
            return super().https_open(request)
        except urllib.error.URLError as error:
            if isinstance(error.reason, ssl.SSLCertVerificationError):
                raise RuntimeError(
                    '无法验证 Confluence 的 HTTPS 证书。请在同一台电脑上检查该站点的证书，'
                    '或请管理员检查证书链与系统信任设置。'
                ) from error
            raise


def create_opener():
    # Use native trust on macOS and Windows without changing global SSL settings.
    context = truststore.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
    return urllib.request.build_opener(NoRedirect, VerifiedHTTPSHandler(context=context))
