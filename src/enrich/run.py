from __future__ import annotations

import asyncio
import json
import time
from dataclasses import asdict
from pathlib import Path
from typing import Any, Dict, List, Tuple

from loguru import logger

from ..core.config import (
    PIPELINE_ARTEFACTS_DIR,
    PHASE2_TOKEN_BUDGET,
    PHASE2_CONCURRENCY,
    PHASE2_RPS,
)
from ..core.token_meter import TokenBudget
from ..core.rate_limiter import AsyncLeakyBucket
from ..planner.reduce import normalize_label
from .enricher import EnrichItem, enrich_sketch, enrich_refine
from .suggestions import build_suggestions, write_suggestions, write_suggestions_partial
from ..obs.metrics import emit, is_cancelled


def _load_plan(doc_dir: Path) -> Dict[str, Any]:
    plan_path = doc_dir / "plan.json"
    if not plan_path.exists():
        raise FileNotFoundError("plan.json tidak ditemukan. Jalankan Phase-1 planner lebih dulu.")
    return json.loads(plan_path.read_text(encoding="utf-8"))


def _select_items_from_plan(plan: Dict[str, Any]) -> List[EnrichItem]:
    out: List[EnrichItem] = []
    def _pack(itype: str, entry: Dict[str, Any]):
        label = str(entry.get("label") or "").strip()
        provs = entry.get("provenances") or []
        prov = provs[0] if provs else {}
        # normalize provenance fields
        prov = {
            "seg_id": prov.get("seg_id"),
            "page": int(prov.get("page") or 0),
            "char": [int((prov.get("char") or [0, 0])[0]), int((prov.get("char") or [0, 0])[1])],
            "header_path": list(prov.get("header_path") or []),
        }
        score = float(entry.get("score") or 0.0)
        return EnrichItem(doc_id="", type=itype, label=label, provenance=prov, score=score)

    for e in (plan.get("terms_to_define") or []):
        out.append(_pack("term", e))
    for e in (plan.get("concepts_to_simplify") or []):
        out.append(_pack("concept", e))

    # sort by plan score descending
    out.sort(key=lambda x: float(x.score or 0.0), reverse=True)
    return out


def _key_for_map(it: EnrichItem) -> str:
    return f"{it.type}:{normalize_label(it.label)}"


