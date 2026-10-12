"""File type detection and safe content/metadata extraction.

All extractors treat file content as untrusted data: they never execute
anything, never extract archives to disk, and cap the amount of text read.
"""

from __future__ import annotations

import csv
import io
import json
import mimetypes
import struct
import zipfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

# extension -> (category, mime)
SUPPORTED_TYPES: dict[str, tuple[str, str]] = {
    ".pdf": ("document", "application/pdf"),
    ".docx": ("document", "application/vnd.openxmlformats-officedocument.wordprocessingml.document"),
    ".txt": ("document", "text/plain"),
    ".md": ("document", "text/markdown"),
    ".markdown": ("document", "text/markdown"),
    ".csv": ("data", "text/csv"),
    ".json": ("data", "application/json"),
    ".jpg": ("image", "image/jpeg"),
    ".jpeg": ("image", "image/jpeg"),
    ".png": ("image", "image/png"),
    ".webp": ("image", "image/webp"),
    ".mp4": ("video", "video/mp4"),
    ".m4v": ("video", "video/x-m4v"),
    ".mov": ("video", "video/quicktime"),
    ".avi": ("video", "video/x-msvideo"),
    ".mkv": ("video", "video/x-matroska"),
    ".webm": ("video", "video/webm"),
    ".zip": ("archive", "application/zip"),
}

TEXT_EXTRACTABLE = {".pdf", ".docx", ".txt", ".md", ".markdown", ".csv", ".json"}

# Magic-number prefixes used to cross-check the extension.
_MAGIC: dict[str, tuple[bytes, ...]] = {
    ".pdf": (b"%PDF",),
    ".png": (b"\x89PNG\r\n\x1a\n",),
    ".jpg": (b"\xff\xd8\xff",),
    ".jpeg": (b"\xff\xd8\xff",),
    ".zip": (b"PK\x03\x04", b"PK\x05\x06"),
    ".docx": (b"PK\x03\x04",),
}


@dataclass
class Extraction:
    text: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)
    warnings: list[str] = field(default_factory=list)


def classify(path_or_name: str) -> tuple[str, str, str, bool]:
    """Return ``(extension, category, mime_type, supported)`` for a filename."""
    ext = Path(path_or_name).suffix.lower()
    if ext in SUPPORTED_TYPES:
        category, mime = SUPPORTED_TYPES[ext]
        return ext, category, mime, True
    mime = mimetypes.guess_type(path_or_name)[0] or "application/octet-stream"
    return ext, "other", mime, False


def check_magic(ext: str, head: bytes) -> bool:
    """Return False when the file signature clearly contradicts the extension."""
    if ext == ".webp":
        return head[:4] == b"RIFF" and head[8:12] == b"WEBP"
    expected = _MAGIC.get(ext)
    if not expected:
        return True
    return any(head.startswith(sig) for sig in expected)


def _decode_text(data: bytes) -> str:
    for encoding in ("utf-8-sig", "utf-16"):
        try:
            text = data.decode(encoding)
            if encoding == "utf-16" and "\x00" in text:
                continue
            return text
        except UnicodeDecodeError:
            continue
    return data.decode("latin-1", errors="replace")


