"""Exercise the Windows startup and first-link flow with isolated data."""
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest


@unittest.skipUnless(sys.platform == 'win32', 'Windows native application')
class WindowsStartupTests(unittest.TestCase):
    def test_home_without_setup_and_first_link(self):
        root = Path(__file__).resolve().parents[1]
        compiler = Path(os.environ['WINDIR']) / 'Microsoft.NET/Framework64/v4.0.30319/csc.exe'
        if not compiler.exists():
            self.skipTest('.NET Framework compiler unavailable')
        with tempfile.TemporaryDirectory() as directory:
            executable = Path(directory) / 'startup-tests.exe'
            subprocess.run([str(compiler), '/nologo', '/target:exe', '/platform:x64',
                '/main:StartupTests', '/out:' + str(executable),
                '/r:System.Windows.Forms.dll', '/r:System.Drawing.dll',
                '/r:System.Web.Extensions.dll', '/r:System.Security.dll',
                *map(str, (root / 'windows').glob('*.cs')),
                str(root / 'tests/windows_startup.cs')], check=True, capture_output=True, text=True, errors='replace')
            environment = dict(os.environ, PALIMPSEST_DATA=str(Path(directory) / 'data'))
            environment.pop('PALIMPSEST_SITE', None)
            result = subprocess.run([str(executable)], env=environment, timeout=30,
                                    capture_output=True, text=True, errors='replace')
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            self.assertIn('PASS:', result.stdout)
