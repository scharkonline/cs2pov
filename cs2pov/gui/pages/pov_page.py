"""POV Recording page - full recording workflow."""

import sys
from pathlib import Path

from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QPushButton,
    QComboBox, QCheckBox, QLabel, QGroupBox, QSpinBox,
)

from ..widgets import FilePicker, PlayerTable, DemoHeader, LogConsole
from ..workers import ParseWorker, RecordWorker


RESOLUTIONS = [
    ("1920x1080", (1920, 1080)),
    ("2560x1440", (2560, 1440)),
    ("3840x2160", (3840, 2160)),
    ("1280x720", (1280, 720)),
]


class POVPage(QWidget):
    """Tab for the full POV recording workflow."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self._worker = None
        self._demo_info = None
        self._recording_disabled = False

        layout = QVBoxLayout(self)

        # --- File pickers ---
        self._demo_picker = FilePicker("Demo File", "Demo Files (*.dem)")
        layout.addWidget(self._demo_picker)

        self._output_picker = FilePicker("Output", "Video Files (*.mp4)", save_mode=True)
        self._output_picker.path_changed.connect(lambda _: self._update_record_btn())
        layout.addWidget(self._output_picker)

        # --- Parse button ---
        self._parse_btn = QPushButton("Parse Demo")
        self._parse_btn.clicked.connect(self._on_parse)
        layout.addWidget(self._parse_btn)

        # --- Demo header ---
        self._demo_header = DemoHeader()
        layout.addWidget(self._demo_header)

        # --- Player table ---
        self._player_table = PlayerTable(selectable=True)
        self._player_table.player_selected.connect(self._on_player_selected)
        layout.addWidget(self._player_table, 1)

        # --- Settings ---
        settings_box = QGroupBox("Recording Settings")
        settings_layout = QVBoxLayout(settings_box)

        # Row 1: resolution, framerate, display
        row1 = QHBoxLayout()
        row1.addWidget(QLabel("Resolution:"))
        self._res_combo = QComboBox()
        for label, _ in RESOLUTIONS:
            self._res_combo.addItem(label)
        row1.addWidget(self._res_combo)

        row1.addWidget(QLabel("Framerate:"))
        self._fps_spin = QSpinBox()
        self._fps_spin.setRange(24, 240)
        self._fps_spin.setValue(60)
        row1.addWidget(self._fps_spin)

        row1.addWidget(QLabel("Display:"))
        self._display_spin = QSpinBox()
        self._display_spin.setRange(0, 9)
        self._display_spin.setValue(0)
        row1.addWidget(self._display_spin)

        row1.addStretch()
        settings_layout.addLayout(row1)

        # Row 2: checkboxes
        row2 = QHBoxLayout()
        self._hud_cb = QCheckBox("Hide HUD")
        self._hud_cb.setChecked(True)
        row2.addWidget(self._hud_cb)

        self._tick_nav_cb = QCheckBox("Tick Navigation")
        self._tick_nav_cb.setToolTip("Skip death periods during recording (real-time)")
        row2.addWidget(self._tick_nav_cb)

        self._trim_cb = QCheckBox("Trim Deaths")
        self._trim_cb.setChecked(True)
        self._trim_cb.setToolTip("Post-process to remove death periods from video")
        row2.addWidget(self._trim_cb)

        self._audio_cb = QCheckBox("Record Audio")
        self._audio_cb.setChecked(True)
        row2.addWidget(self._audio_cb)

        row2.addStretch()
        settings_layout.addLayout(row2)

        layout.addWidget(settings_box)

        # --- Record button ---
        self._record_btn = QPushButton("Record POV")
        self._record_btn.setEnabled(False)
        self._record_btn.setMinimumHeight(40)
        self._record_btn.setStyleSheet(
            "QPushButton:enabled { background-color: #4e9aff; color: white; font-weight: bold; font-size: 14px; }"
        )
        self._record_btn.clicked.connect(self._on_record)
        layout.addWidget(self._record_btn)

        # --- Log console ---
        self._log = LogConsole()
        layout.addWidget(self._log)

        self._selected_player_row = -1

    def set_recording_enabled(self, enabled: bool):
        """Enable/disable recording capability (platform guard)."""
        self._recording_disabled = not enabled
        if not enabled:
            self._record_btn.setEnabled(False)
            self._record_btn.setToolTip("Recording is only available on Linux")

    def _on_parse(self):
        path = self._demo_picker.path()
        if not path:
            self._log.append_line("No demo file selected.")
            return

        demo_path = Path(path).resolve()
        if not demo_path.exists():
            self._log.append_line(f"File not found: {demo_path}")
            return

        self._set_busy(True, "parse")
        self._log.clear()
        self._demo_header.clear()
        self._player_table.clear()
        self._demo_info = None
        self._selected_player_row = -1
        self._update_record_btn()

        self._worker = ParseWorker(demo_path)
        self._worker.message.connect(self._log.append_line)
        self._worker.finished.connect(self._on_parse_done)
        self._worker.error.connect(self._on_error)
        self._worker.start()

    def _on_parse_done(self, result):
        demo_info, _ = result
        self._demo_info = demo_info
        self._demo_header.set_info(demo_info)
        self._player_table.set_players(demo_info.players)
        self._log.append_line(f"Select a player to record ({len(demo_info.players)} found).")
        self._set_busy(False, "parse")

    def _on_player_selected(self, row):
        self._selected_player_row = row
        if self._demo_info and 0 <= row < len(self._demo_info.players):
            p = self._demo_info.players[row]
            self._log.append_line(f"Selected: {p.name} ({p.steamid})")
        self._update_record_btn()

    def _update_record_btn(self):
        if self._recording_disabled:
            return
        can_record = (
            self._demo_info is not None
            and self._selected_player_row >= 0
            and bool(self._output_picker.path())
            and self._worker is None
        )
        self._record_btn.setEnabled(can_record)

    def _on_record(self):
        if not self._demo_info or self._selected_player_row < 0:
            return

        player = self._demo_info.players[self._selected_player_row]
        demo_path = Path(self._demo_picker.path()).resolve()
        output_path = Path(self._output_picker.path()).resolve()

        res_idx = self._res_combo.currentIndex()
        resolution = RESOLUTIONS[res_idx][1]

        self._set_busy(True, "record")
        self._log.clear()
        self._log.append_line(f"Starting recording: {player.name} on {self._demo_info.map_name}")

        self._worker = RecordWorker(
            demo_path=demo_path,
            player_identifier=str(player.steamid),
            output_path=output_path,
            resolution=resolution,
            framerate=self._fps_spin.value(),
            hide_hud=self._hud_cb.isChecked(),
            display_num=self._display_spin.value(),
            enable_audio=self._audio_cb.isChecked(),
            tick_nav=self._tick_nav_cb.isChecked(),
            do_trim=self._trim_cb.isChecked(),
        )
        self._worker.message.connect(self._log.append_line)
        self._worker.finished.connect(self._on_record_done)
        self._worker.error.connect(self._on_error)
        self._worker.start()

    def _on_record_done(self, result):
        if result and result.success:
            self._log.append_line(f"\nRecording complete: {result.video_path}")
            size_mb = result.video_path.stat().st_size / (1024 * 1024)
            self._log.append_line(f"Size: {size_mb:.1f} MB")
        elif result:
            self._log.append_line(f"\nRecording ended: {result.exit_reason}")
        self._set_busy(False, "record")

    def _on_error(self, msg):
        self._log.append_line(f"Error: {msg}")
        self._set_busy(False, "parse")

    def _set_busy(self, busy, mode="parse"):
        if busy:
            self._parse_btn.setEnabled(False)
            self._record_btn.setEnabled(False)
            self._demo_picker.set_enabled(False)
            self._output_picker.set_enabled(False)
            if mode == "parse":
                self._parse_btn.setText("Parsing...")
            else:
                self._record_btn.setText("Recording...")
        else:
            self._worker = None
            self._parse_btn.setEnabled(True)
            self._parse_btn.setText("Parse Demo")
            self._record_btn.setText("Record POV")
            self._demo_picker.set_enabled(True)
            self._output_picker.set_enabled(True)
            self._update_record_btn()
