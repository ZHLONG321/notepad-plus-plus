# 构建与测量

## 目录

项目根目录是唯一的 Git 仓库。源码始终位于 `src/notepad-plus-plus`，其中 PowerEditor、scintilla、boostregex 保持上游的相对位置。构建输出在 `src/notepad-plus-plus/PowerEditor/bin64`。

仓库保存源码、通用脚本与本说明。本地配置、编辑文档、备份、运行目录、录像、转储和个人研究记录放在 Git 忽略的目录中。

## 换电脑

拉取 `startup-optimization` 分支，保持上述相对目录。需要复用原有隔离运行副本时，通过私人文件传输另行复制对应的 `tmp/<会话标识>` 目录，保留其内部结构。运行配置可能引用原项目的完整路径；复用前打开隔离配置和会话文件，确认它们全部指向新电脑上的项目副本。使用相同的盘符和项目根路径可以保留原引用。

Git 不携带编辑文档和主题配置。没有另行复制运行数据时，先根据目标电脑实际安装的插件与配置创建新的隔离副本，再测量。不同电脑的插件清单和配置分别调查。

## 准备脚本

需要 Windows x64、Git、uv，以及带 C++ 桌面工具的 Visual Studio Build Tools。以下命令从项目根目录运行；`SESSION_ID` 替换为当前会话的目录名：

```cmd
uv run ref/notepad-startup-analysis/scripts/prepare_session.py SESSION_ID
```

入口把脚本复制到 `tmp/SESSION_ID`，输出也写到该目录。同名脚本内容不同时会停止，请使用新的会话目录。后续从 `tmp/SESSION_ID` 执行脚本，保留它们预期的相对路径。

## 构建

默认使用 v143 和 Windows SDK 10.0.26100.0。构建脚本通过 Visual Studio 安装器的 vswhere 找到本机工具，限制为单编译任务、低优先级和两个逻辑处理器：

```cmd
uv run tmp/SESSION_ID/build.py npp > tmp/SESSION_ID/build.log 2>&1
```

可在末尾传入 MSBuild 参数覆盖 SDK 版本。原上游构建说明位于 `src/notepad-plus-plus/BUILD.md`。

窗口采集器使用同一套工具链和 SDK，在会话目录中生成 DLL：

```cmd
uv run tmp/SESSION_ID/build_window_frames.py > tmp/SESSION_ID/build-window-frames-console.log 2>&1
uv run tmp/SESSION_ID/build_accessibility_reader.py > tmp/SESSION_ID/build-accessibility.log 2>&1
```

第二个入口需要 Windows .NET Framework 4 的 x64 编译器与 UI Automation 程序集，用于读取 CsvQuery 等控件。耗时命令在支持后台执行的终端或任务工具中运行，并保留日志。

## 界面模式

“查看”菜单中的 Simple Mode 切换精简和完整界面，默认快捷键为 F12。在“设置 → 管理快捷键”的主菜单命令中修改 Simple Mode，即可更换快捷键。原来的 Post-It 功能保留在菜单里，默认不再占用 F12。

精简模式只显示编辑区，隐藏菜单、工具栏、标签页、状态栏和停靠、浮动面板。切回完整界面时恢复各项原来的显示设置和面板位置。模式单独保存到配置，下次启动沿用；用 `-simple` 可指定精简启动，例如：

```cmd
notepad++.exe -simple
```

精简启动先显示当前文档，输入暂停时分步恢复其余会话和原版插件。完整启动先完成文档、插件命令、工具栏图标和保存面板，再绘制并显示主窗口。两种界面模式与每个插件的 normal/delayed 设置分别保存。

## 测量输入

测量脚本以会话目录为数据根，需要下面的本地输入：

- `audit.json`：目标机器实际插件列表，包含每项的 `name` 和原发布 DLL 的 `sha256`。
- `runtime/startup-idle-default44` 与 `runtime/startup-idle-normal44`：完整隔离程序、原版插件依赖、`settings`、应用数据和临时目录，以及 `tab-preparation.json`。
- `configuration-source/effective`、`configuration-source/local`：面板与会话检查使用的配置副本。
- `session-fixture.json`、运行目录内的文档副本及备份：字符、滚动和恢复检查使用。
- `artifacts/<程序哈希>/`：需要指定旧程序对照时，放置对应程序、PDB 和来源记录。
- `windows-window-capture/scripts/window_capture.py` 与 `skill-capture-build/window_capture.dll`：精简模式检查使用的窗口显存采集器。按窗口录制工具的构建方法在本机准备，先创建采集设备，程序退出后再保存图片。

这些名称沿用现有测量入口的约定。目录名中的数字是夹具名称；运行前按实际配置核对文档和插件数量。数据缺少时，先准备数据，不直接运行检查入口。

以下入口测量后台新进程到采集画面的时间。短时样本先保留在显存中，目标退出后读回图片：

```cmd
uv run tmp/SESSION_ID/inspect_background_render.py --runtime startup-idle-default44 --no-trace --startup-burst
uv run tmp/SESSION_ID/inspect_background_render.py --runtime startup-idle-normal44 --no-trace --startup-burst
```

画面取自指定窗口的系统合成结果。采集在窗口出现后附加，首帧时间是采样上界。`measure_posted_input.py` 使用窗口字符消息，真实系统按键另外记录。启动时使用 `-noActivate`，并观察窗口是否进入前台。

`verify_*` 入口保留插件实际命令、保存面板、标签、滚动和增量查找等检查。先阅读对应入口的参数和数据需求，再对隔离运行副本操作。测量结果、截图和日志留在会话目录。

精简模式的检查分别覆盖保存模式、参数覆盖、改键、启动期间切换，以及停靠和浮动面板的隐藏、恢复、保存和再次启动。以下命令在后台隔离副本中操作：

```cmd
uv run tmp/SESSION_ID/verify_simple_mode.py --runtime startup-idle-default44
uv run tmp/SESSION_ID/verify_simple_mode.py --runtime startup-idle-normal44
uv run tmp/SESSION_ID/verify_simple_panels.py --runtime startup-idle-default44 --floating
uv run tmp/SESSION_ID/verify_simple_panels.py --runtime startup-idle-normal44
uv run tmp/SESSION_ID/repeat_editor_first.py
```

重复输入入口对两种插件设置各启动三次，逐帧读取真正编辑区里的字符，并检查文档、光标、撤销和重做在恢复其他会话内容后保留。输入来自后台窗口消息；真实系统按键需要另测。`verify_editor_first.py --scenario blank|commandline|secondary` 分别检查空文档、命令行文件和副视图克隆，`--trace --resume-during-completion` 记录补齐期间再次输入的排队时间。面板截图为原生请求绘制，用来查看面板内容和位置；启动和字符画面另取系统合成器的窗口帧。
