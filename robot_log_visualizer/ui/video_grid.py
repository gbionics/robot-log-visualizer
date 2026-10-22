# Copyright (C) 2026 Generative Bionics S.R.L. All rights reserved.
# This software may be modified and distributed under the terms of the
# Released under the terms of the BSD 3-Clause License

from qtpy.QtCore import Qt
from qtpy.QtWidgets import (QComboBox, QFrame, QGridLayout, QHBoxLayout, QLabel,
                            QSizePolicy, QVBoxLayout, QWidget)

from robot_log_visualizer.ui.grid_layout import (GRID_LAYOUTS, GridLayoutButton,
                                                 place_in_grid)

NO_VIDEO = "No video"


class _VideoCell(QFrame):
    """Cell of the grid, showing the source (video or rendered image) selected in its combo box."""

    def __init__(self, sources, parent=None):
        super().__init__(parent)
        self._source = None
        self._view = None

        self._combo = QComboBox()
        self._combo.addItem(NO_VIDEO, None)
        for label, source in sources:
            self._combo.addItem(label, source)
        self._combo.setToolTip("Video or image shown in this window")

        self._placeholder = QLabel("No video selected")
        self._placeholder.setAlignment(Qt.AlignCenter)
        self._placeholder.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Ignored)
        self._placeholder.setStyleSheet("background-color: black; color: #aaaaaa;")

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(2)
        layout.addWidget(self._combo)
        layout.addWidget(self._placeholder, 1)

        self._combo.currentIndexChanged.connect(self._on_selected)

    @property
    def source(self):
        return self._source

    def select(self, source):
        self._combo.setCurrentIndex(self._index_of(source))

    def add_source(self, label, source):
        self._combo.addItem(label, source)

    def remove_source(self, source):
        index = self._index_of(source)
        if index == self._combo.currentIndex():
            self._combo.setCurrentIndex(0)
        self._combo.removeItem(index)

    def _index_of(self, source):
        return next(i for i in range(self._combo.count()) if self._combo.itemData(i) is source)

    def _on_selected(self, index):
        source = self._combo.itemData(index)
        # removing an item before the current one changes the index but not the source
        if source is self._source and (source is None or self._view is not None):
            return
        self.release()
        self._source = source
        self._placeholder.setVisible(source is None)
        if source is not None:
            self._view = source.create_view(self)
            self.layout().addWidget(self._view, 1)

    def release(self):
        if self._view is not None:
            self._view.release()
            self._view.deleteLater()
            self._view = None


class VideoGridTab(QWidget):
    """Grid of windows, each showing the video or rendered image chosen by the user."""

    def __init__(self, sources):
        """sources: list of (label, item), the item must implement create_view(parent)."""
        super().__init__(None)
        self._sources = list(sources)
        # cells hidden by shrinking the grid are kept, so that they are restored with their video
        self.cells = []
        self.shape = None
        # the layout follows the number of sources until the user selects one
        self._auto_shape = True

        self.layout_button = GridLayoutButton("Number of video windows")
        self.layout_button.layout_selected.connect(self._on_layout_selected)

        toolbar = QHBoxLayout()
        toolbar.addWidget(self.layout_button)
        toolbar.addWidget(QLabel("Layout"))
        toolbar.addStretch(1)

        self._grid = QGridLayout()
        self._grid.setSpacing(4)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(2, 2, 2, 2)
        layout.addLayout(toolbar)
        layout.addLayout(self._grid, 1)

        self.set_shape(*self._default_shape())

    @property
    def is_empty(self):
        return not self._sources

    def add_source(self, label, source):
        self._sources.append((label, source))
        for cell in self.cells:
            cell.add_source(label, source)
        self._update_shape()

    def remove_source(self, source):
        self._sources = [(label, s) for label, s in self._sources if s is not source]
        for cell in self.cells:
            cell.remove_source(source)
        self._update_shape()

    def _default_shape(self):
        """The smallest grid showing all the sources, GRID_LAYOUTS lists the vertical ones first."""
        return next(
            (s for s in GRID_LAYOUTS if s[0] * s[1] >= len(self._sources)),
            max(GRID_LAYOUTS, key=lambda s: s[0] * s[1]),
        )

    def _update_shape(self):
        if self._auto_shape:
            self.set_shape(*self._default_shape())
        else:
            self._fill_empty_cells()

    def _on_layout_selected(self, rows, cols):
        self._auto_shape = False
        self.set_shape(rows, cols)

    def set_shape(self, rows, cols):
        while len(self.cells) < rows * cols:
            self.cells.append(_VideoCell(self._sources, self))
        self.shape = (rows, cols)
        place_in_grid(self._grid, self.cells, rows, cols)
        self.layout_button.set_current(rows, cols)
        self._fill_empty_cells()

    def _fill_empty_cells(self):
        """Show in the empty visible windows the sources that are not shown in the other ones."""
        visible = self.cells[: self.shape[0] * self.shape[1]]
        shown = [cell.source for cell in visible]
        missing = [source for _, source in self._sources if source not in shown]
        empty = [cell for cell in visible if cell.source is None]
        for cell, source in zip(empty, missing):
            cell.select(source)

    def release(self):
        for cell in self.cells:
            cell.release()
