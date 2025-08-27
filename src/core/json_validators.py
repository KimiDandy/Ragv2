from typing import Any, Tuple, List

ALLOWED_TERM_KEYS = {"term", "name", "original_context", "context", "provenances", "confidence_score"}
ALLOWED_CONCEPT_KEYS = {"identifier", "id", "name", "original_context", "paragraph_text", "provenances", "confidence_score"}


def _is_scalar_string(x: Any) -> bool:
    return isinstance(x, str) and len(x) <= 20000


def validate_enrichment_plan(plan: Any) -> Tuple[bool, List[str]]:
    errs: List[str] = []
    if not isinstance(plan, dict):
        return False, ["plan must be an object"]

    terms = plan.get("terms_to_define", []) or []
    concepts = plan.get("concepts_to_simplify", []) or []

    if not isinstance(terms, list):
        errs.append("terms_to_define must be a list")
    if not isinstance(concepts, list):
        errs.append("concepts_to_simplify must be a list")

    if isinstance(terms, list):
        for i, it in enumerate(terms):
            if isinstance(it, str):
                continue
            if not isinstance(it, dict):
                errs.append(f"terms_to_define[{i}] must be string or object")
                continue
            extra = set(it.keys()) - ALLOWED_TERM_KEYS
            if extra:
                errs.append(f"terms_to_define[{i}] has unsupported keys: {sorted(extra)}")
            # basic required fields presence
            if not (it.get("term") or it.get("name")):
                errs.append(f"terms_to_define[{i}] missing 'term'|'name'")
            if it.get("original_context") and not _is_scalar_string(it.get("original_context")):
                errs.append(f"terms_to_define[{i}].original_context too long or not string")

    if isinstance(concepts, list):
        for i, it in enumerate(concepts):
            if isinstance(it, str):
                continue
            if not isinstance(it, dict):
                errs.append(f"concepts_to_simplify[{i}] must be string or object")
                continue
            extra = set(it.keys()) - ALLOWED_CONCEPT_KEYS
            if extra:
                errs.append(f"concepts_to_simplify[{i}] has unsupported keys: {sorted(extra)}")
            if not (it.get("identifier") or it.get("id") or it.get("name")):
                errs.append(f"concepts_to_simplify[{i}] missing 'identifier'|'id'|'name'")
            if it.get("original_context") and not _is_scalar_string(it.get("original_context")):
                errs.append(f"concepts_to_simplify[{i}].original_context too long or not string")

    return (len(errs) == 0), errs


def validate_suggestions(suggestions: Any) -> Tuple[bool, List[str]]:
    errs: List[str] = []
    if not isinstance(suggestions, list):
        return False, ["suggestions must be a list"]

    for i, s in enumerate(suggestions):
        if not isinstance(s, dict):
            errs.append(f"suggestions[{i}] must be object")
            continue
        if not _is_scalar_string(s.get("id", "")):
            errs.append(f"suggestions[{i}].id missing or invalid")
        if s.get("type") not in {"term_to_define", "concept_to_simplify"}:
            errs.append(f"suggestions[{i}].type invalid")
        if not _is_scalar_string(s.get("original_context", "")):
            errs.append(f"suggestions[{i}].original_context missing or invalid")
        if not _is_scalar_string(s.get("generated_content", "")):
            errs.append(f"suggestions[{i}].generated_content missing or invalid")
        cs = s.get("confidence_score", 0.5)
        try:
            _ = float(cs)
        except Exception:
            errs.append(f"suggestions[{i}].confidence_score must be numeric")
        st = s.get("status")
        if st is not None and not _is_scalar_string(st):
            errs.append(f"suggestions[{i}].status invalid")

    return (len(errs) == 0), errs
