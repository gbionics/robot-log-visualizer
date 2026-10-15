# Copyright (C) 2022 Istituto Italiano di Tecnologia (IIT). All rights reserved.
# Copyright (C) 2026 Generative Bionics S.R.L. All rights reserved.
# This software may be modified and distributed under the terms of the
# Released under the terms of the BSD 3-Clause License

import os
import threading
from typing import Optional

import av
import numpy as np
import pyqtgraph as pg
from qtpy.QtCore import QRect, Qt, QThread, Signal
from qtpy.QtGui import QImage, QPainter, QPixmap
from qtpy.QtWidgets import QFrame, QHBoxLayout, QLabel, QSizePolicy, QVBoxLayout, QWidget

# Decoding forward is cheaper than seeking, unless the target is farther than this (seconds).
SEEK_THRESHOLD = 3.0
# Depth images are 16-bit in millimeters.
DEPTH_RANGE_M = (0.2, 5.0)
DEPTH_COLORMAP = "turbo"


def depth_lookup_table() -> np.ndarray:
    """RGB lookup table (256 x 3, uint8) of the colormap used for depth images."""
    return pg.colormap.get(DEPTH_COLORMAP).getLookupTable(nPts=256, alpha=False)


class _VideoDecoder(QThread):
    """Decode the frame shown at a requested time without blocking the GUI thread."""

    frame_ready = Signal(QImage)

    def __init__(self, container, stream, depth_lut: Optional[np.ndarray], parent=None):
        super().__init__(parent)
        self._container = container
        self._stream = stream
        self._depth_lut = depth_lut
        self._start = float(stream.start_time * stream.time_base) if stream.start_time else 0.0

        self._frames = None
        self._current = None
        self._next = None

        self._cv = threading.Condition()
        self._target = None
        self._last_target = None
        self._rerender = False
        self._running = True
        self._output_size = (0, 0)

    def request(self, t: float):
        with self._cv:
            self._target = t
            self._cv.notify()

    def set_output_size(self, width: int, height: int):
        with self._cv:
            self._output_size = (width, height)
            self._rerender = True
            self._cv.notify()

    def stop(self):
        with self._cv:
            self._running = False
            self._cv.notify()
        self.wait()

    def run(self):
        while True:
            with self._cv:
                while self._running and self._target is None and not self._rerender:
                    self._cv.wait()
                if not self._running:
                    break
                t = self._target if self._target is not None else self._last_target
                rerender = self._rerender
                self._target = None
                self._rerender = False
            if t is None:
                continue
            self._last_target = t
            changed = self._advance_to(max(t, self._start))
            if (changed or rerender) and self._current is not None:
                self.frame_ready.emit(self._to_qimage(self._current))
        self._container.close()

    def _seek(self, t: float):
        self._container.seek(int(t / self._stream.time_base), stream=self._stream)
        self._frames = self._container.decode(self._stream)
        self._current = None
        self._next = None

    def _advance_to(self, t: float) -> bool:
        """Move to the last frame with time <= t. Return True if the frame changed."""
        previous = self._current
        if previous is None or t < previous.time or t - previous.time > SEEK_THRESHOLD:
            self._seek(t)
        while True:
            if self._next is None:
                try:
                    self._next = next(self._frames)
                except (StopIteration, av.error.FFmpegError):
                    break
            if self._current is not None and self._next.time > t:
                break
            self._current, self._next = self._next, None
        return self._current is not previous

    def _to_qimage(self, frame) -> QImage:
        if self._depth_lut is not None:
            depth = frame.to_ndarray(format="gray16le")
            low, high = (1000.0 * d for d in DEPTH_RANGE_M)
            index = np.clip((depth - low) * (255.0 / (high - low)), 0, 255).astype(np.uint8)
            rgb = self._depth_lut[index]
            rgb[depth == 0] = 0
        else:
            width, height = self._fit(frame.width, frame.height)
            rgb = frame.reformat(width=width, height=height, format="rgb24").to_ndarray()
        rgb = np.ascontiguousarray(rgb)
        height, width, _ = rgb.shape
        return QImage(rgb.data, width, height, 3 * width, QImage.Format_RGB888).copy()

    def _fit(self, width: int, height: int):
        out_w, out_h = self._output_size
        if out_w <= 0 or out_h <= 0:
            return width, height
        scale = min(out_w / width, out_h / height, 1.0)
        return max(1, int(width * scale)), max(1, int(height * scale))


class FrameView(QWidget):
    resized = Signal(int, int)

    def __init__(self, parent=None, smooth: bool = True):
        super().__init__(parent)
        self._image = None
        self._smooth = smooth
        self.setMinimumSize(1, 1)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)

    def set_image(self, image: QImage):
        self._image = image
        self.update()

    def resizeEvent(self, event):
        super().resizeEvent(event)
        ratio = self.devicePixelRatioF()
        self.resized.emit(int(self.width() * ratio), int(self.height() * ratio))

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.fillRect(self.rect(), Qt.black)
        if self._image is None:
            return
        size = self._image.size().scaled(self.size(), Qt.KeepAspectRatio)
        rect = QRect(0, 0, size.width(), size.height())
        rect.moveCenter(self.rect().center())
        painter.setRenderHint(QPainter.SmoothPixmapTransform, self._smooth)
        painter.drawImage(rect, self._image)


def colorbar(lut: np.ndarray, low_label: str, high_label: str) -> QWidget:
    widget = QWidget()
    layout = QHBoxLayout(widget)
    layout.setContentsMargins(0, 0, 0, 0)

    gradient = np.ascontiguousarray(np.repeat(lut[None], 2, axis=0))
    image = QImage(gradient.data, 256, 2, 3 * 256, QImage.Format_RGB888).copy()
    bar = QLabel()
    bar.setPixmap(QPixmap.fromImage(image))
    bar.setScaledContents(True)
    bar.setFixedHeight(14)
    bar.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Fixed)

    layout.addWidget(QLabel(low_label))
    layout.addWidget(bar, 1)
    layout.addWidget(QLabel(high_label))
    return widget


class VideoItem(QFrame):
    def __init__(self, video_filename: str, start_time: Optional[float] = None):
        super().__init__(None)

        # time of the first frame, used to synchronize the video with the dataset
        self.start_time = start_time

        self.media_loaded = False
        self.duration = 0.0
        self._decoder = None

        self._view = FrameView(self)
        layout = QVBoxLayout(self)
        layout.addWidget(self._view, 1)

        if not os.path.isfile(video_filename):
            return
        try:
            container = av.open(video_filename)
        except av.error.FFmpegError:
            return
        stream = container.streams.video[0]
        stream.thread_type = "AUTO"
        if container.duration:
            self.duration = container.duration / av.time_base

        depth_lut = None
        pixel_format = stream.codec_context.format
        if pixel_format is not None and pixel_format.name.startswith("gray16"):
            depth_lut = depth_lookup_table()
            layout.addWidget(
                colorbar(
                    depth_lut,
                    f"{DEPTH_RANGE_M[0]:.1f} m",
                    f"{DEPTH_RANGE_M[1]:.1f} m  (black: no data)",
                )
            )

        self._decoder = _VideoDecoder(container, stream, depth_lut, self)
        self._decoder.frame_ready.connect(self._view.set_image)
        self._view.resized.connect(self._decoder.set_output_size)
        self._decoder.start()
        self.media_loaded = True

    def set_time(self, t: float):
        """Show the frame at time t (seconds from the beginning of the video)."""
        if self._decoder is not None:
            self._decoder.request(t)

    def release(self):
        if self._decoder is not None:
            self._decoder.stop()
            self._decoder = None
        self.media_loaded = False
