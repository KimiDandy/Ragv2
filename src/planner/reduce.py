from __future__ import annotations

import json
import math
import re
import hashlib
from collections import defaultdict
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Tuple

import numpy as np
from loguru import logger

HEADER_BONUS_TERMS = {
    "definisi", "ketentuan", "ketentuan umum", "risiko", "syarat", "syarat & ketentuan",
    "prosedur", "ringkasan", "glossary", "glosarium"
}


def normalize_label(label: str) -> str:
    if not label:
        return ""
    s = label.strip().lower()
    # Python re lacks \p classes unless 'regex' package; fallback: remove common punct
    s = re.sub(r"[\W_]+", " ", s, flags=re.UNICODE)
    s = re.sub(r"\s+", " ", s).strip()
    return s


def _header_weight(header_path: List[str] | None) -> float:
    if not header_path:
        return 0.0
    hp = [str(h or "").strip().lower() for h in header_path]
    score = 0.0
    for h in hp:
        for kw in HEADER_BONUS_TERMS:
            if kw in h:
                score = max(score, 1.0)
    return score


def _embed_label(label: str, dim: int = 128) -> np.ndarray:
    # simple hashing vectorizer
    vec = np.zeros(dim, dtype=np.float32)
    for tok in (label.split() if label else []):
        h = int(hashlib.md5(tok.encode("utf-8")).hexdigest(), 16)
        idx = h % dim
        vec[idx] += 1.0
    n = np.linalg.norm(vec) or 1.0
    return vec / n


def _cosine(a: np.ndarray, b: np.ndarray) -> float:
    na = np.linalg.norm(a)
    nb = np.linalg.norm(b)
    if na == 0 or nb == 0:
        return 0.0
    return float(np.dot(a, b) / (na * nb))


@dataclass
class LabelOccurrence:
    norm_key: str
    display: str
    confidence: float
    seg_id: str
    page: int
    header_path: List[str]
    char_span: Tuple[int, int]
    numeric_ratio: float
    shard_id: str | None = None


@dataclass
class Cluster:
    centroid: np.ndarray
    key: str
    items: List[LabelOccurrence] = field(default_factory=list)


def _collect_occurrences(
    skim_results: List[Dict[str, Any]],
    seg_by_hash: Dict[str, Dict[str, Any]],
    label_keys: Tuple[str, str] = ("terms_to_define", "concepts_to_simplify"),
) -> Tuple[List[LabelOccurrence], List[LabelOccurrence]]:
    terms: List[LabelOccurrence] = []
    concepts: List[LabelOccurrence] = []
    for obj in skim_results:
        seg_h = str(obj.get("segment_hash") or "")
        seg = seg_by_hash.get(seg_h)
        if not seg:
            continue
        seg_id = seg.get("segment_id") or seg.get("id") or ""
        page = int(seg.get("page") or 0)
        header_path = list(seg.get("header_path") or [])
        char_span = (
            int(seg.get("char_start") or 0),
            int(seg.get("char_end") or 0),
        )
        numeric_ratio = float(seg.get("numeric_ratio") or 0.0)
        shard_id = seg.get("shard_id") or None

        for item in (obj.get(label_keys[0]) or []):
            label = str(item.get("label") or "").strip()
            if not label:
                continue
            norm = normalize_label(label)
            if not norm:
                continue
            conf = float(item.get("confidence") or 0.0)
            terms.append(
                LabelOccurrence(norm, label, conf, seg_id, page, header_path, char_span, numeric_ratio, shard_id)
            )
        for item in (obj.get(label_keys[1]) or []):
            label = str(item.get("label") or "").strip()
            if not label:
                continue
            norm = normalize_label(label)
            if not norm:
                continue
            conf = float(item.get("confidence") or 0.0)
            concepts.append(
                LabelOccurrence(norm, label, conf, seg_id, page, header_path, char_span, numeric_ratio, shard_id)
            )
    return terms, concepts


