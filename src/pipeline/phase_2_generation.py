import json
from pathlib import Path
import google.generativeai as genai
from PIL import Image
from loguru import logger

from ..core.config import GENERATION_MODEL, PIPELINE_ARTEFACTS_DIR, GOOGLE_API_KEY

def generate_bulk_content(doc_output_dir: str) -> str:
    """
    Mengeksekusi rencana enrichment untuk menghasilkan konten baru secara massal.

    Fungsi ini adalah Fase 2 dari proyek Genesis-RAG.
    Fungsi ini membaca rencana dari enrichment_plan.json, menggabungkannya dengan
    dokumen asli dan gambar-gambar yang relevan, lalu mengirimkan permintaan multimodal
    ke Gemini API untuk menghasilkan semua konten yang dibutuhkan dalam satu panggilan.

    Args:
        doc_output_dir (str): Direktori output dari fase sebelumnya yang berisi
                              file-file yang diperlukan (markdown, plan, assets).

    Returns:
        str: Path menuju file generated_content.json yang berisi konten hasil generasi.
    """
    doc_path = Path(doc_output_dir)
    markdown_path = doc_path / "markdown_v1.md"
    plan_path = doc_path / "enrichment_plan.json"
    assets_path = doc_path / "assets"

    try:
        with open(markdown_path, 'r', encoding='utf-8') as f:
            markdown_content = f.read()
        with open(plan_path, 'r', encoding='utf-8') as f:
            enrichment_plan = json.load(f)
    except FileNotFoundError as e:
        logger.error(f"File yang dibutuhkan tidak ditemukan - {e}")
        return ""

    image_parts = []
    images_to_describe = enrichment_plan.get("images_to_describe", [])
    for image_filename in images_to_describe:
        image_path = assets_path / image_filename
        if image_path.exists():
            try:
                img = Image.open(image_path)
                image_parts.append(img)
                image_parts.append(f"\n--- Image Filename: {image_filename} ---\n")
            except Exception as e:
                logger.warning(f"Tidak dapat memproses gambar {image_filename}: {e}")
        else:
            logger.warning(f"File gambar tidak ditemukan: {image_path}")

    prompt_parts = [
        "Role: You are a living encyclopedia and an expert technical writer.",
        "Context: I am providing you with an original document, an enrichment plan, and relevant image assets.",
        "Task: Execute the enrichment plan. Generate all requested content clearly and accurately based on the document's context.",
        "Format Output: You MUST respond ONLY with a single JSON object that maps each item from the plan to your generated content.",
        "\n--- Original Document (for context) ---",
        markdown_content,
        "\n--- Enrichment Plan to Execute ---",
        json.dumps(enrichment_plan, indent=2),
        "\n--- Image Assets ---"
    ] + image_parts

    logger.info("Mengirim permintaan multimodal ke Gemini untuk generasi konten...")
    model = genai.GenerativeModel(GENERATION_MODEL)
    response = model.generate_content(prompt_parts)
  
    try:
        json_text = response.text.strip().replace('```json', '').replace('```', '').strip()
        content_data = json.loads(json_text)
    except (json.JSONDecodeError, AttributeError) as e:
        logger.error(f"Error saat mendekode JSON dari respons Gemini: {e}")
        logger.error(f"Teks respons mentah:\n{response.text}")
        return ""

    output_path = doc_path / "generated_content.json"
    with open(output_path, 'w', encoding='utf-8') as f:
        json.dump(content_data, f, indent=2)

    logger.info(f"Fase 2 selesai. Konten yang digenerasi disimpan di: {output_path}")
    return str(output_path)

if __name__ == '__main__':
    if GOOGLE_API_KEY:
        genai.configure(api_key=GOOGLE_API_KEY)
    else:
        logger.error("GOOGLE_API_KEY not found. Please set it in your .env file.")

    base_artefacts_dir = Path(PIPELINE_ARTEFACTS_DIR)
    if not base_artefacts_dir.exists():
        logger.error(f"'{PIPELINE_ARTEFACTS_DIR}' directory not found. Run phase_0 first.")
    else:
        all_doc_dirs = [d for d in base_artefacts_dir.iterdir() if d.is_dir()]
        if not all_doc_dirs:
            logger.error(f"No document directories found in '{PIPELINE_ARTEFACTS_DIR}'.")
        else:
            latest_doc_dir = max(all_doc_dirs, key=lambda d: d.stat().st_mtime)
            logger.info(f"Running Phase 2 on directory: {latest_doc_dir}")
            generate_bulk_content(str(latest_doc_dir))