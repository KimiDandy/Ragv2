from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Dict, List, Optional, Tuple

try:
    import tiktoken
    _enc = tiktoken.encoding_for_model("gpt-4.1")
except Exception:
    import tiktoken
    _enc = tiktoken.get_encoding("cl100k_base")


@dataclass
class TokenMaps:
    char_to_token_idx: List[int]
    token_to_char_span: List[Tuple[int, int]]


def tokenize_with_map(text: str) -> Tuple[List[int], TokenMaps]:
    # encode_with_offsets is not public in tiktoken stable, so we approximate spans by decoding tokens.
    tokens = _enc.encode(text)
    token_to_char_span: List[Tuple[int, int]] = []
    i = 0
    for tok in tokens:
        piece = _enc.decode([tok])
        start = i
        end = i + len(piece)
        token_to_char_span.append((start, end))
        i = end
    # build char->token index map
    char_to_token_idx = [-1] * max(1, len(text))
    for ti, (s, e) in enumerate(token_to_char_span):
        for c in range(s, min(e, len(char_to_token_idx))):
            char_to_token_idx[c] = ti
    return tokens, TokenMaps(char_to_token_idx, token_to_char_span)


def adjust_left_to_token_boundary(char_idx: int, maps: TokenMaps) -> int:
    if char_idx <= 0:
        return 0
    if char_idx >= len(maps.char_to_token_idx):
        return len(maps.char_to_token_idx) - 1
    ti = maps.char_to_token_idx[char_idx]
    if ti < 0 and char_idx > 0:
        # move left until we hit a mapped char
        j = char_idx
        while j > 0 and maps.char_to_token_idx[j] < 0:
            j -= 1
        ti = maps.char_to_token_idx[j]
    if ti < 0:
        return 0
    s, _ = maps.token_to_char_span[ti]
    return s


def adjust_right_to_token_boundary(char_idx: int, maps: TokenMaps) -> int:
    N = len(maps.char_to_token_idx)
    if char_idx >= N:
        return N
    if char_idx < 0:
        return 0
    ti = maps.char_to_token_idx[char_idx-1] if char_idx > 0 else maps.char_to_token_idx[0]
    if ti < 0:
        j = min(char_idx, N - 1)
        while j < N and maps.char_to_token_idx[j] < 0:
            j += 1
        ti = maps.char_to_token_idx[j-1] if j > 0 else maps.char_to_token_idx[0]
    if ti < 0:
        return N
    _, e = maps.token_to_char_span[ti]
    return e


# Sentence boundary regex: try to split around ., !, ?, … followed by space/newline and uppercase or quote
_SENT_END = re.compile(r"(?<=[\.!?…])\s+")

# Unsafe regions patterns
_URL = re.compile(r"https?://\S+")
_CODE_FENCE = re.compile(r"^\s*```", re.M)
_INLINE_CODE = re.compile(r"`+")
_HEADING = re.compile(r"^\s*#{1,6}\s", re.M)
_HTML_TAG = re.compile(r"<[^>]+>")
_LINK_MD = re.compile(r"\[[^\]]+\]\([^\)]+\)")


def _in_region(pattern: re.Pattern, text: str, pos: int) -> bool:
    for m in pattern.finditer(text):
        if m.start() <= pos < m.end():
            return True
    return False


def is_safe_region(text: str, pos: int) -> bool:
    if pos < 0 or pos > len(text):
        return False
    # inside code fence line
    for fence in _CODE_FENCE.finditer(text):
        # toggle fenced blocks; if pos between two fences, it's unsafe
        pass
    # quick checks
    if _in_region(_URL, text, pos):
        return False
    if _in_region(_LINK_MD, text, pos):
        return False
    # inline code heuristic: odd number of backticks from line start to pos
    line_start = text.rfind("\n", 0, pos) + 1
    segment = text[line_start:pos]
    if segment.count("`") % 2 == 1:
        return False
    # headings: do not insert on heading lines
    line = text[line_start: text.find("\n", pos) if text.find("\n", pos) != -1 else len(text)]
    if _HEADING.match(line):
        return False
    # naive html tag check
    if _in_region(_HTML_TAG, text, pos):
        return False
    return True


def expand_to_sentence(text: str, start_char: int, end_char: int, max_len: int = 300) -> Tuple[int, int]:
    # Find sentence boundaries around the span; fallback to paragraph ends
    s = max(0, start_char)
    e = min(len(text), end_char)
    # move left to previous sentence boundary or paragraph
    left = text.rfind("\n\n", 0, s)
    cand_left = left + 2 if left != -1 else 0
    # try punctuation based split
    p = s
    while p > max(0, s - 400):
        if text[p-1:p] in (".", "!", "?", "…"):
            cand_left = p
            break
        p -= 1
    # move right to next sentence boundary or paragraph
    right_para = text.find("\n\n", e)
    cand_right = right_para if right_para != -1 else len(text)
    p = e
    while p < min(len(text), e + 400):
        if text[p:p+1] in (".", "!", "?", "…"):
            cand_right = p + 1
            break
        p += 1
    # clamp to max_len
    S = max(0, cand_right - max_len)
    E = min(len(text), S + max_len)
    # ensure original span inside
    if S > s:
        S = cand_left
    if E < e:
        E = cand_right
    S = max(0, min(S, s))
    E = min(len(text), max(E, e))
    return S, E


def nearest_safe_insert_after(text: str, sent_end_char: int, search_window: int = 120) -> Optional[int]:
    pos = min(len(text), max(0, sent_end_char))
    # skip whitespace and repeated punctuation
    while pos < len(text) and text[pos] in (" ", "\t", ")", "]", ","):
        pos += 1
    end = min(len(text), pos + max(1, search_window))
    # prefer positions right after space or newline
    for p in range(pos, end):
        if text[p:p+1] in (" ", "\n") and is_safe_region(text, p):
            return p
    # fallback: next non-alnum boundary
    for p in range(pos, end):
        if not text[p:p+1].isalnum() and is_safe_region(text, p):
            return p
    return None
