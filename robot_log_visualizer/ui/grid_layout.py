# Copyright (C) 2026 Generative Bionics S.R.L. All rights reserved.
# This software may be modified and distributed under the terms of the
# Released under the terms of the BSD 3-Clause License

from qtpy.QtCore import QRectF, Qt, Signal
from qtpy.QtGui import QColor, QIcon, QPainter, QPen, QPixmap
from qtpy.QtWidgets import QActionGroup, QMenu, QToolButton

# (rows, columns) of the supported grids, larger grids would not leave enough space
GRID_LAYOUTS = ((1, 1), (2, 1), (1, 2), (2, 2), (3, 2), (2, 3))
MAX_GRID_ROWS = max(rows for rows, _ in GRID_LAYOUTS)
MAX_GRID_COLS = max(cols for _, cols in GRID_LAYOUTS)


def grid_layout_icon(rows, cols, size=24):
    pixmap = QPixmap(size, size)
    pixmap.fill(Qt.transparent)
    painter = QPainter(pixmap)
    painter.setPen(QPen(QColor("#444444"), 1))
    painter.setBrush(QColor("#cfe3f7"))
    margin, spacing = 2.5, 2.0
    width = (size - 2 * margin - (cols - 1) * spacing) / cols
    height = (size - 2 * margin - (rows - 1) * spacing) / rows
    for row in range(rows):
        for col in range(cols):
            x = margin + col * (width + spacing)
            y = margin + row * (height + spacing)
            painter.drawRect(QRectF(x, y, width, height))
    painter.end()
    return QIcon(pixmap)


def place_in_grid(grid, widgets, rows, cols):
    """Show the first rows * cols widgets in the grid layout and hide the others."""
    for widget in widgets:
        grid.removeWidget(widget)
    for index, widget in enumerate(widgets):
        if index < rows * cols:
            grid.addWidget(widget, index // cols, index % cols)
            widget.show()
        else:
            widget.hide()
    for row in range(MAX_GRID_ROWS):
        grid.setRowStretch(row, 1 if row < rows else 0)
    for col in range(MAX_GRID_COLS):
        grid.setColumnStretch(col, 1 if col < cols else 0)


class GridLayoutButton(QToolButton):
    """Button with a menu to select one of the GRID_LAYOUTS."""

    layout_selected = Signal(int, int)

    def __init__(self, tooltip, parent=None):
        super().__init__(parent)
        self.setToolTip(tooltip)
        self.setPopupMode(QToolButton.InstantPopup)
        menu = QMenu(self)
        group = QActionGroup(self)
        self._actions = {}
        for rows, cols in GRID_LAYOUTS:
            action = menu.addAction(
                grid_layout_icon(rows, cols),
                f"{rows}x{cols}  ({rows} {'row' if rows == 1 else 'rows'}, "
                f"{cols} {'column' if cols == 1 else 'columns'})",
            )
            action.setCheckable(True)
            action.setActionGroup(group)
            action.triggered.connect(
                lambda _checked=False, shape=(rows, cols): self._on_triggered(*shape)
            )
            self._actions[(rows, cols)] = action
        self.setMenu(menu)
        self.set_current(1, 1)

    def set_current(self, rows, cols):
        self.setIcon(grid_layout_icon(rows, cols))
        self._actions[(rows, cols)].setChecked(True)

    def _on_triggered(self, rows, cols):
        self.set_current(rows, cols)
        self.layout_selected.emit(rows, cols)
