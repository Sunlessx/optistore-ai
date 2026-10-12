"""Unit tests for pure functions: paths, extraction, chunking, facts."""

from __future__ import annotations

import io
import zipfile
from pathlib import Path

import pytest

from app.core.paths import PathNotAllowedError, safe_join, validate_scan_root
from app.services import extractors, facts
from app.services.embeddings import HashingEmbedder, chunk_text, from_bytes, split_sentences, to_bytes
from tests.conftest import SAMPLE_DIR


class TestPaths:
    def test_allowed_root_accepted(self):
        assert validate_scan_root(str(SAMPLE_DIR)) == SAMPLE_DIR.resolve()

    def test_subdirectory_accepted(self):
        assert validate_scan_root(str(SAMPLE_DIR / "projects")).name == "projects"

    @pytest.mark.parametrize("raw", [str(Path.home()), str(SAMPLE_DIR / ".." / "backend"), "relative/path", "", "bad\x00path"])
    def test_outside_or_invalid_rejected(self, raw):
        with pytest.raises(PathNotAllowedError):
            validate_scan_root(raw)

    def test_safe_join_blocks_traversal(self):
        with pytest.raises(PathNotAllowedError):
            safe_join(SAMPLE_DIR, "../backend/app/main.py")
        assert safe_join(SAMPLE_DIR, "projects/project_alpha_brief.md").exists()


class TestExtractors:
    def test_classify(self):
        assert extractors.classify("a/B.PDF")[:3] == (".pdf", "document", "application/pdf")
        assert extractors.classify("x.webp")[1] == "image"
        assert extractors.classify("movie.mkv")[1] == "video"
        assert extractors.classify("thing.exe")[3] is False

    def test_magic_detects_mislabelled_pdf(self):
        assert extractors.check_magic(".pdf", b"%PDF-1.7") is True
        assert extractors.check_magic(".pdf", b"This is not") is False
        assert extractors.check_magic(".webp", b"RIFF\x00\x00\x00\x00WEBPVP8 ") is True

    def test_pdf_and_docx_text(self):
        pdf = extractors.extract_text_from_bytes(".pdf", (SAMPLE_DIR / "legal/contract_renewal_terms.pdf").read_bytes(), 10_000)
        assert "renew" in pdf.text.lower() and pdf.metadata["pages"] == 1
        docx = extractors.extract_text_from_bytes(
            ".docx", (SAMPLE_DIR / "support/customer_complaints_q3.docx").read_bytes(), 10_000
        )
        assert "late delivery" in docx.text

    def test_csv_and_json_rendered_as_facts(self):
        csv_ex = extractors.extract_text_from_bytes(".csv", b"name,city\nAda,Paris\n", 1000)
        assert csv_ex.metadata["rows"] == 1 and "city: Paris" in csv_ex.text
        js = extractors.extract_text_from_bytes(".json", b'{"budget": 5, "team": {"lead": "Ada"}}', 1000)
        assert "lead: Ada" in js.text
        bad = extractors.extract_text_from_bytes(".json", b"{oops", 1000)
        assert bad.warnings

    def test_text_truncation(self):
        result = extractors.extract_text_from_bytes(".txt", b"a" * 500, 100)
        assert len(result.text) == 100 and result.warnings

    def test_zip_metadata_without_extraction(self):
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
            zf.writestr("a.txt", "x" * 10_000)
        meta = extractors.zip_metadata(buf.getvalue()).metadata
        assert meta["entries"] == 1 and meta["uncompressed_bytes"] == 10_000

    def test_mp4_metadata(self):
        meta = extractors.mp4_metadata(SAMPLE_DIR / "media/demo_clip_metadata_only.mp4").metadata
        assert meta["width"] == 1920 and meta["height"] == 1080 and meta["duration_seconds"] == 12
        assert meta["bitrate_kbps"] > 1000


class TestTextProcessing:
    def test_chunking_overlaps_and_covers_text(self):
        text = " ".join(f"Sentence number {i} talks about storage." for i in range(200))
        chunks = chunk_text(text, size=300, overlap=50)
        assert len(chunks) > 5
        assert all(len(c.text) <= 300 for c in chunks)
        assert chunks[-1].text.endswith("storage.")

    def test_short_text_single_chunk(self):
        assert len(chunk_text("hello world")) == 1
        assert chunk_text("   ") == []

    def test_sentence_split(self):
        assert split_sentences("First one. Second one!\n- Third line") == ["First one.", "Second one!", "Third line"]

    def test_hashing_embedder_normalised_and_roundtrip(self):
        emb = HashingEmbedder(dim=256)
        vecs = emb.encode(["cloud storage budget", "cloud storage budget", "banana bread recipe"])
        assert vecs.shape == (3, 256)
        assert abs(float(vecs[0] @ vecs[1]) - 1.0) < 1e-5
        assert float(vecs[0] @ vecs[2]) < 0.2
        assert (from_bytes(to_bytes(vecs[0])) == vecs[0]).all()


class TestFacts:
    def test_kv_facts(self):
        found = facts.extract_kv_facts("- Budget: $50,000\nDeadline: March 15, 2026\nhttps://example.com\n")
        assert {f.key for f in found} == {"budget", "deadline"}
        assert found[0].norm_value == "$50000"

    def test_key_normalisation_merges_variants(self):
        assert facts.normalize_key("Project Budget") == facts.normalize_key("budget")

    def test_numeric_and_salient_tokens(self):
        tokens = facts.salient_tokens("Sarah Chen approved $1.2 million on October 2, 2025 (12%).\r\nNext line")
        assert "sarah chen" in tokens and "12%" in tokens
        assert any(t == "october2,2025" for t in tokens)
        assert not any("\n" in t for t in tokens)
