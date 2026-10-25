"""A small, always-on-top desktop companion for Windows."""

from __future__ import annotations

import json
import hashlib
import ctypes
import math
import os
import random
import sys
import tempfile
import threading
import time
import tkinter as tk
from ctypes import wintypes
from pathlib import Path
from tkinter import messagebox, ttk
from urllib.parse import urlsplit
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen
from datetime import datetime

from pet_context import (ContextReactionScheduler, WeatherReading, WeatherLocation,
                         fetch_weather, fetch_weather_at)
from pet_ai import ChatError, request_chat, credential_target as ai_credential_target, migrate_model_name
from pet_i18n import LANGUAGES, localize, normalize_language, weather_condition
from pet_metrics import MetricsMonitor
from pet_startup import StartupManager, SingleInstance
from pet_tray import temperature_lines, temperature_tooltip, temperature_icon, representative_values
from pet_visual_effects import draw_sleep_zzz
from pet_behavior import PetAction, PetBehaviorManager
from pet_dialogue import DialogueManager
from pet_motion import MotionPlayer
from pet_presentation import AnimationController, SpeechBubble
from pet_voice import WindowsSpeechInput


APP_NAME = "猫咪"
KEY_COLOR = "#fffefe"
INK = "#493d50"
PURPLE = "#7561a8"
BASE_PET_WIDTH = 360
BASE_PET_HEIGHT = 430
SPRITE_TOP = 112
FRAME_PERIOD = 1.0 / 24.0
MIN_PET_SCALE = 0.5
MAX_PET_SCALE = 1.5
DEFAULT_PET_SCALE = 1.3
OPENAI_ENDPOINT = "https://api.openai.com/v1"
OPENAI_MODEL = "gpt-5-mini"
DEEPSEEK_ENDPOINT = "https://api.deepseek.com"
DEEPSEEK_MODEL = "deepseek-flash"
WS_EX_LAYERED = 0x00080000
GWL_EXSTYLE = -20
ULW_ALPHA = 0x00000002
AC_SRC_OVER = 0x00
AC_SRC_ALPHA = 0x01
BI_RGB = 0
CRED_TYPE_GENERIC = 1
CRED_PERSIST_LOCAL_MACHINE = 2
ERROR_NOT_FOUND = 1168
SYSTEM_PROMPT = (
    "你是住在用户桌面上的一只安静的狸花猫。平时像真实猫咪一样休息、观察、舔毛，"
    "不主动唠叨、不扮演大哥、不长篇吐槽。用户主动聊天时，用自然、温和的语言回应，"
    "默认一到三句，不必每次加喵或猫咪动作描写，不使用夸张颜文字。"
    "用户需要实际帮助时认真回答，不让角色扮演妨碍清楚表达。"
    "不要声称看见屏幕、读取文件或控制电脑；没有工具就坦诚说明。"
    "关心用户但不过度说教，不套用影视作品台词。"
)


class _SIZE(ctypes.Structure):
    _fields_ = [("cx", wintypes.LONG), ("cy", wintypes.LONG)]


class _BLENDFUNCTION(ctypes.Structure):
    _fields_ = [
        ("BlendOp", ctypes.c_ubyte),
        ("BlendFlags", ctypes.c_ubyte),
        ("SourceConstantAlpha", ctypes.c_ubyte),
        ("AlphaFormat", ctypes.c_ubyte),
    ]


class _BITMAPINFOHEADER(ctypes.Structure):
    _fields_ = [
        ("biSize", wintypes.DWORD),
        ("biWidth", wintypes.LONG),
        ("biHeight", wintypes.LONG),
        ("biPlanes", wintypes.WORD),
        ("biBitCount", wintypes.WORD),
        ("biCompression", wintypes.DWORD),
        ("biSizeImage", wintypes.DWORD),
        ("biXPelsPerMeter", wintypes.LONG),
        ("biYPelsPerMeter", wintypes.LONG),
        ("biClrUsed", wintypes.DWORD),
        ("biClrImportant", wintypes.DWORD),
    ]


class _RGBQUAD(ctypes.Structure):
    _fields_ = [
        ("rgbBlue", ctypes.c_ubyte),
        ("rgbGreen", ctypes.c_ubyte),
        ("rgbRed", ctypes.c_ubyte),
        ("rgbReserved", ctypes.c_ubyte),
    ]


class _BITMAPINFO(ctypes.Structure):
    _fields_ = [("bmiHeader", _BITMAPINFOHEADER), ("bmiColors", _RGBQUAD * 1)]


class _CREDENTIAL_ATTRIBUTEW(ctypes.Structure):
    _fields_ = [
        ("Keyword", wintypes.LPWSTR),
        ("Flags", wintypes.DWORD),
        ("ValueSize", wintypes.DWORD),
        ("Value", ctypes.POINTER(ctypes.c_ubyte)),
    ]


class _CREDENTIALW(ctypes.Structure):
    _fields_ = [
        ("Flags", wintypes.DWORD),
        ("Type", wintypes.DWORD),
        ("TargetName", wintypes.LPWSTR),
        ("Comment", wintypes.LPWSTR),
        ("LastWritten", wintypes.FILETIME),
        ("CredentialBlobSize", wintypes.DWORD),
        ("CredentialBlob", ctypes.POINTER(ctypes.c_ubyte)),
        ("Persist", wintypes.DWORD),
        ("AttributeCount", wintypes.DWORD),
        ("Attributes", ctypes.POINTER(_CREDENTIAL_ATTRIBUTEW)),
        ("TargetAlias", wintypes.LPWSTR),
        ("UserName", wintypes.LPWSTR),
    ]


def resource_path(filename: str) -> Path:
    """Return an asset path for both source runs and PyInstaller bundles."""
    bundle_dir = getattr(sys, "_MEIPASS", None)
    base_dir = Path(bundle_dir) if bundle_dir else Path(__file__).resolve().parent
    return base_dir / filename


def provider_name_for_endpoint(endpoint: str) -> str:
    try:
        host = (urlsplit(endpoint).hostname or "").lower()
    except ValueError:
        return "自定义 / Ollama"
    if host == "api.openai.com":
        return "OpenAI"
    if host == "api.deepseek.com" or host.endswith(".deepseek.com"):
        return "DeepSeek"
    return "自定义 / Ollama"


def is_deepseek_endpoint(endpoint: str) -> bool:
    return provider_name_for_endpoint(endpoint) == "DeepSeek"


