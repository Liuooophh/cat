# CPU、GPU 温度

温度读取直接集成在现有 Tkinter 程序内，保留原来的设置、聊天和 FPS 功能。v5 按用户要求将温度移到托盘：气泡只保留时间、天气、CPU 利用率与前台 FPS；托盘悬停与菜单有温度详情，额外数字图标上 GPU、下 CPU。用户不需要下载、启动或保持 LibreHardwareMonitor.exe 运行。

## 运行条件

- Windows x64；源码需要 CPython 3.10–3.13 x64，开发与打包使用 3.12.14。
- LibreHardwareMonitorLib 0.9.6，官方 NuGet 的 **win-x64/net472 运行实现**。
- pythonnet 3.0.5、clr-loader 0.2.7.post0，显式加载 netfx。
- 系统需要 **.NET Framework 4.8 或更高版本**。发布包不包含系统 .NET 运行时，也不需要 .NET SDK 或单独的 .NET Core 运行时。
- CPU 低层传感器可能需要已经安装的 PawnIO 驱动与适当访问权限；GPU 可通过现有 NVIDIA、AMD 或 Intel 驱动接口读取，是否有温度取决于硬件与驱动支持。

DLL、依赖和许可证随程序发布，位于 assets/vendor/librehardwaremonitor/。本应用不包含 LibreHardwareMonitor 图形界面、PawnIO 安装器或驱动服务，不下载运行代码，不弹 UAC，也不安装、启动驱动或修改系统安全设置。

LibreHardwareMonitorLib 0.9.6 的 PawnIO 路径只尝试用 OPEN_EXISTING 打开现有设备。缺少驱动时，管理员身份本身不能补齐驱动。用户可先运行下方普通权限诊断，查看具体结果，再自行决定是否另行准备驱动或授权运行。不要为读取温度关闭驱动阻止列表、内存完整性或其他安全功能。

## 读取与显示

温度由独立后台线程约每 **1.5 秒**更新。该线程初始化一次 Computer，启用 CPU、GPU，逐次更新 Hardware 与 SubHardware，并读取 Temperature 类型传感器。Tk 主线程只拿不可变快照，不加载 CLR、不做传感器 I/O。关闭监控或退出时，在原工作线程关闭 Computer。

设备按硬件类型和完整标识区分；传感器保留原名称、类型、硬件来源及标识。不是依据产品型号硬编码，也不把多个同名 GPU 合并。

| 数据 | 选择规则 |
| --- | --- |
| CPU | 优先 CPU Package；缺少时根据实际传感器选择 Tdie、Tctl/Tdie、Die、Tctl 等合理代表值，再考虑 Core Max、Core Avg 或 CCD Max。保留真实来源，核心最大值不会显示成 Package。 |
| GPU | 每个设备优先 GPU Core；明确的通用 GPU 温度保留 Temp 标签。不会用显存、Hot Spot、Junction 或板卡温度冒充核心温度。 |
| 多 GPU | 每张显卡分别保存并显示设备名和温度；核显、独显互不代替。 |
| 多 CPU | 底层与托盘菜单保留每颗处理器结果；数字图标使用有效代表温度中的最高值，菜单明确说明该聚合规则。 |
| 无效读数 | None、非有限值、明显无效值、低层不可访问时的伪零均不作为有效温度；显示 --°C，不生成固定温度。 |

GPU 只有 Hot Spot 或显存温度时，即使这些传感器有数值，核心温度仍显示不可用。CPU 只有 Tctl 时会明确标注，避免把可能带控制偏移的温度当作 Tdie。极少数真实零度的特殊制冷场景也会被当前保守规则判为不可用；原始读数留在诊断里。

## 原先为什么可能读不到

旧实现查询 Libre/OpenHardwareMonitor 的 WMI 数据。通常需要另一套监控程序先创建并维护对应 WMI 提供程序；只启动桌宠时并没有实际传感器数据来源。Windows 通用 WMI 温度也不能保证提供准确的 CPU Package 或独立显卡温度。

新实现直接调用随包库，但仍可能受到驱动缺失、普通用户无访问权限、厂商不暴露传感器、DLL/运行时不兼容、暂时空值或硬件不受支持的影响。无数据与温度为零是两回事。

## 诊断与日志

