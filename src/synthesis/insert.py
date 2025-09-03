from __future__ import annotations

import json
import re
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from .anchoring import (
    tokenize_with_map,
    adjust_left_to_token_boundary,
    expand_to_sentence,
    nearest_safe_insert_after,
)

FN_MARK_RE = re.compile(r"\[\^fn(\d+)\]")
SECTION_FOOTNOTES_RE = re.compile(r"^##\s+Footnotes\s*$", re.M)
SECTION_APPENDIX_RE = re.compile(r"^##\s+Appendix\s*$", re.M)
PLACEHOLDER_RE = re.compile(r"\[\[FN:([^\]]+)\]\]")


@dataclass
class AnchorResult:
    id: str
    index: int
    sentence_span: Tuple[int, int]
    inserted: bool
    reason: str | None = None


def strip_existing_anchors(md_text: str) -> tuple[str, int]:
    """Remove existing [^fnX] markers and Footnotes/Appendix sections to ensure idempotency.
    Returns cleaned_text and count_removed_markers.
    """
    removed = len(FN_MARK_RE.findall(md_text))
    text = FN_MARK_RE.sub("", md_text)

    # Remove Footnotes and Appendix sections (only from the last ones onwards)
    def _strip_section(t: str, pattern: re.Pattern) -> str:
        m = list(pattern.finditer(t))
        if not m:
            return t
        start = m[-1].start()
        return t[:start].rstrip() + "\n"

    text = _strip_section(text, SECTION_FOOTNOTES_RE)
    text = _strip_section(text, SECTION_APPENDIX_RE)
    return text, removed


def _find_context_pos(md_text: str, context: str) -> Optional[int]:
    if not context:
        return None
    ctx = (context or "").strip()
    if not ctx:
        return None
    # Try exact match
    pos = md_text.find(ctx)
    if pos != -1:
        return pos
    # Fuzzy: first 40 chars
    stub = ctx[:40]
    if len(stub) >= 10:
        pos = md_text.find(stub)
        if pos != -1:
            return pos
    # Fuzzy: any 3-word window
    words = ctx.split()
    for wlen in (5, 4, 3):
        if len(words) >= wlen:
            span = " ".join(words[:wlen])
            pos = md_text.find(span)
            if pos != -1:
                return pos
    return None


def _insert_at(text: str, pos: int, marker: str) -> str:
    return text[:pos] + marker + text[pos:]


def anchor_suggestions(
    md_text: str,
    suggestions: List[Dict[str, Any]],
    *,
    max_sentence_len: int = 400,
) -> tuple[str, Dict[str, Any], List[Dict[str, Any]], List[Dict[str, Any]]]:
    """Insert footnote markers for suggestions into the markdown text.

    Returns (new_text, anchors_map, footnotes, appendix)
    - anchors_map: { fn<number>: {suggestion_id, position, sentence_span, inserted, reason?}, ... }
    - footnotes: list of {number, id, label, content}
    - appendix: same as footnotes but for items not inserted
    """
    anchors_map: Dict[str, Any] = {}
    footnotes: List[Dict[str, Any]] = []
    appendix: List[Dict[str, Any]] = []

    text = md_text
    placeholders: List[Tuple[str, int]] = []  # (suggestion_id, position)

    for i, sug in enumerate(suggestions, start=1):
        sid = str(sug.get("id") or f"s{i}")
        content = (sug.get("generated_content") or "").strip()
        context = (sug.get("original_context") or "").strip()
        label = (sug.get("type") or "item").capitalize()
        placeholder = f"[[FN:{sid}]]"

        if not content:
            anchors_map[f"pending:{i}"] = {
                "suggestion_id": sid,
                "inserted": False,
                "reason": "empty_content",
            }
            continue

        pos = _find_context_pos(text, context)
        if pos is None:
            appendix.append({"id": sid, "label": label, "content": content})
            anchors_map[f"pending:{i}"] = {
                "suggestion_id": sid,
                "inserted": False,
                "reason": "context_not_found",
            }
            continue

        # Determine sentence span and insertion point
        s, e = expand_to_sentence(text, pos, pos + min(len(context), 50), max_len=max_sentence_len)
        # prefer insert after sentence end near e
        insert_after = nearest_safe_insert_after(text, e)
        if insert_after is None:
            # fallback: end of sentence span
            insert_after = e
        # token-aware left boundary check (avoid mid-token)
        try:
            _, maps = tokenize_with_map(text)
            insert_after = adjust_left_to_token_boundary(insert_after, maps)
        except Exception:
            pass

        text = _insert_at(text, insert_after, placeholder)
        placeholders.append((sid, insert_after))
        anchors_map[f"pending:{i}"] = {
            "suggestion_id": sid,
            "position": insert_after,
            "sentence_span": [s, e],
            "inserted": True,
        }

    # Renumber placeholders in order of appearance (left-to-right)
    sid_to_number: Dict[str, int] = {}
    order: List[str] = []
    def _assign(m: re.Match) -> str:
        sid = m.group(1)
        if sid not in sid_to_number:
            sid_to_number[sid] = len(sid_to_number) + 1
            order.append(sid)
        num = sid_to_number[sid]
        return f"[^fn{num}]"

    text = PLACEHOLDER_RE.sub(_assign, text)

    # Build footnotes list aligned with numbering
    sug_by_id = {str(s.get("id") or ""): s for s in suggestions}
    for sid in order:
        s = sug_by_id.get(sid) or {}
        label = (s.get("type") or "item").capitalize()
        content = (s.get("generated_content") or "").strip()
        number = sid_to_number[sid]
        footnotes.append({"number": number, "id": sid, "label": label, "content": content})

    # Update anchors_map keys with actual fn numbers
    final_map: Dict[str, Any] = {}
    for k, v in anchors_map.items():
        if not v.get("inserted"):
            continue
        sid = v.get("suggestion_id")
        num = sid_to_number.get(str(sid))
        if num is not None:
            final_map[f"fn{num}"] = v
    # Add failures (non-inserted) with synthetic keys
    fail_idx = 1
    for k, v in anchors_map.items():
        if v.get("inserted"):
            continue
        final_map[f"skip{fail_idx}"] = v
        fail_idx += 1

    return text, final_map, footnotes, appendix


def build_sections(footnotes: List[Dict[str, Any]], appendix: List[Dict[str, Any]]) -> str:
    parts: List[str] = []
    if footnotes:
        parts.append("\n\n## Footnotes\n")
        for it in footnotes:
            parts.append(f"[^fn{it['number']}]: {it['content'].strip()}\n")
    if appendix:
        parts.append("\n\n## Appendix\n")
        for it in appendix:
            parts.append(f"- {it['label']}: {it['content'].strip()}\n")
    return "".join(parts)
