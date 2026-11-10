"""User-owned HTTPS endpoint configuration. No company-specific defaults."""
import json
import os
from pathlib import Path
import sys
from urllib.parse import urlsplit, urlunsplit


def normalize_site(value):
    parsed = urlsplit(value.strip())
    if (parsed.scheme != 'https' or not parsed.hostname or parsed.username or parsed.password
            or parsed.query or parsed.fragment or '\\' in value):
        raise ValueError('请输入 HTTPS Confluence 站点地址，例如 https://wiki.example.com 或 https://wiki.example.com/wiki。')
    port = parsed.port
    host = '['+parsed.hostname.lower()+']' if ':' in parsed.hostname else parsed.hostname.lower()
    if port not in (None, 443):
        host += ':'+str(port)
    path = parsed.path.rstrip('/')
    if any(segment in ('.', '..') for segment in path.split('/')):
        raise ValueError('站点路径不能包含 . 或 ..。')
    return urlunsplit(('https', host, path, '', ''))


if sys.platform == 'win32':
    default_root = Path(os.environ.get('LOCALAPPDATA', str(Path.home()))) / 'Palimpsest'
elif sys.platform == 'darwin':
    default_root = Path.home() / 'Library/Application Support/Palimpsest'
else:
    default_root = Path(os.environ.get('XDG_DATA_HOME', str(Path.home()/'.local/share'))) / 'palimpsest'
DATA_ROOT = Path(os.environ.get('PALIMPSEST_DATA', str(default_root)))
SETTINGS = DATA_ROOT / 'site.json'


def configured_site():
    value = os.environ.get('PALIMPSEST_SITE')
    if not value and SETTINGS.exists():
        value = json.loads(SETTINGS.read_text(encoding='utf-8'))['base_url']
    return normalize_site(value) if value else None


def configure_site(value):
    global SITE
    SITE = normalize_site(value)
    DATA_ROOT.mkdir(parents=True, exist_ok=True)
    temporary = SETTINGS.with_suffix('.tmp')
    temporary.write_text(json.dumps({'base_url': SITE}), encoding='utf-8')
    os.replace(temporary, SETTINGS)
    os.environ['PALIMPSEST_SITE'] = SITE
    return SITE


SITE = configured_site() or 'https://wiki.example.com'
