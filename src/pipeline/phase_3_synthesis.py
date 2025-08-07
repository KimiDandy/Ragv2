import json
from pathlib import Path
from loguru import logger

from ..core.config import PIPELINE_ARTEFACTS_DIR

def synthesize_final_markdown(doc_output_dir: str) -> str:
    """
    Mensintesis file markdown akhir (v2) dengan menggabungkan konten asli dan konten yang diperkaya.

    Fungsi ini adalah Fase 3 dari proyek Genesis-RAG.
    Fungsi ini membaca markdown_v1.md dan generated_content.json, lalu dengan cerdas
    menyisipkan definisi, penyederhanaan, dan deskripsi gambar sebagai catatan kaki (footnotes)
    ke dalam dokumen asli untuk membuat markdown_v2.md.

    Args:
        doc_output_dir (str): Direktori output yang berisi file-file yang diperlukan.

    Returns:
        str: Path menuju file markdown_v2.md yang telah disintesis.
    """
    doc_path = Path(doc_output_dir)
    markdown_path = doc_path / "markdown_v1.md"
    generated_content_path = doc_path / "generated_content.json"

    try:
        with open(markdown_path, 'r', encoding='utf-8') as f:
            markdown_content = f.read()
        with open(generated_content_path, 'r', encoding='utf-8') as f:
            raw_content = f.read()
        
        try:
            if '```json' in raw_content:
                clean_json_string = raw_content.split('```json\n')[1].split('```')[0]
            else:
                clean_json_string = raw_content
            
            generated_content = json.loads(clean_json_string)

            if isinstance(generated_content, str):
                generated_content = json.loads(generated_content)

        except (json.JSONDecodeError, IndexError) as e:
            logger.error(f"Error saat mendekode JSON dari {generated_content_path}: {e}")
            return ""

    except FileNotFoundError as e:
        logger.error(f"File yang dibutuhkan tidak ditemukan - {e}")
        return ""

    footnote_counter = 1
    footnotes_list = []
    modified_markdown = markdown_content

    if not isinstance(generated_content, dict):
        logger.error(f"Konten yang diparsing dari {generated_content_path} bukan sebuah dictionary.")
        return ""

    for item in generated_content.get("terms_to_define", []):
        term = item.get("term")
        definition = item.get("definition")
        if term and definition:
            modified_markdown = modified_markdown.replace(term, f"{term}[^{footnote_counter}]", 1)
            footnotes_list.append(f"[^{footnote_counter}]: **{term}:** {definition}")
            footnote_counter += 1

    for item in generated_content.get("concepts_to_simplify", []):
        identifier = item.get("identifier")
        simplified_text = item.get("simplified_text")
        if identifier and simplified_text:
            if identifier in modified_markdown:
                modified_markdown = modified_markdown.replace(identifier, f"{identifier}[^{footnote_counter}]", 1)
                footnotes_list.append(f"[^{footnote_counter}]: **Simplified:** {simplified_text}")
                footnote_counter += 1

    for item in generated_content.get("image_descriptions", []):
        image_file = item.get("image_file")
        description = item.get("description")
        placeholder = f"[IMAGE_PLACEHOLDER: {image_file}]"
        if image_file and description:
            if placeholder in modified_markdown:
                modified_markdown = modified_markdown.replace(placeholder, f"{placeholder}[^{footnote_counter}]", 1)
                footnotes_list.append(f"[^{footnote_counter}]: **Image Description ({image_file}):** {description}")
                footnote_counter += 1

    if footnotes_list:
        final_content = modified_markdown + "\n\n---\n\n" + "\n".join(footnotes_list)
    else:
        final_content = modified_markdown
    output_path = doc_path / "markdown_v2.md"
    with open(output_path, 'w', encoding='utf-8') as f:
        f.write(final_content)

    logger.info(f"Fase 3 selesai. Markdown final disimpan di: {output_path}")
    return str(output_path)

if __name__ == '__main__':
    base_artefacts_dir = Path(PIPELINE_ARTEFACTS_DIR)
    if not base_artefacts_dir.exists():
        logger.error(f"'{PIPELINE_ARTEFACTS_DIR}' directory not found.")
    else:
        all_doc_dirs = [d for d in base_artefacts_dir.iterdir() if d.is_dir()]
        if not all_doc_dirs:
            logger.error("No document directories found.")
        else:
            latest_doc_dir = max(all_doc_dirs, key=lambda d: d.stat().st_mtime)
            logger.info(f"Running Phase 3 on directory: {latest_doc_dir}")
            synthesize_final_markdown(str(latest_doc_dir))
