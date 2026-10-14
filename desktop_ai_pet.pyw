"""A small, always-on-top desktop companion for Windows."""

from __future__ import annotations

import json
import ctypes
import math
import os
import random
import sys
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

from pet_context import CpuMonitor, ContextReactionScheduler, WeatherReading, fetch_weather
from pet_behavior import PetAction, PetBehaviorManager
from pet_dialogue import DialogueManager
from pet_presentation import AnimationController, IdlePoseCycle, SpeechBubble
from pet_voice import WindowsSpeechInput, WindowsSpeechOutput


APP_NAME = "熊大"
KEY_COLOR = "#fffefe"
INK = "#493d50"
PURPLE = "#7561a8"
BASE_PET_WIDTH = 340
BASE_PET_HEIGHT = 410
MIN_PET_SCALE = 0.5
MAX_PET_SCALE = 1.5
DEFAULT_PET_SCALE = 1.0
OPENAI_ENDPOINT = "https://api.openai.com/v1"
OPENAI_MODEL = "gpt-5.6-sol"
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
    "你叫熊大，是住在用户桌面上的一只憨厚大哥型熊伙伴。你贪吃、爱犯懒、调皮、"
    "喜欢吐槽，有点嘴硬但讲义气，偶尔犯傻、卖萌或小生气。说话自然、接地气，像熟人陪聊，"
    "不许像客服或正式助手；先接住用户的情绪，再用简短的话回应或帮忙。不要攻击用户，"
    "不要假装看见屏幕或控制电脑。所有表达必须原创，不套用影视作品台词或剧情。使用中文。"
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
        self.root.title(APP_NAME)
        self.root.overrideredirect(True)
        self.root.attributes("-topmost", True)
        self.root.configure(bg=KEY_COLOR)

        self.width, self.height = BASE_PET_WIDTH, BASE_PET_HEIGHT
        self.settings_path = self._settings_path()
        self.settings = self._load_settings()
        self.weather_info: WeatherReading | None = None
        self.weather_error: str | None = None
        self.weather_request_pending = False
        self.next_weather_refresh = 0.0
        self.cpu_monitor = CpuMonitor()
        self.cpu_percent: float | None = None
        self.next_cpu_sample = 0.0
        self.next_status_refresh = 0.0
        self.context_reactions = ContextReactionScheduler()
        self.voice_output = WindowsSpeechOutput()
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
        self.animation = AnimationController(resource_path("assets/pet"))
        self.idle_pose = IdlePoseCycle(len(self.animation.frames_for("idle")))

        self.api_key = self._load_api_key()
        self.tray_icon = None
        self.tray_thread: threading.Thread | None = None
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
        self.behavior = PetBehaviorManager()
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
        self.blink_until = 0.0
        self.next_blink = time.monotonic() + random.uniform(2.5, 5.5)
        self.current_sprite_bbox = (48, 112, 292, 360)
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
        self.root.protocol("WM_DELETE_WINDOW", self._quit_app)
        self._start_tray_icon()
        self._animate()

    def _init_layered_surface(self) -> None:
        if os.name != "nt":
            raise OSError("熊大的逐像素透明桌宠窗口需要在 Windows 上运行。")

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

        factor = 2
        frame = Image.new("RGBA", (self.width * factor, self.height * factor), (0, 0, 0, 0))
        draw = ImageDraw.Draw(frame)
        s = lambda value: round(value * factor)
        now = time.monotonic()
        self.bubble.draw(draw, factor=factor, width=self.width,
                         font_path=r"C:\Windows\Fonts\msyh.ttc")

        phase = now * 1.7
        float_y = 2.1 * math.sin(phase)
        float_x = 1.2 * math.sin(phase * 0.55)
        state = self.behavior.state
        elapsed = now - self.behavior.state_started
        if state == "idle":
            from_index, to_index, blend = self.idle_pose.frame_pair(now=now)
            if from_index != to_index and blend > 0.0:
                relaxed_or_laughing = self.animation.blend_frames(
                    "idle", from_index, to_index, blend
                )
                sprite = relaxed_or_laughing
            else:
                # The available blink art matches the laughing pose only. Avoid
                # flashing it over the relaxed pose, which caused the odd jump.
                use_blink = now < self.blink_until and from_index == 0
                sprite = self.animation.frame(
                    state, elapsed, from_index, blink=use_blink
                )
        else:
            sprite = self.animation.frame(
                state, elapsed, self.sprite_frame_index,
                mirror=state == "walk" and self.walk_direction < 0,
            )
        visible = sprite.getchannel("A").getbbox() or (
            0, 0, self.animation.cell_size[0], self.animation.cell_size[1]
        )
        try:
            lanczos = Image.Resampling.LANCZOS
        except AttributeError:
            lanczos = Image.LANCZOS
        sprite = sprite.resize((sprite.width * factor, sprite.height * factor), lanczos)
        sprite_left = (self.width - sprite.width / factor) / 2 + float_x
        sprite_top = 112 + float_y
        self.current_sprite_bbox = (
            round(sprite_left + visible[0]), round(sprite_top + visible[1]),
            round(sprite_left + visible[2]), round(sprite_top + visible[3]),
        )
        foot_y = 112 + self.animation.cell_size[1] + float_y
        draw.ellipse(
            (s(170 + float_x - 29), s(foot_y - 1),
             s(170 + float_x + 29), s(foot_y + 8)),
            fill=(118, 102, 128, 54),
        )
        draw.ellipse(
            (s(170 + float_x - 18), s(foot_y + 1),
             s(170 + float_x + 18), s(foot_y + 6)),
            fill=(118, 102, 128, 42),
        )
        frame.alpha_composite(sprite, (s(sprite_left), s(sprite_top)))

        frame = frame.resize((self.window_width, self.window_height), lanczos)
        self._present_layered_frame(frame)

    def _release_layered_surface(self) -> None:
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
    def _load_api_key() -> str:
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
            "TuanziDesktopPet/OpenAI", CRED_TYPE_GENERIC, 0,
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
    def _store_api_key(api_key: str) -> None:
        advapi32 = ctypes.WinDLL("Advapi32.dll", use_last_error=True)
        if not api_key:
            delete_credential = advapi32.CredDeleteW
            delete_credential.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD]
            delete_credential.restype = wintypes.BOOL
            if not delete_credential("TuanziDesktopPet/OpenAI", CRED_TYPE_GENERIC, 0):
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
        credential.TargetName = "TuanziDesktopPet/OpenAI"
        credential.Comment = "熊大 AI API 密钥"
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

    def _start_tray_icon(self) -> None:
        import pystray
        from PIL import Image

        with Image.open(resource_path("pet_icon.png")) as icon_image:
            self._tray_image = icon_image.convert("RGBA")

        self.tray_icon = pystray.Icon(
            "xiongda_desktop_pet",
            self._tray_image,
            APP_NAME,
            menu=pystray.Menu(
                pystray.MenuItem("和熊大聊天", self._tray_open_chat, default=True),
                pystray.MenuItem("设置 AI 接口", self._tray_open_settings),
                pystray.MenuItem("陪伴与语音设置", self._tray_open_companion_settings),
                pystray.MenuItem("调整桌宠大小", self._tray_open_size_settings),
                pystray.Menu.SEPARATOR,
                pystray.MenuItem("退出熊大", self._tray_quit),
            ),
        )
        self.tray_thread = threading.Thread(
            target=self.tray_icon.run, name="xiongda-tray", daemon=True,
        )
        self.tray_thread.start()

    def _schedule_ui(self, callback: object) -> None:
        try:
            self.root.after(0, callback)
        except tk.TclError:
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
        self.voice_output.close()
        if self.tray_icon is not None:
            try:
                self.tray_icon.stop()
            except Exception:
                pass
            self.tray_icon = None
        self._release_layered_surface()
        try:
            self.root.destroy()
        except tk.TclError:
            pass

    @staticmethod
    def _settings_path() -> Path:
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
            if isinstance(data, dict):
                return {
                    "endpoint": str(data.get("endpoint") or OPENAI_ENDPOINT),
                    "model": str(data.get("model") or OPENAI_MODEL),
                    "pet_scale": self._normalize_pet_scale(data.get("pet_scale")),
                    "weather_city": str(data.get("weather_city") or "").strip(),
                    "cpu_reactions": bool(data.get("cpu_reactions", True)),
                    "night_reactions": bool(data.get("night_reactions", True)),
                    "voice_reply_enabled": bool(data.get("voice_reply_enabled", True)),
                }
        except (OSError, json.JSONDecodeError):
            pass
        return {
            "endpoint": OPENAI_ENDPOINT,
            "model": OPENAI_MODEL,
            "pet_scale": DEFAULT_PET_SCALE,
            "weather_city": "",
            "cpu_reactions": True,
            "night_reactions": True,
            "voice_reply_enabled": True,
        }

    def _save_settings_file(self) -> None:
        self.settings_path.parent.mkdir(parents=True, exist_ok=True)
        self.settings_path.write_text(
            json.dumps(self.settings, ensure_ascii=False, indent=2), encoding="utf-8"
        )

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
        now = time.monotonic()
        self._track_hover_pointer()
        if now >= self.next_blink:
            self.blink_until = now + 0.16
            self.next_blink = now + random.uniform(2.5, 5.5)
        self._sample_cpu(now)
        self._maybe_refresh_weather(now)
        self.behavior.update(now=now)
        action = self.behavior.autonomous_action(now=now)
        if action is not None:
            self._on_action(action)
        context_event = self.context_reactions.poll(
            now=now,
            local_time=datetime.now(),
            cpu_percent=self.cpu_percent,
            weather=self.weather_info,
            is_idle=self.behavior.state == "idle",
            cpu_enabled=bool(self.settings.get("cpu_reactions", True)),
            night_enabled=bool(self.settings.get("night_reactions", True)),
        )
        if context_event is not None:
            action = self.behavior.context_action(
                context_event.state, context_event.dialogue, now=now,
                duration=context_event.duration,
            )
            if action is not None:
                self._on_action(action)
        self.idle_pose.update(now=now, is_idle=self.behavior.state == "idle")
        self._move_autonomously(now)
        self.bubble.tick(now=now)
        self._update_pet_context_bubble(now)
        if now >= self.next_status_refresh:
            self.next_status_refresh = now + 5.0
            self._update_chat_context_status()
        self._draw()
        try:
            self.root.after(60, self._animate)
        except tk.TclError:
            return

    def _sample_cpu(self, now: float) -> None:
        if now < self.next_cpu_sample:
            return
        self.next_cpu_sample = now + 2.0
        try:
            self.cpu_percent = self.cpu_monitor.sample()
        except (OSError, AttributeError):
            self.cpu_percent = None

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
        try:
            reading = fetch_weather(city)
            error = None
        except (OSError, ValueError, TimeoutError) as exc:
            reading = None
            error = str(exc)
        try:
            self.root.after(0, lambda: self._finish_weather(city, reading, error))
        except tk.TclError:
            return

    def _finish_weather(self, city: str, reading: WeatherReading | None,
                        error: str | None) -> None:
        self.weather_request_pending = False
        if city != str(self.settings.get("weather_city", "")).strip():
            self.next_weather_refresh = 0.0
            return
        self.weather_info = reading
        self.weather_error = error
        self.next_weather_refresh = time.monotonic() + (3600.0 if reading else 1800.0)
        if reading is None:
            self.context_reactions.reset_weather()
        self._update_chat_context_status(force=True)

    def _context_status_text(self) -> str:
        if self.weather_info is not None:
            weather = self.weather_info.status_text
        elif self.settings.get("weather_city"):
            weather = f"{self.settings['weather_city']} 天气更新中" if self.weather_error is None else "天气暂不可用"
        else:
            weather = "天气未设置"
        cpu = "CPU --" if self.cpu_percent is None else f"CPU {self.cpu_percent:.0f}%"
        return f"{datetime.now().strftime('%H:%M')}  ·  {weather}  ·  {cpu}"

    def _update_pet_context_bubble(self, now: float) -> None:
        idle_without_dialogue = (
            self.behavior.state == "idle"
            and not self.hover_inside
            and self.chat_window is None
            and self.context_window is None
        )
        if idle_without_dialogue and self.context_bubble_active and self.bubble.text:
            # Keep a persistent status bubble current without restarting its fade.
            status_text = self._context_status_text()
            if self.bubble.text != status_text:
                self.bubble.text = status_text
            self.bubble.expires_at = now + 86400
        elif (idle_without_dialogue and not self.bubble.text
              and self.bubble.alpha <= 0.001):
            self.bubble.show(self._context_status_text(), 86400, now=now)
            self.context_bubble_active = True
        elif self.context_bubble_active:
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
                label.configure(text=self._context_status_text())
        except tk.TclError:
            if force:
                self.chat_context_status = None

    def _move_autonomously(self, now: float) -> None:
        elapsed = max(0.0, min(0.12, now - self.walk_step_time))
        self.walk_step_time = now
        if self.behavior.state != "walk" or elapsed <= 0:
            return
        screen_w = self.root.winfo_screenwidth()
        left_limit, right_limit = 8, max(8, screen_w - self.window_width - 8)
        next_x = self.root.winfo_x() + self.walk_direction * 34 * elapsed
        if next_x <= left_limit:
            next_x = left_limit
            self.walk_direction = 1
        elif next_x >= right_limit:
            next_x = right_limit
            self.walk_direction = -1
        self.root.geometry(f"+{round(next_x)}+{self.root.winfo_y()}")

    def _on_action(self, action: PetAction) -> None:
        self._hide_context_bubble()
        self.hover_bubble = False
        frames = self.animation.frames_for(action.state)
        if action.state in {"walk", "sleep"}:
            self.sprite_frame_index = 0
        else:
            self.sprite_frame_index = random.randrange(max(1, len(frames)))
        now = time.monotonic()
        if action.state == "walk":
            self.walk_direction = random.choice((-1, 1))
            self.walk_step_time = now
        selected = self.dialogue.choose(action.dialogue, now=now)
        if selected is not None:
            line, duration = selected
            self.bubble.show(line, duration, now=now)

    def _set_hover(self, inside: bool) -> None:
        if inside == self.hover_inside:
            return
        self.hover_inside = inside
        now = time.monotonic()
        if inside:
            self._hide_context_bubble()
            if now - self.last_hover_line >= 18.0 and self.behavior.state == "idle":
                selected = self.dialogue.choose("interaction", now=now)
                if selected is not None:
                    line, duration = selected
                    self.bubble.show(line, min(duration, 3.0), now=now)
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
        if abs(dx) + abs(dy) > 5:
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
        menu_w, menu_h = 224, 249
        x = min(event.x_root, popup.winfo_screenwidth() - menu_w - 8)
        y = min(event.y_root, popup.winfo_screenheight() - menu_h - 8)
        popup.geometry(f"{menu_w}x{menu_h}+{max(0, x)}+{max(0, y)}")
        canvas = tk.Canvas(popup, width=menu_w, height=menu_h, bg=KEY_COLOR,
                           highlightthickness=0, bd=0)
        canvas.pack(fill="both", expand=True)

        rounded_polygon(canvas, 4, 4, menu_w - 4, menu_h - 4, 18,
                        fill="#fbf9fe", outline="#e9e3f0", width=1)
        actions = [
            ("chat", "和熊大聊天", self.open_chat),
            ("settings", "设置 AI 接口", self.open_settings),
            ("companion", "陪伴与语音设置", self.open_companion_settings),
            ("size", "调整桌宠大小", self.open_size_settings),
            ("quit", "退出熊大", self._quit_app),
        ]
        for index, (tag, label, action) in enumerate(actions):
            top = 8 + index * 47
            if index == 4:
                canvas.create_line(18, top - 5, menu_w - 18, top - 5, fill="#eee8f2")
            row_tag = f"row_{tag}"
            bg_tag = f"bg_{tag}"
            rounded_polygon(canvas, 10, top, menu_w - 10, top + 40, 12,
                            fill="#fbf9fe", outline="", tags=(bg_tag,))
            canvas.create_text(28, top + 20, text=label, anchor="w", fill=INK,
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
        win.title("和熊大聊天")
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
        canvas.create_text(26, 34, text="✦", fill=PURPLE,
                           font=("Segoe UI Symbol", 19), anchor="w", tags=("title_drag",))
        canvas.create_text(55, 34, text="熊大", fill=INK, anchor="w",
                           font=("Microsoft YaHei UI", 15, "bold"), tags=("title_drag",))
        canvas.create_text(56, 60, text="DESKTOP COMPANION", fill="#9b91a4", anchor="w",
                           font=("Segoe UI", 8), tags=("title_drag",))
        rounded_polygon(canvas, 299, 18, 364, 50, 15, fill="#eee8fa", outline="", tags=("settings",))
        canvas.create_text(331, 34, text="设置", fill="#67588a",
                           font=("Microsoft YaHei UI", 9), tags=("settings",))
        canvas.create_text(390, 34, text="×", fill="#82768d",
                           font=("Segoe UI", 18), tags=("close",))
        canvas.tag_bind("settings", "<Button-1>", lambda _event: self.open_settings())
        canvas.tag_bind("close", "<Button-1>", lambda _event: self._close_chat())
        canvas.tag_bind("title_drag", "<ButtonPress-1>", self._start_chat_drag)
        canvas.tag_bind("title_drag", "<B1-Motion>", self._drag_chat)
        canvas.tag_bind("header_drag", "<ButtonPress-1>", self._start_chat_drag)
        canvas.tag_bind("header_drag", "<B1-Motion>", self._drag_chat)

        rounded_polygon(canvas, 18, 86, 402, 487, 22, fill="#fffdfd", outline="#ede8f3", width=1)
        self.chat_context_status = ttk.Label(
            win, text=self._context_status_text(), background="#fffdfd",
            foreground="#71658a", font=("Microsoft YaHei UI", 8),
            wraplength=360, anchor="w",
        )
        canvas.create_window(31, 92, anchor="nw", window=self.chat_context_status,
                             width=365, height=20)
        self.chat_status = ttk.Label(win, text=self._mode_label(), background="#fffdfd",
                                     foreground="#968b9e", font=("Microsoft YaHei UI", 8),
                                     wraplength=350, anchor="w")
        canvas.create_window(31, 115, anchor="nw", window=self.chat_status,
                             width=365, height=20)
        self.chat_text = tk.Text(
            win, wrap="word", state="disabled", relief="flat", bd=0,
            bg="#fffdfd", fg=INK, padx=12, pady=10,
            font=("Microsoft YaHei UI", 10), spacing1=3, spacing3=7,
            highlightthickness=0,
        )
        canvas.create_window(28, 144, anchor="nw", window=self.chat_text, width=364, height=323)
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
            canvas.create_text(left + 59, 517, text=label, fill="#67588a",
                               font=("Microsoft YaHei UI", 9), tags=(tag,))
            canvas.tag_bind(tag, "<Button-1>", lambda _event, p=prompt: self._send_text(p))

        rounded_polygon(canvas, 18, 546, 402, 602, 20, fill="#f5f2f8", outline="#eee9f2", width=1)
        self.chat_input = tk.Text(win, height=2, wrap="word", relief="flat", bd=0,
                                  bg="#f5f2f8", fg=INK, padx=5, pady=6,
                                  font=("Microsoft YaHei UI", 10), highlightthickness=0)
        canvas.create_window(31, 556, anchor="nw", window=self.chat_input, width=250, height=38)
        self.chat_input.insert("1.0", "说点什么…")
        self.chat_input.configure(fg="#a69aaa")
        self.chat_input.bind("<FocusIn>", self._clear_placeholder)
        self.chat_input.bind("<Return>", self._send_on_enter)
        rounded_polygon(canvas, 288, 553, 328, 595, 17, fill="#eee8fa", outline="",
                        tags=("mic_bg", "mic"))
        canvas.create_text(308, 574, text="\uE720", fill="#67588a",
                           font=("Segoe MDL2 Assets", 14), tags=("mic_icon", "mic"))
        self.mic_canvas = canvas
        canvas.tag_bind("mic", "<Button-1>", lambda _event: self._toggle_speech_input())
        rounded_polygon(canvas, 337, 553, 391, 595, 18, fill=PURPLE, outline="", tags=("send",))
        canvas.create_text(364, 574, text="发送", fill="#ffffff",
                           font=("Microsoft YaHei UI", 9, "bold"), tags=("send",))
        canvas.tag_bind("send", "<Button-1>", lambda _event: self._send_from_input())
        win.bind("<Escape>", lambda _event: self._close_chat())

        self._append_message("pet", self._initial_greeting())
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
                self.chat_status.configure(text="俺还在听；说完停顿一下，识别后会填进输入框。")
            return
        if self.chat_input is None:
            return
        self.mic_listening = True
        if self.mic_canvas is not None:
            self.mic_canvas.itemconfigure("mic_bg", fill="#f7e4e5")
            self.mic_canvas.itemconfigure("mic_icon", fill="#b64d58")
        if self.chat_status is not None:
            self.chat_status.configure(text="正在听你说话；说完停顿一下，识别后会填进输入框。")

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
                    self.chat_status.configure(text=self._mode_label())
                except tk.TclError:
                    pass
            return
        if text:
            if self.chat_input.get("1.0", "end-1c") == "说点什么…":
                self.chat_input.delete("1.0", "end")
                self.chat_input.configure(fg=INK)
            existing = self.chat_input.get("1.0", "end-1c").strip()
            self.chat_input.insert("end" if existing else "1.0",
                                   (" " if existing else "") + text)
            self.chat_input.focus_set()
            if self.waiting or existing:
                if self.chat_status is not None:
                    self.chat_status.configure(text="识别完成，文字已放进输入框；当前有内容，检查后点发送。")
                    try:
                        self.root.after(5000, self._restore_speech_status)
                    except tk.TclError:
                        pass
            else:
                if self.chat_status is not None:
                    self.chat_status.configure(text="识别完成，正在发送给熊大…")
                self._send_from_input()
        elif error:
            self._append_message("pet", error)
            if self.chat_status is not None:
                self.chat_status.configure(text=error)

    def _restore_speech_status(self) -> None:
        if self.chat_status is None:
            return
        try:
            if self.chat_status.winfo_exists() and str(self.chat_status.cget("text")).startswith("识别完成"):
                self.chat_status.configure(text=self._mode_label())
        except tk.TclError:
            pass

    def _clear_placeholder(self, _event: tk.Event) -> None:
        if self.chat_input is not None and self.chat_input.get("1.0", "end-1c") == "说点什么…":
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
        if not text or text == "说点什么…":
            return
        self.chat_input.delete("1.0", "end")
        self.chat_input.configure(fg=INK)
        self._send_text(text)

    def _append_message(self, speaker: str, text: str) -> None:
        if self.chat_text is None:
            return
        self.chat_text.configure(state="normal")
        tag = "user" if speaker == "user" else "pet"
        prefix = "你  " if speaker == "user" else "熊大  "
        self.chat_text.insert("end", prefix + text.strip() + "\n\n", tag)
        self.chat_text.configure(state="disabled")
        self.chat_text.see("end")

    def _initial_greeting(self) -> str:
        endpoint = self.settings.get("endpoint", "").strip()
        model = self.settings.get("model", "").strip()
        if not endpoint or not model:
            return "现在能本地陪你唠嗑；想接 AI 就点右上角设置，先说好 API 账单你负责啊。"
        provider = provider_name_for_endpoint(endpoint)
        if provider in ("OpenAI", "DeepSeek") and not self.api_key:
            return f"先把 {provider} API Key 填进设置，咱就能开聊了。"
        return "今天想聊点啥？先说好，聊完得给俺留点零食。"

    def _mode_label(self) -> str:
        model = self.settings.get("model", "").strip()
        endpoint = self.settings.get("endpoint", "").strip()
        provider = provider_name_for_endpoint(endpoint)
        if model and endpoint and provider in ("OpenAI", "DeepSeek") and not self.api_key:
            return f"{provider} · 请设置 API Key"
        if model and endpoint:
            return f"AI 已配置 · {provider}"
        return "本地轻量陪聊 · 尚未连接 AI 接口"

    def _send_text(self, text: str) -> None:
        self.open_chat()
        if self.waiting:
            self._append_message("pet", "我还在想上一条呢，等我一下下～")
            return
        self._append_message("user", text)
        self.history.append({"role": "user", "content": text})
        endpoint = self.settings.get("endpoint", "").strip()
        model = self.settings.get("model", "").strip()
        if not endpoint or not model:
            self.root.after(450, lambda: self._finish_local_reply(self._offline_reply(text)))
            return

        provider = provider_name_for_endpoint(endpoint)
        if provider in ("OpenAI", "DeepSeek") and not self.api_key:
            if provider == "DeepSeek":
                prompt = "还没有 DeepSeek API 密钥，请在设置 AI 接口中填写密钥。"
            else:
                prompt = "还没有 OpenAI API 密钥。ChatGPT Go 订阅不包含 API 用量，请在设置中填写密钥。"
            self._append_message("pet", prompt)
            return
        key = self.api_key
        self.waiting = True
        if self.chat_status is not None:
            self.chat_status.configure(text="熊大正琢磨呢…")
        messages = [{"role": "system", "content": SYSTEM_PROMPT}] + self.history[-12:]
        threading.Thread(target=self._request_ai, args=(endpoint, model, key, messages), daemon=True).start()

    @staticmethod
    def _offline_reply(text: str) -> str:
        lower = text.lower()
        if any(word in lower for word in ("累", "困", "休息", "烦", "压力")):
            return random.choice((
                "累了就歇会儿，别跟自己较劲。起来记得喝口水，这主意不错吧？",
                "瞧你累的，肩膀松松。俺陪你缓一会儿，谁催你我帮你瞪他——开玩笑的。",
                "先喘口气，等会儿咱再一小步一小步啃难题。",
            ))
        if any(word in lower for word in ("摸", "可爱", "喜欢")):
            return "哼，还挺会挑。摸两下就行，别把俺的熊毛摸秃了。"
        if any(word in lower for word in ("笑话", "好笑")):
            return "有只猫去上编程课，老师问它会什么语言。它说：喵 Python！……我是不是该再练练？"
        if any(word in lower for word in ("代码", "编程", "工作", "学习")):
            return "把难题切成小块，一口一口啃……呃，我说正经的，告诉俺卡在哪一步。"
        return random.choice((
            "俺听着呢，说详细点，别光吊我胃口。",
            "行，熊大听明白了。你想先唠唠，还是跟俺一起想个办法？",
            "这事俺先记着。你眼下最想解决的是哪一块？",
        ))

    def _request_ai(self, endpoint: str, model: str, key: str,
                    messages: list[dict[str, str]]) -> None:
        base = endpoint.rstrip("/")
        if base.endswith("/chat/completions"):
            url = base
        elif base.endswith("/v1"):
            url = base + "/chat/completions"
        elif is_deepseek_endpoint(endpoint):
            url = base + "/chat/completions"
        else:
            url = base + "/v1/chat/completions"
        payload_data = {
            "model": model,
            "messages": messages,
            "stream": False,
        }
        if model.lower().startswith(("gpt-5", "gpt-6")):
            # Reasoning models reject sampling parameters at non-none effort.
            payload_data["reasoning_effort"] = "low"
            payload_data["max_completion_tokens"] = 512
        elif is_deepseek_endpoint(endpoint):
            payload_data["reasoning_effort"] = "low"
            payload_data["max_tokens"] = 512
        else:
            payload_data["max_tokens"] = 512
            payload_data["temperature"] = 0.8
        payload = json.dumps(payload_data).encode("utf-8")
        headers = {"Content-Type": "application/json"}
        if key:
            headers["Authorization"] = "Bearer " + key
        request = Request(url, data=payload, headers=headers, method="POST")
        try:
            with urlopen(request, timeout=50) as response:
                body = json.loads(response.read().decode("utf-8"))
            answer = body["choices"][0]["message"]["content"]
            if isinstance(answer, list):
                answer = "".join(str(part.get("text", "")) for part in answer if isinstance(part, dict))
            answer = str(answer).strip() or "我刚刚走神啦，再问我一次好吗？"
            self.root.after(0, lambda a=answer: self._finish_ai(a, None))
        except (HTTPError, URLError, TimeoutError, OSError, ValueError, KeyError, IndexError) as exc:
            detail = "连接失败。请检查接口地址、模型名称和服务是否可用。"
            if isinstance(exc, HTTPError):
                if exc.code == 401 and provider_name_for_endpoint(endpoint) == "OpenAI":
                    detail = (
                        "API 密钥认证失败。请确认这是 OpenAI Platform 的 API Key；"
                        "如果你用的是 DeepSeek 密钥，请在设置中切换为 DeepSeek。"
                    )
                elif exc.code == 401 and provider_name_for_endpoint(endpoint) == "DeepSeek":
                    detail = "DeepSeek API 密钥认证失败，请检查密钥和 DeepSeek 账户的 API 访问权限。"
                else:
                    detail = f"服务返回 HTTP {exc.code}。请检查接口地址、模型名称和 API 权限。"
            self.root.after(0, lambda d=detail: self._finish_ai(None, d))

    def _finish_ai(self, answer: str | None, error: str | None) -> None:
        self.waiting = False
        if answer:
            self.history.append({"role": "assistant", "content": answer})
            self._append_message("pet", answer)
            if self.settings.get("voice_reply_enabled", True):
                self._speak_text(answer)
        elif error:
            self._append_message("pet", error)
        if self.chat_status is not None:
            self.chat_status.configure(text=self._mode_label())

    def _finish_local_reply(self, answer: str) -> None:
        self._append_message("pet", answer)
        if self.settings.get("voice_reply_enabled", True):
            self._speak_text(answer)

    def _speak_text(self, text: str) -> None:
        self.voice_output.speak(text, on_error=self._handle_speech_error)

    def _handle_speech_error(self, error: str) -> None:
        def show_error() -> None:
            if self.speech_warning_shown:
                return
            self.speech_warning_shown = True
            if error == "NO_CHINESE_VOICE":
                detail = "Windows 没有可用的中文语音，请在语音设置中安装中文语音。"
            else:
                detail = "Windows 语音播放失败，请检查语音设置后再试。"
            parent = self.companion_window or self.root
            try:
                messagebox.showinfo("熊大语音", detail, parent=parent)
            except tk.TclError:
                pass

        try:
            self.root.after(0, show_error)
        except tk.TclError:
            pass

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
        win.title("熊大 · 桌宠大小")
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
        tk.Label(heading, text="桌宠大小", bg="#f7f4fb", fg=INK,
                 font=("Microsoft YaHei UI", 13, "bold")).pack(side="left")
        value = tk.DoubleVar(value=round(self.pet_scale * 100))
        value_label = tk.Label(heading, text=f"{round(self.pet_scale * 100)}%",
                               bg="#f7f4fb", fg=PURPLE,
                               font=("Microsoft YaHei UI", 11, "bold"))
        value_label.pack(side="right")
        tk.Label(
            pad, text="拖动滑块预览，点击“完成”保存这个大小。",
            bg="#f7f4fb", fg="#82768d", font=("Microsoft YaHei UI", 9),
        ).pack(anchor="w", pady=(5, 8))

        def preview(raw_value: str) -> None:
            scale = self._normalize_pet_scale(float(raw_value) / 100)
            value_label.configure(text=f"{round(scale * 100)}%")
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
        tk.Label(bounds, text="50%", bg="#f7f4fb", fg="#82768d",
                 font=("Microsoft YaHei UI", 8)).pack(side="left")
        tk.Label(bounds, text="150%", bg="#f7f4fb", fg="#82768d",
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
                messagebox.showerror("保存失败", f"无法保存桌宠大小：{exc}", parent=win)
                return
            if self.size_window is win:
                self.size_window = None
            win.destroy()

        def restore_default() -> None:
            value.set(DEFAULT_PET_SCALE * 100)
            preview(str(DEFAULT_PET_SCALE * 100))

        win.protocol("WM_DELETE_WINDOW", finish)
        win.bind("<Escape>", lambda _event: finish())
        ttk.Button(buttons, text="完成", style="Send.TButton", command=finish).pack(side="right")
        ttk.Button(buttons, text="恢复默认", style="Pet.TButton",
                   command=restore_default).pack(side="right", padx=8)

    def _apply_queued_pet_scale(self, value: float) -> None:
        self._size_resize_job = None
        self._apply_pet_scale(value)

    def open_companion_settings(self) -> None:
        if self.companion_window is not None:
            try:
                if self.companion_window.winfo_exists():
                    self.companion_window.deiconify()
                    self.companion_window.lift()
                    self.companion_window.focus_force()
                    return
            except tk.TclError:
                pass
            self.companion_window = None

        win = tk.Toplevel(self.root)
        self.companion_window = win
        win.title("熊大 · 陪伴与语音设置")
        win_width, win_height = 500, 430
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
        tk.Label(pad, text="陪伴与语音", bg="#f7f4fb", fg=INK,
                 font=("Microsoft YaHei UI", 14, "bold")).pack(anchor="w")
        tk.Label(
            pad, text="天气只查询你填写的城市；CPU 和时间状态在本机处理。",
            bg="#f7f4fb", fg="#82768d", font=("Microsoft YaHei UI", 9),
        ).pack(anchor="w", pady=(3, 12))

        tk.Label(pad, text="天气城市（留空可关闭天气反应）", bg="#f7f4fb", fg=INK,
                 font=("Microsoft YaHei UI", 9, "bold")).pack(anchor="w", pady=(2, 4))
        city_entry = tk.Entry(
            pad, relief="flat", bd=0, bg="#ffffff", fg=INK,
            insertbackground=INK, font=("Microsoft YaHei UI", 10),
            highlightthickness=1, highlightbackground="#e6deef",
            highlightcolor=PURPLE,
        )
        city_entry.pack(fill="x", ipady=6)
        city_entry.insert(0, str(self.settings.get("weather_city", "")))

        cpu_var = tk.BooleanVar(value=bool(self.settings.get("cpu_reactions", True)))
        night_var = tk.BooleanVar(value=bool(self.settings.get("night_reactions", True)))
        voice_var = tk.BooleanVar(value=bool(self.settings.get("voice_reply_enabled", True)))
        for variable, label in (
            (cpu_var, "CPU 连续高负载时提醒（本机采样，不上传）"),
            (night_var, "深夜 00:00–04:59 偶尔提醒休息"),
            (voice_var, "用中文语音朗读聊天回复"),
        ):
            ttk.Checkbutton(pad, text=label, variable=variable).pack(anchor="w", pady=(9, 0))

        tk.Label(
            pad,
            text="麦克风先在本机转成文字，再发送给当前聊天模型；不会上传原始录音。语音回复优先使用 Windows 中文男声；天气使用 Open-Meteo。",
            bg="#f7f4fb", fg="#82768d", wraplength=450, justify="left",
            font=("Microsoft YaHei UI", 8),
        ).pack(anchor="w", pady=(12, 8))

        buttons = tk.Frame(pad, bg="#f7f4fb")
        buttons.pack(fill="x", side="bottom", pady=(8, 0))

        def close() -> None:
            if self.companion_window is win:
                self.companion_window = None
            try:
                if win.winfo_exists():
                    win.destroy()
            except tk.TclError:
                pass

        win.protocol("WM_DELETE_WINDOW", close)
        win.bind("<Escape>", lambda _event: close())

        def save() -> None:
            city = city_entry.get().strip()
            new_settings = {
                **self.settings,
                "weather_city": city,
                "cpu_reactions": cpu_var.get(),
                "night_reactions": night_var.get(),
                "voice_reply_enabled": voice_var.get(),
            }
            try:
                self.settings_path.parent.mkdir(parents=True, exist_ok=True)
                self.settings_path.write_text(
                    json.dumps(new_settings, ensure_ascii=False, indent=2), encoding="utf-8"
                )
            except OSError as exc:
                messagebox.showerror("保存失败", f"无法保存陪伴设置：{exc}", parent=win)
                return
            city_changed = city != str(self.settings.get("weather_city", "")).strip()
            self.settings = new_settings
            if city_changed:
                self.weather_info = None
                self.weather_error = None
                self.context_reactions.reset_weather()
                self.next_weather_refresh = 0.0
            self._update_chat_context_status(force=True)
            close()

        ttk.Button(buttons, text="保存设置", style="Send.TButton", command=save).pack(side="right")
        ttk.Button(buttons, text="取消", style="Pet.TButton", command=close).pack(side="right", padx=8)
        ttk.Button(
            buttons, text="试听语音", style="Pet.TButton",
            command=lambda: self._speak_text("熊大来报到，听得到俺说话吗？"),
        ).pack(side="left")

    def open_settings(self) -> None:
        if self.settings_window is not None:
            try:
                if self.settings_window.winfo_exists():
                    self.settings_window.deiconify()
                    self.settings_window.lift()
                    self.settings_window.focus_force()
                    return
            except tk.TclError:
                pass
            self.settings_window = None

        win = tk.Toplevel(self.root)
        self.settings_window = win
        win.title("熊大 · AI 接口设置")
        win_width, win_height = 500, 560
        screen_width, screen_height = win.winfo_screenwidth(), win.winfo_screenheight()
        win.geometry(
            f"{win_width}x{win_height}+{max(8, (screen_width - win_width) // 2)}"
            f"+{max(8, (screen_height - win_height) // 2)}"
        )
        win.resizable(False, False)
        win.configure(bg="#f7f4fb")
        win.attributes("-topmost", True)
        pad = tk.Frame(win, bg="#f7f4fb", padx=20, pady=14)
        pad.pack(fill="both", expand=True)
        tk.Label(pad, text="连接 AI 接口", bg="#f7f4fb", fg=INK,
                 font=("Microsoft YaHei UI", 14, "bold")).pack(anchor="w")
        tk.Label(pad, text="支持 OpenAI、DeepSeek 或自定义兼容服务。",
                 bg="#f7f4fb", fg="#82768d", font=("Microsoft YaHei UI", 9)).pack(anchor="w", pady=(3, 8))

        buttons = tk.Frame(pad, bg="#f7f4fb")
        buttons.pack(fill="x", side="bottom", pady=(10, 0))

        provider = tk.StringVar(value=provider_name_for_endpoint(self.settings.get("endpoint", "")))
        tk.Label(pad, text="服务商", bg="#f7f4fb", fg=INK,
                 font=("Microsoft YaHei UI", 9, "bold")).pack(anchor="w", pady=(2, 3))
        provider_box = ttk.Combobox(
            pad, textvariable=provider, state="readonly",
            values=("OpenAI", "DeepSeek", "自定义 / Ollama"),
            font=("Microsoft YaHei UI", 10),
        )
        provider_box.pack(fill="x", ipady=4)

        def field(label: str, initial: str, show: str = "") -> tk.Entry:
            tk.Label(pad, text=label, bg="#f7f4fb", fg=INK,
                     font=("Microsoft YaHei UI", 9, "bold")).pack(anchor="w", pady=(7, 3))
            entry = tk.Entry(pad, relief="flat", bd=0, bg="#ffffff", fg=INK,
                             insertbackground=INK, font=("Microsoft YaHei UI", 10),
                             highlightthickness=1, highlightbackground="#e6deef",
                             highlightcolor=PURPLE, show=show)
            entry.pack(fill="x", ipady=6)
            entry.insert(0, initial)
            return entry

        endpoint = field("接口地址", self.settings.get("endpoint", ""))
        model = field("模型名称", self.settings.get("model", ""))
        key_entry = field("API 密钥（Windows 加密保存）", self.api_key, show="•")

        def apply_provider(_event: tk.Event | None = None) -> None:
            presets = {
                "OpenAI": (OPENAI_ENDPOINT, OPENAI_MODEL),
                "DeepSeek": (DEEPSEEK_ENDPOINT, DEEPSEEK_MODEL),
            }
            selected = provider.get()
            if selected not in presets:
                return
            preset_endpoint, preset_model = presets[selected]
            endpoint.delete(0, "end")
            endpoint.insert(0, preset_endpoint)
            model.delete(0, "end")
            model.insert(0, preset_model)

        provider_box.bind("<<ComboboxSelected>>", apply_provider)
        hint = tk.Frame(pad, bg="#f7f4fb")
        hint.pack(fill="x", anchor="w", pady=(9, 2))
        tk.Label(
            hint,
            text="选择服务商会自动填地址和模型；请用对应服务商的 API Key。",
            bg="#f7f4fb", fg="#82768d", anchor="w", wraplength=450,
            font=("Microsoft YaHei UI", 8),
        ).pack(fill="x", anchor="w")
        tk.Label(
            hint, text="密钥留空保存会删除已存密钥。",
            bg="#f7f4fb", fg="#82768d", anchor="w",
            font=("Microsoft YaHei UI", 8),
        ).pack(anchor="w", pady=(2, 0))

        def close() -> None:
            if self.settings_window is win:
                self.settings_window = None
            try:
                if win.winfo_exists():
                    win.destroy()
            except tk.TclError:
                pass

        win.protocol("WM_DELETE_WINDOW", close)
        win.bind("<Escape>", lambda _event: close())

        def save() -> None:
            new_settings = {
                **self.settings,
                "endpoint": endpoint.get().strip(),
                "model": model.get().strip(),
                "pet_scale": self.pet_scale,
            }
            new_api_key = key_entry.get().strip()
            if not new_settings["endpoint"] or not new_settings["model"]:
                messagebox.showerror("信息不完整", "请填写接口地址和模型名称。", parent=win)
                return
            try:
                parsed_endpoint = urlsplit(new_settings["endpoint"])
            except ValueError:
                parsed_endpoint = None
            if parsed_endpoint is None or parsed_endpoint.scheme not in ("http", "https") or not parsed_endpoint.netloc:
                messagebox.showerror("地址格式错误", "接口地址需以 http:// 或 https:// 开头。", parent=win)
                return
            try:
                self._store_api_key(new_api_key)
                self.settings_path.parent.mkdir(parents=True, exist_ok=True)
                self.settings_path.write_text(json.dumps(new_settings, ensure_ascii=False, indent=2),
                                              encoding="utf-8")
            except (OSError, ValueError) as exc:
                messagebox.showerror("保存失败", f"无法保存接口设置：{exc}", parent=win)
                return
            self.settings = new_settings
            self.api_key = new_api_key
            if self.chat_status is not None:
                self.chat_status.configure(text=self._mode_label())
            messagebox.showinfo(
                "设置已保存",
                f"已保存 {provider_name_for_endpoint(new_settings['endpoint'])} 接口和模型「{new_settings['model']}」。\n"
                "回到聊天窗口发一条消息即可测试。",
                parent=win,
            )
            close()

        ttk.Button(buttons, text="保存设置", style="Send.TButton", command=save).pack(side="right")
        ttk.Button(buttons, text="取消", style="Pet.TButton", command=close).pack(side="right", padx=8)


if __name__ == "__main__":
    app = DesktopPet()
    try:
        app.root.mainloop()
    finally:
        app._release_layered_surface()
