"""Job queue page - scrollable list of job cards with action bar."""

from PySide6.QtCore import Signal, Qt
from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QPushButton,
    QScrollArea, QFrame,
)

from .widgets.job_card import JobCard, JobStatus


class JobQueuePage(QWidget):
    """Main page: scrollable job card list with add/clear/start controls."""

    start_requested = Signal(list)  # emits list of job dicts

    def __init__(self, demo_cache=None, parent=None):
        super().__init__(parent)
        self._demo_cache = demo_cache
        self._cards: list[JobCard] = []

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        # --- Scroll area for cards ---
        self._scroll = QScrollArea()
        self._scroll.setWidgetResizable(True)
        self._scroll.setFrameShape(QFrame.Shape.NoFrame)
        self._scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self._scroll.setStyleSheet(
            "QScrollArea { background: transparent; border: none; }"
            "QScrollBar:vertical { background: #1e1e24; width: 8px; border: none; }"
            "QScrollBar::handle:vertical { background: #3a3a44; border-radius: 4px; min-height: 30px; }"
            "QScrollBar::handle:vertical:hover { background: #555560; }"
            "QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical { height: 0; }"
        )

        self._scroll_content = QWidget()
        self._scroll_layout = QVBoxLayout(self._scroll_content)
        self._scroll_layout.setContentsMargins(12, 12, 12, 12)
        self._scroll_layout.setSpacing(10)

        # "+" add button at the bottom of the scroll area
        self._add_btn = QPushButton("+  Add Job")
        self._add_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._add_btn.setMinimumHeight(52)
        self._add_btn.setStyleSheet(
            "QPushButton {"
            "  background: transparent;"
            "  border: 2px dashed #555560;"
            "  border-radius: 8px;"
            "  color: #8c8c96;"
            "  font-size: 14px;"
            "  font-weight: bold;"
            "}"
            "QPushButton:hover {"
            "  border-color: #4e9aff;"
            "  color: #4e9aff;"
            "}"
        )
        self._add_btn.clicked.connect(lambda: self.add_job())
        self._scroll_layout.addWidget(self._add_btn)

        self._scroll_layout.addStretch()
        self._scroll.setWidget(self._scroll_content)
        layout.addWidget(self._scroll, 1)

        # --- Bottom action bar ---
        action_bar = QWidget()
        action_bar.setStyleSheet("background: #1e1e24;")
        action_layout = QHBoxLayout(action_bar)
        action_layout.setContentsMargins(12, 8, 12, 8)

        self._clear_btn = QPushButton("Clear Jobs")
        self._clear_btn.setFixedHeight(36)
        self._clear_btn.setStyleSheet(
            "QPushButton {"
            "  background: #2a2a32;"
            "  color: #dcdcdc;"
            "  border: 1px solid #3a3a44;"
            "  border-radius: 6px;"
            "  padding: 0 20px;"
            "  font-size: 13px;"
            "}"
            "QPushButton:hover { border-color: #555560; background: #333340; }"
            "QPushButton:disabled { color: #555560; border-color: #2a2a32; }"
        )
        self._clear_btn.clicked.connect(self.clear_jobs)
        action_layout.addWidget(self._clear_btn)

        action_layout.addStretch()

        self._start_btn = QPushButton("Start Jobs")
        self._start_btn.setFixedHeight(36)
        self._start_btn.setStyleSheet(
            "QPushButton {"
            "  background: qlineargradient(x1:0, y1:0, x2:1, y2:0, "
            "    stop:0 #5aa8ff, stop:1 #3d7dd9);"
            "  color: white;"
            "  font-weight: bold;"
            "  font-size: 14px;"
            "  border: none;"
            "  border-radius: 6px;"
            "  padding: 0 28px;"
            "}"
            "QPushButton:hover {"
            "  background: qlineargradient(x1:0, y1:0, x2:1, y2:0, "
            "    stop:0 #6db5ff, stop:1 #4e8ee6);"
            "}"
            "QPushButton:disabled {"
            "  background: #3a3a44;"
            "  color: #555560;"
            "}"
        )
        self._start_btn.clicked.connect(self._on_start)
        action_layout.addWidget(self._start_btn)

        layout.addWidget(action_bar)

    def add_job(self, config: dict = None) -> JobCard:
        """Create a new job card and insert it above the '+' button."""
        card = JobCard(job_number=len(self._cards) + 1)

        # Connect card signals
        card.remove_requested.connect(self._on_remove)
        card.duplicate_requested.connect(self._on_duplicate)

        if self._demo_cache:
            card.demo_changed.connect(self._demo_cache.request_parse)

        self._cards.append(card)

        # Insert before the "+" button (which is at index count-2 before stretch)
        insert_idx = self._scroll_layout.count() - 2  # before add_btn and stretch
        self._scroll_layout.insertWidget(insert_idx, card)

        if config:
            card.load_from_dict(config)

        return card

    def _on_remove(self, card: JobCard):
        if card in self._cards:
            self._cards.remove(card)
            self._scroll_layout.removeWidget(card)
            card.deleteLater()
            self._renumber()

    def _on_duplicate(self, card: JobCard):
        config = card.to_job_dict()
        new_card = self.add_job(config)
        # If demo was already parsed, propagate players
        if self._demo_cache and config.get("demo_path"):
            demo_info = self._demo_cache.get_cached(config["demo_path"])
            if demo_info:
                new_card.set_players(demo_info.players)
                # Try to restore player selection by matching text
                player_text = config.get("player_text", "")
                if player_text:
                    idx = new_card._player_combo.findText(player_text)
                    if idx >= 0:
                        new_card._player_combo.setCurrentIndex(idx)

    def _renumber(self):
        for i, card in enumerate(self._cards):
            card.set_job_number(i + 1)

    def clear_jobs(self):
        for card in self._cards[:]:
            self._scroll_layout.removeWidget(card)
            card.deleteLater()
        self._cards.clear()

    def cards(self) -> list[JobCard]:
        return list(self._cards)

    def set_all_locked(self, locked: bool):
        """Lock/unlock all cards and action buttons during execution."""
        for card in self._cards:
            card.set_locked(locked)
        self._add_btn.setEnabled(not locked)
        self._clear_btn.setEnabled(not locked)
        self._start_btn.setEnabled(not locked)
        self._start_btn.setText("Running..." if locked else "Start Jobs")

    def _on_start(self):
        if not self._cards:
            return
        jobs = [card.to_job_dict() for card in self._cards]
        self.start_requested.emit(jobs)

    def propagate_players(self, demo_path: str, demo_info):
        """Send player list to all cards that have this demo path."""
        from pathlib import Path
        resolved = str(Path(demo_path).resolve())
        for card in self._cards:
            card_path = card._demo_picker.path().strip()
            if card_path and str(Path(card_path).resolve()) == resolved:
                card.set_players(demo_info.players)
