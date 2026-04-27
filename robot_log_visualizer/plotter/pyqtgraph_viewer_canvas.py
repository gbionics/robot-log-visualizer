# Copyright (C) 2025 Istituto Italiano di Tecnologia (IIT). All rights reserved.
# This software may be modified and distributed under the terms of the
# Released under the terms of the BSD 3-Clause License

from __future__ import annotations

from typing import Dict, Iterable, Sequence, Tuple

import numpy as np
import pyqtgraph as pg  # type: ignore
from qtpy import QtCore, QtWidgets  # type: ignore

# --- Global pyqtgraph performance tuning ---
pg.setConfigOptions(antialias=False, useOpenGL=True)

from robot_log_visualizer.signal_provider.signal_provider import ProviderType
from robot_log_visualizer.utils.utils import ColorPalette

# ------------------------------------------------------------------------
# Type aliases
# ------------------------------------------------------------------------
Path = Sequence[str]
Legend = Sequence[str]
Point = Tuple[float, float]


class PyQtGraphViewerCanvas(QtWidgets.QWidget):
    """Interactive time‑series viewer built on *pyqtgraph*."""

    #: Default click radius as a fraction of the current view‑range.
    DEFAULT_RADIUS: float = 0.01  # 1 % of the axes extent
    #: Default diameter of the marker drawn on a selected point.
    DEFAULT_MARKER_SIZE: int = 8

    def __init__(
        self,
        parent: QtWidgets.QWidget | None,
        period: float,
        *,
        click_radius: float | None = None,
        marker_size: int | None = None,
    ) -> None:
        """Create the widget.

        Args:
            parent: Parent widget or *None*.
            period: Update period for the vertical line (seconds).
            click_radius: Override :pyattr:`DEFAULT_RADIUS`.
            marker_size: Override :pyattr:`DEFAULT_MARKER_SIZE`.
        """
        super().__init__(parent)

        # injected dependencies
        self._signal_provider = None
        self._period_ms: int = int(period * 1000)
        self._click_radius: float = click_radius or self.DEFAULT_RADIUS
        self._marker_size: int = marker_size or self.DEFAULT_MARKER_SIZE

        # data structures
        self._curves: Dict[str, pg.PlotDataItem] = {}
        self._curve_fulldata: Dict[str, tuple] = {}  # key -> (x, y) full-res
        self._annotations: Dict[Point, pg.TextItem] = {}
        self._markers: Dict[Point, pg.ScatterPlotItem] = {}
        self._palette: Iterable = ColorPalette()

        # UI set‑up
        self._init_ui()
        self._connect_signals()

    # -------------------------------------------------------------#
    # Public API (called from the outside)                         #
    # -------------------------------------------------------------#

    def set_signal_provider(self, signal_provider) -> None:
        """Set the signal provider to fetch data from.

        Args:
            signal_provider: An instance of `SignalProvider`.
        """

        if signal_provider is None:
            return

        self._signal_provider = signal_provider

        # Connect to real-time updates for real-time provider
        if self._signal_provider.provider_type == ProviderType.REALTIME:
            self._signal_provider.update_index_signal.connect(
                self._update_realtime_curves
            )

    def update_plots(self, paths: Sequence[Path], legends: Sequence[Legend]) -> None:
        """Synchronise plots with the *paths* list.

        New items are added, disappeared items removed. Existing ones are
        left untouched to avoid flicker.
        """
        if self._signal_provider is None:
            return

        # For real-time provider, update the set of selected signals to buffer
        if self._signal_provider.provider_type == ProviderType.REALTIME:
            selected_keys = ["::".join(path) for path in paths]
            self._signal_provider.add_signals_to_buffer(selected_keys)

        self._add_missing_curves(paths, legends)
        self._remove_obsolete_curves(paths)

        # Set the X axis range based on the provider type
        if self._signal_provider.provider_type == ProviderType.REALTIME:
            # For real-time data, show a fixed window with 0 set at the right edge for the latest data
            self._plot.setXRange(-self._signal_provider.realtime_fixed_plot_window, 0.0)
            # Disable mouse panning on x axis
            self._plot.plotItem.vb.setMouseEnabled(x=False, y=True)
            # For real-time data enable autoscaling of Y axis
            self._plot.plotItem.vb.enableAutoRange(axis=pg.ViewBox.YAxis, enable=True)
        else:
            # Default behavior
            self._plot.setXRange(
                0.0,
                self._signal_provider.end_time - self._signal_provider.initial_time,
            )

    # The following trio is wired to whoever controls the replay/stream
    def pause_animation(self) -> None:  # noqa: D401
        """Pause the vertical‑line animation."""
        self._timer.stop()

    def resume_animation(self) -> None:  # noqa: D401
        """Resume the vertical‑line animation."""
        self._timer.start(self._period_ms)

    def quit_animation(self) -> None:  # noqa: D401
        """Permanently stop the animation (e.g. when closing the tab)."""
        self._timer.stop()
    def update_vline_value(self, value: float) -> None:
        """Directly set the vline position (called from update_index)."""
        if value == getattr(self, '_last_vline_val', None):
            return
        self._last_vline_val = value
        self._vline.setValue(value)
    # -------------------------------------------------------------#
    # Qt event handlers                                            #
    # -------------------------------------------------------------#
    def closeEvent(self, event):  # type: ignore[override]
        """Ensure timers are stopped before Qt destroys the object."""
        self._timer.stop()
        super().closeEvent(event)

    # -------------------------------------------------------------#
    # Internal helpers                                            #
    # -------------------------------------------------------------#
    def _init_ui(self) -> None:
        """Create all Qt widgets and lay them out."""
        self._layout = QtWidgets.QVBoxLayout(self)
        self._plot: pg.PlotWidget = pg.PlotWidget(background="w")
        self._layout.addWidget(self._plot)

        # --- Performance: only render points visible in the current
        # view-port and auto-downsample when zoomed out.
        self._plot.setClipToView(True)
        self._plot.setDownsampling(mode="peak")

        label_style = {"color": "#000000", "font-size": "12pt"}
        self._plot.setLabel("bottom", "Time [s]", **label_style)
        self._plot.setLabel("left", "Value", **label_style)
        self._plot.showGrid(x=True, y=True)

        self._plot.addLegend(
            offset=(10, 10), labelTextSize="12pt", brush=(255, 255, 255, 150)
        )
        self._plot.plotItem.legend.setParentItem(self._plot.plotItem)
        self._plot.plotItem.legend.anchor((0, 0), (0, 0))

        # Vertical line that follows *current_time*
        self._vline = pg.InfiniteLine(angle=90, movable=False, pen=pg.mkPen("#555555"))
        self._plot.addItem(self._vline)

        # Timer that updates the vertical line
        self._timer = QtCore.QTimer(self)
        self._timer.timeout.connect(self._update_vline)
        self._timer.start(self._period_ms)

    def _connect_signals(self) -> None:
        """Wire Qt / pyqtgraph signals to slots."""
        self._plot.scene().sigMouseClicked.connect(self._on_mouse_click)

    def _add_missing_curves(
        self, paths: Sequence[Path], legends: Sequence[Legend]
    ) -> None:
        """Plot curves that are present in *paths* but not yet on screen."""
        for path, legend in zip(paths, legends):
            key = "/".join(path)
            if key in self._curves:
                continue

            # Drill down to the data array
            data = self._signal_provider.data
            for subkey in path[:-1]:
                data = data[subkey]
            try:
                y = data["data"][:, int(path[-1])]
            except (IndexError, ValueError):  # scalar variable
                y = data["data"][:]

            x = data["timestamps"] - self._signal_provider.initial_time
            palette_color = next(self._palette)
            pen = pg.mkPen(palette_color.as_hex(), width=2)

            # Pre-downsample for fast rendering (207k → ~4000 points)
            x_ds, y_ds = self._downsample_peak(x, y)

            self._curves[key] = self._plot.plot(
                x_ds,
                y_ds,
                pen=pen,
                name="/".join(legend[1:]),
                symbol=None,
                clipToView=True,
            )
            self._curve_fulldata[key] = (x, y)

            # For real-time mode, disable curve clickability to avoid interfering with live updates
            if self._signal_provider.provider_type == ProviderType.REALTIME:
                self._curves[key].setCurveClickable(False)
                self._curves[key].setEnabled(False)

    def _remove_obsolete_curves(self, paths: Sequence[Path]) -> None:
        """Delete curves that disappeared from *paths*."""
        valid = {"/".join(p) for p in paths}
        for key in [k for k in self._curves if k not in valid]:
            self._plot.removeItem(self._curves.pop(key))
            self._curve_fulldata.pop(key, None)

    @staticmethod
    def _downsample_peak(x, y, max_points: int = 4000):
        """Peak-preserving downsample: keep min/max per window."""
        n = len(x)
        if n <= max_points:
            return x, y
        factor = max(1, n // (max_points // 2))
        trim = (n // factor) * factor
        yr = y[:trim].reshape(-1, factor)
        xr = x[:trim].reshape(-1, factor)
        stx = factor // 2
        x_mid = xr[:, stx]
        y_max_v = yr.max(axis=1)
        y_min_v = yr.min(axis=1)
        out_n = len(x_mid) * 2
        x_out = np.empty(out_n, dtype=x.dtype)
        y_out = np.empty(out_n, dtype=y.dtype)
        x_out[0::2] = x_mid
        x_out[1::2] = x_mid
        y_out[0::2] = y_max_v
        y_out[1::2] = y_min_v
        if trim < n:
            x_out = np.concatenate([x_out, x[trim:]])
            y_out = np.concatenate([y_out, y[trim:]])
        return x_out, y_out

    def _update_vline(self) -> None:
        """Move the vertical line to ``current_time``."""
        if self._signal_provider is None:
            return

        new_val = self._signal_provider.current_time
        # Skip the update (and the expensive repaint it triggers) when the
        # position has not actually changed.
        if new_val == getattr(self, '_last_vline_val', None):
            return
        self._last_vline_val = new_val
        self._vline.setValue(new_val)

    def _update_realtime_curves(self):
        """Update all curves with the latest data from the signal provider."""

        if self._signal_provider is None:
            return
        for key, curve in self._curves.items():
            # Drill down to the data array using the path
            path = key.split("/")
            data = self._signal_provider.data
            for subkey in path[:-1]:
                data = data[subkey]
            try:
                y = data["data"][:, int(path[-1])]
            except (IndexError, ValueError):
                y = data["data"][:]

            # Set the 0 of the x axis to the latest timestamp
            latest_time = data["timestamps"][-1] if len(data["timestamps"]) > 0 else 0
            x = data["timestamps"] - latest_time
            curve.setData(x, y)

    def _on_mouse_click(self, event) -> None:  # noqa: N802
        """Handle a left‑click: select or unselect the nearest data point."""
        if event.button() != QtCore.Qt.MouseButton.LeftButton:
            return  # ignore other buttons

        # Disable point selection / labels in realtime mode
        if self._signal_provider.provider_type == ProviderType.REALTIME:
            return

        # Scene → data coordinates
        scene_pos = event.scenePos()
        if not self._plot.sceneBoundingRect().contains(scene_pos):
            return

        mouse_pt = self._plot.plotItem.vb.mapSceneToView(scene_pos)
        mx, my = float(mouse_pt.x()), float(mouse_pt.y())

        # Locate the closest (curve, index)
        candidate: Point | None = None
        min_dist_sq = np.inf
        nearest_curve: pg.PlotDataItem | None = None

        for curve in self._curves.values():
            xs, ys = curve.getData()
            d2 = (xs - mx) ** 2 + (ys - my) ** 2  # squared distance
            idx = int(np.argmin(d2))
            if (cur_d2 := float(d2[idx])) < min_dist_sq:
                min_dist_sq = cur_d2
                candidate = (float(xs[idx]), float(ys[idx]))
                nearest_curve = curve

        if candidate is None:
            return

        # Check click radius (fraction of axis span)
        x_span = np.diff(self._plot.viewRange()[0])[0]
        y_span = np.diff(self._plot.viewRange()[1])[0]
        thresh_sq = (self._click_radius * x_span) ** 2 + (
            self._click_radius * y_span
        ) ** 2
        if min_dist_sq > thresh_sq:
            return  # click too far away -> ignore

        # Toggle selection state
        if candidate in self._annotations:
            self._deselect(candidate)
        else:
            assert nearest_curve is not None  # mypy‑friendly
            self._select(candidate, nearest_curve.opts["pen"])

    def _select(self, pt: Point, pen: pg.QtGui.QPen) -> None:
        """Add label + circle marker on *pt*."""
        x_span = np.diff(self._plot.viewRange()[0])[0]
        y_span = np.diff(self._plot.viewRange()[1])[0]
        x_prec = max(0, int(-np.log10(max(x_span, 1e-12))) + 2)
        y_prec = max(0, int(-np.log10(max(y_span, 1e-12))) + 2)
        label = f"{pt[0]:.{x_prec}f}, {pt[1]:.{y_prec}f}"

        txt = pg.TextItem(
            text=label,
            anchor=(0, 1),
            color="#000000",
            fill=pg.mkBrush(255, 255, 255, 200),
            border=pg.mkPen("#000000"),
        )
        txt.setPos(*pt)
        self._plot.addItem(txt)
        self._annotations[pt] = txt

        # Convert QColor → RGBA tuple for the marker fill
        qcol = pen.color()
        marker = pg.ScatterPlotItem(
            [pt[0]],
            [pt[1]],
            symbol="o",
            size=self._marker_size,
            pen=pg.mkPen("#000000"),
            brush=pg.mkBrush(qcol.red(), qcol.green(), qcol.blue(), 255),
        )
        self._plot.addItem(marker)
        self._markers[pt] = marker

    def _deselect(self, pt: Point) -> None:
        """Remove annotation + marker on *pt*."""
        self._plot.removeItem(self._annotations.pop(pt))
        self._plot.removeItem(self._markers.pop(pt))

    def clear_selections(self) -> None:  # noqa: D401
        """Remove **all** annotations and markers."""
        for item in (*self._annotations.values(), *self._markers.values()):
            self._plot.removeItem(item)
        self._annotations.clear()
        self._markers.clear()
