"""GUI scenario run in a separate pytest process by test_visualizer.py.

The child process isolates native Qt crashes and allows a hard timeout.
3D rendering and real-time streaming require manual testing.
"""

import gc
import os
import sys
import time

import h5py
import numpy as np
import pytest


@pytest.fixture
def sample_log(tmp_path):
    model = tmp_path / "robot.urdf"
    model.write_text(
        '<robot name="test_robot"><link name="base">'
        '<visual><geometry><box size="0.1 0.1 0.1"/></geometry></visual>'
        '</link></robot>',
        encoding="utf-8",
    )
    log = tmp_path / "log.mat"
    timestamps = 100.0 + np.arange(101) * 0.05
    values = np.column_stack((np.sin(timestamps), np.cos(timestamps)))
    with h5py.File(log, "w") as file:
        file.create_dataset("version", data=np.array([ord(c) for c in "0.1.0"]))
        root = file.create_group("robot_logger_device")
        root.create_dataset(
            "yarp_robot_name",
            data=np.array([[ord(c)] for c in str(model)]),
        )
        signal = root.create_group("signal")
        signal.create_dataset("data", data=values[:, None, :])
        signal.create_dataset("timestamps", data=timestamps[:, None])
        joints = root.create_group("joints_state").create_group("positions")
        joints.create_dataset("data", data=np.zeros((len(timestamps), 1, 2)))
        joints.create_dataset("timestamps", data=timestamps[:, None])
    return log, timestamps, values


@pytest.fixture(scope="session")
def application():
    with pytest.MonkeyPatch.context() as monkeypatch:
        # Set these before importing Qt, including when running on a desktop.
        qt_api = os.environ.get("QT_API", "pyside6")
        monkeypatch.setenv("QT_API", qt_api)
        # Use the runner's Windows desktop for WebEngine's native surfaces.
        # The offscreen plugin cannot provide the same Windows graphics setup.
        platform = "windows" if sys.platform == "win32" else "offscreen"
        monkeypatch.setenv("QT_QPA_PLATFORM", platform)
        monkeypatch.setenv("QT_QUICK_BACKEND", "software")
        monkeypatch.setenv("QTWEBENGINE_CHROMIUM_FLAGS", "--disable-gpu")
        if sys.platform == "win32":
            # The sandboxed renderer cannot reliably load the conda DLLs on
            # Windows CI. These tests only visit the local Meshcat server.
            monkeypatch.setenv("QTWEBENGINE_DISABLE_SANDBOX", "1")

        from qtpy import API_NAME, QT_VERSION
        from qtpy.QtCore import Qt
        from qtpy.QtWidgets import QApplication
        # Import WebEngine before constructing QApplication, as in the entry point.
        from robot_log_visualizer import __main__  # noqa: F401
        import pyqtgraph as pg

        pg.setConfigOption("useOpenGL", False)
        expected_api, expected_version = {
            "pyside6": ("PySide6", "6."),
            "pyqt5": ("PyQt5", "5."),
        }[qt_api]
        assert API_NAME == expected_api
        assert QT_VERSION.startswith(expected_version)
        QApplication.setAttribute(Qt.AA_UseSoftwareOpenGL)
        app = QApplication([])
        yield app
        # Dispose Python/Qt reference cycles while QApplication is still alive.
        gc.collect()
        app.processEvents()


@pytest.fixture
def wait_until(application):
    from qtpy.QtTest import QTest

    def wait(predicate, message):
        deadline = time.monotonic() + 10
        while not predicate() and time.monotonic() < deadline:
            QTest.qWait(20)
        assert predicate(), message

    return wait


@pytest.fixture
def visualizer(application, wait_until):
    from robot_log_visualizer.__main__ import MeshcatProvider, RobotViewerMainWindow

    provider = MeshcatProvider(period=0.05)
    window = RobotViewerMainWindow(0.03, provider, 0.03)
    try:
        loaded = []
        load_errors = []

        def record_load(info):
            if info.errorCode():
                load_errors.append(
                    f"{info.url().toString()}: {info.errorDomain().name} "
                    f"({info.errorCode()}): {info.errorString()}"
                )

        page = window.ui.meshcatView.page()
        if hasattr(page, "loadingChanged"):  # Qt 6 only
            page.loadingChanged.connect(record_load)
        page.renderProcessTerminated.connect(
            lambda status, code: load_errors.append(
                f"Renderer terminated: {status!r} "
                f"(exit code {code}, 0x{code & 0xFFFFFFFF:08X})"
            )
        )
        window.ui.meshcatView.loadFinished.connect(loaded.append)
        window.show()
        wait_until(lambda: bool(loaded), "Meshcat page did not finish loading")
        assert loaded[-1], f"Meshcat page failed to load: {load_errors}"
        yield window, provider
    finally:
        window.close()
        if window.signal_provider is not None:
            assert not window.signal_provider.isRunning()
        assert not provider.isRunning()
        application.processEvents()


