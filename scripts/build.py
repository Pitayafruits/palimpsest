"""Build on the target OS with an official CPython 3.12 installation."""
import hashlib
import os
from pathlib import Path
import platform
import shutil
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
os.chdir(ROOT)
DIST = ROOT / 'dist'


def run(*args):
    subprocess.run([str(a) for a in args], check=True)


def freeze(entry, name, *extra):
    run(sys.executable, '-m', 'PyInstaller', '--noconfirm', '--clean',
        '--onedir', '--name', name, '--paths', 'src', *extra, entry)


def main():
    if sys.platform == 'win32':
        freeze('src/sync_engine.py', 'SyncCore', '--exclude-module', 'PySide6')
        package = DIST / 'Palimpsest'
        package.mkdir(exist_ok=True)
        shutil.copytree(DIST / 'SyncCore', package / 'engine', dirs_exist_ok=True)
        compiler = Path(os.environ['WINDIR']) / 'Microsoft.NET/Framework64/v4.0.30319/csc.exe'
        run(compiler, '/nologo', '/target:winexe', '/platform:x64',
            '/out:' + str(package / 'Palimpsest.exe'),
            '/r:System.Windows.Forms.dll', '/r:System.Drawing.dll',
            '/r:System.Web.Extensions.dll', '/r:System.Security.dll',
            ROOT / 'windows/SyncWindow.cs', ROOT / 'windows/LoginSupport.cs', ROOT / 'windows/ServerSettings.cs')
        run(package / 'engine/SyncCore.exe', '--self-test')
        label = 'windows-x64'
    elif sys.platform == 'darwin':
        freeze('src/launch.py', 'Palimpsest', '--windowed',
               '--osx-bundle-identifier', 'io.palimpsest.sync',
               '--hidden-import', 'app', '--hidden-import', 'associations',
               '--hidden-import', 'login', '--hidden-import', 'sync_engine')
        package = DIST / 'Palimpsest.app'
        label = 'macos-' + ('arm64' if platform.machine() == 'arm64' else 'x64')
    else:
        raise SystemExit('Build on Windows or macOS.')

    notices = package / ('Contents/Resources/legal' if sys.platform == 'darwin' else 'legal')
    notices.mkdir(parents=True, exist_ok=True)
    for name in ('LICENSE', 'THIRD_PARTY_NOTICES.md', 'README.md'):
        shutil.copy2(ROOT / name, notices / name)
    # Include installed dependency license texts, including Qt's LGPL/GPL notices.
    import importlib.metadata
    for distribution in importlib.metadata.distributions():
        for item in distribution.files or []:
            if any(term in str(item).lower() for term in ('license', 'copying', 'copyright')):
                source = distribution.locate_file(item)
                if source.is_file():
                    target = notices / distribution.metadata['Name'] / str(item).replace('..', '_')
                    target.parent.mkdir(parents=True, exist_ok=True)
                    shutil.copy2(source, target)
    assets = DIST / 'release'
    assets.mkdir(exist_ok=True)
    stem = 'Palimpsest-' + label
    if sys.platform == 'darwin':
        run('codesign', '--force', '--deep', '--sign', '-', package)
        run('ditto', '-c', '-k', '--sequesterRsrc', '--keepParent', package, assets / (stem + '.zip'))
        run('hdiutil', 'create', '-volname', 'Palimpsest', '-srcfolder', package,
            '-ov', '-format', 'UDZO', assets / (stem + '.dmg'))
    else:
        shutil.make_archive(str(assets / stem), 'zip', DIST, 'Palimpsest')
    hashes = []
    for artifact in sorted(assets.iterdir()):
        if artifact.suffix in ('.zip', '.dmg'):
            hashes.append(hashlib.sha256(artifact.read_bytes()).hexdigest() + '  ' + artifact.name)
    (assets / ('SHA256SUMS-' + label + '.txt')).write_text('\n'.join(hashes) + '\n', encoding='utf-8')


if __name__ == '__main__':
    main()
