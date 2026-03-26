"""Player table widget with radio button selection."""

from PySide6.QtCore import Signal
from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QTableWidget, QTableWidgetItem,
    QRadioButton, QHeaderView, QButtonGroup,
)


class PlayerTable(QWidget):
    """Table showing players from a demo with optional radio selection."""

    player_selected = Signal(int)  # Emits row index

    def __init__(self, selectable: bool = True, parent=None):
        super().__init__(parent)
        self._selectable = selectable
        self._button_group = QButtonGroup(self) if selectable else None

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)

        columns = ["Name", "SteamID", "Team", "Kills", "Assists"]
        if selectable:
            columns = [""] + columns

        self._table = QTableWidget(0, len(columns))
        self._table.setHorizontalHeaderLabels(columns)
        self._table.verticalHeader().setVisible(False)
        self._table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self._table.setSelectionMode(QTableWidget.SelectionMode.NoSelection)

        header = self._table.horizontalHeader()
        if selectable:
            header.setSectionResizeMode(0, QHeaderView.ResizeMode.Fixed)
            self._table.setColumnWidth(0, 30)
            for i in range(1, len(columns)):
                header.setSectionResizeMode(i, QHeaderView.ResizeMode.Stretch)
        else:
            for i in range(len(columns)):
                header.setSectionResizeMode(i, QHeaderView.ResizeMode.Stretch)

        layout.addWidget(self._table)

    def set_players(self, players):
        """Populate table from a list of PlayerInfo objects.

        Args:
            players: list of parser.PlayerInfo dataclass instances
        """
        self._table.setRowCount(0)
        if self._button_group:
            # Remove old buttons from group
            for btn in self._button_group.buttons():
                self._button_group.removeButton(btn)

        for row, p in enumerate(players):
            self._table.insertRow(row)
            col_offset = 0

            if self._selectable:
                radio = QRadioButton()
                radio.clicked.connect(lambda checked, r=row: self.player_selected.emit(r))
                self._button_group.addButton(radio, row)
                self._table.setCellWidget(row, 0, radio)
                col_offset = 1

            self._table.setItem(row, col_offset, QTableWidgetItem(p.name))
            self._table.setItem(row, col_offset + 1, QTableWidgetItem(str(p.steamid)))
            self._table.setItem(row, col_offset + 2, QTableWidgetItem(p.team or ""))
            self._table.setItem(row, col_offset + 3, QTableWidgetItem(str(p.kills)))
            self._table.setItem(row, col_offset + 4, QTableWidgetItem(str(p.assists)))

    def selected_row(self) -> int:
        """Return selected row index, or -1 if none selected."""
        if not self._button_group:
            return -1
        return self._button_group.checkedId()

    def clear(self):
        self._table.setRowCount(0)
