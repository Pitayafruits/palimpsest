"""Minimal Qt UI; network IO runs off the UI thread."""
import json
import sys
import threading
import urllib.error
from pathlib import Path
from PySide6.QtCore import QThread, Signal, QLockFile, QTimer, QUrl, Qt
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import (QApplication, QWidget, QVBoxLayout, QHBoxLayout,
    QLabel, QComboBox, QPushButton, QLineEdit, QFileDialog, QMessageBox)
from associations import Catalogue, SITE, parse_page
from login import KeychainStore, verify
import sync_engine as engine


def page_url(page):
    return SITE + '/pages/viewpage.action?pageId=' + page


class Worker(QThread):
    message = Signal(str)
    auth_expired = Signal()

    def __init__(self, config_path, username, password):
        super().__init__()
        self.config_path = config_path
        self.username, self.password = username, password
        self.stop_event = threading.Event()

    def run(self):
        sync = client = None
        try:
            config = json.loads(self.config_path.read_text(encoding='utf-8'))
            config['_data_dir'] = str(self.config_path.parent)
            client = engine.Client(config, self.username, self.password)
            self.username = self.password = ''
            sync = engine.Sync(config, client, self.message.emit)
            self.message.emit('正在连接文档…')
            while not self.stop_event.is_set():
                try:
                    sync.tick()
                except (urllib.error.URLError, TimeoutError, OSError):
                    self.message.emit('网络或文件访问失败，30 秒后重试。')
                if self.stop_event.wait(config['poll_seconds']):
                    break
            self.message.emit('同步已停止。')
        except engine.AuthenticationExpired:
            self.auth_expired.emit()
        except Exception as error:
            self.message.emit('同步已停止：' + str(error))
        finally:
            self.username = self.password = ''
            if client:
                client.auth = ''
            if sync:
                sync.close()


class LoginWorker(QThread):
    result = Signal(bool, str)

    def __init__(self, username, password, verifier):
        super().__init__()
        self.username, self.password, self.verifier = username, password, verifier

    def run(self):
        try:
            self.verifier(self.username, self.password)
            self.result.emit(True, '')
        except Exception as error:
            self.result.emit(False, str(error))
        finally:
            self.username = self.password = ''


