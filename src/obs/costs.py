from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Dict, Tuple

from ..core.config import PIPELINE_ARTEFACTS_DIR

IDR_PER_USD = float(os.getenv("IDR_PER_USD", "16000"))

# USD per 1K tokens (approx; override via env OPENAI_PRICE_<MODEL>_IN/_OUT)
_DEFAULT_PRICES_USD: Dict[str, Tuple[float, float]] = {
    # model: (input_per_1k, output_per_1k)
    "gpt-4o-mini": (0.15, 0.6),
    "gpt-4o": (5.0, 15.0),
    "gpt-4.1": (5.0, 15.0),
    "gpt-3.5-turbo": (0.5, 1.5),
    # embeddings
    "text-embedding-3-large": (0.13, 0.0),
    "text-embedding-3-small": (0.02, 0.0),
}


def _load_price_for(model: str) -> Tuple[float, float]:
    key_base = model.upper().replace("-", "_").replace(":", "_")
    pin = os.getenv(f"OPENAI_PRICE_{key_base}_IN")
    pout = os.getenv(f"OPENAI_PRICE_{key_base}_OUT")
    if pin is not None or pout is not None:
        try:
            return (float(pin or 0.0), float(pout or 0.0))
        except Exception:
            pass
    return _DEFAULT_PRICES_USD.get(model, (0.0, 0.0))


def estimate_cost_idr(model: str, token_in: int = 0, token_out: int = 0) -> float:
    pin, pout = _load_price_for(model)
    usd = ((token_in / 1000.0) * pin) + ((token_out / 1000.0) * pout)
    return float(usd * IDR_PER_USD)


def write_cost_report(doc_id: str, cost_by_phase: Dict[str, Dict[str, float]]):
    base = Path(PIPELINE_ARTEFACTS_DIR) / doc_id / "reports"
    base.mkdir(parents=True, exist_ok=True)
    (base / "cost_report.json").write_text(
        json.dumps(cost_by_phase, ensure_ascii=False, indent=2), encoding="utf-8"
    )
