"""Discovery of optional external tools (Tesseract OCR, ffmpeg/ffprobe).

Tools are looked up in this order: explicit setting, ``PATH``, then common
install locations (winget and installer defaults on Windows). The result is
cached; ``tool_status()`` feeds the health endpoint and the UI.
"""

from __future__ import annotations

import glob
import os
import shutil
import subprocess
from functools import lru_cache
from pathlib import Path

from app.core.config import get_settings

_WINDOWS_CANDIDATES = {
    "tesseract": [
        r"C:\Program Files\Tesseract-OCR\tesseract.exe",
        r"C:\Program Files (x86)\Tesseract-OCR\tesseract.exe",
        os.path.expandvars(r"%LOCALAPPDATA%\Programs\Tesseract-OCR\tesseract.exe"),
    ],
    "ffmpeg": [
        os.path.expandvars(r"%LOCALAPPDATA%\Microsoft\WinGet\Links\ffmpeg.exe"),
        os.path.expandvars(r"%LOCALAPPDATA%\Microsoft\WinGet\Packages\Gyan.FFmpeg*\*\bin\ffmpeg.exe"),
        r"C:\ffmpeg\bin\ffmpeg.exe",
    ],
    "ffprobe": [
        os.path.expandvars(r"%LOCALAPPDATA%\Microsoft\WinGet\Links\ffprobe.exe"),
        os.path.expandvars(r"%LOCALAPPDATA%\Microsoft\WinGet\Packages\Gyan.FFmpeg*\*\bin\ffprobe.exe"),
        r"C:\ffmpeg\bin\ffprobe.exe",
    ],
}


def _find(name: str, configured: str | None) -> str | None:
    if configured:
        return configured if Path(configured).exists() or shutil.which(configured) else None
    found = shutil.which(name)
    if found:
        return found
    if os.name == "nt":
        for pattern in _WINDOWS_CANDIDATES.get(name, []):
            matches = sorted(glob.glob(pattern))
            if matches:
                return matches[-1]
    return None


@lru_cache
def tesseract_path() -> str | None:
    settings = get_settings()
    if not settings.ocr_enabled:
        return None
    return _find("tesseract", settings.tesseract_cmd)


@lru_cache
def ffmpeg_path() -> str | None:
    return _find("ffmpeg", get_settings().ffmpeg_path)


@lru_cache
def ffprobe_path() -> str | None:
    return _find("ffprobe", get_settings().ffprobe_path)


def _version(path: str | None) -> str | None:
    if not path:
        return None
    try:
        out = subprocess.run(
            [path, "-version" if "ff" in Path(path).name else "--version"], capture_output=True, text=True, timeout=10
        )
        first = (out.stdout or out.stderr).strip().splitlines()
        return first[0][:120] if first else None
    except (OSError, subprocess.SubprocessError):
        return None


@lru_cache
def tool_status() -> dict[str, dict[str, str | bool | None]]:
    return {
        "tesseract": {"available": tesseract_path() is not None, "version": _version(tesseract_path())},
        "ffmpeg": {"available": ffmpeg_path() is not None, "version": _version(ffmpeg_path())},
        "ffprobe": {"available": ffprobe_path() is not None},
    }


def reset_cache() -> None:
    """Testing hook / re-detect after installing a tool."""
    for fn in (tesseract_path, ffmpeg_path, ffprobe_path, tool_status):
        fn.cache_clear()
