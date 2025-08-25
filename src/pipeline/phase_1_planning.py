import json
from pathlib import Path
import google.generativeai as genai
from loguru import logger
import re
import time

from ..core.config import GOOGLE_API_KEY, PLANNING_MODEL, PIPELINE_ARTEFACTS_DIR


genai.configure(api_key=GOOGLE_API_KEY)

def create_enrichment_plan(markdown_path: str, doc_output_dir: str) -> str:
    """
    Menganalisis dokumen markdown untuk membuat rencana enrichment yang komprehensif.

    Fungsi ini adalah Fase 1 dari proyek Genesis-RAG.
    Fungsi ini membaca konten dari markdown_v1.md, mengirimkannya ke Gemini API dengan
    prompt spesifik, dan menyimpan hasilnya sebagai file JSON (enrichment_plan.json).

    Args:
        markdown_path (str): Path menuju file markdown_v1.md.
        doc_output_dir (str): Direktori tempat menyimpan file enrichment_plan.json.

    Returns:
        str: Path menuju file enrichment_plan.json yang telah dibuat.
    """
    try:
        with open(markdown_path, 'r', encoding='utf-8') as f:
            markdown_content = f.read()
    except FileNotFoundError:
        logger.error(f"File markdown tidak ditemukan di {markdown_path}")
        return ""

    prompt_intro = """Peran: Anda adalah analis riset multidisiplin.
Tugas: Baca dokumen Markdown berikut secara menyeluruh. Identifikasi SEMUA item yang membutuhkan penjelasan lebih lanjut atau penyempurnaan agar mudah dipahami. JANGAN berikan penjelasannya sekarang.
Format Keluaran: Anda HARUS membalas HANYA dengan sebuah objek JSON valid, tanpa teks tambahan apa pun sebelum atau sesudahnya.

PENTING: Untuk setiap item yang diidentifikasi, sertakan dua field tambahan:
- original_context: kalimat/paragraf persis (atau baris placeholder gambar persis) yang memicu item ini.
- confidence_score: angka pecahan antara 0.0 hingga 1.0 yang menunjukkan keyakinan Anda bahwa item tersebut perlu disempurnakan.

Struktur JSON yang Diharuskan:
"""

    schema_block = """
{
  "terms_to_define": [
    {
      "term": "string",
      "original_context": "string (kalimat lengkap tempat istilah muncul)",
      "confidence_score": 0.0
    }
  ],
  "concepts_to_simplify": [
    {
      "identifier": "string (10 kata pertama paragraf)",
      "original_context": "string (teks paragraf kompleks lengkap)",
      "confidence_score": 0.0
    }
  ],
  "images_to_describe": [
    {
      "image_file": "string (nama file dari placeholder, misal 'image_p3_42.png')",
      "original_context": "string (baris placeholder persis, misal '[IMAGE_PLACEHOLDER: image_p3_42.png]')",
      "confidence_score": 0.0
    }
  ],
  "inferred_connections": [
    {
      "from_concept": "string (identifier paragraf sumber)",
      "to_concept": "string (identifier paragraf tujuan)",
      "relationship_type": "string (contoh: 'memberikan contoh untuk', 'adalah sanggahan terhadap')",
      "confidence_score": 0.0,
      "original_context": "string (kalimat/paragraf yang menunjukkan keterkaitan ini)"
    }
  ]
}
"""

    prompt = (
        prompt_intro
        + "\n"
        + schema_block
        + "\n\nDokumen untuk Dianalisis:\n---\n"
        + markdown_content
        + "\n---\n"
    )

    logger.info("Mengirim permintaan ke Gemini untuk membuat rencana enrichment...")
    model = genai.GenerativeModel(
        PLANNING_MODEL,
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

    # Retry hingga 3x dengan backoff jika JSON tidak valid
    max_attempts = 3
    plan_data = None
    last_finish_reason = None
    for attempt in range(1, max_attempts + 1):
        try:
            if attempt == 1:
                prompt_to_send = prompt
            else:
                prompt_to_send = (
                    prompt
                    + "\n\nPERSYARATAN KETAT: Balas HANYA dengan satu objek JSON valid yang sesuai skema di atas. "
                      "JANGAN sertakan narasi, penjelasan, atau code fence."
                )

            response = model.generate_content(prompt_to_send)
            raw_text = _extract_text(response)
            try:
                with open(Path(doc_output_dir) / f"enrichment_plan_raw_attempt{attempt}.txt", 'w', encoding='utf-8') as f:
                    f.write(raw_text)
            except Exception:
                pass

            if raw_text:
                json_str = raw_text.replace('```json', '').replace('```', '').strip()
                try:
                    plan_data = json.loads(json_str)
                except json.JSONDecodeError:
                    m = re.search(r"\{.*\}", json_str, re.DOTALL)
                    if m:
                        try:
                            plan_data = json.loads(m.group(0))
                        except json.JSONDecodeError:
                            plan_data = None

            if plan_data is not None:
                break

            # log alasan kegagalan percobaan
            try:
                last_finish_reason = response.candidates[0].finish_reason if response.candidates else None
            except Exception:
                last_finish_reason = None
            logger.warning(f"Percobaan Fase 1 (planning) ke-{attempt} gagal parsing JSON. finish_reason={last_finish_reason}")

        except Exception as e:
            logger.warning(f"Percobaan Fase 1 (planning) ke-{attempt} error: {e}")

        if attempt < max_attempts:
            time.sleep(1.5 ** attempt)

    if plan_data is None:
        fr = last_finish_reason
        logger.error(f"Gagal mendapatkan JSON rencana enrichment. finish_reason={fr}")
        image_placeholders = []
        try:
            for m in re.finditer(r"\[IMAGE_PLACEHOLDER:\s*([^\]]+)\]", markdown_content or ""):
                fname = (m.group(1) or "").strip()
                if fname:
                    image_placeholders.append({
                        "image_file": fname,
                        "original_context": m.group(0),
                        "confidence_score": 0.4,
                    })
        except Exception:
            image_placeholders = []

        plan_data = {
            "terms_to_define": [],
            "concepts_to_simplify": [],
            "images_to_describe": image_placeholders,
            "inferred_connections": [],
        }
        logger.warning("Membuat enrichment_plan.json minimal sebagai fallback (list kosong + deteksi gambar)")

    plan_output_path = Path(doc_output_dir) / "enrichment_plan.json"
    with open(plan_output_path, 'w', encoding='utf-8') as f:
        json.dump(plan_data, f, indent=2)

    logger.info(f"Fase 1 selesai. Rencana enrichment disimpan di: {plan_output_path}")
    return str(plan_output_path)

if __name__ == '__main__':
    base_artefacts_dir = Path(PIPELINE_ARTEFACTS_DIR)
    if not base_artefacts_dir.exists():
        logger.error(f"Direktori '{PIPELINE_ARTEFACTS_DIR}' tidak ditemukan. Jalankan phase_0 terlebih dahulu.")
    else:
        all_doc_dirs = [d for d in base_artefacts_dir.iterdir() if d.is_dir()]
        if not all_doc_dirs:
            logger.error(f"Tidak ada direktori dokumen di '{PIPELINE_ARTEFACTS_DIR}'.")
        else:
            latest_doc_dir = max(all_doc_dirs, key=lambda d: d.stat().st_mtime)
            markdown_file = latest_doc_dir / "markdown_v1.md"
            logger.info(f"Menjalankan Fase 1 pada: {markdown_file}")
            create_enrichment_plan(str(markdown_file), str(latest_doc_dir))