先在普通权限 PowerShell 中进入项目目录；不需要先启动桌宠。

```powershell
py .\desktop_ai_pet.pyw --temperature-diagnostics .\temperature-source.json --samples 3 --interval 1.5
```

也可直接运行诊断模块：

```powershell
py -m pet_temperature_cli --temperature-diagnostics .\temperature-source.json --samples 3
```

诊断只执行有限次硬件采样，随后关闭 Computer；不开 GUI、不注册自启动。--samples 允许 1–20，--interval 允许 1–2 秒。--debug-temperature 可临时开启逐次采样日志。省略报告路径时，报告写到设置目录的 temperature-diagnostics.json。

报告包含 DLL 路径与版本、实际 .NET 运行时、Python 位数、是否管理员、PawnIO 安装和设备访问状态、设备与传感器清单、数值与最终选择来源、错误、清理状态。检查 any_temperature_available 与 all_temperatures_available；**进程退出码为 0 不代表 CPU、GPU 都可用**。初始化/依赖错误返回 2，报告无法写入返回 3。

正常日志：%APPDATA%\TuanziDesktopPet\logs\temperature.log。设置 PAWPAL_DATA_DIR 时改写到该目录下的 logs/。日志记录初始化、DLL 加载、设备/传感器清单与选择变化、错误及退出；默认不逐次记录温度，采用约 512 KB × 3 的轮转上限。

| 状态或原因 | 含义与检查方法 |
| --- | --- |
| driver_missing | 没有可用的 PawnIO 设备；CPU 温度不会显示成伪零。 |
| permission_denied | 已有设备但访问被拒绝；先检查驱动与本机权限，再由用户决定是否手动授权。 |
| driver_busy / driver_unavailable | 现有驱动忙或打开失败；报告包含 Windows 错误码。 |
| no_temperature_sensors | 检测到设备，但它没有暴露温度传感器。 |
| no_valid_temperature_readings | 传感器存在，但本次都为空或无效；后续仍会刷新。 |
| no_cpu_representative_sensor / no_gpu_core_sensor | 有其他温度，但无法可靠认定为 CPU 代表值或 GPU 核心。 |
| dll_missing / dependency_missing | 发布目录不完整、清单缺失或 pythonnet 依赖不可用；恢复完整发布包。 |
| dll_load_failed / dll_version_mismatch | DLL 损坏、版本不一致或加载失败；检查校验与详细错误。 |
| runtime_missing / runtime_incompatible | 缺少 Framework 4.8+，或进程中已加载其他 CLR；检查运行时报告。 |
| architecture_mismatch | 当前不是本发布支持的 x64 进程。 |

## 测试与构建

项目根目录执行：

```powershell
py -m pip install -r requirements.txt
py -m unittest discover -s tests -p 'test_pet_temperature*.py' -v
py -m unittest discover -s tests -v
py .\tools\prepare_lhm.py --verify-only
py -m pip install pyinstaller==6.22.3
py .\tools\build_windows.py
```

模拟测试覆盖 CPU 来源优先级、Intel/AMD 常见命名差异、多 GPU、子硬件归属、空值、伪零、缺失传感器、后台隔离与清理。它们验证逻辑，不能替代真实硬件测试。

默认产物 dist/PawpalCat-v6/ 包含 EXE 与 _internal，必须整体保留。构建会纳入 Python.Runtime.dll、CLR 加载器、CFFI、固定 LHM 实现及运行依赖，并校验资源清单。--output-dir 可以更改产物目录。

如果要恢复构建依赖，执行 py .\tools\prepare_lhm.py；它只下载锁文件记录的官方 NuGet 包和许可，校验 SHA-256，不安装系统组件。--verify-only 不访问网络。

发布目录的实际诊断：

```powershell
$probe = Start-Process -FilePath '.\dist\PawpalCat-v6\PawpalCat-v6.exe' -ArgumentList '--temperature-diagnostics=temperature-directory.json','--samples','3' -Wait -PassThru
$probe.ExitCode
Get-Content -LiteralPath '.\temperature-directory.json' -Raw
```

建议在另一份完整解压目录、包含中文或空格的路径，以及未安装 Python 的干净 Windows 环境重复验证。核对报告中 frozen 为 true、DLL 路径位于随包资源、cleanup_completed 为 true，并检查每个 CPU/GPU 的具体结果。

