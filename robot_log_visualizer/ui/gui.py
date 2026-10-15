# Copyright (C) 2022 Istituto Italiano di Tecnologia (IIT). All rights reserved.
# Copyright (C) 2026 Generative Bionics S.R.L. All rights reserved.
# This software may be modified and distributed under the terms of the
# Released under the terms of the BSD 3-Clause License

import os
import pathlib
import re
import sys
# for logging
from time import localtime, strftime

import numpy as np
import pyqtconsole.highlighter as hl
from pyqtconsole.console import PythonConsole
# QtPy abstraction
from qtpy import QtWebEngineWidgets  # noqa: F401
from qtpy import QtGui, QtWidgets
from qtpy.QtCore import QMutex, QMutexLocker, Qt, QTimer, QUrl, Slot
from qtpy.QtWidgets import (QDialog, QDialogButtonBox, QFileDialog, QLineEdit,
                            QToolButton, QTreeWidgetItem, QVBoxLayout)

from robot_log_visualizer.robot_visualizer.meshcat_provider import \
    MeshcatProvider
from robot_log_visualizer.signal_provider.matfile_signal_provider import \
    MatfileSignalProvider
from robot_log_visualizer.signal_provider.realtime_signal_provider import (
    ROBOT_REALTIME_KEY, RealtimeSignalProvider, are_deps_installed)
from robot_log_visualizer.signal_provider.signal_provider import (
    ProviderType, SignalProvider)
from robot_log_visualizer.ui.image_item import ImageItem, ImageLayout
from robot_log_visualizer.ui.plot_item import PlotItem
from robot_log_visualizer.ui.text_logging import TextLoggingItem
# QtDesigner generated classes
from robot_log_visualizer.ui.ui_loader import load_ui
from robot_log_visualizer.ui.video_item import VideoItem
from robot_log_visualizer.utils.utils import (ColorPalette,
                                              PeriodicThreadState,
                                              RobotStatePath)

pyqtSlot = Slot

# first log format storing the robot model as a resolve-robotics-uri, see
# https://github.com/gbionics/bipedal-locomotion-framework/blob/master/devices/YarpRobotLoggerDevice/docs/log-format/README.md
MIN_ROBOT_MODEL_URI_VERSION = (0, 1, 0)


def parse_version(version):
    """Return the (major, minor, patch) tuple of a log format version, None if not valid."""
    match = re.match(r"^v?(\d+)\.(\d+)\.(\d+)", version)
    return tuple(int(v) for v in match.groups()) if match else None


class SetRobotModelDialog(QtWidgets.QDialog):
    def __init__(self, meshcat_provider, parent=None, dataset_loaded=False):
        # call QMainWindow constructor
        super().__init__(parent)
        self.ui = load_ui("set_robot_model.ui", self)

        model_path = meshcat_provider.model_path
        package_dir = meshcat_provider.custom_package_dir

        if model_path:
            self.ui.robotModelLineEdit.setText(model_path)

        if package_dir:
            self.ui.packageDirLineEdit.setText(package_dir)

        self.ui.robotModelLineEdit.setEnabled(not dataset_loaded)
        self.ui.packageDirLineEdit.setEnabled(not dataset_loaded)

        self.ui.robotModelToolButton.clicked.connect(self.open_urdf_file)
        self.ui.packageDirToolButton.clicked.connect(self.open_package_directory)

        # Force the arrowScaling_lineEdit to be a positive float
        self.ui.arrowScaling_lineEdit.setValidator(QtGui.QDoubleValidator(0, 100, 2))

        # connect the arrowScaling_checkBox to the handle_arrow_scaling method
        self.ui.arrowScaling_checkBox.toggled.connect(self.handle_arrow_scaling)

        self.clicked_button = None
        self.std_button = None
        self.ui.buttonBox.clicked.connect(self.buttonBox_on_click)

        if dataset_loaded:
            frames = meshcat_provider.robot_frames()
            self.ui.frameNameComboBox.addItems(frames)
            self.ui.frameNameComboBox.setCurrentText(meshcat_provider.base_frame)

    def open_urdf_file(self):
        file_name, _ = QFileDialog.getOpenFileName(
            self, "Open urdf file", ".", filter="*.urdf"
        )
        if file_name:
            self.ui.robotModelLineEdit.setText(file_name)

    def open_package_directory(self):
        dir_path = QFileDialog.getExistingDirectory(self, "Select package directory")
        if dir_path:
            self.ui.packageDirLineEdit.setText(dir_path)

    def get_urdf_path(self):
        return self.ui.robotModelLineEdit.text()

    def get_package_directory(self):
        return self.ui.packageDirLineEdit.text()

    def buttonBox_on_click(self, button):
        self.clicked_button = button

        self.std_button = self.ui.buttonBox.standardButton(button)

    def get_clicked_button_role(self):
        if self.clicked_button is not None:
            return self.ui.buttonBox.buttonRole(self.clicked_button)
        return None

    def get_clicked_button_text(self):
        if self.clicked_button is not None:
            return self.clicked_button.text()
        return None

    def get_clicked_standard_button(self):
        return self.std_button

    def handle_arrow_scaling(self):
        # if arrowScaling_checkBox is checked the lineEdit must be disabled else it must be enabled
        if self.ui.arrowScaling_checkBox.isChecked():
            self.ui.arrowScaling_lineEdit.setText("")
            self.ui.arrowScaling_lineEdit.setEnabled(False)
        else:
            self.ui.arrowScaling_lineEdit.setText("")
            self.ui.arrowScaling_lineEdit.setEnabled(True)


class About(QtWidgets.QMainWindow):
    def __init__(self):
        # call QMainWindow constructor
        super().__init__()
        self.ui = load_ui("about.ui", self)


def build_plot_title_box_dialog():
    dlg = QDialog()
    dlg.setWindowTitle("Plot title")
    la = QVBoxLayout(dlg)
    line_edit = QLineEdit()
    la.addWidget(line_edit)
    bb = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
    bb.clicked.connect(dlg.accept)
    bb.rejected.connect(dlg.reject)
    la.addWidget(bb)
    dlg.setLayout(la)
    return dlg, line_edit


def get_icon(icon_name):
    icon = QtGui.QIcon()
    icon.addPixmap(
        QtGui.QPixmap(str(pathlib.Path(__file__).parent / "misc" / icon_name)),
        QtGui.QIcon.Normal,
        QtGui.QIcon.Off,
    )
    return icon