def extract_text_from_bytes(ext: str, data: bytes, max_chars: int) -> Extraction:
    """Extract text + metadata from in-memory content (used for local files and S3 objects)."""
    result = Extraction()
    if ext in {".txt", ".md", ".markdown"}:
        text = _decode_text(data)
        result.metadata["lines"] = text.count("\n") + 1
        result.text = text
    elif ext == ".csv":
        text = _decode_text(data)
        try:
            sample = text[:20_000]
            dialect = csv.Sniffer().sniff(sample) if sample.strip() else csv.excel
            rows = list(csv.reader(io.StringIO(text), dialect))
        except csv.Error:
            rows = list(csv.reader(io.StringIO(text)))
        result.metadata["rows"] = max(len(rows) - 1, 0)
        result.metadata["columns"] = rows[0] if rows else []
        # Render "header: value" lines so facts in tables are searchable.
        header = rows[0] if rows else []
        lines = [", ".join(header)]
        for row in rows[1:]:
            lines.append("; ".join(f"{h}: {v}" for h, v in zip(header, row, strict=False)))
        result.text = "\n".join(lines)
    elif ext == ".json":
        text = _decode_text(data)
        try:
            parsed = json.loads(text)
            result.metadata["json_type"] = type(parsed).__name__
            if isinstance(parsed, dict):
                result.metadata["top_level_keys"] = list(parsed.keys())[:50]
            elif isinstance(parsed, list):
                result.metadata["items"] = len(parsed)
            result.text = "\n".join(_flatten_json(parsed))
        except json.JSONDecodeError as exc:
            result.warnings.append(f"Invalid JSON: {exc.msg}")
            result.text = text
    elif ext == ".pdf":
        from pypdf import PdfReader

        reader = PdfReader(io.BytesIO(data))
        if reader.is_encrypted:
            result.warnings.append("PDF is encrypted; text not extracted.")
            result.metadata["encrypted"] = True
            return result
        result.metadata["pages"] = len(reader.pages)
        info = reader.metadata or {}
        for key in ("/Title", "/Author", "/CreationDate"):
            if info.get(key):
                result.metadata[key.strip("/").lower()] = str(info.get(key))
        parts: list[str] = []
        total = 0
        for page in reader.pages:
            page_text = page.extract_text() or ""
            parts.append(page_text)
            total += len(page_text)
            if total > max_chars:
                break
        result.text = "\n".join(parts)
        if not result.text.strip():
            ocr_text = ocr_pdf(data, max_chars)
            if ocr_text is None:
                result.warnings.append("No extractable text (the PDF may be scanned images) and OCR is unavailable.")
            elif ocr_text.strip():
                result.text = ocr_text
                result.metadata["ocr"] = True
                result.warnings.append("Text obtained by OCR (Tesseract); may contain recognition errors.")
            else:
                result.warnings.append("No text found, even with OCR.")
    elif ext == ".docx":
        import docx

        document = docx.Document(io.BytesIO(data))
        paragraphs = [p.text for p in document.paragraphs]
        for table in document.tables:
            for row in table.rows:
                paragraphs.append(" | ".join(cell.text for cell in row.cells))
        result.text = "\n".join(paragraphs)
        props = document.core_properties
        result.metadata["paragraphs"] = len(document.paragraphs)
        if props.title:
            result.metadata["title"] = props.title
        if props.author:
            result.metadata["author"] = props.author
    if result.text is not None and len(result.text) > max_chars:
        result.text = result.text[:max_chars]
        result.warnings.append(f"Text truncated to {max_chars} characters.")
    return result


# --- OCR (optional, requires the Tesseract binary) ----------------------------------------------

OCR_TIMEOUT_S = 60
MIN_OCR_LETTERS = 20


def _tesseract():
    from app.services.tools import tesseract_path

    cmd = tesseract_path()
    if cmd is None:
        return None
    import pytesseract

    pytesseract.pytesseract.tesseract_cmd = cmd
    return pytesseract


def ocr_image(image) -> str | None:
    """OCR a PIL image. Returns None when Tesseract is unavailable."""
    from app.core.config import get_settings

    engine = _tesseract()
    if engine is None:
        return None
    return engine.image_to_string(image.convert("L"), lang=get_settings().ocr_languages, timeout=OCR_TIMEOUT_S)


def ocr_pdf(data: bytes, max_chars: int) -> str | None:
    """Render PDF pages with pdfium (~300 dpi) and OCR them. None when Tesseract is unavailable."""
    from app.core.config import get_settings

    if _tesseract() is None:
        return None
    import pypdfium2 as pdfium

    pdf = pdfium.PdfDocument(data)
    try:
        parts: list[str] = []
        for index in range(min(len(pdf), get_settings().ocr_max_pages)):
            bitmap = pdf[index].render(scale=300 / 72)
            parts.append(ocr_image(bitmap.to_pil()) or "")
            if sum(len(p) for p in parts) > max_chars:
                break
        return "\n".join(parts)
    finally:
        pdf.close()


def image_text(path: Path, max_chars: int) -> str | None:
    """Text visible in an image (screenshots, scanned pages); None if OCR is unavailable or nothing legible."""
    from PIL import Image

    with Image.open(path) as img:
        if img.width * img.height > 40_000_000:
            return None
        text = ocr_image(img)
    if text is None:
        return None
    text = text.strip()
    return text[:max_chars] if sum(ch.isalpha() for ch in text) >= MIN_OCR_LETTERS else None


def _flatten_json(value: Any, prefix: str = "", depth: int = 0) -> list[str]:
    if depth > 20:
        return []
    lines: list[str] = []
    if isinstance(value, dict):
        for key, child in value.items():
            path = f"{prefix}.{key}" if prefix else str(key)
            lines.extend(_flatten_json(child, path, depth + 1))
    elif isinstance(value, list):
        for idx, child in enumerate(value[:1000]):
            lines.extend(_flatten_json(child, f"{prefix}[{idx}]", depth + 1))
    else:
        label = prefix.split(".")[-1] if prefix else "value"
        lines.append(f"{label}: {value}")
    return lines


