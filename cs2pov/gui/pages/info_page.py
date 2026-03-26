"""Demo Info page - parse and display demo metadata."""

from pathlib import Path

from PySide6.QtWidgets import QWidget, QVBoxLayout, QPushButton

from ..widgets import FilePicker, PlayerTable, DemoHeader, LogConsole
from ..workers import ParseWorker


class InfoPage(QWidget):
    """Tab for viewing demo information."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self._worker = None

        layout = QVBoxLayout(self)

        # File picker
        self._demo_picker = FilePicker("Demo File", "Demo Files (*.dem)")
        layout.addWidget(self._demo_picker)

        # Parse button
        self._parse_btn = QPushButton("Parse Demo")
        self._parse_btn.clicked.connect(self._on_parse)
        layout.addWidget(self._parse_btn)

        # Demo header
        self._demo_header = DemoHeader()
        layout.addWidget(self._demo_header)

        # Player table (non-selectable, shows all players)
        self._player_table = PlayerTable(selectable=False)
        layout.addWidget(self._player_table, 1)

        # Log console
        self._log = LogConsole()
        layout.addWidget(self._log)

    def _on_parse(self):
        path = self._demo_picker.path()
        if not path:
            self._log.append_line("No demo file selected.")
            return

        demo_path = Path(path).resolve()
        if not demo_path.exists():
            self._log.append_line(f"File not found: {demo_path}")
            return

        self._set_busy(True)
        self._log.clear()
        self._demo_header.clear()
        self._player_table.clear()

        self._worker = ParseWorker(demo_path)
        self._worker.message.connect(self._log.append_line)
        self._worker.finished.connect(self._on_parse_done)
        self._worker.error.connect(self._on_error)
        self._worker.start()

    def _on_parse_done(self, result):
        demo_info, _ = result
        self._demo_header.set_info(demo_info)
        self._player_table.set_players(demo_info.players)
        self._log.append_line(f"Found {len(demo_info.players)} players.")
        self._set_busy(False)

    def _on_error(self, msg):
        self._log.append_line(f"Error: {msg}")
        self._set_busy(False)

    def _set_busy(self, busy):
        self._parse_btn.setEnabled(not busy)
        self._parse_btn.setText("Parsing..." if busy else "Parse Demo")
        self._demo_picker.set_enabled(not busy)
