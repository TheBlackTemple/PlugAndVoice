"""
gui — PySide6 front-end for PlugAndVoice.

Public surface:
  launch()           — create QApplication, apply stylesheet, show MainWindow.
  MainWindow         — the main application window.
  SettingsView       — settings / entry-point dialog.
  DbGauge            — reusable dB bar widget.
  STYLESHEET         — global dark theme stylesheet string.
"""

from .styles import DbGauge, STYLESHEET
from .settings_view import SettingsView
from .main_window import MainWindow
from utils.paths import asset_path

def launch() -> None:
    """
    Create and run the application.
    Blocks until the window is closed.
    """
    import sys
    from PySide6.QtWidgets import QApplication
    from PySide6.QtGui import QIcon

    app = QApplication.instance() or QApplication(sys.argv)
    app.setApplicationName("PlugAndVoice")
    app.setStyleSheet(STYLESHEET)
    app.setWindowIcon(QIcon(asset_path("resources", "icon.ico")))

    # Prevent Qt from quitting the event loop when the window is hidden to tray
    app.setQuitOnLastWindowClosed(False)
    
    window = MainWindow()
    window.show()

    sys.exit(app.exec())


__all__ = [
    "launch",
    "MainWindow",
    "SettingsView",
    "DbGauge",
    "STYLESHEET",
]
