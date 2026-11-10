"""One-page Markdown/Confluence Server sync. Credentials live only in memory."""
import base64
import hashlib
import html
import json
import os
from pathlib import Path
import re
import sys
from server import SITE, DATA_ROOT, normalize_site
import time
import urllib.request
import urllib.error
from lxml import etree
from image_sync import Images, AttachmentClient
from markdown_it import MarkdownIt
from review import ConflictPending, ReviewService

ROOT = DATA_ROOT
MARKDOWN = MarkdownIt('commonmark', {'html': True}).enable(['table', 'strikethrough'])
AC = 'http://atlassian.com/content'
RI = 'http://atlassian.com/resource/identifier'
TOKEN = re.compile(r'<!-- confluence-storage:([A-Za-z0-9+/=]+) -->')


def digest(text):
    return hashlib.sha256(text.encode('utf-8')).hexdigest()


def save_json(path, obj):
    temp = path.with_suffix('.tmp')
    temp.write_text(json.dumps(obj, ensure_ascii=False, indent=2), encoding='utf-8')
    os.replace(temp, path)


def xml_entities(storage):
    # Markdown renderers produce HTML; storage requires XML-safe entities.
    # Leave literal code in CDATA/comments intact and never decode into markup.
    pattern = r'<!\[CDATA\[.*?\]\]>|<!--.*?-->|<\?.*?\?>|&(?:#[0-9]+;|#x[0-9a-fA-F]+;|[A-Za-z][A-Za-z0-9]*;)?'
    def replace(match):
        value = match[0]
        if not value.startswith('&') or value.startswith('&#'):
            return value
        if value in ('&amp;', '&lt;', '&gt;', '&quot;', '&apos;'):
            return value
        decoded = html.entities.html5.get(value[1:])
        if decoded is not None:
            return ''.join('&#' + str(ord(char)) + ';' for char in decoded)
        return '&amp;' + value[1:]
    return re.sub(pattern, replace, storage, flags=re.S)


def xml_root(storage):
    storage = xml_entities(storage)
    parser = etree.XMLParser(resolve_entities=False, no_network=True)
    return etree.fromstring((f'<root xmlns:ac="{AC}" xmlns:ri="{RI}">' + storage + '</root>').encode(), parser)


def opaque(node):
    raw = etree.tostring(node, encoding='unicode', with_tail=False)
    return '<!-- confluence-storage:' + base64.b64encode(raw.encode()).decode() + ' -->'


