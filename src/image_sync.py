"""Confluence attachment image transport and per-document image references."""
import copy
import hashlib
import json
import os
from pathlib import Path
import re
import urllib.error
import urllib.parse
import urllib.request
import uuid
from lxml import etree

AC = 'http://atlassian.com/content'
RI = 'http://atlassian.com/resource/identifier'
MAX_IMAGE = 50 * 1024 * 1024


def sha(data):
    return hashlib.sha256(data).hexdigest()


def image_type(data):
    if data.startswith(b'\x89PNG\r\n\x1a\n'): return 'png', 'image/png'
    if data.startswith(b'\xff\xd8\xff'): return 'jpg', 'image/jpeg'
    if data.startswith((b'GIF87a', b'GIF89a')): return 'gif', 'image/gif'
    if data.startswith(b'RIFF') and data[8:12] == b'WEBP': return 'webp', 'image/webp'
    if data.startswith(b'BM'): return 'bmp', 'image/bmp'
    raise ValueError('图片格式不支持或下载内容不是图片；目前支持 PNG、JPEG、GIF、WebP、BMP。')


class AttachmentClient:
    """Mixed into the existing authenticated client. Never follows redirects."""
    def _image_http(self, url, data=None, content_type=None, binary=False):
        base = urllib.parse.urlsplit(self.base)
        if url.startswith('/rest/') or url.startswith('/download/'):
            url = base.path.rstrip('/') + url
        url = urllib.parse.urljoin(self.base + '/', url)
        parsed = urllib.parse.urlsplit(url)
        if (parsed.scheme != 'https' or parsed.netloc != urllib.parse.urlsplit(self.base).netloc
                or parsed.scheme != urllib.parse.urlsplit(self.base).scheme or parsed.username or parsed.password):
            raise ValueError('附件地址不属于当前 Confluence 站点。')
        headers = {'Authorization': self.auth, 'Accept': '*/*' if binary else 'application/json'}
        if data is not None:
            headers.update({'Content-Type': content_type, 'X-Atlassian-Token': 'nocheck'})
        request = urllib.request.Request(url, data=data, headers=headers)
        try:
            with self.opener.open(request, timeout=45) as response:
                raw = response.read((MAX_IMAGE if binary else 8 * 1024 * 1024) + 1)
        except urllib.error.HTTPError as error:
            if error.code == 401: raise self.authentication_error('登录已失效，请重新登录。') from None
            if error.code == 403: raise RuntimeError('没有附件的读取或上传权限（403）。') from None
            raise RuntimeError('附件接口失败：HTTP ' + str(error.code)) from None
        if len(raw) > (MAX_IMAGE if binary else 8 * 1024 * 1024):
            raise ValueError('附件或接口响应过大，单张图片上限为 50 MB。')
        return raw if binary else json.loads(raw)

    def list_attachments(self, page):
        result, visited = [], set()
        url = '/rest/api/content/' + str(page) + '/child/attachment?expand=version&limit=100'
        while url:
            if url in visited: raise RuntimeError('附件分页接口重复，已停止同步。')
            visited.add(url)
            response = self._image_http(url)
            result.extend(response['results'])
            url = response.get('_links', {}).get('next')
        return result

    def download_attachment(self, item):
        raw = self._image_http(item['_links']['download'], binary=True)
        image_type(raw)
        return raw

    def resolve_image_page(self, title, space):
        query = dict(type='page', title=title, limit=2)
        if space: query['spaceKey'] = space
        response = self._image_http('/rest/api/content?' + urllib.parse.urlencode(query))
        if len(response['results']) != 1:
            raise RuntimeError('无法唯一定位图片所属页面：' + title)
        return str(response['results'][0]['id'])

    def upload_image(self, filename, data, media_type):
        boundary = 'ConfluenceSync' + uuid.uuid4().hex
        body = (('--' + boundary + '\r\nContent-Disposition: form-data; name="file"; filename="'
                + filename + '"\r\nContent-Type: ' + media_type + '\r\n\r\n').encode()
                + data + ('\r\n--' + boundary + '\r\nContent-Disposition: form-data; name="minorEdit"'
                + '\r\n\r\ntrue\r\n--' + boundary + '--\r\n').encode())
        response = self._image_http('/rest/api/content/' + self.page + '/child/attachment',
                                    data=body, content_type='multipart/form-data; boundary=' + boundary)
        items = response.get('results', [])
        if len(items) != 1 or items[0].get('title') != filename:
            raise RuntimeError('图片上传结果不符合预期，已停止更新正文。')
        return items[0]


