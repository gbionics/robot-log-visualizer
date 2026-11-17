"""GUI scenario run in a separate pytest process by test_visualizer.py.

The child process isolates native Qt crashes and allows a hard timeout.
3D rendering, video, and streaming require manual testing.
"""

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


@pytest.fixture
def application(monkeypatch):
    # Set these before importing Qt, including when running on a desktop.
    monkeypatch.setenv("QT_API", "pyqt5")
    monkeypatch.setenv("QT_QPA_PLATFORM", "offscreen")
    monkeypatch.setenv("QT_QUICK_BACKEND", "software")
    monkeypatch.setenv("QTWEBENGINE_CHROMIUM_FLAGS", "--disable-gpu")

    from qtpy.QtCore import Qt
    from qtpy.QtWidgets import QApplication
    # Import WebEngine before constructing QApplication, as in the entry point.
    from robot_log_visualizer import __main__  # noqa: F401
    import pyqtgraph as pg

    pg.setConfigOption("useOpenGL", False)
    QApplication.setAttribute(Qt.AA_UseSoftwareOpenGL)
    app = QApplication([])
    yield app
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
        window.ui.meshcatView.loadFinished.connect(loaded.append)
        window.show()
        wait_until(lambda: bool(loaded), "Meshcat page did not finish loading")
        assert loaded[-1], "Meshcat page failed to load"
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
