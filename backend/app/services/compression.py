"""Measured compression benchmarking.

Every number reported here is measured by actually compressing the file
(in memory or into the separate outputs directory). Originals are never
overwritten. Lossy transformations are labelled and require approval before
an output copy is kept as part of an optimisation plan.
"""

from __future__ import annotations

import bz2
import gzip
import io
import lzma
import time
import zipfile
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np
import zstandard
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.db.models import CompressionResult, FileRecord, StorageLocation
from app.services import runtime_settings

ALREADY_COMPRESSED = {
    ".jpg",
    ".jpeg",
    ".png",
    ".webp",
    ".mp4",
    ".m4v",
    ".mov",
    ".mkv",
    ".webm",
    ".avi",
    ".zip",
    ".docx",
    ".gz",
    ".zst",
    ".7z",
    ".rar",
}
LOSSLESS_TEXT_EXTS = {".txt", ".md", ".markdown", ".csv", ".json", ".pdf", ".log", ".xml", ".html", ".svg"}
MAX_BENCH_BYTES = 200 * 1024 * 1024
SAMPLE_BYTES = 1024 * 1024


@dataclass
class Measurement:
    method: str
    lossless: bool
    original: int
    output: int | None
    duration_ms: float | None
    status: str = "measured"
    quality_note: str | None = None
    quality_metric: dict = field(default_factory=dict)
    restore: str | None = None
    payload: bytes | None = None
    extension: str = ""


def _time(fn):
    start = time.perf_counter()
    out = fn()
    return out, (time.perf_counter() - start) * 1000


def _zip_bytes(name: str, data: bytes) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=9) as zf:
        zf.writestr(name, data)
    return buf.getvalue()


def lossless_benchmarks(name: str, data: bytes) -> list[Measurement]:
    codecs = [
        ("gzip-9", lambda d: gzip.compress(d, compresslevel=9, mtime=0), ".gz", "gunzip / gzip -d"),
        ("zip-deflate-9", lambda d: _zip_bytes(name, d), ".zip", "unzip"),
        ("bzip2-9", lambda d: bz2.compress(d, 9), ".bz2", "bunzip2"),
        ("xz-6", lambda d: lzma.compress(d, preset=6), ".xz", "unxz / xz -d"),
        ("zstd-19", lambda d: zstandard.ZstdCompressor(level=19).compress(d), ".zst", "zstd -d"),
    ]
    results = []
    for method, fn, ext, restore in codecs:
        out, ms = _time(lambda fn=fn: fn(data))
        results.append(
            Measurement(
                method,
                True,
                len(data),
                len(out),
                round(ms, 2),
                quality_note="Lossless: decompression restores the exact original bytes.",
                restore=f"{restore} (byte-identical; verify with SHA-256)",
                payload=out,
                extension=ext,
            )
        )
    # Verify one round-trip so "lossless" is checked, not assumed.
    decoders = {
        "gzip-9": gzip.decompress,
        "bzip2-9": bz2.decompress,
        "xz-6": lzma.decompress,
        "zstd-19": lambda b: zstandard.ZstdDecompressor().decompress(b),
        "zip-deflate-9": lambda b: zipfile.ZipFile(io.BytesIO(b)).read(name),
    }
    best = min(results, key=lambda m: m.output or 0)
    if decoders[best.method](best.payload) != data:
        raise RuntimeError(f"Round-trip verification failed for {best.method}")
    best.quality_metric = {"round_trip_verified": True}
    return results


def _psnr(a, b) -> float | None:
    arr_a = np.asarray(a.convert("RGB"), dtype=np.float64)
    arr_b = np.asarray(b.convert("RGB"), dtype=np.float64)
    if arr_a.shape != arr_b.shape:
        return None
    mse = float(np.mean((arr_a - arr_b) ** 2))
    if mse == 0:
        return float("inf")
    return round(float(20 * np.log10(255.0) - 10 * np.log10(mse)), 2)


