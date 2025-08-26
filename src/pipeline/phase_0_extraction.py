import fitz  # PyMuPDF
import uuid
import os
from pathlib import Path
from loguru import logger
import re
import json
import time
from datetime import datetime
from typing import List
from pydantic import BaseModel, Field

from ..core.config import PIPELINE_ARTEFACTS_DIR

class Segment(BaseModel):
    segment_id: str
    page: int = Field(ge=1)
    char_start: int = Field(ge=0)
    char_end: int = Field(ge=0)
    header_path: List[str]
    text: str
    contains_entities: bool
    is_difficult: bool
    numeric_ratio: float = Field(ge=0.0, le=1.0, default=0.0)


class Phase0Metrics(BaseModel):
    document_id: str
    pages: int
    segments: int
    total_chars: int
    avg_segment_chars: float
    started_at: str
    ended_at: str
    duration_sec: float


_RE_NUMBERED = re.compile(r"^\s*(\d+(?:\.\d+){0,3})\s+(.+)$")
_RE_URL = re.compile(r"https?://[\w\-./?%&=#:]+", re.IGNORECASE)
_RE_EMAIL = re.compile(r"\b[\w.+-]+@[\w-]+\.[\w.-]+\b")
_RE_DATE = re.compile(r"\b\d{4}-\d{2}-\d{2}\b")
_RE_ACRONYM = re.compile(r"\b[A-Z]{2,6}\b")


def _looks_like_header(line: str) -> int:
    """
    Heuristic header detector.
    Returns a level >=1 if the line resembles a header, else 0.
    Numbered headings (1., 1.1, 2.3.4) set level by depth. ALL-CAPS short lines become level 1.
    """
    s = (line or "").strip()
    if not s:
        return 0
    m = _RE_NUMBERED.match(s)
    if m:
        depth = m.group(1).count(".") + 1
        return min(depth, 6)
    # ALL CAPS short line (not too long)
    if len(s) <= 80 and s.upper() == s and re.search(r"[A-Z]", s):
        return 1
    return 0


def _update_header_path(header_path: List[str], line: str, level: int) -> List[str]:
    new_path = list(header_path)
    if level <= 0:
        return new_path
    # shrink or extend
    if level <= len(new_path):
        new_path = new_path[:level - 1]
    return new_path + [line.strip()]


def _count_syllables(word: str) -> int:
    w = word.lower()
    # very rough heuristic
    w = re.sub(r"[^a-z]", "", w)
    if not w:
        return 0
    vowels = re.findall(r"[aeiouy]+", w)
    count = len(vowels)
    # silent 'e'
    if w.endswith("e") and count > 1:
        count -= 1
    return max(count, 1)


def _readability_is_difficult(text: str) -> bool:
    sentences = [s for s in re.split(r"[.!?]+\s+", text.strip()) if s]
    words = re.findall(r"\b[\w'-]+\b", text)
    if not sentences or not words:
        return False
    syllables = sum(_count_syllables(w) for w in words)
    words_count = len(words)
    sentences_count = max(len(sentences), 1)
    # Flesch Reading Ease
    fre = 206.835 - 1.015 * (words_count / sentences_count) - 84.6 * (syllables / words_count)
    long_sentence = (words_count / sentences_count) > 25
    long_para = words_count > 120
    return fre < 60 or long_sentence or long_para


def _contains_entities(text: str) -> bool:
    return bool(_RE_URL.search(text) or _RE_EMAIL.search(text) or _RE_DATE.search(text) or _RE_ACRONYM.search(text))


