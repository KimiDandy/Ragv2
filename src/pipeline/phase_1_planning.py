import json
from pathlib import Path
from typing import List, Dict, Any
from hashlib import sha256
import asyncio
import time
import re
from pydantic import BaseModel, Field
from langchain_openai import ChatOpenAI
from loguru import logger

from ..core.config import CHAT_MODEL, PIPELINE_ARTEFACTS_DIR


class PlanItem(BaseModel):
    """Unified item structure for planning with provenance."""
    kind: str  # "term" | "concept" | "connection"
    key: str
    original_context: str
    confidence_score: float = Field(ge=0.0, le=1.0)
    provenance: Dict[str, Any]


class PlanOutput(BaseModel):
    terms_to_define: List[Dict[str, Any]] = []
    concepts_to_simplify: List[Dict[str, Any]] = []
    inferred_connections: List[Dict[str, Any]] = []


def _norm_key(s: str) -> str:
    return re.sub(r"\s+", " ", (s or "").strip().lower())[:200]


def _cache_path(doc_dir: Path, key: str) -> Path:
    cdir = doc_dir / "cache_phase1"
    cdir.mkdir(exist_ok=True)
    return cdir / f"{key}.json"


def _hash_for_segment(model: str, prompt: str, text: str) -> str:
    h = sha256()
    h.update(model.encode("utf-8"))
    h.update(b"\x00")
    h.update(prompt.encode("utf-8"))
    h.update(b"\x00")
    h.update(text.encode("utf-8"))
    return h.hexdigest()


def _build_prompt(segment_text: str) -> str:
    # Use existing required schema to keep compatibility
    prompt_intro = (
        "Peran: Anda adalah analis riset multidisiplin.\n"
        "Tugas: Baca paragraf berikut. Identifikasi item yang butuh definisi/simplifikasi atau koneksi implisit.\n"
        "Balas HANYA satu objek JSON valid sesuai skema.\n"
    )
    schema_block = (
        "{"
        "\n  \"terms_to_define\": [{\n    \"term\": \"string\",\n    \"original_context\": \"string\",\n    \"confidence_score\": 0.0\n  }],"
        "\n  \"concepts_to_simplify\": [{\n    \"identifier\": \"string\",\n    \"original_context\": \"string\",\n    \"confidence_score\": 0.0\n  }],"
        "\n  \"inferred_connections\": [{\n    \"from_concept\": \"string\",\n    \"to_concept\": \"string\",\n    \"relationship_type\": \"string\",\n    \"confidence_score\": 0.0,\n    \"original_context\": \"string\"\n  }]\n}"
    )
    return (
        prompt_intro
        + "\nSkema Wajib:\n"
        + schema_block
        + "\n\nParagraf:\n---\n"
        + segment_text
        + "\n---\n"
    )


async def _plan_one(chat: ChatOpenAI, doc_dir: Path, seg: dict, sem: asyncio.Semaphore, stats: dict) -> List[PlanItem]:
    text = seg.get("text", "")
    if not text.strip():
        return []
    prompt = _build_prompt(text)
    key = _hash_for_segment(chat.model_name, prompt, text)
    cpath = _cache_path(doc_dir, key)
    if cpath.exists():
        try:
            with open(cpath, "r", encoding="utf-8") as f:
                data = json.load(f)
            stats["cache_hits"] += 1
            return _to_items(data, seg)
        except Exception:
            pass

    async with sem:
        try:
            resp = await asyncio.get_event_loop().run_in_executor(None, chat.invoke, prompt)
            raw = getattr(resp, "content", None) or str(resp)
        except Exception as e:
            logger.warning(f"Map call failed for segment {seg.get('segment_id')}: {e}")
            return []

    # parse json
    parsed = _parse_json_strict(raw)
    if parsed is None:
        return []

    try:
        with open(cpath, "w", encoding="utf-8") as f:
            json.dump(parsed, f, ensure_ascii=False)
    except Exception:
        pass

    stats["llm_calls"] += 1
    return _to_items(parsed, seg)


def _parse_json_strict(raw_text: str):
    if not raw_text:
        return None
    js = raw_text.replace('```json', '').replace('```', '').strip()
    try:
        return json.loads(js)
    except json.JSONDecodeError:
        m = re.search(r"(\{[\s\S]*\}|\[[\s\S]*\])", js)
        if m:
            try:
                return json.loads(m.group(0))
            except json.JSONDecodeError:
                return None
        return None


def _to_items(parsed: dict, seg: dict) -> List[PlanItem]:
    items: List[PlanItem] = []
    prov = {
        "segment_id": seg.get("segment_id"),
        "page": seg.get("page"),
        "char_start": seg.get("char_start"),
        "char_end": seg.get("char_end"),
        "header_path": seg.get("header_path"),
    }
    for obj in (parsed.get("terms_to_define") or []):
        if isinstance(obj, str):
            term = obj
            ctx = obj
            conf = 0.5
        else:
            term = obj.get("term") or obj.get("name")
            ctx = obj.get("original_context") or obj.get("context") or ""
            conf = float(obj.get("confidence_score", 0.5))
        if term:
            items.append(PlanItem(kind="term", key=term, original_context=ctx, confidence_score=conf, provenance=prov))

    for obj in (parsed.get("concepts_to_simplify") or []):
        if isinstance(obj, str):
            ident = obj
            ctx = obj
            conf = 0.5
        else:
            ident = obj.get("identifier") or obj.get("id") or obj.get("name")
            ctx = obj.get("original_context") or obj.get("paragraph_text") or ""
            conf = float(obj.get("confidence_score", 0.5))
        if ident:
            items.append(PlanItem(kind="concept", key=ident, original_context=ctx, confidence_score=conf, provenance=prov))

    for obj in (parsed.get("inferred_connections") or []):
        f = (obj or {}).get("from_concept")
        t = (obj or {}).get("to_concept")
        rel = (obj or {}).get("relationship_type")
        ctx = (obj or {}).get("original_context") or ""
        conf = float((obj or {}).get("confidence_score", 0.5))
        if f and t:
            items.append(PlanItem(kind="connection", key=f"{f} -> {t} ({rel})", original_context=ctx, confidence_score=conf, provenance=prov))
    return items


