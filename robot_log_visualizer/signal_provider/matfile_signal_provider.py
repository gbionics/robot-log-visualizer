# Copyright (C) 2022 Istituto Italiano di Tecnologia (IIT). All rights reserved.
# This software may be modified and distributed under the terms of the
# Released under the terms of the BSD 3-Clause License

import time
import h5py
import numpy as np
from robot_log_visualizer.signal_provider.signal_provider import (
    SignalProvider,
    ProviderType,
    TextLoggingMsg,
)
from robot_log_visualizer.utils.utils import PeriodicThreadState


class MatfileSignalProvider(SignalProvider):
    def __init__(self, period: float, signal_root_name: str):
        super().__init__(period, signal_root_name, ProviderType.OFFLINE)

    def __len__(self):
        return len(self.timestamps)

    def __populate_text_logging_data(self, file_object):
        data = {}
        for key, value in file_object.items():
            if not isinstance(value, h5py._hl.group.Group):
                continue
            if key == "#refs#":
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
            if key == "log":
                continue
            if "data" in value.keys():
                data[key] = {}
                data[key]["data"] = np.squeeze(np.array(value["data"]))
                raw_ts = np.squeeze(np.array(value["timestamps"]))

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

    def open(self, source: str) -> bool:
        with h5py.File(source, "r") as file:
            self.data = self.__populate_numerical_data(file)

            # Find the root variable: try the configured name first, then
            # search for any top-level group that contains description_list.
            root_variable = file.get(self.root_name)
            if root_variable is None:
                for name in file.keys():
                    item = file[name]
                    if isinstance(item, h5py._hl.group.Group) and "description_list" in item.keys():
                        self.root_name = name
                        root_variable = item
                        break

            if root_variable is not None:
                if "log" in root_variable.keys():
                    self.text_logging_data["log"] = self.__populate_text_logging_data(
                        root_variable["log"]
                    )

                if "description_list" in root_variable.keys():
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

            version_ref = file.get("version")
            if isinstance(version_ref, h5py.Dataset):
                try:
                    self.version = "".join(chr(c) for c in np.ravel(version_ref[()]))
                except (TypeError, ValueError):
                    pass
            self.index = 0

        # Pre-compute relative timestamps for binary search in run()
        self._relative_timestamps = self.timestamps - self.initial_time

        return True

    def run(self):
        prev_wall = time.time()
        was_running = False
        while True:
            start = time.time()
            if self.state == PeriodicThreadState.running:
                now = time.time()
                if not was_running:
                    # Just resumed — ignore stale elapsed time
                    prev_wall = now
                wall_elapsed = now - prev_wall
                prev_wall = now
                was_running = True
                self.index_lock.lock()
                self._current_time += wall_elapsed * self.playback_speed
                self._current_time = min(
                    self._current_time, self._relative_timestamps[-1]
                )

                # Binary search (O(log n)) instead of linear scan
                self._index = int(
                    np.searchsorted(self._relative_timestamps, self._current_time)
                )

                self.index_lock.unlock()

                self.update_index_signal.emit()
            else:
                was_running = False

            sleep_time = self.period - (time.time() - start)
            if sleep_time > 0:
                time.sleep(sleep_time)

            if self.state == PeriodicThreadState.closed:
                return