class Images:
    def __init__(self, document, data_dir, client, parse_storage, parse_markdown):
        self.document = Path(document).resolve()
        self.data_dir, self.client = Path(data_dir), client
        self.parse_storage, self.parse_markdown = parse_storage, parse_markdown
        self.path = self.data_dir / 'images.json'
        self.mapping = json.loads(self.path.read_text(encoding='utf-8')) if self.path.exists() else {}
        self.begin()

    def begin(self):
        self.attachments, self.downloads, self.page_ids = {}, {}, {}
        self.markdown_roots = {}
        self.page = None

    def markdown_root(self, md):
        if md not in self.markdown_roots:
            self.markdown_roots[md] = self.parse_markdown(md)
        return self.markdown_roots[md]

    def persist(self):
        temp = self.path.with_suffix('.tmp')
        temp.write_text(json.dumps(self.mapping, ensure_ascii=False, indent=2), encoding='utf-8')
        os.replace(temp, self.path)

    def index(self, page):
        page = str(page)
        if page not in self.attachments:
            self.attachments[page] = {item['title']: item for item in self.client.list_attachments(page)}
        return self.attachments[page]

    def attachment(self, node):
        ref = node.find('{' + RI + '}attachment')
        if ref is None: return None
        page = self.client.page
        other = ref.find('{' + RI + '}page')
        if other is not None:
            title = other.get('{' + RI + '}content-title')
            space = other.get('{' + RI + '}space-key') or (self.page or {}).get('space', {}).get('key')
            key = (title, space)
            if key not in self.page_ids:
                self.page_ids[key] = self.client.resolve_image_page(title, space)
            page = self.page_ids[key]
        filename = ref.get('{' + RI + '}filename')
        try: item = self.index(page)[filename]
        except KeyError: raise RuntimeError('找不到图片附件：' + str(filename)) from None
        return str(page), item

    def remote_signature(self, page, refresh=False):
        if refresh: self.attachments.clear()
        self.page = page
        root = self.parse_storage(page['body']['storage']['value'])
        result = {}
        for node in root.iter('{' + AC + '}image'):
            match = self.attachment(node)
            if match:
                owner, item = match
                result[owner + ':' + item['title']] = [str(item['id']), int(item['version']['number']),
                                                       item.get('extensions', {}).get('fileSize')]
        return result

    def file_for(self, src):
        parsed = urllib.parse.urlsplit(src)
        if parsed.scheme in ('http', 'https'): return None
        if parsed.scheme == 'file':
            if parsed.netloc not in ('', 'localhost'): raise ValueError('不支持网络共享图片路径。')
            name = urllib.request.url2pathname(parsed.path)
        elif parsed.scheme and not (len(parsed.scheme) == 1 and src[1:2] == ':'):
            raise ValueError('不支持此图片地址：' + src)
        else: name = urllib.parse.unquote(src)
        path = Path(name).expanduser()
        return (path if path.is_absolute() else self.document.parent / path).resolve()

    def read_file(self, path):
        try:
            before = path.stat()
            if before.st_size > MAX_IMAGE: raise ValueError('单张图片不能超过 50 MB：' + path.name)
            data = path.read_bytes()
            after = path.stat()
        except FileNotFoundError:
            raise ValueError('找不到本地图片，已停止同步：' + str(path)) from None
        if (before.st_mtime_ns, before.st_size) != (after.st_mtime_ns, after.st_size):
            raise RuntimeError('图片仍在保存，请稍后重新同步：' + path.name)
        image_type(data)
        return data

    def local_signature(self, md):
        result = {}
        for node in self.markdown_root(md).iter('img'):
            src = node.get('src', '')
            path = self.file_for(src)
            if path is not None: result[src] = sha(self.read_file(path))
        return result

    def recent(self, md, seconds):
        if not seconds: return False
        import time
        for node in self.markdown_root(md).iter('img'):
            path = self.file_for(node.get('src', ''))
            if path is not None and time.time() - path.stat().st_mtime < seconds: return True
        return False

    def markdown(self, node):
        original = etree.tostring(node, encoding='unicode', with_tail=False)
        match = self.attachment(node)
        if not match:
            ref = node.find('{' + RI + '}url')
            if ref is None: return None
            src = ref.get('{' + RI + '}value', '')
            if not src.startswith('https://'): return None
        else:
            owner, item = match
            key = (str(item['id']), int(item['version']['number']))
            if key not in self.downloads:
                data = self.client.download_attachment(item)
                image_type(data)
                self.downloads[key] = data
            data = self.downloads[key]
            extension, _ = image_type(data)
            stem = re.sub(r'[^\w.-]', '_', Path(item['title']).stem)[:40] or 'image'
            directory = self.document.with_name(self.document.stem + '.assets') / ('confluence-' + self.client.page)
            directory.mkdir(parents=True, exist_ok=True)
            filename = stem + '-' + sha(original.encode())[:10] + '-' + sha(data)[:16] + '.' + extension
            path = directory / filename
            if path.exists() and sha(path.read_bytes()) != sha(data):
                # Never overwrite a user's edit, even if an old asset is downloaded again.
                path = directory / (path.stem + '-' + uuid.uuid4().hex[:8] + path.suffix)
            if not path.exists():
                temp = path.with_suffix(path.suffix + '.tmp')
                temp.write_bytes(data)
                os.replace(temp, path)
            src = urllib.parse.quote(path.relative_to(self.document.parent).as_posix(), safe='/')
        self.mapping[src] = {'storage': original, 'hash': sha(data) if match else None,
                             'remote': [match[0], str(match[1]['id']), match[1]['version']['number']] if match else None}
        self.last_src = src
        self.persist()
        alt = node.get('{' + AC + '}alt', '')
        alt = re.sub(r'([\\\[\]])', r'\\\1', alt).replace('\n', ' ').replace('\r', ' ')
        return '![' + alt + '](' + src + ')'

    def table(self, node):
        table = copy.deepcopy(node)
        for image in list(table.iter('{' + AC + '}image')):
            markdown = self.markdown(image)
            if markdown is None: continue
            # The src has just been recorded; avoid parsing Markdown inside raw tables.
            src = self.last_src
            replacement = etree.Element('img', src=src, alt=image.get('{' + AC + '}alt', ''))
            replacement.tail = image.tail
            image.getparent().replace(image, replacement)
        return etree.tostring(table, encoding='unicode', with_tail=False)

    def replace_images(self, root, upload=True):
        for node in list(root.iter('img')):
            src = node.get('src', '')
            path = self.file_for(src)
            entry = self.mapping.get(src)
            if path is None:
                if entry:
                    replacement = self.parse_storage(entry['storage'])[0]
                else: continue
            else:
                data = self.read_file(path)
                checksum = sha(data)
                if entry and entry.get('hash') == checksum:
                    replacement = self.parse_storage(entry['storage'])[0]
                    remote = self.attachment(replacement)
                    if remote:
                        owner, item = remote
                        identity = [owner, str(item['id']), item['version']['number']]
                        if entry.get('remote') != identity and sha(self.client.download_attachment(item)) != checksum:
                            raise RuntimeError('网页图片与本地图片已不一致，已停止更新正文，请处理冲突。')
                else:
                    if not upload: raise RuntimeError('图片映射与本地内容不一致，已停止覆盖。')
                    extension, media_type = image_type(data)
                    filename = 'mdsync-' + checksum + '.' + extension
                    item = self.index(self.client.page).get(filename)
                    if item is not None:
                        if sha(self.client.download_attachment(item)) != checksum:
                            raise RuntimeError('同名附件内容不符，已停止上传。')
                    else:
                        item = self.client.upload_image(filename, data, media_type)
                        if sha(self.client.download_attachment(item)) != checksum:
                            raise RuntimeError('上传图片的内容校验失败，未更新网页正文。')
                        self.attachments.clear()
                    replacement = (self.parse_storage(entry['storage'])[0] if entry else
                        etree.Element('{' + AC + '}image', nsmap={'ac': AC, 'ri': RI}))
                    for child in list(replacement): replacement.remove(child)
                    etree.SubElement(replacement, '{' + RI + '}attachment', {'{' + RI + '}filename': filename})
                    if node.get('alt'): replacement.set('{' + AC + '}alt', node.get('alt'))
                    self.mapping[src] = {'storage': etree.tostring(replacement, encoding='unicode'), 'hash': checksum,
                                         'remote': [self.client.page, str(item['id']), item.get('version', {}).get('number')]}
                    self.persist()
            if (etree.QName(replacement).namespace == AC and etree.QName(replacement).localname == 'image'
                    and node.get('alt', '') != replacement.get('{' + AC + '}alt', '')):
                replacement.set('{' + AC + '}alt', node.get('alt', ''))
            replacement.tail = node.tail
            node.getparent().replace(node, replacement)
        return (root.text or '') + ''.join(etree.tostring(child, encoding='unicode') for child in root)