class RobotViewerMainWindow(QtWidgets.QMainWindow):
    def __init__(self, signal_provider_period, meshcat_provider, animation_period):
        # call QMainWindow constructor
        super().__init__()

        self.signal_provider_period = signal_provider_period

        # for realtime logging
        self.realtimePlotUpdaterThreadActive = False
        self.plotData = {}
        self.realtime_connection_enabled = False
        self.timeoutAttempts = 20

        self.animation_period = animation_period

        # set up the user interface
        self.ui = load_ui("visualizer.ui", self)

        # Set all the icons
        self.ui.startButton.setIcon(get_icon("play-outline.svg"))
        self.ui.pauseButton.setIcon(get_icon("pause-outline.svg"))
        self.ui.pauseButton.setIcon(get_icon("pause-outline.svg"))
        self.ui.meshcatAndVideoTab.setTabIcon(
            0, get_icon("game-controller-outline.svg")
        )
        self.ui.tabWidget.setTabIcon(0, get_icon("calendar-outline.svg"))
        self.ui.tabWidget.setTabIcon(1, get_icon("terminal-outline.svg"))
        self.ui.tabWidget.setTabIcon(2, get_icon("document-text-outline.svg"))

        self.ui.actionQuit.setIcon(get_icon("close-circle-outline.svg"))
        self.ui.actionQuit.setIcon(get_icon("close-circle-outline.svg"))

        # Playback speed options
        self._speed_options = [0.25, 0.5, 1.0, 1.5, 2.0, 4.0]
        for s in self._speed_options:
            self.ui.speedComboBox.addItem(f"{s}x")
        self.ui.speedComboBox.setCurrentIndex(self._speed_options.index(1.0))
        self.ui.actionOpen.setIcon(get_icon("folder-open-outline.svg"))
        self.ui.actionSet_Robot_Model.setIcon(get_icon("body-outline.svg"))
        self.setWindowIcon(get_icon("icon.png"))

        self.about = About()

        self.signal_provider: SignalProvider = None

        self.meshcat_provider: MeshcatProvider = meshcat_provider

        self.tool_button = QToolButton()
        self.tool_button.setText("+")
        self.ui.tabPlotWidget.setCornerWidget(self.tool_button)
        self.tool_button.clicked.connect(self.toolButton_on_click)

        self.plot_items = []
        self.video_items = []
        # images rendered from logged signals, indexed by the path of the signal
        self.image_items = {}
        self.visualized_3d_points = set()
        self.visualized_3d_trajectories = set()
        self.visualized_3d_arrows = set()
        self.visualized_3d_points_colors_palette = ColorPalette()

        self.toolButton_on_click()

        # instantiate the Logger
        self.logger = Logger(self.ui.logLabel, self.ui.logScrollArea)
        # print welcome message
        self.logger.write_to_log("Robot Viewer started.")

        self.text_logger = TextLoggingItem(self.ui.yarpTextLogTableWidget)

        self._slider_pressed_mutex = QMutex()
        self._slider_pressed = False

        self.dataset_loaded = False

        # connect action
        self.ui.actionQuit.triggered.connect(self.close)
        self.ui.actionOpen.triggered.connect(self.open_mat_file)

        if are_deps_installed():
            self.ui.actionRealtime_Connect.triggered.connect(
                self.connect_realtime_logger
            )
        else:
            self.ui.actionRealtime_Connect.setEnabled(False)

        self.ui.actionAbout.triggered.connect(self.open_about)
        self.ui.actionSet_Robot_Model.triggered.connect(self.open_set_robot_model)

        self.ui.meshcatView.setUrl(
            QUrl(meshcat_provider._meshcat_visualizer.viewer.url())
        )

        self.ui.pauseButton.clicked.connect(self.pauseButton_on_click)
        self.ui.startButton.clicked.connect(self.startButton_on_click)
        self.ui.refreshButton.clicked.connect(self.refreshButton_on_click)
        self.ui.speedComboBox.currentIndexChanged.connect(
            self.speedComboBox_on_changed
        )

        # by default the refresh button is only relevant for realtime connections
        try:
            self.ui.refreshButton.setEnabled(False)
        except Exception:
            pass

        # Setup for refresh button blinking/color change
        self.refresh_button_blink_state = False
        self.refresh_button_timer = QTimer(self)
        self.refresh_button_timer.timeout.connect(self._toggle_refresh_button_style)
        self.refresh_button_timer.setInterval(500)  # Blink every 500ms

        # Timer to periodically check for new metadata in realtime mode
        self.metadata_check_timer = QTimer(self)
        self.metadata_check_timer.timeout.connect(self._check_for_new_metadata)
        self.metadata_check_timer.setInterval(2000)  # Check every 2 seconds

        self.ui.timeSlider.sliderReleased.connect(self.timeSlider_on_release)
        self.ui.timeSlider.sliderPressed.connect(self.timeSlider_on_pressed)
        self.ui.timeSlider.sliderMoved.connect(self.timeSlider_on_sliderMoved)

        self.ui.variableTreeWidget.itemClicked.connect(self.variableTreeWidget_on_click)
        self.ui.yarpTextLogTreeWidget.itemClicked.connect(
            self.textLogTreeWidget_on_click
        )

        self.ui.tabPlotWidget.tabCloseRequested.connect(
            self.plotTabCloseButton_on_click
        )
        self.ui.tabPlotWidget.tabBarDoubleClicked.connect(
            self.plotTabBar_on_doubleClick
        )
        self.ui.tabPlotWidget.currentChanged.connect(self.plotTabBar_currentChanged)

        # add a custom context menu to the variable tree widget
        self.ui.variableTreeWidget.setContextMenuPolicy(Qt.CustomContextMenu)
        self.ui.variableTreeWidget.customContextMenuRequested.connect(
            self.variableTreeWidget_on_right_click
        )

        self.robot_state_path = RobotStatePath()

        self.pyconsole = PythonConsole(
            parent=self.ui.pythonWidget,
            formats={
                "keyword": hl.format("#204a87", "bold"),
                "operator": hl.format("#ce5c00"),
                # 'brace':      hl.format('#eeeeec'),
                "defclass": hl.format("#000000", "bold"),
                "string": hl.format("#8f5902"),
                "string2": hl.format("#8f5902"),
                "comment": hl.format("#8f5902", "italic"),
                "self": hl.format("#000000", "italic"),
                "numbers": hl.format("#0000cf"),
                "inprompt": hl.format("#8f5902", "bold"),
                "outprompt": hl.format("#8f5902", "bold"),
            },
        )
        self.pyconsole.edit.setStyleSheet("font-size: 12px;")
        self.ui.pythonWidgetLayout.addWidget(self.pyconsole)
        self.pyconsole.eval_in_thread()

        # self.media_player = QMediaPlayer(None, QMediaPlayer.VideoSurface)
        # self.media_player.setVideoOutput(self.ui.webcamView)
        # self.media_loaded = False

    @property
    def slider_pressed(self):
        locker = QMutexLocker(self._slider_pressed_mutex)
        value = self._slider_pressed
        return value

    @slider_pressed.setter
    def slider_pressed(self, slider_pressed):
        locker = QMutexLocker(self._slider_pressed_mutex)
        self._slider_pressed = slider_pressed

    def keyPressEvent(self, event):
        if not self.dataset_loaded:
            return

        if event.modifiers() & Qt.ControlModifier:
            if event.key() == Qt.Key_B:
                self.slider_pressed = True
                new_index = int(self.ui.timeSlider.value()) - 1
                dataset_percentage = float(new_index) / float(
                    self.ui.timeSlider.maximum()
                )
                self.signal_provider.set_dataset_percentage(dataset_percentage)
                self.ui.timeLabel.setText(f"{self.signal_provider.current_time:.2f}")
                self.text_logger.highlight_cell(
                    self.find_text_log_index(self.get_text_log_item_path())
                )

                self.__set_videos_position(dataset_percentage)

                # update the time slider
                self.ui.timeSlider.setValue(new_index)
                self.slider_pressed = False
            elif event.key() == Qt.Key_F:
                self.slider_pressed = True
                new_index = int(self.ui.timeSlider.value()) + 1
                dataset_percentage = float(new_index) / float(
                    self.ui.timeSlider.maximum()
                )
                self.signal_provider.set_dataset_percentage(dataset_percentage)
                self.ui.timeLabel.setText(f"{self.signal_provider.current_time:.2f}")
                self.text_logger.highlight_cell(
                    self.find_text_log_index(self.get_text_log_item_path())
                )

                self.__set_videos_position(dataset_percentage)

                self.ui.timeSlider.setValue(new_index)
                self.slider_pressed = False
        else:
            # If the user presses the space bar, the play/pause state is toggled.
            if event.key() == Qt.Key_Space:
                # toggle the play/pause button
                if self.ui.startButton.isEnabled():
                    self.ui.startButton.click()
                else:
                    self.ui.pauseButton.click()

    def toolButton_on_click(self):
        self.plot_items.append(PlotItem(period=self.animation_period))
        self.plot_items[-1].set_signal_provider(self.signal_provider)
        self.ui.tabPlotWidget.addTab(self.plot_items[-1], "Plot")

        if self.ui.tabPlotWidget.count() == 1:
            self.ui.tabPlotWidget.setTabsClosable(False)
        else:
            self.ui.tabPlotWidget.setTabsClosable(True)

    def timeSlider_on_pressed(self):
        self.slider_pressed = True

    def timeSlider_on_sliderMoved(self):
        index = int(self.ui.timeSlider.value())
        dataset_percentage = float(index) / float(self.ui.timeSlider.maximum())

        self.signal_provider.set_dataset_percentage(dataset_percentage)
        self.ui.timeLabel.setText(f"{self.signal_provider.current_time:.2f}")
        self.__update_image_items()

        # Defer heavy work (video seek, text-log highlight) so the slider
        # stays responsive while dragging.  The deferred work is performed
        # once the user releases the slider (timeSlider_on_release).

    def timeSlider_on_release(self):
        index = int(self.ui.timeSlider.value())
        dataset_percentage = float(index) / float(self.ui.timeSlider.maximum())

        self.signal_provider.set_dataset_percentage(dataset_percentage)
        self.__set_videos_position(dataset_percentage)

        self.ui.timeLabel.setText(f"{self.signal_provider.current_time:.2f}")
        self.text_logger.highlight_cell(
            self.find_text_log_index(self.get_text_log_item_path())
        )
        self.slider_pressed = False

    def startButton_on_click(self):
        self.ui.startButton.setEnabled(False)
        self.ui.pauseButton.setEnabled(True)
        self.signal_provider.state = PeriodicThreadState.running
        self.meshcat_provider.state = PeriodicThreadState.running

        self.logger.write_to_log("Dataset started.")

    def pauseButton_on_click(self):
        self.ui.pauseButton.setEnabled(False)
        self.ui.startButton.setEnabled(True)

        self.signal_provider.state = PeriodicThreadState.pause
        self.meshcat_provider.state = PeriodicThreadState.pause

        self.logger.write_to_log("Dataset paused.")

    def speedComboBox_on_changed(self, index):
        speed = self._speed_options[index]
        if self.signal_provider is not None:
            self.signal_provider.playback_speed = speed
        self.logger.write_to_log(f"Playback speed set to {speed}x.")

    def plotTabCloseButton_on_click(self, index):
        self.plot_items[index].canvas.quit_animation()
        del self.plot_items[index]
        if self.ui.tabPlotWidget.count() == 1:
            self.ui.tabPlotWidget.setTabsClosable(False)
        self.ui.tabPlotWidget.removeTab(index)

    def plotTabBar_on_doubleClick(self, index):
        dlg, plot_title = build_plot_title_box_dialog()
        if dlg.exec_() == QDialog.Accepted:
            self.ui.tabPlotWidget.setTabText(index, plot_title.text())

    def variableTreeWidget_on_click(self):
        paths = []
        legends = []
        for index in self.ui.variableTreeWidget.selectedIndexes():
            path = []
            legend = []
            is_leaf = True
            self.logger.write_to_log(f"Selected index data: {index.data()}")
            while index.data() is not None:
                legend.append(index.data())
                if not is_leaf:
                    path.append(index.data())
                else:
                    path.append(str(index.row()))
                    is_leaf = False

                index = index.parent()
            path.reverse()
            legend.reverse()

            paths.append(path)
            legends.append(legend)

        # Debug logs
        self.logger.write_to_log(f"Selected paths: {paths}")
        self.logger.write_to_log(f"Selected legends: {legends}")

        # if there is no selection we do nothing
        if not paths:
            return

        self.plotData[self.ui.tabPlotWidget.currentIndex()] = {
            "paths": paths,
            "legends": legends,
        }

        self.plot_items[self.ui.tabPlotWidget.currentIndex()].canvas.update_plots(
            paths, legends
        )

    def find_text_log_index(self, path):
        if not path:
            return None

        current_time = self.signal_provider.current_time
        initial_time = self.signal_provider.initial_time

        ref = self.signal_provider.text_logging_data
        for key in path:
            ref = ref[key]

        # Use binary search (O(log n)) instead of linear scan (O(n))
        ts = ref["timestamps"]
        target = current_time + initial_time
        idx = np.searchsorted(ts, target, side="right") - 1
        if idx < 0:
            return None
        return int(idx)

    def show_text_log(self, path):
        initial_time = self.signal_provider.initial_time
        ref = self.signal_provider.text_logging_data
        for key in path:
            ref = ref[key]

        self.text_logger.clean()
        logs = ref["data"]
        timestamps = ref["timestamps"]
        for i in range(len(ref["data"])):
            self.text_logger.add_entry(
                logs[i].text, timestamps[i] - initial_time, font_color=logs[i].color()
            )

    def get_text_log_item_path(self):
        paths = []
        for index in self.ui.yarpTextLogTreeWidget.selectedIndexes():
            path = []
            is_leaf = True
            while index.data() is not None:
                if not is_leaf:
                    path.append(index.data())
                else:
                    path.append(index.data())
                    is_leaf = False

                index = index.parent()

            path.reverse()
            paths.append(path)
        if paths:
            return paths[0]
        else:
            return None

    def textLogTreeWidget_on_click(self):
        path = self.get_text_log_item_path()
        if path:
            self.show_text_log(path)
            self.text_logger.highlight_cell(self.find_text_log_index(path))

    def plotTabBar_currentChanged(self, index):
        # pause all the animations except the one that is selected, this is done to avoid the overhead of the animations
        for i in range(len(self.plot_items)):
            if i != index:
                self.plot_items[i].canvas.pause_animation()
            else:
                self.plot_items[i].canvas.resume_animation()

        # clear the selection to prepare a new one
        self.ui.variableTreeWidget.clearSelection()
        for active_path_str in self.plot_items[index].canvas._curves.keys():
            path = active_path_str.split("/")

            # select the item in the tree from the path
            item = self.ui.variableTreeWidget.topLevelItem(0)
            for subpath in path[1:-1]:
                # find the item given its name
                for child_id in range(item.childCount()):
                    if item.child(child_id).text(0) == subpath:
                        item = item.child(child_id)
                        break

            # the latest value is a number
            item = item.child(int(path[-1]))
            item.setSelected(True)

    @pyqtSlot()
    def update_index(self):
        if self.slider_pressed:
            return

        index = self.signal_provider.index
        current_time = self.signal_provider.current_time

        self.ui.timeSlider.setValue(index)
        self.ui.timeLabel.setText(f"{current_time:.2f}")

        # Also push the current time to the active plot tab so the vline stays
        # in sync with the selected index. This complements the canvas timer-
        # driven updates rather than replacing them.
        active_tab = self.ui.tabPlotWidget.currentIndex()
        if 0 <= active_tab < len(self.plot_items):
            self.plot_items[active_tab].canvas.update_vline_value(current_time)

        # Only update text log highlight if a text log entry is selected
        text_path = self.get_text_log_item_path()
        if text_path:
            self.text_logger.highlight_cell(
                self.find_text_log_index(text_path)
            )

        # Only seek videos that are loaded
        slider_max = self.ui.timeSlider.maximum()
        if slider_max > 0:
            self.__set_videos_position(float(index) / float(slider_max))

    def __set_videos_position(self, dataset_percentage):
        """Seek the videos to the current instant of the dataset.

        If the time of the first frame of a video is known, the position is computed from the
        time, otherwise the video is assumed to span the entire dataset.
        """
        self.__update_image_items()

        current_time = self.signal_provider.initial_time + self.signal_provider.current_time
        for video_item in self.video_items:
            if not video_item.media_loaded:
                continue
            if video_item.start_time is not None:
                position = max(0.0, current_time - video_item.start_time)
            else:
                position = dataset_percentage * video_item.duration
            video_item.set_time(position)

    def __update_image_items(self):
        for image_item in self.image_items.values():
            image_item.refresh()

    def __video_start_time(self, video_label):
        """Time of the first frame of a video stored by the YarpRobotLoggerDevice.

        The frame indices of the video `<prefix>_<camera>_<type>` are stored in the channel
        camera::<camera>::<type>. The index restarts from zero in each video.
        """
        camera, _, image_type = video_label.rpartition("_")
        root = list(self.signal_provider.data.keys())[0]
        try:
            channel = self.signal_provider.data[root]["camera"][camera][image_type]
            indices = np.atleast_1d(np.asarray(channel["data"])).reshape(-1)
            timestamps = np.atleast_1d(np.asarray(channel["timestamps"])).reshape(-1)
        except (KeyError, TypeError):
            return None

        first = np.flatnonzero(indices == 0)
        if first.size == 0 or first[0] >= timestamps.size:
            return None
        return float(timestamps[first[0]])

    def closeEvent(self, event):
        # ensure update callbacks are not triggered while tearing down the UI
        if self.signal_provider is not None:
            try:
                self.signal_provider.update_index_signal.disconnect(self.update_index)
            except (TypeError, RuntimeError):
                # ignore if already disconnected
                pass

        # stop timers/animations before widgets disappear
        for plot_item in self.plot_items:
            plot_item.canvas.quit_animation()

        # stop the embedded Python console (it owns a QThread)
        self.pyconsole.close()

        # gracefully stop worker threads before deleting widgets they use
        if self.signal_provider is not None:
            self.signal_provider.state = PeriodicThreadState.closed
            self.signal_provider.wait()
        # hide/disable refresh on close
        try:
            self.ui.refreshButton.setEnabled(False)
        except Exception:
            pass
            self.signal_provider = None

        # Stop the meshcat_provider if exists
        if self.meshcat_provider is not None:
            self.meshcat_provider.state = PeriodicThreadState.closed
            self.meshcat_provider.wait()

        # release multimedia resources explicitly to avoid late callbacks
        # while closing
        for video_item in self.video_items:
            video_item.release()
            video_item.deleteLater()
        self.video_items.clear()

        for image_item in self.image_items.values():
            image_item.release()
            image_item.deleteLater()
        self.image_items.clear()

        # Disable realtime connection
        if self.realtime_connection_enabled:
            self.realtime_connection_enabled = False

        # Stop timers
        if hasattr(self, "metadata_check_timer"):
            self.metadata_check_timer.stop()
        if hasattr(self, "refresh_button_timer"):
            self.refresh_button_timer.stop()

        event.accept()

    def __populate_variable_tree_widget(
        self, obj: dict, parent: QTreeWidgetItem
    ) -> QTreeWidgetItem:

        if not isinstance(obj, dict):
            return parent
        if "data" in obj.keys() and "timestamps" in obj.keys():
            temp_array = obj["data"]
            try:
                n_cols = temp_array.shape[1]
            except IndexError:
                # This happens in the case the variable is a scalar.
                n_cols = 1

            # In yarp telemetry v0.4.0 the elements_names was saved.
            if "elements_names" in obj.keys():
                for name in obj["elements_names"]:
                    item = QTreeWidgetItem([name])
                    parent.addChild(item)
            else:
                for i in range(n_cols):
                    item = QTreeWidgetItem(["Element " + str(i)])
                    parent.addChild(item)
            return parent
        for key, value in obj.items():
            item = QTreeWidgetItem([key])
            item = self.__populate_variable_tree_widget(value, item)
            item.setFlags(item.flags() & ~Qt.ItemIsSelectable)
            parent.addChild(item)
        return parent

    def _update_variable_tree_widget(
        self, obj: dict, parent: QTreeWidgetItem
    ) -> QTreeWidgetItem:
        """
        Recursively merge new metadata into the variable tree widget.
        Only checks for existing items at non-leaf nodes.
        At leaf nodes, always adds all children.
        """
        if not isinstance(obj, dict):
            return parent

        if "data" in obj.keys() and "timestamps" in obj.keys():
            temp_array = obj["data"]
            try:
                n_cols = temp_array.shape[1]
            except Exception:
                n_cols = 1

            # Always add all children at leaf level
            if "elements_names" in obj.keys():
                for name in obj["elements_names"]:
                    item = QTreeWidgetItem([name])
                    parent.addChild(item)
            else:
                for i in range(n_cols):
                    item = QTreeWidgetItem(["Element " + str(i)])
                    parent.addChild(item)
            return parent

        for key, value in obj.items():
            # Only check for existing child at non-leaf nodes
            child_item = None
            for i in range(parent.childCount()):
                if parent.child(i).text(0) == key:
                    child_item = parent.child(i)
                    break
            if child_item is None:
                child_item = QTreeWidgetItem([key])
                child_item.setFlags(child_item.flags() & ~Qt.ItemIsSelectable)
                parent.addChild(child_item)
                self._update_variable_tree_widget(value, child_item)
        return parent

    def __populate_text_logging_tree_widget(self, obj, parent) -> QTreeWidgetItem:
        if not isinstance(obj, dict):
            return parent

        if "data" in obj.keys() and "timestamps" in obj.keys():
            return parent

        for key, value in obj.items():
            item = QTreeWidgetItem([key])
            item = self.__populate_text_logging_tree_widget(value, item)
            if "data" not in value.keys():
                item.setFlags(item.flags() & ~Qt.ItemIsSelectable)
            parent.addChild(item)
        return parent

    def __load_mat_file(self, file_name):
        self.signal_provider = MatfileSignalProvider(
            self.signal_provider_period, "robot_logger_device"
        )
        self.signal_provider.open(file_name)
        self.signal_provider.playback_speed = self._speed_options[
            self.ui.speedComboBox.currentIndex()
        ]
        self.signal_size = len(self.signal_provider)
        self.signal_provider.start()

        self.signal_provider.register_update_index(self.update_index)

        # add signal provider to the plot items
        self.plot_items[-1].set_signal_provider(self.signal_provider)

        # load the model and load the provider
        self.meshcat_provider.set_signal_provider(self.signal_provider)
        if not self.__is_robot_model_available():
            self.logger.write_to_log(
                "The file uses the log format v"
                + self.signal_provider.version
                + ", where the robot model is not stored as a URI (it is stored from v"
                + ".".join(str(v) for v in MIN_ROBOT_MODEL_URI_VERSION)
                + "). The robot model is not loaded, it can be set manually before opening "
                "the file."
            )
        elif not self.meshcat_provider.load_model(
            self.signal_provider.joints_name, self.signal_provider.robot_name
        ):
            # if not loaded we print an error but we continue
            msg = "Unable to load the model: "
            if self.meshcat_provider.model_path:
                msg = msg + self.meshcat_provider.model_path
            else:
                msg = msg + self.signal_provider.robot_name

            self.logger.write_to_log(msg)

        self.meshcat_provider.start()

        # populate tree
        root = list(self.signal_provider.data.keys())[0]
        root_item = QTreeWidgetItem([root])
        root_item.setFlags(root_item.flags() & ~Qt.ItemIsSelectable)
        items = self.__populate_variable_tree_widget(
            self.signal_provider.data[root], root_item
        )
        self.ui.variableTreeWidget.insertTopLevelItems(0, [items])

        # populate text logging tree
        if self.signal_provider.text_logging_data:
            root = list(self.signal_provider.text_logging_data.keys())[0]
            root_item = QTreeWidgetItem([root])
            root_item.setFlags(root_item.flags() & ~Qt.ItemIsSelectable)
            items = self.__populate_text_logging_tree_widget(
                self.signal_provider.text_logging_data[root], root_item
            )
            self.ui.yarpTextLogTreeWidget.insertTopLevelItems(0, [items])

        # spawn the console
        self.pyconsole.push_local_ns("data", self.signal_provider.data)

        self.ui.timeSlider.setMaximum(self.signal_size)
        self.ui.startButton.setEnabled(True)
        self.ui.timeSlider.setEnabled(True)
        # loading a MAT file: refresh is not relevant
        try:
            self.ui.refreshButton.setEnabled(False)
        except Exception:
            pass

        # get all the video associated to the datase
        filename_without_path = pathlib.Path(file_name).name
        (prefix, sep, suffix) = filename_without_path.rpartition(".")

        video_filenames = [
            str(pathlib.Path(file_name).parent.absolute() / pathlib.Path(f))
            for f in os.listdir(pathlib.Path(file_name).parent.absolute())
            if re.search(prefix + r"_[a-zA-Z0-9_]*\.(mp4|mkv)$", f)
        ]

        # for every video we create a video item and we append it to the tab
        for video_filename in video_filenames:
            video_prefix, _, _ = pathlib.Path(video_filename).name.rpartition(".")
            video_label = str(video_prefix).replace(prefix + "_", "")
            self.video_items.append(
                VideoItem(
                    video_filename=video_filename,
                    start_time=self.__video_start_time(video_label),
                )
            )
            self.ui.meshcatAndVideoTab.addTab(
                self.video_items[-1], get_icon("videocam-outline.svg"), video_label
            )
            self.logger.write_to_log("Video '" + video_filename + "' opened.")

        # show the first frame of the videos
        self.__set_videos_position(0.0)

        self.meshcat_provider.state = PeriodicThreadState.running
        self.ui.actionRealtime_Connect.setEnabled(False)

        self.dataset_loaded = True

        # write something in the log
        self.logger.write_to_log("File '" + file_name + "' opened.")
        self.logger.write_to_log(
            "Robot name: '" + self.signal_provider.robot_name + "'."
        )
        self.logger.write_to_log("Log format version: '" + self.signal_provider.version + "'.")

    def __is_robot_model_available(self):
        # in mat files, the robot name is a resolve-robotics-uri only from MIN_ROBOT_MODEL_URI_VERSION
        version = parse_version(self.signal_provider.version)
        return (
            self.signal_provider.provider_type != ProviderType.OFFLINE
            or (version is not None and version >= MIN_ROBOT_MODEL_URI_VERSION)
            or bool(self.meshcat_provider.custom_package_dir)
        )

    def open_mat_file(self):
        file_name, _ = QFileDialog.getOpenFileName(
            self, "Open a mat file", ".", filter="*.mat"
        )
        if file_name:
            self.__load_mat_file(file_name)
            return True
        return False

    def connect_realtime_logger(self):
        self.signal_provider = RealtimeSignalProvider(
            self.signal_provider_period, ROBOT_REALTIME_KEY
        )
        self.realtime_connection_enabled = True

        # Do initial connection to populate the necessary data
        if not self.signal_provider.open("/yarp-robot-logger/rt_logging"):
            self.logger.write_to_log("Could not connect to the real-time logger.")
            self.realtime_connection_enabled = False
            self.signal_provider = None
            # failed to connect: ensure refresh is disabled
            try:
                self.ui.refreshButton.setEnabled(False)
            except Exception:
                pass
            return
        # only display one root in the gui
        root = list(self.signal_provider.data.keys())[0]
        root_item = QTreeWidgetItem([root])
        root_item.setFlags(root_item.flags() & ~Qt.ItemIsSelectable)
        items = self.__populate_variable_tree_widget(
            self.signal_provider.data[root], root_item
        )
        self.ui.variableTreeWidget.insertTopLevelItems(0, [items])

        if not self.meshcat_provider.load_model(
            self.signal_provider.joints_name, self.signal_provider.robot_name
        ):
            # if not loaded we print an error but we continue
            msg = "Unable to load the model: "
            if self.meshcat_provider.model_path:
                msg = msg + self.meshcat_provider.model_path
            else:
                msg = msg + self.signal_provider.robot_name

            self.logger.write_to_log(msg)

        # Disable these buttons for RT communication
        self.ui.startButton.setEnabled(False)
        self.ui.timeSlider.setEnabled(False)

        # start the threads accordingly
        self.signal_provider.state = PeriodicThreadState.running
        self.signal_provider.start()
        self.meshcat_provider.set_signal_provider(self.signal_provider)
        self.meshcat_provider.state = PeriodicThreadState.running
        self.meshcat_provider.start()
        for plot in self.plot_items:
            plot.set_signal_provider(self.signal_provider)

        # In realtime mode, the refresh button starts disabled until new metadata is available
        try:
            self.ui.refreshButton.setEnabled(False)
            self.ui.refreshButton.setStyleSheet("")  # Reset to normal style
            self.refresh_button_blink_state = False
            # Start checking for new metadata periodically
            self.metadata_check_timer.start()
        except Exception:
            pass

    def open_about(self):
        self.about.show()

    def open_set_robot_model(self):
        dlg = SetRobotModelDialog(
            self.meshcat_provider,
            self,
            self.dataset_loaded,
        )
        outcome = dlg.exec_()
        if outcome == QDialog.Accepted:

            # check which button was clicked
            button_role = dlg.get_clicked_button_role()
            button_text = dlg.get_clicked_button_text()
            std_button = dlg.get_clicked_standard_button()

            if std_button == QtWidgets.QDialogButtonBox.SaveAll:
                if not self.dataset_loaded:
                    self.meshcat_provider.model_path = dlg.get_urdf_path()
                    self.meshcat_provider.custom_package_dir = (
                        dlg.get_package_directory()
                    )

                arrow_scaling_value = dlg.ui.arrowScaling_lineEdit.text()
                if not arrow_scaling_value:
                    arrow_scaling_value = "1.0"
                else:
                    arrow_scaling_value = float(arrow_scaling_value)
                self.signal_provider.set_custom_max_arrow(
                    not dlg.ui.arrowScaling_checkBox.isChecked(), arrow_scaling_value
                )
            if std_button == QtWidgets.QDialogButtonBox.Save:
                # we need to check which tab is selected in the dlg
                if dlg.ui.tabWidget.currentIndex() == 0:
                    if not self.dataset_loaded:
                        self.meshcat_provider.model_path = dlg.get_urdf_path()
                        self.meshcat_provider.custom_package_dir = (
                            dlg.get_package_directory()
                        )
                else:
                    arrow_scaling_value = dlg.ui.arrowScaling_lineEdit.text()
                    # if it is empty we set it to 1.0
                    if not arrow_scaling_value:
                        arrow_scaling_value = "1.0"
                    else:
                        arrow_scaling_value = float(arrow_scaling_value)
                    self.signal_provider.set_custom_max_arrow(
                        not dlg.ui.arrowScaling_checkBox.isChecked(),
                        arrow_scaling_value,
                    )

            elif self.__is_robot_model_available():
                self.meshcat_provider.load_model(
                    self.signal_provider.joints_name,
                    self.signal_provider.robot_name,
                    base_frame=dlg.ui.frameNameComboBox.currentText(),
                )

    def dropEvent(self, event):
        if len(event.mimeData().urls()) != 1:
            event.ignore()

        url = event.mimeData().urls()[0].toLocalFile()
        ext = os.path.splitext(url)[-1].lower()
        if ext == ".mat":
            self.__load_mat_file(url)
            event.accept()
        else:
            event.ignore()

    def dragEnterEvent(self, event):
        if event.mimeData().hasUrls():
            event.accept()
        else:
            event.ignore()

    def variableTreeWidget_on_right_click(self, item_position):
        # check if the variable tree widget is empty
        if self.ui.variableTreeWidget.topLevelItemCount() == 0:
            menu = QtWidgets.QMenu()
            menu.addAction("Open a mat file")
            action = menu.exec_(self.ui.variableTreeWidget.mapToGlobal(item_position))
            if action is None:
                return
            if action.text() == "Open a mat file":
                self.open_mat_file()

            return

        # find the item from the position
        item = self.ui.variableTreeWidget.itemAt(item_position)
        if item is None:
            return

        if item.childCount() == 0:
            return

        # the child has to be a leaf
        if item.child(0).childCount() != 0:
            return

        # check the number of children
        item_size = item.childCount()
        item_path = self.get_item_path(item)
        item_key = "/".join(item_path)

        menu = QtWidgets.QMenu()

        add_3d_point_str = "Show as a 3D point"
        add_3d_trajectory_str = "Show as a 3D trajectory"
        add_3d_arrow_str = "Show as a 3D arrow"
        remove_3d_point_str = "Remove the 3D point"
        remove_3d_trajectory_str = "Remove the 3D trajectory"
        remove_3d_arrow_str = "Remove the 3D arrow"
        use_as_base_position_str = "Use as base position"
        use_as_base_orientation_str = "Use as base orientation"
        dont_use_as_base_position_str = "Don't use as base position"
        dont_use_as_base_orientation_str = "Don't use as base orientation"
        render_as_image_str = "Render as image"
        remove_image_str = "Remove the image"
        selected_base_color = QtGui.QColor(255, 0, 0, 127)
        deselected_base_color = QtGui.QColor(0, 0, 0, 0)

        # in this case we can use the item as 3d point where the z coordinate is set to 0
        if item_size == 2:
            if item_key in self.visualized_3d_points:
                menu.addAction(remove_3d_point_str)
            if item_key in self.visualized_3d_trajectories:
                menu.addAction(remove_3d_trajectory_str)
            if (
                item_key not in self.visualized_3d_points
                and item_key not in self.visualized_3d_trajectories
            ):
                menu.addAction(add_3d_point_str)
                menu.addAction(add_3d_trajectory_str)

        # in this case we can use the item as base position, base orientation or 3d point
        if item_size == 3:
            if item_path == self.robot_state_path.base_position_path:
                menu.addAction(dont_use_as_base_position_str)
            else:
                menu.addAction(use_as_base_position_str)

            if item_path == self.robot_state_path.base_orientation_path:
                menu.addAction(dont_use_as_base_orientation_str)
            else:
                menu.addAction(use_as_base_orientation_str + " (Roll-Pitch-Yaw)")

            menu.addSeparator()

            if item_key in self.visualized_3d_points:
                menu.addAction(remove_3d_point_str)
            if item_key in self.visualized_3d_trajectories:
                menu.addAction(remove_3d_trajectory_str)
            if (
                item_key not in self.visualized_3d_points
                and item_key not in self.visualized_3d_trajectories
            ):
                menu.addAction(add_3d_point_str)
                menu.addAction(add_3d_trajectory_str)

        if item_size == 4:
            if item_path == self.robot_state_path.base_orientation_path:
                menu.addAction(dont_use_as_base_orientation_str)
            else:
                menu.addAction(use_as_base_orientation_str + " (xyzw Quaternion)")
                menu.addAction(use_as_base_orientation_str + " (wxyz Quaternion)")

        if item_size == 6:
            if item_key in self.visualized_3d_arrows:
                menu.addAction(remove_3d_arrow_str)
            else:
                menu.addAction(add_3d_arrow_str)

        # signals named image (e.g. the depth seen by a policy) can be rendered as a video
        image_layout = None
        if item_key in self.image_items:
            menu.addSeparator()
            menu.addAction(remove_image_str)
        elif re.search(r"image|img", item.text(0), re.IGNORECASE):
            image_layout = ImageLayout.from_signal(
                item_size, [item.child(i).text(0) for i in range(item_size)]
            )
            if image_layout is not None:
                menu.addSeparator()
                menu.addAction(
                    f"{render_as_image_str} ({image_layout.height}x{image_layout.width})"
                )

        if menu.isEmpty():
            return

        # show the menu
        action = menu.exec_(self.ui.variableTreeWidget.mapToGlobal(item_position))
        if action is None:
            return

        item_path = self.get_item_path(item)

        if (
            action.text() == add_3d_point_str
            or action.text() == add_3d_trajectory_str
            or action.text() == add_3d_arrow_str
        ):
            color = next(self.visualized_3d_points_colors_palette)

            item.setForeground(0, QtGui.QBrush(QtGui.QColor(color.as_hex())))

            if action.text() == add_3d_point_str:
                self.meshcat_provider.register_3d_point(
                    item_key, list(color.as_normalized_rgb())
                )
                self.signal_provider.register_3d_point(item_key, item_path)
                self.visualized_3d_points.add(item_key)
            elif action.text() == add_3d_trajectory_str:
                self.meshcat_provider.register_3d_trajectory(
                    item_key, list(color.as_normalized_rgb())
                )
                self.signal_provider.register_3d_trajectory(item_key, item_path)
                self.visualized_3d_trajectories.add(item_key)
            elif action.text() == add_3d_arrow_str:
                self.meshcat_provider.register_3d_arrow(
                    item_key, list(color.as_normalized_rgb())
                )
                self.signal_provider.register_3d_arrow(item_key, item_path)
                self.visualized_3d_arrows.add(item_key)
            else:
                raise ValueError("Unknown action")

        if action.text().startswith(render_as_image_str):
            self.__add_image_item(item, item_path, image_layout)

        if action.text() == remove_image_str:
            self.__remove_image_item(item, item_key)

        if action.text() == remove_3d_point_str:
            self.meshcat_provider.unregister_3d_point(item_key)
            self.signal_provider.unregister_3d_point(item_key)
            self.visualized_3d_points.remove(item_key)
            item.setForeground(0, QtGui.QBrush(QtGui.QColor(0, 0, 0)))

        if action.text() == remove_3d_trajectory_str:
            self.meshcat_provider.unregister_3d_trajectory(item_key)
            self.signal_provider.unregister_3d_trajectory(item_key)
            self.visualized_3d_trajectories.remove(item_key)
            item.setForeground(0, QtGui.QBrush(QtGui.QColor(0, 0, 0)))

        if action.text() == remove_3d_arrow_str:
            self.meshcat_provider.unregister_3d_arrow(item_key)
            self.signal_provider.unregister_3d_arrow(item_key)
            self.visualized_3d_arrows.remove(item_key)
            item.setForeground(0, QtGui.QBrush(QtGui.QColor(0, 0, 0)))

        if (
            use_as_base_orientation_str in action.text()
            or action.text() == use_as_base_position_str
        ):
            item.setBackground(0, QtGui.QBrush(selected_base_color))

        # check that the action is the one we want
        if action.text() == use_as_base_position_str:
            # if base position is already set we remove the color
            if self.robot_state_path.base_position_path:
                self.get_item_from_path(
                    self.robot_state_path.base_position_path
                ).setBackground(0, QtGui.QBrush(deselected_base_color))
            self.robot_state_path.base_position_path = item_path
            # In real time mode, add those signal to the provider buffer
            if (
                self.signal_provider
                and self.signal_provider.provider_type == ProviderType.REALTIME
            ):
                # Convert item_path to signal name string
                signal_name = f"{ROBOT_REALTIME_KEY}::" + "::".join(item_path)
                self.signal_provider.add_signals_to_buffer([signal_name])

        if use_as_base_orientation_str in action.text():
            # if base orientation is already set we remove the color
            if self.robot_state_path.base_orientation_path:
                self.get_item_from_path(
                    self.robot_state_path.base_orientation_path
                ).setBackground(0, QtGui.QBrush(deselected_base_color))
            self.robot_state_path.base_orientation_path = item_path
            if "wxyz Quaternion" in action.text():
                self.robot_state_path.base_orientation_type = "wxyz"
            elif "xyzw Quaternion" in action.text():
                self.robot_state_path.base_orientation_type = "xyzw"
            else:
                self.robot_state_path.base_orientation_type = "rpy"
            # In real time mode, add those signal to the provider buffer
            if (
                self.signal_provider
                and self.signal_provider.provider_type == ProviderType.REALTIME
            ):
                signal_name = f"{ROBOT_REALTIME_KEY}::" + "::".join(item_path)
                self.signal_provider.add_signals_to_buffer([signal_name])

        if action.text() == dont_use_as_base_position_str:
            self.robot_state_path.base_position_path = []
            # if the item is used as base orientation we do not remove the color
            if item_path != self.robot_state_path.base_orientation_path:
                item.setBackground(0, QtGui.QBrush(deselected_base_color))

        if action.text() == dont_use_as_base_orientation_str:
            self.robot_state_path.base_orientation_path = []
            self.robot_state_path.base_orientation_type = "rpy"
            # if the item is used as base position we do not remove the color
            if item_path != self.robot_state_path.base_position_path:
                item.setBackground(0, QtGui.QBrush(deselected_base_color))

        # we update the robot state path
        self.signal_provider.robot_state_path = self.robot_state_path

    def __add_image_item(self, item, item_path, image_layout):
        item_key = "/".join(item_path)

        # In real time mode, add the signal to the provider buffer
        if (
            self.signal_provider
            and self.signal_provider.provider_type == ProviderType.REALTIME
        ):
            signal_name = f"{ROBOT_REALTIME_KEY}::" + "::".join(item_path)
            self.signal_provider.add_signals_to_buffer([signal_name])

        image_item = ImageItem(self.signal_provider, item_path, image_layout)
        self.image_items[item_key] = image_item

        label = "/".join(item_path[-3:])
        tab_index = self.ui.meshcatAndVideoTab.addTab(
            image_item, get_icon("videocam-outline.svg"), label
        )
        self.ui.meshcatAndVideoTab.setTabToolTip(tab_index, item_key)
        self.ui.meshcatAndVideoTab.setCurrentIndex(tab_index)

        item.setBackground(0, QtGui.QBrush(QtGui.QColor(0, 120, 255, 70)))
        self.logger.write_to_log(
            f"Rendering '{item_key}' as a {image_layout.height}x{image_layout.width} image."
        )

    def __remove_image_item(self, item, item_key):
        image_item = self.image_items.pop(item_key)
        tab_index = self.ui.meshcatAndVideoTab.indexOf(image_item)
        if tab_index >= 0:
            self.ui.meshcatAndVideoTab.removeTab(tab_index)
        image_item.release()
        image_item.deleteLater()
        item.setBackground(0, QtGui.QBrush(QtGui.QColor(0, 0, 0, 0)))

    def get_item_from_path(self, path):
        item = self.ui.variableTreeWidget.topLevelItem(0)
        for subpath in path:
            # find the item given its name
            for child_id in range(item.childCount()):
                if item.child(child_id).text(0) == subpath:
                    item = item.child(child_id)
                    break
        return item

    def get_item_path(self, item):
        path = []
        while item.parent() is not None:
            path.append(item.text(0))
            item = item.parent()
        path.reverse()
        return path

    def _toggle_refresh_button_style(self):
        """Toggle the refresh button style to create a blinking effect."""
        if self.refresh_button_blink_state:
            # Normal state
            self.ui.refreshButton.setStyleSheet("")
        else:
            # Highlighted state - use a bright color to draw attention
            self.ui.refreshButton.setStyleSheet(
                "QPushButton { background-color: #4CAF50; border: 2px solid #45a049; }"
            )
        self.refresh_button_blink_state = not self.refresh_button_blink_state

    def _check_for_new_metadata(self):
        """Periodically check if new metadata is available in realtime mode."""
        if not isinstance(self.signal_provider, RealtimeSignalProvider):
            return

        provider = self.signal_provider
        if provider.check_for_new_metadata():
            # New metadata is available - enable and start blinking the button
            self.ui.refreshButton.setEnabled(True)
            if not self.refresh_button_timer.isActive():
                self.refresh_button_timer.start()
            self.logger.write_to_log("New metadata available. Click refresh to update.")

    def refreshButton_on_click(self):
        """Fetch fresh realtime metadata, add only new keys, and extend the tree."""

        if not isinstance(self.signal_provider, RealtimeSignalProvider):
            self.logger.write_to_log(
                "Refresh metadata: realtime provider not connected."
            )
            return

        provider = self.signal_provider
        new_items = provider.update_metadata()
        if not new_items:
            self.logger.write_to_log("Refresh metadata: no new metadata keys found.")
            return

        self.logger.write_to_log(f"New metadata keys found: {list(new_items.keys())}")

        # Treat the top-level item as the robot_realtime node
        root_item = self.ui.variableTreeWidget.topLevelItem(0)
        if root_item is None or root_item.text(0) != ROBOT_REALTIME_KEY:
            self.logger.write_to_log(
                f"Refresh metadata: '{ROBOT_REALTIME_KEY}' node not found, cannot insert."
            )
            return

        # Merge the children of the new subtree into the existing robot_realtime node
        with QMutexLocker(provider.index_lock):
            self._update_variable_tree_widget(
                provider.data[ROBOT_REALTIME_KEY], root_item
            )

        # After refresh, disable the button and stop blinking until new metadata arrives
        self.ui.refreshButton.setEnabled(False)
        self.refresh_button_timer.stop()
        self.ui.refreshButton.setStyleSheet("")  # Reset to normal style
        self.refresh_button_blink_state = False