def storage_to_md(storage, images=None):
    root = xml_root(storage)

    def escape(text):
        return re.sub(r'([\\`*_\[\]<>])', r'\\\1', text or '')

    def children(n):
        return escape(n.text) + ''.join(convert(c) + escape(c.tail) for c in n)

    def convert(n):
        if not isinstance(n.tag, str):
            return opaque(n)
        name = etree.QName(n).localname
        ns = etree.QName(n).namespace
        if ns == AC and name == 'image' and images is not None:
            result = images.markdown(n)
            if result is not None: return result
        if ns in (AC, RI):
            return opaque(n)
        if name == 'table':
            # Complex tables are preserved verbatim, never flattened silently.
            if n.xpath('.//*[@rowspan and @rowspan!="1"] | .//*[@colspan and @colspan!="1"] | .//table | .//ul | .//ol | .//pre | .//blockquote'):
                return '\n\n' + (images.table(n) if images is not None else opaque(n)) + '\n\n'
            rows = []
            for row in n.xpath('.//tr'):
                cells = []
                for cell in row:
                    if etree.QName(cell).localname not in ('td', 'th'):
                        continue
                    text = children(cell).strip().replace('|', '\\|')
                    text = re.sub(r'\n+', '<br />', text)
                    cells.append(text)
                if cells:
                    rows.append(cells)
            if not rows:
                return '\n\n' + (images.table(n) if images is not None else opaque(n)) + '\n\n'
            width = max(map(len, rows))
            rows = [r + [''] * (width - len(r)) for r in rows]
            lines = ['| ' + ' | '.join(r) + ' |' for r in rows]
            lines.insert(1, '| ' + ' | '.join(['---'] * width) + ' |')
            return '\n\n' + '\n'.join(lines) + '\n\n'
        if name in ('ul', 'ol'):
            items = []
            for i, child in enumerate(n, 1):
                if etree.QName(child).localname != 'li':
                    continue
                body = children(child).strip()
                marker = f'{i}. ' if name == 'ol' else '- '
                items.append(marker + body.replace('\n', '\n' + ' ' * len(marker)))
            return '\n\n' + '\n'.join(items) + '\n\n'
        if name == 'pre':
            text = ''.join(n.itertext())
            fence = '`' * max(3, max([len(m[0]) + 1 for m in re.finditer(r'`+', text)] or [3]))
            return '\n\n' + fence + '\n' + text + '\n' + fence + '\n\n'
        if name not in {'root', 'p', 'div', 'span', 'strong', 'b', 'em', 'i', 's', 'del', 'code', 'a', 'br', 'hr', 'blockquote', 'h1', 'h2', 'h3', 'h4', 'h5', 'h6', 'li'}:
            return opaque(n)
        text = children(n)
        if re.fullmatch(r'h[1-6]', name):
            return '\n\n' + '#' * int(name[1]) + ' ' + text.strip() + '\n\n'
        if name in ('p', 'div'):
            if name == 'p':
                # Confluence may store typed numbering as paragraph text, including
                # after <br>. Keep those digits literal rather than Markdown list syntax.
                # Run after joining spans so split inline formatting is covered too.
                text = re.sub(r'(?m)^([ \t]{0,3})([0-9]{1,9})([.)])(?=[ \t])',
                              lambda m: m[1] + m[2] + '\\' + m[3], text)
            return '\n\n' + text.strip() + '\n\n'
        if name in ('strong', 'b', 'em', 'i', 's', 'del'):
            if not text.strip():
                return text
            tag, marker = ('strong', '**') if name in ('strong', 'b') else ('em', '*') if name in ('em', 'i') else ('del', '~~')
            # CommonMark delimiter rules depend on adjacent punctuation/whitespace.
            # Explicit inline tags keep these styles from becoming literal markers.
            if (not text[0].isalnum() or not text[-1].isalnum()
                    or '<!-- confluence-storage:' in text):
                return '<' + tag + '>' + text + '</' + tag + '>'
            return marker + text + marker
        if name == 'code':
            text = ''.join(n.itertext())
            fence = '`' * max(1, max([len(m[0]) + 1 for m in re.finditer(r'`+', text)] or [1]))
            return fence + ' ' + text + ' ' + fence
        if name == 'a':
            href = n.get('href', '')
            return '[' + text + '](' + href.replace(')', '%29') + ')' if href else text
        if name == 'br':
            return '<br />\n'
        if name == 'hr':
            return '\n\n---\n\n'
        if name == 'blockquote':
            return '\n\n' + '\n'.join('> ' + line for line in text.strip().splitlines()) + '\n\n'
        return text

    result = convert(root).replace('\r\n', '\n')
    return re.sub(r'\n{3,}', '\n\n', result).strip() + '\n'


def md_to_storage(md, images=None, upload=True, raw_images=False):
    if not md.strip():
        raise ValueError('文档为空，已停止同步。')
    rendered = MARKDOWN.render(md)
    rendered = TOKEN.sub(lambda m: base64.b64decode(m[1], validate=True).decode(), rendered)
    # marked emits HTML void elements; Confluence storage requires XHTML.
    rendered = re.sub(r'<(br|hr|img|input)(\b[^>]*?)(?<!/)>', r'<\1\2 />', rendered)
    rendered = xml_entities(rendered)
    root = xml_root(rendered)
    for n in root.iter():
        if not isinstance(n.tag, str):
            continue
        name = etree.QName(n).localname.lower()
        if name in {'script', 'iframe', 'object', 'embed', 'form', 'input'}:
            raise ValueError(f'暂不支持的 HTML 元素：{name}')
        for key, value in n.attrib.items():
            if key.lower().startswith('on') or re.match(r'\s*(javascript|vbscript):', value, re.I):
                raise ValueError('检测到不支持的 HTML 属性。')
        if name == 'img' and images is None and not raw_images and not n.get('src', '').startswith('https://'):
            raise ValueError('本地图片需要通过同步引擎上传。')
    return images.replace_images(root, upload=upload) if images is not None else rendered


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        raise RuntimeError('接口发生重定向，请检查登录方式。')


class AuthenticationExpired(RuntimeError):
    pass


