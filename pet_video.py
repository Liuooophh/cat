"""Small MP4 decoder used by the desktop pet animation controller.

The application ships only OpenCV's FFmpeg VideoIO plugin.  Calling its C ABI
directly keeps ``cv2`` and NumPy out of the runtime while still using a mature
H.264 decoder.  Animation videos store colour in the left half and a grayscale
alpha matte in the right half; :class:`PackedAlphaVideo` turns those frames
back into ordinary Pillow RGBA images.
"""

from __future__ import annotations

import ctypes
import math
import os
import sys
import threading
from dataclasses import dataclass
from pathlib import Path

from PIL import Image


_CV_ERROR_OK = 0
_CAPTURE_ABI_VERSION = 1
_CAPTURE_API_VERSION = 0

_CAP_PROP_POS_FRAMES = 1
_CAP_PROP_FRAME_WIDTH = 3
_CAP_PROP_FRAME_HEIGHT = 4
_CAP_PROP_FPS = 5
_CAP_PROP_FRAME_COUNT = 7

_DEFAULT_PLUGIN = Path("assets/vendor/opencv-videoio/opencv_videoio_ffmpeg500_64.dll")
_ALPHA_LUT = tuple(0 if value <= 3 else 255 if value >= 252 else value
                   for value in range(256))


class VideoDecodeError(RuntimeError):
    """Raised when the bundled decoder cannot open or decode an animation."""


@dataclass(frozen=True)
class VideoInfo:
    """Stable metadata read once when a video is opened."""

    width: int
    height: int
    fps: float
    frame_count: int


class _OpenCVApiHeader(ctypes.Structure):
    _fields_ = [
        ("valid_size", ctypes.c_size_t),
        ("min_api_version", ctypes.c_uint),
        ("api_version", ctypes.c_uint),
        ("opencv_version_major", ctypes.c_uint),
        ("opencv_version_minor", ctypes.c_uint),
        ("opencv_version_patch", ctypes.c_uint),
        ("opencv_version_status", ctypes.c_char_p),
        ("api_description", ctypes.c_char_p),
    ]


_CaptureOpen = ctypes.CFUNCTYPE(
    ctypes.c_int, ctypes.c_char_p, ctypes.c_int, ctypes.POINTER(ctypes.c_void_p))
_CaptureRelease = ctypes.CFUNCTYPE(ctypes.c_int, ctypes.c_void_p)
_CaptureGet = ctypes.CFUNCTYPE(
    ctypes.c_int, ctypes.c_void_p, ctypes.c_int, ctypes.POINTER(ctypes.c_double))
_CaptureSet = ctypes.CFUNCTYPE(
    ctypes.c_int, ctypes.c_void_p, ctypes.c_int, ctypes.c_double)
_CaptureGrab = ctypes.CFUNCTYPE(ctypes.c_int, ctypes.c_void_p)
_CaptureRetrieveCallback = ctypes.CFUNCTYPE(
    ctypes.c_int,
    ctypes.c_int,
    ctypes.POINTER(ctypes.c_ubyte),
    ctypes.c_int,
    ctypes.c_int,
    ctypes.c_int,
    ctypes.c_int,
    ctypes.c_void_p,
)
_CaptureRetrieve = ctypes.CFUNCTYPE(
    ctypes.c_int,
    ctypes.c_void_p,
    ctypes.c_int,
    _CaptureRetrieveCallback,
    ctypes.c_void_p,
)


class _CapturePluginApiV0(ctypes.Structure):
    _fields_ = [
        ("header", _OpenCVApiHeader),
        ("backend_id", ctypes.c_int),
        ("capture_open", _CaptureOpen),
        ("capture_release", _CaptureRelease),
        ("capture_get", _CaptureGet),
        ("capture_set", _CaptureSet),
        ("capture_grab", _CaptureGrab),
        ("capture_retrieve", _CaptureRetrieve),
    ]


_PLUGIN_LOCK = threading.Lock()
_PLUGINS: dict[Path, tuple[ctypes.CDLL, object, _CapturePluginApiV0]] = {}


def default_video_plugin_path() -> Path:
    """Return the decoder location in source and PyInstaller builds."""

    bundle_dir = getattr(sys, "_MEIPASS", None)
    root = Path(bundle_dir) if bundle_dir else Path(__file__).resolve().parent
    return root / _DEFAULT_PLUGIN


