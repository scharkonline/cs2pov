"""Job card widget - a single job in the queue."""

from enum import Enum

from PySide6.QtCore import Signal, Qt
from PySide6.QtWidgets import (
    QFrame, QVBoxLayout, QHBoxLayout, QLabel, QComboBox,
    QPushButton, QCheckBox, QSpinBox, QDoubleSpinBox, QWidget,
)

from .file_picker import FilePicker


class JobStatus(Enum):
    IDLE = "idle"
    QUEUED = "queued"
    RUNNING = "running"
    SUCCESS = "success"
    FAILED = "failed"


STATUS_COLORS = {
    JobStatus.IDLE: "#3a3a44",
    JobStatus.QUEUED: "#555560",
    JobStatus.RUNNING: "#4e9aff",
    JobStatus.SUCCESS: "#4CAF50",
    JobStatus.FAILED: "#f44336",
}

RESOLUTIONS = [
    ("1920x1080", (1920, 1080)),
    ("2560x1440", (2560, 1440)),
    ("3840x2160", (3840, 2160)),
    ("1280x720", (1280, 720)),
]

JOB_TYPES = ["pov", "record", "trim", "comms"]


class JobCard(QFrame):
    """A single job card in the queue."""

    remove_requested = Signal(object)  # emits self
    duplicate_requested = Signal(object)  # emits self
    demo_changed = Signal(str)  # emits demo path for cache lookup

    def __init__(self, job_number: int = 1, parent=None):
        super().__init__(parent)
        self._job_number = job_number
        self._status = JobStatus.IDLE
        self._status_message = "Ready"
        self._locked = False

        self.setFrameShape(QFrame.Shape.NoFrame)
        self._build_ui()
        self._on_type_changed()
        self._update_style()

    def _build_ui(self):
        main_layout = QVBoxLayout(self)
        main_layout.setContentsMargins(12, 10, 12, 10)
        main_layout.setSpacing(8)

        # --- Header row: remove btn, job label, spacer, type dropdown ---
        header = QHBoxLayout()
        header.setSpacing(8)

        self._remove_btn = QPushButton("\u00d7")
        self._remove_btn.setFixedSize(24, 24)
        self._remove_btn.setStyleSheet(
            "QPushButton { background: transparent; color: #8c8c96; font-size: 16px; "
            "font-weight: bold; border: none; border-radius: 12px; }"
            "QPushButton:hover { color: #f44336; background: rgba(244,67,54,0.15); }"
        )
        self._remove_btn.clicked.connect(lambda: self.remove_requested.emit(self))
        header.addWidget(self._remove_btn)

        self._job_label = QLabel(f"Job #{self._job_number}")
        self._job_label.setStyleSheet("font-weight: bold; color: #8c8c96; font-size: 13px;")
        header.addWidget(self._job_label)

        header.addStretch()

        self._dup_btn = QPushButton("Duplicate")
        self._dup_btn.setFixedHeight(24)
        self._dup_btn.setStyleSheet(
            "QPushButton { background: transparent; color: #8c8c96; font-size: 11px; "
            "border: 1px solid #3a3a44; border-radius: 4px; padding: 2px 8px; }"
            "QPushButton:hover { color: #dcdcdc; border-color: #555560; }"
        )
        self._dup_btn.clicked.connect(lambda: self.duplicate_requested.emit(self))
        header.addWidget(self._dup_btn)

        type_label = QLabel("Type:")
        type_label.setStyleSheet("color: #8c8c96;")
        header.addWidget(type_label)

        self._type_combo = QComboBox()
        self._type_combo.addItems(JOB_TYPES)
        self._type_combo.setFixedWidth(100)
        self._type_combo.currentTextChanged.connect(self._on_type_changed)
        header.addWidget(self._type_combo)

        main_layout.addLayout(header)

        # --- File pickers ---
        self._demo_picker = FilePicker("Demo", "Demo Files (*.dem)", label_width=70)
        self._demo_picker.path_changed.connect(self._on_demo_changed)
        main_layout.addWidget(self._demo_picker)

        self._output_picker = FilePicker("Output", "Video Files (*.mp4)", save_mode=True, label_width=70)
        main_layout.addWidget(self._output_picker)

        self._video_picker = FilePicker("Video", "Video Files (*.mp4 *.mkv)", label_width=70)
        main_layout.addWidget(self._video_picker)

        self._audio_picker = FilePicker("Audio", "Audio Files (*.mp3 *.wav *.ogg *.m4a)", label_width=70)
        main_layout.addWidget(self._audio_picker)

        # --- Player dropdown ---
        player_row = QHBoxLayout()
        player_row.setContentsMargins(0, 0, 0, 0)
        plbl = QLabel("Player")
        plbl.setFixedWidth(70)
        player_row.addWidget(plbl)
        self._player_combo = QComboBox()
        self._player_combo.setPlaceholderText("Parse a demo first...")
        player_row.addWidget(self._player_combo, 1)
        self._player_container = QWidget()
        self._player_container.setLayout(player_row)
        main_layout.addWidget(self._player_container)

        # --- Recording options (checkboxes) ---
        self._rec_checks_container = QWidget()
        checks_layout = QHBoxLayout(self._rec_checks_container)
        checks_layout.setContentsMargins(0, 0, 0, 0)
        checks_layout.setSpacing(16)

        self._hud_cb = QCheckBox("Hide HUD")
        self._hud_cb.setChecked(True)
        checks_layout.addWidget(self._hud_cb)

        self._tick_nav_cb = QCheckBox("Tick Nav")
        self._tick_nav_cb.setToolTip("Skip death periods during recording")
        checks_layout.addWidget(self._tick_nav_cb)

        self._trim_cb = QCheckBox("Trim")
        self._trim_cb.setChecked(True)
        self._trim_cb.setToolTip("Post-process to remove death periods")
        checks_layout.addWidget(self._trim_cb)

        self._audio_cb = QCheckBox("Audio")
        self._audio_cb.setChecked(True)
        checks_layout.addWidget(self._audio_cb)

        checks_layout.addStretch()
        main_layout.addWidget(self._rec_checks_container)

        # --- Recording settings (resolution, fps, display) ---
        self._rec_settings_container = QWidget()
        settings_layout = QHBoxLayout(self._rec_settings_container)
        settings_layout.setContentsMargins(0, 0, 0, 0)
        settings_layout.setSpacing(8)

        settings_layout.addWidget(QLabel("Resolution"))
        self._res_combo = QComboBox()
        for label, _ in RESOLUTIONS:
            self._res_combo.addItem(label)
        self._res_combo.setFixedWidth(110)
        settings_layout.addWidget(self._res_combo)

        settings_layout.addWidget(QLabel("FPS"))
        self._fps_spin = QSpinBox()
        self._fps_spin.setRange(24, 240)
        self._fps_spin.setValue(60)
        self._fps_spin.setFixedWidth(60)
        settings_layout.addWidget(self._fps_spin)

        settings_layout.addWidget(QLabel("Display"))
        self._display_spin = QSpinBox()
        self._display_spin.setRange(0, 9)
        self._display_spin.setValue(0)
        self._display_spin.setFixedWidth(50)
        settings_layout.addWidget(self._display_spin)

        settings_layout.addStretch()
        main_layout.addWidget(self._rec_settings_container)

        # --- Trim-specific: startup time ---
        self._startup_container = QWidget()
        startup_layout = QHBoxLayout(self._startup_container)
        startup_layout.setContentsMargins(0, 0, 0, 0)
        startup_layout.addWidget(QLabel("Startup time:"))
        self._startup_spin = QDoubleSpinBox()
        self._startup_spin.setRange(0, 9999)
        self._startup_spin.setDecimals(1)
        self._startup_spin.setSpecialValueText("auto")
        self._startup_spin.setValue(0)
        self._startup_spin.setSuffix(" s")
        startup_layout.addWidget(self._startup_spin)

        # Trim-specific tick-nav checkbox (separate from recording tick-nav)
        self._trim_tick_nav_cb = QCheckBox("Tick-nav mode")
        self._trim_tick_nav_cb.setToolTip("Video was recorded with --tick-nav")
        startup_layout.addWidget(self._trim_tick_nav_cb)

        startup_layout.addStretch()
        main_layout.addWidget(self._startup_container)

        # --- Comms-specific: sync time, volumes ---
        self._comms_container = QWidget()
        comms_layout = QHBoxLayout(self._comms_container)
        comms_layout.setContentsMargins(0, 0, 0, 0)
        comms_layout.setSpacing(8)

        comms_layout.addWidget(QLabel("R1 sync time:"))
        self._sync_spin = QDoubleSpinBox()
        self._sync_spin.setRange(0, 9999)
        self._sync_spin.setDecimals(1)
        self._sync_spin.setSuffix(" s")
        self._sync_spin.setFixedWidth(80)
        comms_layout.addWidget(self._sync_spin)

        comms_layout.addWidget(QLabel("Game vol:"))
        self._game_vol_spin = QDoubleSpinBox()
        self._game_vol_spin.setRange(0, 5.0)
        self._game_vol_spin.setDecimals(2)
        self._game_vol_spin.setValue(1.0)
        self._game_vol_spin.setSingleStep(0.1)
        self._game_vol_spin.setFixedWidth(70)
        comms_layout.addWidget(self._game_vol_spin)

        comms_layout.addWidget(QLabel("Comms vol:"))
        self._comms_vol_spin = QDoubleSpinBox()
        self._comms_vol_spin.setRange(0, 5.0)
        self._comms_vol_spin.setDecimals(2)
        self._comms_vol_spin.setValue(1.0)
        self._comms_vol_spin.setSingleStep(0.1)
        self._comms_vol_spin.setFixedWidth(70)
        comms_layout.addWidget(self._comms_vol_spin)

        comms_layout.addStretch()
        main_layout.addWidget(self._comms_container)

        # --- Status label ---
        self._status_label = QLabel("Ready")
        self._status_label.setStyleSheet("color: #8c8c96; font-size: 11px;")
        main_layout.addWidget(self._status_label)

    def _on_type_changed(self):
        """Show/hide fields based on current job type."""
        t = self._type_combo.currentText()

        # Demo picker: all types
        self._demo_picker.setVisible(True)

        # Output picker: all types (optional for trim/comms)
        self._output_picker.setVisible(True)

        # Video picker: trim and comms only
        self._video_picker.setVisible(t in ("trim", "comms"))

        # Audio picker: comms only
        self._audio_picker.setVisible(t == "comms")

        # Player dropdown: all types
        self._player_container.setVisible(True)

        # Recording checkboxes: pov and record
        self._rec_checks_container.setVisible(t in ("pov", "record"))

        # Trim checkbox visibility within rec checks
        self._trim_cb.setVisible(t == "pov")

        # Recording settings: pov and record
        self._rec_settings_container.setVisible(t in ("pov", "record"))

        # Startup time: trim only
        self._startup_container.setVisible(t == "trim")

        # Comms settings: comms only
        self._comms_container.setVisible(t == "comms")

    def _on_demo_changed(self, path: str):
        if path.strip():
            self.demo_changed.emit(path.strip())

    def set_players(self, players):
        """Populate player dropdown from list of PlayerInfo objects."""
        current = self._player_combo.currentText()
        self._player_combo.clear()
        for p in players:
            self._player_combo.addItem(f"{p.name} ({p.steamid})", userData=p)
        # Restore selection if same player exists
        if current:
            idx = self._player_combo.findText(current)
            if idx >= 0:
                self._player_combo.setCurrentIndex(idx)

    def set_job_number(self, num: int):
        self._job_number = num
        self._job_label.setText(f"Job #{num}")

    def set_status(self, status: JobStatus, message: str = ""):
        self._status = status
        self._status_message = message or status.value.capitalize()
        self._status_label.setText(self._status_message)
        self._update_style()

    def set_locked(self, locked: bool):
        """Disable all inputs during execution."""
        self._locked = locked
        self._type_combo.setEnabled(not locked)
        self._demo_picker.set_enabled(not locked)
        self._output_picker.set_enabled(not locked)
        self._video_picker.set_enabled(not locked)
        self._audio_picker.set_enabled(not locked)
        self._player_combo.setEnabled(not locked)
        self._hud_cb.setEnabled(not locked)
        self._tick_nav_cb.setEnabled(not locked)
        self._trim_cb.setEnabled(not locked)
        self._audio_cb.setEnabled(not locked)
        self._res_combo.setEnabled(not locked)
        self._fps_spin.setEnabled(not locked)
        self._display_spin.setEnabled(not locked)
        self._startup_spin.setEnabled(not locked)
        self._trim_tick_nav_cb.setEnabled(not locked)
        self._sync_spin.setEnabled(not locked)
        self._game_vol_spin.setEnabled(not locked)
        self._comms_vol_spin.setEnabled(not locked)
        self._remove_btn.setEnabled(not locked)
        self._dup_btn.setEnabled(not locked)

    def job_type(self) -> str:
        return self._type_combo.currentText()

    def to_job_dict(self) -> dict:
        """Extract configuration dict for batch execution."""
        t = self._type_combo.currentText()
        player_data = self._player_combo.currentData()

        d = {
            "type": t,
            "demo_path": self._demo_picker.path(),
            "output_path": self._output_picker.path(),
            "player": player_data,
            "player_text": self._player_combo.currentText(),
        }

        if t in ("pov", "record"):
            res_idx = self._res_combo.currentIndex()
            d["resolution"] = RESOLUTIONS[res_idx][1]
            d["framerate"] = self._fps_spin.value()
            d["display_num"] = self._display_spin.value()
            d["hide_hud"] = self._hud_cb.isChecked()
            d["enable_audio"] = self._audio_cb.isChecked()
            d["tick_nav"] = self._tick_nav_cb.isChecked()
            if t == "pov":
                d["do_trim"] = self._trim_cb.isChecked()
            else:
                d["do_trim"] = False

        if t == "trim":
            d["video_path"] = self._video_picker.path()
            d["tick_nav"] = self._trim_tick_nav_cb.isChecked()
            startup = self._startup_spin.value()
            d["startup_time"] = startup if startup > 0 else None

        if t == "comms":
            d["video_path"] = self._video_picker.path()
            d["comms_audio_path"] = self._audio_picker.path()
            d["r1_sync_time"] = self._sync_spin.value()
            d["game_volume"] = self._game_vol_spin.value()
            d["comms_volume"] = self._comms_vol_spin.value()

        return d

    def load_from_dict(self, d: dict):
        """Populate card fields from a job dict (for duplication)."""
        idx = self._type_combo.findText(d.get("type", "pov"))
        if idx >= 0:
            self._type_combo.setCurrentIndex(idx)

        if d.get("demo_path"):
            self._demo_picker.set_path(d["demo_path"])
        if d.get("output_path"):
            self._output_picker.set_path(d["output_path"])
        if d.get("video_path"):
            self._video_picker.set_path(d["video_path"])
        if d.get("comms_audio_path"):
            self._audio_picker.set_path(d["comms_audio_path"])

        if d.get("resolution"):
            for i, (_, res) in enumerate(RESOLUTIONS):
                if res == d["resolution"]:
                    self._res_combo.setCurrentIndex(i)
                    break
        if d.get("framerate"):
            self._fps_spin.setValue(d["framerate"])
        if d.get("display_num") is not None:
            self._display_spin.setValue(d["display_num"])
        if "hide_hud" in d:
            self._hud_cb.setChecked(d["hide_hud"])
        if "enable_audio" in d:
            self._audio_cb.setChecked(d["enable_audio"])
        if "tick_nav" in d:
            self._tick_nav_cb.setChecked(d["tick_nav"])
            self._trim_tick_nav_cb.setChecked(d["tick_nav"])
        if "do_trim" in d:
            self._trim_cb.setChecked(d["do_trim"])
        if d.get("startup_time") is not None:
            self._startup_spin.setValue(d["startup_time"])
        if "r1_sync_time" in d:
            self._sync_spin.setValue(d["r1_sync_time"])
        if "game_volume" in d:
            self._game_vol_spin.setValue(d["game_volume"])
        if "comms_volume" in d:
            self._comms_vol_spin.setValue(d["comms_volume"])

    def _update_style(self):
        color = STATUS_COLORS.get(self._status, "#3a3a44")
        self.setStyleSheet(
            f"JobCard {{"
            f"  background: #2a2a32;"
            f"  border-radius: 8px;"
            f"  border-left: 3px solid {color};"
            f"}}"
            f"JobCard:hover {{"
            f"  border-left: 3px solid {color};"
            f"  background: #2e2e38;"
            f"}}"
        )
