"""
Enhancement planner using Map-Reduce pattern for generating candidates.
"""

import json
import hashlib
import asyncio
from typing import List, Dict, Any, Optional, Set
from dataclasses import dataclass, asdict
from datetime import datetime
import re
import sys
import os

from openai import AsyncOpenAI
from loguru import logger

from .config import EnhancementConfig
from .windowing import DocumentWindow
from ..core.rate_limiter import AsyncLeakyBucket


@dataclass
class EnhancementCandidate:
    """Represents a candidate enhancement."""
    cand_id: str
    type: str  # glossary, highlight, faq, caption
    title: str
    rationale: str
    source_unit_ids: List[str]
    pages: List[int]
    section: str
    dup_key: str
    priority: float
    confidence: float
    suggested_placement: str  # page or global
    window_id: str
    
    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


class EnhancementPlanner:
    """Plans enhancement candidates using window-based map-reduce."""
    
    def __init__(self, config: EnhancementConfig):
        self.config = config
        self.client = AsyncOpenAI(api_key=config.openai_api_key)
        self.rate_limiter = AsyncLeakyBucket(rps=config.requests_per_second)
        
        # Priority terms for domain-specific boosting
        self.priority_terms = set(config.priority_terms)
        
    async def plan_enhancements(
        self,
        doc_id: str,
        windows: List[DocumentWindow],
        units_metadata: List[Dict[str, Any]]
    ) -> Dict[str, Any]:
        """
        Plan enhancements for a document using map-reduce.
        
        Args:
            doc_id: Document identifier
            windows: List of document windows
            units_metadata: Complete units metadata
            
        Returns:
            Dictionary with planning results
        """
        logger.info(f"Starting enhancement planning for {doc_id} with {len(windows)} windows")
        
        # Map phase: plan candidates per window
        all_candidates = []
        
        for window in windows:
            try:
                window_candidates = await self._plan_window(
                    window=window,
                    units_metadata=units_metadata
                )
                all_candidates.extend(window_candidates)
                logger.info(f"Window {window.window_id}: {len(window_candidates)} candidates")
            except Exception as e:
                logger.error(f"Error planning window {window.window_id}: {e}")
                continue
        
        # Reduce phase: deduplicate and prioritize
        final_candidates = self._reduce_candidates(all_candidates)
        
        # Adaptive target based on windows and content richness
        # Aim more items for richer content, minimal capping
        per_window_base = 20  # Naikkan base untuk dokumen finansial
        try:
            avg_tokens = int(sum(w.total_tokens for w in windows) / max(1, len(windows)))
            if avg_tokens < 3000:
                per_window_base = 12  # Minimal untuk dokumen kecil
            elif avg_tokens < 8000:
                per_window_base = 20  # Standard untuk dokumen medium
            else:
                per_window_base = 30  # Maksimal untuk dokumen besar kaya informasi
        except Exception:
            per_window_base = 20
        
        # Hanya soft cap, prioritaskan kekayaan konten
        adaptive_target = min(self.config.target_items, per_window_base * max(1, len(windows)))
        adaptive_target = max(12, adaptive_target)  # Minimal lebih tinggi

        # Select top candidates for initial generation
        selected_candidates = self._select_top_candidates(
            final_candidates,
            target_count=adaptive_target
        )
        
        result = {
            "doc_id": doc_id,
            "run_id": f"plan_{datetime.utcnow().isoformat()}",
            "windows": [w.to_dict() for w in windows],
            "all_candidates": [c.to_dict() for c in all_candidates],
            "final_candidates": [c.to_dict() for c in final_candidates],
            "selected_candidates": [c.to_dict() for c in selected_candidates],
            "metrics": {
                "total_windows": len(windows),
                "total_candidates": len(all_candidates),
                "unique_candidates": len(final_candidates),
                "selected_count": len(selected_candidates)
            }
        }
        
        return result
    
    async def _plan_window(
        self,
        window: DocumentWindow,
        units_metadata: List[Dict[str, Any]]
    ) -> List[EnhancementCandidate]:
        """Plan candidates for a single window."""
        # Get whitelist of available source units
        whitelist = self._get_source_whitelist(window, units_metadata)
        
        # Prepare prompt
        prompt = self._build_planning_prompt(window, whitelist)
        logger.info(f"Planning prompt length: {len(prompt)} chars")
        logger.info(f"Window tokens: {window.total_tokens}, Pages: {window.pages}")
        logger.info(f"Whitelist size: {len(whitelist)} units")
        
        # Call LLM
        # Implement retry mechanism for LLM calls
        max_retries = 3
        for retry in range(max_retries):
            await self.rate_limiter.acquire()
            
            try:
                # Enforce strict JSON via Structured Outputs (json_schema)
                planner_schema = {
                    "type": "json_schema",
                    "json_schema": {
                        "name": "enhancement_candidates",
                        "schema": {
                            "$schema": "http://json-schema.org/draft-07/schema#",
                            "type": "object",
                            "properties": {
                                "candidates": {
                                    "type": "array",
                                    "maxItems": 20,
                                    "items": {
                                        "type": "object",
                                        "additionalProperties": False,
                                        "properties": {
                                            "type": {"type": "string", "enum": ["glossary", "highlight", "faq", "caption"]},
                                            "title": {"type": "string", "minLength": 3, "maxLength": 80, "pattern": "^[^\"\n\r]+$"},
                                            "rationale": {"type": "string", "minLength": 5, "maxLength": 240},
                                            "source_unit_ids": {"type": "array", "items": {"type": "string"}, "minItems": 1, "maxItems": 5},
                                            "priority": {"type": "number", "minimum": 0.0, "maximum": 1.0},
                                            "suggested_placement": {"type": "string", "enum": ["page", "global"]}
                                        },
                                        "required": ["type", "title", "rationale", "source_unit_ids", "priority", "suggested_placement"]
                                    }
                                }
                            },
                            "required": ["candidates"],
                            "additionalProperties": False
                        }
                    }
                }

                response = await self.client.chat.completions.create(
                    model=self.config.planner_model,
                    messages=[
                        {"role": "system", "content": self._get_planner_system_prompt()},
                        {"role": "user", "content": prompt}
                    ],
                    # Lower temperature to improve structure adherence
                    temperature=min(0.2, getattr(self.config, "openai_temperature", 0.2)),
                    max_tokens=3000,
                    response_format=planner_schema
                )
                
                # Debug: Check if Structured Outputs is working
                content = response.choices[0].message.content
                logger.info(f"Raw LLM Response (attempt {retry + 1}): {content}")
                logger.info(f"Response finish_reason: {response.choices[0].finish_reason}")
                
                try:
                    data = json.loads(content)
                    logger.info("JSON parsing successful with Structured Outputs")
                except json.JSONDecodeError as parse_error:
                    logger.error(f"CRITICAL: Structured Outputs failed! JSON Error: {parse_error}")
                    logger.error(f"Model used: {self.config.planner_model}")
                    logger.error(f"Content that failed: {content}")
                    
                    # Emergency fallback: try without structured outputs
                    logger.warning("Attempting fallback without Structured Outputs...")
                    fallback_response = await self.client.chat.completions.create(
                        model=self.config.planner_model,
                        messages=[
                            {"role": "system", "content": self._get_planner_system_prompt()},
                            {"role": "user", "content": prompt}
                        ],
                        temperature=0.1,
                        max_tokens=2000,
                        response_format={"type": "json_object"}  # Basic JSON mode
                    )
                    
                    fallback_content = fallback_response.choices[0].message.content
                    logger.info(f"Fallback response: {fallback_content}")
                    
                    try:
                        data = json.loads(fallback_content)
                        logger.info("Fallback JSON parsing successful")
                    except json.JSONDecodeError:
                        logger.error("Both Structured Outputs and fallback failed")
                        raise parse_error
                
                # Success - break retry loop
                break
                
            except Exception as e:
                logger.error(f"LLM call failed (attempt {retry + 1}): {e}")
                if retry < max_retries - 1:
                    logger.info(f"Retrying LLM call... (attempt {retry + 2})")
                    continue
                else:
                    logger.error("All LLM retry attempts failed")
                    return []
        
        # Validate and create candidates
        candidates = []
        for item in data.get("candidates", []):
            candidate = self._validate_and_create_candidate(
                item, whitelist, window.window_id
            )
            if candidate:
                candidates.append(candidate)
        
        return candidates
    
    def _get_source_whitelist(
        self,
        window: DocumentWindow,
        units_metadata: List[Dict[str, Any]]
    ) -> List[Dict[str, Any]]:
        """Get whitelist of source units for this window."""
        whitelist = []
        
        # Units in this window
        window_unit_ids = set(window.unit_ids)
        
        # Additional relevant units from whole document (via simple keyword matching)
        relevant_units = self._find_relevant_units(window, units_metadata)
        
        # Combine and limit
        all_unit_ids = window_unit_ids | set(relevant_units)
        
        for unit in units_metadata:
            if unit.get('unit_id') in all_unit_ids:
                # Add summary for each unit
                content = unit.get('content', '')
                summary = content[:100] + '...' if len(content) > 100 else content
                
                whitelist.append({
                    'unit_id': unit.get('unit_id'),
                    'page': unit.get('page'),
                    'type': unit.get('unit_type'),
                    'summary': summary
                })
        
        # Limit to reasonable size (smaller to keep prompt compact)
        return whitelist[:25]
    
    def _find_relevant_units(
        self,
        window: DocumentWindow,
        units_metadata: List[Dict[str, Any]],
        max_units: int = 20
    ) -> List[str]:
        """Find relevant units from whole document using keyword matching."""
        # Extract key terms from window text
        window_text = window.text_preview.lower()
        
        # Find priority terms in window
        found_terms = [term for term in self.priority_terms if term.lower() in window_text]
        
        if not found_terms:
            return []
        
        # Score units by relevance
        unit_scores = {}
        for unit in units_metadata:
            if unit.get('unit_id') in window.unit_ids:
                continue  # Skip units already in window
            
            content = unit.get('content', '').lower()
            score = 0
            
            for term in found_terms:
                if term.lower() in content:
                    score += 1
            
            if score > 0:
                unit_scores[unit.get('unit_id')] = score
        
        # Return top scored units
        sorted_units = sorted(unit_scores.items(), key=lambda x: x[1], reverse=True)
        return [unit_id for unit_id, _ in sorted_units[:max_units]]
    
    def _build_planning_prompt(
        self,
        window: DocumentWindow,
        whitelist: List[Dict[str, Any]]
    ) -> str:
        """Build prompt for planning candidates."""
        sys.path.append(os.path.join(os.path.dirname(__file__), '..', '..'))
        from prompts.enhancement_planner import USER_PROMPT_TEMPLATE
        
        # Format whitelist
        unit_metadata = []
        for item in whitelist:
            unit_metadata.append(
                f"- {item['unit_id']} (Halaman {item['page']}, {item['type']}): {item['summary']}"
            )
        return USER_PROMPT_TEMPLATE.format(
            window_id=window.window_id,
            pages=f"{min(window.pages)}-{max(window.pages)}",
            token_count=window.total_tokens,
            content=window.text_preview,
            unit_metadata=chr(10).join(unit_metadata)
        )
    
    def _get_planner_system_prompt(self) -> str:
        """Get system prompt for planner."""
        import sys
        import os
        sys.path.append(os.path.join(os.path.dirname(__file__), '..', '..'))
        from prompts.enhancement_planner import SYSTEM_PROMPT
        return SYSTEM_PROMPT
    
    def _validate_and_create_candidate(
        self,
        item: Dict[str, Any],
        whitelist: List[Dict[str, Any]],
        window_id: str
    ) -> Optional[EnhancementCandidate]:
        """Validate and create an enhancement candidate."""
        # Check required fields
        required = ['type', 'title', 'source_unit_ids']
        if not all(field in item for field in required):
            return None
        
        # Validate source_unit_ids against whitelist
        whitelist_ids = {w['unit_id'] for w in whitelist}
        source_ids = item.get('source_unit_ids', [])
        
        if not source_ids or not all(uid in whitelist_ids for uid in source_ids):
            logger.warning(f"Invalid source_unit_ids: {source_ids}")
            return None
        
        # Get pages from source units
        pages = []
        for unit in whitelist:
            if unit['unit_id'] in source_ids:
                pages.append(unit['page'])
        pages = sorted(set(pages))
        
        # Generate candidate ID and deduplication key
        cand_type = item.get('type', 'unknown')
        title = item.get('title', 'Untitled')
        cand_id = f"cand_{cand_type}_{hashlib.md5(title.encode()).hexdigest()[:8]}"
        
        # Create deduplication key
        if cand_type == 'glossary':
            dup_key = f"TERM:{title.upper()}"
        elif cand_type == 'faq':
            dup_key = f"FAQ:{title}"
        else:
            dup_key = f"{cand_type.upper()}:{':'.join(source_ids)}"
        
        # Determine placement suggestion
        suggested_placement = 'page' if len(pages) == 1 else 'global'
        
        return EnhancementCandidate(
            cand_id=cand_id,
            type=cand_type,
            title=title,
            rationale=item.get('rationale', ''),
            source_unit_ids=source_ids,
            pages=pages,
            section=item.get('section', 'General'),
            dup_key=dup_key,
            priority=item.get('priority_score', 0.5),
            confidence=0.8,  # Default confidence
            suggested_placement=suggested_placement,
            window_id=window_id
        )
    
    def _reduce_candidates(
        self,
        candidates: List[EnhancementCandidate]
    ) -> List[EnhancementCandidate]:
        """Deduplicate and merge candidates."""
        # Group by deduplication key
        dup_groups = {}
        for candidate in candidates:
            if candidate.dup_key not in dup_groups:
                dup_groups[candidate.dup_key] = []
            dup_groups[candidate.dup_key].append(candidate)
        
        # Merge duplicates
        final_candidates = []
        for dup_key, group in dup_groups.items():
            if len(group) == 1:
                final_candidates.append(group[0])
            else:
                # Merge: combine source_unit_ids and pages, average priority
                merged = group[0]
                for other in group[1:]:
                    merged.source_unit_ids = list(set(merged.source_unit_ids + other.source_unit_ids))
                    merged.pages = sorted(set(merged.pages + other.pages))
                    merged.priority = (merged.priority + other.priority) / 2
                
                # Update placement suggestion
                merged.suggested_placement = 'page' if len(merged.pages) == 1 else 'global'
                final_candidates.append(merged)
        
        return final_candidates
    
    
    def _select_top_candidates(
        self,
        candidates: List[EnhancementCandidate],
        target_count: int
    ) -> List[EnhancementCandidate]:
        """Select top candidates for generation."""
        # Score candidates
        scored = []
        for candidate in candidates:
            score = self._score_candidate(candidate)
            scored.append((score, candidate))
        
        # Sort by score
        scored.sort(key=lambda x: x[0], reverse=True)
        
        # Select top N
        selected = [candidate for _, candidate in scored[:target_count]]
        
        # Ensure diversity, but prioritize comprehensive coverage
        type_counts = {}
        capped = []
        max_per_type = max(5, target_count // 3 * 2)  # Much softer cap untuk lebih banyak item per type
        for candidate in selected:
            ctype = candidate.type
            count = type_counts.get(ctype, 0)
            if count < max_per_type:
                capped.append(candidate)
                type_counts[ctype] = count + 1
            if len(capped) >= target_count:
                break
        # Fill if still under target
        if len(capped) < target_count:
            seen_ids = {c.cand_id for c in capped}
            for _, cand in scored:
                if cand.cand_id in seen_ids:
                    continue
                capped.append(cand)
                if len(capped) >= target_count:
                    break
        return capped
    
    def _score_candidate(self, candidate: EnhancementCandidate) -> float:
        """Score a candidate for prioritization."""
        score = candidate.priority
        
        # Boost for priority terms
        title_lower = candidate.title.lower()
        for term in self.priority_terms:
            if term.lower() in title_lower:
                score += 0.1
        
        # Boost for certain types
        type_boost = {
            'glossary': 0.15,  # Important for understanding
            'highlight': 0.10,  # Key insights
            'faq': 0.05,
            'caption': 0.0
        }
        score += type_boost.get(candidate.type, 0)
        
        # Boost for multi-page references (likely important)
        if len(candidate.pages) > 1:
            score += 0.05
        
        # Normalize
        return min(score, 1.0)