class OpenCVVideoDecoder:
    """Decode one video through OpenCV's VideoIO plugin C ABI.

    The decoded buffer belongs to the plugin only for the duration of its
    callback, so every frame is copied before control returns to native code.
    Instances are safe to close repeatedly.  Read/seek/close calls are
    serialized because a capture handle itself is not thread-safe.
    """

    def __init__(self, video_path: str | Path,
                 plugin_path: str | Path | None = None) -> None:
        self.path = Path(video_path).resolve()
        self.plugin_path = Path(plugin_path or default_video_plugin_path()).resolve()
        self._lock = threading.RLock()
        self._dll: ctypes.CDLL | None = None
        self._api_pointer: ctypes.POINTER(_CapturePluginApiV0) | None = None
        self._api: _CapturePluginApiV0 | None = None
        self._handle = ctypes.c_void_p()
        self._next_frame_index = 0
        self._callback_frame: tuple[bytes, int, int, int, int] | None = None
        self._callback_error: Exception | None = None
        # Keep this object alive for as long as the native capture can call it.
        self._retrieve_callback = _CaptureRetrieveCallback(self._receive_frame)

        if not self.path.is_file():
            raise FileNotFoundError(f"Animation video is missing: {self.path}")
        if not self.plugin_path.is_file():
            raise FileNotFoundError(f"Video decoder is missing: {self.plugin_path}")
        if os.name != "nt" or ctypes.sizeof(ctypes.c_void_p) != 8:
            raise VideoDecodeError("The bundled video decoder requires 64-bit Windows.")

        self._load_plugin()
        try:
            self._open()
            width = self._integer_property(_CAP_PROP_FRAME_WIDTH, "width", positive=True)
            height = self._integer_property(_CAP_PROP_FRAME_HEIGHT, "height", positive=True)
            fps = self._property(_CAP_PROP_FPS, "frame rate")
            if fps <= 0:
                raise VideoDecodeError(f"Invalid video frame rate for {self.path}: {fps}")
            frame_count = self._integer_property(
                _CAP_PROP_FRAME_COUNT, "frame count", positive=True)
            self.info = VideoInfo(width, height, fps, frame_count)
        except BaseException:
            self._release_noexcept()
            raise

    @property
    def closed(self) -> bool:
        return not bool(self._handle.value)

    @property
    def next_frame_index(self) -> int:
        return self._next_frame_index

    @property
    def plugin_description(self) -> str:
        api = self._require_api()
        value = api.header.api_description or b""
        return value.decode("utf-8", errors="replace")

    @property
    def plugin_version(self) -> tuple[int, int, int]:
        header = self._require_api().header
        return (header.opencv_version_major,
                header.opencv_version_minor,
                header.opencv_version_patch)

    def _load_plugin(self) -> None:
        # Keep one native library reference per path for the whole application.
        # Switching actions only replaces the capture handle, never reloads DLLs.
        with _PLUGIN_LOCK:
            cached = _PLUGINS.get(self.plugin_path)
            if cached is None:
                self._initialize_plugin()
                cached = (self._dll, self._api_pointer, self._api)
                _PLUGINS[self.plugin_path] = cached
            self._dll, self._api_pointer, self._api = cached

    def _initialize_plugin(self) -> None:
        directory_handle = None
        try:
            if hasattr(os, "add_dll_directory"):
                directory_handle = os.add_dll_directory(str(self.plugin_path.parent))
            self._dll = ctypes.CDLL(str(self.plugin_path))
        except OSError as exc:
            raise VideoDecodeError(
                f"Could not load the bundled video decoder {self.plugin_path}: {exc}") from exc
        finally:
            if directory_handle is not None:
                directory_handle.close()

        try:
            initialize = self._dll.opencv_videoio_capture_plugin_init_v1
        except AttributeError as exc:
            raise VideoDecodeError(
                "The bundled decoder does not expose the OpenCV VideoIO capture ABI.") from exc
        initialize.argtypes = [ctypes.c_int, ctypes.c_int, ctypes.c_void_p]
        initialize.restype = ctypes.POINTER(_CapturePluginApiV0)
        api_pointer = initialize(_CAPTURE_ABI_VERSION, _CAPTURE_API_VERSION, None)
        if not api_pointer:
            raise VideoDecodeError("The bundled decoder rejected VideoIO ABI 1/API 0.")
        api = api_pointer.contents
        if api.header.valid_size < ctypes.sizeof(_CapturePluginApiV0):
            raise VideoDecodeError("The bundled decoder exposes an incomplete VideoIO API.")
        for name in ("capture_open", "capture_release", "capture_get", "capture_set",
                     "capture_grab", "capture_retrieve"):
            if not bool(getattr(api, name)):
                raise VideoDecodeError(f"The bundled decoder is missing {name}.")
        self._api_pointer = api_pointer
        self._api = api

    def _open(self) -> None:
        api = self._require_api()
        handle = ctypes.c_void_p()
        result = api.capture_open(
            str(self.path).encode("utf-8"), 0, ctypes.byref(handle))
        if result != _CV_ERROR_OK or not handle.value:
            raise VideoDecodeError(f"Could not open animation video: {self.path}")
        self._handle = handle

    def _require_api(self) -> _CapturePluginApiV0:
        if self._api is None:
            raise VideoDecodeError("The video decoder is not initialized.")
        return self._api

    def _require_handle(self) -> int:
        # Pass the integer value to ctypes. Passing the c_void_p object itself to
        # this plugin's release entry can crash the process on Windows.
        value = self._handle.value
        if value is None:
            raise VideoDecodeError("The animation video is closed.")
        return value

    def _property(self, property_id: int, label: str) -> float:
        value = ctypes.c_double()
        result = self._require_api().capture_get(
            self._require_handle(), property_id, ctypes.byref(value))
        if result != _CV_ERROR_OK or not math.isfinite(value.value):
            raise VideoDecodeError(f"Could not read video {label}: {self.path}")
        return value.value

    def _integer_property(self, property_id: int, label: str, *, positive: bool) -> int:
        raw = self._property(property_id, label)
        value = int(round(raw))
        if abs(raw - value) > 0.01 or (positive and value <= 0):
            raise VideoDecodeError(f"Invalid video {label} for {self.path}: {raw}")
        return value

    def _receive_frame(self, stream_index: int,
                       data: ctypes.POINTER(ctypes.c_ubyte), step: int,
                       width: int, height: int, image_type: int,
                       _userdata: ctypes.c_void_p) -> int:
        try:
            if stream_index != 0 or not data or width <= 0 or height <= 0:
                raise VideoDecodeError("The decoder returned an invalid video frame.")
            api = self._require_api()
            channel_shift = 5 if api.header.opencv_version_major >= 5 else 3
            depth_mask = (1 << channel_shift) - 1
            depth = image_type & depth_mask
            channels = (image_type >> channel_shift) + 1
            if depth != 0 or channels != 3 or step < width * channels:
                raise VideoDecodeError(
                    f"Unsupported decoded pixel format: type={image_type}, step={step}.")
            copied = ctypes.string_at(data, step * height)
            self._callback_frame = (copied, step, width, height, channels)
            return _CV_ERROR_OK
        except Exception as exc:
            self._callback_error = exc
            return -1

    def read(self) -> Image.Image | None:
        """Return the next decoded BGR-converted RGB frame, or ``None`` at EOF."""

        with self._lock:
            handle = self._require_handle()
            if self._next_frame_index >= self.info.frame_count:
                return None
            self._callback_frame = None
            self._callback_error = None
            if self._require_api().capture_grab(handle) != _CV_ERROR_OK:
                raise VideoDecodeError(
                    f"Could not decode frame {self._next_frame_index} from {self.path}")
            result = self._require_api().capture_retrieve(
                handle, 0, self._retrieve_callback, None)
            if self._callback_error is not None:
                raise VideoDecodeError(str(self._callback_error)) from self._callback_error
            if result != _CV_ERROR_OK or self._callback_frame is None:
                raise VideoDecodeError(
                    f"Could not retrieve frame {self._next_frame_index} from {self.path}")
            raw, step, width, height, _channels = self._callback_frame
            self._callback_frame = None
            try:
                frame = Image.frombytes("RGB", (width, height), raw, "raw", "BGR", step, 1)
            except (TypeError, ValueError) as exc:
                raise VideoDecodeError(
                    f"Could not copy frame {self._next_frame_index} from {self.path}") from exc
            self._next_frame_index += 1
            return frame

    def seek(self, frame_index: int) -> None:
        """Position the next read at a zero-based frame index."""

        if isinstance(frame_index, bool) or not isinstance(frame_index, int):
            raise TypeError("frame_index must be an integer")
        if not 0 <= frame_index <= self.info.frame_count:
            raise IndexError("video frame index out of range")
        with self._lock:
            if frame_index == self._next_frame_index:
                return
            result = self._require_api().capture_set(
                self._require_handle(), _CAP_PROP_POS_FRAMES, float(frame_index))
            if result != _CV_ERROR_OK:
                raise VideoDecodeError(
                    f"Could not seek to frame {frame_index} in {self.path}")
            self._next_frame_index = frame_index

    def close(self) -> None:
        """Release the native capture handle. Safe to call more than once."""

        with self._lock:
            handle = self._handle.value
            if handle is None:
                return
            self._handle = ctypes.c_void_p()
            result = self._require_api().capture_release(handle)
            if result != _CV_ERROR_OK:
                raise VideoDecodeError(f"Could not release animation video: {self.path}")

    def _release_noexcept(self) -> None:
        try:
            self.close()
        except Exception:
            pass

    def __enter__(self) -> OpenCVVideoDecoder:
        return self

    def __exit__(self, _exc_type: object, _exc: object, _traceback: object) -> None:
        self.close()
    def __del__(self) -> None:
        self._release_noexcept()


