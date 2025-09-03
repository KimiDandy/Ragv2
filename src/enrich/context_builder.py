from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Tuple, Optional

MAX_CONTEXT_CHARS = 1000


@dataclass
class Segment:
    segment_id: str
    page: int
    header_path: List[str]
    char_start: int
    char_end: int
    text: str


def _load_segments(doc_dir: Path) -> List[Dict[str, Any]]:
    seg_path = Path(doc_dir) / "segments.json"
    if not seg_path.exists():
        return []
    try:
        return json.loads(seg_path.read_text(encoding="utf-8"))
    except Exception:
        return []


def _index_segments(segments: List[Dict[str, Any]]):
    by_id: Dict[str, Dict[str, Any]] = {}
    by_page: Dict[int, List[Dict[str, Any]]] = {}
    for s in segments:
        sid = s.get("segment_id") or s.get("id")
        if sid:
            by_id[str(sid)] = s
        pg = int(s.get("page") or 0)
        by_page.setdefault(pg, []).append(s)
    for pg in by_page:
        by_page[pg].sort(key=lambda x: int(x.get("char_start") or 0))
    return by_id, by_page


def _neighbors_in_page(by_page: Dict[int, List[Dict[str, Any]]], page: int, seg: Dict[str, Any], window: int = 1) -> List[Dict[str, Any]]:
    arr = by_page.get(page) or []
    try:
        idx = arr.index(seg)
    except ValueError:
        # fallback by span
        s0 = int(seg.get("char_start") or 0)
        idx = 0
        for i, s in enumerate(arr):
            if int(s.get("char_start") or 0) >= s0:
                idx = i
                break
    lo = max(0, idx - window)
    hi = min(len(arr), idx + window + 1)
    return arr[lo:hi]


def build_local_context(doc_dir: str | Path, seg_id: str, *, window: int = 1, max_chars: int = MAX_CONTEXT_CHARS) -> Dict[str, Any]:
    """
    Build a small local context snippet around a segment id, including header path and neighbor segments on the same page.

    Returns { "seg_id", "header", "page", "char", "text" }
    """
    d = Path(doc_dir)
    segments = _load_segments(d)
    by_id, by_page = _index_segments(segments)
    seg = by_id.get(str(seg_id))
    if not seg:
        return {"seg_id": seg_id, "header": [], "page": 0, "char": [0, 0], "text": ""}

    page = int(seg.get("page") or 0)
    header = list(seg.get("header_path") or [])
    char = [int(seg.get("char_start") or 0), int(seg.get("char_end") or 0)]

    neighbors = _neighbors_in_page(by_page, page, seg, window=max(0, int(window)))
    pieces: List[str] = []
    for s in neighbors:
        t = (s.get("text") or "").strip()
        if t:
            pieces.append(t)
        if sum(len(p) for p in pieces) >= max_chars:
            break

    body = ("\n\n".join(pieces))[:max_chars]
    header_str = " / ".join(h for h in header if h)
    context_text = (f"[{header_str}]\n" if header_str else "") + body

    return {
        "seg_id": str(seg_id),
        "header": header,
        "page": page,
        "char": char,
        "text": context_text.strip(),
    }
