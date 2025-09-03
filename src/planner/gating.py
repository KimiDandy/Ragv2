import math
from typing import List, Dict, Any, Tuple, DefaultDict
from collections import defaultdict

HEADER_BONUS_TERMS = {
    "definisi", "ketentuan", "ketentuan umum", "risiko", "syarat", "syarat & ketentuan",
    "prosedur", "ringkasan", "glossary", "glosarium"
}


def _header_weight(header_path: List[str] | None) -> float:
    if not header_path:
        return 0.0
    hp = [str(h or "").strip().lower() for h in header_path]
    top = hp[0] if hp else ""
    score = 0.0
    for h in hp:
        for kw in HEADER_BONUS_TERMS:
            if kw in h:
                score = max(score, 1.0)
    # slight bonus if appears near top-level
    if any(kw in (top or "") for kw in HEADER_BONUS_TERMS):
        score = max(score, 1.0)
    return score


def pre_score(seg: Dict[str, Any]) -> float:
    flags = seg.get("flags") or {}
    ce_top = seg.get("contains_entities")
    id_top = seg.get("is_difficult")
    contains_entities = 1.0 if (flags.get("contains_entities") if isinstance(flags, dict) else None) or ce_top else 0.0
    is_difficult = 1.0 if (flags.get("is_difficult") if isinstance(flags, dict) else None) or id_top else 0.0
    numeric_ratio = float(seg.get("numeric_ratio") or 0.0)
    tfidf_glossary_score = float(seg.get("tfidf_glossary_score") or 0.0)
    header_path = seg.get("header_path") or []
    h_weight = _header_weight(header_path)
    score = (
        1.2 * contains_entities
        + 0.8 * is_difficult
        + 0.6 * h_weight
        + 0.4 * numeric_ratio
        + 0.4 * tfidf_glossary_score
    )
    return float(score)


def _map_segment_to_shard(shards_obj: Dict[str, Any]) -> Dict[str, str]:
    seg2shard: Dict[str, str] = {}
    shards = shards_obj.get("shards") or []
    for sh in shards:
        shard_id = sh.get("id") or sh.get("shard_id") or ""
        for sid in (sh.get("segment_ids") or []):
            if sid and shard_id:
                seg2shard[str(sid)] = str(shard_id)
    return seg2shard


def _group_by_shard(segments: List[Dict[str, Any]], shards_obj: Dict[str, Any]) -> Tuple[DefaultDict[str, List[Dict[str, Any]]], Dict[str, str]]:
    seg2shard = _map_segment_to_shard(shards_obj)
    grouped: DefaultDict[str, List[Dict[str, Any]]] = defaultdict(list)
    for s in segments:
        sid = s.get("segment_id") or s.get("id")
        shard_id = seg2shard.get(str(sid))
        if not shard_id:
            # fallback to top-level header
            hp = s.get("header_path") or []
            shard_id = (hp[0].lower() if hp else "ROOT")
        s_copy = dict(s)
        s_copy["_shard_id"] = shard_id
        grouped[shard_id].append(s_copy)
    return grouped, seg2shard


def _is_definition_shard(shard_id: str, shards_obj: Dict[str, Any]) -> bool:
    sid = (shard_id or "").lower()
    if any(k in sid for k in ("definisi", "ketentuan")):
        return True
    # also check shard titles
    for sh in (shards_obj.get("shards") or []):
        if str(sh.get("id") or sh.get("shard_id") or "").lower() == sid:
            title = str(sh.get("title") or "").lower()
            if any(k in title for k in ("definisi", "ketentuan")):
                return True
    return False


def _compute_k_global(n_segments: int) -> int:
    return int(min(2000, math.ceil(0.015 * max(n_segments, 0))))


def select_candidates(
    segments: List[Dict[str, Any]],
    shards_obj: Dict[str, Any],
    k_global: int | None = None,
    quota_per_shard: int = 8,
) -> List[Dict[str, Any]]:
    n = len(segments)
    k = int(k_global) if k_global is not None else _compute_k_global(n)
    grouped, _ = _group_by_shard(segments, shards_obj)

    # per-shard pick
    selected: List[Dict[str, Any]] = []
    for shard_id, segs in grouped.items():
        segs_sorted = sorted(segs, key=pre_score, reverse=True)
        cap = 10 if _is_definition_shard(shard_id, shards_obj) else int(quota_per_shard or 8)
        selected.extend(segs_sorted[:cap])

    # fill remainder by global score
    if len(selected) < k:
        already = { (s.get("segment_id") or s.get("id")) for s in selected }
        remain = [s for s in segments if (s.get("segment_id") or s.get("id")) not in already]
        remain_sorted = sorted(remain, key=pre_score, reverse=True)
        selected.extend(remain_sorted[: (k - len(selected))])

    # attach shard_id if missing
    by_shard, _ = _group_by_shard(selected, shards_obj)
    out = []
    for shard_id, segs in by_shard.items():
        for s in segs:
            d = dict(s)
            d["shard_id"] = s.get("_shard_id") or shard_id
            d.pop("_shard_id", None)
            out.append(d)

    # final top-k by score ordering
    out_sorted = sorted(out, key=pre_score, reverse=True)[:k]
    return out_sorted
