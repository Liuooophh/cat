# 猫咪 · Pawpal

面向 Windows 的透明桌面猫咪。新版使用 cat-assets.zip 中四组透明序列帧，保留聊天、拖动、右键菜单、系统托盘、大小调节和 AI 接口设置。

## 使用

便携版：先退出旧版，解压完整目录后双击 PawpalCat-v5.exe；保留旁边的 _internal 目录，不要只复制 EXE。也可使用单文件版。发布包不需要另装 Python，温度功能需要 Windows x64 与 .NET Framework 4.8 或更高版本。右键猫咪或托盘图标可以退出。

源码版：Windows x64、CPython 3.10–3.13（本轮使用 3.12.14），在项目目录执行：

```powershell
py -m pip install -r requirements.txt
pyw .\desktop_ai_pet.pyw
```

- 按住猫咪拖动；单击偶尔显示短喵或呼噜气泡，默认不发声，也不会弹出聊天窗口。
- 每 30–70 秒检查一次，大多数时候保持安静；偶尔舔毛或走几步。
- 没有与猫咪互动约 3–5 分钟后，更容易睡觉。睡眠持续到单击或菜单唤醒；拖动和聊天不会唤醒。稳定睡眠有腹部呼吸和独立 zzz。
- 右键猫咪或托盘，可指定走路、洗脸、睡觉和唤醒。手动走路和洗脸完整播放一次后回待机；其他请求最多排队一个。
- 右键选择“和猫咪聊天”可主动聊天，默认回复简短、温和。
- 视频音轨不播放。朗读功能已移除，CPU 和深夜主动提醒默认关闭。数据气泡默认在稳定待机和睡眠时显示，可在通用设置关闭。
- 气泡只展示时间、天气、系统 CPU 利用率和前台 FPS；优化分层合成、字体、留白和轻阴影，保持紧凑三行（关闭 FPS 时两行）。睡眠 zzz 加大加粗、增加描边并持续可见，与气泡留出距离。
- CPU/GPU 温度只在托盘与主动聊天查询中显示。原猫咪托盘保留，另有双行数字图标：上方绿色 GPU、下方琥珀色 CPU；多设备各组取最高有效值，悬停和菜单分别列出每个设备与来源。没有数据时显示 --，可在通用设置关闭温度托盘。
- 天气设置支持多结果城市选择、保存坐标和立即测试；每15分钟更新，失败保留并标记上次数据。城市留空时不查询天气。
- 系统 CPU 每秒用 Windows PDH 的整机总计数器采样，优先采用本机 Windows 22621 对应的 Processor Utility，并记录 Processor Time 与回退来源；过期快照不保持显示。不同 Windows 任务管理器版本可能采用不同口径，详见 [CPU 采样说明](docs/CPU_USAGE.md)。FPS 用随包 PresentMon 2.6.0 获取前台应用实际显示帧率，不能采集时显示状态。CPU、GPU 温度通过随包 LibreHardwareMonitorLib + pythonnet 在后台约每 1.5 秒读取，不需要打开 LibreHardwareMonitor 图形程序；无有效读数显示 --°C。
- CPU 优先 Package，替代来源会标明 Tdie、Tctl、Core Max 等；GPU 优先 Core，各张显卡按设备标识独立显示，不把显存或 Hot Spot 当成核心温度。部分 CPU 读取需要已有 PawnIO 驱动及适当权限；程序不安装驱动、不提权、不修改安全设置。详见 [温度说明](docs/TEMPERATURE.md)。
- 通用设置支持简体中文、English、Bahasa Melayu，即时切换并保存。
- 麦克风使用 Windows 本机中文识别，结果只填入输入框，检查后手动发送；不会自动发送或上传原始录音。
- 大小调节范围为 50%–150%。新用户默认 130%，已有设置保留原比例。

## 动画与资源

运行素材在 assets/cat/，都是预处理的透明 PNG；运行时不需要视频解码器或抠图模型。clips.json 定义统一画布、地面位置、动作帧率、循环方式和源朝向。

播放器按时间选择帧，使用最多 96 张的解码缓存。缺失的情绪动作使用连续待机画面。四组素材均为 320×300、24 fps、RGBA，每组保留原始 240 帧；播放范围通过清单配置，不重新缩放单个姿势。

- 待机：整段循环，循环接缝使用 3 帧透明混合。
- 走路：起步 [0,103)，步态循环 [103,147)，坐下 [147,240)。默认只走一至两轮步态，完整保留起步和收尾。仅 [72,160) 帧推动窗口，起止缓动；遇屏幕边缘停止并收尾，不在迈步中突然镜像掉头。
- 舔毛：播放 [0,225) 一次，再回待机，避开原片末尾再次低头。
- 睡眠：趴下 [0,132)，睡眠循环 [132,178)，醒来 [178,240)。明确唤醒时完成当前循环后接醒来动作，睡眠不会超时。旧版睡眠未混入新姿势。

