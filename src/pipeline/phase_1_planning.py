import json
from pathlib import Path
from typing import List, Dict, Any, Tuple
from hashlib import sha256
import asyncio
import time
import re
import statistics
from pydantic import BaseModel, Field
from langchain_openai import ChatOpenAI
from loguru import logger

from ..core.config import (
    CHAT_MODEL,
    PIPELINE_ARTEFACTS_DIR,
    PHASE1_CONCURRENCY,
    PHASE1_RPS,
    PHASE1_TOKEN_BUDGET,
)
from ..core.rate_limiter import AsyncLeakyBucket
from ..core.token_meter import TokenBudget, estimate_tokens
from ..core.json_validators import validate_enrichment_plan
from ..core import local_cache


class PlanItem(BaseModel):
    kind: str  
    key: str
    original_context: str
    confidence_score: float = Field(ge=0.0, le=1.0)
    provenance: Dict[str, Any]


class PlanOutput(BaseModel):
    terms_to_define: List[Dict[str, Any]] = []
    concepts_to_simplify: List[Dict[str, Any]] = []
    inferred_connections: List[Dict[str, Any]] = []


def _norm_key(s: str) -> str:
    return re.sub(r"\s+", " ", (s or "").strip().lower())[:200]


def _cache_path(doc_dir: Path, key: str) -> Path:
    cdir = doc_dir / "cache_phase1"
    cdir.mkdir(exist_ok=True)
    return cdir / f"{key}.json"


def _hash_for_segment(model: str, prompt: str, text: str) -> str:
    h = sha256()
    h.update(model.encode("utf-8"))
    h.update(b"\x00")
    h.update(prompt.encode("utf-8"))
    h.update(b"\x00")
    h.update(text.encode("utf-8"))
    return h.hexdigest()


def _build_prompt(segment_text: str) -> str:
    prompt_intro = (
        "Peran: Anda adalah analis riset multidisiplin.\n"
        "Tugas: Baca paragraf berikut. Identifikasi item yang butuh definisi/simplifikasi atau koneksi implisit.\n"
        "Balas HANYA satu objek JSON valid sesuai skema.\n"
    )
    schema_block = (
        "{"
        "\n  \"terms_to_define\": [{\n    \"term\": \"string\",\n    \"original_context\": \"string\",\n    \"confidence_score\": 0.0\n  }],"
        "\n  \"concepts_to_simplify\": [{\n    \"identifier\": \"string\",\n    \"original_context\": \"string\",\n    \"confidence_score\": 0.0\n  }],"
        "\n  \"inferred_connections\": [{\n    \"from_concept\": \"string\",\n    \"to_concept\": \"string\",\n    \"relationship_type\": \"string\",\n    \"confidence_score\": 0.0,\n    \"original_context\": \"string\"\n  }]\n}"
    )
    return (
        prompt_intro
        + "\nSkema Wajib:\n"
        + schema_block
        + "\n\nParagraf:\n---\n"
        + segment_text
        + "\n---\n"
    )


def _build_skim_prompt(segment_text: str) -> str:
    prompt_intro = (
        "Peran: Anda adalah analis cepat dan hemat token.\n"
        "Tugas: Baca paragraf berikut. Jika perlu, pilih MAKS 1 istilah untuk didefinisikan dan MAKS 1 konsep untuk disederhanakan.\n"
        "Balas HANYA JSON valid sesuai skema. Jangan tambahkan teks lain.\n"
    )
    schema_block = (
        "{" 
        "\n  \"terms_to_define\": [{\n    \"term\": \"string\",\n    \"original_context\": \"string\",\n    \"confidence_score\": 0.0\n  }],"
        "\n  \"concepts_to_simplify\": [{\n    \"identifier\": \"string\",\n    \"original_context\": \"string\",\n    \"confidence_score\": 0.0\n  }]\n}"
    )
    return (
        prompt_intro
        + "\nSkema Wajib:\n"
        + schema_block
        + "\n\nParagraf:\n---\n"
        + segment_text
        + "\n---\n"
        + "\nBatasan: maksimal 2 item total, kosongkan list jika tidak ada."
    )


