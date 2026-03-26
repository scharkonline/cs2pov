"""CS2 POV Recorder GUI - PySide6 frontend."""


def launch_gui():
    """Launch the GUI application.

    Raises ImportError with install instructions if PySide6 is not available.
    """
    try:
        from PySide6 import QtWidgets  # noqa: F401
    except ImportError:
        raise SystemExit(
            "PySide6 is required for the GUI.\n"
            "Install it with: pip install cs2pov[gui]\n"
            "  or: pip install PySide6>=6.5"
        )

    from .app import MainWindow

    app = QtWidgets.QApplication([])
    app.setApplicationName("cs2pov")
    window = MainWindow()
    window.show()
    raise SystemExit(app.exec())
