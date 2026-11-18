#!/usr/bin/env python3

# Copyright (C) 2022 Istituto Italiano di Tecnologia (IIT). All rights reserved.
# This software may be modified and distributed under the terms of the
# Released under the terms of the BSD 3-Clause License

import sys

# GUI
from qtpy.QtWidgets import QApplication
from robot_log_visualizer.ui.gui import RobotViewerMainWindow

# Meshcat
from robot_log_visualizer.robot_visualizer.meshcat_provider import MeshcatProvider


def main():
    thread_periods = {
        "meshcat_provider": 0.05,
        "signal_provider": 0.03,
        "plot_animation": 0.03,
    }

    meshcat_provider = MeshcatProvider(period=thread_periods["meshcat_provider"])

    # instantiate a QApplication
    app = QApplication(sys.argv)

    # instantiate the main window
    gui = RobotViewerMainWindow(
        signal_provider_period=thread_periods["signal_provider"],
        meshcat_provider=meshcat_provider,
        animation_period=thread_periods["plot_animation"],
    )

    # show the main window
    gui.show()

    exec_method = getattr(app, "exec", None)
    if exec_method is None:
        exec_method = app.exec_

    return exec_method()


if __name__ == "__main__":
    sys.exit(main())