def _gate(segments: List[dict]) -> List[dict]:
    gated = []
    for s in segments:
        if s.get("is_difficult") or s.get("contains_entities"):
            gated.append(s)
            continue
        # header-based allowlist
        hp = " ".join(s.get("header_path") or [])
        if re.search(r"\b(introduction|overview|summary|conclusion|results)\b", hp, re.I):
            gated.append(s)
    return gated


async def create_enrichment_plan(doc_output_dir: str) -> str:
    """Phase-1: Gate → Map (async + file cache) → Reduce over segments.json.
    Writes plan.json and enrichment_plan.json (compat) and metrics.
    """
    doc_dir = Path(doc_output_dir)
    segments_path = doc_dir / "segments.json"
    if not segments_path.exists():
        logger.error(f"segments.json tidak ditemukan di {segments_path}")
        return ""

    with open(segments_path, "r", encoding="utf-8") as f:
        segments: List[dict] = json.load(f)

    gated = _gate(segments)
    logger.info(f"Phase-1 Gate: {len(gated)}/{len(segments)} segmen dipilih")

    chat = ChatOpenAI(model=CHAT_MODEL, temperature=0.2)
    sem = asyncio.Semaphore(6)

    stats = {"cache_hits": 0, "llm_calls": 0}
    t0 = time.time()

    results: List[List[PlanItem]] = await asyncio.gather(
        *[_plan_one(chat, doc_dir, seg, sem, stats) for seg in gated]
    )

    flat: List[PlanItem] = [it for sub in results for it in sub]

    # Reduce: dedup by normalized key
    terms_map: Dict[str, Dict[str, Any]] = {}
    concepts_map: Dict[str, Dict[str, Any]] = {}
    connections: List[Dict[str, Any]] = []

    for it in flat:
        if it.kind == "term":
            k = _norm_key(it.key)
            if k not in terms_map:
                terms_map[k] = {
                    "term": it.key,
                    "original_context": it.original_context,
                    "confidence_score": it.confidence_score,
                    "provenances": [it.provenance],
                }
            else:
                terms_map[k]["provenances"].append(it.provenance)
                terms_map[k]["confidence_score"] = max(terms_map[k]["confidence_score"], it.confidence_score)
        elif it.kind == "concept":
            k = _norm_key(it.key)
            if k not in concepts_map:
                concepts_map[k] = {
                    "identifier": it.key,
                    "original_context": it.original_context,
                    "confidence_score": it.confidence_score,
                    "provenances": [it.provenance],
                }
            else:
                concepts_map[k]["provenances"].append(it.provenance)
                concepts_map[k]["confidence_score"] = max(concepts_map[k]["confidence_score"], it.confidence_score)
        else:
            connections.append({
                "connection": it.key,
                "original_context": it.original_context,
                "confidence_score": it.confidence_score,
                "provenance": it.provenance,
            })

    plan = PlanOutput(
        terms_to_define=list(terms_map.values()),
        concepts_to_simplify=list(concepts_map.values()),
        inferred_connections=connections,
    )

    plan_path = doc_dir / "plan.json"
    with open(plan_path, "w", encoding="utf-8") as f:
        json.dump(plan.model_dump(), f, ensure_ascii=False, indent=2)

    # Back-compat for Phase-2
    compat_path = doc_dir / "enrichment_plan.json"
    with open(compat_path, "w", encoding="utf-8") as f:
        json.dump(plan.model_dump(), f, ensure_ascii=False, indent=2)

    # Metrics
    metrics = {
        "gated": len(gated),
        "total_segments": len(segments),
        "llm_calls": stats["llm_calls"],
        "cache_hits": stats["cache_hits"],
        "duration_sec": time.time() - t0,
    }
    with open(doc_dir / "phase_1_metrics.json", "w", encoding="utf-8") as f:
        json.dump(metrics, f, ensure_ascii=False, indent=2)

    logger.info(f"Phase-1 selesai. plan.json disimpan di: {plan_path}")
    return str(plan_path)


if __name__ == '__main__':
    base_artefacts_dir = Path(PIPELINE_ARTEFACTS_DIR)
    if not base_artefacts_dir.exists():
        logger.error(f"Direktori '{PIPELINE_ARTEFACTS_DIR}' tidak ditemukan. Jalankan phase_0 terlebih dahulu.")
    else:
        all_doc_dirs = [d for d in base_artefacts_dir.iterdir() if d.is_dir()]
        if not all_doc_dirs:
            logger.error(f"Tidak ada direktori dokumen di '{PIPELINE_ARTEFACTS_DIR}'.")
        else:
            latest_doc_dir = max(all_doc_dirs, key=lambda d: d.stat().st_mtime)
            logger.info(f"Menjalankan Fase 1 pada direktori: {latest_doc_dir}")
            asyncio.run(create_enrichment_plan(str(latest_doc_dir)))