class PackedAlphaVideo:
    """Read side-by-side colour/alpha MP4 frames as Pillow RGBA images."""

    def __init__(self, video_path: str | Path,
                 plugin_path: str | Path | None = None,
                 expected_size: tuple[int, int] | None = None) -> None:
        self._decoder = OpenCVVideoDecoder(video_path, plugin_path)
        try:
            packed_width = self._decoder.info.width
            height = self._decoder.info.height
            if packed_width % 2:
                raise VideoDecodeError(
                    f"Packed animation width must be even: {self._decoder.path}")
            self.size = (packed_width // 2, height)
            if expected_size is not None and self.size != tuple(expected_size):
                raise VideoDecodeError(
                    f"Animation frame size is {self.size}; expected {tuple(expected_size)}.")
            self.fps = self._decoder.info.fps
            self.frame_count = self._decoder.info.frame_count
        except BaseException:
            self._decoder._release_noexcept()
            raise

    @property
    def next_frame_index(self) -> int:
        return self._decoder.next_frame_index

    @property
    def closed(self) -> bool:
        return self._decoder.closed

    def read(self) -> Image.Image | None:
        """Return the next RGBA frame, or ``None`` after the final frame."""

        packed = self._decoder.read()
        if packed is None:
            return None
        width, height = self.size
        colour = packed.crop((0, 0, width, height))
        alpha = packed.crop((width, 0, width * 2, height)).convert("L")
        # Lossy H.264 can turn nominal 0/255 matte pixels into near-black or
        # near-white values. Clamping only those endpoints avoids a faint box
        # without changing the antialiased edge.
        alpha = alpha.point(_ALPHA_LUT)
        colour.putalpha(alpha)
        return colour

    def seek(self, frame_index: int) -> None:
        self._decoder.seek(frame_index)

    def frame(self, frame_index: int) -> Image.Image:
        """Return one zero-based frame, seeking only when needed."""

        if isinstance(frame_index, bool) or not isinstance(frame_index, int):
            raise TypeError("frame_index must be an integer")
        if not 0 <= frame_index < self.frame_count:
            raise IndexError("video frame index out of range")
        if frame_index != self.next_frame_index:
            self.seek(frame_index)
        frame = self.read()
        if frame is None:
            raise VideoDecodeError(
                f"Animation ended before frame {frame_index}: {self._decoder.path}")
        return frame

    def close(self) -> None:
        self._decoder.close()

    def __enter__(self) -> PackedAlphaVideo:
        return self

    def __exit__(self, _exc_type: object, _exc: object, _traceback: object) -> None:
        self.close()