单文件版为可选构建：

```powershell
py .\tools\build_windows.py --onefile --name PawpalCat-v6-Onefile
$probe = Start-Process -FilePath '.\dist\PawpalCat-v6-Onefile.exe' -ArgumentList '--temperature-diagnostics=temperature-onefile.json','--samples','3' -Wait -PassThru
Get-Content -LiteralPath '.\temperature-onefile.json' -Raw
```

单文件版还需确认资源来自临时 _MEI 解包目录、DLL 成功加载、探测退出后资源释放。自启动的受管目录版副本携带 _internal；单文件版使用另一独立目录，避免继承旧目录版依赖。诊断命令不注册自启动。

## v4 温度接入时的验证范围（历史记录）

普通权限 Windows 11 x64 源码探测已经实际读取到 NVIDIA GPU Core **68–69°C**；CPU 返回 driver_missing，Intel GPU 返回 no_temperature_sensors。最终目录版、单文件版和目录版自启动稳定副本，各进行了三次真实硬件采样，NVIDIA GPU Core 均为当时的 **71°C**。系统没有因此被安装驱动或提权。数值是当时读数，不是固定值，也不是 CPU 读取成功的证明。

已确认 CPython 3.12.14、pythonnet 3.0.5、netfx 和官方 win-x64/net472 DLL 可加载；实际系统 Framework Release 为 533320（4.8.1）。191 项自动化测试通过，覆盖旧功能回归与传感器模拟；模拟结果不算硬件验证。

| 实际运行检查 | 结果 |
| --- | --- |
| 源码 GUI + 真实传感器 | 运行约 8.67 秒，149 次 Tk 心跳；初始化期间继续响应，184 帧实际绘制，退出后三个监控线程结束。 |
| 最终目录 EXE 的诊断模式 | 从空工作目录、移除 PYTHONPATH/PYTHONHOME 后运行，frozen=true，从随包 _internal 加载 DLL，三次采样后清理完成。 |
| 最终目录 EXE 的正常 GUI | 运行约 8.59 秒，6 次窗口响应检查通过；WM_CLOSE 正常退出，未强杀，温度对象已释放。 |
| 最终单文件 EXE 的诊断模式 | 从临时 _MEI 目录加载 DLL，三次采样及清理通过，进程退出后解包目录已移除。 |
| 目录版稳定副本 | 完整复制 _internal 后，从含中文、空格的独立 QA 路径启动诊断并读取真实 GPU 温度；使用模拟注册表，未修改用户真实自启动。 |

**CPU 读取成功仍未完成真实验证。** 当前机器没有 PawnIO，本次没有安装它或以管理员身份运行。AMD CPU/GPU、其他 Intel/NVIDIA 型号、已有 PawnIO 且有权限时的 CPU 温度、其他 Windows/.NET 版本、无 Python 的全新系统仍需实机验证。已有显卡驱动参与了真实 GPU 测试；不能据此宣称在无驱动或所有机器上都能读取。

如果由用户自行准备了合适驱动或改变运行权限，需要重启应用再诊断。优先检查报告中的 pawnio、selected 和每个设备的 reason；不要仅凭退出码认定 CPU 成功。登录后自动启动的真实场景本轮未测试；测试只验证了稳定副本内容与可执行性。

## 第三方许可

保留 assets/vendor/librehardwaremonitor/ 整个目录，尤其 THIRD-PARTY-NOTICES.txt、licenses/、packages.lock.json 与 manifest.json。上游库未修改：LHM、BlackSharp、DiskInfoToolkit、RAMSPDToolkit 使用 MPL-2.0；嵌入的 PawnIO 模块保留 LGPL-2.1 许可与源代码链接；HidSharp 使用 Apache-2.0；其余 MIT、MIT-0、BSD 许可随包保留。目录发布便于替换兼容的库；修改依赖时须同步更新校验及来源，并保留相应源代码和许可说明。

上游信息：[LibreHardwareMonitor 官方仓库](https://github.com/LibreHardwareMonitor/LibreHardwareMonitor)、[固定版本 NuGet](https://www.nuget.org/packages/LibreHardwareMonitorLib/0.9.6)、[pythonnet 运行时说明](https://pythonnet.github.io/pythonnet/python.html)。
