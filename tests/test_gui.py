"""Offscreen interaction regression checks. No real credentials or network."""
import os
os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch, Mock
from PySide6.QtWidgets import QApplication, QMessageBox
from PySide6.QtTest import QTest
from associations import Catalogue
from app import Window, Worker, page_url
import sync_engine as engine

APP = QApplication.instance() or QApplication([])

class MemoryStore:
    def __init__(self, value=None): self.value = value
    def load(self): return self.value
    def save(self, username, password): self.value = (username, password)
    def clear(self): self.value = None

class GuiTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.doc = self.root / '需求.md'
        self.doc.write_text('# Test', encoding='utf-8')
        self.store = MemoryStore()
        self.verifier = Mock()
        self.window = Window(Catalogue(self.root / 'data'), self.store, self.verifier)

    def tearDown(self):
        self.window.close()
        APP.processEvents()
        self.temp.cleanup()

    def bind(self):
        self.window.page.setText(page_url('123'))
        self.window.file.setText(str(self.doc))
        return self.window.save_binding()

    def wait_login(self):
        for _ in range(200):
            APP.processEvents()
            if self.window.login_worker is None: return
            QTest.qWait(10)
        self.fail('Login worker did not finish')

    def test_picker_auto_save_reload(self):
        self.window.page.setText('123')
        with patch('app.QFileDialog.getOpenFileName', return_value=(str(self.doc), 'Markdown')):
            self.window.choose()
        reopened = Window(Catalogue(self.root / 'data'), self.store)
        self.assertEqual(reopened.page.text(), page_url('123'))
        self.assertEqual(reopened.file.text(), str(self.doc.resolve()))
        self.assertEqual(reopened.password.text(), '')
        reopened.close()

    def test_known_link_restores_new_link_clears_and_draft_preserves(self):
        self.bind()
        self.window.page.setText('456')
        self.assertEqual(self.window.file.text(), '')
        self.window.save_draft()
        self.assertEqual(self.window.catalogue.bindings[0]['page_id'], '123')
        self.window.page.setText('123')
        self.assertEqual(self.window.file.text(), str(self.doc.resolve()))

    def test_baseline_disables_direction_but_new_pair_enables(self):
        binding = self.bind()
        (self.window.catalogue.directory(binding) / 'state.json').write_text('{}')
        self.window.update_controls()
        self.assertFalse(self.window.direction.isEnabled())
        self.window.page.setText('456')
        self.assertTrue(self.window.direction.isEnabled())

    def test_file_replacement_is_automatic(self):
        self.bind()
        second = self.root / 'new.md'
        second.write_text('# Second')
        self.window.file.setText(str(second))
        self.window.file.editingFinished.emit()
        self.assertEqual(len(self.window.catalogue.bindings), 1)
        self.assertEqual(self.window.catalogue.bindings[0]['local_file'], str(second.resolve()))

    def test_default_browser_receives_canonical_url(self):
        self.window.page.setText('123')
        with patch('app.QDesktopServices.openUrl', return_value=True) as open_url:
            self.window.open_document()
        self.assertEqual(open_url.call_args.args[0].toString(), page_url('123'))

    def test_start_requires_credentials(self):
        self.bind()
        self.window.begin()
        self.assertIsNone(self.window.worker)
        self.assertIn('请输入账号', self.window.status.text())

    def test_login_remember_restore_and_logout(self):
        self.window.user.setText('alice_dev')
        self.window.password.setText('example-secret')
        self.window.login()
        self.wait_login()
        self.verifier.assert_called_once_with('alice_dev', 'example-secret')
        self.assertEqual(self.store.value, ('alice_dev', 'example-secret'))
        self.assertEqual(self.window.account.text(), 'alice_dev')
        self.assertEqual(self.window.password.text(), '')
        reopened = Window(Catalogue(self.root / 'data'), self.store)
        self.assertEqual(reopened.credentials, self.store.value)
        self.assertTrue(reopened.login_form.isHidden())
        self.assertEqual(reopened.password.text(), '')
        reopened.logout()
        self.assertIsNone(self.store.value)
        self.assertIsNone(reopened.credentials)
        reopened.close()

    def test_rejected_login_never_persists(self):
        self.verifier.side_effect = ValueError('rejected')
        self.window.user.setText('alice_dev')
        self.window.password.setText('bad')
        self.window.login()
        self.wait_login()
        self.assertIsNone(self.store.value)
        self.assertIsNone(self.window.credentials)
        self.assertIn('rejected', self.window.status.text())

    def test_unavailable_keychain_keeps_current_login(self):
        self.store.save = Mock(side_effect=RuntimeError('denied'))
        self.window.user.setText('alice_dev')
        self.window.password.setText('secret')
        self.window.login()
        self.wait_login()
        self.assertEqual(self.window.credentials, ('alice_dev', 'secret'))
        self.assertIn('无法记住', self.window.status.text())

    def test_corrupt_saved_login_returns_form(self):
        broken = Mock()
        broken.load.side_effect = ValueError('corrupt')
        reopened = Window(Catalogue(self.root / 'data'), broken)
        self.assertIsNone(reopened.credentials)
        self.assertFalse(reopened.login_form.isHidden())
        self.assertIn('corrupt', reopened.status.text())
        reopened.close()

    def test_logout_waits_for_worker(self):
        self.window.credentials = ('alice_dev', 'secret')
        self.store.value = self.window.credentials
        fake = Mock()
        self.window.worker = fake
        self.window.logout()
        fake.stop_event.set.assert_called_once()
        self.assertTrue(self.window.pending_logout)
        self.assertIsNotNone(self.window.credentials)
        self.assertIsNone(self.store.value)
        self.assertFalse(self.window.start.isEnabled())
        self.window.finished()
        self.assertIsNone(self.window.credentials)
        self.assertFalse(self.window.pending_logout)

    def test_start_after_login_keeps_overwrite_confirmation(self):
        self.bind()
        self.window.user.setText('alice_dev')
        self.window.password.setText('secret')
        with patch('app.QMessageBox.question', return_value=QMessageBox.StandardButton.Cancel) as confirm:
            self.window.begin()
            self.wait_login()
        confirm.assert_called_once()
        self.assertIsNone(self.window.worker)
        self.assertEqual(self.doc.read_text(), '# Test')

    def test_auth_expired_forgets_session_even_if_keychain_clear_denied(self):
        self.window.credentials = ('alice_dev', 'secret')
        self.store.clear = Mock(side_effect=RuntimeError('denied'))
        self.window.authentication_expired()
        self.assertIsNone(self.window.credentials)
        self.assertFalse(self.window.login_form.isHidden())
        self.assertIn('登录已失效', self.window.status.text())
        self.assertIn('钥匙串未能清除', self.window.status.text())

    def test_worker_stop_and_credentials_clear(self):
        binding = self.bind()
        from test_sync import Client as FakeClient
        fake = FakeClient()
        fake.auth = 'placeholder'
        worker = Worker(self.window.catalogue.directory(binding) / 'mapping.json', 'user', 'secret')
        config = json.loads(worker.config_path.read_text(encoding='utf-8'))
        config['settle_seconds'] = 0
        worker.config_path.write_text(json.dumps(config), encoding='utf-8')
        with patch('app.engine.Client', return_value=fake), patch('app.engine.ROOT', self.root / 'runtime'):
            worker.start()
            for _ in range(150):
                APP.processEvents()
                if fake.writes: break
                QTest.qWait(10)
            worker.stop_event.set()
            self.assertTrue(worker.wait(3000))
        self.assertEqual(fake.writes, 1)
        self.assertEqual(worker.password, '')
        self.assertEqual(fake.auth, '')

    def test_401_worker_invalidates_login(self):
        binding = self.bind()
        client = Mock()
        client.get.side_effect = engine.AuthenticationExpired('expired')
        worker = Worker(self.window.catalogue.directory(binding) / 'mapping.json', 'user', 'secret')
        config = json.loads(worker.config_path.read_text(encoding='utf-8'))
        config['settle_seconds'] = 0
        worker.config_path.write_text(json.dumps(config), encoding='utf-8')
        received = []
        worker.auth_expired.connect(lambda: received.append(True))
        with patch('app.engine.Client', return_value=client), patch('app.engine.ROOT', self.root / 'runtime'):
            worker.start()
            self.assertTrue(worker.wait(3000))
            APP.processEvents()
        self.assertEqual(received, [True])
        self.assertEqual(client.auth, '')

if __name__ == '__main__':
    unittest.main(verbosity=2)
