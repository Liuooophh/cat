"""Local settings windows. Network tests run only after an explicit button click."""
from __future__ import annotations

import sys
import threading
import time
import tkinter as tk
from pathlib import Path
from tkinter import messagebox, ttk

from pet_ai import ChatError, normalize_chat_url, test_connection, migrate_model_name, provider_name, legacy_credential_target
from pet_context import WeatherError, WeatherLocation, search_weather_locations, fetch_weather_at
from pet_i18n import LANGUAGES, weather_condition


PAGE = "#f7f4fb"
CARD = "#ffffff"
INK = "#493d50"
MUTED = "#82768d"
ACCENT = "#7561a8"


def _settings_style(win):
    """Scope the settings palette so opening settings first looks intentional."""
    from PIL import Image, ImageDraw, ImageTk

    style = ttk.Style(win)
    if style.theme_use() != "clam":
        style.theme_use("clam")
    font = ("Microsoft YaHei UI", 10)
    style.configure("Settings.TFrame", background=PAGE)
    style.configure("SettingsCard.TFrame", background=CARD)
    style.configure("Settings.TLabel", background=PAGE, foreground=INK, font=font)
    style.configure("SettingsCard.TLabel", background=CARD, foreground=INK, font=font)
    style.configure("Settings.TNotebook", background=PAGE, borderwidth=0,
                    bordercolor=PAGE, lightcolor=PAGE, darkcolor=PAGE, tabmargins=0)
    style.layout("Settings.TNotebook.Tab", [])
    style.configure("SettingsNav.TButton", background=PAGE, foreground=MUTED,
                    font=font, padding=(20, 10), borderwidth=0)
    style.configure("SettingsNavActive.TButton", background="#eae3f5", foreground=ACCENT,
                    font=("Microsoft YaHei UI", 10, "bold"), padding=(20, 10), borderwidth=0)
    style.map("SettingsNav.TButton", background=[("active", "#f0eaf8")])
    style.map("SettingsNavActive.TButton", background=[("active", "#e3d9f2")])
    style.configure("Settings.Vertical.TScrollbar", background="#d7cce7", troughcolor=PAGE,
                    borderwidth=0, width=8, lightcolor="#d7cce7", darkcolor="#d7cce7")
    style.layout("Settings.Vertical.TScrollbar", [("Vertical.Scrollbar.trough", {
        "sticky": "ns", "children": [("Vertical.Scrollbar.thumb", {"expand": True, "sticky": "nswe"})],
    })])
    style.configure("Settings.TButton", font=font, padding=(16, 9), borderwidth=0,
                    background="#eee8f7", foreground=INK)
    style.map("Settings.TButton", background=[("active", "#e3d9f2"), ("disabled", "#efedf2")],
              foreground=[("disabled", "#a69daa")])
    style.configure("SettingsPrimary.TButton", font=("Microsoft YaHei UI", 10, "bold"),
                    padding=(20, 9), borderwidth=0, background=ACCENT, foreground="white")
    style.map("SettingsPrimary.TButton", background=[("active", "#665293"), ("disabled", "#c0b6d3")])
    for name in ("Settings.TEntry", "Settings.TCombobox"):
        style.configure(name, font=font, padding=(10, 8), foreground=INK,
                        fieldbackground=CARD, background=CARD, bordercolor="#e5deed",
                        lightcolor=CARD, darkcolor=CARD, arrowcolor=ACCENT)
        style.map(name, bordercolor=[("focus", "#a695c4")],
                  fieldbackground=[("readonly", CARD)], selectbackground=[("!focus", CARD)],
                  selectforeground=[("!focus", INK)])
    win.option_add("*TCombobox*Listbox.font", font)
    win.option_add("*TCombobox*Listbox.background", CARD)
    win.option_add("*TCombobox*Listbox.foreground", INK)
    if "PawpalSwitch" not in style.element_names():
        images = []
        for color in ("#d6cedf", ACCENT, "#c9bdd9", "#655094"):
            image = Image.new("RGBA", (42 * 3, 24 * 3))
            draw = ImageDraw.Draw(image)
            draw.rounded_rectangle((3, 3, 123, 69), radius=33, fill=color)
            selected = color in {ACCENT, "#655094"}
            x = 23 if selected else 3
            draw.ellipse((x * 3, 3 * 3, (x + 16) * 3, 19 * 3), fill="white")
            images.append(ImageTk.PhotoImage(image.resize((42, 24), Image.Resampling.LANCZOS), master=win))
        win._root()._settings_switch_images = images
        style.element_create("PawpalSwitch", "image", images[0],
                             ("selected", "active", images[3]),
                             ("selected", images[1]), ("active", images[2]), border=0, sticky="")
    style.layout("SettingsSwitch.TCheckbutton", [("Checkbutton.padding", {"children": [
        ("PawpalSwitch", {"side": "left", "sticky": ""}),
    ]})])
    style.configure("SettingsSwitch.TCheckbutton", background=CARD, padding=(4, 5))
    style.map("SettingsSwitch.TCheckbutton", background=[("active", CARD)])


