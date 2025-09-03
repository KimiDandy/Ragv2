from __future__ import annotations

import json
import os
import threading
import time
from dataclasses import dataclass, asdict
from pathlib import Path
from typing import Any, Dict, List, Optional

from loguru import logger

from ..core.config import PIPELINE_ARTEFACTS_DIR

# In-memory registries (simple, per-process)
_REGISTRY: dict[str, "Metrics"] = {}
_CANCEL: set[str] = set()
_LOCK = threading.Lock()


@dataclass
class MetricEvent:
    ts: str
    doc_id: str
    phase: str
    event: str  # start|batch_done|end|timeout|error|cache_hit|budget_stop|circuit_open
    values: Dict[str, Any]
    meta: Dict[str, Any]


def _iso_now() -> str:
    # ISO 8601 with milliseconds, Z suffix
    return time.strftime("%Y-%m-%dT%H:%M:%S", time.gmtime()) + f".{int((time.time()%1)*1000):03d}Z"


def _safe_snippet(s: str, max_len: int = 200) -> str:
    s = (s or "").replace("\n", " ").strip()
    return (s[:max_len] + "…") if len(s) > max_len else s


class Metrics:
    def __init__(self, doc_id: str, flush_every: int = 10):
        self.doc_id = doc_id
        self.flush_every = max(1, int(flush_every))
        self._buf: List[MetricEvent] = []
        self._buf_lock = threading.Lock()
        self.base_dir = Path(PIPELINE_ARTEFACTS_DIR) / doc_id
        self.logs_dir = self.base_dir / "logs"
        self.reports_dir = self.base_dir / "reports"
        self.logs_dir.mkdir(parents=True, exist_ok=True)
        self.reports_dir.mkdir(parents=True, exist_ok=True)
        self.metrics_path = self.logs_dir / "metrics.jsonl"
        self.errors_path = self.logs_dir / "errors.jsonl"

    @staticmethod
    def get(doc_id: str) -> "Metrics":
        with _LOCK:
            m = _REGISTRY.get(doc_id)
            if not m:
                m = Metrics(doc_id)
                _REGISTRY[doc_id] = m
            return m

    # Cancel helpers (global)
    @staticmethod
    def set_cancel(doc_id: str):
        with _LOCK:
            _CANCEL.add(doc_id)

    @staticmethod
    def clear_cancel(doc_id: str):
        with _LOCK:
            _CANCEL.discard(doc_id)

    @staticmethod
    def is_cancelled(doc_id: str) -> bool:
        with _LOCK:
            return doc_id in _CANCEL

    def emit(self, phase: str, event: str, values: Optional[Dict[str, Any]] = None, meta: Optional[Dict[str, Any]] = None):
        try:
            values = values or {}
            meta = meta or {}
            # privacy: trim any long text fields in values/meta
            for k in list(values.keys()):
                v = values[k]
                if isinstance(v, str):
                    values[k] = _safe_snippet(v, 200)
            for k in list(meta.keys()):
                v = meta[k]
                if isinstance(v, str):
                    meta[k] = _safe_snippet(v, 200)

            ev = MetricEvent(ts=_iso_now(), doc_id=self.doc_id, phase=phase, event=event, values=values, meta=meta)
            with self._buf_lock:
                self._buf.append(ev)
                if len(self._buf) >= self.flush_every:
                    self.flush()
        except Exception as e:
            logger.warning(f"[Metrics] emit failed: {e}")

    def log_error(self, phase: str, error: str, meta: Optional[Dict[str, Any]] = None):
        try:
            meta = meta or {}
            row = {
                "ts": _iso_now(),
                "doc_id": self.doc_id,
                "phase": phase,
                "error": _safe_snippet(str(error), 200),
                "meta": meta,
            }
            with self.errors_path.open("a", encoding="utf-8") as f:
                f.write(json.dumps(row, ensure_ascii=False) + "\n")
        except Exception as e:
            logger.warning(f"[Metrics] log_error failed: {e}")

    def flush(self):
        try:
            if not self._buf:
                return
            with self.metrics_path.open("a", encoding="utf-8") as f:
                for ev in self._buf:
                    f.write(json.dumps(asdict(ev), ensure_ascii=False) + "\n")
            self._buf.clear()
        except Exception as e:
            logger.warning(f"[Metrics] flush failed: {e}")

    def summary(self) -> Dict[str, Any]:
        """Compute lightweight summary from metrics.jsonl (best-effort)."""
        phases: Dict[str, Dict[str, Any]] = {}
        lat_all: Dict[str, List[float]] = {}
        tokens_in: Dict[str, int] = {}
        tokens_out: Dict[str, int] = {}
        cost_idr: Dict[str, float] = {}
        timeouts: Dict[str, int] = {}
        cache_hits: Dict[str, int] = {}
        processed: Dict[str, int] = {}
        total_hint: Dict[str, int] = {}
        try:
            if self.metrics_path.exists():
                with self.metrics_path.open("r", encoding="utf-8") as f:
                    for line in f:
                        line = line.strip()
                        if not line:
                            continue
                        obj = json.loads(line)
                        ph = str(obj.get("phase") or "").upper()
                        vals = obj.get("values") or {}
                        evt = obj.get("event") or ""
                        if "latency_ms" in vals:
                            lat_all.setdefault(ph, []).append(float(vals.get("latency_ms") or 0.0))
                        tokens_in[ph] = tokens_in.get(ph, 0) + int(vals.get("token_in") or 0)
                        tokens_out[ph] = tokens_out.get(ph, 0) + int(vals.get("token_out") or 0)
                        cost_idr[ph] = cost_idr.get(ph, 0.0) + float(vals.get("cost_idr") or 0.0)
                        timeouts[ph] = timeouts.get(ph, 0) + int(vals.get("timeouts") or 0)
                        cache_hits[ph] = cache_hits.get(ph, 0) + int(vals.get("cache_hit") or 0)
                        if "processed" in vals:
                            processed[ph] = max(processed.get(ph, 0), int(vals.get("processed") or 0))
                        if "total" in vals:
                            total_hint[ph] = max(total_hint.get(ph, 0), int(vals.get("total") or 0))
        except Exception as e:
            logger.warning(f"[Metrics] summary read failed: {e}")

        def percentile(arr: List[float], p: float) -> float:
            if not arr:
                return 0.0
            arr_sorted = sorted(arr)
            k = max(0, min(len(arr_sorted) - 1, int(round((p / 100.0) * (len(arr_sorted) - 1)))))
            return float(arr_sorted[k])

        for ph in set(list(lat_all.keys()) + list(tokens_in.keys()) + list(tokens_out.keys())):
            p50 = percentile(lat_all.get(ph, []), 50)
            p95 = percentile(lat_all.get(ph, []), 95)
            phases[ph] = {
                "p50_ms": round(p50, 2),
                "p95_ms": round(p95, 2),
                "token_in": tokens_in.get(ph, 0),
                "token_out": tokens_out.get(ph, 0),
                "cost_idr": round(cost_idr.get(ph, 0.0), 2),
                "timeouts": timeouts.get(ph, 0),
                "cache_hits": cache_hits.get(ph, 0),
                "processed": processed.get(ph, 0),
                "total": total_hint.get(ph, 0),
            }

        return {
            "document_id": self.doc_id,
            "phases": phases,
            "metrics_log": str(self.metrics_path),
            "errors_log": str(self.errors_path),
        }


# Convenience helpers

def emit(doc_id: str, phase: str, event: str, values: Optional[Dict[str, Any]] = None, meta: Optional[Dict[str, Any]] = None):
    Metrics.get(doc_id).emit(phase=phase, event=event, values=values, meta=meta)


def log_error(doc_id: str, phase: str, error: str, meta: Optional[Dict[str, Any]] = None):
    Metrics.get(doc_id).log_error(phase=phase, error=error, meta=meta)


def get_summary(doc_id: str) -> Dict[str, Any]:
    return Metrics.get(doc_id).summary()


def set_cancel(doc_id: str):
    Metrics.set_cancel(doc_id)


def clear_cancel(doc_id: str):
    Metrics.clear_cancel(doc_id)


def is_cancelled(doc_id: str) -> bool:
    return Metrics.is_cancelled(doc_id)


def flush_metrics(doc_id: str):
    """Force-flush buffered metric events to disk for the given document.
    This ensures logs/metrics.jsonl is written even for short runs.
    """
    Metrics.get(doc_id).flush()
