"""Shared existing Windows per-pixel surface; cat rendering is unchanged."""
import ctypes
from ctypes import wintypes
import os

WS_EX_LAYERED = 0x00080000
GWL_EXSTYLE = -20
ULW_ALPHA = 0x00000002
AC_SRC_OVER = 0x00
AC_SRC_ALPHA = 0x01
BI_RGB = 0

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


class LayeredSurface:
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
