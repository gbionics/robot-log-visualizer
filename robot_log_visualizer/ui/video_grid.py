# Copyright (C) 2026 Generative Bionics S.R.L. All rights reserved.
# This software may be modified and distributed under the terms of the
# Released under the terms of the BSD 3-Clause License

from qtpy.QtCore import Qt
from qtpy.QtWidgets import (QComboBox, QFrame, QGridLayout, QHBoxLayout, QLabel,
                            QSizePolicy, QVBoxLayout, QWidget)

from robot_log_visualizer.ui.grid_layout import (GRID_LAYOUTS, GridLayoutButton,
                                                 place_in_grid)
from robot_log_visualizer.ui.video_item import VideoView

NO_VIDEO = "No video"


class _VideoCell(QFrame):
    """Cell of the grid, showing the video selected in its combo box."""

    def __init__(self, videos, selected, parent=None):
        super().__init__(parent)
        self._videos = videos
        self._view = None

        self._combo = QComboBox()
        self._combo.addItem(NO_VIDEO)
        for label, _ in videos:
            self._combo.addItem(label)
        self._combo.setToolTip("Video shown in this window")

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
        self._combo.setCurrentIndex(0 if selected is None else selected + 1)

    def _on_selected(self, index):
        self.release()
        self._placeholder.setVisible(index <= 0)
        if index > 0:
            self._view = VideoView(self._videos[index - 1][1], self)
            self.layout().addWidget(self._view, 1)

    def release(self):
        if self._view is not None:
            self._view.release()
            self._view.deleteLater()
            self._view = None


class VideoGridTab(QWidget):
    """Grid of windows, each showing the video chosen by the user."""

    def __init__(self, videos):
        """videos: list of (label, VideoItem)."""
        super().__init__(None)
        self._videos = list(videos)
        # cells hidden by shrinking the grid are kept, so that they are restored with their video
        self.cells = []
        self.shape = None

        self.layout_button = GridLayoutButton("Number of video windows")
        self.layout_button.layout_selected.connect(self.set_shape)

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

        # the smallest grid showing all the videos
        n_videos = len(self._videos)
        shape = next(
            (s for s in GRID_LAYOUTS if s[0] * s[1] >= n_videos),
            max(GRID_LAYOUTS, key=lambda s: s[0] * s[1]),
        )
        self.set_shape(*shape)

    def set_shape(self, rows, cols):
        while len(self.cells) < rows * cols:
            index = len(self.cells)
            selected = index if index < len(self._videos) else None
            self.cells.append(_VideoCell(self._videos, selected, self))
        self.shape = (rows, cols)
        place_in_grid(self._grid, self.cells, rows, cols)
        self.layout_button.set_current(rows, cols)

    def release(self):
        for cell in self.cells:
            cell.release()
