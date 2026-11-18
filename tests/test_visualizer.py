"""Keep GUI crashes and hangs isolated from the main pytest process."""

import os
from pathlib import Path
import signal
import subprocess
import sys

import pytest


def test_visualizer():
    scenario = Path(__file__).with_name("visualizer_scenario.py")
    with subprocess.Popen(
        [sys.executable, "-m", "pytest", str(scenario), "-v", "--capture=no"],
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        start_new_session=(os.name == "posix"),
    ) as worker:
        try:
            output, _ = worker.communicate(timeout=60)
        except subprocess.TimeoutExpired:
            # Include Chromium and Meshcat children when stopping a hung test.
            if os.name == "posix":
                os.killpg(worker.pid, signal.SIGKILL)
            else:
                worker.kill()
            output, _ = worker.communicate()
            pytest.fail(f"Visualizer timed out after 60 seconds.\n{output}")

    assert worker.returncode == 0, f"Visualizer subprocess failed:\n{output}"