async def _skim_one(chat: ChatOpenAI, limiter: AsyncLeakyBucket, budget: TokenBudget, seg: dict, sem: asyncio.Semaphore, stats: dict) -> List[PlanItem]:
    text = (seg.get("text", "") or "").strip()
    if not text:
        return []
    prompt = _build_skim_prompt(text)
    k = local_cache.key_for(_hash_for_segment(chat.model_name, prompt, text) + "|phase1_skim")
    cached = local_cache.get(k)
    if cached is not None:
        stats["cache_hits"] += 1
        return _to_items(cached, seg)

    max_out = 200
    if not budget.can_afford(prompt, max_out=max_out):
        logger.warning("Phase-1 token budget exhausted before processing all segments")
        return []

    t_start = time.time()
    async with sem:
        await limiter.acquire()
        budget.charge(prompt, max_out=max_out)
        try:
            resp = await chat.ainvoke(prompt)
            raw = getattr(resp, "content", None) or str(resp)
        except Exception as e:
            logger.warning(f"Skim call failed for segment {seg.get('segment_id')}: {e}")
            return []
    latency = time.time() - t_start
    stats.setdefault("latencies", []).append(latency)
    stats["llm_calls"] += 1

    parsed = _parse_json_strict(raw)
    if not isinstance(parsed, dict):
        return []
    ok, errs = validate_enrichment_plan(parsed)
    if not ok:
        logger.debug(f"Validator errors (skim) seg={seg.get('segment_id')}: {errs}")
        return []
    try:
        local_cache.set(k, parsed)
    except Exception:
        pass
    return _to_items(parsed, seg)


def _parse_json_strict(raw_text: str):
    if not raw_text:
        return None
    js = raw_text.replace('```json', '').replace('```', '').strip()
    try:
        return json.loads(js)
    except json.JSONDecodeError:
        m = re.search(r"(\{[\s\S]*\}|\[[\s\S]*\])", js)
        if m:
            try:
                return json.loads(m.group(0))
            except json.JSONDecodeError:
                return None
        return None


def _to_items(parsed: dict, seg: dict) -> List[PlanItem]:
    items: List[PlanItem] = []
    prov = {
        "segment_id": seg.get("segment_id"),
        "page": seg.get("page"),
        "char_start": seg.get("char_start"),
        "char_end": seg.get("char_end"),
        "header_path": seg.get("header_path"),
    }
    for obj in (parsed.get("terms_to_define") or []):
        if isinstance(obj, str):
            term = obj
            ctx = obj
            conf = 0.5
        else:
            term = obj.get("term") or obj.get("name")
            ctx = obj.get("original_context") or obj.get("context") or ""
            conf = float(obj.get("confidence_score", 0.5))
        if term:
            items.append(PlanItem(kind="term", key=term, original_context=ctx, confidence_score=conf, provenance=prov))

    for obj in (parsed.get("concepts_to_simplify") or []):
        if isinstance(obj, str):
            ident = obj
            ctx = obj
            conf = 0.5
        else:
            ident = obj.get("identifier") or obj.get("id") or obj.get("name")
            ctx = obj.get("original_context") or obj.get("paragraph_text") or ""
            conf = float(obj.get("confidence_score", 0.5))
        if ident:
            items.append(PlanItem(kind="concept", key=ident, original_context=ctx, confidence_score=conf, provenance=prov))

    for obj in (parsed.get("inferred_connections") or []):
        f = (obj or {}).get("from_concept")
        t = (obj or {}).get("to_concept")
        rel = (obj or {}).get("relationship_type")
        ctx = (obj or {}).get("original_context") or ""
        conf = float((obj or {}).get("confidence_score", 0.5))
        if f and t:
            items.append(PlanItem(kind="connection", key=f"{f} -> {t} ({rel})", original_context=ctx, confidence_score=conf, provenance=prov))
    return items