def image_benchmarks(ext: str, data: bytes) -> list[Measurement]:
    from PIL import Image

    results: list[Measurement] = []
    with Image.open(io.BytesIO(data)) as img:
        img.load()
        has_alpha = img.mode in ("RGBA", "LA") or "transparency" in img.info
        base = img.copy()

    def encode(fmt: str, **kwargs) -> bytes:
        buf = io.BytesIO()
        src = base
        if fmt == "JPEG" and src.mode not in ("RGB", "L"):
            src = src.convert("RGB")
        src.save(buf, format=fmt, **kwargs)
        return buf.getvalue()

    candidates: list[tuple[str, bool, str, dict, str]] = []
    if ext == ".png":
        candidates += [
            ("png-optimize", True, "PNG", {"optimize": True}, ".png"),
            ("webp-lossless", True, "WEBP", {"lossless": True, "method": 6}, ".webp"),
            ("webp-q80", False, "WEBP", {"quality": 80, "method": 6}, ".webp"),
        ]
        if not has_alpha:
            candidates.append(("jpeg-q85", False, "JPEG", {"quality": 85, "optimize": True, "progressive": True}, ".jpg"))
    elif ext in {".jpg", ".jpeg"}:
        candidates += [
            ("jpeg-q85-progressive", False, "JPEG", {"quality": 85, "optimize": True, "progressive": True}, ".jpg"),
            ("webp-q80", False, "WEBP", {"quality": 80, "method": 6}, ".webp"),
        ]
    elif ext == ".webp":
        candidates += [("webp-q75", False, "WEBP", {"quality": 75, "method": 6}, ".webp")]
    for method, lossless, fmt, kwargs, out_ext in candidates:
        try:
            out, ms = _time(lambda fmt=fmt, kwargs=kwargs: encode(fmt, **kwargs))
        except OSError as exc:
            results.append(Measurement(method, lossless, len(data), None, None, "error", quality_note=str(exc)))
            continue
        with Image.open(io.BytesIO(out)) as decoded:
            decoded.load()
            psnr = _psnr(base, decoded)
        if lossless:
            note = "Lossless re-encoding: pixel data is identical (PSNR = infinite)."
            if psnr not in (None, float("inf")):
                note = f"Expected lossless but PSNR = {psnr} dB; treat as lossy."
                lossless = False
        else:
            note = f"Lossy: PSNR {psnr} dB vs original (>40 dB is usually visually indistinguishable; <30 dB visible artefacts)."
        restore = (
            "Re-encode to the original format (pixels identical)"
            if lossless
            else "Not restorable: keep the original if exact fidelity is required"
        )
        if "jpeg" in method and has_alpha:
            note += " Transparency would be lost."
        results.append(
            Measurement(
                method,
                lossless,
                len(data),
                len(out),
                round(ms, 2),
                quality_note=note,
                quality_metric={"psnr_db": None if psnr == float("inf") else psnr, "pixel_identical": bool(psnr == float("inf"))},
                restore=restore,
                payload=out,
                extension=out_ext,
            )
        )
    return results


def video_recommendation(record: FileRecord, reason: str) -> list[Measurement]:
    """Metadata-based estimate, used only when a measured transcode is not possible."""
    meta = record.metadata_json or {}
    bitrate = meta.get("bitrate_kbps")
    height = meta.get("height") or 0
    note = reason
    if bitrate and height:
        target = 2500 if height <= 720 else 5000 if height <= 1080 else 16000
        if bitrate > target * 1.3:
            note = (
                f"Bitrate {bitrate} kbps is high for {height}p (typical H.265 target ≈ {target} kbps). "
                f"Re-encoding could save an estimated {round(100 * (1 - target / bitrate))}% (estimate, not measured). " + note
            )
        else:
            note = f"Bitrate {bitrate} kbps is already reasonable for {height}p; little expected gain. " + note
    return [
        Measurement(
            "video-transcode-estimate",
            False,
            record.size_bytes,
            None,
            None,
            status="recommended",
            quality_note=note,
            restore="Lossy; keep the original master if fidelity matters",
        )
    ]


def video_benchmark(record: FileRecord, path: Path) -> list[Measurement]:
    """Measured lossy transcode (ffmpeg) with PSNR against the original."""
    from app.services import video

    result = video.measure(path)
    if result.psnr_db is None:
        quality = "PSNR could not be computed."
    else:
        quality = f"PSNR {result.psnr_db} dB vs original (>40 dB is usually visually transparent; <30 dB visible artefacts)."
    return [
        Measurement(
            result.method,
            False,
            record.size_bytes,
            result.output_bytes,
            result.duration_ms,
            quality_note=f"Lossy re-encode measured with ffmpeg. {quality}",
            quality_metric={"psnr_db": result.psnr_db, "codec": result.method},
            restore="Not restorable: keep the original master if exact fidelity is required",
            extension=".mp4",
        )
    ]


def _local_path(db: Session, record: FileRecord) -> Path | None:
    location = db.get(StorageLocation, record.location_id)
    if location.kind != "local":
        return None
    from app.core.paths import safe_join

    return safe_join(Path(location.path), record.rel_path)


def _resolve_source(db: Session, record: FileRecord) -> bytes:
    location = db.get(StorageLocation, record.location_id)
    if location.kind == "local":
        from app.core.paths import safe_join

        path = safe_join(Path(location.path), record.rel_path)
        return path.read_bytes()
    from app.services import s3_service

    return s3_service.download_object(location.bucket, record.rel_path, MAX_BENCH_BYTES)


def _video_measurements(db: Session, record: FileRecord) -> list[Measurement]:
    from app.services import tools, video

    settings = get_settings()
    path = _local_path(db, record)
    if path is None:
        return video_recommendation(record, "S3 videos are not downloaded for transcoding.")
    if tools.ffmpeg_path() is None or video.method_name() is None:
        return video_recommendation(record, "Install ffmpeg to measure a real transcode.")
    if record.size_bytes > settings.video_transcode_max_mb * 1024 * 1024:
        return video_recommendation(record, f"Larger than VIDEO_TRANSCODE_MAX_MB ({settings.video_transcode_max_mb} MB).")
    try:
        return video_benchmark(record, path)
    except Exception as exc:  # noqa: BLE001 - undecodable videos are reported, not fatal
        return [Measurement("video-transcode", False, record.size_bytes, None, None, "error", quality_note=str(exc)[:400])]


