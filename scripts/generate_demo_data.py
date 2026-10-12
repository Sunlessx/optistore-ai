"""Generate the OptiStore AI demonstration dataset in ./sample_data.

The dataset is fictional and deterministic (fixed seeds). It contains:
* exact duplicates (identical bytes, different names/folders),
* semantically similar documents with shared, unique and conflicting facts,
* unrelated documents,
* files with very different compression characteristics,
* image, video-container, archive and malformed files.

Usage:  python scripts/generate_demo_data.py [--out sample_data]
Requires: pillow, python-docx, fpdf2, numpy (all in backend/requirements-dev.txt).
"""

from __future__ import annotations

import argparse
import csv
import io
import json
import random
import glob
import os
import shutil
import struct
import subprocess
import zipfile
from pathlib import Path

import numpy as np
from docx import Document
from fpdf import FPDF
from PIL import Image, ImageDraw

ALPHA_BRIEF = """# Project Alpha - Brief

Budget: $50,000
Deadline: March 15, 2026
Team size: 6
Project lead: Sarah Chen

Project Alpha will migrate the customer analytics platform to a managed cloud data warehouse.
The team will consolidate three legacy reporting databases into a single schema.
Weekly status meetings are held every Tuesday at 10:00.
The main risk is the availability of historical data from the 2019 archive.
"""

ALPHA_UPDATE = """Project Alpha - Status Update (October)

Budget: $50,000
Deadline: March 15, 2026
Team size: 6
Client: Northwind Traders
Project lead: Sarah Chen

Project Alpha will migrate the customer analytics platform to a managed cloud data warehouse.
The team will consolidate three legacy reporting databases into a single schema.
Northwind Traders approved the data-retention policy on October 2, 2025.
Weekly status meetings are held every Tuesday at 10:00.
"""

ALPHA_FINANCE = """Project Alpha - Finance Note

Budget: $65,000
Deadline: March 15, 2026
Cost center: CC-4410

Finance revised the forecast after the vendor quote for the data warehouse licence.
The team will consolidate three legacy reporting databases into a single schema.
Any spending above the approved budget requires sign-off from the CFO.
"""

MEETING_OCT = """# Meeting notes - October 14, 2025

Attendees: Sarah Chen, Omar Haddad, Lina Morel

1. The migration of the reporting databases is 40% complete.
2. Omar presented the new dashboard prototype for the customer success team.
3. Lina raised concerns about the backlog of customer complaints regarding late deliveries.
4. Decision: freeze schema changes until the end of November.

Action items:
- Sarah: share the revised timeline by October 21.
- Omar: prepare the user acceptance test plan.
"""

MEETING_SEP = """# Meeting notes - September 9, 2025

Attendees: Sarah Chen, Omar Haddad

The kickoff covered goals, scope and the communication plan.
The team agreed to use a weekly status report and a shared risk register.
Next meeting scheduled for October 14, 2025.
"""

COMPLAINTS = [
    "Customer complaints summary - Q3 2025",
    "Total complaints received: 182, up 23% compared with Q2.",
    "The most frequent complaint is late delivery of orders (41% of cases).",
    "Billing errors account for 18% of complaints, mostly duplicate charges.",
    "Customers also reported that the support hotline wait time exceeds 15 minutes.",
    "Recommended action: add two support agents during peak hours and automate refund requests.",
]

CONTRACT = [
    "Service Agreement - Renewal Conditions",
    "This agreement between Contoso Ltd and Fabrikam Inc renews automatically for successive twelve-month terms.",
    "Either party may terminate the renewal by giving written notice at least 60 days before the end of the current term.",
    "Upon renewal, fees increase by the consumer price index, capped at 4% per year.",
    "The renewal is conditional on the supplier maintaining ISO 27001 certification.",
    "Service credits of 5% apply when monthly availability falls below 99.5%.",
]

Q3_REPORT = [
    "Quarterly Report - Q3 2025",
    "Revenue reached $1.2 million, an increase of 8% over the previous quarter.",
    "Operating costs were reduced by 3% thanks to the consolidation of cloud storage.",
    "Storage consumption grew to 14 TB, of which an estimated 30% is rarely accessed.",
    "The board approved the budget for the data-platform modernisation programme.",
]

RECIPE = """# Banana bread

Ingredients: 3 ripe bananas, 250 g flour, 100 g sugar, 2 eggs, 80 g butter, 1 teaspoon baking soda.
Mash the bananas, mix with melted butter, then add sugar and eggs.
Fold in the flour and baking soda. Bake at 175 C for 55 minutes.
"""

HANDBOOK_PARA = (
    "Employees must store project documents in the shared drive and avoid keeping personal copies. "
    "Archived files older than two years are reviewed every quarter. "
)


def write_pdf(path: Path, lines: list[str]) -> None:
    pdf = FPDF()
    pdf.set_creator("OptiStore demo generator")
    pdf.set_title(lines[0])
    pdf.add_page()
    pdf.set_font("Helvetica", size=12)
    for line in lines:
        pdf.multi_cell(0, 8, line)
        pdf.ln(2)
    pdf.output(str(path))


