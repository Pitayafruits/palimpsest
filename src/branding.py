"""Shared application icon for source runs and frozen desktop packages."""
from pathlib import Path
import sys
from PySide6.QtGui import QIcon


def apply_icon(application):
    root = Path(getattr(sys, '_MEIPASS', Path(__file__).resolve().parents[1]))
    path = root / 'assets/icons/palimpsest-v1.png'
    if path.is_file():
        application.setWindowIcon(QIcon(str(path)))