def image_metadata(data_or_path: bytes | Path) -> Extraction:
    from PIL import Image

    result = Extraction()
    source = io.BytesIO(data_or_path) if isinstance(data_or_path, bytes) else data_or_path
    with Image.open(source) as img:
        result.metadata.update(
            {
                "format": img.format,
                "width": img.width,
                "height": img.height,
                "mode": img.mode,
                "has_exif": bool(img.info.get("exif")),
                "animated": bool(getattr(img, "is_animated", False)),
            }
        )
    return result


def zip_metadata(path_or_bytes: Path | bytes) -> Extraction:
    """Inspect a ZIP's central directory without extracting anything."""
    result = Extraction()
    source = io.BytesIO(path_or_bytes) if isinstance(path_or_bytes, bytes) else path_or_bytes
    with zipfile.ZipFile(source) as zf:
        infos = zf.infolist()
        compressed = sum(i.compress_size for i in infos)
        uncompressed = sum(i.file_size for i in infos)
        result.metadata.update(
            {
                "entries": len(infos),
                "compressed_bytes": compressed,
                "uncompressed_bytes": uncompressed,
                "archive_ratio": round(uncompressed / compressed, 2) if compressed else None,
                "encrypted_entries": sum(1 for i in infos if i.flag_bits & 0x1),
                "sample_entries": [i.filename for i in infos[:20]],
            }
        )
        if compressed and uncompressed / compressed > 100:
            result.warnings.append("Very high expansion ratio; archive treated as potential zip bomb.")
    return result


# --- Minimal ISO-BMFF (MP4/MOV) metadata parser -------------------------------------------------

_CONTAINER_BOXES = {b"moov", b"trak", b"mdia", b"minf", b"stbl", b"edts", b"udta"}


def _iter_boxes(fh, start: int, end: int, depth: int = 0):
    pos = start
    while pos + 8 <= end and depth < 8:
        fh.seek(pos)
        header = fh.read(8)
        if len(header) < 8:
            return
        size, box_type = struct.unpack(">I4s", header)
        header_len = 8
        if size == 1:
            size = struct.unpack(">Q", fh.read(8))[0]
            header_len = 16
        elif size == 0:
            size = end - pos
        if size < header_len:
            return
        yield box_type, pos + header_len, pos + size
        pos += size


def mp4_metadata(path: Path) -> Extraction:
    """Read duration, dimensions and bitrate from the moov box (no decoding)."""
    result = Extraction()
    file_size = path.stat().st_size
    meta: dict[str, Any] = {}
    tracks: list[dict[str, Any]] = []
    with path.open("rb") as fh:

        def walk(start: int, end: int, depth: int, track: dict | None):
            for box_type, body_start, box_end in _iter_boxes(fh, start, end, depth):
                if box_type == b"ftyp":
                    fh.seek(body_start)
                    meta["brand"] = fh.read(4).decode("ascii", errors="replace")
                elif box_type == b"mvhd":
                    fh.seek(body_start)
                    version = fh.read(1)[0]
                    fh.read(3)
                    if version == 1:
                        fh.read(16)
                        timescale, duration = struct.unpack(">IQ", fh.read(12))
                    else:
                        fh.read(8)
                        timescale, duration = struct.unpack(">II", fh.read(8))
                    if timescale:
                        meta["duration_seconds"] = round(duration / timescale, 3)
                elif box_type == b"tkhd" and track is not None:
                    fh.seek(box_end - 8)
                    width, height = struct.unpack(">II", fh.read(8))
                    track["width"], track["height"] = width >> 16, height >> 16
                elif box_type == b"hdlr" and track is not None:
                    fh.seek(body_start + 8)
                    track["handler"] = fh.read(4).decode("ascii", errors="replace")
                elif box_type == b"trak":
                    new_track: dict[str, Any] = {}
                    tracks.append(new_track)
                    walk(body_start, box_end, depth + 1, new_track)
                elif box_type in _CONTAINER_BOXES:
                    walk(body_start, box_end, depth + 1, track)

        walk(0, file_size, 0, None)
    video = next((t for t in tracks if t.get("handler") == "vide" and t.get("width")), None)
    if video is None:
        video = next((t for t in tracks if t.get("width")), None)
    if video:
        meta["width"], meta["height"] = video["width"], video["height"]
    meta["tracks"] = len(tracks)
    if meta.get("duration_seconds"):
        meta["bitrate_kbps"] = round(file_size * 8 / meta["duration_seconds"] / 1000, 1)
    if not meta.get("duration_seconds"):
        result.warnings.append("Container duration not found; video metadata is partial.")
    result.metadata = meta
    return result