def write_docx(path: Path, lines: list[str]) -> None:
    doc = Document()
    doc.core_properties.title = lines[0]
    doc.core_properties.author = "Demo Generator"
    doc.add_heading(lines[0], level=1)
    for line in lines[1:]:
        doc.add_paragraph(line)
    # python-docx writes the current time; fix it so output is reproducible enough for hashing tests.
    doc.save(str(path))


def mp4_box(kind: bytes, payload: bytes) -> bytes:
    return struct.pack(">I4s", 8 + len(payload), kind) + payload


def write_metadata_only_mp4(path: Path, width: int, height: int, seconds: int, padding_bytes: int) -> None:
    """A structurally valid MP4 container with metadata boxes and filler data (not playable)."""
    ftyp = mp4_box(b"ftyp", b"isom" + struct.pack(">I", 512) + b"isomiso2mp41")
    mvhd = mp4_box(b"mvhd", bytes(4) + struct.pack(">IIII", 0, 0, 1000, seconds * 1000) + bytes(80))
    tkhd = mp4_box(b"tkhd", bytes(4) + bytes(72) + struct.pack(">II", width << 16, height << 16))
    hdlr = mp4_box(b"hdlr", bytes(8) + b"vide" + bytes(12) + b"VideoHandler\x00")
    mdia = mp4_box(b"mdia", hdlr)
    trak = mp4_box(b"trak", tkhd + mdia)
    moov = mp4_box(b"moov", mvhd + trak)
    rng = np.random.default_rng(3)
    mdat = mp4_box(b"mdat", rng.integers(0, 256, padding_bytes, dtype=np.uint8).tobytes())
    path.write_bytes(ftyp + moov + mdat)


SCANNED_ADDENDUM = [
    "NON-DISCLOSURE ADDENDUM No. 2",
    "Between Contoso Ltd and Fabrikam Inc",
    "Effective date: March 1, 2026",
    "The confidentiality period is extended to three years.",
    "Source code and customer lists are classified as confidential.",
    "Signed in Casablanca by both parties.",
]


def write_scanned_pdf(path: Path, lines: list[str]) -> None:
    """An image-only PDF (no text layer), like a scanner produces; only OCR can read it."""
    from PIL import ImageFont

    page = Image.new("L", (1240, 1754), 255)  # A4 at 150 dpi
    draw = ImageDraw.Draw(page)
    font = ImageFont.load_default(size=34)
    y = 160
    for line in lines:
        draw.text((120, y), line, fill=0, font=font)
        y += 70
    page.save(path, format="PDF", resolution=150)


def find_ffmpeg() -> str | None:
    found = os.environ.get("FFMPEG") or shutil.which("ffmpeg")
    if found:
        return found
    if os.name != "nt":
        return None
    matches = sorted(glob.glob(os.path.expandvars(r"%LOCALAPPDATA%\Microsoft\WinGet\Packages\Gyan.FFmpeg*\*\bin\ffmpeg.exe")))
    return matches[-1] if matches else None


def write_real_video(path: Path) -> bool:
    """A short, deliberately high-bitrate H.264 test pattern (requires ffmpeg)."""
    exe = find_ffmpeg()
    if exe is None:
        return False
    cmd = [
        exe, "-hide_banner", "-loglevel", "error", "-y",
        "-f", "lavfi", "-i", "testsrc2=size=1280x720:rate=30:duration=6",
        "-f", "lavfi", "-i", "sine=frequency=440:duration=6",
        "-c:v", "libx264", "-crf", "16", "-preset", "veryfast", "-pix_fmt", "yuv420p",
        "-c:a", "aac", "-b:a", "128k", "-shortest", str(path),
    ]  # fmt: skip
    return subprocess.run(cmd, check=False).returncode == 0