def _window(app, attribute, title, size):
    if getattr(app, "quick_voice", None) is not None:
        app.quick_voice.hide()
    existing = getattr(app, attribute, None)
    if existing is not None:
        try:
            if existing.winfo_exists():
                existing.deiconify()
                existing.lift()
                existing.focus_force()
                return None
        except tk.TclError:
            pass
    win = tk.Toplevel(app.root)
    setattr(app, attribute, win)
    win.title(app._t(title))
    width, height = size
    height = min(height, win.winfo_screenheight() - 100)
    win.geometry(f"{width}x{height}+{max(8, (win.winfo_screenwidth()-width)//2)}+30")
    win.minsize(min(width, 560), min(height, 480))
    win.configure(bg=PAGE)
    _settings_style(win)
    try:
        root = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parent))
        win.iconbitmap(str(root / "pet_icon.ico"))
    except tk.TclError:
        pass
    win.attributes("-topmost", True)
    def close():
        if getattr(app, attribute, None) is win:
            setattr(app, attribute, None)
        win.destroy()
    win.protocol("WM_DELETE_WINDOW", close)
    win.bind("<Escape>", lambda _event: close())
    win.close_window = close
    return win


def _label(parent, app, text, **kwargs):
    kwargs.setdefault("style", "Settings.TLabel")
    return ttk.Label(parent, text=app._t(text), wraplength=530, **kwargs)


def save_ai_configuration(app, url: str, model_name: str, api_key: str, profiles: dict) -> None:
    """Save a provider profile and its credential together, restoring on failure.

    Runs only from the explicit Save action. No key enters the JSON profile.
    Keeping this operation separate also permits non-GUI persistence tests.
    """
    normalize_chat_url(url)
    model_name = migrate_model_name(url, model_name)
    if not model_name:
        raise ChatError("invalid_model")
    profile_id = provider_name(url)
    if profile_id not in {"OpenAI", "DeepSeek"}:
        profile_id = "Custom"
    saved_profiles = {str(name): {"endpoint": str(value.get("endpoint", "")),
                                 "model": str(value.get("model", ""))}
                      for name, value in profiles.items() if isinstance(value, dict)}
    saved_profiles[profile_id] = {"endpoint": url, "model": model_name}
    target = app._credential_target(url)
    old_key = app._load_api_key(target)
    old_settings = dict(app.settings)
    legacy_endpoint = getattr(app, "_legacy_key_endpoint", None)
    try:
        clear_legacy = bool(legacy_endpoint and app._credential_target(legacy_endpoint) == target)
    except (ChatError, ValueError):
        clear_legacy = False
    old_shared_key = app._load_api_key() if clear_legacy else ""
    old_targets = {legacy_credential_target(url)}
    if clear_legacy:
        old_targets.add(legacy_credential_target(legacy_endpoint))
    old_targets.discard(target)
    old_target_keys = {old_target: app._load_api_key(old_target) for old_target in old_targets}
    changed = False
    try:
        app._store_api_key(api_key, target)
        changed = True
        for old_target in old_targets:
            app._store_api_key("", old_target)
        if clear_legacy:
            app._store_api_key("")
        app.settings.update(endpoint=url, model=model_name, ai_profiles=saved_profiles)
        app._save_settings_file()
    except (OSError, ValueError):
        app.settings.clear()
        app.settings.update(old_settings)
        if changed:
            try:
                app._store_api_key(old_key, target)
                for old_target, old_value in old_target_keys.items():
                    app._store_api_key(old_value, old_target)
                if clear_legacy:
                    app._store_api_key(old_shared_key)
            except OSError as restore_error:
                raise OSError("Settings were not saved and credential recovery failed. Re-enter the API key before testing again.") from restore_error
        raise
    if clear_legacy:
        app._legacy_key_endpoint = None


