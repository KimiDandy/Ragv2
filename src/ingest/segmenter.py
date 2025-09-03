from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import List, Dict, Any
from loguru import logger

from ..core.config import PIPELINE_ARTEFACTS_DIR

# Optional header detection hooks (kept for API completeness in Sprint-1)
def detect_headers(lines: List[str]) -> List[str]:
    """Very light placeholder; headers are already inferred in phase_0_extraction.
    Returned value is a header_path list based on simple numbering/uppercase heuristics if needed.
    """
    # In Sprint-1 we rely on phase_0_extraction header_path; this is a no-op shim.
    return [l.strip() for l in lines if l and l.strip()]


def _to_ssot(seg: Dict[str, Any]) -> Dict[str, Any]:
    """Transform existing segment dict into SSOT-style without breaking old consumers.
    SSOT keys:
      id, page, char [start,end], header_path, text, flags{contains_entities,is_difficult}, numeric_ratio, hash
    """
    text = seg.get("text", "") or ""
    sha = hashlib.sha256(text.encode("utf-8")).hexdigest()
    return {
        "id": seg.get("segment_id") or seg.get("id") or "",
        "page": int(seg.get("page") or 0),
        "char": [int(seg.get("char_start") or 0), int(seg.get("char_end") or 0)],
        "header_path": list(seg.get("header_path") or []),
        "text": text,
        "flags": {
            "contains_entities": bool(seg.get("contains_entities")),
            "is_difficult": bool(seg.get("is_difficult")),
        },
        "numeric_ratio": float(seg.get("numeric_ratio") or 0.0),
        "hash": f"sha256:{sha}",
    }


def write_ssot_segments(doc_id: str, segments: List[Dict[str, Any]]) -> Path:
    """Write SSOT-compatible segments alongside legacy segments.json.
    Returns the written path.
    """
    doc_dir = Path(PIPELINE_ARTEFACTS_DIR) / doc_id
    doc_dir.mkdir(parents=True, exist_ok=True)
    ssot = [_to_ssot(s) for s in segments]
    out = doc_dir / "segments_ssot.json"
    out.write_text(json.dumps(ssot, ensure_ascii=False, indent=2), encoding="utf-8")
    logger.info(f"SSOT segments ditulis ke: {out}")
    return out


def segment_pdf(pdf_path: str, doc_id: str) -> List[Dict[str, Any]]:
    """Compatibility stub: Prefer using phase_0_extraction.process_pdf_local upstream.
    This function loads the already-generated segments.json and returns them.
    """
    doc_dir = Path(PIPELINE_ARTEFACTS_DIR) / doc_id
    seg_path = doc_dir / "segments.json"
    if not seg_path.exists():
        logger.error(f"segments.json tidak ditemukan di {seg_path}. Pastikan Phase-0 sudah berjalan.")
        return []
    try:
        segments = json.loads(seg_path.read_text(encoding="utf-8"))
    except Exception as e:
        logger.error(f"Gagal membaca segments.json: {e}")
        return []
    # Write SSOT sidecar for future phases (does not break legacy consumers)
    try:
        write_ssot_segments(doc_id, segments)
    except Exception as e:
        logger.warning(f"Gagal menulis segments_ssot.json: {e}")
    return segments
