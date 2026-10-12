"""Video metadata (ffprobe) and measured transcoding (ffmpeg).

Only fixed argument lists are passed to the binaries (no shell), and input
paths always come from validated locations. Transcoding is lossy: outputs
are written to a temporary file for measurement, or to the outputs folder
when an approved plan action keeps a copy. Originals are never modified.
"""

from __future__ import annotations

import json
import re
import subprocess
import tempfile
import time
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Any

from app.core.config import get_settings
from app.services.tools import ffmpeg_path, ffprobe_path


class VideoToolUnavailable(RuntimeError):
    pass


def probe(path: Path) -> dict[str, Any] | None:
    """Container/stream metadata via ffprobe, or None when ffprobe is unavailable or the file is not decodable."""
    exe = ffprobe_path()
    if exe is None:
        return None
    try:
        out = subprocess.run(
            [exe, "-v", "error", "-print_format", "json", "-show_format", "-show_streams", str(path)],
            capture_output=True,
            text=True,
            timeout=60,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    if out.returncode != 0:
        return None
    data = json.loads(out.stdout or "{}")
    fmt = data.get("format", {})
    video = next((s for s in data.get("streams", []) if s.get("codec_type") == "video"), None)
    audio = next((s for s in data.get("streams", []) if s.get("codec_type") == "audio"), None)
    if video is None:
        return None
    meta: dict[str, Any] = {"probe": "ffprobe", "container": fmt.get("format_name"), "tracks": len(data.get("streams", []))}
    if fmt.get("duration"):
        meta["duration_seconds"] = round(float(fmt["duration"]), 3)
    if fmt.get("bit_rate"):
        meta["bitrate_kbps"] = round(int(fmt["bit_rate"]) / 1000, 1)
    meta.update({"width": video.get("width"), "height": video.get("height"), "video_codec": video.get("codec_name")})
    if video.get("avg_frame_rate") and "/" in video["avg_frame_rate"]:
        num, den = video["avg_frame_rate"].split("/")
        if float(den):
            meta["fps"] = round(float(num) / float(den), 2)
    if audio:
        meta["audio_codec"] = audio.get("codec_name")
    return meta


@lru_cache
def _encoders() -> str:
    exe = ffmpeg_path()
    if exe is None:
        return ""
    try:
        return subprocess.run([exe, "-hide_banner", "-encoders"], capture_output=True, text=True, timeout=30).stdout
    except (OSError, subprocess.SubprocessError):
        return ""


@dataclass
class TranscodeResult:
    method: str
    output_bytes: int
    duration_ms: float
    psnr_db: float | None


def _codec_args() -> tuple[str, list[str]]:
    if "libx265" in _encoders():
        return "h265-crf28", ["-c:v", "libx265", "-crf", "28", "-preset", "medium", "-tag:v", "hvc1"]
    if "libx264" in _encoders():
        return "h264-crf26", ["-c:v", "libx264", "-crf", "26", "-preset", "medium"]
    raise VideoToolUnavailable("ffmpeg has neither libx265 nor libx264.")


def method_name() -> str | None:
    try:
        return _codec_args()[0]
    except VideoToolUnavailable:
        return None


def transcode(source: Path, destination: Path) -> TranscodeResult:
    exe = ffmpeg_path()
    if exe is None:
        raise VideoToolUnavailable("ffmpeg is not installed.")
    method, codec = _codec_args()
    cmd = [
        exe,
        "-hide_banner",
        "-loglevel",
        "error",
        "-y",
        "-i",
        str(source),
        *codec,
        "-c:a",
        "aac",
        "-b:a",
        "128k",
        "-movflags",
        "+faststart",
        str(destination),
    ]
    start = time.perf_counter()
    try:
        run = subprocess.run(cmd, capture_output=True, text=True, timeout=get_settings().video_transcode_timeout_s)
    except subprocess.TimeoutExpired as exc:
        destination.unlink(missing_ok=True)
        raise RuntimeError("Transcoding timed out.") from exc
    elapsed = (time.perf_counter() - start) * 1000
    if run.returncode != 0 or not destination.exists():
        destination.unlink(missing_ok=True)
        raise RuntimeError(f"ffmpeg could not decode/encode this file: {run.stderr.strip()[-300:]}")
    return TranscodeResult(method, destination.stat().st_size, round(elapsed, 1), psnr(source, destination))


def psnr(original: Path, encoded: Path) -> float | None:
    """Average PSNR (dB) of the encoded video against the original, via ffmpeg's psnr filter."""
    exe = ffmpeg_path()
    if exe is None:
        return None
    cmd = [exe, "-hide_banner", "-i", str(encoded), "-i", str(original), "-lavfi", "[0:v][1:v]psnr", "-f", "null", "-"]
    try:
        run = subprocess.run(cmd, capture_output=True, text=True, timeout=get_settings().video_transcode_timeout_s)
    except (OSError, subprocess.SubprocessError):
        return None
    match = re.search(r"average:([0-9.]+|inf)", run.stderr)
    if not match or match.group(1) == "inf":
        return None
    return round(float(match.group(1)), 2)


def measure(source: Path) -> TranscodeResult:
    """Transcode into a temporary file, measure it, and delete it."""
    with tempfile.TemporaryDirectory(prefix="optistore-video-") as tmp:
        return transcode(source, Path(tmp) / "measure.mp4")
