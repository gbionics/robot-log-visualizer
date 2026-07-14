# Copyright (C) 2022 Istituto Italiano di Tecnologia (IIT). All rights reserved.
# This software may be modified and distributed under the terms of the
# Released under the terms of the BSD 3-Clause License

from __future__ import annotations

from enum import Enum


class PeriodicThreadState(Enum):
    running = (0,)
    pause = (1,)
    closed = 2


class RobotStatePath:
    def __init__(self):
        self.joints_state_path = []
        self.base_orientation_path = []
        self.base_orientation_type = "rpy"  # "rpy", "xyzw", or "wxyz"
        self.base_position_path = []


class Color:
    """Utility class representing a colour with handy conversions."""

    def __init__(self, hex: str = "#000000"):
        self.hex = hex

    def as_hex(self) -> str:
        return self.hex

    def as_rgb(self) -> tuple[int, int, int]:
        return self.hex_to_rgb(self.hex)

    def as_normalized_rgb(self) -> tuple[float, float, float]:
        return self.get_to_normalized_rgb(self.hex)

    @staticmethod
    def hex_to_rgb(hex_value: str) -> tuple[int, int, int]:
        # https://stackoverflow.com/questions/29643352/converting-hex-to-rgb-value-in-python
        hex_value = hex_value.lstrip("#")
        hlen = len(hex_value)
        return tuple(
            int(hex_value[i : i + hlen // 3], 16)
            for i in range(0, hlen, hlen // 3)
        )

    @staticmethod
    def get_to_normalized_rgb(hex_value: str) -> tuple[float, float, float]:
        rgb = Color.hex_to_rgb(hex_value)
        return (rgb[0] / 255.0, rgb[1] / 255.0, rgb[2] / 255.0)


DEFAULT_COLOR_CYCLE = (
    "#1f77b4",  # blue
    "#ff7f0e",  # orange
    "#2ca02c",  # green
    "#d62728",  # red
    "#9467bd",  # purple
    "#8c564b",  # brown
    "#e377c2",  # pink
    "#7f7f7f",  # gray
    "#bcbd22",  # olive
    "#17becf",  # cyan
)


class ColorPalette:
    """Cycling palette yielding :class:`Color` objects.

    Args:
        colors: Optional iterable of colour specifications (hex strings or
            :class:`Color` instances). When omitted, ``DEFAULT_COLOR_CYCLE`` is
            used.
    """

    def __init__(self, colors=None):
        palette = colors or DEFAULT_COLOR_CYCLE

        self._color_palette = [
            color if isinstance(color, Color) else Color(str(color))
            for color in palette
        ]

        if not self._color_palette:
            raise ValueError("ColorPalette requires at least one colour")

        self._index = 0

    def __iter__(self):
        self._index = 0
        return self

    def __next__(self) -> Color:
        color = self._color_palette[self._index]
        self._index = (self._index + 1) % len(self._color_palette)
        return color

    def __len__(self) -> int:
        return len(self._color_palette)

    def __call__(self, index: int) -> Color:
        return self._color_palette[index % len(self._color_palette)]

    def reset(self) -> None:
        self._index = 0
