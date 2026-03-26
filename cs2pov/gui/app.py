"""Main application window."""

import sys

from PySide6.QtGui import QColor, QPalette
from PySide6.QtWidgets import QMainWindow, QTabWidget, QStatusBar

from .pages import POVPage, InfoPage, TrimPage


def _apply_dark_theme(app):
    """Apply a CS2-themed dark palette."""
    palette = QPalette()

    dark = QColor(30, 30, 36)
    mid_dark = QColor(42, 42, 50)
    mid = QColor(58, 58, 68)
    text = QColor(220, 220, 220)
    dim_text = QColor(140, 140, 150)
    accent = QColor(78, 154, 255)  # CS2 blue
    highlight_text = QColor(255, 255, 255)

    palette.setColor(QPalette.ColorRole.Window, dark)
    palette.setColor(QPalette.ColorRole.WindowText, text)
    palette.setColor(QPalette.ColorRole.Base, mid_dark)
    palette.setColor(QPalette.ColorRole.AlternateBase, mid)
    palette.setColor(QPalette.ColorRole.ToolTipBase, mid)
    palette.setColor(QPalette.ColorRole.ToolTipText, text)
    palette.setColor(QPalette.ColorRole.Text, text)
    palette.setColor(QPalette.ColorRole.Button, mid_dark)
    palette.setColor(QPalette.ColorRole.ButtonText, text)
    palette.setColor(QPalette.ColorRole.BrightText, highlight_text)
    palette.setColor(QPalette.ColorRole.Link, accent)
    palette.setColor(QPalette.ColorRole.Highlight, accent)
    palette.setColor(QPalette.ColorRole.HighlightedText, highlight_text)
    palette.setColor(QPalette.ColorRole.PlaceholderText, dim_text)

    # Disabled state
    palette.setColor(QPalette.ColorGroup.Disabled, QPalette.ColorRole.WindowText, dim_text)
    palette.setColor(QPalette.ColorGroup.Disabled, QPalette.ColorRole.Text, dim_text)
    palette.setColor(QPalette.ColorGroup.Disabled, QPalette.ColorRole.ButtonText, dim_text)

    app.setPalette(palette)


class MainWindow(QMainWindow):
    """Main application window with tabbed interface."""

    def __init__(self):
        super().__init__()
        self.setWindowTitle("cs2pov")
        self.setMinimumSize(800, 700)
        self.resize(900, 750)

        from PySide6.QtWidgets import QApplication
        _apply_dark_theme(QApplication.instance())

        tabs = QTabWidget()
        self._pov_page = POVPage()
        self._info_page = InfoPage()
        self._trim_page = TrimPage()

        tabs.addTab(self._pov_page, "POV Recording")
        tabs.addTab(self._info_page, "Demo Info")
        tabs.addTab(self._trim_page, "Trim")

        self.setCentralWidget(tabs)

        self._status_bar = QStatusBar()
        self.setStatusBar(self._status_bar)
        self._status_bar.showMessage("Ready")

        # Platform check: disable recording on non-Linux
        if sys.platform != "linux":
            self._pov_page.set_recording_enabled(False)
            self._status_bar.showMessage("Recording is only available on Linux")