class Logger:
    """
    Logger class shows events during the execution of the viewer.
    """

    def __init__(self, log_widget, scroll_area, add_time=True):
        # set log widget from main window
        self.log_widget = log_widget

        # set scroll area form main window
        self.scroll_area = scroll_area

        self.add_time = add_time

    def write_to_log(self, text, font_color=None, background_color=None):
        """
        Log the text "text" with a timestamp.
        """

        # extract current text from the log widget
        current_text = self.log_widget.text()

        if font_color is not None:
            text = '<font color="' + str(font_color) + '">' + text + "</font>"

        if background_color is not None:
            text = (
                '<span style="background-color:'
                + str(background_color)
                + '">'
                + text
                + "</span>"
            )

        # compose new text
        # convert local time to string
        if self.add_time:
            time_str = strftime(" [%H:%M:%S] ", localtime())
            #
            new_text = current_text + time_str + text + "<br>"
        else:
            new_text = current_text + text + "<br>"

        # log into the widget
        self.log_widget.setText(new_text)

        # scroll down text
        self.scroll_down()

    def clean(self):
        self.log_widget.clear()

    def scroll_down(self):
        """
        Scroll down the slider of the scroll area
        linked to this logger
        """
        # extract scroll bar from the scroll area
        scroll_bar = self.scroll_area.verticalScrollBar()

        # set maximum value of the slider, 1000 is enough
        scroll_bar.setMaximum(1000)
        scroll_bar.setValue(scroll_bar.maximum())


if __name__ == "__main__":
    # construct a QApplication
    app = QtWidgets.QApplication(sys.argv)

    # instantiate the main window
    gui = RobotViewerMainWindow()

    # show the main window
    gui.show()

    exec_method = getattr(app, "exec", None)
    if exec_method is None:
        exec_method = app.exec_

    sys.exit(exec_method())
