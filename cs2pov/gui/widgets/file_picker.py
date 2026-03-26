"""File picker widget - label + path display + Browse button."""

from pathlib import Path

from PySide6.QtCore import Signal
from PySide6.QtWidgets import QWidget, QHBoxLayout, QLabel, QLineEdit, QPushButton, QFileDialog


class FilePicker(QWidget):
    """Row widget: label | path text field | Browse button."""

    path_changed = Signal(str)

    def __init__(
        self,
        label: str,
        filter_str: str = "",
        save_mode: bool = False,
        directory_mode: bool = False,
        parent=None,
    ):
        super().__init__(parent)
        self._filter_str = filter_str
        self._save_mode = save_mode
        self._directory_mode = directory_mode

        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)

        self._label = QLabel(label)
        self._label.setFixedWidth(100)
        layout.addWidget(self._label)

        self._line_edit = QLineEdit()
        self._line_edit.setPlaceholderText("No file selected")
        self._line_edit.textChanged.connect(self.path_changed.emit)
        layout.addWidget(self._line_edit, 1)

        self._browse_btn = QPushButton("Browse")
        self._browse_btn.setFixedWidth(80)
        self._browse_btn.clicked.connect(self._browse)
        layout.addWidget(self._browse_btn)

    def _browse(self):
        if self._directory_mode:
            path = QFileDialog.getExistingDirectory(self, "Select Directory")
        elif self._save_mode:
            path, _ = QFileDialog.getSaveFileName(self, "Save File", "", self._filter_str)
        else:
            path, _ = QFileDialog.getOpenFileName(self, "Open File", "", self._filter_str)

        if path:
            self._line_edit.setText(path)

    def path(self) -> str:
        return self._line_edit.text().strip()

    def set_path(self, path: str):
        self._line_edit.setText(path)

    def set_enabled(self, enabled: bool):
        self._line_edit.setEnabled(enabled)
        self._browse_btn.setEnabled(enabled)