def test_log_loading_plotting_and_playback(visualizer, sample_log, wait_until, monkeypatch):
    from qtpy.QtTest import QTest
    from robot_log_visualizer.utils.utils import PeriodicThreadState

    window, provider = visualizer
    log, timestamps, values = sample_log
    # Replace only the native file picker; use the GUI's normal load path.
    monkeypatch.setattr(
        "robot_log_visualizer.ui.gui.QFileDialog.getOpenFileName",
        lambda *args, **kwargs: (str(log), "*.mat"),
    )
    assert window.open_mat_file()
    assert window.dataset_loaded
    assert "base" in provider.robot_frames(), "Robot model was not loaded"
    assert len(window.signal_provider) == len(timestamps)
    assert window.ui.startButton.isEnabled()

    tree = window.ui.variableTreeWidget
    root_item = tree.topLevelItem(0)
    signal_item = next(
        root_item.child(i) for i in range(root_item.childCount())
        if root_item.child(i).text(0) == "signal"
    )
    leaf = signal_item.child(0)
    assert leaf.text(0) == "Element 0"
    leaf.setSelected(True)
    tree.itemClicked.emit(leaf, 0)
    canvas = window.plot_tabs[0].active_plot.canvas
    curves = canvas._plot.listDataItems()
    assert len(curves) == 1, "Selecting a signal did not create a curve"
    x, y = curves[0].getData()
    np.testing.assert_allclose(x, timestamps - timestamps[0])
    np.testing.assert_allclose(y, values[:, 0])

    window.ui.timeSlider.sliderPressed.emit()
    window.ui.timeSlider.setValue(50)
    window.ui.timeSlider.sliderMoved.emit(50)
    window.ui.timeSlider.sliderReleased.emit()
    assert window.signal_provider.index == 50
    assert window.signal_provider.current_time == pytest.approx(2.5)
    assert window.ui.timeLabel.text() == "2.50"
    window.update_index()
    assert canvas._vline.value() == pytest.approx(2.5)

    window.ui.startButton.click()
    wait_until(
        lambda: window.signal_provider.index > 50,
        "Playback did not advance the log",
    )
    window.ui.pauseButton.click()
    assert window.signal_provider.state == PeriodicThreadState.pause
    assert window.ui.startButton.isEnabled()
    # Allow any in-flight iteration to finish before checking it stays paused.
    QTest.qWait(100)
    paused_index = window.signal_provider.index
    QTest.qWait(100)
    assert window.signal_provider.index == paused_index

    # Exercise the QActionGroup menu and the named layouts loaded from Designer.
    window.plot_layout_button._actions[(2, 2)].trigger()
    assert len(window.plot_tabs[0].visible_plot_items) == 4
    window.plot_layout_button._actions[(1, 1)].trigger()
    assert len(window.plot_tabs[0].visible_plot_items) == 1

    from qtpy.QtCore import QTimer
    from qtpy.QtWidgets import QDialog, QDialogButtonBox
    from robot_log_visualizer.ui.gui import SetRobotModelDialog, build_plot_title_box_dialog

    dialog = SetRobotModelDialog(provider, window, dataset_loaded=True)
    assert dialog.ui.frameNameComboBox.findText("base") >= 0
    QTimer.singleShot(0, dialog.reject)
    assert dialog.exec() == QDialog.DialogCode.Rejected
    dialog.deleteLater()

    dialog, title = build_plot_title_box_dialog()
    title.setText("Qt 6 plot")
    buttons = dialog.findChild(QDialogButtonBox)
    QTimer.singleShot(0, buttons.button(QDialogButtonBox.StandardButton.Ok).click)
    assert dialog.exec() == QDialog.DialogCode.Accepted
    assert title.text() == "Qt 6 plot"
    dialog.deleteLater()


@pytest.mark.parametrize("depth", [False, True], ids=["rgb", "depth"])
def test_video_frames(application, wait_until, tmp_path, depth):
    """Check decoding, QImage buffers, frame painting, and the depth colorbar."""
    import av
    from robot_log_visualizer.ui.video_item import VideoItem

    video_path = tmp_path / "video.mkv"
    if depth:
        pixels = np.full((16, 16), 1000, dtype=np.uint16)
        pixels[0, 0] = 0
        pixel_format = "gray16le"
    else:
        pixels = np.zeros((16, 16, 3), dtype=np.uint8)
        pixels[:, :, 0] = 255
        pixel_format = "rgb24"
    with av.open(str(video_path), "w") as container:
        stream = container.add_stream("ffv1", rate=10)
        stream.width = stream.height = 16
        stream.pix_fmt = "gray16le" if depth else "bgr0"
        for _ in range(2):
            frame = av.VideoFrame.from_ndarray(pixels, format=pixel_format)
            for packet in stream.encode(frame):
                container.mux(packet)
        for packet in stream.encode():
            container.mux(packet)

    item = VideoItem(str(video_path))
    try:
        assert item.media_loaded
        item.show()
        received = []
        item._decoder.frame_ready.connect(received.append)
        item.set_time(0)
        wait_until(lambda: bool(received), "Video decoder did not deliver a QImage")
        image = received[-1]
        assert (image.width(), image.height()) == (16, 16)
        if depth:
            assert image.pixelColor(0, 0).getRgb()[:3] == (0, 0, 0)
            assert image.pixelColor(1, 1).getRgb()[:3] != (0, 0, 0)
            assert item.depth_lut is not None
        else:
            assert image.pixelColor(0, 0).getRgb()[:3] == (255, 0, 0)
        assert not item.grab().isNull()
    finally:
        decoder = item._decoder
        item.release()
        item.close()
        assert not decoder.isRunning()


def test_signal_image(application):
    from qtpy.QtWidgets import QWidget
    from robot_log_visualizer.signal_provider.matfile_signal_provider import MatfileSignalProvider
    from robot_log_visualizer.ui.image_item import ImageItem, ImageLayout

    provider = MatfileSignalProvider(0.03, "test")
    item = ImageItem(provider, [], ImageLayout(2, 2))
    try:
        image = item._to_qimage(np.array([0.0, 0.25, 0.5, 1.0]))
        assert (image.width(), image.height()) == (2, 2)
        assert image.pixelColor(0, 0).getRgb()[:3] == (0, 0, 0)
        assert image.pixelColor(1, 1).getRgb()[:3] != (0, 0, 0)
        item.show()
        item._views[0].set_image(image)
        assert not item.grab().isNull()
        assert isinstance(item.colorbar(), QWidget)
    finally:
        item.release()
        item.close()