class Window(QWidget):
    def __init__(self, catalogue, credential_store=None, verifier=verify):
        super().__init__()
        self.catalogue, self.verifier = catalogue, verifier
        self.worker = self.login_worker = None
        self.closing = self.changing = self.pending_logout = False
        self.credentials = self.login_candidate = None
        self.after_login = False
        self.logout_notice = ''
        self.store = credential_store
        self.setWindowTitle('Markdown ↔ Confluence')
        self.resize(780, 530)
        self.setMinimumWidth(640)
        self.setStyleSheet('''
            QWidget { color: #1d1d1f; font-size: 14px; }
            Window { background: #f5f5f7; }
            QWidget#card { background: white; border: 1px solid #e5e5ea; border-radius: 18px; }
            QLineEdit, QComboBox { background: #fafafa; border: 1px solid #d9d9df;
                border-radius: 8px; padding: 8px 10px; min-height: 22px; }
            QLineEdit:focus, QComboBox:focus { border: 1px solid #007aff; }
            QPushButton { background: #f3f3f6; border: 1px solid #e2e2e7;
                border-radius: 8px; padding: 9px 17px; min-height: 22px; }
            QPushButton:hover { background: #e9e9ef; }
            QPushButton#primary { background: #007aff; color: white; border: none; }
            QPushButton#primary:hover { background: #0068de; }
            QPushButton#link { color: #007aff; background: transparent; border: none; padding: 7px; }
            QPushButton:disabled { color: #aaaab2; background: #f0f0f3; border-color: #eeeeef; }
            QLabel#status { color: #6e6e73; font-size: 12px; }
        ''')
        layout = QVBoxLayout(self)
        layout.setContentsMargins(28, 28, 28, 22)
        card = QWidget()
        card.setObjectName('card')
        layout.addWidget(card)
        form = QVBoxLayout(card)
        form.setContentsMargins(26, 24, 26, 24)
        form.setSpacing(10)
        self.page = QLineEdit()
        self.page.setPlaceholderText(SITE + '/pages/viewpage.action?pageId=…')
        self.open_doc = QPushButton('打开文档')
        self.open_doc.setObjectName('link')
        row = QHBoxLayout()
        row.addWidget(self.page, 1)
        row.addWidget(self.open_doc)
        form.addWidget(QLabel('文档链接'))
        form.addLayout(row)
        form.addSpacing(7)
        self.file = QLineEdit()
        self.file.setPlaceholderText('选择本地 Markdown 文件')
        self.browse = QPushButton('选择文件')
        row = QHBoxLayout()
        row.addWidget(self.file, 1)
        row.addWidget(self.browse)
        form.addWidget(QLabel('本地 Markdown'))
        form.addLayout(row)
        form.addSpacing(7)
        self.direction = QComboBox()
        self.direction.addItems(['以本地为准 → 更新网页正文', '以网页为准 → 更新本地 Markdown'])
        form.addWidget(QLabel('首次同步方向'))
        form.addWidget(self.direction)
        form.addSpacing(10)
        self.login_form = QWidget()
        login_layout = QHBoxLayout(self.login_form)
        login_layout.setContentsMargins(0, 0, 0, 0)
        self.user = QLineEdit(catalogue.data.get('username', ''))
        self.user.setPlaceholderText('Confluence 账号')
        self.password = QLineEdit()
        self.password.setPlaceholderText('密码')
        self.password.setEchoMode(QLineEdit.EchoMode.Password)
        for field in (self.user, self.password):
            field.setMinimumHeight(42)
            field.setTextMargins(1, 2, 1, 4)
            login_layout.addWidget(field, 1)
        self.login_button = QPushButton('登录')
        login_layout.addWidget(self.login_button)
        form.addWidget(self.login_form)
        self.account_row = QWidget()
        account_layout = QHBoxLayout(self.account_row)
        account_layout.setContentsMargins(0, 0, 0, 0)
        self.account = QLabel()
        self.account.setTextFormat(Qt.TextFormat.PlainText)
        self.logout_button = QPushButton('退出登录')
        self.logout_button.setObjectName('link')
        account_layout.addWidget(self.account, 1)
        account_layout.addWidget(self.logout_button)
        form.addWidget(self.account_row)
        row = QHBoxLayout()
        row.addStretch()
        self.stop = QPushButton('停止同步')
        self.start = QPushButton('开始同步')
        self.start.setObjectName('primary')
        row.addWidget(self.stop)
        row.addWidget(self.start)
        layout.addSpacing(10)
        layout.addLayout(row)
        self.status = QLabel()
        self.status.setObjectName('status')
        self.status.setWordWrap(True)
        layout.addWidget(self.status)
        layout.addStretch()
        self.page.textChanged.connect(self.page_changed)
        self.page.editingFinished.connect(self.save_draft)
        self.file.editingFinished.connect(self.save_draft)
        self.direction.currentIndexChanged.connect(self.save_draft)
        self.browse.clicked.connect(self.choose)
        self.open_doc.clicked.connect(self.open_document)
        self.start.clicked.connect(self.begin)
        self.stop.clicked.connect(self.request_stop)
        self.login_button.clicked.connect(self.login)
        self.password.returnPressed.connect(self.login)
        self.logout_button.clicked.connect(self.logout)
        binding = next((b for b in catalogue.bindings if b['id'] == catalogue.data.get('last_id')), None)
        if binding:
            self.load_binding(binding)
        self.restore_login()
        self.update_controls()

    def tell(self, text):
        self.status.setText(text)

    def restore_login(self):
        try:
            if self.store is None:
                self.store = KeychainStore()
            self.credentials = self.store.load()
        except Exception as error:
            self.tell(str(error))

    def load_binding(self, binding):
        self.changing = True
        try:
            self.page.setText(page_url(binding['page_id']))
            self.file.setText(binding['local_file'])
            self.direction.setCurrentIndex(1 if binding['initial_sync'] == 'remote' else 0)
        finally:
            self.changing = False

    def page_changed(self):
        if self.changing:
            return
        try:
            page = parse_page(self.page.text())
        except ValueError:
            self.update_controls()
            return
        binding = next((b for b in self.catalogue.bindings if b['page_id'] == page), None)
        self.changing = True
        try:
            self.file.setText(binding['local_file'] if binding else '')
            self.direction.setCurrentIndex(1 if binding and binding['initial_sync'] == 'remote' else 0)
        finally:
            self.changing = False
        self.update_controls()

    def current_binding(self):
        try:
            page = parse_page(self.page.text())
            path = str(Path(self.file.text()).expanduser().resolve())
            return next((b for b in self.catalogue.bindings
                if b['page_id'] == page and b['local_file'] == path), None)
        except (ValueError, OSError):
            return None

    def save_binding(self):
        binding = self.catalogue.save(self.page.text(), self.file.text(),
            'remote' if self.direction.currentIndex() else 'local',
            self.credentials[0] if self.credentials else self.user.text())
        self.load_binding(binding)
        self.update_controls()
        return binding

    def save_draft(self):
        if self.changing or self.worker:
            return
        try:
            self.save_binding()
        except ValueError:
            pass
        except Exception as error:
            self.tell('保存关联失败：' + str(error))

    def choose(self):
        path, _ = QFileDialog.getOpenFileName(self, '选择 Markdown', self.file.text(), 'Markdown (*.md *.MD)')
        if path:
            self.file.setText(path)
            self.save_draft()

    def open_document(self):
        try:
            if not QDesktopServices.openUrl(QUrl(page_url(parse_page(self.page.text())))):
                self.tell('无法打开默认浏览器。')
        except ValueError as error:
            self.tell(str(error))

    def update_controls(self):
        busy = self.worker is not None or self.login_worker is not None
        for control in (self.page, self.file, self.browse, self.user, self.password, self.start, self.login_button):
            control.setEnabled(not busy)
        binding = self.current_binding()
        baseline = binding and (self.catalogue.directory(binding) / 'state.json').exists()
        self.direction.setEnabled(not busy and not baseline)
        self.stop.setEnabled(self.worker is not None and not self.pending_logout)
        self.logout_button.setEnabled(not self.pending_logout)
        self.login_form.setVisible(self.credentials is None)
        self.account_row.setVisible(self.credentials is not None)
        self.account.setText(self.credentials[0] if self.credentials else '')

    def login(self, checked=False, continue_sync=False):
        if self.login_worker or self.worker or self.credentials:
            return
        username, password = self.user.text().strip(), self.password.text()
        if not username or not password:
            self.tell('请输入账号和密码。')
            return
        if ':' in username:
            self.tell('账号不能包含冒号。')
            return
        self.after_login = continue_sync
        self.login_candidate = (username, password)
        self.login_worker = LoginWorker(username, password, self.verifier)
        self.login_worker.result.connect(self.login_result)
        self.login_worker.finished.connect(self.login_finished)
        self.password.clear()
        self.tell('正在登录…')
        self.update_controls()
        self.login_worker.start()

    def login_result(self, success, message):
        if success and not self.closing:
            self.credentials = self.login_candidate
            try:
                if self.store is None:
                    self.store = KeychainStore()
                self.store.save(*self.credentials)
                self.tell('已登录。')
            except Exception as error:
                self.tell('已登录，本次无法记住登录：' + str(error))
        else:
            self.after_login = False
            self.tell(message)
        self.login_candidate = None

    def login_finished(self):
        old, self.login_worker = self.login_worker, None
        old.deleteLater()
        self.update_controls()
        if self.closing:
            QTimer.singleShot(0, self.close)
        elif self.after_login and self.credentials:
            self.after_login = False
            self.begin()

    def logout(self):
        try:
            if self.store:
                self.store.clear()
        except Exception as error:
            self.tell('退出登录失败：' + str(error))
            return
        self.pending_logout = True
        if self.worker:
            self.request_stop()
            self.update_controls()
        else:
            self.complete_logout()

    def complete_logout(self):
        self.credentials = self.login_candidate = None
        self.password.clear()
        self.pending_logout = False
        self.tell(self.logout_notice or '已退出登录。')
        self.logout_notice = ''
        self.update_controls()

    def authentication_expired(self):
        self.logout_notice = '登录已失效，请重新登录。'
        try:
            if self.store:
                self.store.clear()
        except Exception:
            self.logout_notice += ' 钥匙串未能清除，请在系统钥匙串中删除此工具的登录记录。'
        self.pending_logout = True
        if self.worker:
            self.request_stop()
            self.update_controls()
        else:
            self.complete_logout()

    def begin(self):
        if self.worker or self.login_worker:
            return
        try:
            binding = self.save_binding()
            if not self.credentials:
                self.login(continue_sync=True)
                return
            directory = self.catalogue.directory(binding)
            if not (directory / 'state.json').exists():
                effect = '本地 Markdown 将替换网页正文。' if binding['initial_sync'] == 'local' else '网页正文将替换本地 Markdown。'
                answer = QMessageBox.question(self, '确认首次同步方向',
                    '页面：' + page_url(binding['page_id']) + '\n文件：' + binding['local_file']
                    + '\n\n' + effect + '\n覆盖前会备份原文，页面标题不变。',
                    QMessageBox.StandardButton.Ok | QMessageBox.StandardButton.Cancel,
                    QMessageBox.StandardButton.Cancel)
                if answer != QMessageBox.StandardButton.Ok:
                    return
            self.worker = Worker(directory / 'mapping.json', *self.credentials)
            self.worker.message.connect(self.tell)
            self.worker.auth_expired.connect(self.authentication_expired)
            self.worker.finished.connect(self.finished)
            self.update_controls()
            self.worker.start()
        except Exception as error:
            self.tell(str(error))

    def request_stop(self):
        if self.worker:
            self.worker.stop_event.set()
            self.stop.setEnabled(False)
            self.tell('正在停止，等待当前读写完成…')

    def finished(self):
        old, self.worker = self.worker, None
        old.deleteLater()
        if self.pending_logout:
            self.complete_logout()
        self.update_controls()
        if self.closing:
            QTimer.singleShot(0, self.close)

    def closeEvent(self, event):
        if self.worker or self.login_worker:
            self.closing = True
            self.request_stop()
            event.ignore()
        else:
            self.credentials = self.login_candidate = None
            event.accept()


def main():
    app = QApplication.instance() or QApplication(sys.argv)
    app.setApplicationName('Palimpsest')
    app.setOrganizationName('Palimpsest')
    engine.ROOT.mkdir(parents=True, exist_ok=True)
    lock = QLockFile(str(engine.ROOT / 'app.lock'))
    if not lock.tryLock(0):
        QMessageBox.information(None, '文档同步', '同步窗口已打开，请切换到已有窗口。')
        return 1
    try:
        window = Window(Catalogue(engine.ROOT))
    except Exception as error:
        QMessageBox.critical(None, '无法加载关联', str(error))
        return 1
    window.show()
    result = app.exec()
    lock.unlock()
    return result


if __name__ == '__main__':
    sys.exit(main())
