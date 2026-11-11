"""First-run endpoint setup, then the desktop app. Frozen macOS entrypoint."""
import sys
from PySide6.QtWidgets import QApplication, QInputDialog, QMessageBox
from server import configured_site, configure_site


def main():
    application = QApplication.instance() or QApplication(sys.argv)
    application.setApplicationName('Palimpsest')
    from branding import apply_icon
    apply_icon(application)
    if '--review-smoke-test' in sys.argv:
        from review_gui import smoke_test
        return smoke_test()
    while True:
        try:
            if configured_site():
                break
            value, accepted = QInputDialog.getText(None, 'Palimpsest · 连接站点',
                'Confluence Server / Data Center 的 HTTPS 地址：\n例如 https://wiki.example.com 或 https://wiki.example.com/wiki')
            if not accepted:
                return 0
            configure_site(value)
            break
        except Exception as error:
            QMessageBox.warning(None, '无法配置站点', str(error))
            return 1
    from app import main as run
    return run()


if __name__ == '__main__':
    sys.exit(main())