def _cluster_occurrences(occ: List[LabelOccurrence], threshold: float = 0.82, dim: int = 128) -> List[Cluster]:
    clusters: List[Cluster] = []
    centroids: List[np.ndarray] = []
    for o in occ:
        vec = _embed_label(o.norm_key, dim)
        best_idx = -1
        best_sim = -1.0
        for i, cvec in enumerate(centroids):
            sim = _cosine(vec, cvec)
            if sim > best_sim:
                best_sim = sim
                best_idx = i
        if best_idx >= 0 and best_sim >= threshold:
            clusters[best_idx].items.append(o)
            # update centroid
            new_vec = (centroids[best_idx] * len(clusters[best_idx].items) + vec) / (len(clusters[best_idx].items) + 1)
            centroids[best_idx] = new_vec / (np.linalg.norm(new_vec) or 1.0)
        else:
            clusters.append(Cluster(centroid=vec, key=o.norm_key, items=[o]))
            centroids.append(vec)
    return clusters


def _score_cluster(c: Cluster) -> float:
    frequency = float(len(c.items))
    # centrality via header bonus average
    centrality = float(np.mean([_header_weight(x.header_path) for x in c.items]) if c.items else 0.0)
    numeric_bias = float(np.mean([x.numeric_ratio for x in c.items]) if c.items else 0.0)
    score = frequency + 0.7 * centrality + 0.3 * numeric_bias
    return float(score)


def _to_plan_entries(clusters: List[Cluster], top_n: int) -> List[Dict[str, Any]]:
    scored = [(c, _score_cluster(c)) for c in clusters]
    scored.sort(key=lambda t: t[1], reverse=True)
    out: List[Dict[str, Any]] = []
    for c, sc in scored[:top_n]:
        # choose display label from highest confidence occurrence
        best = max(c.items, key=lambda x: (x.confidence, len(x.display)), default=None)
        label_disp = best.display if best and best.display else c.key
        provenances = [
            {
                "seg_id": it.seg_id,
                "page": it.page,
                "header_path": it.header_path,
                "char": [it.char_span[0], it.char_span[1]],
            }
            for it in c.items
        ]
        out.append({
            "label": label_disp,
            "score": float(round(sc, 4)),
            "provenances": provenances,
        })
    return out


def build_plan(
    skim_results: List[Dict[str, Any]],
    segments: List[Dict[str, Any]],
    *,
    cluster_threshold: float = 0.82,
    top_total_min: int = 200,
    top_total_max: int = 300,
) -> Dict[str, Any]:
    # map segment hash to metadata
    seg_by_hash: Dict[str, Dict[str, Any]] = {}
    for s in segments:
        h = s.get("hash")
        if not h:
            txt = (s.get("text") or "").strip()
            h = hashlib.sha256(txt.encode("utf-8")).hexdigest()
        seg_by_hash[str(h)] = s

    terms_occ, concepts_occ = _collect_occurrences(skim_results, seg_by_hash)

    # cluster per type
    terms_clusters = _cluster_occurrences(terms_occ, threshold=cluster_threshold)
    concepts_clusters = _cluster_occurrences(concepts_occ, threshold=cluster_threshold)

    # split budget roughly equally but flexible
    half_min = max(1, top_total_min // 2)
    half_max = max(1, top_total_max // 2)

    terms_entries = _to_plan_entries(terms_clusters, half_max)
    concepts_entries = _to_plan_entries(concepts_clusters, half_max)

    # trim to satisfy min+max totals loosely
    # ensure not exceeding max
    while len(terms_entries) + len(concepts_entries) > top_total_max:
        if len(terms_entries) >= len(concepts_entries) and len(terms_entries) > half_min:
            terms_entries.pop()
        elif len(concepts_entries) > half_min:
            concepts_entries.pop()
        else:
            break

    # ensure at least min if possible
    total = len(terms_entries) + len(concepts_entries)
    if total < top_total_min:
        # already sorted; try extend from remaining clusters
        pass

    plan = {
        "terms_to_define": terms_entries,
        "concepts_to_simplify": concepts_entries,
    }
    return plan


def write_plan_json(plan: Dict[str, Any], artefacts_dir: Path) -> Path:
    artefacts_dir.mkdir(parents=True, exist_ok=True)
    out = artefacts_dir / "plan.json"
    out.write_text(json.dumps(plan, ensure_ascii=False, indent=2), encoding="utf-8")
    return out
