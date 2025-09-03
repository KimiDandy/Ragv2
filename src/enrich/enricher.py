from __future__ import annotations

import json
import hashlib
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, Optional, Tuple, List, Callable

from loguru import logger

from ..core.local_cache import get as cache_get, set as cache_set, key_for as cache_key_for
from ..core.token_meter import TokenBudget
from ..core.rate_limiter import AsyncLeakyBucket
from ..core.config import OPENAI_API_KEY, CHAT_MODEL
from .context_builder import build_local_context
from ..planner.reduce import normalize_label

try:
    from openai import OpenAI
except Exception:  # pragma: no cover
    OpenAI = None  # type: ignore


@dataclass
class EnrichItem:
    doc_id: str
    type: str  # "term" | "concept"
    label: str
    provenance: Dict[str, Any]  # {seg_id, page, header_path, char}
    score: float = 0.0


SYSTEM_SKETCH = (
    "You are an enrichment assistant. Work ONLY with the provided CONTEXT.\n"
    "Return STRICT JSON with keys: label, type, mode, content, confidence, provenance.\n"
    "- mode must be 'sketch'\n- content ≤ 120 words, concise and accurate.\n"
    "- If insufficient context, use empty string for content and set confidence 0.0.\n"
)

USER_SKETCH_TMPL = (
    "TYPE: {itype}\nLABEL: {label}\n\nCONTEXT:\n{context}\n\n"
    "JSON ONLY OUTPUT:\n{\n  \"label\": \"{label}\",\n  \"type\": \"{itype}\",\n  \"mode\": \"sketch\",\n  \"content\": \"...\",\n  \"confidence\": 0.xx,\n  \"provenance\": {\"seg_id\": \"{seg_id}\", \"page\": {page}, \"char\": [{c0}, {c1}], \"header_path\": []}\n}\n"
)

SYSTEM_REFINE = (
    "Refine the SKETCH into a clearer, user-facing enrichment.\n"
    "Return STRICT JSON with keys: label, type, mode, content, confidence, provenance.\n"
    "- mode must be 'refine'\n- content ≤ 160 words, bullet points if helpful.\n"
)

USER_REFINE_TMPL = (
    "TYPE: {itype}\nLABEL: {label}\n\nCONTEXT:\n{context}\n\nSKETCH:\n{sketch}\n\n"
    "JSON ONLY OUTPUT:\n{\n  \"label\": \"{label}\",\n  \"type\": \"{itype}\",\n  \"mode\": \"refine\",\n  \"content\": \"...\",\n  \"confidence\": 0.xx,\n  \"provenance\": {\"seg_id\": \"{seg_id}\", \"page\": {page}, \"char\": [{c0}, {c1}], \"header_path\": []}\n}\n"
)


def _default_llm_call(prompt_messages: List[Dict[str, str]], *, timeout_s: int = 10, max_out: int = 220) -> str:
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


def _cache_key(doc_id: str, mode: str, prompt_version: str, item: EnrichItem, prov: Dict[str, Any]) -> str:
    norm = normalize_label(item.label)
    prov_sig = f"{prov.get('seg_id')}:{prov.get('char')}:{prov.get('page')}"
    raw = f"enrich::{mode}::{prompt_version}::{doc_id}::{item.type}::{norm}::{prov_sig}"
    return cache_key_for(raw)


def _safe_json(raw: str) -> Optional[Dict[str, Any]]:
    try:
        obj = json.loads(raw)
        if isinstance(obj, dict):
            return obj
    except Exception:
        pass
    # fallback: try to extract between first { and last }
    try:
        i = raw.find('{')
        j = raw.rfind('}')
        if 0 <= i < j:
            obj = json.loads(raw[i:j+1])
            if isinstance(obj, dict):
                return obj
    except Exception:
        return None
    return None