def rounded_polygon(canvas: tk.Canvas, x1: int, y1: int, x2: int, y2: int,
                    radius: int, **kwargs: object) -> int:
    r = min(radius, (x2 - x1) // 2, (y2 - y1) // 2)
    points = [
        x1 + r, y1, x2 - r, y1, x2, y1, x2, y1 + r,
        x2, y2 - r, x2, y2, x2 - r, y2, x1 + r, y2,
        x1, y2, x1, y2 - r, x1, y1 + r, x1, y1,
    ]
    return canvas.create_polygon(points, smooth=True, splinesteps=18, **kwargs)


class DesktopPet:
    def __init__(self) -> None:
        self.root = tk.Tk(className="TuanziPet")
        self.root.iconbitmap(default=str(resource_path("pet_icon.ico")))
        self.root.title(self._t(APP_NAME))
        self.root.overrideredirect(True)
        self.root.attributes("-topmost", True)
        self.root.configure(bg=KEY_COLOR)

        self.width, self.height = BASE_PET_WIDTH, BASE_PET_HEIGHT
        self.settings_path = self._settings_path()
        self.settings = self._load_settings()
        self.language = normalize_language(self.settings.get("language"))
        self.root.title(self._t(APP_NAME))
        self.pending_action = None
        self.connection_verified = False
        self.startup = StartupManager()
        self.startup_status = None
        self.startup_error = None
        self.metrics = MetricsMonitor(vendor_dir=resource_path("assets/vendor"))
        self.metrics.configure(fps_enabled=bool(self.settings.get("show_fps", True)),
                               temperature_enabled=bool(self.settings.get("show_temperature", True)))
        self.metrics_snapshot = self.metrics.snapshot()
        self.weather_info: WeatherReading | None = None
        self.weather_error: str | None = None
        self.weather_request_pending = False
        self.next_weather_refresh = 0.0
        self.cpu_percent: float | None = None
        self.next_cpu_sample = 0.0
        self.next_status_refresh = 0.0
        self.context_reactions = ContextReactionScheduler()
        self.speech_input = WindowsSpeechInput()
        self.companion_window: tk.Toplevel | None = None
        self.pet_scale = self._normalize_pet_scale(self.settings.get("pet_scale"))
        self.window_width = max(1, round(self.width * self.pet_scale))
        self.window_height = max(1, round(self.height * self.pet_scale))
        self._size_resize_job: str | None = None
        screen_w = self.root.winfo_screenwidth()
        screen_h = self.root.winfo_screenheight()
        self.root.geometry(
            f"{self.window_width}x{self.window_height}+"
            f"{screen_w - self.window_width - 26}+{screen_h - self.window_height - 60}"
        )
        self.animation = AnimationController(resource_path("assets/cat"))
        self._visual_state = "idle"
        self._actor_mirror = False
        self._visual_mirror = False
        self._visual_started = time.monotonic()
        self._last_sprite = None
        self._transition_sprite = None
        self._transition_started = 0.0

        self._legacy_key_endpoint = str(self.settings.get("endpoint", ""))
        self.api_key = self._load_key_for(self._legacy_key_endpoint)
        self.tray_icon = None
        self.tray_thread: threading.Thread | None = None
        self.temperature_tray_icon = None
        self.temperature_tray_thread: threading.Thread | None = None
        self._tray_metrics_signature = None
        self.history: list[dict[str, str]] = []
        self.chat_window: tk.Toplevel | None = None
        self.chat_text: tk.Text | None = None
        self.chat_input: tk.Text | None = None
        self.chat_status: ttk.Label | None = None
        self.chat_context_status: ttk.Label | None = None
        self.mic_canvas: tk.Canvas | None = None
        self.mic_listening = False
        self.chat_drag_origin: tuple[int, int, int, int] | None = None
        self.settings_window: tk.Toplevel | None = None
        self.size_window: tk.Toplevel | None = None
        self.dialogue = DialogueManager()
        durations = {
            state: self.animation.clip_duration(state)
            for state in ("click", "eat", "groom")
        }
        self.behavior = PetBehaviorManager(
            state_duration={state: duration for state, duration in durations.items()
                            if duration is not None},
        )
        self.motion = MotionPlayer(self.animation.motion_specs, now=self.behavior.state_started,
                                   idle_entries=self.animation.idle_entries)
        self.motion_pose = self.motion.sample(self.behavior.state_started)
        self.bubble = SpeechBubble()
        self.context_bubble_active = False
        self.speech_warning_shown = False
        self.sprite_frame_index = 0
        self.walk_direction = random.choice((-1, 1))
        self.walk_step_time = time.monotonic()
        self.last_hover_line = 0.0
        self.hover_inside = False
        self.hover_bubble = False
        self.waiting = False
        self.drag_start: tuple[int, int, int, int] | None = None
        self.did_drag = False
        self.current_sprite_bbox = (20, SPRITE_TOP, 340, 412)
        self._size_resize_job: str | None = None
        self.context_window: tk.Toplevel | None = None
        self.context_click_poll_job: str | None = None
        self.context_last_buttons = (False, False)
        self.user32 = ctypes.WinDLL("user32", use_last_error=True) if os.name == "nt" else None
        if self.user32 is not None:
            self.user32.GetAsyncKeyState.argtypes = [ctypes.c_int]
            self.user32.GetAsyncKeyState.restype = ctypes.c_short
            self.user32.GetCursorPos.argtypes = [ctypes.POINTER(wintypes.POINT)]
            self.user32.GetCursorPos.restype = wintypes.BOOL

        # Tk's transparent-color mode turns antialiased sprite pixels into a pale
        # fringe. Use a Windows per-pixel-alpha surface for the whole pet window.
        self.root.bind("<ButtonPress-1>", self._start_drag)
        self.root.bind("<B1-Motion>", self._drag)
        self.root.bind("<ButtonRelease-1>", self._finish_drag)
        self.root.bind("<Button-3>", self._show_menu)
        self.root.bind("<Motion>", self._on_hover_motion)
        self.root.bind("<Leave>", self._hide_hover_bubble)
        self._init_layered_surface()
        self._enable_animation_timer()
        self.root.protocol("WM_DELETE_WINDOW", self._quit_app)
        self._start_tray_icon()
        self.metrics.start()
        # Source runs and isolated QA profiles never register login startup.
        if getattr(sys, "frozen", False) and not os.environ.get("PAWPAL_DATA_DIR"):
            self._apply_startup(bool(self.settings.get("start_with_windows", True)), automatic=True)
        self._animate()

    def _t(self, text: str) -> str:
        return localize(text, getattr(self, "language", "zh-CN"))

    def _apply_language(self, language: str) -> None:
        self.language = normalize_language(language)
        self.settings["language"] = self.language
        self._save_settings_file()
        def update(widget):
            try:
                if isinstance(widget, (tk.Tk, tk.Toplevel)):
                    widget.title(self._t(widget.title()))
                if isinstance(widget, tk.Canvas):
                    for item in widget.find_all():
                        if widget.type(item) == "text":
                            widget.itemconfigure(item, text=self._t(widget.itemcget(item, "text")))
                elif isinstance(widget, ttk.Notebook):
                    for tab in widget.tabs():
                        widget.tab(tab, text=self._t(widget.tab(tab, "text")))
                elif isinstance(widget, ttk.Combobox) and hasattr(widget, "_localizable_values"):
                    selected = widget.current()
                    widget.configure(values=[self._t(value) for value in widget._localizable_values])
                    if selected >= 0:
                        widget.current(selected)
                elif "text" in widget.keys():
                    widget.configure(text=self._t(str(widget.cget("text"))))
                for child in widget.winfo_children():
                    update(child)
                refresh = getattr(widget, "_language_refresh", None)
                if callable(refresh):
                    refresh()
            except tk.TclError:
                pass
        update(self.root)
        if self.tray_icon is not None:
            self.tray_icon.menu = self._tray_menu()
            self.tray_icon.update_menu()
        self._tray_metrics_signature = None
        self._update_tray_metrics()
        if self.chat_input is not None:
            value = self.chat_input.get("1.0", "end-1c")
            if value in {localize("说点什么…", lang) for lang in LANGUAGES}:
                self.chat_input.delete("1.0", "end")
                self.chat_input.insert("1.0", self._t("说点什么…"))
        self._update_chat_context_status(force=True)

    def _apply_startup(self, enabled: bool, *, automatic: bool = False) -> None:
        try:
            status = self.startup.status()
            if enabled and automatic and status.disabled_by_windows:
                self.startup_status = status
                return
            self.startup_status = self.startup.enable() if enabled else self.startup.disable()
            self.startup_error = self.startup_status.error
        except (OSError, ValueError, RuntimeError) as exc:
            self.startup_error = str(exc)

    def _enable_animation_timer(self) -> None:
        """Request precise Windows waits for 24 fps, released with the surface."""
        self._animation_timer = None
        if os.name != "nt":
            return
        try:
            timer = ctypes.WinDLL("winmm")
            for name in ("timeBeginPeriod", "timeEndPeriod"):
                function = getattr(timer, name)
                function.argtypes = [wintypes.UINT]
                function.restype = wintypes.UINT
            if timer.timeBeginPeriod(1) == 0:
                self._animation_timer = timer
        except (OSError, AttributeError):
            pass

    def _init_layered_surface(self) -> None:
        if os.name != "nt":
            raise OSError("猫咪的逐像素透明桌宠窗口需要在 Windows 上运行。")

        self.root.update_idletasks()
        # On Windows Tk's winfo_id() is the client child; wm frame is the
        # top-level HWND that Windows requires for UpdateLayeredWindow.
        frame_hwnd = int(str(self.root.tk.call("wm", "frame", self.root._w)), 0)
        self.hwnd = wintypes.HWND(frame_hwnd)
        self.gdi32 = ctypes.WinDLL("gdi32", use_last_error=True)
        self.user32 = ctypes.WinDLL("user32", use_last_error=True)

        get_style = getattr(self.user32, "GetWindowLongPtrW", None)
        set_style = getattr(self.user32, "SetWindowLongPtrW", None)
        if get_style is None:
            get_style = self.user32.GetWindowLongW
        if set_style is None:
            set_style = self.user32.SetWindowLongW
        get_style.argtypes = [wintypes.HWND, ctypes.c_int]
        get_style.restype = ctypes.c_ssize_t
        set_style.argtypes = [wintypes.HWND, ctypes.c_int, ctypes.c_ssize_t]
        set_style.restype = ctypes.c_ssize_t
        style = get_style(self.hwnd, GWL_EXSTYLE)
        set_style(self.hwnd, GWL_EXSTYLE, style | WS_EX_LAYERED)

        self.user32.GetAsyncKeyState.argtypes = [ctypes.c_int]
        self.user32.GetAsyncKeyState.restype = ctypes.c_short
        self.user32.GetCursorPos.argtypes = [ctypes.POINTER(wintypes.POINT)]
        self.user32.GetCursorPos.restype = wintypes.BOOL

        self.gdi32.CreateCompatibleDC.argtypes = [wintypes.HDC]
        self.gdi32.CreateCompatibleDC.restype = wintypes.HDC
        self.gdi32.CreateDIBSection.argtypes = [
            wintypes.HDC, ctypes.POINTER(_BITMAPINFO), wintypes.UINT,
            ctypes.POINTER(ctypes.c_void_p), wintypes.HANDLE, wintypes.DWORD,
        ]
        self.gdi32.CreateDIBSection.restype = wintypes.HBITMAP
        self.gdi32.SelectObject.argtypes = [wintypes.HDC, wintypes.HANDLE]
        self.gdi32.SelectObject.restype = wintypes.HANDLE
        self.gdi32.DeleteObject.argtypes = [wintypes.HANDLE]
        self.gdi32.DeleteObject.restype = wintypes.BOOL
        self.gdi32.DeleteDC.argtypes = [wintypes.HDC]
        self.gdi32.DeleteDC.restype = wintypes.BOOL

        self.user32.UpdateLayeredWindow.argtypes = [
            wintypes.HWND, wintypes.HDC, ctypes.POINTER(wintypes.POINT),
            ctypes.POINTER(_SIZE), wintypes.HDC, ctypes.POINTER(wintypes.POINT),
            wintypes.COLORREF, ctypes.POINTER(_BLENDFUNCTION), wintypes.DWORD,
        ]
        self.user32.UpdateLayeredWindow.restype = wintypes.BOOL

        self._mem_dc = self.gdi32.CreateCompatibleDC(None)
        if not self._mem_dc:
            raise ctypes.WinError(ctypes.get_last_error())
        self._dib = None
        self._old_bitmap = None
        self._create_surface_bitmap(self.window_width, self.window_height)

    def _create_surface_bitmap(self, width: int, height: int) -> None:
        if getattr(self, "_dib", None):
            if getattr(self, "_old_bitmap", None):
                self.gdi32.SelectObject(self._mem_dc, self._old_bitmap)
            self.gdi32.DeleteObject(self._dib)

        bitmap_info = _BITMAPINFO()
        bitmap_info.bmiHeader = _BITMAPINFOHEADER(
            ctypes.sizeof(_BITMAPINFOHEADER), width, -height,
            1, 32, BI_RGB, 0, 0, 0, 0, 0,
        )
        self._dib_bits = ctypes.c_void_p()
        self._dib = self.gdi32.CreateDIBSection(
            self._mem_dc, ctypes.byref(bitmap_info), 0,
            ctypes.byref(self._dib_bits), None, 0,
        )
        if not self._dib or not self._dib_bits.value:
            raise ctypes.WinError(ctypes.get_last_error())
        self._old_bitmap = self.gdi32.SelectObject(self._mem_dc, self._dib)
        self.surface_width, self.surface_height = width, height

    def _present_layered_frame(self, image: object) -> None:
        from PIL import Image

        # UpdateLayeredWindow expects premultiplied BGRA pixels in a top-down DIB.
        premultiplied = image.convert("RGBa")
        red, green, blue, alpha = premultiplied.split()
        bgra = Image.merge("RGBA", (blue, green, red, alpha)).tobytes()
        ctypes.memmove(self._dib_bits, bgra, len(bgra))
        destination = wintypes.POINT(self.root.winfo_x(), self.root.winfo_y())
        source = wintypes.POINT(0, 0)
        size = _SIZE(self.window_width, self.window_height)
        blend = _BLENDFUNCTION(AC_SRC_OVER, 0, 255, AC_SRC_ALPHA)
        if not self.user32.UpdateLayeredWindow(
            self.hwnd, None, ctypes.byref(destination), ctypes.byref(size),
            self._mem_dc, ctypes.byref(source), 0, ctypes.byref(blend), ULW_ALPHA,
        ):
            raise ctypes.WinError(ctypes.get_last_error())

    def _draw(self) -> None:
        from PIL import Image, ImageDraw

        frame = Image.new("RGBA", (self.width, self.height), (0, 0, 0, 0))
        now = time.monotonic()
        pose = self.motion_pose
        visual_state = pose.state
        mirror = self._actor_mirror
        if (visual_state, mirror) != (self._visual_state, self._visual_mirror):
            self._transition_sprite = self._last_sprite
            self._transition_started = now
            self._visual_state = visual_state
            self._visual_mirror = mirror
            self._transition_duration = 0.22 if visual_state == "idle" else 0.16
        sprite = self.animation.frame_at_index(visual_state, pose.frame_index, mirror=mirror)
        blend_index = getattr(pose, "blend_from_index", None)
        if blend_index is not None:
            previous = self.animation.frame_at_index(visual_state, blend_index, mirror=mirror)
            sprite = Image.blend(previous.convert("RGBa"), sprite.convert("RGBa"),
                                 pose.blend_amount).convert("RGBA")
        sleeping = visual_state == "sleep" and pose.phase == "loop"
        if sleeping:
            if getattr(self, "_last_motion_phase", None) != ("sleep", "loop"):
                self._sleep_breath_started = now
        self._last_motion_phase = (visual_state, getattr(pose, "phase", "loop"))
        transition = min(1.0, (now - self._transition_started) / getattr(self, "_transition_duration", 0.16))
        transition = max(0.0, transition)
        transition = transition * transition * (3.0 - 2.0 * transition)
        if self._transition_sprite is not None and transition < 1.0:
            sprite = Image.blend(
                self._transition_sprite.convert("RGBa"), sprite.convert("RGBa"),
                max(0.0, transition),
            ).convert("RGBA")
        else:
            self._transition_sprite = None
        self._last_sprite = sprite
        visible = sprite.getchannel("A").getbbox() or (0, 0, *self.animation.cell_size)
        sprite_left = (self.width - sprite.width) // 2
        self.current_sprite_bbox = (
            sprite_left + visible[0], SPRITE_TOP + visible[1],
            sprite_left + visible[2], SPRITE_TOP + visible[3],
        )
        frame.alpha_composite(sprite, (sprite_left, SPRITE_TOP))
        if sleeping:
            draw_sleep_zzz(frame, self.current_sprite_bbox, now - self._sleep_breath_started, mirror)
        bubble_target = self.current_sprite_bbox[1] - (28 if sleeping else 14)
        if self.bubble.alpha <= 0.001:
            self._bubble_anchor = float(bubble_target)
        else:
            self._bubble_anchor = getattr(self, "_bubble_anchor", float(bubble_target)) * 0.7 + bubble_target * 0.3
        self.bubble.draw(ImageDraw.Draw(frame), factor=1, width=self.width,
                         font_path=r"C:\Windows\Fonts\msyh.ttc",
                         anchor_bottom=round(self._bubble_anchor),
                         status=self.context_bubble_active, frame=frame)
        if frame.size != (self.window_width, self.window_height):
            frame = frame.convert("RGBa").resize(
                (self.window_width, self.window_height), Image.Resampling.LANCZOS,
            ).convert("RGBA")
        self._present_layered_frame(frame)

    def _release_layered_surface(self) -> None:
        timer = getattr(self, "_animation_timer", None)
        if timer is not None:
            timer.timeEndPeriod(1)
            self._animation_timer = None
        if not hasattr(self, "gdi32"):
            return
        if getattr(self, "_mem_dc", None) and getattr(self, "_old_bitmap", None):
            self.gdi32.SelectObject(self._mem_dc, self._old_bitmap)
        if getattr(self, "_dib", None):
            self.gdi32.DeleteObject(self._dib)
            self._dib = None
        if getattr(self, "_mem_dc", None):
            self.gdi32.DeleteDC(self._mem_dc)
            self._mem_dc = None

    @staticmethod
    def _load_api_key(target: str = "TuanziDesktopPet/OpenAI") -> str:
        if os.name != "nt":
            return ""
        advapi32 = ctypes.WinDLL("Advapi32.dll", use_last_error=True)
        read_credential = advapi32.CredReadW
        read_credential.argtypes = [
            wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD,
            ctypes.POINTER(ctypes.POINTER(_CREDENTIALW)),
        ]
        read_credential.restype = wintypes.BOOL
        credential_ptr = ctypes.POINTER(_CREDENTIALW)()
        if not read_credential(
            target, CRED_TYPE_GENERIC, 0,
            ctypes.byref(credential_ptr),
        ):
            return ""
        try:
            credential = credential_ptr.contents
            return ctypes.string_at(
                credential.CredentialBlob, credential.CredentialBlobSize,
            ).decode("utf-8")
        except (ValueError, UnicodeDecodeError):
            return ""
        finally:
            advapi32.CredFree.argtypes = [ctypes.c_void_p]
            advapi32.CredFree.restype = None
            advapi32.CredFree(credential_ptr)

    @staticmethod
    def _store_api_key(api_key: str, target: str = "TuanziDesktopPet/OpenAI") -> None:
        advapi32 = ctypes.WinDLL("Advapi32.dll", use_last_error=True)
        if not api_key:
            delete_credential = advapi32.CredDeleteW
            delete_credential.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD]
            delete_credential.restype = wintypes.BOOL
            if not delete_credential(target, CRED_TYPE_GENERIC, 0):
                error = ctypes.get_last_error()
                if error != ERROR_NOT_FOUND:
                    raise ctypes.WinError(error)
            return

        blob = api_key.encode("utf-8")
        if len(blob) > 2560:
            raise ValueError("API 密钥长度超出 Windows 凭据管理器的限制。")
        blob_buffer = (ctypes.c_ubyte * len(blob)).from_buffer_copy(blob)
        credential = _CREDENTIALW()
        credential.Type = CRED_TYPE_GENERIC
        credential.TargetName = target
        credential.Comment = "猫咪 AI API 密钥"
        credential.CredentialBlobSize = len(blob)
        credential.CredentialBlob = ctypes.cast(
            blob_buffer, ctypes.POINTER(ctypes.c_ubyte),
        )
        credential.Persist = CRED_PERSIST_LOCAL_MACHINE
        credential.UserName = "AI API"

        write_credential = advapi32.CredWriteW
        write_credential.argtypes = [ctypes.POINTER(_CREDENTIALW), wintypes.DWORD]
        write_credential.restype = wintypes.BOOL
        if not write_credential(ctypes.byref(credential), 0):
            raise ctypes.WinError(ctypes.get_last_error())

    @staticmethod
    def _credential_target(endpoint: str) -> str:
        try:
            return ai_credential_target(endpoint)
        except (ChatError, ValueError):
            # Broken old settings must still let the user open and repair them.
            return DesktopPet._legacy_credential_target(endpoint)

    @staticmethod
    def _legacy_credential_target(endpoint: str) -> str:
        try:
            host = (urlsplit(endpoint).netloc or endpoint).lower()
        except ValueError:
            host = str(endpoint).lower()
        return "PawpalCat/AI/" + hashlib.sha256(host.encode("utf-8")).hexdigest()[:24]

    def _load_key_for(self, endpoint: str) -> str:
        key = self._load_api_key(self._credential_target(endpoint))
        legacy = self._legacy_credential_target(endpoint)
        if not key and legacy != self._credential_target(endpoint):
            key = self._load_api_key(legacy)
        if not key and endpoint == getattr(self, "_legacy_key_endpoint", None):
            key = self._load_api_key()
        return key

    def _tray_menu(self):
        import pystray
        items = [pystray.MenuItem(self._t("和猫咪聊天"), self._tray_open_chat, default=True)]
        actions = (("点击互动", "click"), ("吃猫粮", "eat"), ("洗脸", "groom"),
                   ("睡觉", "sleep"), ("唤醒", "idle"))
        def action_handler(state):
            def callback(_icon, _item):
                self._schedule_ui(lambda: self._choose_action(state))
            return callback
        items.append(pystray.MenuItem(self._t("动作"), pystray.Menu(*[
            pystray.MenuItem(self._t(label), action_handler(state))
            for label, state in actions])))
        if getattr(self, "settings", {}).get("show_temperature", True):
            items.append(pystray.MenuItem(self._t("硬件温度"), pystray.Menu(*[
                pystray.MenuItem(line, None, enabled=False)
                for line in [self._t("数字图标：上 GPU，下 CPU；多设备取最高值")] +
                    temperature_lines(getattr(self, "metrics_snapshot", None), self.language)])))
        items.extend([
            pystray.MenuItem(self._t("设置 AI 接口"), self._tray_open_settings),
            pystray.MenuItem(self._t("通用设置"), self._tray_open_companion_settings),
            pystray.MenuItem(self._t("调整桌宠大小"), self._tray_open_size_settings),
            pystray.Menu.SEPARATOR,
            pystray.MenuItem(self._t("退出猫咪"), self._tray_quit),
        ])
        return pystray.Menu(*items)

    def _start_tray_icon(self) -> None:
        import pystray
        from PIL import Image

        with Image.open(resource_path("pet_icon.png")) as icon_image:
            self._tray_image = icon_image.convert("RGBA")

        self.tray_icon = pystray.Icon(
            "pawpal_desktop_cat",
            self._tray_image,
            self._t(APP_NAME),
            menu=self._tray_menu(),
        )
        self.tray_thread = threading.Thread(
            target=self.tray_icon.run, name="pawpal-tray", daemon=True,
        )
        self.tray_thread.start()
        self.temperature_tray_icon = pystray.Icon(
            "pawpal_temperature", temperature_icon(None, None),
            temperature_tooltip(self.metrics_snapshot, self.language), menu=self._tray_menu())
        numeric = self.temperature_tray_icon
        def temperature_setup(icon):
            icon.visible = bool(self.settings.get("show_temperature", True))
        self.temperature_tray_thread = threading.Thread(
            target=lambda: numeric.run(setup=temperature_setup),
            name="pawpal-temperature-tray", daemon=True)
        self.temperature_tray_thread.start()

    def _update_tray_metrics(self) -> None:
        """Update existing native tray surfaces at most once per changed reading."""
        main = getattr(self, "tray_icon", None)
        numeric = getattr(self, "temperature_tray_icon", None)
        if main is None and numeric is None:
            return
        enabled = bool(self.settings.get("show_temperature", True))
        snapshot = getattr(self, "metrics_snapshot", None)
        lines = tuple(temperature_lines(snapshot, self.language)) if enabled else ()
        signature = (enabled, self.language, lines, representative_values(snapshot))
        if signature == getattr(self, "_tray_metrics_signature", None):
            return
        try:
            title = temperature_tooltip(snapshot, self.language, enabled)
            if main is not None:
                main.title = title
                main.menu = self._tray_menu()
            if numeric is not None:
                numeric.title = title
                numeric.icon = temperature_icon(*representative_values(snapshot))
                numeric.menu = self._tray_menu()
                # Visibility is safe only after the Win32 message window exists.
                if getattr(numeric, "_running", False):
                    numeric.visible = enabled
            self._tray_metrics_signature = signature
        except (OSError, RuntimeError, ValueError):
            # A disappearing Explorer tray cannot interrupt the pet's animation.
            self._tray_metrics_signature = None

    def _schedule_ui(self, callback: object) -> None:
        try:
            self.root.after(0, callback)
        except (tk.TclError, RuntimeError):
            pass

    def _tray_open_chat(self, _icon: object, _item: object) -> None:
        self._schedule_ui(self.open_chat)

    def _tray_open_settings(self, _icon: object, _item: object) -> None:
        self._schedule_ui(self.open_settings)

    def _tray_open_companion_settings(self, _icon: object, _item: object) -> None:
        self._schedule_ui(self.open_companion_settings)

    def _tray_open_size_settings(self, _icon: object, _item: object) -> None:
        self._schedule_ui(self.open_size_settings)

    def _tray_quit(self, _icon: object, _item: object) -> None:
        self._schedule_ui(self._quit_app)

    def _quit_app(self) -> None:
        self._close_context_menu()
        self.speech_input.stop()
        self.metrics.stop()
        self.animation.close()
        if getattr(self, "temperature_tray_icon", None) is not None:
            try:
                self.temperature_tray_icon.stop()
            except Exception:
                pass
            self.temperature_tray_icon = None
        if self.tray_icon is not None:
            try:
                self.tray_icon.stop()
            except Exception:
                pass
            self.tray_icon = None
        for thread in (getattr(self, "tray_thread", None), getattr(self, "temperature_tray_thread", None)):
            if thread is not None and thread is not threading.current_thread():
                thread.join(timeout=2)
        self._release_layered_surface()
        try:
            self.root.destroy()
        except tk.TclError:
            pass

    @staticmethod
    def _settings_path() -> Path:
        override = os.environ.get("PAWPAL_DATA_DIR")
        if override:
            return Path(override) / "settings.json"
        base = Path(os.environ.get("APPDATA", str(Path.home())))
        return base / "TuanziDesktopPet" / "settings.json"

    @staticmethod
    def _normalize_pet_scale(value: object) -> float:
        try:
            scale = float(value)
        except (TypeError, ValueError):
            return DEFAULT_PET_SCALE
        if not math.isfinite(scale):
            return DEFAULT_PET_SCALE
        return min(MAX_PET_SCALE, max(MIN_PET_SCALE, scale))

    def _load_settings(self) -> dict[str, object]:
        try:
            data = json.loads(self.settings_path.read_text(encoding="utf-8"))
            if not isinstance(data, dict):
                data = {}
        except (OSError, ValueError):
            data = {}
        upgraded = data.get("cat_settings_version", 0) == 3
        endpoint = str(data.get("endpoint") or OPENAI_ENDPOINT)
        model = str(data.get("model") or OPENAI_MODEL)
        model = migrate_model_name(endpoint, model)
        if provider_name_for_endpoint(endpoint) == "OpenAI" and model == "gpt-5.6-sol":
            model = OPENAI_MODEL
        return {
            "endpoint": endpoint, "model": model,
            "pet_scale": self._normalize_pet_scale(data.get("pet_scale")),
            "weather_city": str(data.get("weather_city") or "").strip(),
            "weather_location": data.get("weather_location") if isinstance(data.get("weather_location"), dict) else None,
            "language": normalize_language(data.get("language")),
            "cat_profile_version": 1, "cat_settings_version": 3,
            "cpu_reactions": bool(data.get("cpu_reactions", False)) if data.get("cat_profile_version") else False,
            "night_reactions": bool(data.get("night_reactions", False)) if data.get("cat_profile_version") else False,
            "show_status_bubble": bool(data.get("show_status_bubble", True)) if upgraded else True,
            "show_fps": bool(data.get("show_fps", True)),
            "show_temperature": bool(data.get("show_temperature", True)),
            "start_with_windows": bool(data.get("start_with_windows", True)),
            "ai_profiles": {str(name): {"endpoint": str(value.get("endpoint", "")),
                                       "model": migrate_model_name(str(value.get("endpoint", "")), str(value.get("model", "")))}
                            for name, value in data.get("ai_profiles", {}).items() if isinstance(value, dict)}
                           if isinstance(data.get("ai_profiles"), dict) else {},
        }

    def _save_settings_file(self) -> None:
        self.settings_path.parent.mkdir(parents=True, exist_ok=True)
        temporary = None
        try:
            with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", prefix="settings-", suffix=".tmp",
                                             dir=self.settings_path.parent, delete=False) as output:
                temporary = Path(output.name)
                json.dump(self.settings, output, ensure_ascii=False, indent=2)
            os.replace(temporary, self.settings_path)
            temporary = None
        finally:
            if temporary is not None:
                temporary.unlink(missing_ok=True)

    def _apply_pet_scale(self, value: object) -> None:
        scale = self._normalize_pet_scale(value)
        old_width, old_height = self.window_width, self.window_height
        self.pet_scale = scale
        self.settings["pet_scale"] = scale
        self.window_width = max(1, round(self.width * scale))
        self.window_height = max(1, round(self.height * scale))
        if (self.window_width, self.window_height) == (old_width, old_height):
            return

        screen_w, screen_h = self.root.winfo_screenwidth(), self.root.winfo_screenheight()
        center_x = self.root.winfo_x() + old_width / 2
        center_y = self.root.winfo_y() + old_height / 2
        x = min(max(8, round(center_x - self.window_width / 2)),
                max(8, screen_w - self.window_width - 8))
        y = min(max(8, round(center_y - self.window_height / 2)),
                max(8, screen_h - self.window_height - 8))
        self.root.geometry(f"{self.window_width}x{self.window_height}+{x}+{y}")
        self.root.update_idletasks()
        self._create_surface_bitmap(self.window_width, self.window_height)

    def _animate(self) -> None:
        frame_started = time.perf_counter()
        now = time.monotonic()
        self._track_hover_pointer()
        self._sample_cpu(now)
        self._maybe_refresh_weather(now)
        self.behavior.update(now=now)
        if self.motion.is_idle and self.behavior.state == "idle" and self.pending_action:
            requested, self.pending_action = self.pending_action, None
            self._on_action(self.behavior.manual_action(requested, now=now))
        action = (self.behavior.autonomous_action(now=now)
                  if self.motion.is_idle and not self.pending_action else None)
        if action is not None:
            self._on_action(action)
        context_event = self.context_reactions.poll(
            now=now,
            local_time=datetime.now(),
            cpu_percent=self.cpu_percent,
            weather=self.weather_info,
            is_idle=self.behavior.state == "idle" and self.motion.is_idle,
            cpu_enabled=bool(self.settings.get("cpu_reactions", False)),
            night_enabled=bool(self.settings.get("night_reactions", False)),
        )
        if context_event is not None:
            action = self.behavior.context_action(
                context_event.state, context_event.dialogue, now=now,
                duration=context_event.duration,
            )
            if action is not None:
                self._on_action(action)
        self.motion_pose = self.motion.update(self.behavior.state, now,
                                              started_at=self.behavior.state_started)
        self._move_autonomously(now)
        self.bubble.tick(now=now)
        self._update_pet_context_bubble(now)
        if now >= self.next_status_refresh:
            self.next_status_refresh = now + 5.0
            self._update_chat_context_status()
        self._draw()
        try:
            spent = time.perf_counter() - frame_started
            self.root.after(max(1, round((FRAME_PERIOD - spent) * 1000)), self._animate)
        except tk.TclError:
            return

    def _sample_cpu(self, now: float) -> None:
        if now < self.next_cpu_sample:
            return
        self.next_cpu_sample = now + 1.0
        self.metrics_snapshot = self.metrics.snapshot()
        updated_at = getattr(self.metrics_snapshot, "updated_at", 0.0)
        self.cpu_percent = (None if updated_at and now - updated_at > 3.5
                            else self.metrics_snapshot.cpu_percent)
        self._update_tray_metrics()

    def _maybe_refresh_weather(self, now: float) -> None:
        city = str(self.settings.get("weather_city", "")).strip()
        if not city:
            if self.weather_info is not None:
                self.weather_info = None
                self.context_reactions.reset_weather()
            return
        if self.weather_request_pending or now < self.next_weather_refresh:
            return
        self.weather_request_pending = True
        self.next_weather_refresh = now + 3600.0
        threading.Thread(
            target=self._request_weather, args=(city,), name="xiongda-weather", daemon=True,
        ).start()

    def _request_weather(self, city: str) -> None:
        stored = self.settings.get("weather_location")
        stored = dict(stored) if isinstance(stored, dict) else None
        signature = json.dumps(stored, sort_keys=True, ensure_ascii=False)
        try:
            language = getattr(self, "language", "zh-CN")
            reading = (fetch_weather_at(WeatherLocation.from_dict(stored), language=language)
                       if stored else fetch_weather(city, language=language))
            error = None
        except (OSError, ValueError, TimeoutError) as exc:
            reading, error = None, str(exc)
        self._schedule_ui(lambda: self._finish_weather(city, reading, error, location_signature=signature))

    def _finish_weather(self, city: str, reading: WeatherReading | None, error: str | None,
                        *, location_signature: str | None = None) -> None:
        self.weather_request_pending = False
        location_changed = (location_signature is not None and location_signature !=
                            json.dumps(self.settings.get("weather_location"), sort_keys=True, ensure_ascii=False))
        if city != str(self.settings.get("weather_city", "")).strip() or location_changed:
            self.next_weather_refresh = 0.0
            return
        self.weather_error = error
        if reading is not None:
            self.weather_info = reading
            self._weather_failures = 0
            delay = 900.0
        else:
            self._weather_failures = min(5, getattr(self, "_weather_failures", 0) + 1)
            delay = min(900.0, 30.0 * 2 ** self._weather_failures)
        self.next_weather_refresh = time.monotonic() + delay
        self._update_chat_context_status(force=True)

    def _context_status_text(self) -> str:
        if self.weather_info is not None:
            reading = self.weather_info
            weather = f"{reading.city} {weather_condition(reading.code, getattr(self, 'language', 'zh-CN'))} {reading.temperature:.0f}°C"
            if self.weather_error:
                weather += " · " + self._t("上次数据")
        elif self.settings.get("weather_city"):
            weather = self._t("天气暂不可用") if self.weather_error else self._t("天气更新中")
        else:
            weather = self._t("天气未设置")
        cpu = "--" if self.cpu_percent is None else f"{self.cpu_percent:.0f}%"
        lines = [f"{datetime.now():%H:%M} · {weather}", self._t("系统 CPU") + " " + cpu]
        snapshot = getattr(self, "metrics_snapshot", None)
        if self.settings.get("show_fps", True) and snapshot is not None:
            target = (snapshot.fps_target or "").removesuffix(".exe")[:22]
            if snapshot.fps is not None:
                fps_text = f"FPS {snapshot.fps:.1f}" + (f" · {target}" if target else "")
            else:
                from pet_i18n import tr
                status = "starting" if snapshot.fps_status == "ready" else snapshot.fps_status
                fps_text = "FPS -- · " + tr("fps." + status, getattr(self, "language", "zh-CN"))
            lines.append(fps_text)
        return "\n".join(lines)

    def _update_pet_context_bubble(self, now: float) -> None:
        if not getattr(self, "settings", {}).get("show_status_bubble", True):
            self._hide_context_bubble()
            return
        pose = self.motion_pose
        available = (pose.state in {"idle", "sleep"} and pose.phase == "loop"
                     and self.chat_window is None and self.context_window is None)
        if available and self.context_bubble_active and self.bubble.text:
            self.bubble.text = self._context_status_text()
            self.bubble.expires_at = now + 86400
            self.bubble.target_alpha = 1.0
        elif available and not self.bubble.text and self.bubble.alpha <= 0.001:
            self.bubble.show(self._context_status_text(), 86400, now=now)
            self.context_bubble_active = True
        elif not available and self.context_bubble_active:
            self.bubble.hide()
            self.context_bubble_active = False

    def _hide_context_bubble(self) -> None:
        if self.context_bubble_active:
            self.bubble.hide()
            self.context_bubble_active = False

    def _update_chat_context_status(self, *, force: bool = False) -> None:
        label = self.chat_context_status
        if label is None:
            return
        try:
            if label.winfo_exists():
                label.configure(text=self._t(self._context_status_text()))
        except tk.TclError:
            if force:
                self.chat_context_status = None

    def _move_autonomously(self, now: float) -> None:
        elapsed = max(0.0, min(0.12, now - self.walk_step_time))
        self.walk_step_time = now
        weight = self.motion_pose.movement_weight
        if weight <= 0 or elapsed <= 0 or self.drag_start is not None:
            self._walk_position_x = None
            return
        screen_w = self.root.winfo_screenwidth()
        left_limit, right_limit = 8, max(8, screen_w - self.window_width - 8)
        current_x = getattr(self, "_walk_position_x", None)
        if current_x is None:
            current_x = float(self.root.winfo_x())
        next_x = current_x + self.walk_direction * 30 * getattr(self, "pet_scale", 1.0) * elapsed * weight
        if next_x <= left_limit:
            next_x = left_limit
            if self.behavior.state == "walk":
                self.behavior.user_wave(now=now)
        elif next_x >= right_limit:
            next_x = right_limit
            if self.behavior.state == "walk":
                self.behavior.user_wave(now=now)
        self._walk_position_x = next_x
        self.root.geometry(f"+{round(next_x)}+{self.root.winfo_y()}")
        # Apply Tk's deferred geometry before presenting the matching pose.
        self.root.update_idletasks()

    def _on_action(self, action: PetAction) -> None:
        self._hide_context_bubble()
        self.hover_bubble = False
        frames = self.animation.frames_for(action.state)
        if action.state in {"click", "eat", "groom", "sleep"}:
            self.sprite_frame_index = 0
        else:
            self.sprite_frame_index = random.randrange(max(1, len(frames)))
        now = time.monotonic()
        selected = self.dialogue.choose(action.dialogue, now=now)
        if selected is not None:
            line, duration = selected
            self.bubble.show(self._t(line), duration, now=now)

    def _set_hover(self, inside: bool) -> None:
        if inside == self.hover_inside:
            return
        self.hover_inside = inside
        now = time.monotonic()
        if inside:
            self._hide_context_bubble()
            if now - self.last_hover_line >= 90.0 and self.behavior.state == "idle" and random.random() < 0.22:
                selected = self.dialogue.choose("interaction", now=now)
                if selected is not None:
                    line, duration = selected
                    self.bubble.show(self._t(line), min(duration, 2.4), now=now)
                    self.hover_bubble = True
                    self.last_hover_line = now
        elif self.hover_bubble:
            self.bubble.hide()
            self.hover_bubble = False

    def _hover_over_pet(self, x: int, y: int) -> bool:
        x = round(x / self.pet_scale)
        y = round(y / self.pet_scale)
        left, top, right, bottom = self.current_sprite_bbox
        return left <= x <= right and top <= y <= bottom

    def _on_hover_motion(self, event: tk.Event) -> None:
        self._set_hover(self._hover_over_pet(event.x, event.y))

    def _track_hover_pointer(self) -> None:
        if self.user32 is None:
            return
        point = wintypes.POINT()
        if not self.user32.GetCursorPos(ctypes.byref(point)):
            return
        x = point.x - self.root.winfo_rootx()
        y = point.y - self.root.winfo_rooty()
        self._set_hover(self._hover_over_pet(x, y))

    def _hide_hover_bubble(self, _event: tk.Event | None = None) -> None:
        self._set_hover(False)

    def _start_drag(self, event: tk.Event) -> None:
        self._hide_context_bubble()
        self.did_drag = False
        self.behavior.mark_activity()
        self.drag_start = (event.x_root, event.y_root, self.root.winfo_x(), self.root.winfo_y())

    def _drag(self, event: tk.Event) -> None:
        if self.drag_start is None:
            return
        sx, sy, wx, wy = self.drag_start
        dx, dy = event.x_root - sx, event.y_root - sy
        if not self.did_drag and abs(dx) + abs(dy) > 5:
            self.did_drag = True
        if self.did_drag:
            screen_w, screen_h = self.root.winfo_screenwidth(), self.root.winfo_screenheight()
            new_x = min(max(8, wx + dx), max(8, screen_w - self.window_width - 8))
            new_y = min(max(8, wy + dy), max(8, screen_h - self.window_height - 8))
            self.root.geometry(f"+{new_x}+{new_y}")

    def _finish_drag(self, _event: tk.Event) -> None:
        if not self.did_drag:
            action = self.behavior.click()
            if action is not None:
                self._on_action(action)
        self.drag_start = None

    def _choose_action(self, state: str) -> None:
        self._hide_context_bubble()
        if state == "idle":
            self.pending_action = None
            self.behavior.manual_action("idle")
        elif self.motion.is_idle and self.behavior.state == "idle":
            self._on_action(self.behavior.manual_action(state))
        elif state != self.behavior.state:
            self.pending_action = state
            from pet_i18n import tr
            action_name = {
                "click": "点击互动", "eat": "吃猫粮", "groom": "洗脸", "sleep": "睡觉",
            }.get(state, state)
            self.bubble.show(tr("action_queued", self.language, action=self._t(action_name)), 2.0)
            # An infinite sleep must wake before the queued action can run.
            if self.behavior.state == "sleep":
                self.behavior.manual_action("idle")

    def _show_menu(self, event: tk.Event) -> None:
        self._hide_context_bubble()
        if self.context_window is not None and self.context_window.winfo_exists():
            self._close_context_menu()
        popup = tk.Toplevel(self.root)
        self.context_window = popup
        popup.overrideredirect(True)
        popup.configure(bg=KEY_COLOR)
        popup.attributes("-topmost", True)
        try:
            popup.wm_attributes("-transparentcolor", KEY_COLOR)
        except tk.TclError:
            popup.attributes("-alpha", 0.98)
        menu_w, menu_h = 300, 482
        x = min(event.x_root, popup.winfo_screenwidth() - menu_w - 8)
        y = min(event.y_root, popup.winfo_screenheight() - menu_h - 8)
        popup.geometry(f"{menu_w}x{menu_h}+{max(0, x)}+{max(0, y)}")
        canvas = tk.Canvas(popup, width=menu_w, height=menu_h, bg=KEY_COLOR,
                           highlightthickness=0, bd=0)
        canvas.pack(fill="both", expand=True)

        rounded_polygon(canvas, 4, 4, menu_w - 4, menu_h - 4, 18,
                        fill="#fbf9fe", outline="#e9e3f0", width=1)
        actions = [
            ("chat", "和猫咪聊天", self.open_chat),
            ("settings", "设置 AI 接口", self.open_settings),
            ("click", "点击互动", lambda: self._choose_action("click")),
            ("eat", "吃猫粮", lambda: self._choose_action("eat")),
            ("groom", "洗脸", lambda: self._choose_action("groom")),
            ("sleep", "睡觉", lambda: self._choose_action("sleep")),
            ("wake", "唤醒", lambda: self._choose_action("idle")),
            ("companion", "通用设置", self.open_companion_settings),
            ("size", "调整桌宠大小", self.open_size_settings),
            ("quit", "退出猫咪", self._quit_app),
        ]
        for index, (tag, label, action) in enumerate(actions):
            top = 8 + index * 47
            if tag == "quit":
                canvas.create_line(18, top - 5, menu_w - 18, top - 5, fill="#eee8f2")
            row_tag = f"row_{tag}"
            bg_tag = f"bg_{tag}"
            rounded_polygon(canvas, 10, top, menu_w - 10, top + 40, 12,
                            fill="#fbf9fe", outline="", tags=(bg_tag,))
            canvas.create_text(28, top + 20, text=self._t(label), anchor="w", fill=INK,
                               font=("Microsoft YaHei UI", 10), tags=(row_tag,))
            for item_tag in (bg_tag, row_tag):
                canvas.tag_bind(item_tag, "<Enter>",
                                lambda _e, t=bg_tag: canvas.itemconfigure(t, fill="#f0eafb"))
                canvas.tag_bind(item_tag, "<Leave>",
                                lambda _e, t=bg_tag: canvas.itemconfigure(t, fill="#fbf9fe"))
                canvas.tag_bind(item_tag, "<Button-1>",
                                lambda _e, a=action: (self._close_context_menu(), a()))
        popup.bind("<Escape>", lambda _event: self._close_context_menu())
        popup.bind("<FocusOut>", lambda _event: popup.after_idle(
            lambda: self._close_context_menu() if self.context_window is popup else None
        ))
        popup.focus_force()
        self.context_last_buttons = self._mouse_buttons_down()
        self._poll_context_outside_click(popup)

    def _mouse_buttons_down(self) -> tuple[bool, bool]:
        if self.user32 is None:
            return False, False
        left = bool(self.user32.GetAsyncKeyState(0x01) & 0x8000)
        right = bool(self.user32.GetAsyncKeyState(0x02) & 0x8000)
        return left, right

    def _poll_context_outside_click(self, popup: tk.Toplevel) -> None:
        self.context_click_poll_job = None
        if self.context_window is not popup or not popup.winfo_exists() or self.user32 is None:
            return

        buttons = self._mouse_buttons_down()
        clicked = any(current and not previous
                      for current, previous in zip(buttons, self.context_last_buttons))
        self.context_last_buttons = buttons
        if clicked:
            point = wintypes.POINT()
            if self.user32.GetCursorPos(ctypes.byref(point)):
                x, y = popup.winfo_rootx(), popup.winfo_rooty()
                if not (x <= point.x < x + popup.winfo_width() and
                        y <= point.y < y + popup.winfo_height()):
                    self._close_context_menu()
                    return

        self.context_click_poll_job = self.root.after(
            15, lambda: self._poll_context_outside_click(popup)
        )

    def _close_context_menu(self) -> None:
        if self.context_click_poll_job is not None:
            try:
                self.root.after_cancel(self.context_click_poll_job)
            except tk.TclError:
                pass
            self.context_click_poll_job = None
        popup = self.context_window
        self.context_window = None
        if popup is not None and popup.winfo_exists():
            popup.destroy()

    def _start_chat_drag(self, event: tk.Event) -> None:
        if self.chat_window is not None:
            self.chat_drag_origin = (
                event.x_root, event.y_root,
                self.chat_window.winfo_x(), self.chat_window.winfo_y(),
            )

    def _drag_chat(self, event: tk.Event) -> None:
        if self.chat_drag_origin is None or self.chat_window is None:
            return
        sx, sy, wx, wy = self.chat_drag_origin
        dx, dy = event.x_root - sx, event.y_root - sy
        self.chat_window.geometry(f"+{wx + dx}+{wy + dy}")

    def _open_chat_window(self) -> tk.Toplevel:
        if self.chat_window is not None and self.chat_window.winfo_exists():
            self.chat_window.deiconify()
            self.chat_window.lift()
            return self.chat_window

        win = tk.Toplevel(self.root)
        win.title(self._t("和猫咪聊天"))
        win.overrideredirect(True)
        popup_x = max(8, self.root.winfo_x() - 432)
        popup_y = min(self.root.winfo_y(), win.winfo_screenheight() - 632)
        win.geometry(f"420x620+{popup_x}+{max(8, popup_y)}")
        win.configure(bg=KEY_COLOR)
        win.attributes("-topmost", True)
        try:
            win.wm_attributes("-transparentcolor", KEY_COLOR)
        except tk.TclError:
            win.attributes("-alpha", 0.98)
        win.protocol("WM_DELETE_WINDOW", self._close_chat)
        self.chat_window = win

        style = ttk.Style(win)
        try:
            style.theme_use("clam")
        except tk.TclError:
            pass
        style.configure("Pet.TButton", font=("Microsoft YaHei UI", 9), padding=(9, 5),
                        background="#eee7ff", foreground=INK, borderwidth=0)
        style.map("Pet.TButton", background=[("active", "#ddd1fa")])
        style.configure("Send.TButton", font=("Microsoft YaHei UI", 10, "bold"), padding=(14, 8),
                        background=PURPLE, foreground="white", borderwidth=0)
        style.map("Send.TButton", background=[("active", "#665293")])

        canvas = tk.Canvas(win, width=420, height=620, bg=KEY_COLOR,
                           highlightthickness=0, bd=0)
        canvas.pack(fill="both", expand=True)
        rounded_polygon(canvas, 4, 4, 416, 616, 28, fill="#f8f6fb", outline="#e9e4ef", width=1)
        rounded_polygon(canvas, 4, 4, 416, 78, 26, fill="#f8f6fb", outline="",
                        tags=("header_drag",))
        canvas.create_text(26, 34, text=self._t("✦"), fill=PURPLE,
                           font=("Segoe UI Symbol", 19), anchor="w", tags=("title_drag",))
        canvas.create_text(55, 34, text=self._t("猫咪"), fill=INK, anchor="w",
                           font=("Microsoft YaHei UI", 15, "bold"), tags=("title_drag",))
        canvas.create_text(56, 60, text=self._t("DESKTOP COMPANION"), fill="#9b91a4", anchor="w",
                           font=("Segoe UI", 8), tags=("title_drag",))
        rounded_polygon(canvas, 299, 18, 364, 50, 15, fill="#eee8fa", outline="", tags=("settings",))
        canvas.create_text(331, 34, text=self._t("设置"), fill="#67588a",
                           font=("Microsoft YaHei UI", 9), tags=("settings",))
        canvas.create_text(390, 34, text=self._t("×"), fill="#82768d",
                           font=("Segoe UI", 18), tags=("close",))
        canvas.tag_bind("settings", "<Button-1>", lambda _event: self.open_settings())
        canvas.tag_bind("close", "<Button-1>", lambda _event: self._close_chat())
        canvas.tag_bind("title_drag", "<ButtonPress-1>", self._start_chat_drag)
        canvas.tag_bind("title_drag", "<B1-Motion>", self._drag_chat)
        canvas.tag_bind("header_drag", "<ButtonPress-1>", self._start_chat_drag)
        canvas.tag_bind("header_drag", "<B1-Motion>", self._drag_chat)

        rounded_polygon(canvas, 18, 86, 402, 487, 22, fill="#fffdfd", outline="#ede8f3", width=1)
        self.chat_context_status = ttk.Label(
            win, text=self._t(self._context_status_text()), background="#fffdfd",
            foreground="#71658a", font=("Microsoft YaHei UI", 8),
            wraplength=360, anchor="w",
        )
        canvas.create_window(31, 92, anchor="nw", window=self.chat_context_status,
                             width=365, height=58)
        self.chat_status = ttk.Label(win, text=self._t(self._mode_label()), background="#fffdfd",
                                     foreground="#968b9e", font=("Microsoft YaHei UI", 8),
                                     wraplength=350, anchor="w")
        canvas.create_window(31, 154, anchor="nw", window=self.chat_status,
                             width=365, height=20)
        self.chat_text = tk.Text(
            win, wrap="word", state="disabled", relief="flat", bd=0,
            bg="#fffdfd", fg=INK, padx=12, pady=10,
            font=("Microsoft YaHei UI", 10), spacing1=3, spacing3=7,
            highlightthickness=0,
        )
        canvas.create_window(28, 180, anchor="nw", window=self.chat_text, width=364, height=287)
        self.chat_text.tag_configure("pet", foreground="#65518f", lmargin1=7, lmargin2=7, rmargin=28)
        self.chat_text.tag_configure("user", foreground="#3b5860", lmargin1=38, lmargin2=38, rmargin=7)
        self.chat_text.tag_configure("note", foreground="#91869a", justify="center",
                                     font=("Microsoft YaHei UI", 8), spacing1=8, spacing3=9)

        quick_actions = (("打招呼", "你好呀"), ("给我鼓励", "今天有点累，鼓励我一下"),
                         ("讲个笑话", "给我讲个简短的笑话"))
        for index, (label, prompt) in enumerate(quick_actions):
            left = 20 + index * 128
            tag = f"quick_{index}"
            rounded_polygon(canvas, left, 500, left + 118, 534, 15,
                            fill="#eee8fa", outline="", tags=(tag,))
            canvas.create_text(left + 59, 517, text=self._t(label), fill="#67588a",
                               font=("Microsoft YaHei UI", 8), width=110, justify="center", tags=(tag,))
            canvas.tag_bind(tag, "<Button-1>", lambda _event, p=prompt: self._send_text(self._t(p)))

        rounded_polygon(canvas, 18, 546, 402, 602, 20, fill="#f5f2f8", outline="#eee9f2", width=1)
        self.chat_input = tk.Text(win, height=2, wrap="word", relief="flat", bd=0,
                                  bg="#f5f2f8", fg=INK, padx=5, pady=6,
                                  font=("Microsoft YaHei UI", 10), highlightthickness=0)
        canvas.create_window(31, 556, anchor="nw", window=self.chat_input, width=250, height=38)
        self.chat_input.insert("1.0", self._t("说点什么…"))
        self.chat_input.configure(fg="#a69aaa")
        self.chat_input.bind("<FocusIn>", self._clear_placeholder)
        self.chat_input.bind("<Return>", self._send_on_enter)
        rounded_polygon(canvas, 288, 553, 328, 595, 17, fill="#eee8fa", outline="",
                        tags=("mic_bg", "mic"))
        canvas.create_text(308, 574, text=self._t("\uE720"), fill="#67588a",
                           font=("Segoe MDL2 Assets", 14), tags=("mic_icon", "mic"))
        self.mic_canvas = canvas
        canvas.tag_bind("mic", "<Button-1>", lambda _event: self._toggle_speech_input())
        rounded_polygon(canvas, 337, 553, 391, 595, 18, fill=PURPLE, outline="", tags=("send",))
        canvas.create_text(364, 574, text=self._t("发送"), fill="#ffffff",
                           font=("Microsoft YaHei UI", 9, "bold"), tags=("send",))
        canvas.tag_bind("send", "<Button-1>", lambda _event: self._send_from_input())
        win.bind("<Escape>", lambda _event: self._close_chat())

        self._append_message("pet", self._t(self._initial_greeting()))
        return win

    def open_chat(self) -> None:
        self._hide_context_bubble()
        self._open_chat_window()
        if self.chat_input is not None:
            self.chat_input.focus_set()

    def _close_chat(self) -> None:
        if self.mic_listening:
            self.speech_input.stop()
            self.mic_listening = False
        if self.chat_window is not None:
            self.chat_window.destroy()
        self.chat_window = None
        self.chat_text = None
        self.chat_input = None
        self.chat_status = None
        self.chat_context_status = None
        self.mic_canvas = None
        self.chat_drag_origin = None

    def _toggle_speech_input(self) -> None:
        if self.mic_listening:
            if self.chat_status is not None:
                self.chat_status.configure(text=self._t("还在听；说完停顿一下，识别后会填进输入框。"))
            return
        if self.chat_input is None:
            return
        self.mic_listening = True
        if self.mic_canvas is not None:
            self.mic_canvas.itemconfigure("mic_bg", fill="#f7e4e5")
            self.mic_canvas.itemconfigure("mic_icon", fill="#b64d58")
        if self.chat_status is not None:
            self.chat_status.configure(text=self._t("正在听你说话；说完停顿一下，识别后会填进输入框。"))

        def completed(text: str | None, error: str | None) -> None:
            try:
                self.root.after(0, lambda: self._finish_speech_input(text, error))
            except tk.TclError:
                return

        if not self.speech_input.start(completed):
            self._finish_speech_input(None, "语音识别正在运行，请稍等。")

    def _finish_speech_input(self, text: str | None, error: str | None,
                             *, cancelled: bool = False) -> None:
        self.mic_listening = False
        if self.mic_canvas is not None:
            try:
                self.mic_canvas.itemconfigure("mic_bg", fill="#eee8fa")
                self.mic_canvas.itemconfigure("mic_icon", fill="#67588a")
            except tk.TclError:
                self.mic_canvas = None
        if cancelled or self.chat_input is None:
            if self.chat_status is not None:
                try:
                    self.chat_status.configure(text=self._t(self._mode_label()))
                except tk.TclError:
                    pass
            return
        if text:
            if self.chat_input.get("1.0", "end-1c") == self._t("说点什么…"):
                self.chat_input.delete("1.0", "end")
                self.chat_input.configure(fg=INK)
            existing = self.chat_input.get("1.0", "end-1c").strip()
            self.chat_input.insert("end" if existing else "1.0",
                                   (" " if existing else "") + text)
            self.chat_input.focus_set()
            if self.chat_status is not None:
                self.chat_status.configure(text=self._t("识别完成，检查输入框里的文字后再点发送。"))
                try:
                    self.root.after(5000, self._restore_speech_status)
                except tk.TclError:
                    pass
        elif error:
            self._append_message("pet", self._t(error))
            if self.chat_status is not None:
                self.chat_status.configure(text=self._t(error))

    def _restore_speech_status(self) -> None:
        if self.chat_status is None:
            return
        try:
            if self.chat_status.winfo_exists() and not self.waiting and not self.mic_listening:
                self.chat_status.configure(text=self._t(self._mode_label()))
        except tk.TclError:
            pass

    def _clear_placeholder(self, _event: tk.Event) -> None:
        if self.chat_input is not None and self.chat_input.get("1.0", "end-1c") == self._t("说点什么…"):
            self.chat_input.delete("1.0", "end")
            self.chat_input.configure(fg=INK)

    def _send_on_enter(self, event: tk.Event) -> str | None:
        if event.state & 0x0001:  # Shift+Enter keeps a line break.
            return None
        self._send_from_input()
        return "break"

    def _send_from_input(self) -> None:
        if self.chat_input is None:
            return
        text = self.chat_input.get("1.0", "end-1c").strip()
        if not text or text == self._t("说点什么…"):
            return
        self.chat_input.delete("1.0", "end")
        self.chat_input.configure(fg=INK)
        self._send_text(text)

    def _append_message(self, speaker: str, text: str) -> None:
        if self.chat_text is None:
            return
        self.chat_text.configure(state="normal")
        tag = "user" if speaker == "user" else "pet"
        prefix = self._t("你") + "  " if speaker == "user" else self._t("猫咪") + "  "
        self.chat_text.insert("end", prefix + text.strip() + "\n\n", tag)
        self.chat_text.configure(state="disabled")
        self.chat_text.see("end")

    def _initial_greeting(self) -> str:
        endpoint = self.settings.get("endpoint", "").strip()
        model = self.settings.get("model", "").strip()
        if not endpoint or not model:
            return "喵。我在这里陪你。需要聊天时，随时叫我。"
        provider = self._t(provider_name_for_endpoint(endpoint))
        if provider in ("OpenAI", "DeepSeek") and not self.api_key:
            return f"喵。想聊天的话，可以先在设置里填入 {provider} API Key。"
        return "喵。今天过得怎么样？"

    def _mode_label(self) -> str:
        model = self.settings.get("model", "").strip()
        endpoint = self.settings.get("endpoint", "").strip()
        provider = self._t(provider_name_for_endpoint(endpoint))
        if model and endpoint and provider in ("OpenAI", "DeepSeek") and not self.api_key:
            return provider + " · " + self._t("请设置 API Key")
        if model and endpoint:
            state = "连接已验证" if getattr(self, "connection_verified", False) else "尚未测试连接"
            return provider + " · " + self._t(state)
        return self._t("本地轻量陪聊 · 尚未连接 AI 接口")

    def _send_text(self, text: str) -> None:
        self.open_chat()
        self.behavior.mark_activity()
        if self.waiting:
            self._append_message("pet", self._t("我还在想上一条呢，等我一下下～"))
            return
        self._append_message("user", text)
        self.history.append({"role": "user", "content": text})
        if self._local_data_query(text):
            return
        endpoint = self.settings.get("endpoint", "").strip()
        model = self.settings.get("model", "").strip()
        if not endpoint or not model:
            self.root.after(450, lambda: self._finish_local_reply(self._offline_reply(text)))
            return

        provider = provider_name_for_endpoint(endpoint)
        if provider in ("OpenAI", "DeepSeek") and not self.api_key:
            if provider == "DeepSeek":
                prompt = self._t("还没有 DeepSeek API 密钥，请在设置 AI 接口中填写密钥。")
            else:
                prompt = self._t("请先在 AI 接口设置中填写 API 密钥。")
            self._append_message("pet", prompt)
            return
        key = self.api_key
        self.waiting = True
        if self.chat_status is not None:
            self.chat_status.configure(text=self._t("正在想一想…"))
        language_instruction = {
            "zh-CN": "默认用简体中文回复；如果用户明确要求其他语言，请按其要求。",
            "en": "Reply in English by default, unless the user explicitly requests another language.",
            "ms": "Balas dalam Bahasa Melayu secara lalai, kecuali pengguna meminta bahasa lain.",
        }[getattr(self, "language", "zh-CN")]
        messages = [{"role": "system", "content": SYSTEM_PROMPT + language_instruction}] + self.history[-12:]
        threading.Thread(target=self._request_ai, args=(endpoint, model, key, messages), daemon=True).start()

    def _local_data_query(self, text: str) -> bool:
        query = text.lower().strip()
        temperature_query = any(word in query for word in ("gpu", "温度", "temperature", "suhu"))
        weather_query = any(word in query for word in ("天气", "气温", "weather", "cuaca"))
        metrics = (any(word in query for word in ("cpu", "gpu", "fps", "帧率", "系统状态"))
                   or (temperature_query and not weather_query))
        if metrics and (query in {"cpu", "gpu", "fps", "系统状态", "温度", "temperature", "suhu"} or any(
                word in query for word in ("多少", "占用", "当前", "使用率", "温度", "temperature", "suhu",
                                          "show", "current", "usage", "berapa", "semasa"))):
            answer = self._context_status_text()
            if temperature_query:
                answer += "\n" + "\n".join(temperature_lines(getattr(self, "metrics_snapshot", None), self.language))
            if temperature_query or query == "cpu":
                snapshot = getattr(self, "metrics_snapshot", None)
                cpus = getattr(snapshot, "cpu_temperatures", ())
                status = getattr(snapshot, "temperature_status", "starting")
                if status in {"driver_missing", "permission_denied", "driver_busy", "driver_unavailable"} or any(
                        device.reason in {"driver_missing", "permission_denied", "driver_busy", "driver_unavailable"}
                        for device in cpus):
                    answer += "\n" + self._t("CPU 温度不可用：可能缺少 PawnIO 或读取权限；诊断步骤见 README。")
                elif status not in {"ok", "partial", "starting", "disabled"}:
                    answer += "\n" + self._t("温度暂不可用；诊断步骤见 README。")
            self._append_message("pet", answer)
            return True
        if weather_query:
            city = str(self.settings.get("weather_city", "")).strip()
            if not city:
                self._append_message("pet", self._t("请先搜索并选择城市"))
                return True
            def run():
                try:
                    stored = self.settings.get("weather_location")
                    reading = (fetch_weather_at(WeatherLocation.from_dict(stored), language=self.language)
                               if stored else fetch_weather(city, language=self.language))
                    answer = reading.status_text + chr(10) + self._t("更新于") + " " + reading.observed_at + " · Open-Meteo"
                    self._schedule_ui(lambda: (self._finish_weather(city, reading, None),
                                               self._append_message("pet", answer)))
                except (OSError, ValueError) as exc:
                    detail = str(exc)
                    self._schedule_ui(lambda: self._append_message("pet", detail))
            threading.Thread(target=run, name="pawpal-weather-chat", daemon=True).start()
            return True
        return False

    @staticmethod
    def _offline_reply(text: str) -> str:
        if any(word in text for word in ("累", "困", "休息", "烦", "压力")):
            return random.choice(("先歇一小会儿吧。我陪你。", "慢慢来，今天已经很努力了。"))
        if any(word in text for word in ("摸", "可爱", "喜欢")):
            return "呼噜……（轻轻蹭了蹭你的手）"
        if any(word in text for word in ("代码", "编程", "工作", "学习")):
            return "我在这里陪你。需要一起想办法时，可以在设置里连接 AI。"
        return random.choice(("喵。我听着呢。", "嗯，慢慢说。", "我在。"))

    def _request_ai(self, endpoint: str, model: str, key: str, messages: list[dict[str, str]]) -> None:
        try:
            answer = request_chat(endpoint, model, key, messages, timeout=50)
            self._schedule_ui(lambda: self._finish_ai(answer, None))
        except ChatError as exc:
            detail = exc.localized(getattr(self, "language", "zh-CN"))
            self._schedule_ui(lambda: self._finish_ai(None, detail))

    def _finish_ai(self, answer: str | None, error: str | None) -> None:
        self.waiting = False
        if answer:
            self.connection_verified = True
            self.history.append({"role": "assistant", "content": answer})
            self._append_message("pet", answer)
        elif error:
            self._append_message("pet", error)
        if self.chat_status is not None:
            self.chat_status.configure(text=self._t(self._mode_label()))

    def _finish_local_reply(self, answer: str) -> None:
        self._append_message("pet", self._t(answer))



    def open_size_settings(self) -> None:
        if self.size_window is not None:
            try:
                if self.size_window.winfo_exists():
                    self.size_window.deiconify()
                    self.size_window.lift()
                    self.size_window.focus_force()
                    return
            except tk.TclError:
                pass
            self.size_window = None

        win = tk.Toplevel(self.root)
        self.size_window = win
        win.title(self._t("猫咪 · 桌宠大小"))
        win_width, win_height = 420, 224
        screen_width, screen_height = win.winfo_screenwidth(), win.winfo_screenheight()
        win.geometry(
            f"{win_width}x{win_height}+{max(8, (screen_width - win_width) // 2)}"
            f"+{max(8, (screen_height - win_height) // 2)}"
        )
        win.resizable(False, False)
        win.configure(bg="#f7f4fb")
        win.attributes("-topmost", True)

        pad = tk.Frame(win, bg="#f7f4fb", padx=22, pady=16)
        pad.pack(fill="both", expand=True)
        heading = tk.Frame(pad, bg="#f7f4fb")
        heading.pack(fill="x")
        tk.Label(heading, text=self._t("桌宠大小"), bg="#f7f4fb", fg=INK,
                 font=("Microsoft YaHei UI", 13, "bold")).pack(side="left")
        value = tk.DoubleVar(value=round(self.pet_scale * 100))
        value_label = tk.Label(heading, text=self._t(f"{round(self.pet_scale * 100)}%"),
                               bg="#f7f4fb", fg=PURPLE,
                               font=("Microsoft YaHei UI", 11, "bold"))
        value_label.pack(side="right")
        tk.Label(
            pad, text=self._t("拖动滑块预览，点击“完成”保存这个大小。"),
            bg="#f7f4fb", fg="#82768d", font=("Microsoft YaHei UI", 9),
        ).pack(anchor="w", pady=(5, 8))

        def preview(raw_value: str) -> None:
            scale = self._normalize_pet_scale(float(raw_value) / 100)
            value_label.configure(text=self._t(f"{round(scale * 100)}%"))
            if self._size_resize_job is not None:
                try:
                    self.root.after_cancel(self._size_resize_job)
                except tk.TclError:
                    pass
            self._size_resize_job = self.root.after(
                100, lambda v=scale: self._apply_queued_pet_scale(v)
            )

        slider = tk.Scale(
            pad, from_=int(MIN_PET_SCALE * 100), to=int(MAX_PET_SCALE * 100),
            resolution=5, orient="horizontal", variable=value, command=preview,
            showvalue=False, length=360, bg="#f7f4fb", fg=INK,
            troughcolor="#e8e0f1", activebackground=PURPLE,
            highlightthickness=0, bd=0, sliderrelief="flat",
        )
        slider.pack(fill="x", pady=(0, 2))
        bounds = tk.Frame(pad, bg="#f7f4fb")
        bounds.pack(fill="x")
        tk.Label(bounds, text=self._t("50%"), bg="#f7f4fb", fg="#82768d",
                 font=("Microsoft YaHei UI", 8)).pack(side="left")
        tk.Label(bounds, text=self._t("150%"), bg="#f7f4fb", fg="#82768d",
                 font=("Microsoft YaHei UI", 8)).pack(side="right")

        buttons = tk.Frame(pad, bg="#f7f4fb")
        buttons.pack(fill="x", side="bottom", pady=(9, 0))

        def finish() -> None:
            if self._size_resize_job is not None:
                try:
                    self.root.after_cancel(self._size_resize_job)
                except tk.TclError:
                    pass
                self._size_resize_job = None
            self._apply_pet_scale(value.get() / 100)
            try:
                self._save_settings_file()
            except OSError as exc:
                messagebox.showerror(self._t("保存失败"), self._t(f"无法保存桌宠大小：{exc}"), parent=win)
                return
            if self.size_window is win:
                self.size_window = None
            win.destroy()

        def restore_default() -> None:
            value.set(DEFAULT_PET_SCALE * 100)
            preview(str(DEFAULT_PET_SCALE * 100))

        win.protocol("WM_DELETE_WINDOW", finish)
        win.bind("<Escape>", lambda _event: finish())
        ttk.Button(buttons, text=self._t("完成"), style="Send.TButton", command=finish).pack(side="right")
        ttk.Button(buttons, text=self._t("恢复默认"), style="Pet.TButton",
                   command=restore_default).pack(side="right", padx=8)

    def _apply_queued_pet_scale(self, value: float) -> None:
        self._size_resize_job = None
        self._apply_pet_scale(value)

    def open_companion_settings(self) -> None:
        from pet_settings import open_preferences
        open_preferences(self)

    def open_settings(self) -> None:
        from pet_settings import open_ai_settings
        open_ai_settings(self)


if __name__ == "__main__":
    if any(argument == "--system-diagnostics" or argument.startswith("--system-diagnostics=")
           for argument in sys.argv[1:]):
        from pet_diagnostics import run_diagnostics
        raise SystemExit(run_diagnostics())
    if any(argument == "--temperature-diagnostics" or argument.startswith("--temperature-diagnostics=")
           for argument in sys.argv[1:]):
        from pet_temperature_cli import run_diagnostics
        raise SystemExit(run_diagnostics())
    isolated = os.environ.get("PAWPAL_DATA_DIR")
    instance = SingleInstance("Local\\PawpalCat-QA-" + hashlib.sha256(isolated.encode()).hexdigest()[:16]
                              if isolated else None)
    if not instance.acquire():
        sys.exit(0)
    app = None
    try:
        app = DesktopPet()
        app.root.mainloop()
    finally:
        if app is not None:
            app.metrics.stop()
            app.animation.close()
            app._release_layered_surface()
        instance.release()
