# Copyright (C) 2022 Istituto Italiano di Tecnologia (IIT). All rights reserved.
# This software may be modified and distributed under the terms of the
# Released under the terms of the BSD 3-Clause License

"""Utilities to load Qt Designer `.ui` files at runtime."""

from __future__ import annotations

from importlib import resources
from typing import Any

from qtpy import uic


class UiProxy:
    """Proxy object exposing UI attributes through the ``ui`` namespace.

    Historically the project relied on the Qt Designer generated ``Ui_*``
    classes that exposed widgets under a ``ui`` attribute. We keep the same
    interface by wrapping the current instance in this proxy so existing code
    can continue to access widgets as ``self.ui.someWidget``.
    """

    def __init__(self, target: Any) -> None:
        super().__setattr__("_target", target)

    def __getattr__(self, name: str) -> Any:
        return getattr(self._target, name)

    def __setattr__(self, name: str, value: Any) -> None:
        setattr(self._target, name, value)


def load_ui(ui_filename: str, baseinstance: Any) -> UiProxy:
    """Load a Qt Designer ``.ui`` file into an existing widget instance.

    Parameters
    ----------
    ui_filename
        Name of the ``.ui`` file stored inside ``robot_log_visualizer.ui.misc``.
    baseinstance
        The Qt widget instance where the UI should be loaded.

    Returns
    -------
    UiProxy
        A proxy object that exposes the loaded widgets under the ``ui``
        namespace, preserving the previous ``self.ui`` calling convention.
    """

    package = "robot_log_visualizer.ui.misc"
    with resources.path(package, ui_filename) as ui_path:
        uic.loadUi(str(ui_path), baseinstance)

    return UiProxy(baseinstance)
