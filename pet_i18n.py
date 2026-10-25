"""Small, explicit UI catalog; user content is never sent for translation."""

from __future__ import annotations

import re
from string import Formatter

LANGUAGES = {"zh-CN": "简体中文", "en": "English", "ms": "Bahasa Melayu"}

# key: Chinese, English, Malay. Keep data labels short enough for the pet bubble.
CATALOG = {
    "app_name": ("猫咪", "Pawpal Cat", "Kucing Pawpal"),
    "chat_subtitle": ("桌面陪伴", "DESKTOP COMPANION", "TEMAN DESKTOP"),
    "chat": ("和猫咪聊天", "Chat with Cat", "Sembang dengan Kucing"),
    "ai_settings": ("设置 AI 接口", "AI connection", "Sambungan AI"),
    "companion_settings": ("陪伴与语音设置", "Companion settings", "Tetapan teman"),
    "general_settings": ("基础设置", "Basic settings", "Tetapan asas"),
    "settings_intro": ("让猫咪按你的习惯陪伴。", "A little company, your way.", "Teman kecil, mengikut cara anda."),
    "desktop_display": ("桌面显示", "On your desktop", "Pada desktop anda"),
    "quiet_reminders": ("轻声提醒", "Quiet reminders", "Peringatan lembut"),
    "bubble_detail": ("待机或睡觉时，轻轻显示时间、天气和电脑状态。", "Time, weather and PC status while Cat rests.", "Masa, cuaca dan status PC semasa Kucing berehat."),
    "fps_detail": ("显示当前游戏或前台应用的帧率。", "Frame rate of your current game or foreground app.", "Kadar bingkai permainan atau aplikasi latar depan anda."),
    "temperature_detail": ("温度只显示在托盘；无有效读数时显示 --°C。", "Temperatures stay in the tray. Unavailable readings show --°C.", "Suhu di dulang sahaja. Bacaan tiada dipaparkan sebagai --°C."),
    "startup_detail": ("登录后，让猫咪自动来到桌面。", "Let Cat join you when you sign in.", "Kucing hadir secara automatik apabila anda log masuk."),
    "cpu_reminder_title": ("电脑忙碌提醒", "Busy PC reminder", "Peringatan PC sibuk"),
    "cpu_reminder_detail": ("CPU 持续高负载时提醒；只在本机采样。", "A gentle note during sustained CPU load. Measured locally.", "Peringatan semasa beban CPU tinggi berterusan. Diukur pada PC ini."),
    "night_reminder_title": ("深夜休息提醒", "Late night reminder", "Peringatan lewat malam"),
    "night_reminder_detail": ("00:00–04:59 偶尔轻声提醒你休息。", "An occasional nudge to rest between 00:00 and 04:59.", "Peringatan sekali-sekala untuk berehat antara 00:00 hingga 04:59."),
    "readings_help": ("读数与启动说明", "Readings and startup", "Bacaan dan permulaan"),
    "general_settings_menu": ("通用设置", "General settings", "Tetapan umum"),
    "display_startup": ("显示与启动", "Display and startup", "Paparan dan permulaan"),
    "weather": ("天气", "Weather", "Cuaca"),
    "fps_hint": ("FPS 跟随前台应用；未持续渲染或无法采集时显示暂无数据。", "FPS follows the foreground app. No data is shown when it is not rendering or capture is unavailable.", "FPS mengikut aplikasi latar depan. Tiada data dipaparkan jika aplikasi tidak merender atau tangkapan tidak tersedia."),
    "temperature_hint": ("已内置 LibreHardwareMonitor；CPU 读取可能需要 PawnIO 和权限。不支持时显示 --°C，诊断步骤见 README。", "LibreHardwareMonitor is built in. CPU readings may need PawnIO and permission. Unavailable readings show --°C; see README for diagnostics.", "LibreHardwareMonitor terbina dalam. Bacaan CPU mungkin memerlukan PawnIO dan kebenaran. Jika tiada bacaan, --°C dipaparkan; lihat README untuk diagnostik."),
    "weather_selection_hint": ("搜索后选择城市、省州和国家；留空保存可关闭天气。", "Search, then choose the city, region and country. Save a blank city to turn weather off.", "Cari, kemudian pilih bandar, negeri dan negara. Simpan bandar kosong untuk mematikan cuaca."),
    "weather_choose_save": ("请选择城市后保存", "Choose a city, then save", "Pilih bandar, kemudian simpan"),
    "weather_search_first": ("请先搜索并选择城市", "Search and choose a city first", "Cari dan pilih bandar dahulu"),
    "fetching_ellipsis": ("获取中…", "Loading…", "Sedang memuatkan…"),
    "updated_label": ("更新于", "Updated", "Dikemas kini"),
    "not_tested": ("尚未测试连接", "Connection not tested", "Sambungan belum diuji"),
    "verified": ("连接已验证", "Connection verified", "Sambungan disahkan"),
    "key_required_label": ("请设置 API Key", "Add an API key", "Tambah kunci API"),
    "companion": ("陪伴与语音", "Companion", "Teman"),
    "size_settings": ("调整桌宠大小", "Resize Cat", "Ubah saiz Kucing"),
    "quit": ("退出猫咪", "Quit Cat", "Keluar Kucing"),
    "settings": ("设置", "Settings", "Tetapan"),
    "size_title": ("猫咪 · 桌宠大小", "Pawpal Cat · Size", "Kucing Pawpal · Saiz"),
    "companion_title": ("猫咪 · 陪伴与语音设置", "Pawpal Cat · Companion", "Kucing Pawpal · Teman"),
    "ai_title": ("猫咪 · AI 接口设置", "Pawpal Cat · AI connection", "Kucing Pawpal · Sambungan AI"),
    "pet_size": ("桌宠大小", "Cat size", "Saiz kucing"),
    "size_hint": ("拖动滑块预览，点击“完成”保存这个大小。", "Drag to preview, then select Done to save.", "Seret untuk pratonton; pilih Selesai untuk simpan."),
    "done": ("完成", "Done", "Selesai"),
    "restore_default": ("恢复默认", "Reset", "Tetap semula"),
    "save": ("保存设置", "Save settings", "Simpan tetapan"),
    "cancel": ("取消", "Cancel", "Batal"),
    "close": ("关闭", "Close", "Tutup"),
    "saved": ("设置已保存", "Settings saved", "Tetapan disimpan"),
    "save_failed": ("保存失败", "Could not save", "Gagal menyimpan"),
    "size_save_failed": ("无法保存桌宠大小：{detail}", "Could not save Cat size: {detail}", "Gagal menyimpan saiz kucing: {detail}"),
    "companion_save_failed": ("无法保存陪伴设置：{detail}", "Could not save companion settings: {detail}", "Gagal menyimpan tetapan teman: {detail}"),
    "ai_save_failed": ("无法保存接口设置：{detail}", "Could not save connection settings: {detail}", "Gagal menyimpan tetapan sambungan: {detail}"),
    "language": ("语言", "Language", "Bahasa"),
    "startup": ("登录 Windows 后自动启动", "Start when I sign in to Windows", "Mula apabila saya log masuk Windows"),
    "startup_disabled": ("已在 Windows 启动应用中关闭", "Disabled in Windows startup apps", "Dimatikan dalam aplikasi permulaan Windows"),
    "startup_source": ("源码运行时不设置开机启动", "Startup is available in the packaged app", "Permulaan tersedia dalam aplikasi berpakej"),
    "startup_failed": ("无法设置开机启动：{detail}", "Could not update startup: {detail}", "Gagal mengemas kini permulaan: {detail}"),
    "startup_hint": ("自启动使用稳定副本；可在此处或 Windows 启动应用中关闭。", "Startup uses a stable copy. Disable it here or in Windows startup apps.", "Permulaan menggunakan salinan tetap. Matikannya di sini atau dalam aplikasi permulaan Windows."),
    "single_instance": ("猫咪已经在运行。", "Cat is already running.", "Kucing sudah berjalan."),
    "actions": ("动作", "Actions", "Aksi"),
    "click": ("点击互动", "Pet", "Belai"),
    "eat": ("吃猫粮", "Eat", "Makan"),
    # Kept so older saved UI text can still be localized during migration.
    "walk": ("走路", "Walk", "Berjalan"),
    "groom": ("洗脸", "Groom", "Bersihkan muka"),
    "sleep": ("睡觉", "Sleep", "Tidur"),
    "wake": ("唤醒", "Wake up", "Bangun"),
    "idle": ("返回待机", "Return to idle", "Kembali berehat"),
    "queued": ("动作已排队", "Action queued", "Aksi dalam giliran"),
    "action_queued": ("动作已排队：{action}", "Action queued: {action}", "Aksi dalam giliran: {action}"),
    "system_cpu": ("系统 CPU", "System CPU", "CPU sistem"),
    "cpu": ("CPU", "CPU", "CPU"),
    "fps": ("前台 FPS", "Foreground FPS", "FPS latar depan"),
    "cpu_temperature": ("CPU 温度", "CPU temperature", "Suhu CPU"),
    "temperature_driver_unavailable": ("CPU 温度不可用：可能缺少 PawnIO 或读取权限；诊断步骤见 README。", "CPU temperature unavailable: PawnIO or access permission may be missing. See README for diagnostics.", "Suhu CPU tidak tersedia: PawnIO atau kebenaran akses mungkin tiada. Lihat README untuk diagnostik."),
    "temperature_unavailable": ("温度暂不可用；诊断步骤见 README。", "Temperature unavailable; see README for diagnostics.", "Suhu tidak tersedia; lihat README untuk diagnostik."),
    "sensor_unavailable": ("传感器不可用", "Sensor unavailable", "Sensor tidak tersedia"),
    "show_fps": ("显示前台应用 FPS", "Show foreground app FPS", "Papar FPS aplikasi latar depan"),
    "show_temperature": ("托盘显示 CPU / GPU 温度", "Show CPU / GPU temperature in tray", "Papar suhu CPU / GPU di dulang"),
    "tray_temperatures": ("硬件温度", "Hardware temperatures", "Suhu perkakasan"),
    "tray_icon_hint": ("数字图标：上 GPU，下 CPU；多设备取最高值", "Icon: GPU above CPU; highest per device group", "Ikon: GPU di atas CPU; nilai tertinggi setiap kumpulan"),
    "tray.reason.driver_missing": ("缺少驱动", "Driver missing", "Pemacu tiada"),
    "tray.reason.permission_denied": ("需要读取权限", "Access required", "Perlu kebenaran"),
    "tray.reason.no_temperature_sensors": ("无温度传感器", "No temperature sensor", "Tiada sensor suhu"),
    "tray.reason.no_valid_temperature_readings": ("暂无读数", "No reading", "Tiada bacaan"),
    "tray.reason.driver_busy": ("驱动忙", "Driver busy", "Pemacu sibuk"),
    "tray.reason.driver_unavailable": ("驱动不可用", "Driver unavailable", "Pemacu tidak tersedia"),
    "tray.reason.unavailable": ("不可用", "Unavailable", "Tidak tersedia"),
    "show_data_bubble": ("显示数据气泡", "Show data bubble", "Papar gelembung data"),
    "fps.starting": ("FPS 启动中", "FPS starting", "FPS sedang bermula"),
    "fps.ok": ("FPS 正常", "FPS available", "FPS tersedia"),
    "fps.no_foreground": ("没有前台应用", "No foreground app", "Tiada aplikasi latar depan"),
    "fps.self_foreground": ("当前为猫咪窗口", "Cat window is in front", "Tetingkap Kucing di hadapan"),
    "fps.not_rendering": ("前台应用未持续渲染", "App is not continuously rendering", "Aplikasi tidak merender secara berterusan"),
    "fps.permission_denied": ("FPS 采集权限不足", "FPS capture needs permission", "Kebenaran tangkapan FPS diperlukan"),
    "fps.unavailable": ("FPS 暂不可用", "FPS unavailable", "FPS tidak tersedia"),
    "fps.dependency_missing": ("缺少 FPS 采集组件", "FPS capture component missing", "Komponen tangkapan FPS tiada"),
    "weather_search": ("搜索城市", "Search city", "Cari bandar"),
    "select_city": ("选择城市", "Choose city", "Pilih bandar"),
    "weather_city": ("天气城市", "Weather city", "Bandar cuaca"),
    "refresh_weather": ("立即刷新", "Refresh now", "Segar semula"),
    "updated_at": ("更新于 {time}", "Updated {time}", "Dikemas kini {time}"),
    "last_data": ("上次数据", "Last reading", "Bacaan terakhir"),
    "fetching": ("获取中", "Loading", "Sedang memuatkan"),
    "unavailable": ("暂不可用", "Unavailable", "Tidak tersedia"),
    "weather_unavailable": ("天气暂不可用", "Weather unavailable", "Cuaca tidak tersedia"),
    "weather_unset": ("天气未设置", "Weather not set", "Cuaca belum ditetapkan"),
    "weather_loading": ("{city} 天气更新中", "Updating weather for {city}", "Mengemas kini cuaca {city}"),
    "weather_updating": ("天气更新中", "Updating weather", "Mengemas kini cuaca"),
    "weather_info": ("天气只查询你填写的城市；CPU 和时间状态在本机处理。", "Weather uses your selected city. CPU and time stay on this PC.", "Cuaca menggunakan bandar pilihan anda. CPU dan masa diproses pada PC ini."),
    "weather_old_city_label": ("天气城市（留空可关闭天气反应）", "Weather city (blank to disable)", "Bandar cuaca (kosongkan untuk matikan)"),
    "weather_enter_city": ("请先填写城市名称。", "Enter a city name first.", "Masukkan nama bandar dahulu."),
    "weather_no_city": ("没找到“{city}”，请试试城市名或“城市, 省份”。", "Could not find “{city}”. Try a city and region.", "“{city}” tidak ditemui. Cuba bandar dan negeri."),
    "weather_no_coordinates": ("没有找到该地点的坐标，请检查城市名称。", "Coordinates not found. Check the city name.", "Koordinat tidak ditemui. Semak nama bandar."),
    "weather_bad_data": ("天气服务返回了无法识别的数据。", "Weather service returned invalid data.", "Perkhidmatan cuaca memulangkan data tidak sah."),
    "weather_service_down": ("天气服务暂时无法查询。", "Weather service is unavailable.", "Perkhidmatan cuaca tidak tersedia."),
    "weather_no_current": ("天气服务没有返回当前天气。", "Current weather was not returned.", "Cuaca semasa tidak diterima."),
    "weather_incomplete": ("天气服务返回的数据不完整。", "Weather data is incomplete.", "Data cuaca tidak lengkap."),
    "weather.clear": ("晴", "Clear", "Cerah"),
    "weather.mostly_clear": ("大致晴朗", "Mostly clear", "Kebanyakannya cerah"),
    "weather.partly_cloudy": ("多云", "Partly cloudy", "Berawan sebahagian"),
    "weather.overcast": ("阴", "Overcast", "Mendung"),
    "weather.fog": ("有雾", "Fog", "Berkabus"),
    "weather.drizzle": ("毛毛雨", "Drizzle", "Gerimis"),
    "weather.freezing_drizzle": ("冻毛毛雨", "Freezing drizzle", "Gerimis beku"),
    "weather.light_rain": ("小雨", "Light rain", "Hujan ringan"),
    "weather.rain": ("中雨", "Rain", "Hujan"),
    "weather.heavy_rain": ("大雨", "Heavy rain", "Hujan lebat"),
    "weather.freezing_rain": ("冻雨", "Freezing rain", "Hujan beku"),
    "weather.light_snow": ("小雪", "Light snow", "Salji ringan"),
    "weather.snow": ("中雪", "Snow", "Salji"),
    "weather.heavy_snow": ("大雪", "Heavy snow", "Salji lebat"),
    "weather.snow_grains": ("雪粒", "Snow grains", "Butiran salji"),
    "weather.showers": ("阵雨", "Showers", "Hujan sekejap"),
    "weather.heavy_showers": ("强阵雨", "Heavy showers", "Hujan lebat sekejap"),
    "weather.snow_showers": ("阵雪", "Snow showers", "Salji sekejap"),
    "weather.heavy_snow_showers": ("强阵雪", "Heavy snow showers", "Salji lebat sekejap"),
    "weather.thunderstorm": ("雷雨", "Thunderstorm", "Ribut petir"),
    "weather.hail": ("雷雨伴冰雹", "Thunderstorm with hail", "Ribut petir dengan hujan batu"),
    "weather.heavy_hail": ("强雷雨伴冰雹", "Severe thunderstorm with hail", "Ribut petir teruk dengan hujan batu"),
    "weather.change": ("天气变化", "Changing weather", "Cuaca berubah"),
    "cpu_alert": ("CPU 连续高负载时提醒（本机采样，不上传）", "Remind me of sustained high CPU use (local only)", "Ingatkan penggunaan CPU tinggi berterusan (setempat sahaja)"),
    "night_alert": ("深夜 00:00–04:59 偶尔提醒休息", "Occasional rest reminder between 00:00 and 04:59", "Peringatan rehat sekali-sekala antara 00:00 dan 04:59"),
    "status_bubble_old": ("在猫咪头顶常驻显示时间、天气和 CPU（默认关闭）", "Show time, weather and CPU above Cat", "Papar masa, cuaca dan CPU di atas Kucing"),
    "connect_ai": ("连接 AI 接口", "Connect to AI", "Sambung ke AI"),
    "ai_support": ("支持 OpenAI、DeepSeek 或自定义兼容服务。", "Supports OpenAI, DeepSeek and compatible services.", "Menyokong OpenAI, DeepSeek dan perkhidmatan serasi."),
    "provider": ("服务商", "Provider", "Penyedia"),
    "custom_provider": ("自定义 / Ollama", "Custom / Ollama", "Tersuai / Ollama"),
    "endpoint": ("接口地址", "API address", "Alamat API"),
    "model": ("模型名称", "Model name", "Nama model"),
    "api_key": ("API 密钥（Windows 加密保存）", "API key (Windows secure storage)", "Kunci API (simpanan selamat Windows)"),
    "provider_hint": ("选择服务商会自动填地址和模型；请用对应服务商的 API Key。", "Selecting a provider fills in its address and model. Use its API key.", "Memilih penyedia mengisi alamat dan modelnya. Gunakan kunci API penyedia itu."),
    "delete_key_hint": ("密钥留空保存会删除已存密钥。", "Saving an empty key deletes the stored key.", "Menyimpan kunci kosong memadam kunci yang disimpan."),
    "incomplete_info": ("信息不完整", "Missing information", "Maklumat tidak lengkap"),
    "need_endpoint_model": ("请填写接口地址和模型名称。", "Enter the API address and model name.", "Masukkan alamat API dan nama model."),
    "invalid_url_title": ("地址格式错误", "Invalid address", "Alamat tidak sah"),
    "invalid_url": ("接口地址需以 http:// 或 https:// 开头。", "API address must start with http:// or https://.", "Alamat API mesti bermula dengan http:// atau https://."),
    "test_connection": ("测试连接", "Test connection", "Uji sambungan"),
    "testing_connection": ("正在测试连接…", "Testing connection…", "Menguji sambungan…"),
    "connection_ok": ("连接成功", "Connection successful", "Sambungan berjaya"),
    "connection_failed": ("连接失败", "Connection failed", "Sambungan gagal"),
    "test_cost_hint": ("测试会发送一条简短请求，可能产生少量 API 费用。", "Testing sends a short request and may incur a small API charge.", "Ujian menghantar permintaan ringkas dan mungkin mengenakan caj API kecil."),
    "test_required": ("已保存，连接尚未验证", "Saved; connection not tested", "Disimpan; sambungan belum diuji"),
    "greet": ("打招呼", "Say hello", "Ucap hai"),
    "encourage": ("给我鼓励", "Encourage me", "Beri semangat"),
    "joke": ("讲个笑话", "Tell a joke", "Bercerita jenaka"),
    "greet_prompt": ("你好呀", "Hello", "Hai"),
    "encourage_prompt": ("今天有点累，鼓励我一下", "I feel tired today. Could you encourage me?", "Saya agak penat hari ini. Boleh beri saya semangat?"),
    "joke_prompt": ("给我讲个简短的笑话", "Tell me a short joke", "Ceritakan jenaka ringkas"),
    "chat_placeholder": ("说点什么…", "Say something…", "Katakan sesuatu…"),
    "send": ("发送", "Send", "Hantar"),
    "you": ("你  ", "You  ", "Anda  "),
    "you_label": ("你", "You", "Anda"),
    "cat_prefix": ("猫咪  ", "Cat  ", "Kucing  "),
    "listening_still": ("还在听；说完停顿一下，识别后会填进输入框。", "Still listening… Pause when done to fill the text box.", "Masih mendengar… Berhenti seketika untuk teks."),
    "listening": ("正在听你说话；说完停顿一下，识别后会填进输入框。", "Listening… Pause when done to fill the text box.", "Mendengar… Berhenti seketika untuk teks."),
    "speech_busy": ("语音识别正在运行，请稍等。", "Speech recognition is running. Please wait.", "Pengecaman pertuturan sedang berjalan. Sila tunggu."),
    "speech_done": ("识别完成，检查输入框里的文字后再点发送。", "Recognition complete. Check the text, then select Send.", "Pengecaman selesai. Semak teks, kemudian pilih Hantar."),
    "speech_no_result": ("没听清，点麦克风再试一次。", "Could not hear clearly. Select the microphone to try again.", "Kurang jelas didengar. Pilih mikrofon untuk cuba lagi."),
    "speech_no_recognizer": ("没有找到中文语音识别器，请在 Windows 语音设置中安装中文语音识别。", "Chinese speech recognition is not installed in Windows.", "Pengecaman pertuturan Cina belum dipasang dalam Windows."),
    "speech_no_microphone": ("无法使用麦克风，请检查 Windows 麦克风权限和输入设备。", "Microphone unavailable. Check Windows permissions and input device.", "Mikrofon tidak tersedia. Semak kebenaran Windows dan peranti input."),
    "speech_failed": ("语音识别没有启动成功，请检查 Windows 语音识别和麦克风设置。", "Speech recognition could not start. Check Windows speech and microphone settings.", "Pengecaman pertuturan gagal bermula. Semak tetapan pertuturan dan mikrofon Windows."),
    "speech_timeout": ("识别时间到了，点麦克风再试一次。", "Recognition timed out. Select the microphone to try again.", "Pengecaman tamat masa. Pilih mikrofon untuk cuba lagi."),
    "speech_no_powershell": ("找不到 Windows PowerShell，暂时无法启动语音识别。", "Windows PowerShell is missing; speech recognition cannot start.", "Windows PowerShell tiada; pengecaman pertuturan tidak boleh bermula."),
    "speech_hint": ("麦克风只把识别结果放入输入框，检查后手动发送。天气使用 Open-Meteo。", "The microphone fills the input box; check the text before sending. Weather by Open-Meteo.", "Mikrofon mengisi kotak input; semak teks sebelum menghantar. Cuaca oleh Open-Meteo."),
    "intro_offline": ("喵。我在这里陪你。需要聊天时，随时叫我。", "Meow. I am here with you. Talk to me whenever you like.", "Meow. Saya di sini menemani anda. Bersembanglah bila-bila masa."),
    "intro_key": ("喵。想聊天的话，可以先在设置里填入 {provider} API Key。", "Meow. Add your {provider} API key in settings to chat.", "Meow. Tambah kunci API {provider} dalam tetapan untuk bersembang."),
    "intro_online": ("喵。今天过得怎么样？", "Meow. How is your day going?", "Meow. Bagaimana hari anda?"),
    "need_key": ("{provider} · 请设置 API Key", "{provider} · Add an API key", "{provider} · Tambah kunci API"),
    "ai_configured": ("AI 已配置 · {provider}", "AI configured · {provider}", "AI dikonfigurasi · {provider}"),
    "mode_verified": ("{provider} · 连接已验证", "{provider} · Connection verified", "{provider} · Sambungan disahkan"),
    "mode_not_tested": ("{provider} · 尚未测试连接", "{provider} · Connection not tested", "{provider} · Sambungan belum diuji"),
    "offline_mode": ("本地轻量陪聊 · 尚未连接 AI 接口", "Local companion · AI not connected", "Teman setempat · AI belum disambungkan"),
    "waiting_reply": ("我还在想上一条呢，等我一下下～", "I am still thinking about the last message. One moment.", "Saya masih memikirkan mesej tadi. Tunggu sebentar."),
    "thinking": ("正在想一想…", "Thinking…", "Sedang berfikir…"),
    "deepseek_need_key": ("还没有 DeepSeek API 密钥，请在设置 AI 接口中填写密钥。", "Add your DeepSeek API key in AI settings.", "Tambah kunci API DeepSeek dalam tetapan AI."),
    "openai_need_key": ("还没有 OpenAI API 密钥。ChatGPT Go 订阅不包含 API 用量，请在设置中填写密钥。", "Add your OpenAI API key in settings. ChatGPT subscriptions do not include API usage.", "Tambah kunci API OpenAI dalam tetapan. Langganan ChatGPT tidak termasuk penggunaan API."),
    "generic_need_key": ("请先在 AI 接口设置中填写 API 密钥。", "Add your API key in AI connection settings first.", "Tambah kunci API dalam tetapan sambungan AI dahulu."),
    "rest_reply": ("先歇一小会儿吧。我陪你。", "Take a short break. I am here.", "Berehatlah sebentar. Saya di sini."),
    "slow_reply": ("慢慢来，今天已经很努力了。", "Take your time. You have worked hard today.", "Perlahan-lahan. Anda sudah berusaha hari ini."),
    "purr_reply": ("呼噜……（轻轻蹭了蹭你的手）", "Purr… (gently nuzzles your hand)", "Purr… (menggesel tangan anda dengan lembut)"),
    "connect_reply": ("我在这里陪你。需要一起想办法时，可以在设置里连接 AI。", "I am here with you. Connect AI in settings if you need help thinking things through.", "Saya menemani anda. Sambungkan AI dalam tetapan jika perlukan bantuan berfikir."),
    "listen_reply": ("喵。我听着呢。", "Meow. I am listening.", "Meow. Saya sedang mendengar."),
    "talk_reply": ("嗯，慢慢说。", "Take your time. I am listening.", "Ambil masa anda. Saya mendengar."),
    "here_reply": ("我在。", "I am here.", "Saya di sini."),
    "meow": ("喵。", "Meow.", "Meow."),
    "purr": ("呼噜……", "Purr…", "Purr…"),
    "growl": ("呜……", "Mrr…", "Mrr…"),
    "meow_question": ("喵？", "Meow?", "Meow?"),
    "hmm": ("嗯？", "Hmm?", "Hmm?"),
    "rain_reminder": ("外面下雨了，记得带伞。", "It is raining. Remember your umbrella.", "Hujan di luar. Jangan lupa payung."),
    "rain_short": ("下雨了。", "It is raining.", "Hujan turun."),
    "cold_reminder": ("外面有点冷，记得添衣。", "It is cold outside. Wear something warm.", "Sejuk di luar. Pakai pakaian hangat."),
    "warm_reminder": ("出门注意保暖。", "Keep warm outside.", "Pastikan badan hangat di luar."),
    "storm_short": ("外面有雷雨。", "There is a thunderstorm outside.", "Ada ribut petir di luar."),
    "storm_reminder": ("雷雨天，出门留意天气。", "Watch the weather if you go out in the storm.", "Perhatikan cuaca jika keluar ketika ribut."),
    "hot_reminder": ("天气热，记得喝水。", "It is hot. Remember to drink water.", "Cuaca panas. Jangan lupa minum air."),
    "sun_reminder": ("出门留意防晒。", "Remember sun protection outside.", "Ingat perlindungan matahari di luar."),
    "cpu_busy": ("电脑正在忙。", "The PC is busy.", "PC sedang sibuk."),
    "cpu_wait": ("等电脑缓一缓。", "Give the PC a moment.", "Beri PC sedikit masa."),
    "night_reminder": ("夜深了，早点休息。", "It is late. Get some rest.", "Sudah lewat. Berehatlah."),
    "rest_reminder": ("该休息一会儿了。", "Time for a short break.", "Masa untuk berehat sebentar."),
    "ai.invalid_endpoint": ("接口地址无效，请填写兼容 Chat Completions 的 http(s) 地址。", "Invalid API address. Enter an http(s) Chat Completions address.", "Alamat API tidak sah. Masukkan alamat http(s) Chat Completions."),
    "ai.invalid_model": ("请填写模型名称。", "Enter a model name.", "Masukkan nama model."),
    "ai.invalid_messages": ("聊天请求内容无效。", "Chat request is invalid.", "Permintaan sembang tidak sah."),
    "ai.authentication": ("API 密钥认证失败，请检查服务商与密钥。", "API key authentication failed. Check the provider and key.", "Pengesahan kunci API gagal. Semak penyedia dan kunci."),
    "ai.permission": ("账户没有使用该模型或接口的权限。", "This account cannot access that model or API.", "Akaun ini tidak boleh mengakses model atau API tersebut."),
    "ai.quota": ("API 余额或额度不足，请检查服务商账户。", "API balance or quota is insufficient. Check your provider account.", "Baki atau kuota API tidak mencukupi. Semak akaun penyedia."),
    "ai.rate_limit": ("请求过于频繁，请稍后重试。", "Too many requests. Try again shortly.", "Terlalu banyak permintaan. Cuba lagi sebentar nanti."),
    "ai.model_not_found": ("模型不存在或不可用，请检查模型名称与账户权限。", "Model not found or unavailable. Check its name and account access.", "Model tidak ditemui atau tidak tersedia. Semak nama dan akses akaun."),
    "ai.not_found": ("接口路径不存在，请检查接口地址。", "API path not found. Check the API address.", "Laluan API tidak ditemui. Semak alamat API."),
    "ai.bad_request": ("服务拒绝了请求，请检查模型与接口兼容性。", "The service rejected the request. Check model and API compatibility.", "Perkhidmatan menolak permintaan. Semak keserasian model dan API."),
    "ai.server": ("AI 服务暂时不可用，请稍后重试。", "AI service is temporarily unavailable. Try again later.", "Perkhidmatan AI tidak tersedia buat sementara waktu. Cuba lagi nanti."),
    "ai.timeout": ("AI 请求超时，请检查网络后重试。", "AI request timed out. Check your connection and try again.", "Permintaan AI tamat masa. Semak sambungan dan cuba lagi."),
    "ai.network": ("无法连接 AI 服务，请检查网络和接口地址。", "Cannot reach the AI service. Check your network and API address.", "Tidak dapat menghubungi perkhidmatan AI. Semak rangkaian dan alamat API."),
    "ai.invalid_response": ("AI 服务返回的数据格式不正确。", "AI service returned an invalid response.", "Perkhidmatan AI memulangkan respons tidak sah."),
    "ai.empty_response": ("AI 没有返回文字，请检查模型或稍后重试。", "AI returned no text. Check the model or try again later.", "AI tidak memulangkan teks. Semak model atau cuba lagi nanti."),
    "weather.rime_fog": ("雾凇", "Rime fog", "Kabus beku"),
    "weather.light_drizzle": ("轻毛毛雨", "Light drizzle", "Gerimis ringan"),
    "weather.heavy_drizzle": ("强毛毛雨", "Heavy drizzle", "Gerimis lebat"),
    "weather.error_location_not_found": ("没有找到该城市，请换一个名称搜索。", "No matching city found. Try another name.", "Bandar tidak ditemui. Cuba nama lain."),
    "weather.error_invalid_location": ("地点坐标无效，请重新选择城市。", "Invalid location coordinates. Select the city again.", "Koordinat lokasi tidak sah. Pilih bandar semula."),
    "weather.error_network": ("天气服务连接失败，请检查网络后重试。", "Could not connect to the weather service. Check your connection.", "Tidak dapat menyambung ke perkhidmatan cuaca. Semak sambungan anda."),
    "weather.error_timeout": ("天气查询超时，请稍后重试。", "The weather request timed out. Try again later.", "Permintaan cuaca tamat masa. Cuba lagi kemudian."),
    "weather.error_service": ("天气服务暂时不可用，请稍后重试。", "The weather service is temporarily unavailable.", "Perkhidmatan cuaca tidak tersedia buat sementara waktu."),
    "weather.error_incomplete": ("天气服务返回的数据不完整，请稍后重试。", "The weather service returned incomplete data.", "Perkhidmatan cuaca mengembalikan data tidak lengkap."),
}