def _pre_score_and_select(segments: List[dict], max_candidates: int = 80, header_quota: int = 8) -> List[dict]:
    def header_root(seg: dict) -> str:
        hp = seg.get("header_path") or []
        return (hp[0] if hp else "ROOT")[:80]

    def score(seg: dict) -> float:
        sc = 0.0
        if seg.get("contains_entities"):
            sc += 1.0
        if seg.get("is_difficult"):
            sc += 0.7
        nr = float(seg.get("numeric_ratio") or 0.0)
        if nr < 0.2:
            sc += 0.2
        elif nr > 0.5:
            sc -= 0.3
        hp = " ".join(seg.get("header_path") or [])
        if re.search(r"\b(introduction|overview|summary|conclusion|results)\b", hp, re.I):
            sc += 0.2
        tl = len(seg.get("text") or "")
        sc += min(tl, 800) / 4000.0
        return sc

    segments_scored = [(score(s), s) for s in segments]
    segments_scored.sort(key=lambda x: x[0], reverse=True)
    picked: List[dict] = []
    per_header: Dict[str, int] = {}
    for _, s in segments_scored:
        h = header_root(s)
        if per_header.get(h, 0) >= header_quota:
            continue
        picked.append(s)
        per_header[h] = per_header.get(h, 0) + 1
        if len(picked) >= max_candidates:
            break
    return picked


def _reduce_items(items: List[PlanItem]) -> Dict[str, Any]:
    terms_map: Dict[str, Dict[str, Any]] = {}
    concepts_map: Dict[str, Dict[str, Any]] = {}
    connections: List[Dict[str, Any]] = []
    for it in items:
        if it.kind == "term":
            k = _norm_key(it.key)
            if k not in terms_map:
                terms_map[k] = {
                    "term": it.key,
                    "original_context": it.original_context,
                    "confidence_score": it.confidence_score,
                    "provenances": [it.provenance],
                }
            else:
                terms_map[k]["provenances"].append(it.provenance)
                terms_map[k]["confidence_score"] = max(terms_map[k]["confidence_score"], it.confidence_score)
        elif it.kind == "concept":
            k = _norm_key(it.key)
            if k not in concepts_map:
                concepts_map[k] = {
                    "identifier": it.key,
                    "original_context": it.original_context,
                    "confidence_score": it.confidence_score,
                    "provenances": [it.provenance],
                }
            else:
                concepts_map[k]["provenances"].append(it.provenance)
                concepts_map[k]["confidence_score"] = max(concepts_map[k]["confidence_score"], it.confidence_score)
        else:
            connections.append({
                "connection": it.key,
                "original_context": it.original_context,
                "confidence_score": it.confidence_score,
                "provenance": it.provenance,
            })
    return {
        "terms_to_define": list(terms_map.values()),
        "concepts_to_simplify": list(concepts_map.values()),
        "inferred_connections": connections,
    }