def process_pdf_local(pdf_path: str, output_base_dir: str = PIPELINE_ARTEFACTS_DIR) -> dict:
    """
    Memproses file PDF secara lokal untuk mengekstrak teks menjadi markdown (teks saja).

    Fungsi ini adalah Fase 0 dari proyek Genesis-RAG (versi teks-only). Proses ini mengimplementasikan langkah utama:
    1. Mengambil semua blok teks dari setiap halaman dan menyortirnya berdasar posisi.
    2. Melakukan segmentasi paragraf dengan menyertakan metadata header_path dan offset karakter.
    3. Menghasilkan artefak:
       - markdown_v1.md (kompatibilitas lama)
       - full_text.txt (string referensi untuk offset karakter)
       - segments.json (daftar segmen terverifikasi Pydantic)
       - phase_0_metrics.json (observability)

    Args:
        pdf_path (str): Path menuju file PDF yang akan diproses.
        output_base_dir (str): Direktori dasar untuk menyimpan artefak yang diproses.

    Returns:
        dict: Dictionary yang berisi document_id dan path ke artefak yang dihasilkan.
    """
    started = time.time()
    started_at = datetime.utcnow().isoformat() + "Z"

    doc_id = str(uuid.uuid4())
    doc_output_dir = Path(output_base_dir) / doc_id
    doc_output_dir.mkdir(parents=True, exist_ok=True)

    doc = fitz.open(pdf_path)
    pages_count = doc.page_count

    final_markdown_content = ""
    full_text_parts: List[str] = []
    segments: List[Segment] = []
    header_path: List[str] = []
    char_offset = 0

    for page_index in range(pages_count):
        page = doc.load_page(page_index)
        blocks = page.get_text("blocks") or []
        # sort by y0, then x0
        blocks = sorted(blocks, key=lambda b: (b[1], b[0]))

        for b in blocks:
            text = (b[4] or "").strip()
            if not text:
                continue

            first_line = text.splitlines()[0].strip()
            level = _looks_like_header(first_line)
            if level > 0:
                header_path = _update_header_path(header_path, first_line, level)
                # do not treat header as a content segment
                final_markdown_content += text + "\n\n"
                full_text_parts.append(text)
                full_text_parts.append("\n\n")
                char_offset += len(text) + 2  # account for the two newlines in full_text
                continue

            # create a paragraph segment
            para_text = text
            para_len = len(para_text)
            if para_len == 0:
                continue

            contains_entities = _contains_entities(para_text)
            is_difficult = _readability_is_difficult(para_text)
            # fraction of numeric characters in the paragraph
            try:
                digits = sum(1 for ch in para_text if ch.isdigit())
                numeric_ratio = digits / max(len(para_text), 1)
            except Exception:
                numeric_ratio = 0.0

            seg = Segment(
                segment_id=f"{doc_id}_{len(segments)+1}",
                page=page_index + 1,
                char_start=char_offset,
                char_end=char_offset + para_len,
                header_path=list(header_path),
                text=para_text,
                contains_entities=contains_entities,
                is_difficult=is_difficult,
                numeric_ratio=float(numeric_ratio),
            )
            segments.append(seg)

            # append to markdown and full_text reference
            final_markdown_content += para_text + "\n\n"
            full_text_parts.append(para_text)
            full_text_parts.append("\n\n")
            char_offset += para_len + 2  # for the two newlines

    md_output_path = doc_output_dir / "markdown_v1.md"
    with open(md_output_path, "w", encoding="utf-8") as f:
        f.write(final_markdown_content)

    full_text = "".join(full_text_parts)
    full_text_path = doc_output_dir / "full_text.txt"
    with open(full_text_path, "w", encoding="utf-8") as f:
        f.write(full_text)

    segments_path = doc_output_dir / "segments.json"
    with open(segments_path, "w", encoding="utf-8") as f:
        json.dump([s.model_dump() for s in segments], f, ensure_ascii=False, indent=2)

    ended = time.time()
    ended_at = datetime.utcnow().isoformat() + "Z"
    metrics = Phase0Metrics(
        document_id=doc_id,
        pages=pages_count,
        segments=len(segments),
        total_chars=len(full_text),
        avg_segment_chars=(len(full_text) / max(len(segments), 1)),
        started_at=started_at,
        ended_at=ended_at,
        duration_sec=ended - started,
    )
    metrics_path = doc_output_dir / "phase_0_metrics.json"
    with open(metrics_path, "w", encoding="utf-8") as f:
        json.dump(metrics.model_dump(), f, ensure_ascii=False, indent=2)

    doc.close()

    result = {
        "document_id": doc_id,
        "markdown_path": str(md_output_path),
        "full_text_path": str(full_text_path),
        "segments_path": str(segments_path),
        "metrics_path": str(metrics_path),
        "output_dir": str(doc_output_dir),
    }

    logger.info(f"Fase 0 selesai untuk dokumen: {pdf_path}")
    logger.info(f"Artefak disimpan di: {doc_output_dir}")
    logger.info(f"Segmen dibuat: {len(segments)} | Halaman: {pages_count}")
    
    return result

if __name__ == '__main__':
    if not os.path.exists("dummy.pdf"):
        doc = fitz.open() 
        page = doc.new_page()
        page.insert_text((50, 72), "This is the first paragraph.")
        try:
            rect = fitz.Rect(72, 100, 200, 200)
            page.draw_rect(rect, color=(0,0,1), fill=(0,1,0))
            page.insert_text((50, 250), "This is the second paragraph, after the image.")
        except Exception as e:
            logger.error(f"Could not add image to dummy PDF: {e}")
        doc.save("dummy.pdf")
        logger.info("Created dummy.pdf for testing.")

    if not os.path.exists(PIPELINE_ARTEFACTS_DIR):
        os.makedirs(PIPELINE_ARTEFACTS_DIR)

    process_pdf_local("dummy.pdf")