def generate(out: Path) -> None:
    if out.exists():
        shutil.rmtree(out)
    rng = random.Random(42)
    d = {name: out / name for name in ("projects", "meetings", "support", "legal", "reports", "backup", "data", "images", "media", "archives", "misc", "broken")}
    for folder in d.values():
        folder.mkdir(parents=True, exist_ok=True)

    (d["projects"] / "project_alpha_brief.md").write_text(ALPHA_BRIEF, encoding="utf-8")
    (d["projects"] / "project_alpha_status_update.txt").write_text(ALPHA_UPDATE, encoding="utf-8")
    (d["projects"] / "project_alpha_finance_note.md").write_text(ALPHA_FINANCE, encoding="utf-8")
    (d["meetings"] / "meeting_notes_2025-10-14_october.md").write_text(MEETING_OCT, encoding="utf-8")
    (d["meetings"] / "meeting_notes_2025-09-09_september.md").write_text(MEETING_SEP, encoding="utf-8")
    write_docx(d["support"] / "customer_complaints_q3.docx", COMPLAINTS)
    write_pdf(d["legal"] / "contract_renewal_terms.pdf", CONTRACT)
    write_scanned_pdf(d["legal"] / "scanned_nda_addendum.pdf", SCANNED_ADDENDUM)
    write_pdf(d["reports"] / "quarterly_report_q3_2025.pdf", Q3_REPORT)
    (d["misc"] / "banana_bread_recipe.md").write_text(RECIPE, encoding="utf-8")
    (d["misc"] / "employee_handbook_excerpt.txt").write_text(HANDBOOK_PARA * 120, encoding="utf-8")

    # Exact duplicates: identical bytes under different names/folders.
    shutil.copyfile(d["reports"] / "quarterly_report_q3_2025.pdf", d["backup"] / "Q3 report (copy).pdf")
    shutil.copyfile(d["meetings"] / "meeting_notes_2025-10-14_october.md", d["backup"] / "notes_oct_backup.md")
    shutil.copyfile(d["projects"] / "project_alpha_brief.md", d["backup"] / "alpha_brief_old.md")

    # Structured data: highly compressible CSV and JSON.
    with (d["data"] / "sales_2025.csv").open("w", newline="", encoding="utf-8") as fh:
        writer = csv.writer(fh)
        writer.writerow(["date", "region", "product", "units", "unit_price", "revenue"])
        regions, products = ["North", "South", "East", "West"], ["Widget", "Gadget", "Gizmo", "Doohickey"]
        for i in range(6000):
            units = rng.randint(1, 40)
            price = rng.choice([9.99, 14.5, 23.0, 49.95])
            writer.writerow([f"2025-{1 + i % 12:02d}-{1 + i % 28:02d}", rng.choice(regions), rng.choice(products), units, price, round(units * price, 2)])
    inventory = {"warehouse": "Casablanca-01", "updated": "2025-10-01",
                 "items": [{"sku": f"SKU-{i:05d}", "name": f"Item {i}", "qty": rng.randint(0, 500), "location": f"A{i % 20}-{i % 7}"} for i in range(1500)]}
    (d["data"] / "inventory.json").write_text(json.dumps(inventory, indent=2), encoding="utf-8")
    (d["data"] / "complaint_log.csv").write_text(
        "date,customer,category,description\n"
        "2025-07-03,ACME Corp,delivery,Order arrived nine days late\n"
        "2025-07-19,Globex,billing,Charged twice for the same invoice\n"
        "2025-08-02,Initech,support,Hotline wait time over 20 minutes\n"
        "2025-09-11,Umbrella,delivery,Package damaged during delivery\n",
        encoding="utf-8",
    )

    # Images: a flat diagram (compresses well), a noisy photo-like JPEG (already compressed), a WebP.
    diagram = Image.new("RGB", (1200, 800), "white")
    draw = ImageDraw.Draw(diagram)
    for i in range(6):
        draw.rectangle([60 + i * 180, 300, 200 + i * 180, 500], fill=(40 + i * 30, 90, 160), outline="black", width=3)
        draw.line([200 + i * 180, 400, 240 + i * 180, 400], fill="black", width=4)
    diagram.save(d["images"] / "architecture_diagram.png", format="PNG", compress_level=1)
    np_rng = np.random.default_rng(7)
    y, x = np.mgrid[0:900, 0:1400]
    photo = np.stack([(x / 1400 * 255), (y / 900 * 255), ((x + y) / 2300 * 255)], axis=-1)
    photo = np.clip(photo + np_rng.normal(0, 18, photo.shape), 0, 255).astype(np.uint8)
    Image.fromarray(photo).save(d["images"] / "site_photo.jpg", format="JPEG", quality=97)
    Image.fromarray(photo[:400, :600]).save(d["images"] / "thumbnail.webp", format="WEBP", quality=90)

    # Video container (metadata-only, labelled) and archive.
    write_metadata_only_mp4(d["media"] / "demo_clip_metadata_only.mp4", 1920, 1080, 12, 2_500_000)
    if not write_real_video(d["media"] / "site_walkthrough.mp4"):
        print("ffmpeg not found: skipped media/site_walkthrough.mp4 (real video clip)")
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("old_assets/readme.txt", "Legacy marketing assets from 2021.\n" * 50)
        zf.writestr("old_assets/data.csv", "id,value\n" + "\n".join(f"{i},{i * 3}" for i in range(2000)))
    (d["archives"] / "old_assets_2021.zip").write_bytes(buf.getvalue())

    # Malformed file: PDF extension, not a PDF.
    (d["broken"] / "corrupted_scan.pdf").write_bytes(b"This is not really a PDF file.\x00\x01\x02")

    (out / "README.txt").write_text(
        "OptiStore AI demonstration dataset (fictional, generated by scripts/generate_demo_data.py).\n"
        "media/demo_clip_metadata_only.mp4 is a metadata-only MP4 container (not playable) used to test video metadata parsing.\n"
        "media/site_walkthrough.mp4 is a synthetic test pattern (ffmpeg testsrc2) used for measured transcoding.\n"
        "legal/scanned_nda_addendum.pdf has no text layer (image only) and can only be read with OCR.\n",
        encoding="utf-8",
    )
    print(f"Demo dataset written to {out.resolve()}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--out", default=str(Path(__file__).resolve().parents[1] / "sample_data"))
    generate(Path(parser.parse_args().out))
