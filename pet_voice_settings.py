"""Speech preferences in the existing settings palette, with local-only saving."""
from __future__ import annotations
import tkinter as tk
from tkinter import ttk, messagebox

from pet_cloud_voice import (CREDENTIAL_TARGET, SpeechCredentials, SpeechError,
                             VOICES, error_text, voice_settings)
from pet_i18n import LANGUAGES, tr
from pet_settings import _window, PAGE, CARD, INK, MUTED
from pet_voice_catalog import VOICE_GROUPS, voice_category, voice_label


def save_speech_configuration(app, credentials, options):
    if bool(credentials.secret_id.strip()) != bool(credentials.secret_key.strip()):
        raise SpeechError("voice.credentials_pair")
    old_credential = app._load_api_key(CREDENTIAL_TARGET)
    old_settings = dict(app.settings)
    try:
        app._store_api_key(credentials.serialize() if credentials.valid else "", CREDENTIAL_TARGET)
        app.settings["tencent_speech"] = voice_settings(options)
        app._save_settings_file()
    except (OSError, ValueError):
        app.settings.clear()
        app.settings.update(old_settings)
        try:
            app._store_api_key(old_credential, CREDENTIAL_TARGET)
        except OSError as exc:
            raise SpeechError("voice.failed") from exc
        raise


