import json
import re
from pathlib import Path
from langchain_openai import ChatOpenAI
from loguru import logger
import time
import asyncio

from ..core.config import CHAT_MODEL, PIPELINE_ARTEFACTS_DIR

# ChatOpenAI will read OPENAI_API_KEY from environment

async def generate_bulk_content(doc_output_dir: str) -> str:
    """
    Phase-2 (async): Generate enrichment content per item with targeted context.

    Optimizations for large docs (many pages/items):
    - No single giant prompt. Generate per item with its original_context only.
    - Concurrency with asyncio.Semaphore.
    - Timeouts and small retries per item.

    Returns path to suggestions.json.
    """
    doc_path = Path(doc_output_dir)
    markdown_path = doc_path / "markdown_v1.md"
    # Prefer structured plan.json produced by Phase-1; fallback to enrichment_plan.json
    plan_path = doc_path / "plan.json"
    if not plan_path.exists():
        plan_path = doc_path / "enrichment_plan.json"

    try:
        with open(markdown_path, 'r', encoding='utf-8') as f:
            markdown_content = f.read()
        with open(plan_path, 'r', encoding='utf-8') as f:
            enrichment_plan = json.load(f)
    except FileNotFoundError as e:
        logger.error(f"File yang dibutuhkan tidak ditemukan - {e}")
        return ""

    # Build targeted generation per item (no giant prompt)
    chat = ChatOpenAI(model=CHAT_MODEL, temperature=0.2)
    logger.info("Fase 2: menghasilkan konten per item dengan konteks terarah (async)...")

    # Load segments for richer context (optional)
    seg_map = {}
    try:
        segs = json.loads((doc_path / "segments.json").read_text(encoding="utf-8"))
        seg_map = {s.get("segment_id"): s for s in segs}
    except Exception:
        seg_map = {}

    terms_plan = list(enrichment_plan.get("terms_to_define", []) or [])
    concepts_plan = list(enrichment_plan.get("concepts_to_simplify", []) or [])

    # Helper to trim context length
    def _clip(t: str, n: int = 1200) -> str:
        t = (t or "").strip()
        return t if len(t) <= n else (t[:n].rsplit(" ", 1)[0] or t[:n])

    sem = asyncio.Semaphore(8)

    async def _call_llm(prompt: str, timeout: float = 45.0, attempts: int = 2) -> str:
        last_err = None
        for i in range(1, attempts + 1):
            try:
                async with sem:
                    resp = await asyncio.wait_for(chat.ainvoke(prompt), timeout=timeout)
                txt = getattr(resp, "content", None) or str(resp)
                return txt.strip()
            except Exception as e:
                last_err = e
                await asyncio.sleep(1.2 * i)
        logger.warning(f"LLM gagal setelah {attempts} percobaan: {last_err}")
        return ""

    async def _gen_term(item: dict) -> dict:
        term = item.get("term") or item.get("name") or ""
        ctx = item.get("original_context") or item.get("context") or ""
        # enrich ctx with segment text if available
        provs = item.get("provenances") or []
        if provs:
            sid = (provs[0] or {}).get("segment_id")
            seg_text = (seg_map.get(sid, {}) or {}).get("text") or ""
            if seg_text:
                ctx = ctx + "\n\nSegmen terkait:\n" + _clip(seg_text, 800)
        prompt = (
            "Definisikan istilah berikut secara ringkas, akurat, dan netral (2-4 kalimat).\n"
            f"Istilah: {term}\n"
            f"Konteks: {_clip(ctx, 1000)}\n\n"
            "Balas HANYA teks definisi tanpa format lain."
        )
        text = await _call_llm(prompt)
        return {"term": term, "definition": text}

    async def _gen_concept(item: dict) -> dict:
        ident = item.get("identifier") or item.get("id") or item.get("name") or ""
        ctx = item.get("original_context") or item.get("paragraph_text") or ""
        provs = item.get("provenances") or []
        if provs:
            sid = (provs[0] or {}).get("segment_id")
            seg_text = (seg_map.get(sid, {}) or {}).get("text") or ""
            if seg_text:
                ctx = ctx + "\n\nSegmen terkait:\n" + _clip(seg_text, 900)
        prompt = (
            "Sederhanakan konsep berikut agar mudah dipahami pembaca umum (3-6 kalimat).\n"
            f"Konsep: {ident}\n"
            f"Konteks: {_clip(ctx, 1100)}\n\n"
            "Balas HANYA teks penjelasan tanpa format lain."
        )
        text = await _call_llm(prompt)
        return {"identifier": ident, "simplified_text": text}

    term_tasks = [_gen_term(it) for it in terms_plan]
    concept_tasks = [_gen_concept(it) for it in concepts_plan]
    term_results, concept_results = await asyncio.gather(
        asyncio.gather(*term_tasks), asyncio.gather(*concept_tasks)
    )

    # Build content_data similar to previous shape for compatibility/logging
    content_data = {
        "terms_to_define": term_results,
        "concepts_to_simplify": concept_results,
    }

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
    gen_terms: dict[str, dict] = {}
    gen_concepts: dict[str, dict] = {}
    if isinstance(content_data, dict):
        # terms_to_define may be list[dict]|list[str]
        for i in content_data.get("terms_to_define", []) or []:
            if isinstance(i, dict):
                key = i.get("term") or i.get("name")
                if key:
                    gen_terms[key] = i
            elif isinstance(i, str):
                gen_terms[i] = {"content": i}

        # concepts_to_simplify may be list[dict]|list[str]
        for i in content_data.get("concepts_to_simplify", []) or []:
            if isinstance(i, dict):
                key = i.get("identifier") or i.get("id") or i.get("name")
                if key:
                    gen_concepts[key] = i
            elif isinstance(i, str):
                gen_concepts[i] = {"content": i}

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
        # Beberapa kemungkinan nama field dari LLM
        definition = (
            gen.get("definition")
            or gen.get("definition_text")
            or gen.get("explanation")
            or gen.get("expanded_definition")
            or gen.get("text")
            or gen.get("content")
            or ""
        )
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
        simplified = (
            gen.get("simplified_text")
            or gen.get("simplified")
            or gen.get("explanation")
            or gen.get("summary")
            or gen.get("text")
            or gen.get("content")
            or ""
        )
        suggestions.append({
            "id": f"concept_{idx}",
            "type": "concept_to_simplify",
            "original_context": original_context,
            "generated_content": simplified,
            "confidence_score": conf,
            "status": "pending",
        })

    # teks-only: tidak ada gambar untuk dideskripsikan

    # Tulis suggestions.json
    suggestions_path = doc_path / "suggestions.json"
    with open(suggestions_path, 'w', encoding='utf-8') as sf:
        json.dump(suggestions, sf, indent=2, ensure_ascii=False)

    logger.info(f"Fase 2 selesai. Konten hasil generasi disimpan di: {output_path}")
    logger.info(f"Daftar saran (human-in-the-loop) disimpan di: {suggestions_path}")
    return str(suggestions_path)

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
            logger.info(f"Menjalankan Fase 2 pada direktori: {latest_doc_dir}")
            asyncio.run(generate_bulk_content(str(latest_doc_dir)))