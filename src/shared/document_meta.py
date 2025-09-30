"""
Document Metadata Management

Professional utilities for managing document metadata including 
filenames, markdown paths, and document information.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any, Dict, Optional

META_FILENAME = "document_meta.json"


def _meta_path(doc_dir: Path) -> Path:
    return doc_dir / META_FILENAME


def load_doc_meta(doc_dir: Path) -> Dict[str, Any]:
    """Load document metadata if available."""
    path = _meta_path(doc_dir)
    if not path.exists():
        return {}
    try:
        with path.open("r", encoding="utf-8") as f:
            return json.load(f)
    except json.JSONDecodeError:
        # Return empty to avoid cascading failures; caller should handle fallback.
        return {}


def save_doc_meta(doc_dir: Path, data: Dict[str, Any]) -> None:
    path = _meta_path(doc_dir)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as f:
        json.dump(data, f, indent=2)


def sanitize_basename(name: str) -> str:
    sanitized = re.sub(r"[\\/:*?\"<>|]+", "_", name)
    sanitized = sanitized.strip()
    return sanitized or "document"


def set_original_pdf_filename(doc_dir: Path, filename: str) -> None:
    meta = load_doc_meta(doc_dir)
    meta["original_pdf_filename"] = filename
    meta.setdefault("base_name", sanitize_basename(Path(filename).stem))
    save_doc_meta(doc_dir, meta)


def default_markdown_filename(version: str, base_name: str) -> str:
    if version == "v2":
        return f"{base_name}_enhanced.md"
    return f"{base_name}.md"


def get_base_name(doc_dir: Path) -> str:
    meta = load_doc_meta(doc_dir)
    if "base_name" not in meta:
        meta["base_name"] = sanitize_basename(doc_dir.name)
        save_doc_meta(doc_dir, meta)
    return meta["base_name"]


def set_markdown_info(
    doc_dir: Path,
    version: str,
    filename: str,
    relative_path: Optional[str] = None,
    extra: Optional[Dict[str, Any]] = None,
) -> None:
    """Persist markdown filename/path info for a specific version."""
    meta = load_doc_meta(doc_dir)
    if extra:
        meta.update(extra)
    key = f"markdown_{version}"
    meta[key] = {
        "filename": filename,
        "relative_path": relative_path or filename,
    }
    save_doc_meta(doc_dir, meta)


def get_markdown_info(doc_dir: Path, version: str) -> Optional[Dict[str, Any]]:
    meta = load_doc_meta(doc_dir)
    return meta.get(f"markdown_{version}")


def get_markdown_relative_path(doc_dir: Path, version: str) -> str:
    info = get_markdown_info(doc_dir, version)
    if info and info.get("relative_path"):
        return info["relative_path"]
    legacy_map = {
        "v1": "markdown_v1.md",
        "v2": "markdown_v2.md",
    }
    legacy_name = legacy_map.get(version)
    if legacy_name and (doc_dir / legacy_name).exists():
        return legacy_name
    base_name = get_base_name(doc_dir)
    return default_markdown_filename(version, base_name)


def get_markdown_path(doc_dir: Path, version: str) -> Path:
    return doc_dir / get_markdown_relative_path(doc_dir, version)


def get_markdown_filename(doc_dir: Path, version: str) -> Optional[str]:
    info = get_markdown_info(doc_dir, version)
    if info:
        return info.get("filename")
    return None


def get_original_pdf_filename(doc_dir: Path) -> Optional[str]:
    meta = load_doc_meta(doc_dir)
    return meta.get("original_pdf_filename")