def open_speech_settings(app):
    win = _window(app, "speech_settings_window", "腾讯云语音设置", (660, 780))
    if win is None:
        return
    L = lambda key: tr(key, app.language)
    outer = ttk.Frame(win, padding=22, style="Settings.TFrame")
    outer.pack(fill="both", expand=True)
    ttk.Label(outer, text=L("voice.settings"), font=("Microsoft YaHei UI", 19, "bold"),
              style="Settings.TLabel").pack(anchor="w")
    ttk.Label(outer, text=L("voice.subtitle"), style="Settings.TLabel", foreground=MUTED).pack(anchor="w", pady=(5, 16))
    footer = ttk.Frame(outer, style="Settings.TFrame")
    footer.pack(side="bottom", fill="x", pady=(14, 0))
    scroll = ttk.Scrollbar(outer, orient="vertical", style="Settings.Vertical.TScrollbar")
    scroll.pack(side="right", fill="y")
    canvas = tk.Canvas(outer, bg=PAGE, highlightthickness=0, yscrollcommand=scroll.set)
    canvas.pack(fill="both", expand=True)
    scroll.configure(command=canvas.yview)
    content = ttk.Frame(canvas, padding=16, style="SettingsCard.TFrame")
    item = canvas.create_window(0, 0, window=content, anchor="nw")
    canvas.bind("<Configure>", lambda event: canvas.itemconfigure(item, width=event.width))
    content.bind("<Configure>", lambda _e: canvas.configure(scrollregion=canvas.bbox("all")))
    win.bind("<MouseWheel>", lambda event: canvas.yview_scroll(-int(event.delta / 120), "units"), add="+")
    def label(key, parent=content):
        widget = ttk.Label(parent, text=L(key), style="SettingsCard.TLabel", wraplength=510)
        widget.pack(anchor="w", pady=(9, 5))
        return widget
    credentials = SpeechCredentials.parse(app._load_api_key(CREDENTIAL_TARGET))
    options = voice_settings(app.settings.get("tencent_speech"))
    label("voice.credentials").configure(font=("Microsoft YaHei UI", 11, "bold"))
    entries = []
    for title, value in (("SecretId", credentials.secret_id), ("SecretKey", credentials.secret_key)):
        ttk.Label(content, text=title, style="SettingsCard.TLabel").pack(anchor="w", pady=(7, 3))
        entry = ttk.Entry(content, style="Settings.TEntry", show="•")
        entry.insert(0, value)
        entry.pack(fill="x")
        entries.append(entry)
    label("voice.credential_hint").configure(foreground=MUTED, font=("Microsoft YaHei UI", 9))
    label("voice.recognition_language")
    languages = list(LANGUAGES)
    recognition = ttk.Combobox(content, state="readonly", values=list(LANGUAGES.values()), style="Settings.TCombobox")
    recognition.current(languages.index(options["recognition_language"]))
    recognition.pack(fill="x")
    voice_row = ttk.Frame(content, style="SettingsCard.TFrame")
    voice_row.pack(fill="x", pady=(5, 0))
    voice_boxes = {}
    preview_language = ["zh-CN"]
    for column, (lang, key, title) in enumerate((("zh-CN", "voice_zh", "voice.chinese_voice"), ("en", "voice_en", "voice.english_voice"))):
        frame = ttk.Frame(voice_row, style="SettingsCard.TFrame")
        frame.pack(fill="x", pady=(0, 5))
        label(title, frame)
        items = list(VOICES[lang])
        if lang == "zh-CN":
            categories = list(VOICE_GROUPS)
            category = ttk.Combobox(frame, state="readonly", values=[app._t(name) for name in categories],
                                    width=26, style="Settings.TCombobox")
            category._localizable_values = categories
            category.current(categories.index(voice_category(options[key])))
            category.pack(fill="x", pady=(0, 5))
            items[:] = VOICE_GROUPS[categories[category.current()]]
        combo = ttk.Combobox(frame, state="readonly", values=[voice_label(number) if lang == "zh-CN" else f"{name} · {number}" for number, name in items],
                             width=40, style="Settings.TCombobox")
        combo.current([number for number, _ in items].index(options[key]))
        combo.pack(fill="x")
        combo.bind("<<ComboboxSelected>>", lambda _event, language=lang: preview_language.__setitem__(0, language))
        voice_boxes[key] = (combo, items)
        if lang == "zh-CN":
            def change_category(_event=None, box=combo, active=items, selector=category):
                preview_language[0] = "zh-CN"
                active[:] = VOICE_GROUPS[categories[selector.current()]]
                box.configure(values=[voice_label(number) for number, _ in active])
                box.current(0)
            category.bind("<<ComboboxSelected>>", change_category)
    levels = ttk.Frame(content, style="SettingsCard.TFrame")
    levels.pack(fill="x")
    number_vars = {}
    for key, title, low, high in (("speed", "voice.speed", -2, 2), ("volume", "voice.volume", -10, 5)):
        frame = ttk.Frame(levels, style="SettingsCard.TFrame")
        frame.pack(side="left", fill="x", expand=True, padx=(0, 12))
        label(title, frame)
        variable = tk.DoubleVar(value=options[key])
        ttk.Spinbox(frame, from_=low, to=high, increment=.25 if key == "speed" else 1,
                    textvariable=variable, width=16).pack(fill="x", ipady=5)
        number_vars[key] = variable
    auto = tk.BooleanVar(value=options["auto_play"])
    row = ttk.Frame(content, style="SettingsCard.TFrame")
    row.pack(fill="x", pady=(16, 8))
    ttk.Label(row, text=L("voice.auto"), style="SettingsCard.TLabel").pack(side="left")
    ttk.Checkbutton(row, variable=auto, style="SettingsSwitch.TCheckbutton").pack(side="right")
    auto_send = tk.BooleanVar(value=options["auto_send"])
    row = ttk.Frame(content, style="SettingsCard.TFrame")
    row.pack(fill="x", pady=8)
    ttk.Label(row, text=L("voice.auto_send"), style="SettingsCard.TLabel").pack(side="left")
    ttk.Checkbutton(row, variable=auto_send, style="SettingsSwitch.TCheckbutton").pack(side="right")
    label("voice.privacy").configure(foreground=MUTED, font=("Microsoft YaHei UI", 9))
    label("voice.language_hint").configure(foreground=MUTED, font=("Microsoft YaHei UI", 9))
    status = ttk.Label(content, style="SettingsCard.TLabel", wraplength=510, foreground="#9a641a")
    status.pack(fill="x", pady=8)
    def values():
        settings = {"recognition_language": languages[recognition.current()], "auto_play": auto.get(), "auto_send": auto_send.get(),
                    **{key: variable.get() for key, variable in number_vars.items()}}
        for key, (combo, items) in voice_boxes.items():
            settings[key] = items[combo.current()][0]
        return SpeechCredentials(entries[0].get().strip(), entries[1].get().strip()), voice_settings(settings)
    def save():
        try:
            credential, settings = values()
            save_speech_configuration(app, credential, settings)
            app._cancel_voice_jobs()
            win.close_window()
        except (SpeechError, OSError, ValueError, tk.TclError) as exc:
            messagebox.showerror(app._t("保存失败"), error_text(exc, app.language), parent=win)
    def preview():
        try:
            credential, settings = values()
            language = preview_language[0]
            def update(phase, error):
                def display():
                    if win.winfo_exists():
                        status.configure(text=error_text(error, app.language) if error else L(phase))
                app._schedule_ui(display)
            app._cancel_voice_jobs()
            app.speech_output.play(tr("voice.preview_text", language), language, credential, settings, update)
        except (SpeechError, ValueError, tk.TclError) as exc:
            status.configure(text=error_text(exc, app.language))
    # Closing the dialog cancels pending preview audio as well as live playback.
    original_close = win.close_window
    def close():
        app._cancel_voice_jobs()
        original_close()
    win.close_window = close
    win.protocol("WM_DELETE_WINDOW", close)
    win.bind("<Escape>", lambda _e: close())
    ttk.Button(footer, text=L("voice.preview"), command=preview, style="Settings.TButton").pack(side="left")
    ttk.Button(footer, text=app._t("保存设置"), command=save, style="SettingsPrimary.TButton").pack(side="right")
    ttk.Button(footer, text=app._t("取消"), command=close, style="Settings.TButton").pack(side="right", padx=8)
