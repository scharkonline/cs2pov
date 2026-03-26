"""Read-only log console widget."""

from PySide6.QtWidgets import QTextEdit
from PySide6.QtGui import QFont, QTextCursor


class LogConsole(QTextEdit):
    """Read-only text area that shows log output, auto-scrolling to bottom."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setReadOnly(True)
        self.setFont(QFont("Monospace", 9))
        self.setMinimumHeight(120)

    def append_line(self, text: str):
        """Append a line and scroll to bottom."""
        self.moveCursor(QTextCursor.MoveOperation.End)
        self.insertPlainText(text + "\n")
        self.moveCursor(QTextCursor.MoveOperation.End)