def weather_city_name(settings):
    location = settings.get("weather_location")
    return str(settings.get("weather_city") or
               (location.get("name", "") if isinstance(location, dict) else "") or "").strip()


def weather_preference_update(settings, query, location, changed):
    """Return only an explicitly edited weather configuration."""
    if not changed:
        return {}
    if not query.strip():
        return {"weather_city": "", "weather_location": None}
    if location is None:
        raise ValueError("Select a searched weather location")
    return {"weather_city": location.name, "weather_location": location.to_dict()}


def open_preferences(app):
    win = _window(app, "companion_window", "基础设置", (660, 780))
    if win is None:
        return
    L = app._t
    pad = ttk.Frame(win, padding=(24, 20), style="Settings.TFrame")
    pad.pack(fill="both", expand=True)
    header = ttk.Frame(pad, style="Settings.TFrame")
    header.pack(fill="x", pady=(0, 18))
    from PIL import Image, ImageTk
    resource_root = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parent))
    with Image.open(resource_root / "pet_icon.png") as icon:
        win._header_icon = ImageTk.PhotoImage(
            icon.convert("RGBA").resize((56, 56), Image.Resampling.LANCZOS), master=win,
        )
    ttk.Label(header, image=win._header_icon, style="Settings.TLabel").pack(side="right")
    _label(header, app, "基础设置", font=("Microsoft YaHei UI", 19, "bold")).pack(anchor="w")
    _label(header, app, "让猫咪按你的习惯陪伴。", foreground=MUTED).pack(anchor="w", pady=(4, 0))
    language_row = ttk.Frame(pad, style="Settings.TFrame")
    language_row.pack(fill="x", pady=(0, 18))
    _label(language_row, app, "语言").pack(side="left", padx=(0, 18))
    language = ttk.Combobox(language_row, state="readonly", values=list(LANGUAGES.values()),
                            width=22, style="Settings.TCombobox")
    language.current(list(LANGUAGES).index(app.language))
    language.pack(side="left")
    def change_language(_event=None):
        app._apply_language(list(LANGUAGES)[language.current()])
    language.bind("<<ComboboxSelected>>", change_language)

    navigation = ttk.Frame(pad, style="Settings.TFrame")
    navigation.pack(fill="x", pady=(0, 10))
    tabs = ttk.Notebook(pad, style="Settings.TNotebook")
    tabs.pack(fill="both", expand=True)
    display = ttk.Frame(tabs, style="Settings.TFrame")
    weather = ttk.Frame(tabs, padding=(4, 12), style="Settings.TFrame")
    tabs.add(display, text=L("显示与启动"))
    tabs.add(weather, text=L("天气"))
    win._translated_tabs = tabs
    navigation_buttons = []
    for page, title in ((display, "显示与启动"), (weather, "天气")):
        button = ttk.Button(navigation, text=L(title), style="SettingsNav.TButton",
                            command=lambda target=page: tabs.select(target))
        button.pack(side="left", padx=(0, 8))
        navigation_buttons.append((page, button))
    def update_navigation(_event=None):
        for page, button in navigation_buttons:
            button.configure(style=("SettingsNavActive.TButton" if tabs.select() == str(page)
                                     else "SettingsNav.TButton"))
    tabs.bind("<<NotebookTabChanged>>", update_navigation)
    update_navigation()
    ttk.Button(navigation, text=app._t("语音"), style="SettingsNav.TButton",
               command=app.open_speech_settings).pack(side="left", padx=(0, 8))
    scroll = ttk.Scrollbar(display, orient="vertical", style="Settings.Vertical.TScrollbar")
    scroll.pack(side="right", fill="y")
    canvas = tk.Canvas(display, bg=PAGE, highlightthickness=0, bd=0,
                       yscrollcommand=scroll.set)
    canvas.pack(side="left", fill="both", expand=True)
    scroll.configure(command=canvas.yview)
    content = ttk.Frame(canvas, style="Settings.TFrame")
    content_id = canvas.create_window(0, 0, window=content, anchor="nw")
    content.bind("<Configure>", lambda _e: canvas.configure(scrollregion=canvas.bbox("all")))
    canvas.bind("<Configure>", lambda event: canvas.itemconfigure(content_id, width=event.width))
    def wheel(event):
        if tabs.select() == str(display) and content.winfo_height() > canvas.winfo_height():
            canvas.yview_scroll(-int(event.delta / 120), "units")
    win.bind("<MouseWheel>", wheel, add="+")

    def section(title):
        _label(content, app, title, foreground=MUTED,
               font=("Microsoft YaHei UI", 9, "bold")).pack(anchor="w", pady=(8, 8))
        border = tk.Frame(content, bg="#e9e2ef", padx=1, pady=1)
        border.pack(fill="x", padx=(0, 8), pady=(0, 10))
        return ttk.Frame(border, style="SettingsCard.TFrame")

    desktop_card = section("桌面显示")
    desktop_card.pack(fill="both", expand=True)
    reminders_card = None
    check_vars = {}
    for index, (key, label, hint) in enumerate((
        ("show_status_bubble", "显示数据气泡", "待机或睡觉时，轻轻显示时间、天气和电脑状态。"),
        ("show_fps", "显示前台应用 FPS", "显示当前游戏或前台应用的帧率。"),
        ("show_temperature", "托盘显示 CPU / GPU 温度", "温度只显示在托盘；无有效读数时显示 --°C。"),
        ("start_with_windows", "登录 Windows 后自动启动", "登录后，让猫咪自动来到桌面。"),
        ("cpu_reactions", "电脑忙碌提醒", "CPU 持续高负载时提醒；只在本机采样。"),
        ("night_reactions", "深夜休息提醒", "00:00–04:59 偶尔轻声提醒你休息。"),
    )):
        if index == 4:
            reminders_card = section("轻声提醒")
            reminders_card.pack(fill="both", expand=True)
        card = reminders_card if index >= 4 else desktop_card
        if index not in {0, 4}:
            tk.Frame(card, bg="#f0ebf5", height=1).pack(fill="x", padx=16)
        row = ttk.Frame(card, padding=(16, 9), style="SettingsCard.TFrame")
        row.pack(fill="x")
        copy = ttk.Frame(row, style="SettingsCard.TFrame")
        copy.pack(side="left", fill="both", expand=True)
        title_label = _label(copy, app, label, style="SettingsCard.TLabel")
        title_label.configure(wraplength=440)
        title_label.pack(anchor="w")
        hint_label = _label(copy, app, hint, style="SettingsCard.TLabel", foreground=MUTED,
                            font=("Microsoft YaHei UI", 9))
        hint_label.configure(wraplength=440)
        hint_label.pack(anchor="w", pady=(3, 0))
        row.bind("<Configure>", lambda event, title=title_label, note=hint_label: (
            title.configure(wraplength=max(200, event.width - 110)),
            note.configure(wraplength=max(200, event.width - 110)),
        ))
        default = key not in {"cpu_reactions", "night_reactions"}
        variable = tk.BooleanVar(value=bool(app.settings.get(key, default)))
        if key == "start_with_windows" and app.startup_status is not None and app.startup_status.disabled_by_windows:
            variable.set(False)
        check_vars[key] = variable
        switch = ttk.Checkbutton(row, text=L(label), variable=variable,
                                 style="SettingsSwitch.TCheckbutton", cursor="hand2")
        switch.pack(side="right", padx=(10, 0))
        for widget in (title_label, hint_label):
            widget.configure(cursor="hand2")
            widget.bind("<Button-1>", lambda _e, control=switch: control.invoke())
    if not getattr(sys, "frozen", False):
        _label(content, app, "源码运行时不设置开机启动", foreground=MUTED).pack(fill="x", pady=6)
    elif app.startup_status is not None and app.startup_status.disabled_by_windows:
        _label(content, app, "已在 Windows 启动应用中关闭", foreground="#9a641a").pack(fill="x", pady=6)
    if app.startup_error:
        ttk.Label(content, text=app.startup_error, wraplength=530, style="Settings.TLabel",
                  foreground="#9a641a").pack(fill="x", pady=6)

    _label(weather, app, "天气城市").pack(anchor="w", pady=(0, 5))
    query_text = tk.StringVar(value=weather_city_name(app.settings))
    # Tk widgets hold only the Tcl variable name, not its Python owner.
    win._weather_query_var = query_text
    weather_changed = [False]
    query = ttk.Entry(weather, textvariable=query_text, style="Settings.TEntry")
    query.pack(fill="x", ipady=5)
    _label(weather, app, "搜索后选择城市、省州和国家；留空保存可关闭天气。",
           foreground="#71658a").pack(fill="x", pady=(6, 12))
    locations = []
    selected = [None]
    preview = [None]
    stored = app.settings.get("weather_location")
    if stored:
        try:
            selected[0] = WeatherLocation.from_dict(stored)
            locations.append(selected[0])
        except ValueError:
            pass
    location_box = ttk.Combobox(weather, state="readonly", values=[item.label for item in locations],
                              style="Settings.TCombobox")
    location_box.pack(fill="x", ipady=5, pady=(0, 10))
    if locations:
        location_box.current(0)
    def select_location(_event=None):
        weather_changed[0] = True
        index = location_box.current()
        selected[0] = locations[index] if 0 <= index < len(locations) else None
    location_box.bind("<<ComboboxSelected>>", select_location)
    status = _label(weather, app, "选择城市", foreground="#71658a")
    status.pack(fill="x", pady=(4, 12))
    def refresh_preview():
        if preview[0] is not None:
            reading = preview[0]
            condition = weather_condition(reading.code, app.language)
            status.configure(text=f"{reading.city} {condition} {reading.temperature:.0f}°C\n{L('更新于')} {reading.observed_at} · Open-Meteo")
    win._language_refresh = refresh_preview
    def invalidate_location(*_args):
        weather_changed[0] = True
        selected[0] = None
        preview[0] = None
        location_box.set("")
    query_text.trace_add("write", invalidate_location)
    weather_buttons = ttk.Frame(weather, style="Settings.TFrame")
    weather_buttons.pack(fill="x")
    search_button = ttk.Button(weather_buttons, text=L("搜索城市"), style="SettingsPrimary.TButton")
    search_button.pack(side="left")
    refresh_button = ttk.Button(weather_buttons, text=L("立即刷新"), style="Settings.TButton")
    refresh_button.pack(side="left", padx=10)
    def finish_search(found, error, searched):
        if not win.winfo_exists():
            return
        search_button.configure(state="normal")
        if query.get().strip() != searched:
            return
        locations[:] = found
        location_box.configure(values=[item.label for item in found])
        location_box.set("")
        selected[0] = None
        status.configure(text=error or (L("请选择城市后保存") if found else str(WeatherError("location_not_found", app.language))))
        if found:
            location_box.current(0)
            select_location()
    def search():
        text = query.get().strip()
        search_button.configure(state="disabled")
        status.configure(text=L("获取中…"))
        def run():
            try:
                found, error = search_weather_locations(text, language=app.language), None
            except (OSError, ValueError) as exc:
                found, error = [], str(exc)
            app._schedule_ui(lambda: finish_search(found, error, text))
        threading.Thread(target=run, name="pawpal-city-search", daemon=True).start()
    search_button.configure(command=search)
    def refresh():
        location = selected[0]
        if location is None:
            status.configure(text=L("请先搜索并选择城市"))
            return
        refresh_button.configure(state="disabled")
        status.configure(text=L("获取中…"))
        def finish(reading, error):
            if not win.winfo_exists():
                return
            refresh_button.configure(state="normal")
            if selected[0] != location:
                return
            if error:
                status.configure(text=error)
            else:
                preview[0] = reading
                refresh_preview()
        def run():
            try:
                reading, error = fetch_weather_at(location, language=app.language), None
            except (OSError, ValueError) as exc:
                reading, error = None, str(exc)
            app._schedule_ui(lambda: finish(reading, error))
        threading.Thread(target=run, name="pawpal-weather-test", daemon=True).start()
    refresh_button.configure(command=refresh)
    buttons = ttk.Frame(pad, style="Settings.TFrame")
    buttons.pack(fill="x", pady=(14, 0))
    def save():
        city = query.get().strip()
        location = selected[0]
        if weather_changed[0] and city and (location is None or location_box.current() < 0):
            tabs.select(weather)
            status.configure(text=L("请先搜索并选择城市"))
            return
        old_location = app.settings.get("weather_location")
        old_startup = bool(app.settings.get("start_with_windows", True))
        update_weather = weather_preference_update(app.settings, city, location, weather_changed[0])
        new_location = update_weather.get("weather_location", old_location)
        old_settings = dict(app.settings)
        app.settings.update({key: variable.get() for key, variable in check_vars.items()})
        app.settings.update(update_weather)
        try:
            app._save_settings_file()
        except OSError as exc:
            app.settings.clear()
            app.settings.update(old_settings)
            messagebox.showerror(L("保存失败"), str(exc), parent=win)
            return
        app.metrics.configure(fps_enabled=check_vars["show_fps"].get(),
                              temperature_enabled=check_vars["show_temperature"].get())
        if old_location != new_location:
            app.weather_info = None
            app.weather_error = None
            app.context_reactions.reset_weather()
        if new_location and preview[0] is not None and preview[0].location == location:
            # Reuse the successful explicit preview; saving should immediately
            # show it rather than discard it and make a second network call.
            app.weather_info = preview[0]
            app.weather_error = None
            app._weather_failures = 0
            app.next_weather_refresh = time.monotonic() + 900.0
        elif weather_changed[0]:
            app.next_weather_refresh = 0.0
        if getattr(sys, "frozen", False) and old_startup != check_vars["start_with_windows"].get():
            app._apply_startup(check_vars["start_with_windows"].get())
            if app.startup_error:
                messagebox.showerror(L("保存失败"), app.startup_error, parent=win)
                return
        app._update_chat_context_status(force=True)
        win.close_window()
    def explain_readings():
        messagebox.showinfo(L("读数与启动说明"), "\n\n".join(L(text) for text in (
            "FPS 跟随前台应用；未持续渲染或无法采集时显示暂无数据。",
            "已内置 LibreHardwareMonitor；CPU 读取可能需要 PawnIO 和权限。不支持时显示 --°C，诊断步骤见 README。",
            "自启动使用稳定副本；可在此处或 Windows 启动应用中关闭。",
        )), parent=win)
    ttk.Button(buttons, text=L("读数与启动说明"), command=explain_readings,
               style="Settings.TButton").pack(side="left")
    ttk.Button(buttons, text=L("保存设置"), command=save, style="SettingsPrimary.TButton").pack(side="right")
    ttk.Button(buttons, text=L("取消"), command=win.close_window, style="Settings.TButton").pack(side="right", padx=8)


