import json
import time
from pathlib import Path
from loguru import logger
import bisect
import tiktoken

from ..core.config import PIPELINE_ARTEFACTS_DIR

def synthesize_final_markdown(doc_output_dir: str, curated_suggestions: list[dict]) -> str:
    """
    Mensintesis file markdown akhir (v2) dari markdown_v1.md dengan menyisipkan catatan kaki
    berdasarkan saran terkurasi (approved/edited) dari frontend.

    Args:
        doc_output_dir (str): Direktori output yang berisi markdown_v1.md
        curated_suggestions (list[dict]): Daftar saran yang sudah diverifikasi manusia

    Returns:
        str: Path menuju file markdown_v2.md yang telah disintesis.
    """
    doc_path = Path(doc_output_dir)
    markdown_path = doc_path / "markdown_v1.md"

    try:
        with open(markdown_path, 'r', encoding='utf-8') as f:
            markdown_content = f.read()
    except FileNotFoundError as e:
        logger.error(f"File markdown_v1.md tidak ditemukan - {e}")
        return ""

    modified_markdown = markdown_content
    footnotes_list: list[str] = []
    appendix_list: list[str] = []
    footnote_counter = 1

    def _build_token_maps(text: str):
        """Bangun peta byte/token untuk penambatan aman berbasis tiktoken.
        Returns (char_to_byte, token_start_bytes, total_bytes).
        """
        try:
            enc = tiktoken.encoding_for_model("gpt-4o")
        except Exception:
            try:
                enc = tiktoken.get_encoding("cl100k_base")
            except Exception:
                enc = None
        if enc is None:
            # Fallback: tanpa token map
            return None, None, len(text.encode("utf-8"))

        tokens = enc.encode(text, disallowed_special=())
        token_bytes_list = enc.decode_tokens_bytes(tokens)
        token_start_bytes: list[int] = []
        bsum = 0
        for tb in token_bytes_list:
            token_start_bytes.append(bsum)
            bsum += len(tb)

        # char -> byte map
        char_to_byte: list[int] = [0]
        bcount = 0
        for ch in text:
            bcount += len(ch.encode("utf-8"))
            char_to_byte.append(bcount)

        return char_to_byte, token_start_bytes, bsum

    def _find_anchor_span(text: str, anchor_text: str) -> tuple[int, int] | None:
        """Cari rentang karakter [start, end) dari anchor di dalam text.
        Coba exact, case-insensitive, dan fallback prefix/suffix.
        """
        if not anchor_text:
            return None
        target = anchor_text
        idx = text.find(target)
        if idx != -1:
            return idx, idx + len(target)
        trimmed = target.strip()
        if trimmed:
            idx = text.find(trimmed)
            if idx != -1:
                return idx, idx + len(trimmed)
        lower_text = text.lower()
        lower_anchor = target.lower().strip()
        if lower_anchor:
            idx = lower_text.find(lower_anchor)
            if idx != -1:
                return idx, idx + len(lower_anchor)
        if len(trimmed) > 30:
            prefix = trimmed[:30].lower()
            suffix = trimmed[-30:].lower()
            pidx = lower_text.find(prefix)
            if pidx != -1:
                return pidx, pidx + len(prefix)
            sidx = lower_text.find(suffix)
            if sidx != -1:
                return sidx, sidx + len(suffix)
        return None

    def _insert_token_aware_after_span(text: str, span: tuple[int, int], footnote_index: int) -> tuple[str, bool]:
        """Sisipkan catatan kaki setelah rentang, digeser ke batas token terdekat (tiktoken) tanpa memotong karakter.
        Mengembalikan (teks_baru, True) bila berhasil; fallback ke penempatan tepat setelah span bila token map tidak tersedia.
        """
        start, end = span
        char_to_byte, token_starts, total_bytes = _build_token_maps(text)
        insert_pos_char = end
        if char_to_byte and token_starts is not None:
            # Tentukan offset byte dari akhir span
            end_byte = char_to_byte[end]
            # Cari boundary token pertama di >= end_byte
            j = bisect.bisect_left(token_starts, end_byte)
            if j < len(token_starts):
                target_byte = token_starts[j]
            else:
                target_byte = total_bytes
            # Konversi kembali ke indeks karakter terdekat ke kanan
            insert_pos_char = bisect.bisect_left(char_to_byte, target_byte)
        # Sisipkan marker
        new_text = text[:insert_pos_char] + f"[^{footnote_index}]" + text[insert_pos_char:]
        return new_text, True

    anchored_count = 0
    appendix_count = 0
    t0 = time.time()

    for s in curated_suggestions or []:
        status = (s.get("status") or "").lower()
        if status not in ("approved", "edited"):
            continue
        s_type = s.get("type")
        content = s.get("generated_content") or ""
        anchor = s.get("original_context") or ""

        label = "Pengayaan"
        if s_type == "term_to_define":
            label = "Definisi"
        elif s_type == "concept_to_simplify":
            label = "Penyederhanaan"

        span = _find_anchor_span(modified_markdown, anchor)
        if span is not None:
            modified_markdown, _ = _insert_token_aware_after_span(modified_markdown, span, footnote_counter)
            footnotes_list.append(f"[^{footnote_counter}]: **{label}:** {content}")
            footnote_counter += 1
            anchored_count += 1
        else:
            logger.warning(f"Tidak dapat menemukan konteks untuk menyisipkan catatan kaki: id={s.get('id')} type={s_type}")
            appendix_list.append(f"- **{label}** (unanchored): {content}")
            appendix_count += 1

    final_content = modified_markdown
    if footnotes_list:
        final_content += "\n\n---\n\n" + "\n".join(footnotes_list)
    if appendix_list:
        final_content += "\n\n## Catatan Pengayaan (unanchored)\n\n" + "\n".join(appendix_list)

    output_path = doc_path / "markdown_v2.md"
    with open(output_path, 'w', encoding='utf-8') as f:
        f.write(final_content)

    # Metrics
    metrics = {
        "anchored": anchored_count,
        "appendixed": appendix_count,
        "total_processed": anchored_count + appendix_count,
        "duration_sec": time.time() - t0,
    }
    try:
        with open(doc_path / "phase_3_metrics.json", 'w', encoding='utf-8') as mf:
            json.dump(metrics, mf, ensure_ascii=False, indent=2)
    except Exception:
        pass

    logger.info(f"Fase 3 selesai. Markdown final disimpan di: {output_path}")
    return str(output_path)

if __name__ == '__main__':
    base_artefacts_dir = Path(PIPELINE_ARTEFACTS_DIR)
    if not base_artefacts_dir.exists():
        logger.error(f"Direktori '{PIPELINE_ARTEFACTS_DIR}' tidak ditemukan.")
    else:
        all_doc_dirs = [d for d in base_artefacts_dir.iterdir() if d.is_dir()]
        if not all_doc_dirs:
            logger.error("Tidak ada direktori dokumen yang ditemukan.")
        else:
            latest_doc_dir = max(all_doc_dirs, key=lambda d: d.stat().st_mtime)
            logger.info(f"Menjalankan Fase 3 pada direktori: {latest_doc_dir}")

            sugg_path = latest_doc_dir / "suggestions.json"
            if sugg_path.exists():
                try:
                    curated = json.loads(sugg_path.read_text(encoding='utf-8'))
                except Exception:
                    curated = []
            else:
                curated = []
            synthesize_final_markdown(str(latest_doc_dir), curated)