class Client(AttachmentClient):
    authentication_error = AuthenticationExpired
    def __init__(self, config, username, password):
        self.base = config['base_url'].rstrip('/')
        self.page = str(config['page_id'])
        if normalize_site(self.base) != SITE or not re.fullmatch(r'[1-9][0-9]*', self.page):
            raise ValueError('请输入当前站点的有效数字 pageId。')
        self.auth = 'Basic ' + base64.b64encode((username + ':' + password).encode()).decode()
        self.opener = urllib.request.build_opener(NoRedirect)

    def request(self, method='GET', payload=None):
        url = self.base + '/rest/api/content/' + self.page
        if method == 'GET':
            url += '?expand=body.storage,version,space'
        headers = {'Authorization': self.auth, 'Accept': 'application/json', 'Content-Type': 'application/json; charset=utf-8'}
        data = json.dumps(payload, ensure_ascii=False).encode() if payload else None
        request = urllib.request.Request(url, data=data, headers=headers, method=method)
        try:
            with self.opener.open(request, timeout=25) as response:
                result = json.load(response)
        except urllib.error.HTTPError as error:
            if error.code == 401:
                raise AuthenticationExpired('登录已失效，请重新登录。') from None
            if error.code == 403:
                raise RuntimeError('没有此页面的访问或编辑权限（403）。') from None
            if error.code == 409:
                raise RuntimeError('网页版本已变化，已停止本次写入；下次检查将重新判断冲突。') from None
            raise RuntimeError(f'Confluence 接口错误：HTTP {error.code}') from None
        if str(result.get('id')) != self.page or result.get('type') != 'page':
            raise RuntimeError('接口没有返回绑定的页面。')
        return result

    def get(self):
        return self.request()

    def put(self, page, storage):
        return self.request('PUT', {'id': self.page, 'type': 'page', 'title': page['title'],
            'version': {'number': page['version']['number'] + 1, 'minorEdit': True,
                        'message': 'Markdown 双向同步'},
            'body': {'storage': {'value': storage, 'representation': 'storage'}}})


