from __future__ import annotations

import json
import math
import re
from collections import Counter, defaultdict
from dataclasses import dataclass
from pathlib import Path
from typing import List, Dict, Any, Tuple
from loguru import logger

from ..core.config import PIPELINE_ARTEFACTS_DIR

_WORD_RE = re.compile(r"\b[\w'-]+\b", flags=re.IGNORECASE)

STOPWORDS = set(
    s.lower()
    for s in (
        # EN minimal
        "the", "a", "an", "and", "or", "in", "on", "of", "for", "to", "with", "by", "at",
        "from", "as", "is", "are", "was", "were", "be", "been", "it", "that", "this", "these",
        # ID minimal
        "dan", "atau", "yang", "di", "ke", "dari", "untuk", "pada", "adalah", "ialah", "itu", "ini",
        "dengan", "sebagai", "dalam", "karena", "oleh",
    )
)


def _normalize(text: str) -> List[str]:
    tokens = [t.lower() for t in _WORD_RE.findall(text or "")]
    return [t for t in tokens if t not in STOPWORDS and not t.isdigit()]


@dataclass
class BM25Index:
    doc_ids: List[str]
    avgdl: float
    k1: float
    b: float
    df: Dict[str, int]  # document frequency per term
    idf: Dict[str, float]
    doc_len: List[int]
    term_tf: Dict[str, Dict[int, int]]  # term -> {doc_index: tf}

    def to_json(self) -> Dict[str, Any]:
        return {
            "doc_ids": self.doc_ids,
            "avgdl": self.avgdl,
            "k1": self.k1,
            "b": self.b,
            "df": self.df,
            "idf": self.idf,
            "doc_len": self.doc_len,
            "term_tf": {t: {str(i): tf for i, tf in postings.items()} for t, postings in self.term_tf.items()},
        }

    @staticmethod
    def from_json(obj: Dict[str, Any]) -> "BM25Index":
        term_tf = {t: {int(i): tf for i, tf in postings.items()} for t, postings in obj.get("term_tf", {}).items()}
        return BM25Index(
            doc_ids=list(obj.get("doc_ids", [])),
            avgdl=float(obj.get("avgdl", 0.0)),
            k1=float(obj.get("k1", 1.5)),
            b=float(obj.get("b", 0.75)),
            df={t: int(v) for t, v in (obj.get("df", {}) or {}).items()},
            idf={t: float(v) for t, v in (obj.get("idf", {}) or {}).items()},
            doc_len=[int(x) for x in (obj.get("doc_len", []) or [])],
            term_tf=term_tf,
        )


def build_bm25_index(doc_id: str, segments: List[Dict[str, Any]], k1: float = 1.5, b: float = 0.75) -> Path:
    """Build a lightweight BM25 index and persist under artefacts/{doc_id}/bm25_index.json"""
    texts = [seg.get("text", "") for seg in segments]
    tokens_list = [_normalize(t) for t in texts]
    doc_ids = [seg.get("segment_id") or seg.get("id") for seg in segments]

    N = len(tokens_list)
    doc_len = [len(toks) for toks in tokens_list]
    avgdl = sum(doc_len) / max(N, 1)

    df: Dict[str, int] = defaultdict(int)
    term_tf: Dict[str, Dict[int, int]] = defaultdict(dict)

    for i, toks in enumerate(tokens_list):
        counts = Counter(toks)
        for term, tf in counts.items():
            term_tf[term][i] = tf
        for term in counts.keys():
            df[term] += 1

    idf: Dict[str, float] = {}
    for term, dfi in df.items():
        idf[term] = math.log((N - dfi + 0.5) / (dfi + 0.5) + 1.0)

    idx = BM25Index(
        doc_ids=doc_ids,
        avgdl=avgdl,
        k1=k1,
        b=b,
        df=dict(df),
        idf=idf,
        doc_len=doc_len,
        term_tf=term_tf,
    )

    out_dir = Path(PIPELINE_ARTEFACTS_DIR) / doc_id
    out_dir.mkdir(parents=True, exist_ok=True)
    out_path = out_dir / "bm25_index.json"
    out_path.write_text(json.dumps(idx.to_json()), encoding="utf-8")

    logger.info(f"BM25 index ditulis: {out_path} (docs={N}, vocab={len(df)})")
    return out_path


def bm25_search(doc_id: str, query: str, top_k: int = 10) -> List[Tuple[str, float, int]]:
    """Return list of (segment_id, score, doc_index) sorted by score desc."""
    out_dir = Path(PIPELINE_ARTEFACTS_DIR) / doc_id
    idx_path = out_dir / "bm25_index.json"
    if not idx_path.exists():
        logger.error(f"BM25 index belum ada untuk doc_id={doc_id}")
        return []
    idx = BM25Index.from_json(json.loads(idx_path.read_text(encoding="utf-8")))

    q_terms = _normalize(query)
    if not q_terms:
        return []

    scores = defaultdict(float)
    for term in q_terms:
        postings = idx.term_tf.get(term)
        if not postings:
            continue
        idf = idx.idf.get(term, 0.0)
        for di, tf in postings.items():
            dl = idx.doc_len[di]
            denom = tf + idx.k1 * (1 - idx.b + idx.b * dl / max(idx.avgdl, 1e-6))
            s = idf * (tf * (idx.k1 + 1)) / max(denom, 1e-6)
            scores[di] += s

    ranked = sorted(scores.items(), key=lambda x: x[1], reverse=True)[: max(1, int(top_k))]
    return [(idx.doc_ids[i], float(s), int(i)) for i, s in ranked]
