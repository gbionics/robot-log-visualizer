# Copyright (C) 2026 Generative Bionics S.R.L. All rights reserved.
# This software may be modified and distributed under the terms of the
# Released under the terms of the BSD 3-Clause License

import re
import time
from typing import List, Optional, Tuple

import numpy as np
from qtpy.QtCore import QTimer, Slot
from qtpy.QtGui import QImage
from qtpy.QtWidgets import QFrame, QVBoxLayout

from robot_log_visualizer.ui.video_item import FrameView, colorbar, depth_lookup_table

# Maximum refresh rate of the rendered image (Hz).
MAX_REFRESH_RATE = 60.0

_PIXEL_NAME = re.compile(r"^r(\d+)_c(\d+)$")


class ImageLayout:
    """Map the flat vector of a logged signal into a 2D image."""

    def __init__(self, height: int, width: int, pixel_index: Optional[np.ndarray] = None):
        self.height = height
        self.width = width
        # pixel_index[k] is the row-major position of the k-th element, None if the
        # elements are already stored in row-major order.
        self.pixel_index = pixel_index

    @property
    def shape(self) -> Tuple[int, int]:
        return self.height, self.width

    def to_image(self, values: np.ndarray) -> np.ndarray:
        values = np.asarray(values, dtype=float).reshape(-1)
        if self.pixel_index is None:
            return values.reshape(self.shape)
        image = np.zeros(self.height * self.width)
        image[self.pixel_index] = values
        return image.reshape(self.shape)

    @staticmethod
    def from_signal(size: int, elements_names: Optional[List[str]] = None):
        """Infer the layout of an image stored as a flat vector.

        If all the elements are named `r<row>_c<col>` the layout is obtained from the names,
        otherwise a square image is assumed. Return None if the layout cannot be inferred.
        """
        if elements_names and len(elements_names) == size:
            matches = [_PIXEL_NAME.match(name) for name in elements_names]
            if all(matches):
                rows = np.array([int(m.group(1)) for m in matches])
                cols = np.array([int(m.group(2)) for m in matches])
                height, width = int(rows.max()) + 1, int(cols.max()) + 1
                pixel_index = rows * width + cols
                if height * width != size or np.unique(pixel_index).size != size:
                    return None
                if np.array_equal(pixel_index, np.arange(size)):
                    pixel_index = None
                return ImageLayout(height, width, pixel_index)

        side = int(round(np.sqrt(size)))
        if size > 1 and side * side == size:
            return ImageLayout(side, side)
        return None


class ImageItem(QFrame):
    """Render a logged signal containing a scaled depth image (e.g. the input of a policy).

    The values are mapped to the same colormap used for the depth videos. Zero is
    considered as missing data and rendered in black.
    """

    def __init__(self, signal_provider, path: List[str], layout: ImageLayout):
        super().__init__(None)
        self._signal_provider = signal_provider
        self._path = list(path)
        self._layout = layout
        self._lut = depth_lookup_table()
        self._low, self._high = self._value_range()

        self._last_index = None
        self._last_render = 0.0
        self._pending = False

        self._view = FrameView(self, smooth=False)
        layout_widget = QVBoxLayout(self)
        layout_widget.addWidget(self._view, 1)
        layout_widget.addWidget(
            colorbar(self._lut, f"{self._low:g}", f"{self._high:g}  (black: no data)")
        )

        self._signal_provider.update_index_signal.connect(self.refresh)

    def _value_range(self) -> Tuple[float, float]:
        """The scaled depth is expected in [0, 1], the range is enlarged if the data exceed it."""
        low, high = 0.0, 1.0
        try:
            data, _ = self._signal_provider.get_item_from_path(self._path)
            data = np.asarray(data, dtype=float)
            if data.size:
                low = min(low, float(np.nanmin(data)))
                high = max(high, float(np.nanmax(data)))
        except (KeyError, TypeError, ValueError):
            pass
        return low, high

    @Slot()
    def refresh(self, force: bool = False):
        """Request to render the image at the current index of the signal provider."""
        if force:
            self._last_index = None
        if self._pending or not self.isVisible():
            return
        self._pending = True
        delay = self._last_render + 1.0 / MAX_REFRESH_RATE - time.monotonic()
        QTimer.singleShot(max(0, int(delay * 1000)), self._render)

    def _render(self):
        self._pending = False
        if self._signal_provider is None or not self.isVisible():
            return
        index = self._signal_provider.index
        if index == self._last_index:
            return
        try:
            values = self._signal_provider.get_item_from_path_at_index(self._path, index)
        except (KeyError, IndexError, TypeError):
            values = None
        if values is None:
            return
        self._last_index = index
        self._last_render = time.monotonic()
        self._view.set_image(self._to_qimage(values))

    def _to_qimage(self, values: np.ndarray) -> QImage:
        depth = np.nan_to_num(self._layout.to_image(values), nan=0.0)
        scale = 255.0 / (self._high - self._low)
        index = np.clip((depth - self._low) * scale, 0, 255).astype(np.uint8)
        rgb = self._lut[index]
        rgb[depth == 0] = 0
        rgb = np.ascontiguousarray(rgb)
        height, width, _ = rgb.shape
        return QImage(rgb.data, width, height, 3 * width, QImage.Format_RGB888).copy()

    def showEvent(self, event):
        super().showEvent(event)
        self.refresh(force=True)

    def release(self):
        if self._signal_provider is not None:
            try:
                self._signal_provider.update_index_signal.disconnect(self.refresh)
            except (TypeError, RuntimeError):
                pass
            self._signal_provider = None