async def _write_partial(doc_dir: Path, generated: Dict[str, Dict[str, Any]], progress: Dict[str, Any]):
    # generated_content.json (map)
    (doc_dir / "generated_content.json").write_text(
        json.dumps(generated, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    # suggestions_partial.json derived from current map
    try:
        sugs = build_suggestions(doc_dir, generated)
        write_suggestions_partial(doc_dir, sugs)
    except Exception as e:
        logger.warning(f"Gagal menulis suggestions_partial.json: {e}")
    # progress
    (doc_dir / "phase_2_progress.json").write_text(
        json.dumps(progress, ensure_ascii=False, indent=2), encoding="utf-8"
    )


async def run_phase2_enrichment(
    document_id: str,
    *,
    prompt_version: str = "v1",
    eager_top_n: int = 100,
    refine_top_n: int = 60,
) -> Dict[str, Any]:
    """
    Orchestrate Phase-2 enrichment.
    - Select top-N items from plan
    - Run sketch for all (respect budget/RPS/concurrency)
    - Run refine for top-M by sketch confidence
    - Write partials frequently and final artefacts
    """
    doc_dir = Path(PIPELINE_ARTEFACTS_DIR) / document_id
    doc_dir.mkdir(parents=True, exist_ok=True)

    # emit start metrics (best-effort)
    start_ts = time.time()
    try:
        emit(document_id, "phase-2", "start", values={"targets_hint": int(eager_top_n or 0), "refine_top_n": int(refine_top_n or 0)})
    except Exception:
        pass

    plan = _load_plan(doc_dir)
    items = _select_items_from_plan(plan)
    targets = items[: max(1, int(eager_top_n))]

    budget = TokenBudget(PHASE2_TOKEN_BUDGET)
    rps = AsyncLeakyBucket(PHASE2_RPS)
    sem = asyncio.Semaphore(max(1, int(PHASE2_CONCURRENCY)))

    generated: Dict[str, Dict[str, Any]] = {}

    # progress
    progress: Dict[str, Any] = {
        "status": "running",
        "targets": len(targets),
        "sketch_done": 0,
        "refine_done": 0,
        "token_used": 0,
    }

    # Early cancellation
    if is_cancelled(document_id):
        progress = {
            "status": "cancelled",
            "targets": len(targets),
            "sketch_done": 0,
            "refine_done": 0,
            "token_used": 0,
            "finished_at": time.time(),
            "duration_sec": round(time.time() - start_ts, 3),
        }
        await _write_partial(doc_dir, {}, progress)
        try:
            emit(document_id, "phase-2", "cancelled", values={"processed": 0, "total": len(targets)})
        except Exception:
            pass
        return {"document_id": document_id, **progress}

    # SKETCH step
    async def sketch_worker(it: EnrichItem):
        nonlocal generated
        key = _key_for_map(it)
        if key in generated:
            return
        if is_cancelled(document_id):
            return
        async with sem:
            res = await enrich_sketch(it, doc_dir, budget=budget, rps=rps, prompt_version=prompt_version)
        if res:
            generated[key] = {
                "label": res.get("label") or it.label,
                "type": res.get("type") or it.type,
                "mode": res.get("mode") or "sketch",
                "content": res.get("content") or "",
                "confidence": float(res.get("confidence") or 0.0),
                "provenance": res.get("provenance") or asdict(it.provenance) if hasattr(it.provenance, "__dict__") else it.provenance,
            }
            progress["sketch_done"] += 1
            progress["token_used"] = int(budget.used)
            if progress["sketch_done"] % 10 == 0:
                await _write_partial(doc_dir, generated, progress)

    await asyncio.gather(*(sketch_worker(it) for it in targets))

    # Cancellation after sketch
    if is_cancelled(document_id):
        progress["status"] = "cancelled"
        progress["finished_at"] = time.time()
        progress["duration_sec"] = round(time.time() - start_ts, 3)
        await _write_partial(doc_dir, generated, progress)
        try:
            emit(document_id, "phase-2", "cancelled", values={"processed": int(progress.get("sketch_done", 0)), "total": len(targets)})
        except Exception:
            pass
        return {"document_id": document_id, **progress}

    # REFINE step: choose top refine_top_n by sketch confidence
    ranked_keys = sorted(
        list(generated.keys()),
        key=lambda k: float(generated[k].get("confidence") or 0.0),
        reverse=True,
    )
    refine_keys = ranked_keys[: max(0, int(refine_top_n))]

    async def refine_worker(key: str):
        nonlocal generated
        obj = generated.get(key)
        if not obj:
            return
        if is_cancelled(document_id):
            return
        itype = obj.get("type") or "term"
        label = obj.get("label") or ""
        prov = obj.get("provenance") or {}
        item = EnrichItem(doc_id=document_id, type=itype, label=label, provenance=prov, score=0.0)
        async with sem:
            res = await enrich_refine(item, obj, doc_dir, budget=budget, rps=rps, prompt_version=prompt_version)
        if res:
            obj.update({
                "mode": "refine",
                "content": res.get("content") or obj.get("content") or "",
                "confidence": float(res.get("confidence") or obj.get("confidence") or 0.0),
                "provenance": res.get("provenance") or prov,
            })
            progress["refine_done"] += 1
            progress["token_used"] = int(budget.used)
            if progress["refine_done"] % 10 == 0:
                await _write_partial(doc_dir, generated, progress)

    if refine_keys:
        await asyncio.gather(*(refine_worker(k) for k in refine_keys))

    # Cancellation after refine
    if is_cancelled(document_id):
        progress["status"] = "cancelled"
        progress["finished_at"] = time.time()
        progress["duration_sec"] = round(time.time() - start_ts, 3)
        await _write_partial(doc_dir, generated, progress)
        try:
            emit(document_id, "phase-2", "cancelled", values={"processed": int(progress.get("refine_done", 0)), "total": len(refine_keys)})
        except Exception:
            pass
        return {"document_id": document_id, **progress}

    # finalize artefacts
    await _write_partial(doc_dir, generated, progress)
    sugs = build_suggestions(doc_dir, generated)
    write_suggestions(doc_dir, sugs)

    progress["status"] = "done"
    (doc_dir / "phase_2_progress.json").write_text(
        json.dumps(progress, ensure_ascii=False, indent=2), encoding="utf-8"
    )

    logger.info(f"[Enrichment] Done for {document_id}: targets={len(targets)} sketches={progress['sketch_done']} refines={progress['refine_done']}")
    try:
        emit(document_id, "phase-2", "end", values={
            "targets": len(targets),
            "sketch_done": int(progress.get("sketch_done", 0)),
            "refine_done": int(progress.get("refine_done", 0)),
            "token_used": int(progress.get("token_used", 0)),
        })
    except Exception:
        pass
    return {"document_id": document_id, **progress}
