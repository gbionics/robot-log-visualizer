# Copyright (C) 2022 Istituto Italiano di Tecnologia (IIT). All rights reserved.
# Copyright (C) 2026 Generative Bionics S.R.L. All rights reserved.
# This software may be modified and distributed under the terms of the
# Released under the terms of the BSD 3-Clause License

from qtpy.QtCore import QUrl
from qtpy.QtMultimedia import QMediaPlayer
try:  # Qt 6
    from qtpy.QtMultimedia import QAudioOutput  # type: ignore
except ImportError:  # Qt 5 fallback
    QAudioOutput = None  # type: ignore

try:  # Qt 5 fallback
    from qtpy.QtMultimedia import QMediaContent  # type: ignore
except ImportError:  # Qt 6
    QMediaContent = None  # type: ignore
from qtpy.QtWidgets import QFrame

from robot_log_visualizer.ui.ui_loader import load_ui
import os
from typing import Optional


class VideoItem(QFrame):
    def __init__(self, video_filename: str, start_time: Optional[float] = None):
        super().__init__(None)
        self.ui = load_ui("video_tab.ui", self)

        # time of the first frame, used to synchronize the video with the dataset
        self.start_time = start_time

        self.media_player = QMediaPlayer(self)

        self.audio_output = None
        if QAudioOutput is not None and hasattr(self.media_player, "setAudioOutput"):
            self.audio_output = QAudioOutput(parent=self)
            self.media_player.setAudioOutput(self.audio_output)

        self.media_player.setVideoOutput(self.ui.webcamView)

        self.media_loaded = False

        if os.path.isfile(video_filename):
            url = QUrl.fromLocalFile(video_filename)
            if hasattr(self.media_player, "setSource"):
                self.media_player.setSource(url)
            elif hasattr(self.media_player, "setMedia"):
                if QMediaContent is not None:
                    self.media_player.setMedia(QMediaContent(url))
                else:
                    self.media_player.setMedia(url)
            else:
                raise AttributeError("QMediaPlayer backend missing media source setter")
            self.media_loaded = True
