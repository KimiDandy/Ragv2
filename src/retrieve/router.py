from __future__ import annotations

import hashlib
import json
import math
import re
from pathlib import Path
from typing import Any, Dict, List, Tuple

from loguru import logger
from langchain_chroma import Chroma

from ..core.config import PIPELINE_ARTEFACTS_DIR, CHROMA_COLLECTION, EMBEDDING_MODEL
from ..ingest.index_bm25 import bm25_search

_WORD_RE = re.compile(r"\b[\w'-]+\b", flags=re.IGNORECASE)


def _normalize(text: str) -> List[str]:
    return [t.lower() for t in _WORD_RE.findall(text or "")]


def _text_hash_vec(text: str, dim: int = 128) -> List[float]:
    vec = [0.0] * dim
    for tok in _normalize(text):
        h = int(hashlib.md5(tok.encode("utf-8")).hexdigest()[:8], 16)
        idx = h % dim
        vec[idx] += 1.0
    norm = math.sqrt(sum(v * v for v in vec)) or 1.0
    return [v / norm for v in vec]


def _cosine(a: List[float], b: List[float]) -> float:
    if not a or not b or len(a) != len(b):
        return 0.0
    return sum(x * y for x, y in zip(a, b))


def _build_snippet(text: str, max_len: int = 280) -> str:
    t = (text or "").strip()
    if len(t) <= max_len:
        return t
    cut = t[: max_len].rsplit(" ", 1)[0]
    return (cut or t[: max_len]).strip() + "…"


def _collection_name() -> str:
    return f"{CHROMA_COLLECTION}__{EMBEDDING_MODEL.replace(':','_').replace('-','_')}"


def _load_json(path: Path) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception as e:
        logger.warning(f"[Router] Failed to read {path.name}: {e}")
        return None


def search_evidence(
    *,
    chroma_client,
    embeddings,
    document_id: str,
    query: str,
    version: str = "v2",
    bm25_top_k: int = 50,
    max_shards: int = 4,
    dense_k_per_shard: int = 5,
    page: int = 1,
    page_size: int = 10,
) -> Dict[str, Any]:
    """Run multi-stage retrieval cascade and return paginated evidence entries.

    Output schema:
      {
        "document_id": str,
        "query": str,
        "version": str,
        "total": int,
        "page": int,
        "page_size": int,
        "items": [ { id, score, snippet, metadata } ]
      }
    """
    assert version in ("v1", "v2"), "version must be 'v1' or 'v2'"

    doc_dir = Path(PIPELINE_ARTEFACTS_DIR) / document_id
    seg_path = doc_dir / "segments.json"
    shards_path = doc_dir / "shards.json"

    segments = _load_json(seg_path) or []
    shards_obj = _load_json(shards_path) or {"shards": []}

    # Build segment_id -> shard_id map
    seg_to_shard: Dict[str, str] = {}
    shard_meta: Dict[str, Dict[str, Any]] = {}
    for sh in shards_obj.get("shards", []) or []:
        sid = sh.get("shard_id")
        shard_meta[sid] = sh
        for seg_id in sh.get("segment_ids", []) or []:
            if seg_id:
                seg_to_shard[seg_id] = sid

    # Stage 1: BM25 prefilter
    ranked = bm25_search(document_id, query, top_k=int(bm25_top_k or 50))
    shard_counts: Dict[str, int] = {}
    for seg_id, score, _ in ranked:
        shid = seg_to_shard.get(seg_id)
        if shid:
            shard_counts[shid] = shard_counts.get(shid, 0) + 1

    # Stage 2: Shard selection (counts + centroid similarity)
    qv = _text_hash_vec(query)
    scored_shards: List[Tuple[str, float]] = []
    for sh in shards_obj.get("shards", []) or []:
        shid = sh.get("shard_id")
        cnt = float(shard_counts.get(shid, 0))
        centroid = list(sh.get("centroid") or [])
        sim = _cosine(qv, centroid)
        score = 0.7 * (cnt) + 0.3 * sim  # simple linear combo
        scored_shards.append((shid, score))

    scored_shards.sort(key=lambda x: x[1], reverse=True)
    selected = [sid for sid, _ in scored_shards[: int(max_shards or 4)] if sid]
    logger.info(f"[Router] Selected shards: {selected} for doc={document_id}")

    # Stage 3: Dense retrieval within selected shards
    vec_store = Chroma(client=chroma_client, collection_name=_collection_name(), embedding_function=embeddings)

    docs_with_scores: List[Tuple[Any, float]] = []
    if selected:
        # Chroma supports $in, but do per-shard queries for compatibility safety
        for shid in selected:
            try:
                pairs = vec_store.similarity_search_with_relevance_scores(
                    query,
                    k=int(dense_k_per_shard or 5),
                    filter={
                        '$and': [
                            {'source_document': {'$eq': document_id}},
                            {'version': {'$eq': version}},
                            {'shard_id': {'$eq': shid}},
                        ]
                    },
                )
                docs_with_scores.extend(list(pairs))
            except Exception as e:
                logger.warning(f"[Router] Dense retrieval failed for shard {shid}: {e}")
    else:
        try:
            pairs = vec_store.similarity_search_with_relevance_scores(
                query, k=int(dense_k_per_shard * 2 or 10), filter={'$and': [
                    {'source_document': {'$eq': document_id}},
                    {'version': {'$eq': version}},
                ]}
            )
            docs_with_scores.extend(list(pairs))
        except Exception as e:
            logger.warning(f"[Router] Dense retrieval (no shard) failed: {e}")

    # Stage 4: Lightweight reranking (combine vector score and token overlap)
    q_terms = set(_normalize(query))
    def tok_overlap(text: str) -> float:
        if not q_terms:
            return 0.0
        t = set(_normalize(text))
        u = len(q_terms | t) or 1
        return len(q_terms & t) / u

    rescored: List[Tuple[Any, float]] = []
    for doc, vec_score in docs_with_scores:
        text = getattr(doc, 'page_content', '') or ''
        ov = tok_overlap(text)
        score = 0.8 * float(vec_score) + 0.2 * ov
        rescored.append((doc, score))

    rescored.sort(key=lambda x: x[1], reverse=True)

    # Paging
    total = len(rescored)
    page = max(int(page or 1), 1)
    page_size = max(int(page_size or 10), 1)
    start = (page - 1) * page_size
    end = min(start + page_size, total)
    page_items = rescored[start:end]

    items: List[Dict[str, Any]] = []
    for doc, sc in page_items:
        content = getattr(doc, 'page_content', '') or ''
        metadata = getattr(doc, 'metadata', {}) or {}
        sid = hashlib.md5((content + json.dumps(metadata, sort_keys=True)).encode('utf-8')).hexdigest()[:12]
        items.append({
            'id': sid,
            'score': float(round(sc, 4)),
            'snippet': _build_snippet(content, 300),
            'metadata': metadata,
        })

    return {
        'document_id': document_id,
        'query': query,
        'version': version,
        'total': total,
        'page': page,
        'page_size': page_size,
        'items': items,
    }
