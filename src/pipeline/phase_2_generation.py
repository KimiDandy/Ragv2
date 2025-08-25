import json
import re
from pathlib import Path
import google.generativeai as genai
from PIL import Image
from loguru import logger
import time

from ..core.config import GENERATION_MODEL, PIPELINE_ARTEFACTS_DIR, GOOGLE_API_KEY

# Pastikan SDK dikonfigurasi ketika modul diimpor oleh FastAPI
if GOOGLE_API_KEY:
   try:
       genai.configure(api_key=GOOGLE_API_KEY)
   except Exception as _e:
       logger.warning(f"Gagal mengkonfigurasi Google Generative AI SDK: {_e}")

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
    for item in images_to_describe:
        # items may be strings (filenames) OR dicts with keys like 'image_file'/'filename'
        if isinstance(item, str):
            image_filename = item
        elif isinstance(item, dict):
            image_filename = item.get("image_file") or item.get("filename") or ""
        else:
            image_filename = ""

        if not image_filename:
            logger.warning("Item images_to_describe tanpa nama file, lewati.")
            continue

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
        "Peran: Anda adalah ensiklopedia hidup dan penulis teknis ahli.",
        "Konteks: Saya memberikan dokumen asli, rencana enrichment, dan aset gambar yang relevan.",
        "Tugas: Jalankan rencana enrichment. Hasilkan semua konten yang diminta dengan jelas dan akurat berdasarkan konteks dokumen.",
        "Format Keluaran: Anda HARUS membalas HANYA dengan satu objek JSON yang memetakan setiap item pada rencana ke konten yang Anda hasilkan.",
        "\n--- Dokumen Asli (untuk konteks) ---",
        markdown_content,
        "\n--- Rencana Enrichment untuk Dieksekusi ---",
        json.dumps(enrichment_plan, indent=2),
        "\n--- Aset Gambar ---"
    ] + image_parts

    logger.info("Mengirim permintaan multimodal ke Gemini untuk generasi konten...")
    model = genai.GenerativeModel(
        GENERATION_MODEL,
        generation_config={
            "temperature": 0.2,
            "response_mime_type": "application/json"
        },
    )

    def _extract_text(resp):
        raw = ""
        if getattr(resp, "candidates", None):
            for c in resp.candidates:
                parts = getattr(c, "content", None) and c.content.parts or []
                for p in parts:
                    if hasattr(p, "text") and p.text:
                        raw += p.text
        if not raw:
            try:
                raw = resp.text
            except Exception:
                raw = ""
        return (raw or "").strip()

    def _parse_json(raw_text: str):
        if not raw_text:
            return None
        js = raw_text.replace('```json', '').replace('```', '').strip()
        try:
            return json.loads(js)
        except json.JSONDecodeError:
            # try to capture either an object {...} or an array [...]
            m = re.search(r"(\{[\s\S]*\}|\[[\s\S]*\])", js)
            if m:
                try:
                    return json.loads(m.group(0))
                except json.JSONDecodeError:
                    return None
            return None

    # Retry hingga 3x dengan variasi prompt dan backoff
    max_attempts = 3
    content_data = None
    attempts_meta = []

    for attempt in range(1, max_attempts + 1):
        # Siapkan prompt per percobaan
        if attempt == 1:
            prompt_to_send = prompt_parts  # multimodal, termasuk gambar
        elif attempt == 2:
            strict_intro = (
                "PERSYARATAN KETAT: Balas HANYA dengan satu objek JSON valid. "
                "JANGAN sertakan penjelasan, code fence, atau teks tambahan apa pun."
            )
            prompt_to_send = [
                "Peran: Anda adalah ensiklopedia hidup dan penulis teknis ahli.",
                "Konteks: Saya memberikan dokumen asli dan rencana enrichment.",
                "Tugas: Jalankan rencana enrichment. Hasilkan semua konten yang diminta dengan jelas dan akurat.",
                "Format Keluaran: Anda HARUS membalas HANYA dengan satu objek JSON yang memetakan setiap item pada rencana ke konten yang Anda hasilkan.",
                strict_intro,
                "\n--- Dokumen Asli (untuk konteks) ---",
                markdown_content,
                "\n--- Rencana Enrichment untuk Dieksekusi ---",
                json.dumps(enrichment_plan, indent=2),
            ]
        else:
            strict_intro2 = (
                "BALAS HANYA JSON VALID. Jika ragu, kembalikan objek kosong dengan keys: "
                "terms_to_define, concepts_to_simplify, image_descriptions."
            )
            prompt_to_send = [
                strict_intro2,
                "\n--- Dokumen Asli (untuk konteks) ---",
                markdown_content,
                "\n--- Rencana Enrichment untuk Dieksekusi ---",
                json.dumps(enrichment_plan, indent=2),
            ]

        try:
            response = model.generate_content(prompt_to_send)
            raw_text = _extract_text(response)
            try:
                with open(doc_path / f"generated_content_raw_attempt{attempt}.txt", 'w', encoding='utf-8') as rf:
                    rf.write(raw_text)
            except Exception:
                pass

            parsed = _parse_json(raw_text)
            if parsed is not None:
                content_data = parsed
                break

            # Kumpulkan meta untuk logging
            try:
                fr = response.candidates[0].finish_reason if response.candidates else None
            except Exception:
                fr = None
            pf = getattr(response, "prompt_feedback", None)
            attempts_meta.append({"attempt": attempt, "finish_reason": fr, "prompt_feedback": pf})
            logger.warning(f"Percobaan Fase 2 (generation) ke-{attempt} gagal parsing JSON. finish_reason={fr}, prompt_feedback={pf}")
        except Exception as e:
            attempts_meta.append({"attempt": attempt, "error": str(e)})
            logger.warning(f"Percobaan Fase 2 (generation) ke-{attempt} error: {e}")

        if attempt < max_attempts:
            time.sleep(1.5 ** attempt)

    if content_data is None:
        logger.error(f"Gagal mendapatkan JSON valid dari Gemini setelah {max_attempts} percobaan. detail={attempts_meta}")
        # Fallback: tetap lanjutkan dengan membuat suggestions.json berbasis plan
        content_data = {"terms_to_define": [], "concepts_to_simplify": [], "image_descriptions": []}

    # Simpan konten mentah untuk pelacakan
    output_path = doc_path / "generated_content.json"
    try:
        with open(output_path, 'w', encoding='utf-8') as f:
            json.dump(content_data, f, indent=2)
    except Exception:
        # Jika gagal menulis, tetap lanjutkan membuat daftar saran
        pass

    # Bangun suggestions.json dengan menggabungkan plan + hasil generasi menjadi daftar datar
    suggestions: list[dict] = []
    plan = enrichment_plan

    # Peta bantu untuk lookup cepat dari hasil generasi
    gen_terms = {i.get("term"): i for i in content_data.get("terms_to_define", [])} if isinstance(content_data, dict) else {}
    gen_concepts = {i.get("identifier"): i for i in content_data.get("concepts_to_simplify", [])} if isinstance(content_data, dict) else {}
    gen_images = {i.get("image_file"): i for i in content_data.get("image_descriptions", [])} if isinstance(content_data, dict) else {}

    # Istilah untuk didefinisikan
    for idx, item in enumerate(plan.get("terms_to_define", []) or []):
        if isinstance(item, str):
            term = item
            original_context = item
            conf = 0.5
        else:
            term = item.get("term") or item.get("name")
            original_context = item.get("original_context") or item.get("context") or term or ""
            conf = float(item.get("confidence_score", 0.5))
        gen = gen_terms.get(term, {})
        definition = gen.get("definition") or gen.get("content") or ""
        suggestions.append({
            "id": f"term_{idx}",
            "type": "term_to_define",
            "original_context": original_context,
            "generated_content": definition,
            "confidence_score": conf,
            "status": "pending",
        })

    # Konsep untuk disederhanakan
    for idx, item in enumerate(plan.get("concepts_to_simplify", []) or []):
        if isinstance(item, str):
            identifier = item
            original_context = item
            conf = 0.5
        else:
            identifier = item.get("identifier")
            original_context = item.get("original_context") or item.get("paragraph_text") or identifier or ""
            conf = float(item.get("confidence_score", 0.5))
        gen = gen_concepts.get(identifier, {})
        simplified = gen.get("simplified_text") or gen.get("content") or ""
        suggestions.append({
            "id": f"concept_{idx}",
            "type": "concept_to_simplify",
            "original_context": original_context,
            "generated_content": simplified,
            "confidence_score": conf,
            "status": "pending",
        })

    # Gambar untuk dideskripsikan (plan bisa list[str] atau list[dict])
    images_plan = plan.get("images_to_describe", []) or []
    for idx, item in enumerate(images_plan):
        if isinstance(item, str):
            image_file = item
            original_context = f"[IMAGE_PLACEHOLDER: {image_file}]"
            conf = 0.5
        else:
            image_file = item.get("image_file") or item.get("filename")
            original_context = item.get("original_context") or f"[IMAGE_PLACEHOLDER: {image_file}]"
            conf = float(item.get("confidence_score", 0.5))
        gen = gen_images.get(image_file, {})
        desc = gen.get("description") or gen.get("content") or ""
        suggestions.append({
            "id": f"image_{idx}",
            "type": "image_description",
            "original_context": original_context,
            "generated_content": desc,
            "confidence_score": conf,
            "status": "pending",
        })

    # Tulis suggestions.json
    suggestions_path = doc_path / "suggestions.json"
    with open(suggestions_path, 'w', encoding='utf-8') as sf:
        json.dump(suggestions, sf, indent=2, ensure_ascii=False)

    logger.info(f"Fase 2 selesai. Konten hasil generasi disimpan di: {output_path}")
    logger.info(f"Daftar saran (human-in-the-loop) disimpan di: {suggestions_path}")
    return str(suggestions_path)

if __name__ == '__main__':
    if GOOGLE_API_KEY:
        genai.configure(api_key=GOOGLE_API_KEY)
    else:
        logger.error("GOOGLE_API_KEY tidak ditemukan. Harap set di file .env Anda.")

    base_artefacts_dir = Path(PIPELINE_ARTEFACTS_DIR)
    if not base_artefacts_dir.exists():
        logger.error(f"Direktori '{PIPELINE_ARTEFACTS_DIR}' tidak ditemukan. Jalankan phase_0 terlebih dahulu.")
    else:
        all_doc_dirs = [d for d in base_artefacts_dir.iterdir() if d.is_dir()]
        if not all_doc_dirs:
            logger.error(f"Tidak ada direktori dokumen di '{PIPELINE_ARTEFACTS_DIR}'.")
        else:
            latest_doc_dir = max(all_doc_dirs, key=lambda d: d.stat().st_mtime)
            logger.info(f"Menjalankan Fase 2 pada direktori: {latest_doc_dir}")
            generate_bulk_content(str(latest_doc_dir))