import json
import re
from pathlib import Path
from loguru import logger

from ..core.config import PIPELINE_ARTEFACTS_DIR

# Known mappings for common Indonesian role abbreviations
KNOWN_ROLE_ABBR_MAP = {
    # Direktur variants
    "dir.ut.": "Direktur Utama",
    "dirut": "Direktur Utama",
    "dir. op.": "Direktur Operasional",
    "dir. keu.": "Direktur Keuangan",
    "dir. tek.": "Direktur Teknik",
    "dir. mkt.": "Direktur Pemasaran",
    "dir. pem.": "Direktur Pembinaan",
    # Komisaris variants
    "kom. ut.": "Komisaris Utama",
    "komut": "Komisaris Utama",
}

ROLE_KEYWORDS = [
    "direktur", "komisaris", "manajer", "manager", "ketua", "wakil",
    "sekretaris", "bendahara", "chief", "ceo", "cfo", "coo",
]

def _norm(s: str) -> str:
    return (s or "").strip().lower().replace("  ", " ")

def _is_role_term(s: str) -> bool:
    ls = _norm(s)
    return any(k in ls for k in ROLE_KEYWORDS)


def _extract_pairs_parenthetical(text: str):
    pairs = []
    if not text:
        return pairs
    # Long (Short)
    pattern_long_short = re.compile(r"([A-Za-zÀ-ÖØ-öø-ÿ][^()\n]{2,100}?)\s*\(([^)\n]{2,40})\)")
    # Short (Long)
    pattern_short_long = re.compile(r"([A-Z][A-Za-z.\s]{1,20})\s*\(([^)\n]{3,100})\)")
    for m in pattern_long_short.finditer(text):
        long_form = (m.group(1) or "").strip()
        short_form = (m.group(2) or "").strip()
        # Heuristic: treat short if contains dots or is all caps <= 10 chars
        if "." in short_form or (short_form.isupper() and len(short_form) <= 10):
            pairs.append((short_form, long_form))
    for m in pattern_short_long.finditer(text):
        short_form = (m.group(1) or "").strip()
        long_form = (m.group(2) or "").strip()
        if "." in short_form or (short_form.isupper() and len(short_form) <= 10):
            pairs.append((short_form, long_form))
    return pairs


def _extract_dot_abbr(text: str):
    # Capture dotted abbreviations like "Dir. Ut.", "A.B.C.", up to 4 parts
    pattern = re.compile(r"\b(?:[A-Za-z]{2,5}\.)\s*(?:[A-Za-z]{2,5}\.){0,3}")
    return set(m.group(0).strip() for m in pattern.finditer(text or ""))


def build_global_glossary(markdown_text: str) -> dict:
    """Return a glossary map favoring role titles for role-style abbreviations.

    Rules:
    - If short matches KNOWN_ROLE_ABBR_MAP, map to the known role title.
    - If pair looks like Person Name (Role Abbr), map short -> role title (from known map if possible).
    - If pair is Role Title (Role Abbr), map short -> role title.
    - For dotted abbreviations found without pairs, map using KNOWN_ROLE_ABBR_MAP if matched; else keep as-is.
    """
    text = markdown_text or ""
    pairs = _extract_pairs_parenthetical(text)
    abbrs = _extract_dot_abbr(text)

    glossary: dict[str, str] = {}

    def _apply(short: str, longf: str):
        s_norm = _norm(short)
        # Prefer known mapping if available
        canonical = KNOWN_ROLE_ABBR_MAP.get(s_norm, longf)
        # Ensure we don't map role abbr to a person name; if longf not a role, but abbreviation suggests a role, force role title when known
        if not _is_role_term(longf) and any(tok in s_norm for tok in ("dir", "kom")):
            canonical = KNOWN_ROLE_ABBR_MAP.get(s_norm, longf if _is_role_term(longf) else longf)
        glossary[short] = canonical
        glossary[short.replace(" ", "")] = canonical

    for short, longf in pairs:
        s_norm = _norm(short)
        longf = (longf or "").strip()
        # Person Name (Abbr) -> prefer role title for abbreviation
        if not _is_role_term(longf) and any(tok in s_norm for tok in ("dir", "kom")):
            canon = KNOWN_ROLE_ABBR_MAP.get(s_norm)
            if canon:
                _apply(short, canon)
                continue
        # Role Title (Abbr) or known mapping
        if _is_role_term(longf) or s_norm in KNOWN_ROLE_ABBR_MAP:
            _apply(short, KNOWN_ROLE_ABBR_MAP.get(s_norm, longf))
            continue
        # Default
        _apply(short, longf)

    for ab in abbrs:
        s_norm = _norm(ab)
        if s_norm in KNOWN_ROLE_ABBR_MAP:
            _apply(ab, KNOWN_ROLE_ABBR_MAP[s_norm])
        else:
            glossary.setdefault(ab, ab)

    return {"glossary": glossary}


def extract_global_glossary(doc_output_dir: str) -> str:
    """Read markdown_v1.md, extract glossary, and save global_glossary.json.
    Returns the path to the saved glossary file.
    """
    doc_path = Path(doc_output_dir)
    md_path = doc_path / "markdown_v1.md"
    if not md_path.exists():
        logger.error(f"markdown_v1.md tidak ditemukan di {doc_output_dir}")
        return ""
    try:
        text = md_path.read_text(encoding="utf-8")
    except Exception as e:
        logger.error(f"Gagal membaca markdown_v1.md: {e}")
        return ""

    data = build_global_glossary(text)
    out_path = doc_path / "global_glossary.json"
    try:
        out_path.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
        logger.info(f"Global glossary diekstraksi: {out_path}")
    except Exception as e:
        logger.error(f"Gagal menulis global_glossary.json: {e}")
        return ""
    return str(out_path)
