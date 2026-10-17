# Copyright (C) 2022 Istituto Italiano di Tecnologia (IIT). All rights reserved.
# This software may be modified and distributed under the terms of the
# Released under the terms of the BSD 3-Clause License

from qtpy.QtCore import Signal
from qtpy.QtWidgets import QFrame, QGridLayout, QWidget

from robot_log_visualizer.plotter.pyqtgraph_viewer_canvas import PyQtGraphViewerCanvas
from robot_log_visualizer.ui.grid_layout import place_in_grid
from robot_log_visualizer.ui.ui_loader import load_ui


class PlotItem(QFrame):
    def __init__(self, period):
        super().__init__(None)
        self.ui = load_ui("plot_tab.ui", self)
        self.canvas = PyQtGraphViewerCanvas(parent=self, period=period)
        self.ui.plotLayout.addWidget(self.canvas)
        self.set_highlighted(False)

    def set_signal_provider(self, signal_provider):
        self.canvas.set_signal_provider(signal_provider)

    def set_highlighted(self, highlighted):
        # a transparent border keeps the plot size constant when the highlight moves
        color = "#2a82da" if highlighted else "transparent"
        self.setStyleSheet(f"QFrame#PlotTab {{ border: 2px solid {color}; }}")


class PlotGridTab(QWidget):
    """Tab showing a grid of plots, the signals selected in the tree go to the active plot."""

    active_plot_changed = Signal(object)

    def __init__(self, period, signal_provider=None):
        super().__init__(None)
        self._period = period
        self._signal_provider = signal_provider
        self._animated = False
        self._grid = QGridLayout(self)
        self._grid.setContentsMargins(0, 0, 0, 0)
        self._grid.setSpacing(2)

        # plots hidden by shrinking the grid are kept, so that they are restored with their signals
        self.plot_items = []
        self.shape = None
        self.active_plot = None
        self.set_shape(1, 1)

    @property
    def visible_plot_items(self):
        rows, cols = self.shape
        return self.plot_items[: rows * cols]

    def set_signal_provider(self, signal_provider):
        self._signal_provider = signal_provider
        for plot_item in self.plot_items:
            plot_item.set_signal_provider(signal_provider)

    def set_shape(self, rows, cols):
        while len(self.plot_items) < rows * cols:
            plot_item = PlotItem(period=self._period)
            plot_item.setParent(self)
            plot_item.set_signal_provider(self._signal_provider)
            plot_item.canvas.sig_clicked.connect(
                lambda plot_item=plot_item: self.set_active_plot(plot_item)
            )
            self.plot_items.append(plot_item)

        self.shape = (rows, cols)
        place_in_grid(self._grid, self.plot_items, rows, cols)

        self.set_animated(self._animated)

        if self.active_plot not in self.visible_plot_items:
            self.set_active_plot(self.plot_items[0])
        else:
            self._update_highlight()

    def set_active_plot(self, plot_item):
        if plot_item is self.active_plot:
            return
        self.active_plot = plot_item
        self._update_highlight()
        self.active_plot_changed.emit(plot_item)

    def set_animated(self, animated):
        """Run the animation of the visible plots only, to avoid useless overhead."""
        self._animated = animated
        visible = self.visible_plot_items
        for plot_item in self.plot_items:
            if animated and plot_item in visible:
                plot_item.canvas.resume_animation()
            else:
                plot_item.canvas.pause_animation()

    def quit_animation(self):
        for plot_item in self.plot_items:
            plot_item.canvas.quit_animation()

    def _update_highlight(self):
        multiple_plots = len(self.visible_plot_items) > 1
        for plot_item in self.plot_items:
            plot_item.set_highlighted(multiple_plots and plot_item is self.active_plot)
