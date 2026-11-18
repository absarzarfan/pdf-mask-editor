"""Application entry point."""
import sys

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QApplication

from . import APP_NAME, APP_VERSION
from .resources import app_icon, asset_dir, load_fonts
from .theme import stylesheet


def main() -> int:
    QApplication.setHighDpiScaleFactorRoundingPolicy(Qt.HighDpiScaleFactorRoundingPolicy.PassThrough)
    app = QApplication(sys.argv)
    app.setApplicationName(APP_NAME)
    app.setApplicationVersion(APP_VERSION)
    app.setOrganizationName("absarz software")
    app.setWindowIcon(app_icon())
    load_fonts()
    app.setStyle("Fusion")
    app.setStyleSheet(stylesheet(asset_dir().as_posix()))

    from .main_window import MainWindow
    from .splash import Splash

    window = MainWindow()
    splash = Splash()
    splash.finished.connect(window.show)
    splash.start()
    return app.exec()


if __name__ == "__main__":
    sys.exit(main())
