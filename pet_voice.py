"""Windows-native dictation; speech output has been removed."""

from __future__ import annotations

import shutil
import subprocess
import threading
from collections.abc import Callable


_POWERSHELL = shutil.which("powershell.exe") or shutil.which("powershell") or "powershell.exe"
_NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)

_RECOGNIZE_SCRIPT = r"""
$ErrorActionPreference = 'Stop'
[Console]::OutputEncoding = New-Object -TypeName System.Text.UTF8Encoding -ArgumentList $false
try {
    Add-Type -AssemblyName System.Speech
    $culture = [System.Globalization.CultureInfo]::GetCultureInfo('zh-CN')
    $recognizer = New-Object -TypeName System.Speech.Recognition.SpeechRecognitionEngine -ArgumentList $culture
    $recognizer.LoadGrammar((New-Object System.Speech.Recognition.DictationGrammar))
    $recognizer.SetInputToDefaultAudioDevice()
    $recognizer.InitialSilenceTimeout = [TimeSpan]::FromSeconds(6)
    $recognizer.EndSilenceTimeout = [TimeSpan]::FromSeconds(1.2)
    $result = $recognizer.Recognize([TimeSpan]::FromSeconds(15))
    $recognizer.Dispose()
    if ($result -and $result.Text) {
        [Console]::WriteLine($result.Text)
        exit 0
    }
    [Console]::Error.WriteLine('NO_SPEECH')
    exit 3
} catch {
    [Console]::Error.WriteLine($_.Exception.Message)
    exit 4
}
"""


class WindowsSpeechInput:
    """Capture one Chinese dictation through the built-in Windows recognizer."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._cancelled = threading.Event()
        self._process: subprocess.Popen[bytes] | None = None
        self._thread: threading.Thread | None = None
        self._listening = False

    @property
    def listening(self) -> bool:
        with self._lock:
            return self._listening

    def start(self, callback: Callable[[str | None, str | None], None]) -> bool:
        with self._lock:
            if self._listening:
                return False
            self._listening = True
            self._cancelled.clear()
            self._thread = threading.Thread(
                target=self._listen, args=(callback,), name="xiongda-dictation", daemon=True,
            )
            self._thread.start()
        return True

    def stop(self) -> None:
        self._cancelled.set()
        with self._lock:
            process = self._process
            self._listening = False
        if process is not None and process.poll() is None:
            process.terminate()

    @staticmethod
    def _friendly_error(stderr: bytes, returncode: int) -> str:
        detail = stderr.decode("utf-8", errors="replace").lower()
        if "no_speech" in detail or returncode == 3:
            return "没听清，点麦克风再试一次。"
        if "recognizer" in detail or "culture" in detail:
            return "没有找到中文语音识别器，请在 Windows 语音设置中安装中文语音识别。"
        if any(word in detail for word in ("audio device", "microphone", "input device", "麦克风")):
            return "无法使用麦克风，请检查 Windows 麦克风权限和输入设备。"
        return "语音识别没有启动成功，请检查 Windows 语音识别和麦克风设置。"

    def _listen(self, callback: Callable[[str | None, str | None], None]) -> None:
        process: subprocess.Popen[bytes] | None = None
        result: str | None = None
        error: str | None = None
        try:
            process = subprocess.Popen(
                [_POWERSHELL, "-NoLogo", "-NoProfile", "-NonInteractive",
                 "-Command", _RECOGNIZE_SCRIPT],
                stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                creationflags=_NO_WINDOW,
            )
            with self._lock:
                self._process = process
            if self._cancelled.is_set():
                process.terminate()
            try:
                output, stderr = process.communicate(timeout=22)
            except subprocess.TimeoutExpired:
                process.terminate()
                output, stderr = process.communicate(timeout=3)
                error = "识别时间到了，点麦克风再试一次。"
            if not self._cancelled.is_set() and error is None:
                result = output.decode("utf-8-sig", errors="replace").strip()
                if process.returncode != 0 or not result:
                    error = self._friendly_error(stderr, process.returncode or 1)
                    result = None
        except OSError:
            error = "找不到 Windows PowerShell，暂时无法启动语音识别。"
        finally:
            with self._lock:
                self._process = None
                self._listening = False
        if not self._cancelled.is_set():
            callback(result, error)
