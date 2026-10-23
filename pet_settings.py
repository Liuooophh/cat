"""Local settings windows. Network tests run only after an explicit button click."""
from __future__ import annotations

import sys
import threading
import time
import tkinter as tk
from tkinter import messagebox, ttk

from pet_ai import ChatError, normalize_chat_url, test_connection, migrate_model_name, provider_name, legacy_credential_target
from pet_context import WeatherError, WeatherLocation, search_weather_locations, fetch_weather_at
from pet_i18n import LANGUAGES, weather_condition


def _window(app, attribute, title, size):
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
    win.configure(bg="#f7f4fb")
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


def open_preferences(app):
    win = _window(app, "companion_window", "基础设置", (620, 720))
    if win is None:
        return
    L = app._t
    pad = ttk.Frame(win, padding=18)
    pad.pack(fill="both", expand=True)
    language_row = ttk.Frame(pad)
    language_row.pack(fill="x", pady=(0, 14))
    _label(language_row, app, "语言").pack(side="left", padx=(0, 18))
    language = ttk.Combobox(language_row, state="readonly", values=list(LANGUAGES.values()), width=24)
    language.current(list(LANGUAGES).index(app.language))
    language.pack(side="left")
    def change_language(_event=None):
        app._apply_language(list(LANGUAGES)[language.current()])
    language.bind("<<ComboboxSelected>>", change_language)

    tabs = ttk.Notebook(pad)
    tabs.pack(fill="both", expand=True)
    display = ttk.Frame(tabs, padding=14)
    weather = ttk.Frame(tabs, padding=14)
    tabs.add(display, text=L("显示与启动"))
    tabs.add(weather, text=L("天气"))
    win._translated_tabs = tabs
    check_vars = {}
    for key, label in (
        ("show_status_bubble", "显示数据气泡"),
        ("show_fps", "显示前台应用 FPS"),
        ("show_temperature", "托盘显示 CPU / GPU 温度"),
        ("start_with_windows", "登录 Windows 后自动启动"),
        ("cpu_reactions", "CPU 连续高负载时提醒（本机采样，不上传）"),
        ("night_reactions", "深夜 00:00–04:59 偶尔提醒休息"),
    ):
        default = key not in {"cpu_reactions", "night_reactions"}
        variable = tk.BooleanVar(value=bool(app.settings.get(key, default)))
        if key == "start_with_windows" and app.startup_status is not None and app.startup_status.disabled_by_windows:
            variable.set(False)
        check_vars[key] = variable
        ttk.Checkbutton(display, text=L(label), variable=variable).pack(anchor="w", pady=9)
    _label(display, app, "自启动使用稳定副本；可在此处或 Windows 启动应用中关闭。",
           foreground="#71658a").pack(fill="x", pady=(10, 8))
    _label(display, app, "FPS 跟随前台应用；未持续渲染或无法采集时显示暂无数据。",
           foreground="#71658a").pack(fill="x", pady=6)
    _label(display, app, "已内置 LibreHardwareMonitor；CPU 读取可能需要 PawnIO 和权限。不支持时显示 --°C，诊断步骤见 README。",
           foreground="#71658a").pack(fill="x", pady=6)
    if not getattr(sys, "frozen", False):
        _label(display, app, "源码运行时不设置开机启动", foreground="#71658a").pack(fill="x", pady=6)
    elif app.startup_status is not None and app.startup_status.disabled_by_windows:
        _label(display, app, "已在 Windows 启动应用中关闭", foreground="#9a641a").pack(fill="x", pady=6)
    if app.startup_error:
        ttk.Label(display, text=app.startup_error, wraplength=530,
                  foreground="#9a641a").pack(fill="x", pady=6)

    _label(weather, app, "天气城市").pack(anchor="w", pady=(0, 5))
    query_text = tk.StringVar(value=str(app.settings.get("weather_city", "")))
    query = ttk.Entry(weather, textvariable=query_text)
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
    location_box = ttk.Combobox(weather, state="readonly", values=[item.label for item in locations])
    location_box.pack(fill="x", ipady=5, pady=(0, 10))
    if locations:
        location_box.current(0)
    def select_location(_event=None):
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
        selected[0] = None
        preview[0] = None
        location_box.set("")
    query_text.trace_add("write", invalidate_location)
    weather_buttons = ttk.Frame(weather)
    weather_buttons.pack(fill="x")
    search_button = ttk.Button(weather_buttons, text=L("搜索城市"))
    search_button.pack(side="left")
    refresh_button = ttk.Button(weather_buttons, text=L("立即刷新"))
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
    buttons = ttk.Frame(pad)
    buttons.pack(fill="x", pady=(14, 0))
    def save():
        city = query.get().strip()
        location = selected[0]
        if city and (location is None or location_box.current() < 0):
            tabs.select(weather)
            status.configure(text=L("请先搜索并选择城市"))
            return
        old_location = app.settings.get("weather_location")
        old_startup = bool(app.settings.get("start_with_windows", True))
        new_location = location.to_dict() if city and location else None
        old_settings = dict(app.settings)
        app.settings.update({key: variable.get() for key, variable in check_vars.items()})
        app.settings.update(weather_city=location.name if new_location else "",
                            weather_location=new_location)
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
        else:
            app.next_weather_refresh = 0.0
        if getattr(sys, "frozen", False) and old_startup != check_vars["start_with_windows"].get():
            app._apply_startup(check_vars["start_with_windows"].get())
            if app.startup_error:
                messagebox.showerror(L("保存失败"), app.startup_error, parent=win)
                return
        app._update_chat_context_status(force=True)
        win.close_window()
    ttk.Button(buttons, text=L("保存设置"), command=save).pack(side="right")
    ttk.Button(buttons, text=L("取消"), command=win.close_window).pack(side="right", padx=8)


def open_ai_settings(app):
    from pet_ai import DEFAULT_ENDPOINTS, DEFAULT_MODELS, provider_name
    win = _window(app, "settings_window", "猫咪 · AI 接口设置", (620, 660))
    if win is None:
        return
    L = app._t
    pad = ttk.Frame(win, padding=20)
    pad.pack(fill="both", expand=True)
    _label(pad, app, "连接 AI 接口", font=("Microsoft YaHei UI", 14, "bold")).pack(anchor="w")
    _label(pad, app, "支持 OpenAI、DeepSeek 或自定义兼容服务。").pack(fill="x", pady=(5, 14))
    provider_ids = ["OpenAI", "DeepSeek", "Custom"]
    provider_box = ttk.Combobox(pad, state="readonly", values=["OpenAI", "DeepSeek", L("自定义 / Ollama")])
    provider_box._localizable_values = ("OpenAI", "DeepSeek", "自定义 / Ollama")
    provider_box.pack(fill="x", ipady=5)
    def field(label, initial="", show=""):
        _label(pad, app, label).pack(anchor="w", pady=(13, 4))
        entry = ttk.Entry(pad, show=show)
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
    buttons = ttk.Frame(pad)
    buttons.pack(fill="x", side="bottom", pady=(12, 0))
    test_button = ttk.Button(buttons, text=L("测试连接"))
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
    ttk.Button(buttons, text=L("保存设置"), command=save).pack(side="right")
    ttk.Button(buttons, text=L("取消"), command=win.close_window).pack(side="right", padx=8)

