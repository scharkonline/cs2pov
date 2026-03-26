"""Demo metadata display widget."""

from PySide6.QtWidgets import QWidget, QHBoxLayout, QLabel


class DemoHeader(QWidget):
    """Displays demo metadata: map, tickrate, duration, ticks."""

    def __init__(self, parent=None):
        super().__init__(parent)
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)

        self._map_label = QLabel()
        self._tickrate_label = QLabel()
        self._ticks_label = QLabel()
        self._duration_label = QLabel()

        for label in (self._map_label, self._tickrate_label, self._ticks_label, self._duration_label):
            label.setStyleSheet("font-weight: bold; padding: 4px 8px;")
            layout.addWidget(label)

        layout.addStretch()
        self.clear()

    def set_info(self, demo_info):
        """Update from a DemoInfo dataclass.

        Args:
            demo_info: parser.DemoInfo instance
        """
        self._map_label.setText(f"Map: {demo_info.map_name}")
        self._tickrate_label.setText(f"Tickrate: {demo_info.tick_rate:.0f}")
        self._ticks_label.setText(f"Ticks: {demo_info.total_ticks}")
        if demo_info.total_ticks and demo_info.tick_rate:
            duration = demo_info.total_ticks / demo_info.tick_rate
            mins, secs = divmod(int(duration), 60)
            self._duration_label.setText(f"Duration: {mins}:{secs:02d}")
        else:
            self._duration_label.setText("Duration: N/A")

    def clear(self):
        for label in (self._map_label, self._tickrate_label, self._ticks_label, self._duration_label):
            label.setText("")