async def enrich_sketch(
    item: EnrichItem,
    doc_dir: Path,
    *,
    budget: TokenBudget,
    rps: AsyncLeakyBucket,
    prompt_version: str = "v1",
    llm_call: Optional[Callable[..., str]] = None,
    timeout_s: int = 10,
    max_out: int = 220,
) -> Optional[Dict[str, Any]]:
    prov = item.provenance or {}
    seg_id = prov.get("seg_id")
    ctx = build_local_context(doc_dir, str(seg_id)) if seg_id else {"text": ""}

    key = _cache_key(Path(doc_dir).name, "sketch", prompt_version, item, prov)
    cached = cache_get(key)
    if cached:
        return cached

    user = USER_SKETCH_TMPL.format(
        itype=item.type,
        label=item.label,
        context=ctx.get("text", ""),
        seg_id=str(seg_id or ""),
        page=int(prov.get("page") or ctx.get("page") or 0),
        c0=int((prov.get("char") or [0, 0])[0]),
        c1=int((prov.get("char") or [0, 0])[1]),
    )
    sys_p = SYSTEM_SKETCH
    prompt_for_est = sys_p + "\n\n" + user
    if not budget.can_afford(prompt_for_est, max_out):
        return None

    await rps.acquire()
    call = llm_call or _default_llm_call
    raw = await __to_thread(call, [
        {"role": "system", "content": sys_p},
        {"role": "user", "content": user},
    ], timeout_s=timeout_s, max_out=max_out)

    obj = _safe_json(raw or "")
    if not obj:
        logger.warning("Sketch JSON invalid; skipping")
        return None
    # minimal validation
    obj.setdefault("label", item.label)
    obj.setdefault("type", item.type)
    obj.setdefault("mode", "sketch")
    obj.setdefault("content", "")
    obj.setdefault("confidence", 0.0)
    obj.setdefault("provenance", prov)

    cache_set(key, obj)
    budget.charge(prompt_for_est, max_out)
    return obj


async def enrich_refine(
    item: EnrichItem,
    sketch: Dict[str, Any],
    doc_dir: Path,
    *,
    budget: TokenBudget,
    rps: AsyncLeakyBucket,
    prompt_version: str = "v1",
    llm_call: Optional[Callable[..., str]] = None,
    timeout_s: int = 12,
    max_out: int = 260,
) -> Optional[Dict[str, Any]]:
    prov = item.provenance or {}
    seg_id = prov.get("seg_id")
    ctx = build_local_context(doc_dir, str(seg_id)) if seg_id else {"text": ""}

    key = _cache_key(Path(doc_dir).name, "refine", prompt_version, item, prov)
    cached = cache_get(key)
    if cached:
        return cached

    user = USER_REFINE_TMPL.format(
        itype=item.type,
        label=item.label,
        context=ctx.get("text", ""),
        sketch=json.dumps(sketch, ensure_ascii=False),
        seg_id=str(seg_id or ""),
        page=int(prov.get("page") or ctx.get("page") or 0),
        c0=int((prov.get("char") or [0, 0])[0]),
        c1=int((prov.get("char") or [0, 0])[1]),
    )
    sys_p = SYSTEM_REFINE
    prompt_for_est = sys_p + "\n\n" + user
    if not budget.can_afford(prompt_for_est, max_out):
        return None

    await rps.acquire()
    call = llm_call or _default_llm_call
    raw = await __to_thread(call, [
        {"role": "system", "content": sys_p},
        {"role": "user", "content": user},
    ], timeout_s=timeout_s, max_out=max_out)

    obj = _safe_json(raw or "")
    if not obj:
        logger.warning("Refine JSON invalid; skipping")
        return None
    obj.setdefault("label", item.label)
    obj.setdefault("type", item.type)
    obj.setdefault("mode", "refine")
    obj.setdefault("content", "")
    obj.setdefault("confidence", 0.0)
    obj.setdefault("provenance", prov)

    cache_set(key, obj)
    budget.charge(prompt_for_est, max_out)
    return obj


async def __to_thread(func: Callable[..., str], *args, **kwargs) -> str:
    import asyncio
    return await asyncio.to_thread(func, *args, **kwargs)
