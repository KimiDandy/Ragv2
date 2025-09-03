from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any, Dict, List, Tuple

from loguru import logger

from ..core.config import PIPELINE_ARTEFACTS_DIR
from .insert import strip_existing_anchors, anchor_suggestions, build_sections

try:
    import tiktoken
    _enc = tiktoken.encoding_for_model("gpt-4.1")
except Exception:
    import tiktoken
    _enc = tiktoken.get_encoding("cl100k_base")


def _read_json(p: Path, default: Any) -> Any:
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except Exception:
        return default


def _token_count(s: str) -> int:
    try:
        return len(_enc.encode(s or ""))
    except Exception:
        return max(1, len(s or "") // 4)


def _load_suggestions(doc_dir: Path, prefer_final: bool = True) -> List[Dict[str, Any]]:
    final_p = doc_dir / "suggestions.json"
    partial_p = doc_dir / "suggestions_partial.json"
    if prefer_final and final_p.exists():
        return _read_json(final_p, [])
    if partial_p.exists():
        return _read_json(partial_p, [])
    if final_p.exists():
        return _read_json(final_p, [])
    return []


def run_phase3_synthesis(
    document_id: str,
    *,
    prefer_final_suggestions: bool = True,
    max_sentence_len: int = 400,
) -> Dict[str, Any]:
    """Run Sprint-4 synthesis: idempotent footnote anchoring and report generation.

    Inputs (artefacts/<doc_id>/):
      - markdown_v1.md
      - suggestions.json or suggestions_partial.json

    Outputs (artefacts/<doc_id>/):
      - markdown_v2.md
      - anchors_map.json
      - synthesis_report.json
    """
    t0 = time.time()
    doc_dir = Path(PIPELINE_ARTEFACTS_DIR) / document_id
    doc_dir.mkdir(parents=True, exist_ok=True)

    md_v1 = doc_dir / "markdown_v1.md"
    if not md_v1.exists():
        raise FileNotFoundError("markdown_v1.md tidak ditemukan. Pastikan Phase-0 selesai.")

    md_text = md_v1.read_text(encoding="utf-8")
    suggestions = _load_suggestions(doc_dir, prefer_final=prefer_final_suggestions)

    cleaned_text, removed_markers = strip_existing_anchors(md_text)

    anchored_text, anchors_map, footnotes, appendix = anchor_suggestions(
        cleaned_text, suggestions, max_sentence_len=max_sentence_len
    )

    sections = build_sections(footnotes, appendix)
    if sections.strip():
        final_text = (anchored_text.rstrip() + "\n\n" + sections.strip() + "\n")
    else:
        final_text = anchored_text

    v2_path = doc_dir / "markdown_v2.md"
    anchors_map_path = doc_dir / "anchors_map.json"
    report_path = doc_dir / "synthesis_report.json"

    v2_path.write_text(final_text, encoding="utf-8")
    anchors_map_path.write_text(json.dumps(anchors_map, ensure_ascii=False, indent=2), encoding="utf-8")

    elapsed = time.time() - t0
    inserted = sum(1 for v in anchors_map.values() if v.get("inserted"))
    skipped = sum(1 for v in anchors_map.values() if not v.get("inserted"))

    report = {
        "document_id": document_id,
        "elapsed_sec": round(elapsed, 3),
        "removed_existing_markers": int(removed_markers),
        "suggestions_total": len(suggestions),
        "anchors_inserted": int(inserted),
        "anchors_skipped": int(skipped),
        "appendix_count": int(len(appendix)),
        "doc_tokens_v1": _token_count(md_text),
        "doc_tokens_v2": _token_count(final_text),
        "footnote_count": int(len(footnotes)),
    }
    report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")

    logger.info(
        f"[Synthesis] doc_id={document_id} inserted={inserted} skipped={skipped} appendix={len(appendix)} time_sec={round(elapsed,3)}"
    )

    return report
