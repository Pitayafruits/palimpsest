"""Persistent one-to-one page/file associations; never stores credentials."""
import hashlib
import json
import os
from pathlib import Path
import re
import time
from urllib.parse import urlparse, parse_qs

from server import SITE, normalize_site


def parse_page(value):
    value = value.strip()
    if re.fullmatch(r'[1-9][0-9]*', value):
        return value
    url = urlparse(value)
    expected = urlparse(SITE)
    if (url.scheme != expected.scheme or url.netloc != expected.netloc
            or url.path != expected.path + '/pages/viewpage.action'
            or url.username or url.password):
        raise ValueError('请输入当前站点的数字 pageId 或完整页面链接。')
    values = parse_qs(url.query).get('pageId', [])
    if len(values) != 1 or not re.fullmatch(r'[1-9][0-9]*', values[0]):
        raise ValueError('链接中未找到唯一的有效 pageId。')
    return values[0]


def atomic_json(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_suffix('.tmp')
    temp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding='utf-8')
    os.replace(temp, path)


class Catalogue:
    def __init__(self, root):
        self.root = Path(root)
        self.path = self.root / 'associations.json'
        self.data = json.loads(self.path.read_text(encoding='utf-8')) if self.path.exists() else {
            'bindings': [], 'last_id': None, 'username': ''}
        if not isinstance(self.data, dict) or not isinstance(self.data.get('bindings'), list):
            raise ValueError('关联列表格式错误，请保留数据并检查 associations.json。')
        for item in self.bindings:
            if (not isinstance(item, dict) or not re.fullmatch(r'[a-f0-9]{64}', item.get('id', ''))
                    or not isinstance(item.get('local_file'), str)):
                raise ValueError('关联记录损坏，请保留 associations.json。')

    @property
    def bindings(self):
        return self.data['bindings']

    def directory(self, binding):
        return self.root / 'bindings' / binding['id']

    def persist(self):
        atomic_json(self.path, self.data)

    def save(self, page, file, direction, username):
        page = parse_page(page)
        if not file.strip():
            raise ValueError('请选择 Markdown 文件。')
        path = Path(file).expanduser().resolve()
        if path.suffix.lower() != '.md' or not path.is_file():
            raise ValueError('请选择已存在的 .md 文件。')
        if direction not in ('local', 'remote'):
            raise ValueError('请选择首次同步方向。')
        file = str(path)
        identity = hashlib.sha256((page + '\n' + file).encode()).hexdigest()
        retained = []
        active = any(item['id'] == identity for item in self.bindings)
        for item in self.bindings:
            same_file = Path(item['local_file']).resolve() == path
            if not same_file and Path(item['local_file']).exists():
                same_file = os.path.samefile(item['local_file'], path)
            if item['id'] != identity and item['page_id'] != page and not same_file:
                retained.append(item)
        binding = {'id': identity, 'page_id': page, 'local_file': file, 'initial_sync': direction}
        state = self.directory(binding) / 'state.json'
        if not active and state.exists():
            history = state.parent / 'state-history'
            history.mkdir(parents=True, exist_ok=True)
            os.replace(state, history / (str(time.time_ns()) + '.json'))
        config = dict(base_url=SITE, page_id=page, local_file=file, initial_sync=direction,
                      poll_seconds=30, settle_seconds=5, initial_remote_backup=True)
        atomic_json(self.directory(binding) / 'mapping.json', config)
        self.data['bindings'] = retained + [binding]
        self.data.update(last_id=identity, username=username.strip())
        self.persist()
        return binding

    def remove(self, identity):
        self.data['bindings'] = [b for b in self.bindings if b['id'] != identity]
        self.data['last_id'] = None
        self.persist()
