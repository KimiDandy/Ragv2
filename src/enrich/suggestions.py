from __future__ import annotations

import json
import hashlib
from pathlib import Path
from typing import Any, Dict, List, Tuple

from .context_builder import build_local_context


def _suggestion_id(doc_id: str, itype: str, label: str) -> str:
    base = f"{doc_id}::{itype}::{label}".encode("utf-8")
    return hashlib.md5(base).hexdigest()[:16]


def build_suggestions(doc_dir: str | Path, generated_map: Dict[str, Dict[str, Any]]) -> List[Dict[str, Any]]:
    """
    Convert generated content map into UI suggestions list matching `SuggestionItem` schema in `src/api/models.py`.

    Each item: {id, type, original_context, generated_content, confidence_score, status}
    """
    d = Path(doc_dir)
    doc_id = d.name
    out: List[Dict[str, Any]] = []

    for key, obj in (generated_map or {}).items():
        itype = obj.get("type") or ("term" if key.startswith("term:") else "concept")
        label = obj.get("label") or key.split(":", 1)[-1]
        prov = obj.get("provenance") or {}
        seg_id = prov.get("seg_id")
        ctx = build_local_context(d, str(seg_id)) if seg_id else {"text": ""}
        sug = {
            "id": _suggestion_id(doc_id, itype, label),
            "type": itype,
            "original_context": (ctx.get("text") or "")[:400],
            "generated_content": obj.get("content") or "",
            "confidence_score": float(obj.get("confidence") or 0.0),
            "status": "pending",
        }
        out.append(sug)

    return out


def write_suggestions(doc_dir: str | Path, suggestions: List[Dict[str, Any]]) -> Path:
    d = Path(doc_dir)
    d.mkdir(parents=True, exist_ok=True)
    out = d / "suggestions.json"
    out.write_text(json.dumps(suggestions, ensure_ascii=False, indent=2), encoding="utf-8")
    return out


def write_suggestions_partial(doc_dir: str | Path, suggestions: List[Dict[str, Any]]) -> Path:
    d = Path(doc_dir)
    d.mkdir(parents=True, exist_ok=True)
    out = d / "suggestions_partial.json"
    out.write_text(json.dumps(suggestions, ensure_ascii=False, indent=2), encoding="utf-8")
    return out


def paginate(items: List[Dict[str, Any]], page: int, page_size: int) -> Tuple[List[Dict[str, Any]], int]:
    total = len(items)
    page = max(1, int(page or 1))
    page_size = max(1, int(page_size or 20))
    start = (page - 1) * page_size
    end = min(start + page_size, total)
    return items[start:end], total
