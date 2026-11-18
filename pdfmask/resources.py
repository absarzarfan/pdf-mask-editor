"""Locate bundled assets both from source and from a PyInstaller build."""
import sys
from pathlib import Path

from PySide6.QtGui import QFontDatabase, QIcon


def asset_dir() -> Path:
    base = getattr(sys, "_MEIPASS", None)
    if base:
        return Path(base) / "assets"
    return Path(__file__).resolve().parent.parent / "assets"


def load_fonts() -> None:
    for ttf in (asset_dir() / "fonts").glob("*.ttf"):
        QFontDatabase.addApplicationFont(str(ttf))


def app_icon() -> QIcon:
    for name in ("icon.ico", "icon.png"):
        p = asset_dir() / name
        if p.exists():
            return QIcon(str(p))
    return QIcon()
