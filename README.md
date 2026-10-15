# 🤖 robot-log-visualizer

`robot-log-visualizer` implements a python visualizer to display the data logged with
[`YarpRobotLoggerDevice`](https://github.com/ami-iit/bipedal-locomotion-framework/tree/master/devices/YarpRobotLoggerDevice) application.

## 📝 Install

Please follow one of the following methods to use the software

### <img src="https://github.com/ami-iit/robot-log-visualizer/assets/16744101/8de4bc21-26be-4ec5-a262-6179b53ef082" width="22" height="22"/> Install with `conda` (recommended)

Assuming that you have [`conda`](https://docs.conda.io/en/latest/) installed, you can create a new environment and install the `robot-log-visualizer` with the following commands:
```console
conda create -n visualizer-env
conda activate visualizer-env
```
 and you can install the `robot-log-visualizer` with the following command
```console
conda install -c robotology robot-log-visualizer
```

### 🧚 Install with pixi

You can also run the application using `pixi`:

```console
pixi run robot-log-visualizer
```

See the pixi homepage: [pixi](https://pixi.run).

### 🐍 Install from `pip` with apt python

Install `python3`, if not installed (in **Ubuntu 20.04**):

```console
sudo apt install python3.8 python3-virtualenv swig
```

Create a [virtual environment](https://docs.python.org/3/library/venv.html#venv-def) and activate it. For example:
```console
python3 -m venv visualizer-env
. visualizer-env/bin/activate
```

Inside the virtual environment, install the application from `pip`:

```console
pip install robot-log-visualizer
```

### 👷 Install the latest version (not recommended)
If you want to use the latest feature of the `robot-log-visualizer` you can install it with the
following command
```console
python -m pip install git+https://github.com/ami-iit/robot-log-visualizer.git
```

## 🏃 Usage

Once you have installed the `robot-log-visualizer` you can run it from the terminal.

[robot-log-visualizer.webm](https://github.com/ami-iit/robot-log-visualizer/assets/16744101/3fd5c516-da17-4efa-b83b-392b5ce1383b)

You can navigate the dataset thanks to the slider or by pressing `Ctrl-f` and `Ctrl-b` to move
forward and backward.

> [!IMPORTANT]
> `robot-log-visualizer` only supports reading `.mat` file [version 7.3 or newer](https://www.mathworks.com/help/matlab/import_export/mat-file-versions.html).

### 📄 Supported log formats
`robot-log-visualizer` reads the logs created by the [`YarpRobotLoggerDevice`](https://github.com/gbionics/bipedal-locomotion-framework/tree/master/devices/YarpRobotLoggerDevice) of `bipedal-locomotion-framework`, or by any other software writing the same format. The format is specified in the [log format specification](https://github.com/gbionics/bipedal-locomotion-framework/blob/master/devices/YarpRobotLoggerDevice/docs/log-format/README.md), and it is versioned independently of the software that created the log: the version is stored in the `version` variable of the `.mat` file, and it is printed in the log of the visualizer when the file is opened.

| Format | Created by | Robot model |
|:---:|:---|:---|
| [`v0.1.0`](https://github.com/gbionics/bipedal-locomotion-framework/blob/master/devices/YarpRobotLoggerDevice/docs/log-format/v0.1.0.md) | `bipedal-locomotion-framework` `v0.29.0` or newer | Loaded automatically from the URI stored in the log. |
| [`v0.0.1`](https://github.com/gbionics/bipedal-locomotion-framework/blob/master/devices/YarpRobotLoggerDevice/docs/log-format/v0.0.1.md) (no `version` variable) | `bipedal-locomotion-framework` up to `v0.28.0` | Not loaded automatically, it can be set manually. |

### 🤖 Robot model
Starting from the format `v0.1.0`, the log stores the robot model as a URI (e.g., `package://ergoCub/robots/ergoCubSN001/model.urdf`) that is resolved with [`resolve-robotics-uri-py`](https://github.com/ami-iit/resolve-robotics-uri-py). The packages are searched in the folders listed in the environment variables supported by `resolve-robotics-uri-py`, e.g., `GAZEBO_MODEL_PATH`, `GZ_SIM_RESOURCE_PATH`, `ROS_PACKAGE_PATH` and `AMENT_PREFIX_PATH`. Please remember to add the folder containing your model to one of these environment variables, e.g.
```console
export GAZEBO_MODEL_PATH=${GAZEBO_MODEL_PATH}:~/my-models
```

The model and the folder of its package can also be set manually from the GUI before opening the file. This is the only way to visualize the robot of a log in the format `v0.0.1`.

### 🎥 Videos and depth images
When a `.mat` file is opened, the videos saved by the logger in the same folder (`<file name>_*.mp4` and `<file name>_*.mkv`) are opened in a tab next to the robot.

- **Synchronization.** For the logs in the format `v0.1.0`, the time of each image is stored in the channel `camera::<camera>::<rgb|depth>`, and the videos are synchronized with the time of the other signals, even if some images have been dropped or the video starts after the beginning of the log. Otherwise, the video is assumed to span the entire log.
- **Depth videos.** The 16 bit depth videos (`<file name>_<camera>_depth.mkv`) are displayed with the `turbo` colormap in the range `0.2 m` to `5.0 m` (the depth is assumed in millimeters, i.e., the default `rgbd_cameras_depth_scale` of the logger). The pixels without depth are black, and a colorbar is shown below the image.
- **Images streamed as signals.** An image streamed as a numerical vector by an exogenous application (e.g., the scaled depth image seen by a policy, streamed with a `VectorsCollectionServer`) can be displayed as an image: right click on the signal in the variables tree and select `Render as image`. The option is available if the name of the signal contains `image` or `img`, and either its elements are named `r<row>_c<col>` (the size of the image is obtained from the names), or the number of elements is a perfect square (a square image stored row by row is assumed). The values are displayed with the same colormap of the depth videos, in the range `0` to `1` enlarged to include the minimum and maximum values of the signal, and `0` is displayed in black. This works both with `.mat` files and in real-time mode.

See the [visualization section of the format specification](https://github.com/gbionics/bipedal-locomotion-framework/blob/master/devices/YarpRobotLoggerDevice/docs/log-format/v0.1.0.md#visualization-with-robot-log-visualizer) for the details.

##  🐛 Bug reports and support
All types of [issues](https://github.com/ami-iit/robot-log-visualizer/issues/new) are welcome.

## 📝 License
Materials in this repository are distributed under the following license:

> All software is licensed under the BSD 3-Clause License. See [LICENSE](https://github.com/ami-iit/robot-log-visualizer/blob/main/LICENSE) file for details.

## 🧑‍💻 Maintainer

* Giulio Romualdi ([@GiulioRomualdi](https://github.com/GiulioRomualdi))