def analyze_file(db: Session, record: FileRecord) -> list[CompressionResult]:
    """Benchmark compression methods for one file and persist the measurements."""
    min_saving = float(runtime_settings.get(db, "compression_min_saving_percent"))
    db.query(CompressionResult).filter(CompressionResult.file_id == record.id).delete()
    measurements: list[Measurement] = []
    ext = record.extension
    if record.status == "missing":
        raise ValueError("File no longer exists at its location.")
    if record.size_bytes == 0:
        measurements.append(Measurement("none", True, 0, None, None, "skipped", quality_note="Empty file."))
    elif record.category == "video":
        measurements = _video_measurements(db, record)
    elif record.size_bytes > MAX_BENCH_BYTES:
        measurements.append(
            Measurement(
                "none", True, record.size_bytes, None, None, "skipped", quality_note="File too large to benchmark in-process."
            )
        )
    else:
        try:
            data = _resolve_source(db, record)
            if record.category == "image" and ext in {".png", ".jpg", ".jpeg", ".webp"}:
                measurements = image_benchmarks(ext, data)
            elif ext in ALREADY_COMPRESSED:
                sample = data[:SAMPLE_BYTES]
                out = zstandard.ZstdCompressor(level=3).compress(sample)
                saving = 100 * (1 - len(out) / max(len(sample), 1))
                measurements.append(
                    Measurement(
                        "zstd-3-sample-probe",
                        True,
                        len(sample),
                        len(out),
                        None,
                        "skipped",
                        quality_note=f"Format is already compressed; a probe on the first {len(sample)} bytes saved {saving:.1f}%. Full benchmark skipped.",
                    )
                )
            else:
                measurements = lossless_benchmarks(record.name, data)
        except Exception as exc:  # noqa: BLE001
            measurements = [
                Measurement("none", True, record.size_bytes, None, None, "error", quality_note=f"{exc.__class__.__name__}: {exc}")
            ]

    measured = [m for m in measurements if m.status == "measured" and m.output is not None]
    best = min(measured, key=lambda m: m.output) if measured else None
    if best and 100 * (1 - best.output / best.original) < min_saving:
        best.quality_note = (best.quality_note or "") + f" Saving below the {min_saving}% threshold: not recommended."
        best = None
    results = []
    for m in measurements:
        results.append(
            CompressionResult(
                file_id=record.id,
                method=m.method,
                lossless=m.lossless,
                status=m.status,
                original_bytes=m.original,
                output_bytes=m.output,
                duration_ms=m.duration_ms,
                quality_note=m.quality_note,
                quality_metric=m.quality_metric,
                restore_method=m.restore,
                is_best=m is best,
            )
        )
    db.add_all(results)
    db.commit()
    return results


def write_output_copy(db: Session, result: CompressionResult) -> Path:
    """Re-run the chosen method and store the output in the separate outputs directory."""
    record = db.get(FileRecord, result.file_id)
    out_dir = get_settings().outputs_dir / "compression" / str(record.id)
    out_dir.mkdir(parents=True, exist_ok=True)
    if record.category == "video":
        from app.services import video

        source = _local_path(db, record)
        if source is None:
            raise ValueError("Only local videos can be transcoded.")
        path = out_dir / f"{Path(record.name).stem}.{result.method}.mp4"
        video.transcode(source, path)
        result.output_path = str(path)
        result.output_bytes = path.stat().st_size
        db.commit()
        return path
    data = _resolve_source(db, record)
    if record.category == "image":
        measurements = image_benchmarks(record.extension, data)
    else:
        measurements = lossless_benchmarks(record.name, data)
    chosen = next((m for m in measurements if m.method == result.method), None)
    if chosen is None or chosen.payload is None:
        raise ValueError(f"Method {result.method} is not reproducible for this file.")
    stem = Path(record.name).stem if record.category == "image" else record.name
    path = out_dir / f"{stem}.{result.method}{chosen.extension}"
    path.write_bytes(chosen.payload)
    result.output_path = str(path)
    result.output_bytes = path.stat().st_size
    db.commit()
    return path


def summary(db: Session) -> dict:
    rows = db.query(CompressionResult).filter(CompressionResult.is_best.is_(True)).all()
    original = sum(r.original_bytes for r in rows)
    output = sum(r.output_bytes or 0 for r in rows)
    return {
        "files_with_recommendation": len(rows),
        "lossless_recommendations": sum(1 for r in rows if r.lossless),
        "lossy_recommendations": sum(1 for r in rows if not r.lossless),
        "original_bytes": original,
        "output_bytes": output,
        "bytes_saved": original - output,
        "files_benchmarked": db.query(CompressionResult.file_id).distinct().count(),
    }