def open_ai_settings(app):
    from pet_ai import DEFAULT_ENDPOINTS, DEFAULT_MODELS, provider_name
    win = _window(app, "settings_window", "猫咪 · AI 接口设置", (620, 660))
    if win is None:
        return
    L = app._t
    pad = ttk.Frame(win, padding=20, style="Settings.TFrame")
    pad.pack(fill="both", expand=True)
    _label(pad, app, "连接 AI 接口", font=("Microsoft YaHei UI", 14, "bold")).pack(anchor="w")
    _label(pad, app, "支持 OpenAI、DeepSeek 或自定义兼容服务。").pack(fill="x", pady=(5, 14))
    provider_ids = ["OpenAI", "DeepSeek", "Custom"]
    provider_box = ttk.Combobox(pad, state="readonly", values=["OpenAI", "DeepSeek", L("自定义 / Ollama")],
                              style="Settings.TCombobox")
    provider_box._localizable_values = ("OpenAI", "DeepSeek", "自定义 / Ollama")
    provider_box.pack(fill="x", ipady=5)
    def field(label, initial="", show=""):
        _label(pad, app, label).pack(anchor="w", pady=(13, 4))
        entry = ttk.Entry(pad, show=show, style="Settings.TEntry")
        entry.insert(0, initial)
        entry.pack(fill="x", ipady=5)
        return entry
    endpoint = field("接口地址", str(app.settings.get("endpoint", "")))
    model = field("模型名称", migrate_model_name(str(app.settings.get("endpoint", "")), str(app.settings.get("model", ""))))
    key = field("API 密钥（Windows 加密保存）", app.api_key, show="•")
    _label(pad, app, "密钥留空保存会删除已存密钥。", foreground="#71658a").pack(fill="x", pady=10)
    status = _label(pad, app, "尚未测试连接", foreground="#71658a")
    status.pack(fill="x", pady=(3, 12))
    profiles = {name: dict(value) for name, value in app.settings.get("ai_profiles", {}).items()}
    drafts = {}
    current_provider = [provider_name(str(app.settings.get("endpoint", "")))]
    if current_provider[0] not in provider_ids:
        current_provider[0] = "Custom"
    provider_box.current(provider_ids.index(current_provider[0]))
    verified = [None]
    test_generation = [0]
    def values():
        return endpoint.get().strip(), model.get().strip(), key.get().strip()
    def select_provider(_event=None):
        drafts[current_provider[0]] = values()
        selected = provider_ids[provider_box.current()]
        current_provider[0] = selected
        if selected in drafts:
            new_values = drafts[selected]
        else:
            saved = profiles.get(selected, {})
            new_endpoint = saved.get("endpoint") or DEFAULT_ENDPOINTS.get(selected, "http://localhost:11434/v1")
            new_model = migrate_model_name(new_endpoint, saved.get("model") or DEFAULT_MODELS.get(selected, ""))
            new_values = new_endpoint, new_model, app._load_key_for(new_endpoint)
        for entry, text in zip((endpoint, model, key), new_values):
            entry.delete(0, "end")
            entry.insert(0, text)
        verified[0] = None
        status.configure(text=L("尚未测试连接"))
    provider_box.bind("<<ComboboxSelected>>", select_provider)
    def validate():
        url, model_name, api_key = values()
        if not model_name:
            raise ValueError(L("请填写接口地址和模型名称。"))
        normalize_chat_url(url)
        updated_model = migrate_model_name(url, model_name)
        if updated_model != model_name:
            model.delete(0, "end")
            model.insert(0, updated_model)
            model_name = updated_model
        return url, model_name, api_key
    buttons = ttk.Frame(pad, style="Settings.TFrame")
    buttons.pack(fill="x", side="bottom", pady=(12, 0))
    test_button = ttk.Button(buttons, text=L("测试连接"), style="Settings.TButton")
    test_button.pack(side="left")
    def test():
        try:
            submitted = validate()
        except ChatError as exc:
            status.configure(text=exc.localized(app.language))
            return
        except ValueError as exc:
            status.configure(text=str(exc))
            return
        test_generation[0] += 1
        generation = test_generation[0]
        test_button.configure(state="disabled")
        status.configure(text=L("正在测试连接…"))
        def finish(error):
            if not win.winfo_exists() or generation != test_generation[0]:
                return
            test_button.configure(state="normal")
            if values() != submitted:
                status.configure(text=L("尚未测试连接"))
                return
            verified[0] = submitted if error is None else None
            status.configure(text=error or L("连接已验证"))
        def run():
            try:
                test_connection(*submitted, timeout=30)
                error = None
            except ChatError as exc:
                error = exc.localized(app.language)
            app._schedule_ui(lambda: finish(error))
        threading.Thread(target=run, name="pawpal-ai-test", daemon=True).start()
    test_button.configure(command=test)
    def save():
        try:
            url, model_name, api_key = validate()
            save_ai_configuration(app, url, model_name, api_key, profiles)
        except ChatError as exc:
            status.configure(text=exc.localized(app.language))
            return
        except (ValueError, OSError) as exc:
            status.configure(text=str(exc))
            return
        app.api_key = api_key
        app.connection_verified = verified[0] == values()
        if app.chat_status is not None:
            app.chat_status.configure(text=app._mode_label())
        win.close_window()
    ttk.Button(buttons, text=L("保存设置"), command=save, style="SettingsPrimary.TButton").pack(side="right")
    ttk.Button(buttons, text=L("取消"), command=win.close_window, style="Settings.TButton").pack(side="right", padx=8)

