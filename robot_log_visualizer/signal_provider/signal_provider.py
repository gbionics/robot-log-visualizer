# Copyright (C) 2022 Istituto Italiano di Tecnologia (IIT). All rights reserved.
# This software may be modified and distributed under the terms of the
# Released under the terms of the BSD 3-Clause License

import abc
import math
from enum import Enum

import h5py
import idyntree.swig as idyn
import numpy as np
from qtpy.QtCore import QMutex, QMutexLocker, QThread, Signal

pyqtSignal = Signal

from robot_log_visualizer.utils.utils import PeriodicThreadState, RobotStatePath


class ProviderType(Enum):
    OFFLINE = 0
    REALTIME = 1
    NOT_DEFINED = 2


class TextLoggingMsg:
    def __init__(self, level, text):
        self.level = level
        self.text = text

    def color(self):
        if self.level == "ERROR":
            return "#d62728"
        elif self.level == "WARNING":
            return "#ff7f0e"
        elif self.level == "DEBUG":
            return "#1f77b4"
        elif self.level == "INFO":
            return "#2ca02c"
        else:
            return "black"


class SignalProvider(QThread):
    update_index_signal = pyqtSignal()

    def __init__(
        self, period: float, signal_root_name: str, provider_type: ProviderType
    ):
        QThread.__init__(self)

        # set device state
        self._state = PeriodicThreadState.pause
        self.state_lock = QMutex()

        self._index = 0
        self.index_lock = QMutex()

        self._robot_state_path = RobotStatePath()
        self.robot_state_path_lock = QMutex()

        self._3d_points_path = {}
        self._3d_points_path_lock = QMutex()

        self._3d_trajectories_path = {}
        self._3d_trajectories_path_lock = QMutex()

        self._3d_arrows = {}
        self._3d_arrows_path_lock = QMutex()

        self._max_arrow = 0
        self._custom_max_arrow = 0
        self._is_custom_max_arrow_used = False
        self._max_arrow_mutex = QMutex()

        self.period = period

        self.data = {}
        self.text_logging_data = {}

        self.initial_time = math.inf
        self.end_time = -math.inf

        self.joints_name = []
        self.robot_name = ""

        self.root_name = signal_root_name

        self._current_time = 0

        self._playback_speed = 1.0
        self._playback_speed_lock = QMutex()

        self.trajectory_span = 200

        self.provider_type = provider_type

    def __populate_text_logging_data(self, file_object):
        data = {}
        for key, value in file_object.items():
            if not isinstance(value, h5py._hl.group.Group):
                continue
            if key == "#refs#":
                continue
            if key == "#subsystem#":
                continue
            if "data" in value.keys():
                data[key] = {}
                level_ref = value["data"]["level"]
                text_ref = value["data"]["text"]

                data[key]["timestamps"] = np.squeeze(np.array(value["timestamps"]))

                # New way to store the struct array in robometry https://github.com/robotology/robometry/pull/175
                if text_ref.shape[0] == len(data[key]["timestamps"]):
                    # If len(value[text[0]].shape) == 2 then the text contains a string, otherwise it is empty
                    # We need to manually check the shape to handle the case in which the text is empty
                    data[key]["data"] = [
                        (
                            TextLoggingMsg(
                                text="".join(chr(c[0]) for c in value[text[0]]),
                                level="".join(chr(c[0]) for c in value[level[0]]),
                            )
                            if len(value[text[0]].shape) == 2
                            else TextLoggingMsg(
                                text="",
                                level="".join(chr(c[0]) for c in value[level[0]]),
                            )
                        )
                        for text, level in zip(text_ref, level_ref)
                    ]

                # Old approach (before https://github.com/robotology/robometry/pull/175)
                else:
                    data[key]["data"] = [
                        TextLoggingMsg(
                            text="".join(chr(c[0]) for c in value[text]),
                            level="".join(chr(c[0]) for c in value[level]),
                        )
                        for text, level in zip(text_ref[0], level_ref[0])
                    ]

            else:
                data[key] = self.__populate_text_logging_data(file_object=value)

        return data

    def __populate_numerical_data(self, file_object):
        data = {}
        # Cache for timestamp deduplication: share identical arrays
        ts_cache = getattr(self, '_ts_cache', {})
        self._ts_cache = ts_cache

        for key, value in file_object.items():
            if not isinstance(value, h5py._hl.group.Group):
                continue
            if key == "#refs#":
                continue
            if key == "#subsystem#":
                continue
            if key == "log":
                continue
            if "data" in value.keys():
                data[key] = {}
                data[key]["data"] = np.atleast_1d(np.squeeze(np.array(value["data"])))
                raw_ts = np.atleast_1d(
                    np.squeeze(np.array(value["timestamps"]))
                )

                # Deduplicate: reuse an existing identical timestamp array
                ts_key = (len(raw_ts), raw_ts[0], raw_ts[-1])
                if ts_key in ts_cache and np.array_equal(ts_cache[ts_key], raw_ts):
                    data[key]["timestamps"] = ts_cache[ts_key]
                else:
                    ts_cache[ts_key] = raw_ts
                    data[key]["timestamps"] = raw_ts

                # if the initial or end time has been updated we can also update the entire timestamps dataset
                if data[key]["timestamps"][0] < self.initial_time:
                    self.timestamps = data[key]["timestamps"]
                    self.initial_time = self.timestamps[0]

                if data[key]["timestamps"][-1] > self.end_time:
                    self.timestamps = data[key]["timestamps"]
                    self.end_time = self.timestamps[-1]

                # In yarp telemetry v0.4.0 the elements_names was saved.
                if "elements_names" in value.keys():
                    elements_names_ref = value["elements_names"]
                    data[key]["elements_names"] = [
                        "".join(chr(c[0]) for c in value[ref])
                        for ref in elements_names_ref[0]
                    ]
            else:
                data[key] = self.__populate_numerical_data(file_object=value)

        return data

    def open_mat_file(self, file_name: str):
        with h5py.File(file_name, "r") as file:
            root_variable = file.get(self.root_name)
            self.data = self.__populate_numerical_data(file)

            if "log" in root_variable.keys():
                self.text_logging_data["log"] = self.__populate_text_logging_data(
                    root_variable["log"]
                )

            for name in file.keys():
                if "description_list" in file[name].keys():
                    self.root_name = name
                    break

            joint_ref = root_variable["description_list"]
            self.joints_name = [
                "".join(chr(c[0]) for c in file[ref]) for ref in joint_ref[0]
            ]
            if "yarp_robot_name" in root_variable.keys():
                robot_name_ref = root_variable["yarp_robot_name"]
                try:
                    self.robot_name = "".join(chr(c[0]) for c in robot_name_ref)
                except:
                    pass
            self.index = 0

        self.provider_type = ProviderType.OFFLINE

    @abc.abstractmethod
    def open(self, source: str) -> bool:
        return False

    @abc.abstractmethod
    def __len__(self):
        pass

    @property
    def state(self):
        locker = QMutexLocker(self.state_lock)
        value = self._state
        return value

    @state.setter
    def state(self, new_state: PeriodicThreadState):
        locker = QMutexLocker(self.state_lock)
        self._state = new_state

    @property
    def index(self):
        locker = QMutexLocker(self.index_lock)
        value = self._index
        return value

    @index.setter
    def index(self, index):
        locker = QMutexLocker(self.index_lock)
        self._index = index

    @property
    def robot_state_path(self):
        locker = QMutexLocker(self.robot_state_path_lock)
        value = self._robot_state_path
        return value

    @property
    def max_arrow(self):
        locker = QMutexLocker(self._max_arrow_mutex)
        if self._is_custom_max_arrow_used:
            return self._custom_max_arrow
        else:
            return self._max_arrow

    @robot_state_path.setter
    def robot_state_path(self, robot_state_path):
        locker = QMutexLocker(self.robot_state_path_lock)
        self._robot_state_path = robot_state_path

    def register_update_index(self, slot):
        self.update_index_signal.connect(slot)

    def set_dataset_percentage(self, percentage):
        self.update_index(int(percentage * len(self)))

    def update_index(self, index):
        locker = QMutexLocker(self.index_lock)
        self._index = max(min(index, len(self.timestamps) - 1), 0)
        self._current_time = self.timestamps[self._index] - self.initial_time

    @property
    def current_time(self):
        locker = QMutexLocker(self.index_lock)
        value = self._current_time
        return value

    @property
    def playback_speed(self):
        locker = QMutexLocker(self._playback_speed_lock)
        return self._playback_speed

    @playback_speed.setter
    def playback_speed(self, value):
        locker = QMutexLocker(self._playback_speed_lock)
        self._playback_speed = value

    def get_item_from_path(self, path, default_path=None):
        data = self.data[self.root_name]

        if not path:
            if default_path is None:
                return None, None
            else:
                for key in default_path:
                    data = data[key]
                return data["data"], data["timestamps"]

        for key in path:
            data = data[key]

        return data["data"], data["timestamps"]

    def get_item_from_path_at_index(self, path, index, default_path=None, neighbor=0):
        data, timestamps = self.get_item_from_path(path, default_path)
        if (
            data is None
            or timestamps is None
            or len(self.timestamps) == 0
            or len(timestamps) == 0
        ):
            return None

        if self.timestamps is None or len(self.timestamps) == 0:
            return None

        target = self.timestamps[index]
        # Use binary search (O(log n)) instead of linear scan (O(n))
        closest_index = np.searchsorted(timestamps, target)
        # Clamp and pick the nearer of the two neighbors
        if closest_index >= len(timestamps):
            closest_index = len(timestamps) - 1
        elif closest_index > 0 and (
            abs(timestamps[closest_index - 1] - target)
            <= abs(timestamps[closest_index] - target)
        ):
            closest_index -= 1

        if neighbor == 0:
            return data[closest_index, :]

        initial_index = max(0, closest_index - neighbor)
        end_index = min(len(timestamps), closest_index + neighbor + 1)
        return data[initial_index:end_index, :]

    def get_robot_state_at_index(self, index):
        robot_state = {}

        self.robot_state_path_lock.lock()
        robot_state["joints_position"] = self.get_item_from_path_at_index(
            self._robot_state_path.joints_state_path,
            index,
            default_path=["joints_state", "positions"],
        )

        robot_state["base_position"] = self.get_item_from_path_at_index(
            self._robot_state_path.base_position_path, index
        )

        robot_state["base_orientation"] = self.get_item_from_path_at_index(
            self._robot_state_path.base_orientation_path, index
        )
        self.robot_state_path_lock.unlock()

        if robot_state["joints_position"] is None:
            robot_state["joints_position"] = np.zeros(len(self.joints_name))

        if robot_state["base_position"] is None:
            robot_state["base_position"] = np.zeros(3)

        if robot_state["base_orientation"] is None:
            robot_state["base_orientation"] = np.zeros(3)

        # check the size of the base_orientation if 3 we assume is store ad rpy otherwise as a quaternion we need to convert it in a rotation matrix
        if robot_state["base_orientation"].shape[0] == 3:
            robot_state["base_orientation"] = idyn.Rotation.RPY(
                robot_state["base_orientation"][0],
                robot_state["base_orientation"][1],
                robot_state["base_orientation"][2],
            ).toNumPy()
        if robot_state["base_orientation"].shape[0] == 4:
            # convert the x y z w quaternion into w x y z
            tmp_quat = robot_state["base_orientation"]
            tmp_quat = np.array([tmp_quat[3], tmp_quat[0], tmp_quat[1], tmp_quat[2]])

            robot_state["base_orientation"] = idyn.Rotation.RotationFromQuaternion(
                tmp_quat
            ).toNumPy()

        return robot_state

    def set_custom_max_arrow(self, use_custom_max_arrow: bool, max_arrow: float):
        _ = QMutexLocker(self._max_arrow_mutex)
        self._is_custom_max_arrow_used = use_custom_max_arrow
        if use_custom_max_arrow:
            self._custom_max_arrow = max_arrow
        else:
            self._custom_max_arrow = 0

    def get_3d_point_at_index(self, index):
        points = {}

        self._3d_points_path_lock.lock()

        for key, value in self._3d_points_path.items():
            # force the size of the points to be 3 if less than 3 we assume that the point is a 2d point and we add a 0 as z coordinate
            points[key] = self.get_item_from_path_at_index(value, index)
            if points[key].shape[0] < 3:
                points[key] = np.concatenate(
                    (points[key], np.zeros(3 - points[key].shape[0]))
                )

        self._3d_points_path_lock.unlock()

        return points

    def get_3d_arrow_at_index(self, index):
        arrows = {}

        self._3d_arrows_path_lock.lock()

        for key, value in self._3d_arrows.items():
            arrows[key] = self.get_item_from_path_at_index(value, index)

        self._3d_arrows_path_lock.unlock()

        return arrows

    def get_3d_trajectory_at_index(self, index):
        trajectories = {}

        self._3d_trajectories_path_lock.lock()

        for key, value in self._3d_trajectories_path.items():
            trajectories[key] = self.get_item_from_path_at_index(
                value, index, neighbor=self.trajectory_span
            )
            # force the size of the points to be 3 if less than 3 we assume that the point is a 2d point and we add a 0 as z coordinate
            if trajectories[key].shape[1] < 3:
                trajectories[key] = np.concatenate(
                    (
                        trajectories[key],
                        np.zeros(
                            (trajectories[key].shape[0], 3 - trajectories[key].shape[1])
                        ),
                    ),
                    axis=1,
                )

        self._3d_trajectories_path_lock.unlock()

        return trajectories

    def register_3d_point(self, key, points_path):
        self._3d_points_path_lock.lock()
        self._3d_points_path[key] = points_path
        self._3d_points_path_lock.unlock()

    def unregister_3d_point(self, key):
        self._3d_points_path_lock.lock()
        del self._3d_points_path[key]
        self._3d_points_path_lock.unlock()

    def register_3d_arrow(self, key, arrow_path):
        self._3d_arrows_path_lock.lock()
        self._3d_arrows[key] = arrow_path
        for _, value in self._3d_arrows.items():
            data, _ = self.get_item_from_path(arrow_path)
            arrow = data[:, 3:]
            self._max_arrow_mutex.lock()
            self._max_arrow = max(
                np.max(np.linalg.norm(arrow, axis=1)), self._max_arrow
            )
            self._max_arrow_mutex.unlock()
        self._3d_arrows_path_lock.unlock()

    def unregister_3d_arrow(self, key):
        self._3d_arrows_path_lock.lock()
        del self._3d_arrows[key]
        self._3d_arrows_path_lock.unlock()

    def register_3d_trajectory(self, key, trajectory_path):
        self._3d_trajectories_path_lock.lock()
        self._3d_trajectories_path[key] = trajectory_path
        self._3d_trajectories_path_lock.unlock()

    def unregister_3d_trajectory(self, key):
        self._3d_trajectories_path_lock.lock()
        del self._3d_trajectories_path[key]
        self._3d_trajectories_path_lock.unlock()

    @abc.abstractmethod
    def run(self):
        return