WEATHER_CODE_KEYS = {
    0: "clear", 1: "mostly_clear", 2: "partly_cloudy", 3: "overcast",
    45: "fog", 48: "rime_fog", 51: "light_drizzle", 53: "drizzle", 55: "heavy_drizzle",
    56: "freezing_drizzle", 57: "freezing_drizzle", 61: "light_rain", 63: "rain",
    65: "heavy_rain", 66: "freezing_rain", 67: "freezing_rain", 71: "light_snow",
    73: "snow", 75: "heavy_snow", 77: "snow_grains", 80: "showers", 81: "showers",
    82: "heavy_showers", 85: "snow_showers", 86: "heavy_snow_showers",
    95: "thunderstorm", 96: "hail", 99: "heavy_hail",
}

_LANG_INDEX = {"zh-CN": 0, "en": 1, "ms": 2}
_EXACT = {text: key for key, variants in CATALOG.items() for text in variants}
_TEMPLATES: list[tuple[re.Pattern[str], str]] = []
for _key, _variants in CATALOG.items():
    for _variant in _variants:
        if "{" not in _variant:
            continue
        _parts = []
        _seen: set[str] = set()
        for _literal, _field, _format, _conversion in Formatter().parse(_variant):
            _parts.append(re.escape(_literal))
            if _field:
                _parts.append(f"(?P={_field})" if _field in _seen else f"(?P<{_field}>.+?)")
                _seen.add(_field)
        _TEMPLATES.append((re.compile("".join(_parts), re.DOTALL), _key))


def normalize_language(value: object) -> str:
    text = str(value or "").strip()
    for code, label in LANGUAGES.items():
        if text == label or text.lower() == code.lower():
            return code
    prefix = text.lower().split("-")[0].split("_")[0]
    return {"zh": "zh-CN", "en": "en", "ms": "ms"}.get(prefix, "zh-CN")


def weather_condition(code: int, language: str = "zh-CN") -> str:
    """Render a cached weather code immediately in the selected UI language."""
    return tr("weather." + WEATHER_CODE_KEYS.get(code, "change"), language)


def tr(key: str, language: str = "zh-CN", **values: object) -> str:
    variants = CATALOG.get(key)
    if variants is None:
        return localize(key, language)
    text = variants[_LANG_INDEX[normalize_language(language)]]
    if not values:
        return text
    return text.format(**values)


def localize(text: str, language: str = "zh-CN") -> str:
    """Translate known UI text in either direction, leaving unknown text intact."""
    if not isinstance(text, str):
        return text
    language = normalize_language(language)
    key = _EXACT.get(text)
    if key is not None:
        return tr(key, language)
    for pattern, key in _TEMPLATES:
        match = pattern.fullmatch(text)
        if match:
            return tr(key, language, **match.groupdict())
    return text
