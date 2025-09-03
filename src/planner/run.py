from __future__ import annotations

import asyncio
import json
import time
from pathlib import Path
from typing import Any, Dict, List

from loguru import logger
import contextlib

from ..core.config import (
    PIPELINE_ARTEFACTS_DIR,
    PHASE1_TOKEN_BUDGET,
    PHASE1_RPS,
    PHASE1_CONCURRENCY,
)
from ..core.token_meter import TokenBudget
from ..core.rate_limiter import AsyncLeakyBucket
from .gating import select_candidates
from .skim import run_skim_async
from .reduce import build_plan, write_plan_json
from ..obs.metrics import emit, is_cancelled, flush_metrics


async def _write_json_atomic(path: Path, obj: Dict[str, Any]):
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(obj, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(path)


async def run_phase1_planner(doc_id: str, prompt_version: str = "v1", force: bool = False) -> Dict[str, Any]:
    """
    Orchestrates Phase-1 (Planner): Gating -> Skim -> Reduce.
    Writes:
      - skim_results.jsonl (append per result)
      - plan_partial.json (periodic)
      - plan.json (final)
      - phase_1_progress.json (status metrics)
    """
    doc_dir = Path(PIPELINE_ARTEFACTS_DIR) / doc_id
    seg_path = doc_dir / "segments.json"
    shards_path = doc_dir / "shards.json"
    plan_path = doc_dir / "plan.json"
    progress_path = doc_dir / "phase_1_progress.json"
    partial_path = doc_dir / "plan_partial.json"
    skim_jsonl = doc_dir / "skim_results.jsonl"

    if plan_path.exists() and not force:
        logger.info(f"[Planner] plan.json already exists for {doc_id}; reuse. Pass force=True to overwrite.")
        return {"status": "skipped", "document_id": doc_id}

    if not seg_path.exists() or not shards_path.exists():
        raise FileNotFoundError("segments.json atau shards.json tidak ditemukan. Jalankan Sprint-1 terlebih dahulu.")

    segments: List[Dict[str, Any]] = json.loads(seg_path.read_text(encoding="utf-8"))
    shards_obj: Dict[str, Any] = json.loads(shards_path.read_text(encoding="utf-8"))

    # Step A: Gating
    candidates = select_candidates(segments, shards_obj)
    preselected = len(candidates)
    total_segments = len(segments)
    logger.info(f"[Planner] Gating selected {preselected}/{total_segments} candidates for {doc_id}")
    # emit start
    try:
        emit(doc_id, "phase-1", "start", values={"total": total_segments, "preselected": preselected})
    except Exception:
        pass

    # Initialize progress
    start_ts = time.time()
    await _write_json_atomic(progress_path, {
        "phase": "phase-1",
        "status": "running",
        "document_id": doc_id,
        "segments_total": total_segments,
        "preselected": preselected,
        "processed": 0,
        "token_used": 0,
        "timeouts": 0,
        "cache_hits": 0,
        "p50": 0,
        "p95": 0,
        "started_at": start_ts,
    })

    # Step B: Skim (async with guards)
    budget = TokenBudget(total=int(PHASE1_TOKEN_BUDGET))
    limiter = AsyncLeakyBucket(rps=float(PHASE1_RPS), capacity=10)

    stop_progress = False

    async def _progress_poller():
        # Update processed count by reading JSONL lines periodically and write partial plan every 50 lines
        last_partial_at = 0
        while not stop_progress:
            try:
                if is_cancelled(doc_id):
                    break
                processed = 0
                lines = []
                if skim_jsonl.exists():
                    with skim_jsonl.open("r", encoding="utf-8") as f:
                        lines = f.readlines()
                processed = len(lines)
                # write partial plan every 50
                if processed >= 1 and processed // 50 > last_partial_at:
                    last_partial_at = processed // 50
                    try:
                        current = [json.loads(ln) for ln in lines if ln.strip()]
                        # build a small partial plan (cap at 180 to stay light)
                        plan_partial = build_plan(current, segments, top_total_min=100, top_total_max=180)
                        partial_path.write_text(json.dumps(plan_partial, ensure_ascii=False, indent=2), encoding="utf-8")
                    except Exception as e:
                        logger.warning(f"[Planner] Failed to write partial plan: {e}")
                prog = {
                    "phase": "phase-1",
                    "status": "running",
                    "document_id": doc_id,
                    "segments_total": total_segments,
                    "preselected": preselected,
                    "processed": processed,
                    "token_used": int(budget.used),
                    "timeouts": 0,
                    "cache_hits": 0,
                    "started_at": start_ts,
                }
                await _write_json_atomic(progress_path, prog)
            except Exception:
                pass
            await asyncio.sleep(1.0)

    poller_task = asyncio.create_task(_progress_poller())
    try:
        # cancellation before skim
        if is_cancelled(doc_id):
            stop_progress = True
            await _write_json_atomic(progress_path, {
                "phase": "phase-1",
                "status": "cancelled",
                "document_id": doc_id,
                "segments_total": total_segments,
                "preselected": preselected,
                "processed": 0,
                "token_used": int(budget.used),
                "timeouts": 0,
                "cache_hits": 0,
                "started_at": start_ts,
                "finished_at": time.time(),
            })
            try:
                emit(doc_id, "phase-1", "cancelled", values={"processed": 0, "total": preselected})
            except Exception:
                pass
            return {"status": "cancelled", "document_id": doc_id}

        skim_results, metrics = await run_skim_async(
            candidates,
            artefacts_dir=doc_dir,
            budget=budget,
            rps=limiter,
            prompt_version=prompt_version,
            timeout_s=8,
            retry=1,
            max_out=40,
            concurrency=int(PHASE1_CONCURRENCY),
        )
    finally:
        stop_progress = True
        with contextlib.suppress(Exception):
            await poller_task
        # Force-flush metrics so logs/metrics.jsonl is written even for short runs
        with contextlib.suppress(Exception):
            flush_metrics(doc_id)

    # cancelled during skim?
    if is_cancelled(doc_id):
        done_cancel = {
            "phase": "phase-1",
            "status": "cancelled",
            "document_id": doc_id,
            "segments_total": total_segments,
            "preselected": preselected,
            "processed": int(metrics.get("processed", 0)),
            "token_used": int(metrics.get("token_used", 0)),
            "timeouts": int(metrics.get("timeouts", 0)),
            "cache_hits": int(metrics.get("cache_hits", 0)),
            "finished_at": time.time(),
            "duration_sec": round(time.time() - start_ts, 3),
        }
        await _write_json_atomic(progress_path, done_cancel)
        try:
            emit(doc_id, "phase-1", "cancelled", values={"processed": done_cancel["processed"], "total": preselected})
        except Exception:
            pass
        with contextlib.suppress(Exception):
            flush_metrics(doc_id)
        return done_cancel

    # Step C: Reduce -> plan.json
    plan = build_plan(
        skim_results=skim_results,
        segments=segments,
        cluster_threshold=0.82,
        top_total_min=200,
        top_total_max=300,
    )
    write_plan_json(plan, doc_dir)

    # Final progress
    done = {
        "phase": "phase-1",
        "status": "done",
        "document_id": doc_id,
        "segments_total": total_segments,
        "preselected": preselected,
        "processed": int(metrics.get("processed", 0)),
        "token_used": int(metrics.get("token_used", 0)),
        "timeouts": int(metrics.get("timeouts", 0)),
        "cache_hits": int(metrics.get("cache_hits", 0)),
        "finished_at": time.time(),
        "duration_sec": round(time.time() - start_ts, 3),
    }
    await _write_json_atomic(progress_path, done)
    try:
        emit(doc_id, "phase-1", "end", values={"processed": done["processed"], "total": preselected})
    except Exception:
        pass
    with contextlib.suppress(Exception):
        flush_metrics(doc_id)

    logger.info(f"[Planner] Completed Phase-1 for {doc_id}: plan items = {len(plan.get('terms_to_define', [])) + len(plan.get('concepts_to_simplify', []))}")
    return done