class Sync:
    def __init__(self, config, client, report):
        self.config, self.client, self.report = config, client, report
        self.file = Path(config['local_file'])
        if self.file.suffix.lower() != '.md' or not self.file.is_file():
            raise ValueError('请选择一个已存在的 Markdown（.md）文件。')
        self.data = Path(config.get('_data_dir', ROOT))
        self.data.mkdir(parents=True, exist_ok=True)
        self.images = Images(self.file, self.data, client, xml_root,
                             lambda md: xml_root(md_to_storage(md, raw_images=True)))
        self.state_file = self.data / 'state.json'
        self.state = json.loads(self.state_file.read_text(encoding='utf-8')) if self.state_file.exists() else None
        if self.state and (os.path.normcase(self.state['file']) != os.path.normcase(str(self.file)) or self.state['page'] != str(config['page_id']) or self.state.get('base', config['base_url'] if 'base_url' in config else '') != config.get('base_url', '')):
            raise RuntimeError('同步基准与当前绑定不一致。')
        ROOT.mkdir(parents=True, exist_ok=True)
        self.lock = open(ROOT / 'sync.lock', 'a+b')
        if self.lock.seek(0, 2) == 0:
            self.lock.write(b'0')
            self.lock.flush()
        self.lock.seek(0)
        if os.name == 'nt':
            import msvcrt
            try:
                msvcrt.locking(self.lock.fileno(), msvcrt.LK_NBLCK, 1)
            except OSError:
                self.lock.close()
                raise RuntimeError('已有一个同步程序正在运行。') from None
        else:
            import fcntl
            try:
                fcntl.flock(self.lock.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            except OSError:
                self.lock.close()
                raise RuntimeError('已有一个同步程序正在运行。') from None

    def close(self):
        self.lock.close()

    def read(self):
        return self.file.read_text(encoding='utf-8-sig')

    def baseline(self, md, page, local_images=None, remote_images=None):
        self.state = {'file': str(self.file), 'page': str(self.config['page_id']),
                      'base': self.config.get('base_url', ''),
                      'local_hash': digest(md), 'remote_hash': digest(page['body']['storage']['value']),
                      'version': page['version']['number'], 'updated': time.time(), 'image_sync': 1,
                      'baseline_md': md,
                      'baseline_images': self.images.local_signature(md) if local_images is None else local_images,
                      'local_images': self.images.local_signature(md) if local_images is None else local_images,
                      'remote_images': self.images.remote_signature(page) if remote_images is None else remote_images}
        save_json(self.state_file, self.state)

    def push(self, md, page):
        local_images = self.images.local_signature(md)
        original_remote_images = self.images.remote_signature(page)
        storage = md_to_storage(md, self.images)
        if self.read() != md or self.images.local_signature(md) != local_images:
            raise RuntimeError('本地文件仍在修改，等待下一轮。')
        latest = self.client.get()
        if latest['version']['number'] != page['version']['number']:
            raise RuntimeError('网页在转换期间发生变化，等待重新检查。')
        if self.images.remote_signature(latest, refresh=True) != original_remote_images:
            raise RuntimeError('网页图片在上传期间变化，已停止更新正文。')
        target = dict(latest, body={'storage': {'value': storage}})
        target_images = self.images.remote_signature(target)
        if self.read() != md or self.images.local_signature(md) != local_images:
            raise RuntimeError('本地文档或图片仍在修改，已停止更新正文。')
        self.client.put(latest, storage)
        verified = self.client.get()
        # Confluence may normalize XHTML. Confirm semantic text and preserved macros.
        if semantic(storage) != semantic(verified['body']['storage']['value']):
            raise RuntimeError('写入后的内容校验不一致，未更新同步基准，请检查网页。')
        if self.images.remote_signature(verified, refresh=True) != target_images:
            raise RuntimeError('写入后网页图片已变化，未更新同步基准。')
        self.baseline(md, verified, local_images=local_images, remote_images=target_images)
        self.report('已同步到 Confluence，版本 ' + str(verified['version']['number']))

    def pull(self, md, page):
        remote = page['body']['storage']['value']
        local_images = self.images.local_signature(md)
        remote_images = self.images.remote_signature(page)
        incoming = storage_to_md(remote, self.images)
        incoming_images = self.images.local_signature(incoming)
        if semantic(remote) != semantic(md_to_storage(incoming, self.images, upload=False)):
            raise RuntimeError('网页内容无法完整转换为 Markdown，已停止覆盖本地，请检查格式。')
        if self.read() != md:
            raise RuntimeError('本地同时发生修改，等待重新检查。')
        latest = self.client.get()
        if latest['version']['number'] != page['version']['number']:
            raise RuntimeError('网页仍在修改，等待重新检查。')
        if self.images.remote_signature(latest, refresh=True) != remote_images:
            raise RuntimeError('网页图片仍在修改，已取消覆盖本地。')
        if self.images.local_signature(md) != local_images:
            raise RuntimeError('本地图片同时发生修改，已取消覆盖本地。')
        backups = self.data / 'local-backups'
        backups.mkdir(exist_ok=True)
        (backups / (time.strftime('%Y%m%d-%H%M%S') + '.md')).write_text(md, encoding='utf-8')
        temp = self.file.with_name(self.file.name + '.sync-tmp')
        temp.write_text(incoming, encoding='utf-8')
        if self.read() != md or self.images.local_signature(md) != local_images:
            temp.unlink(missing_ok=True)
            raise RuntimeError('本地同时发生修改，已取消覆盖。')
        os.replace(temp, self.file)
        self.baseline(incoming, page, local_images=incoming_images, remote_images=remote_images)
        self.report('已从 Confluence 更新本地，版本 ' + str(page['version']['number']))

    def tick(self):
        self.images.begin()
        review = ReviewService(self)
        if review.recover():
            self.report('两端已更新，自动同步已恢复。')
            return
        pending = review.load()
        if pending and pending.get('phase') != 'resolved':
            raise ConflictPending('两边都已修改，有内容待确认。请打开文档审阅，确认后恢复同步。')
        md = self.read()
        if self.config['settle_seconds'] > 0 and time.time() - self.file.stat().st_mtime < self.config['settle_seconds']:
            self.report('等待本地文件保存完成…')
            return
        if self.images.recent(md, self.config['settle_seconds']):
            self.report('等待图片保存完成…')
            return
        page = self.client.get()
        current_local_images = self.images.local_signature(md)
        current_remote_images = self.images.remote_signature(page)
        if self.state is None:
            if self.config.get('initial_sync') == 'remote':
                self.pull(md, page)
                return
            if self.config.get('initial_sync') != 'local':
                raise RuntimeError('首次同步方向尚未配置。')
            if self.config.get('initial_remote_backup', True):
                backup = self.data / 'initial-remote.json'
                if not backup.exists():
                    save_json(backup, page)
            self.push(md, page)
            return
        remote = page['body']['storage']['value']
        local_changed = digest(md) != self.state['local_hash'] or current_local_images != self.state.get('local_images', current_local_images)
        remote_changed = digest(remote) != self.state['remote_hash'] or current_remote_images != self.state.get('remote_images', current_remote_images)
        if local_changed and remote_changed:
            review.capture(md, page)
            raise ConflictPending('两边都已修改，有内容待确认。请打开文档审阅，确认后恢复同步。')
        if local_changed:
            self.push(md, page)
        elif remote_changed:
            self.pull(md, page)
        elif not self.state.get('image_sync') and current_remote_images:
            # Materialize existing retained image tokens when upgrading an unchanged pair.
            self.pull(md, page)
        else:
            self.report('两端一致，等待修改（每 30 秒检查）')


def semantic(storage):
    root = xml_root(storage)
    text = ''.join(root.itertext())
    macros = [etree.QName(n).localname + ':' + (n.get('{'+AC+'}name') or n.get('{'+RI+'}filename') or '')
              for n in root.iter() if isinstance(n.tag, str) and etree.QName(n).namespace in (AC, RI)]
    return re.sub(r'\s+', '', text), macros


def headless():
    if '--config' not in sys.argv:
        raise SystemExit('缺少关联配置路径。')
    config_path = Path(sys.argv[sys.argv.index('--config') + 1]).resolve()
    config = json.loads(config_path.read_text(encoding='utf-8-sig'))
    config['_data_dir'] = str(config_path.parent)
    data_dir = config_path.parent
    creds = json.loads(sys.stdin.readline())
    engine = None
    def report(message, kind='running', error_code=None):
        save_json(data_dir / 'status.json', {'status': kind, 'message': message,
                                        'time': time.strftime('%H:%M:%S'), 'error_code': error_code})
    try:
        client = Client(config, creds['username'], creds['password'])
        creds.clear()
        engine = Sync(config, client, report)
        report('正在连接页面 ' + str(config['page_id']) + '…')
        while not (data_dir / 'stop.request').exists():
            try:
                engine.tick()
            except (urllib.error.URLError, TimeoutError, OSError):
                report('网络或文件访问失败，30 秒后重试。')
            for _ in range(config['poll_seconds']):
                if (data_dir / 'stop.request').exists():
                    break
                time.sleep(1)
        report('同步已停止。', 'stopped')
    except AuthenticationExpired as error:
        report(str(error), 'stopped', 'auth_expired')
    except ConflictPending as error:
        report(str(error), 'review', 'conflict')
    except Exception as error:
        report(str(error), 'stopped')
    finally:
        if engine:
            engine.client.auth = ''
            engine.close()


if __name__ == '__main__':
    if '--review' in sys.argv:
        from review_gui import standalone
        raise SystemExit(standalone())
    elif '--review-smoke-test' in sys.argv:
        from review_gui import smoke_test
        raise SystemExit(smoke_test())
    elif '--self-test' in sys.argv:
        import ssl
        example = '# Portable test\n\n| A | B |\n| --- | --- |\n| 1 | **2** |\n'
        rendered = md_to_storage(example)
        assert len(xml_root(rendered).xpath('.//table')) == 1
        assert semantic(rendered) == semantic(md_to_storage(storage_to_md(rendered)))
        numbered = '<p>1. First<br />2. Second<br />3. Third</p>'
        assert semantic(numbered) == semantic(md_to_storage(storage_to_md(numbered)))
        styled = '<p>确认删除 &quot;{<em>小组名称} &quot;</em>吗？</p>'
        styled_roundtrip = md_to_storage(storage_to_md(styled))
        assert semantic(styled) == semantic(styled_roundtrip)
        assert xml_root(styled_roundtrip).find('.//em') is not None
        design_url = 'https://www.figma.com/design/test?node-id=641-1048&p=f&t=sample-0'
        design = md_to_storage('[Design](' + design_url + ')')
        assert etree.fromstring(('<root>' + design + '</root>').encode()).find('.//a').get('href') == design_url
        long_text = '普通个人点评与中文长文' * 15000
        assert semantic(md_to_storage(long_text))[0] == long_text
        ssl.create_default_context()
        import tempfile
        from types import SimpleNamespace
        from image_sync import image_type
        png = base64.b64decode('iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+aXioAAAAASUVORK5CYII=')
        assert image_type(png)[0] == 'png'
        with tempfile.TemporaryDirectory() as directory:
            folder = Path(directory)
            (folder / 'sample.png').write_bytes(png)
            items = []
            def upload(name, data, media):
                assert data == png and media == 'image/png'
                item = {'id': '1', 'title': name, 'version': {'number': 1}}
                items.append(item)
                return item
            client = SimpleNamespace(page='123', list_attachments=lambda page: items,
                                     download_attachment=lambda item: png, upload_image=upload)
            images = Images(folder/'test.md', folder, client, xml_root,
                            lambda md: xml_root(md_to_storage(md, raw_images=True)))
            storage = md_to_storage('![sample](sample.png)', images)
            assert len(items) == 1
            incoming = storage_to_md(storage, images)
            assert '.assets/' in incoming
            assert semantic(md_to_storage(incoming, images, upload=False)) == semantic(storage)
        print('PASS: Python, lxml, markdown-it, numbering, image upload/download roundtrip and TLS (offline)')
    elif '--headless' in sys.argv:
        headless()
    else:
        raise SystemExit('请运行 Palimpsest.exe 打开登录窗口。')
