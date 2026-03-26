"""Trim page - post-process existing recordings."""

from pathlib import Path

from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QPushButton,
    QLabel, QLineEdit, QCheckBox, QDoubleSpinBox,
)

from ..widgets import FilePicker, LogConsole
from ..workers import TrimWorker


class TrimPage(QWidget):
    """Tab for trimming death periods from existing recordings."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self._worker = None

        layout = QVBoxLayout(self)

        # File pickers
        self._video_picker = FilePicker("Video File", "Video Files (*.mp4 *.mkv)")
        layout.addWidget(self._video_picker)

        self._demo_picker = FilePicker("Demo File", "Demo Files (*.dem)")
        layout.addWidget(self._demo_picker)

        # Player input
        player_row = QHBoxLayout()
        lbl = QLabel("Player")
        lbl.setFixedWidth(100)
        player_row.addWidget(lbl)
        self._player_edit = QLineEdit()
        self._player_edit.setPlaceholderText("Player name or SteamID")
        player_row.addWidget(self._player_edit)
        layout.addLayout(player_row)

        # Output path
        self._output_picker = FilePicker("Output", "Video Files (*.mp4)", save_mode=True)
        layout.addWidget(self._output_picker)

        # Options row
        opts = QHBoxLayout()
        self._tick_nav_cb = QCheckBox("Tick-nav mode")
        self._tick_nav_cb.setToolTip("Video was recorded with --tick-nav (changes trim strategy)")
        opts.addWidget(self._tick_nav_cb)

        opts.addWidget(QLabel("Startup time override:"))
        self._startup_spin = QDoubleSpinBox()
        self._startup_spin.setRange(0, 9999)
        self._startup_spin.setDecimals(1)
        self._startup_spin.setSpecialValueText("auto")
        self._startup_spin.setValue(0)
        self._startup_spin.setSuffix(" s")
        opts.addWidget(self._startup_spin)
        opts.addStretch()
        layout.addLayout(opts)

        # Trim button
        self._trim_btn = QPushButton("Trim Video")
        self._trim_btn.clicked.connect(self._on_trim)
        layout.addWidget(self._trim_btn)

        # Log console
        self._log = LogConsole()
        layout.addWidget(self._log, 1)

    def _on_trim(self):
        video = self._video_picker.path()
        demo = self._demo_picker.path()
        player = self._player_edit.text().strip()

        if not video or not demo or not player:
            self._log.append_line("Please fill in video file, demo file, and player.")
            return

        video_path = Path(video).resolve()
        demo_path = Path(demo).resolve()

        if not video_path.exists():
            self._log.append_line(f"Video not found: {video_path}")
            return
        if not demo_path.exists():
            self._log.append_line(f"Demo not found: {demo_path}")
            return

        output = self._output_picker.path()
        output_path = Path(output).resolve() if output else None

        startup_time = self._startup_spin.value() if self._startup_spin.value() > 0 else None

        self._set_busy(True)
        self._log.clear()

        self._worker = TrimWorker(
            video_path=video_path,
            demo_path=demo_path,
            player_identifier=player,
            output_path=output_path,
            tick_nav=self._tick_nav_cb.isChecked(),
            startup_time=startup_time,
        )
        self._worker.message.connect(self._log.append_line)
        self._worker.finished.connect(self._on_done)
        self._worker.error.connect(self._on_error)
        self._worker.start()

    def _on_done(self, result):
        if result:
            path, segments = result
            self._log.append_line(f"Trimmed video saved: {path}")
        self._set_busy(False)

    def _on_error(self, msg):
        self._log.append_line(f"Error: {msg}")
        self._set_busy(False)

    def _set_busy(self, busy):
        self._trim_btn.setEnabled(not busy)
        self._trim_btn.setText("Trimming..." if busy else "Trim Video")
        self._video_picker.set_enabled(not busy)
        self._demo_picker.set_enabled(not busy)
        self._output_picker.set_enabled(not busy)
        self._player_edit.setEnabled(not busy)
