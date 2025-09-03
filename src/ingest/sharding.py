from __future__ import annotations

import hashlib
import json
import math
import re
from pathlib import Path
from typing import List, Dict, Any
from loguru import logger

from ..core.config import PIPELINE_ARTEFACTS_DIR

_WORD_RE = re.compile(r"\b[\w'-]+\b", flags=re.IGNORECASE)


def _tokenize(text: str) -> List[str]:
    return [t.lower() for t in _WORD_RE.findall(text or "")]


def _text_hash_vec(text: str, dim: int = 128) -> List[float]:
    """Cheap, deterministic text vector using token hashing. L2-normalized.
    Suitable for fast, dependency-free centroid computation in Sprint-1.
    """
    vec = [0.0] * dim
    for tok in _tokenize(text):
        h = int(hashlib.md5(tok.encode("utf-8")).hexdigest()[:8], 16)
        idx = h % dim
        vec[idx] += 1.0
    # L2 normalize
    norm = math.sqrt(sum(v * v for v in vec)) or 1.0
    return [v / norm for v in vec]


def compute_centroid(vectors: List[List[float]]) -> List[float]:
    if not vectors:
        return []
    dim = len(vectors[0]) if vectors[0] else 0
    if dim == 0:
        return []
    acc = [0.0] * dim
    for v in vectors:
        if not v:
            continue
        for i, x in enumerate(v):
            acc[i] += x
    # average then normalize
    n = max(1, len(vectors))
    avg = [a / n for a in acc]
    norm = math.sqrt(sum(v * v for v in avg)) or 1.0
    return [v / norm for v in avg]


def build_shards(doc_id: str, segments: List[Dict[str, Any]], dim: int = 128) -> Path:
    """Group segments by top-level header and write shards.json under artefacts/{doc_id}/.

    Shard schema:
      - shard_id: str
      - title: str  (top-level header or 'ROOT')
      - header_path_prefix: List[str]
      - size: int  (number of segments)
      - segment_ids: List[str]
      - centroid: List[float]  (hash-vector centroid)
    """
    # Group by top-level header
    groups: Dict[str, List[Dict[str, Any]]] = {}
    for seg in segments:
        header_path = list(seg.get("header_path") or [])
        key = header_path[0].strip() if header_path else "ROOT"
        groups.setdefault(key, []).append(seg)

    shards: List[Dict[str, Any]] = []
    for idx, (key, segs) in enumerate(groups.items(), start=1):
        vecs = [_text_hash_vec(s.get("text", ""), dim=dim) for s in segs]
        centroid = compute_centroid(vecs)
        shard = {
            "shard_id": f"shard_{idx}",
            "title": key,
            "header_path_prefix": [] if key == "ROOT" else [key],
            "size": len(segs),
            "segment_ids": [s.get("segment_id") or s.get("id") for s in segs],
            "centroid": centroid,
        }
        shards.append(shard)

    out_dir = Path(PIPELINE_ARTEFACTS_DIR) / doc_id
    out_dir.mkdir(parents=True, exist_ok=True)
    out_path = out_dir / "shards.json"
    out_path.write_text(json.dumps({"doc_id": doc_id, "shards": shards}, ensure_ascii=False, indent=2), encoding="utf-8")
    logger.info(f"Shards ditulis: {out_path} (count={len(shards)})")
    return out_path
