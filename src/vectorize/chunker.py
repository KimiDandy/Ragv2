from __future__ import annotations

import hashlib
import json
import math
import re
from dataclasses import dataclass, asdict
from pathlib import Path
from typing import Any, Dict, List, Tuple

from loguru import logger

from ..core.config import PIPELINE_ARTEFACTS_DIR

try:
    import tiktoken
    _enc = tiktoken.encoding_for_model("gpt-4.1")
except Exception:
    import tiktoken
    _enc = tiktoken.get_encoding("cl100k_base")

_SENT_RE = re.compile(r"(?s)(.+?)([.!?]+\s+|$)")
_WORD_RE = re.compile(r"\b[\w'-]+\b", flags=re.IGNORECASE)


@dataclass
class Chunk:
    text: str
    doc_id: str
    version: str
    chunk_id: str
    chunk_index: int
    header_path: List[str]
    section_title: str
    page_start: int
    page_end: int
    char: Tuple[int, int]
    shard_id: str
    shard_title: str
    hash: str

    def metadata(self) -> Dict[str, Any]:
        return {
            "doc_id": self.doc_id,
            "version": self.version,
            "chunk_id": self.chunk_id,
            "chunk_index": self.chunk_index,
            "header_path": self.header_path,
            "section_title": self.section_title,
            "page_start": self.page_start,
            "page_end": self.page_end,
            "char": list(self.char),
            "shard_id": self.shard_id,
            "shard_title": self.shard_title,
            "hash": self.hash,
        }


def _token_count(s: str) -> int:
    try:
        return len(_enc.encode(s or ""))
    except Exception:
        return max(1, len(s or "") // 4)


def _split_sentences(text: str) -> List[Tuple[int, int, str]]:
    # Return list of (start, end, sentence)
    out: List[Tuple[int, int, str]] = []
    for m in _SENT_RE.finditer(text or ""):
        s = m.group(1)
        if not s or not s.strip():
            continue
        start = m.start(1)
        end = m.end(1)
        out.append((start, end, s))
    return out


def _normalize(text: str) -> List[str]:
    return [t.lower() for t in _WORD_RE.findall(text or "")]


def _bow_score(q_terms: List[str], text: str) -> float:
    if not q_terms:
        return 0.0
    toks = _normalize(text)
    if not toks:
        return 0.0
    q = set(q_terms)
    t = set(toks)
    inter = len(q & t)
    union = len(q | t) or 1
    return inter / union


def _majority(items: List[str]) -> str:
    if not items:
        return ""
    counts: Dict[str, int] = {}
    for it in items:
        counts[it] = counts.get(it, 0) + 1
    return max(counts.items(), key=lambda kv: kv[1])[0]


def _assign_page_and_section(chunk_text: str, segments: List[Dict[str, Any]]) -> Tuple[int, int, List[str], str]:
    # Approximate mapping via BoW overlap against segment texts
    terms = _normalize(chunk_text)
    scored: List[Tuple[float, Dict[str, Any]]] = []
    for s in segments:
        sc = _bow_score(terms, s.get("text", ""))
        if sc > 0:
            scored.append((sc, s))
    scored.sort(key=lambda x: x[0], reverse=True)
    top = [s for _, s in scored[:3]]
    pages = [int(s.get("page") or 0) for s in top]
    header_paths = [" > ".join(list(s.get("header_path") or [])) for s in top]
    if not pages:
        return 0, 0, [], ""
    page_start = min(pages)
    page_end = max(pages)
    hp = _majority(header_paths)
    header_path_list = [p.strip() for p in (hp.split(" > ") if hp else []) if p.strip()]
    section_title = header_path_list[-1] if header_path_list else ""
    return page_start, page_end, header_path_list, section_title


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


def _assign_shard(chunk_text: str, shards_obj: Dict[str, Any], fallback_title: str = "") -> Tuple[str, str]:
    shards = shards_obj.get("shards", [])
    if not shards:
        return "", fallback_title
    qv = _text_hash_vec(chunk_text)
    best = max(shards, key=lambda sh: _cosine(qv, list(sh.get("centroid") or [])))
    return best.get("shard_id", ""), best.get("title", fallback_title)


def chunk_markdown(
    doc_id: str,
    version: str,
    md_text: str,
    segments: List[Dict[str, Any]],
    shards_obj: Dict[str, Any],
    target_tok: Tuple[int, int] = (400, 700),
    overlap: int = 80,
) -> List[Chunk]:
    """Sentence-first chunking with token-aware size and overlap.

    - Avoid splitting inside sentences.
    - Compute md_v2 char span for each chunk.
    - Approximate page range and header_path via segments BoW match.
    - Assign shard via centroid similarity.
    - Stable ID via sha1(doc_id|version|chunk_index|char_start|char_end).
    """
    min_tok, max_tok = target_tok
    sents = _split_sentences(md_text)
    chunks: List[Chunk] = []

    i = 0
    cur_start_char = None
    while i < len(sents):
        # accumulate sentences
        start_i = i
        start_char = sents[i][0]
        cur_text_parts = []
        tok_count = 0
        while i < len(sents) and tok_count < min_tok:
            _, end_char, sent = sents[i]
            cur_text_parts.append(sent)
            tok_count = _token_count(" ".join(cur_text_parts))
            i += 1
            if tok_count >= max_tok:
                break
        end_char = sents[i - 1][1] if i > start_i else sents[i][1]
        chunk_text = (" ".join(cur_text_parts)).strip()
        if not chunk_text:
            continue

        page_start, page_end, header_path, section_title = _assign_page_and_section(chunk_text, segments)
        shard_id, shard_title = _assign_shard(chunk_text, shards_obj, section_title)

        char_span = (int(start_char), int(end_char))
        chunk_index = len(chunks)
        base = f"{doc_id}|{version}|{chunk_index}|{char_span[0]}|{char_span[1]}"
        chunk_id = hashlib.sha1(base.encode("utf-8")).hexdigest()
        chash = hashlib.sha256(chunk_text.encode("utf-8")).hexdigest()

        chunks.append(
            Chunk(
                text=chunk_text,
                doc_id=doc_id,
                version=version,
                chunk_id=chunk_id,
                chunk_index=chunk_index,
                header_path=header_path,
                section_title=section_title,
                page_start=page_start,
                page_end=page_end,
                char=char_span,
                shard_id=shard_id,
                shard_title=shard_title,
                hash=chash,
            )
        )

        # overlap by tokens: approximate by sentence backtracking
        if overlap > 0 and i < len(sents):
            # move pointer back so next chunk overlaps ~overlap tokens
            back_tokens = 0
            j = i - 1
            while j > start_i and back_tokens < overlap:
                back_tokens += _token_count(sents[j][2])
                j -= 1
            i = max(j + 1, start_i + 1)

    logger.info(f"[Chunker] Built {len(chunks)} chunks for doc={doc_id} version={version}")
    return chunks
