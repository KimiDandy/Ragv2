import json
from pathlib import Path
from loguru import logger

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

    def _insert_footnote(anchor_text: str) -> bool:
        """Mencoba menyisipkan penanda catatan kaki setelah kemunculan pertama dari anchor_text.
        Pencocokan tidak peka huruf besar/kecil dan memiliki fallback pencocokan parsial.
        """
        nonlocal modified_markdown, footnote_counter
        if not anchor_text:
            return False
        target = anchor_text

        if target in modified_markdown:
            modified_markdown = modified_markdown.replace(target, f"{target}[^{footnote_counter}]", 1)
            return True
        trimmed = target.strip()
        if trimmed and trimmed in modified_markdown:
            modified_markdown = modified_markdown.replace(trimmed, f"{trimmed}[^{footnote_counter}]", 1)
            return True
        lower_md = modified_markdown.lower()
        lower_anchor = target.lower().strip()
        if lower_anchor and (idx := lower_md.find(lower_anchor)) != -1:
            end = idx + len(lower_anchor)
            original_slice = modified_markdown[idx:end]
            modified_markdown = (
                modified_markdown[:idx]
                + f"{original_slice}[^{footnote_counter}]"
                + modified_markdown[end:]
            )
            return True
        if len(trimmed) > 30:
            prefix = trimmed[:30].lower()
            suffix = trimmed[-30:].lower()
            if (pidx := lower_md.find(prefix)) != -1:
                start = pidx
                end = pidx + len(prefix)
                original_slice = modified_markdown[start:end]
                modified_markdown = (
                    modified_markdown[:start]
                    + f"{original_slice}[^{footnote_counter}]"
                    + modified_markdown[end:]
                )
                return True
            if (sidx := lower_md.find(suffix)) != -1:
                start = sidx
                end = sidx + len(suffix)
                original_slice = modified_markdown[start:end]
                modified_markdown = (
                    modified_markdown[:start]
                    + f"{original_slice}[^{footnote_counter}]"
                    + modified_markdown[end:]
                )
                return True
        return False

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
        elif s_type == "image_description":
            label = "Deskripsi Gambar"

        if _insert_footnote(anchor):
            footnotes_list.append(f"[^{footnote_counter}]: **{label}:** {content}")
            footnote_counter += 1
        else:
            logger.warning(f"Tidak dapat menemukan konteks untuk menyisipkan catatan kaki: id={s.get('id')} type={s_type}")
            # simpan untuk lampiran agar pengguna tetap mendapat kontennya
            appendix_list.append(f"- **{label}** (unanchored): {content}")

    final_content = modified_markdown
    if footnotes_list:
        final_content += "\n\n---\n\n" + "\n".join(footnotes_list)
    if appendix_list:
        final_content += "\n\n## Catatan Pengayaan (unanchored)\n\n" + "\n".join(appendix_list)

    output_path = doc_path / "markdown_v2.md"
    with open(output_path, 'w', encoding='utf-8') as f:
        f.write(final_content)

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
