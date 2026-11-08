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

精简启动先显示当前文档，输入暂停时分步恢复其余会话和原版插件。完整启动先恢复文档，画好主程序的菜单、工具栏、标签页和编辑区，再显示主窗口。两种模式均在首次编辑区绘制后、输入空闲时逐个加载 delayed 插件，再补齐对应的菜单、工具栏图标和保存面板。两种界面模式与每个插件的 normal/delayed 设置分别保存；显式设置 normal 的插件仍立即加载。

精简和完整模式共用同一条延迟加载通路：注册插件及其菜单、恢复快捷键、更新上下文菜单，依次发送工具栏、文档回放和就绪通知，再恢复对应的保存面板并提交工具栏图标。延迟加载仍在界面线程执行，输入暂停后才开始一个插件；已经开始的原版插件初始化不能被宿主随意中断。使用原发布版 DLL。

输入法正在组合文字或等待选词时，自动恢复会话和加载插件继续等待，即使暂时没有按键。编辑区处理完组合结束或焦点离开后，自动工作恢复原有的空闲判断。此条件只约束自动补加载；明确执行的菜单命令、模式切换和退出仍按原流程处理。

首次显示先在不激活窗口的状态下完成主题布局和绘制，再解除 DWM 隐藏，最后为普通启动请求前台和编辑区焦点。输入法同步初始化期间，窗口已经有真实的主题画面；这项顺序调整不代表输入法已准备好，首次可见输入仍单独测量。`-noActivate` 保持窗口在后面，也不请求前台。最大化窗口在隐藏创建时就准备尺寸，明确指定坐标的普通启动仍沿用正常窗口位置。

后台功能检查可通过 `NPPM_MENUCOMMAND` 调用宿主命令；在 `-noActivate` 窗口位于后台时，该入口保护同步命令免于激活窗口。直接投递 `WM_COMMAND` 或按菜单位置投递 `WM_MENUCOMMAND` 不经过这一保护入口，不能根据启动时未激活推定后续命令也不会激活。检查原生菜单分发时，按实际菜单样式选择消息，并持续观察前台；任何抢前台或退出异常都单独判为失败。

## 输入法兼容性

程序使用系统原生输入法初始化和 Scintilla 的 IMM 输入处理。单独提前激活 Windows 文字服务的实验已停用：在确认中文输入法已开启后，实验程序仍只提交拉丁字母，没有显示中文候选框，因此不能作为保留原功能的启动优化。

编辑区使用 Scintilla 自带的 inline 组合文字处理，输入法候选列表仍由输入法提供。开始组合时关闭已有的自动补全列表，临时文字不触发宿主的缩进、括号配对或自动补全；确认后的文字正常处理。原始 Scintilla 通知继续传给插件，取消和撤销由编辑控件管理。主、副编辑区共用此设置，精简模式补齐界面时沿用已有编辑区。

