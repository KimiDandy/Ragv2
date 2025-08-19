import json
from pathlib import Path
import google.generativeai as genai
from loguru import logger
import re

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

    prompt = f"""Role: You are a multi-disciplinary research analyst.
Task: Read the following Markdown document thoroughly. Identify ALL items that require further explanation or enrichment for a complete understanding. DO NOT provide the explanations now.
Format Output: You MUST respond ONLY with a valid JSON object, with no additional text before or after it.

Required JSON Structure:
{{
  "terms_to_define": [
    {{"term": "string", "context": "string (the full sentence where the term appears)"}}
  ],
  "concepts_to_simplify": [
    {{"identifier": "string (first 10 words of the paragraph)", "paragraph_text": "string (the full text of the complex paragraph)"}}
  ],
  "images_to_describe": ["string (the filename from the placeholder, e.g., 'image_01.png')"],
  "inferred_connections": [
    {{"from_concept": "string (source paragraph identifier)", "to_concept": "string (destination paragraph identifier)", "relationship_type": "string (e.g., 'provides an example for', 'is a counter-argument to')"}}
  ]
}}

Document to Analyze:
---
{markdown_content}
---
"""

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

    # Attempt 1
    response = model.generate_content(prompt)
    raw_text = _extract_text(response)
    raw_out = Path(doc_output_dir) / "enrichment_plan_raw.txt"
    try:
        with open(raw_out, 'w', encoding='utf-8') as f:
            f.write(raw_text)
    except Exception:
        pass

    plan_data = None
    if raw_text:
        json_str = raw_text.replace('```json', '').replace('```', '').strip()
        try:
            plan_data = json.loads(json_str)
        except json.JSONDecodeError:
            m = re.search(r"\{.*\}", json_str, re.DOTALL)
            if m:
                plan_data = json.loads(m.group(0))

    # Retry once with stricter instruction if needed
    if plan_data is None:
        strict_prompt = (
            prompt +
            "\n\nSTRICT REQUIREMENT: Respond ONLY with a single valid JSON object matching the exact schema. "
            "Do NOT include prose, explanations, or code fences."
        )
        response2 = model.generate_content(strict_prompt)
        raw_text2 = _extract_text(response2)
        try:
            with open(Path(doc_output_dir) / "enrichment_plan_raw_retry.txt", 'w', encoding='utf-8') as f:
                f.write(raw_text2)
        except Exception:
            pass
        if raw_text2:
            json_str2 = raw_text2.replace('```json', '').replace('```', '').strip()
            try:
                plan_data = json.loads(json_str2)
            except json.JSONDecodeError:
                m2 = re.search(r"\{.*\}", json_str2, re.DOTALL)
                if m2:
                    plan_data = json.loads(m2.group(0))

    if plan_data is None:
        fr = None
        try:
            fr = response.candidates[0].finish_reason if response.candidates else None
        except Exception:
            pass
        logger.error(f"Gagal mendapatkan JSON rencana enrichment. finish_reason={fr}")
        return ""

    plan_output_path = Path(doc_output_dir) / "enrichment_plan.json"
    with open(plan_output_path, 'w', encoding='utf-8') as f:
        json.dump(plan_data, f, indent=2)

    logger.info(f"Fase 1 selesai. Rencana enrichment disimpan di: {plan_output_path}")
    return str(plan_output_path)

if __name__ == '__main__':
    base_artefacts_dir = Path(PIPELINE_ARTEFACTS_DIR)
    if not base_artefacts_dir.exists():
        logger.error(f"'{PIPELINE_ARTEFACTS_DIR}' directory not found. Run phase_0 first.")
    else:
        all_doc_dirs = [d for d in base_artefacts_dir.iterdir() if d.is_dir()]
        if not all_doc_dirs:
            logger.error(f"No document directories found in '{PIPELINE_ARTEFACTS_DIR}'.")
        else:
            latest_doc_dir = max(all_doc_dirs, key=lambda d: d.stat().st_mtime)
            markdown_file = latest_doc_dir / "markdown_v1.md"
            logger.info(f"Running Phase 1 on: {markdown_file}")
            create_enrichment_plan(str(markdown_file), str(latest_doc_dir))