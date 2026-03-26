"""Main application window."""

import sys

from PySide6.QtCore import Qt
from PySide6.QtGui import QColor, QPalette
from PySide6.QtWidgets import QMainWindow, QSplitter, QStatusBar, QApplication

from .job_queue import JobQueuePage
from .widgets.log_console import LogConsole
from .widgets.demo_cache import DemoCache
from .widgets.job_card import JobStatus
from .workers import CLIJobRunner


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
    """Main application window with job queue and log console."""

    def __init__(self):
        super().__init__()
        self.setWindowTitle("cs2pov")
        self.setMinimumSize(800, 700)
        self.resize(900, 750)

        _apply_dark_theme(QApplication.instance())

        self._runner = None

        # Shared demo cache
        self._demo_cache = DemoCache(self)

        # Splitter: job queue (top) + log console (bottom)
        splitter = QSplitter(Qt.Orientation.Vertical)

        self._job_queue = JobQueuePage(demo_cache=self._demo_cache)
        splitter.addWidget(self._job_queue)

        self._log = LogConsole()
        splitter.addWidget(self._log)

        # ~70/30 ratio
        splitter.setSizes([500, 200])
        splitter.setHandleWidth(4)
        splitter.setStyleSheet(
            "QSplitter::handle { background: #3a3a44; }"
            "QSplitter::handle:hover { background: #4e9aff; }"
        )

        self.setCentralWidget(splitter)

        self._status_bar = QStatusBar()
        self.setStatusBar(self._status_bar)
        self._status_bar.showMessage("Ready")

        # Wire up signals
        self._demo_cache.parse_complete.connect(self._on_parse_complete)
        self._demo_cache.parse_error.connect(self._on_parse_error)
        self._demo_cache.parse_started.connect(
            lambda p: self._log.append_line(f"Parsing: {p.split('/')[-1]}...")
        )

        self._job_queue.start_requested.connect(self._on_start_jobs)

        # Platform check
        if sys.platform != "linux":
            self._status_bar.showMessage("Recording is only available on Linux (trim/comms still work)")

    def _on_parse_complete(self, path: str, demo_info):
        self._log.append_line(
            f"Parsed: {demo_info.map_name}, {len(demo_info.players)} players"
        )
        self._job_queue.propagate_players(path, demo_info)

    def _on_parse_error(self, path: str, msg: str):
        self._log.append_line(f"Parse error ({path.split('/')[-1]}): {msg}")

    def _on_start_jobs(self, jobs: list):
        if not jobs or self._runner is not None:
            return

        # Validate jobs
        for i, job in enumerate(jobs):
            if not job.get("demo_path"):
                self._log.append_line(f"Job {i + 1}: No demo file selected.")
                return
            if not job.get("player"):
                self._log.append_line(f"Job {i + 1}: No player selected.")
                return
            if job["type"] in ("pov", "record") and not job.get("output_path"):
                self._log.append_line(f"Job {i + 1}: No output path set.")
                return
            if job["type"] == "trim" and not job.get("video_path"):
                self._log.append_line(f"Job {i + 1}: No video file set.")
                return
            if job["type"] == "comms" and (not job.get("video_path") or not job.get("comms_audio_path")):
                self._log.append_line(f"Job {i + 1}: Video and audio files required for comms.")
                return

        self._log.clear()
        self._log.append_line(f"Starting {len(jobs)} job(s)...")
        self._status_bar.showMessage("Running jobs...")

        # Set all cards to queued
        cards = self._job_queue.cards()
        self._job_queue.set_all_locked(True)
        for card in cards:
            card.set_status(JobStatus.QUEUED, "Queued")

        self._runner = CLIJobRunner(jobs, parent=self)
        self._runner.message.connect(self._log.append_line)
        self._runner.job_started.connect(self._on_job_started)
        self._runner.job_finished.connect(self._on_job_finished)
        self._runner.all_finished.connect(self._on_all_finished)
        self._runner.start()

    def _on_job_started(self, index: int):
        cards = self._job_queue.cards()
        if 0 <= index < len(cards):
            cards[index].set_status(JobStatus.RUNNING, "Running...")
        self._status_bar.showMessage(f"Running job {index + 1}...")

    def _on_job_finished(self, index: int, success: bool):
        cards = self._job_queue.cards()
        if 0 <= index < len(cards):
            if success:
                cards[index].set_status(JobStatus.SUCCESS, "Completed")
            else:
                cards[index].set_status(JobStatus.FAILED, "Failed")

    def _on_all_finished(self, succeeded: int, total: int):
        self._runner = None
        self._job_queue.set_all_locked(False)
        self._log.append_line(f"\nAll done: {succeeded}/{total} jobs succeeded.")
        self._status_bar.showMessage(f"Done: {succeeded}/{total} succeeded")
