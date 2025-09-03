import asyncio
import json
import hashlib
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Tuple, Callable

from loguru import logger

from ..core.local_cache import get as cache_get, set as cache_set, key_for as cache_key_for
from ..core.token_meter import TokenBudget
from ..core.rate_limiter import AsyncLeakyBucket
from ..core.config import OPENAI_API_KEY, CHAT_MODEL
from ..obs.metrics import emit, is_cancelled, log_error

try:
    from openai import OpenAI
except Exception:  # pragma: no cover
    OpenAI = None  # type: ignore


SYSTEM_PROMPT = (
    "You are a careful planner. Work ONLY with the provided SEGMENT.\n"
    "Return STRICT JSON. No markdown. If nothing useful, return empty arrays."
)

USER_PROMPT_TEMPLATE = (
    "SEGMENT:\n{segment_text}\n\n"
    "OUTPUT:\n{\n"
    "  \"segment_hash\": \"{segment_hash}\",\n"
    "  \"terms_to_define\": [{\"label\": \"...\", \"confidence\": 0.xx}],\n"
    "  \"concepts_to_simplify\": [{\"label\": \"...\", \"confidence\": 0.xx}]\n"
    "}\n"
    "RULES:\n"
    "- ≤ 2 items per array\n"
    "- labels ≤ 6 words\n"
    "- confidence ∈ [0,1]\n"
    "- If none, both arrays empty.\n"
)


def _segment_hash(seg: Dict[str, Any]) -> str:
    h = seg.get("hash")
    if h:
        return str(h)
    text = (seg.get("text") or "").strip()
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _default_llm_call(prompt_messages: List[Dict[str, str]], *, timeout_s: int = 8, max_out: int = 40) -> str:
    if OpenAI is None:
        raise RuntimeError("OpenAI client not available")
    client = OpenAI(api_key=OPENAI_API_KEY or None)
    resp = client.chat.completions.create(
        model=CHAT_MODEL,
        messages=prompt_messages,
        temperature=0,
        max_tokens=max_out,
        timeout=timeout_s,
    )
    return (resp.choices[0].message.content or "").strip()


def skim_segment(seg: Dict[str, Any], prompt_version: str = "v1", llm_call: Optional[Callable[..., str]] = None, timeout_s: int = 8, max_out: int = 40) -> Optional[Dict[str, Any]]:
    """Run the skim prompt for a single segment. Returns parsed JSON or None if invalid."""
    seg_h = _segment_hash(seg)
    text = (seg.get("text") or "").strip()
    if not text:
        return None

    key = cache_key_for(f"skim::{prompt_version}::{seg_h}")
    cached = cache_get(key)
    if cached:
        return cached

    messages = [
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "user", "content": USER_PROMPT_TEMPLATE.format(segment_text=text, segment_hash=seg_h)},
    ]

    call = llm_call or _default_llm_call
    raw = call(messages, timeout_s=timeout_s, max_out=max_out)

    try:
        obj = json.loads(raw)
        if not isinstance(obj, dict):
            raise ValueError("not a dict")
        # validate keys
        if "segment_hash" not in obj:
            obj["segment_hash"] = seg_h
        for k in ("terms_to_define", "concepts_to_simplify"):
            v = obj.get(k)
            if v is None or not isinstance(v, list):
                obj[k] = []
        # post-filter to ≤ 2
        obj["terms_to_define"] = obj["terms_to_define"][:2]
        obj["concepts_to_simplify"] = obj["concepts_to_simplify"][:2]
        cache_set(key, obj)
        return obj
    except Exception:
        logger.warning("Invalid JSON from skim; skipping one segment")
        return None