async def create_enrichment_plan(doc_output_dir: str) -> str:
    """Phase-1: Two-stage gating → Async skim (rate-limited, budgeted) → Reduce, with checkpoints and metrics."""
    doc_dir = Path(doc_output_dir)
    segments_path = doc_dir / "segments.json"
    if not segments_path.exists():
        logger.error(f"segments.json tidak ditemukan di {segments_path}")
        return ""

    with open(segments_path, "r", encoding="utf-8") as f:
        segments: List[dict] = json.load(f)

    preselected = _pre_score_and_select(segments, max_candidates=80, header_quota=8)
    logger.info(f"Phase-1 Stage-A: {len(preselected)}/{len(segments)} segmen dipilih untuk skim")

    chat = ChatOpenAI(model=CHAT_MODEL, temperature=0.2)
    sem = asyncio.Semaphore(int(PHASE1_CONCURRENCY))
    limiter = AsyncLeakyBucket(rps=float(PHASE1_RPS), capacity=max(int(PHASE1_CONCURRENCY), 1))
    budget = TokenBudget(total=int(PHASE1_TOKEN_BUDGET))

    stats: Dict[str, Any] = {"cache_hits": 0, "llm_calls": 0, "latencies": [], "processed": 0}
    t0 = time.time()

    items_collected: List[PlanItem] = []

    async def run_worker(seg: dict) -> List[PlanItem]:
        return await _skim_one(chat, limiter, budget, seg, sem, stats)

    tasks = [asyncio.create_task(run_worker(seg)) for seg in preselected]

    def write_checkpoint():
        partial = _reduce_items(items_collected)
        plan_ckpt = doc_dir / "plan_checkpoint.json"
        with open(plan_ckpt, "w", encoding="utf-8") as f:
            json.dump(partial, f, ensure_ascii=False, indent=2)
        lat = stats.get("latencies", [])
        p50 = statistics.median(lat) if lat else 0.0
        p95 = (statistics.quantiles(lat, n=100)[94] if len(lat) >= 20 else max(lat) if lat else 0.0)
        metrics = {
            "preselected": len(preselected),
            "total_segments": len(segments),
            "processed": stats["processed"],
            "llm_calls": stats["llm_calls"],
            "cache_hits": stats["cache_hits"],
            "duration_sec": time.time() - t0,
            "p50_latency_sec": p50,
            "p95_latency_sec": p95,
            "token_budget_used": budget.used,
            "token_budget_total": budget.total,
        }
        with open(doc_dir / "phase_1_metrics.json", "w", encoding="utf-8") as f:
            json.dump(metrics, f, ensure_ascii=False, indent=2)
        with open(doc_dir / "phase_1_progress.json", "w", encoding="utf-8") as f:
            json.dump(metrics, f, ensure_ascii=False, indent=2)

    checkpoint_every = 10
    for coro in asyncio.as_completed(tasks):
        try:
            res_items = await coro
        except Exception as e:
            logger.debug(f"Worker error: {e}")
            res_items = []
        items_collected.extend(res_items)
        stats["processed"] += 1
        if stats["processed"] % checkpoint_every == 0:
            write_checkpoint()

    final_plan_dict = _reduce_items(items_collected)
    plan = PlanOutput(
        terms_to_define=final_plan_dict.get("terms_to_define", []),
        concepts_to_simplify=final_plan_dict.get("concepts_to_simplify", []),
        inferred_connections=final_plan_dict.get("inferred_connections", []),
    )

    plan_path = doc_dir / "plan.json"
    with open(plan_path, "w", encoding="utf-8") as f:
        json.dump(plan.model_dump(), f, ensure_ascii=False, indent=2)

    compat_path = doc_dir / "enrichment_plan.json"
    with open(compat_path, "w", encoding="utf-8") as f:
        json.dump(plan.model_dump(), f, ensure_ascii=False, indent=2)
    lat = stats.get("latencies", [])
    p50 = statistics.median(lat) if lat else 0.0
    p95 = (statistics.quantiles(lat, n=100)[94] if len(lat) >= 20 else max(lat) if lat else 0.0)
    metrics = {
        "preselected": len(preselected),
        "total_segments": len(segments),
        "processed": stats["processed"],
        "llm_calls": stats["llm_calls"],
        "cache_hits": stats["cache_hits"],
        "duration_sec": time.time() - t0,
        "p50_latency_sec": p50,
        "p95_latency_sec": p95,
        "token_budget_used": budget.used,
        "token_budget_total": budget.total,
    }
    with open(doc_dir / "phase_1_metrics.json", "w", encoding="utf-8") as f:
        json.dump(metrics, f, ensure_ascii=False, indent=2)
    with open(doc_dir / "phase_1_progress.json", "w", encoding="utf-8") as f:
        json.dump(metrics, f, ensure_ascii=False, indent=2)

    logger.info(f"Phase-1 selesai. plan.json disimpan di: {plan_path}")
    return str(plan_path)


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
            logger.info(f"Menjalankan Fase 1 pada direktori: {latest_doc_dir}")
            asyncio.run(create_enrichment_plan(str(latest_doc_dir)))