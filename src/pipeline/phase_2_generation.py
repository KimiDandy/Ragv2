import json
import re
from pathlib import Path
from langchain_openai import ChatOpenAI
from loguru import logger
import time
import asyncio
import statistics

from ..core.config import (
    CHAT_MODEL,
    PIPELINE_ARTEFACTS_DIR,
    PHASE2_CONCURRENCY,
    PHASE2_RPS,
    PHASE2_TOKEN_BUDGET,
)
from ..core.rate_limiter import AsyncLeakyBucket
from ..core.token_meter import TokenBudget

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

    # Controls
    sem = asyncio.Semaphore(int(PHASE2_CONCURRENCY))
    limiter = AsyncLeakyBucket(rps=float(PHASE2_RPS), capacity=max(int(PHASE2_CONCURRENCY), 1))
    budget = TokenBudget(total=int(PHASE2_TOKEN_BUDGET))

    async def _call_llm(prompt: str, timeout: float = 45.0, attempts: int = 2, max_out: int = 250) -> str:
        last_err = None
        for i in range(1, attempts + 1):
            try:
                # Token budget gating (approximate)
                if not budget.can_afford(prompt, max_out=max_out):
                    logger.warning("Phase-2 token budget exhausted; skipping remaining items")
                    return ""
                # Rate limit + concurrency
                async with sem:
                    await limiter.acquire()
                    # charge before call to account for concurrency
                    budget.charge(prompt, max_out=max_out)
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
        text = await _call_llm(prompt, max_out=220)
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
        text = await _call_llm(prompt, max_out=260)
        return {"identifier": ident, "simplified_text": text}
    # Progressive generation with checkpoints and metrics
    total_terms = len(terms_plan)
    total_concepts = len(concepts_plan)
    total_items = total_terms + total_concepts

    stats = {"llm_calls": 0, "latencies": [], "processed": 0}
    t0 = time.time()

    term_results: list[dict] = []
    concept_results: list[dict] = []
    suggestions_accum: list[dict] = []

    # Build workers that return both generation result and suggestion entry
    def _mk_term_worker(idx: int, item: dict):
        async def _worker():
            t_start = time.time()
            gen = await _gen_term(item)
            latency = time.time() - t_start
            stats["latencies"].append(latency)
            stats["llm_calls"] += 1

            # extract fields for suggestion
            if isinstance(item, str):
                term = item
                original_context = item
                conf = 0.5
            else:
                term = item.get("term") or item.get("name")
                original_context = item.get("original_context") or item.get("context") or term or ""
                conf = float(item.get("confidence_score", 0.5))
            definition = (
                gen.get("definition")
                or gen.get("definition_text")
                or gen.get("explanation")
                or gen.get("expanded_definition")
                or gen.get("text")
                or gen.get("content")
                or ""
            )
            suggestion = {
                "id": f"term_{idx}",
                "type": "term_to_define",
                "original_context": original_context,
                "generated_content": definition,
                "confidence_score": conf,
                "status": "pending",
            }
            return gen, suggestion
        return _worker

    def _mk_concept_worker(idx: int, item: dict):
        async def _worker():
            t_start = time.time()
            gen = await _gen_concept(item)
            latency = time.time() - t_start
            stats["latencies"].append(latency)
            stats["llm_calls"] += 1

            if isinstance(item, str):
                identifier = item
                original_context = item
                conf = 0.5
            else:
                identifier = item.get("identifier") or item.get("id") or item.get("name")
                original_context = item.get("original_context") or item.get("paragraph_text") or identifier or ""
                conf = float(item.get("confidence_score", 0.5))
            simplified = (
                gen.get("simplified_text")
                or gen.get("simplified")
                or gen.get("explanation")
                or gen.get("summary")
                or gen.get("text")
                or gen.get("content")
                or ""
            )
            suggestion = {
                "id": f"concept_{idx}",
                "type": "concept_to_simplify",
                "original_context": original_context,
                "generated_content": simplified,
                "confidence_score": conf,
                "status": "pending",
            }
            return gen, suggestion
        return _worker

    # Create tasks
    tasks: list[asyncio.Task] = []
    for idx, it in enumerate(terms_plan):
        tasks.append(asyncio.create_task(_mk_term_worker(idx, it)()))
    for idx, it in enumerate(concepts_plan):
        tasks.append(asyncio.create_task(_mk_concept_worker(idx, it)()))

    # Helpers to write checkpoints and progress
    def _write_progress():
        lat = stats.get("latencies", [])
        p50 = statistics.median(lat) if lat else 0.0
        p95 = (statistics.quantiles(lat, n=100)[94] if len(lat) >= 20 else max(lat) if lat else 0.0)
        metrics = {
            "total_items": total_items,
            "processed": stats["processed"],
            "llm_calls": stats["llm_calls"],
            "duration_sec": time.time() - t0,
            "p50_latency_sec": p50,
            "p95_latency_sec": p95,
            "token_budget_used": budget.used,
            "token_budget_total": budget.total,
            "percent": (stats["processed"] / total_items) if total_items else 1.0,
        }
        try:
            (doc_path / "phase_2_metrics.json").write_text(json.dumps(metrics, indent=2, ensure_ascii=False), encoding="utf-8")
            (doc_path / "phase_2_progress.json").write_text(json.dumps(metrics, indent=2, ensure_ascii=False), encoding="utf-8")
        except Exception:
            pass

    def _write_partial_suggestions():
        try:
            (doc_path / "suggestions_partial.json").write_text(
                json.dumps(suggestions_accum, indent=2, ensure_ascii=False), encoding="utf-8"
            )
        except Exception:
            pass

    checkpoint_every = 10
    # Consume results progressively
    for coro in asyncio.as_completed(tasks):
        try:
            gen, suggestion = await coro
        except Exception as e:
            logger.debug(f"Worker error (phase-2): {e}")
            continue
        if suggestion.get("type") == "term_to_define":
            term_results.append(gen)
        else:
            concept_results.append(gen)
        suggestions_accum.append(suggestion)
        stats["processed"] += 1
        if stats["processed"] % checkpoint_every == 0:
            _write_partial_suggestions()
            _write_progress()

    # Final writes
    content_data = {
        "terms_to_define": term_results,
        "concepts_to_simplify": concept_results,
    }
    output_path = doc_path / "generated_content.json"
    try:
        with open(output_path, 'w', encoding='utf-8') as f:
            json.dump(content_data, f, indent=2)
    except Exception:
        pass

    # Write final suggestions and remove partial if exists
    suggestions_path = doc_path / "suggestions.json"
    with open(suggestions_path, 'w', encoding='utf-8') as sf:
        json.dump(suggestions_accum, sf, indent=2, ensure_ascii=False)

    # final progress write
    _write_progress()
    try:
        part = doc_path / "suggestions_partial.json"
        if part.exists():
            part.unlink(missing_ok=True)
    except Exception:
        pass

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