以上范围为从 0 开始、包含左端且不包含右端的帧索引。实际分段切换直接使用连续帧；返回待机选择最接近的相位，并使用约0.22秒缓动预乘alpha混合。行走镜像在后续待机中保持。原素材姿态并非完全一致，过渡只能减轻接缝，仍可能看到轻微姿态变化。

tools/import_cat_package.py 可重新导入已定稿的 cat-assets.zip，验证全部帧后原样复制，并生成 v2 clips.json。tools/prepare_cat_assets.py 保留旧视频处理流程。原始 MP4 不包含在便携包内。

旧熊素材 assets/pet/ 和切图工具保留在源码包中用于兼容性测试，猫咪程序不加载它们。

## AI、设置与数据

右键“设置 AI 接口”可配置 OpenAI、DeepSeek 或兼容接口，并通过同一请求路径测试连接；显示经过脱敏的具体错误。不同服务商分别保存配置，密钥保存在当前 Windows 用户凭据管理器中，不写入设置文件。留空保存会删除对应密钥。聊天询问天气或系统状态时优先读取本机查询数据。

DeepSeek 官方地址使用 https://api.deepseek.com 或 /v1，当前默认 deepseek-flash；旧 deepseek-chat/deepseek-reasoner 配置会迁移。普通聊天关闭默认高强度思考，连接测试限制输出 16 tokens。网络请求使用 Windows/环境代理及按网址解析的 PAC，保留 TLS 校验，不在代理失败时偷偷直连。天气保留成功预览并在保存后立即使用；失败提供具体 HTTP/网络原因。详见 [网络说明](docs/NETWORK.md)。

设置路径沿用 %APPDATA%\TuanziDesktopPet\settings.json。环境变量 PAWPAL_DATA_DIR 可指定独立验收目录，并跳过自启动注册。首次正常运行打包版本时默认启用用户级登录自启动，稳定副本在 %LOCALAPPDATA%\PawpalCat\app\ 下按目录版和单文件版分别保存；可在设置关闭，并尊重 Windows 手动禁用状态。单实例控制防止重复运行。

天气查询使用 Open-Meteo；CPU、GPU 温度和前台 FPS 在本机读取，Tencent Cloud 语音识别尚未接入。FPS随包第三方许可证与来源位于 assets/vendor/presentmon/，看护源码位于 tools/metrics_guard.cs，确保程序异常退出后也清理自己的ETW会话。温度依赖、精确版本、SHA-256 和许可证位于 assets/vendor/librehardwaremonitor/；诊断日志位于设置目录的 logs/temperature.log。

## 检查与打包

```powershell
py -m unittest discover -s tests -v
py .\tools\prepare_lhm.py --verify-only
py -m pip install pyinstaller==6.22.3
py .\tools\build_windows.py
```

默认构建目录版 dist/PawpalCat-v5/，包含 EXE 与 _internal。可用 --output-dir 指定目录，--onefile 构建单文件版。tools/prepare_lhm.py 可从固定的官方 NuGet 包恢复依赖，--verify-only 仅校验本地文件。tools/prepare_presentmon.py 可从记录的官方来源准备 FPS 依赖并编译看护程序。构建脚本只生成本地文件；如果构建用 Python 的 SSL 模块依赖证书策略资源，会一并收集，避免 EXE 中 TLS 初始化失败。

本机温度诊断（不开 GUI、不注册自启动）：

```powershell
py .\desktop_ai_pet.pyw --temperature-diagnostics .\temperature-source.json --samples 3
```

系统和网络诊断（不读密钥、不调用收费聊天，不打开 GUI、不注册自启动；天气使用公开测试城市吉隆坡）：

```powershell
py .\desktop_ai_pet.pyw --system-diagnostics .\system-report.json
```

本轮 249 项自动化测试通过，真实源码 GUI、双托盘、天气显示与 CPU 总计数器对比均通过。用户明确授权的一次 DeepSeek 官方最小聊天请求返回 HTTP 200，共使用 9 tokens；密钥未放入源码或发布包。温度硬件条件延续 v4：NVIDIA 可读，CPU 缺 PawnIO、Intel 核显无温度传感器，仍如实显示不可用。其他硬件、全新无 Python 系统和新版 Windows 任务管理器口径仍有未验证范围。