这部分按 Windows 输入消息和 Scintilla 的组合文字标记处理，不按输入法名称分支。应用自己绘制组合文字时，Windows 的 [WM_IME_SETCONTEXT 接口](https://learn.microsoft.com/en-us/windows/win32/intl/wm-ime-setcontext)允许清除 `ISC_SHOWUICOMPOSITIONWINDOW`，同时保留输入法候选列表。Scintilla 的 inline 路径使用这个接口；它不保证输入法内部因此不创建隐藏窗口。

Windows 窗口处理成本可以被不同输入法共同触发，但输入法内部窗口的创建顺序、父子关系和跨进程调用不由同一输入协议规定。下面的隐藏窗口改造只适用于已经核实的引擎调用路径，不能仅按 `CS_IME` 或相同导入函数推广。Windows 的 [TSF UI-less 模式](https://learn.microsoft.com/en-us/windows/win32/tsf/uiless-mode-overview)要求输入服务实现相应接口，不满足条件的输入服务不会激活；为保留其他输入法兼容性，程序不强制启用此模式。

旧脚本即使设置 `NPP_STARTUP_TSF=1`，也不会创建或激活额外的文字服务管理器。启用 `NPP_STARTUP_TRACE` 时会记录 `inputServices.experiment.rejected`，便于识别仍在使用旧实验参数的测量入口。没有常驻或预加载进程。

数字按键显示、后台窗口消息和不显示的独立桌面均不能代替中文组合输入检查。中文通路检查记录输入法开启状态、编辑控件的实际代码页和最终可见文字，并还原临时输入状态；候选框是否出现属于另一个观察范围，未录制时不宣称通过。等待后台初始化完成后再输入的功能检查，不算启动至首字成绩。启动成绩仍以新进程中的真实可见字符为准；输入法功能失败的实验时间不能当作有效优化收益。

主窗口成为前台不代表编辑区已取得焦点。真实发键前，用 `GetGUIThreadInfo` 确认焦点属于本次隔离进程的 Scintilla，并在每次发键前确认目标仍是前台；没有取得焦点就停止发键。等待编辑区焦点不自动重试启动，也不增加已获准的前台次数。

`GetGUIThreadInfo` 也可能在同步焦点调用返回前就报告目标句柄。单键测量先准备空文档，确认目标及修饰键状态后发送真实按键；发键前同步查询编辑区文字，会先等到界面线程响应，从而推迟测量的起点。焦点句柄、文字查询完成和可见字符的时间分别保存。按键已进入系统队列，也不表示界面线程已经处理它。

等待采集器附加或等待空白帧都会推迟发键。需要减少这段测量等待时，可在启动前安装进程外 WinEvent 观察器，从窗口创建和焦点事件发现目标句柄，核对所属进程，再异步附加按窗口采集。采集与发键并行；空白帧和文字帧在事后核对。事件回调线程必须泵送消息，见 [SetWinEventHook](https://learn.microsoft.com/en-us/windows/win32/api/winuser/nf-winuser-setwineventhook) 和 [窗口事件定义](https://learn.microsoft.com/en-us/windows/win32/winauto/event-constants)。事件时间不代替像素时间；没有覆盖到字符出现前的画面时，只报告实际首个文字样本的上界。测量流程改变后不能把数值差异全部记作程序提速。

分析焦点等待时，将程序分段与系统线程状态按 QPC 对齐。`NtQuerySystemInformation(SystemProcessInformation)` 提供线程状态和等待原因，可区分等待与等待分配 CPU 的就绪状态；它不提供所等待的锁地址或持有者。只保留目标进程及线程的数据，记录每次查询耗时，不能把离散采样比例换算成精确等待时长，也不能仅凭 `WrResource` 认定某个输入法或外部进程有问题。

## 隐藏窗口的系统成本

### 程序自身的输入法窗口优化

普通构建直接包含窗口优化，不需要额外代理、QQ 项目、环境变量或指定输入法。程序仍由 Windows 加载当前输入法。当前界面线程的窗口创建通知只观察已经加载的组件；确认窗口属于 QQ 引擎后，核对它的 Windows 窗口导入，再处理后续符合条件的隐藏窗口。微软拼音及其他输入法继续使用原路径，没有安装 QQ 也可正常启动。

第一个被观察的 QQ 窗口按原方式创建，后续属于同一引擎、带 `CS_IME`、同线程拥有者且没有菜单的隐藏弹出窗口先作为子窗口创建。首次显示、移动、读取几何或修改相关样式前恢复弹出窗口。窗口回调和导入处理全部在本程序内，系统输入法文件及用户默认输入法不变。导入不符合预期时保留原调用，不阻止编辑器启动。

窗口创建通知使用当前线程的 `WH_CBT`。微软文档说明 [HCBT_CREATEWND](https://learn.microsoft.com/en-us/windows/win32/winmsg/cbtproc) 在 `WM_NCCREATE` 与 `WM_CREATE` 前触发；此时第一个窗口已经开始创建，所以不能把全部窗口创建成本视为已经推迟。记录 `inputServices.imeWindows.installed`、`deferred` 和 `restored`，并用实际候选列表和文字画面验证结果。源码为 `StartupImeWindows.cpp`。

输入法选择与状态诊断可在专用构建中定义 `NPP_STARTUP_PROFILE_PROBE`。注册消息 `Notepad.StartupImeProbe.93249AC7-8C55-4464-83B7-412D8A633C94` 在编辑区所属线程读取 IMM 状态；`wParam` 是本窗口内的 Scintilla 句柄，`lParam` 为 1/2/3 时读取开启状态、转换模式、句子模式，4/5 时关闭或开启该编辑区输入法，失败返回 -1。普通构建不接受该消息。

比较不同输入法时，在同一诊断程序中按进程选择已启用的 TSF 配置，并在窗口显示前后核对实际 GUID；不改用户默认输入法。内部焦点对照可以关闭采集和按键，单独记录焦点调用与显示提交。不同程序版本、不同测试批次的差值不能直接作为某项改动的收益；失败轮次中的真实慢调用也应保留。

继续拆分 QQ 成本时，可在隔离进程中记录 TSF 根窗口创建、原引擎加载、输入法导出和原引擎窗口 API 的起止 QPC。导入观察器保持原参数、返回值和错误状态，记录调用方模块及 RVA，退出前恢复导入地址。按实际焦点区间筛选记录；根窗口创建包含子窗口创建，`ImeInquire` 也包含引擎加载，不能重复相加。观察器的构建和提前映射会影响环境，因此其时间用于定位调用，普通候选仍另测真实输入。较快样本中某个 API 耗时很少，不能排除它在未捕获的慢样本中发生等待。

定义 `NPP_STARTUP_GUARD_PROBE` 可额外诊断最初的窗口保护：普通前台启动设置 `NPP_STARTUP_SKIP_INITIAL_GUARD=1` 时，跳过首次创建窗口期间的防激活保护。`-noActivate` 始终保留保护，后续初始化和插件加载的保护也保持原行为。这个入口仅用于同一程序的受控对照；普通构建不包含开关。分段记录另包含 `window.WM_NCCREATE` 和精简模式的 `window.editorOnly.activate`，用于区分非客户区创建、主窗口激活和编辑区焦点。只有真实对照证明收益，才据此调整正常启动行为。

系统记事本的对照也应确认实际输入法并记录真实按键与文字画面。跨进程读取输入法状态时，可对自有进程的指定线程使用原生消息钩子，在该线程调用 IMM 接口；记录钩子安装、进入回调及查询结束时间，避免把等待目标线程响应误算成查询本身耗时。原始系统程序副本、配置与测量数据留在本机，不加入源码仓库。

`scripts/measure_hidden_window_cost.cpp` 用空窗口比较直接创建弹出窗口和先创建子窗口再恢复弹出窗口的成本，也测量重复清除窗口区域的耗时。它不加载输入法，不显示窗口，不设置焦点。每种路径运行三组、每组六个窗口；每组对前三个窗口各清除两次区域。子窗口路径在恢复弹出窗口之前清除区域，这与真实输入法的调用顺序可能不同。

在配置好 x64 MSVC 和 Windows SDK 的开发者终端中，从项目根构建，输出放在已经准备好的会话目录：

```text
cl /nologo /std:c++17 /EHsc /O2 /MT /Zi /utf-8 ref/notepad-startup-analysis/scripts/measure_hidden_window_cost.cpp /Fotmp/SESSION_ID/measure_hidden_window_cost.obj /Fdtmp/SESSION_ID/compile.pdb /Fetmp/SESSION_ID/measure_hidden_window_cost.exe /link /DEBUG /INCREMENTAL:NO /MANIFEST:EMBED /MANIFESTINPUT:src/notepad-plus-plus/PowerEditor/src/notepad++.exe.manifest /MANIFESTINPUT:src/notepad-plus-plus/PowerEditor/src/dpiAware.manifest user32.lib gdi32.lib
```

在限制为两个逻辑处理器、低于正常优先级的任务进程中运行。无参数使用当前桌面；附加 `private` 使用新建但从未显示的桌面。两者都会检查输入桌面没有改变，输出 `done,0,input_desktop_unchanged,1` 表示本次结构检查通过。日志中的 `thread` 给出进程与工作线程，`qpc_frequency` 给出计时频率；`create`、`region`、`attach` 最后两列是调用前后的 QPC，可与系统跟踪对齐。保留运行日志、程序哈希和跟踪文件。

这个诊断用于判断 Windows 窗口操作的成本。空窗口更快、样式和 owner 恢复成功，都不能证明实际输入法兼容或 Notepad++ 首次可见输入更快。真实目标仍需单独对照；调试和系统跟踪的时间不混入普通启动成绩。

## 测量输入

日常启动直接运行构建后的程序，沿用系统输入法。测量也使用同一程序，记录实际加载的输入法组件，避免把输入法切换或额外加载的成本混入比较。原版输入法和插件文件不需要改动。

真实输入检查可在自有测试线程安装短时原生消息钩子，读取、临时开启并恢复 IMM 状态，同时用 TSF 接口确认当前输入法。跨输入法兼容性检查只在隔离进程选择本机已经启用的配置，使用 `TF_IPPMF_FORPROCESS`，不改变用户默认设置。这类启动后切换的检查用于验证兼容性，不计启动成绩。

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

采集开销需要直接测量。缩小屏幕截图范围未必降低 GDI 同步和读回成本；屏幕 `BitBlt` 的调用前后时间只能包住一次取样，不能当作合成器的呈现时间。窗口刚取得前台时，即使命中测试已属于目标窗口，屏幕仍可能处于显示过渡中，因此必须查看像素内容。按窗口采集仍是被遮挡场景的测量方式。用开启和关闭采集的交错对照分析影响，保留失败与慢样本，不从启动成绩中任意减去采集耗时。

`measure_system_key.py` 每次只做一次真实数字键检查，需要已有短暂占用前台和输入的授权。它只操作会话内的隔离运行目录，使用同一组 44 个测试文档；检查后还原配置和前台，并持续观察插件补加载及退出。获准后可运行：

```cmd
uv run tmp/SESSION_ID/measure_system_key.py --runtime startup-idle-default44 --simple --early-capture
```

`--early-capture` 在窗口已有可见样式、仍被 DWM 暂时隐藏时附加采集；必须等解除隐藏且保留空白编辑区画面后才发送按键。结果按实际字符像素出现的采样帧计时，不从旧成绩扣除采集等待。仍须查看空白参考帧和字符帧，判断主题、编辑区是否完整；数字键检查不替代中文输入法检查。去掉 `--simple` 可测完整模式，`--trace` 记录程序分段，但会改变测量开销。

真实按键检查在进程启动前等待最多 20 秒，只有桌面连续 5 秒没有输入才启动；等待不计入启动时间。脚本给 `SendInput` 加来源标记，通过独立消息线程观察本次进程处于前台时的按键。发现额外输入就中断当前采样并还原前台，结果记为失败，不能把额外退格或输入造成的文字变化判成程序吞键。观察器只保存事件数量、是否为系统标记的注入事件及首次干扰时间，不保存额外按键内容；退出时移除钩子。`input_provenance` 记录这些结果。空闲条件只用于减少对正在操作电脑的人的干扰，不代表冷缓存或稳定性能已经通过。

`verify_*` 入口保留插件实际命令、保存面板、标签、滚动和增量查找等检查。先阅读对应入口的参数和数据需求，再对隔离运行副本操作。测量结果、截图和日志留在会话目录。

插件验证按共用宿主通路选代表操作，覆盖立即及延迟加载、菜单命令、快捷键接入、文档通知、保存面板恢复和退出。保留全部原版 DLL，不逐个遍历每个插件的全部命令；只有故障明确指向某个插件时才单独定位。报告分别说明通路检查结果和实际执行过的代表操作，不将代表操作通过扩大为所有插件功能均已验证。首次可见输入和中文输入按编辑器通路测量，不为每个插件重复执行。

全部 DLL 已映射还不表示初始化回调和就绪通知已完成。需要在补加载结束后执行同步功能检查时，同时等待宿主补齐结束及已开始的延迟加载均产生对应的完成记录；不能只按 DLL 数量判断就绪。精简模式的首字计时仍在这些后台工作之前进行，分别记录首次可用和完整恢复时间。

面板检查等待同步切换命令完成后再读取尺寸，并比较保存后重启的尺寸。面板和插件命令检查持续观察前台直到测试进程退出；退出异常或抢到前台均判为失败。

`verify_simple_panels.py --runtime <隔离目录名>` 可选择会话 `runtime` 内的新候选目录；目录仍需具备上面的配置、文档夹具及来源记录。`--floating` 检查浮动面板，省略时检查停靠面板。每次入口启动两次程序，检查模式保存后重启及面板位置恢复。

启动期间的临时窗口保护按实际安装的窗口句柄解除，也覆盖不参与普通窗口枚举的消息窗口。后台保护持续观察到退出；不能根据启动时未激活就推断关闭过程正常。开启分段记录时，异常诊断使用系统提供的卸载事件大小、数量和数组指针；数组指针变量的位置与数组本身分别处理，避免把诊断数据误读为已卸载模块。

主窗口销毁后，主消息循环停止派发消息，防止插件在关闭通知中释放对象后，其辅助窗口又收到排队消息。`GetMessage` 自身可能处理导致主窗口销毁的同步消息，因此取消息后也检查主窗口的存续状态。配置保存和插件关闭通知仍在销毁前完成；取消关闭时主窗口继续运行。此处不能只依赖 `WM_QUIT`：Windows 会在其他已投递消息处理后才生成退出消息，见 [Microsoft 对 PostQuitMessage 的说明](https://devblogs.microsoft.com/oldnewthing/20051104-33/?p=33453)。开启诊断时，空地址间接调用也保留栈上的调用者返回地址。

精简模式的检查分别覆盖保存模式、参数覆盖、改键、启动期间切换，以及停靠和浮动面板的隐藏、恢复、保存和再次启动。以下命令在后台隔离副本中操作：

```cmd
uv run tmp/SESSION_ID/verify_simple_mode.py --runtime startup-idle-default44
uv run tmp/SESSION_ID/verify_simple_mode.py --runtime startup-idle-normal44
uv run tmp/SESSION_ID/verify_simple_panels.py --runtime startup-idle-default44 --floating
uv run tmp/SESSION_ID/verify_simple_panels.py --runtime startup-idle-normal44
uv run tmp/SESSION_ID/repeat_editor_first.py
```

重复输入入口对两种插件设置各启动三次，逐帧读取真正编辑区里的字符，并检查文档、光标、撤销和重做在恢复其他会话内容后保留。输入来自后台窗口消息；真实系统按键需要另测。`verify_editor_first.py --scenario blank|commandline|secondary` 分别检查空文档、命令行文件和副视图克隆，`--trace --resume-during-completion` 记录补齐期间再次输入的排队时间。面板截图为原生请求绘制，用来查看面板内容和位置；启动和字符画面另取系统合成器的窗口帧。