async def run_skim_async(
    candidates: List[Dict[str, Any]],
    artefacts_dir: Path,
    *,
    budget: TokenBudget,
    rps: AsyncLeakyBucket,
    prompt_version: str = "v1",
    llm_call: Optional[Callable[..., str]] = None,
    timeout_s: int = 8,
    retry: int = 1,
    max_out: int = 40,
    concurrency: int = 6,
) -> Tuple[List[Dict[str, Any]], Dict[str, int]]:
    """
    Run skim concurrently with budget & RPS guards. Writes each valid result to skim_results.jsonl.
    Returns (results, metrics) where metrics includes token_used (estimate), cache_hits, timeouts.
    """
    artefacts_dir.mkdir(parents=True, exist_ok=True)
    doc_id = artefacts_dir.name
    out_path = artefacts_dir / "skim_results.jsonl"
    file_lock = asyncio.Lock()

    cache_hits = 0
    timeouts = 0
    results: List[Dict[str, Any]] = []

    sem = asyncio.Semaphore(max(1, int(concurrency)))

    async def worker(seg: Dict[str, Any]):
        nonlocal cache_hits, timeouts
        # cooperative cancel
        if is_cancelled(doc_id):
            return
        seg_h = _segment_hash(seg)
        key = cache_key_for(f"skim::{prompt_version}::{seg_h}")
        cached = cache_get(key)
        if cached:
            cache_hits += 1
            res = cached
        else:
            # budget guard
            user_prompt = USER_PROMPT_TEMPLATE.format(segment_text=(seg.get("text") or "").strip(), segment_hash=seg_h)
            sys_prompt = SYSTEM_PROMPT
            prompt_text_for_est = sys_prompt + "\n\n" + user_prompt
            if not budget.can_afford(prompt_text_for_est, max_out):
                return  # stop spawning more when budget tight (outer loop ensures early exit)
            # rate limit
            await rps.acquire()
            # run with timeout + retry
            last_exc: Optional[Exception] = None
            for attempt in range(max(1, int(retry)) + 1):
                try:
                    def_call = llm_call or _default_llm_call
                    raw = await asyncio.to_thread(
                        def_call,
                        [
                            {"role": "system", "content": sys_prompt},
                            {"role": "user", "content": user_prompt},
                        ],
                        timeout_s=timeout_s,
                        max_out=max_out,
                    )
                    obj = json.loads(raw)
                    if not isinstance(obj, dict):
                        raise ValueError("not a dict")
                    if "segment_hash" not in obj:
                        obj["segment_hash"] = seg_h
                    for k in ("terms_to_define", "concepts_to_simplify"):
                        v = obj.get(k)
                        if v is None or not isinstance(v, list):
                            obj[k] = []
                    obj["terms_to_define"] = obj["terms_to_define"][:2]
                    obj["concepts_to_simplify"] = obj["concepts_to_simplify"][:2]
                    cache_set(key, obj)
                    budget.charge(prompt_text_for_est, max_out)
                    res = obj
                    break
                except Exception as e:
                    last_exc = e
                    if "timeout" in (str(e).lower()):
                        timeouts += 1
                    continue
            else:
                logger.warning(f"Skim failed for segment {seg_h}: {last_exc}")
                try:
                    log_error(doc_id, "phase-1", f"skim_failed: {last_exc}", meta={"segment_hash": seg_h})
                except Exception:
                    pass
                return

        # write JSONL
        async with file_lock:
            with out_path.open("a", encoding="utf-8") as f:
                f.write(json.dumps(res, ensure_ascii=False) + "\n")
        results.append(res)

    async def producer_loop():
        tasks = []
        for seg in candidates:
            # stop-when-budget < 10%
            if budget.used >= int(budget.total * 0.9):
                break
            if is_cancelled(doc_id):
                break
            await sem.acquire()
            task = asyncio.create_task(worker(seg))
            task.add_done_callback(lambda t: sem.release())
            tasks.append(task)
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)

    # emit start
    try:
        emit(doc_id, "phase-1", "start", values={"total": int(len(candidates))})
    except Exception:
        pass

    await producer_loop()

    metrics = {
        "token_used": int(budget.used),
        "cache_hits": int(cache_hits),
        "timeouts": int(timeouts),
        "processed": int(len(results)),
        "total_candidates": int(len(candidates)),
    }
    # emit budget stop or end
    try:
        if budget.used >= int(budget.total * 0.9):
            emit(doc_id, "phase-1", "budget_stop", values={"processed": metrics["processed"], "total": metrics["total_candidates"]})
        if is_cancelled(doc_id):
            emit(doc_id, "phase-1", "cancelled", values=metrics)
        else:
            emit(doc_id, "phase-1", "end", values=metrics)
    except Exception:
        pass
    return results, metrics
