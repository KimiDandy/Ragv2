"""
Enhancement Planner V2 - Context-Aware Planning untuk Financial Domain
Fokus pada ekstraksi informasi tersirat dengan referensi data yang presisi
"""

import json
import hashlib
import asyncio
from typing import List, Dict, Any, Optional, Set, Tuple
from dataclasses import dataclass, asdict
from datetime import datetime
import re

from openai import AsyncOpenAI
from loguru import logger
import tiktoken

from .config import EnhancementConfig
from .windowing import DocumentWindow
from .enhancement_types_universal import (
    UniversalEnhancementType as EnhancementType,
    AdaptiveEnhancementCandidate as EnhancementCandidate,
    DocumentProfile,
    EnhancementSelector
)
from ..core.rate_limiter import AsyncLeakyBucket
from .prompts_universal import UNIVERSAL_PLANNING_SYSTEM as SYSTEM_PROMPT, UNIVERSAL_PLANNING_USER as USER_PROMPT


class ContextExtractor:
    """Extract precise context from document for enhancement"""
    
    @staticmethod
    def extract_tables(units_metadata: List[Dict]) -> List[Dict[str, Any]]:
        """Extract all tables with their data"""
        tables = []
        for unit in units_metadata:
            if unit.get('unit_type') == 'table':
                table_data = {
                    'unit_id': unit.get('unit_id'),
                    'page': unit.get('page'),
                    'content': unit.get('content', ''),
                    'parsed_data': ContextExtractor._parse_table(unit.get('content', ''))
                }
                tables.append(table_data)
        return tables
    
    @staticmethod
    def _parse_table(content: str) -> Dict[str, Any]:
        """Parse markdown table into structured data"""
        lines = content.strip().split('\n')
        if len(lines) < 3:  # Need header, separator, at least one row
            return {}
        
        # Extract headers
        headers = [h.strip() for h in lines[0].split('|') if h.strip()]
        
        # Extract rows (skip separator line)
        rows = []
        for line in lines[2:]:
            if '|' in line:
                row = [cell.strip() for cell in line.split('|') if cell.strip()]
                if len(row) == len(headers):
                    rows.append(dict(zip(headers, row)))
        
        return {
            'headers': headers,
            'rows': rows,
            'row_count': len(rows)
        }
    
    @staticmethod
    def extract_numerical_patterns(content: str) -> List[Dict[str, Any]]:
        """Extract numerical patterns for formula discovery"""
        patterns = []
        
        # Currency amounts
        currency_pattern = r'(?:Rp|IDR|USD)\s*([\d,.]+)(?:\s*(?:ribu|juta|miliar|billion))?'
        for match in re.finditer(currency_pattern, content):
            patterns.append({
                'type': 'currency',
                'value': match.group(0),
                'position': match.span()
            })
        
        # Percentages
        percentage_pattern = r'(\d+(?:\.\d+)?)\s*%'
        for match in re.finditer(percentage_pattern, content):
            patterns.append({
                'type': 'percentage',
                'value': match.group(0),
                'position': match.span()
            })
        
        # Time periods
        period_pattern = r'(\d+)\s*(tahun|bulan|hari|minggu)'
        for match in re.finditer(period_pattern, content):
            patterns.append({
                'type': 'period',
                'value': match.group(0),
                'position': match.span()
            })
        
        # Ages
        age_pattern = r'(?:usia|umur)\s*(\d+)'
        for match in re.finditer(age_pattern, content):
            patterns.append({
                'type': 'age',
                'value': match.group(0),
                'position': match.span()
            })
        
        return patterns
    
    @staticmethod
    def find_calculation_examples(content: str) -> List[Dict[str, str]]:
        """Find calculation examples that might reveal formulas"""
        calc_keywords = [
            'contoh perhitungan', 'ilustrasi', 'simulasi', 'proyeksi',
            'misalnya', 'sebagai contoh', 'dengan asumsi'
        ]
        
        examples = []
        lines = content.split('\n')
        
        for i, line in enumerate(lines):
            line_lower = line.lower()
            for keyword in calc_keywords:
                if keyword in line_lower:
                    # Extract context around the keyword
                    start_idx = max(0, i - 2)
                    end_idx = min(len(lines), i + 10)
                    context = '\n'.join(lines[start_idx:end_idx])
                    
                    examples.append({
                        'keyword': keyword,
                        'line': line,
                        'context': context,
                        'line_number': i
                    })
        
        return examples


class EnhancementPlannerV2:
    """
    Context-aware planner for financial document enhancement
    """
    
    def __init__(self, config: EnhancementConfig):
        self.config = config
        self.client = AsyncOpenAI(api_key=config.openai_api_key)
        self.rate_limiter = AsyncLeakyBucket(rps=config.requests_per_second)
        self.context_extractor = ContextExtractor()
        
        # Token counting
        self.encoder = tiktoken.encoding_for_model("gpt-4")
    
    async def plan_enhancements(
        self,
        doc_id: str,
        windows: List[DocumentWindow],
        units_metadata: List[Dict[str, Any]]
    ) -> Tuple[List[EnhancementCandidate], Dict[str, Any]]:
        """
        Plan enhancements with context-aware analysis
        
        Returns:
            Tuple of (candidates, planning_metrics)
        """
        logger.info(f"Planning enhancements for {doc_id} with {len(windows)} windows")
        
        # Extract global context
        all_tables = self.context_extractor.extract_tables(units_metadata)
        logger.info(f"Found {len(all_tables)} tables in document")
        
        # Process each window
        all_candidates = []
        window_tasks = []
        
        for i, window in enumerate(windows):
            # Extract window-specific context
            window_context = self._prepare_window_context(window, units_metadata, all_tables)
            
            # Create planning task
            task = self._plan_window(
                window=window,
                window_number=i + 1,
                total_windows=len(windows),
                context=window_context,
                doc_id=doc_id
            )
            window_tasks.append(task)
        
        # Execute planning (sequential for now as per user request)
        for i, task in enumerate(window_tasks):
            logger.info(f"Processing window {i + 1}/{len(window_tasks)}")
            candidates = await task
            all_candidates.extend(candidates)
            logger.info(f"Window {i + 1} generated {len(candidates)} candidates")
        
        # Deduplicate and prioritize
        final_candidates = self._deduplicate_candidates(all_candidates)
        final_candidates = self._prioritize_candidates(final_candidates)
        
        # Prepare metrics
        metrics = {
            'total_windows': len(windows),
            'total_candidates_raw': len(all_candidates),
            'total_candidates_final': len(final_candidates),
            'candidates_by_type': self._count_by_type(final_candidates),
            'average_confidence': sum(c.confidence for c in final_candidates) / len(final_candidates) if final_candidates else 0
        }
        
        logger.info(f"Planning complete: {len(final_candidates)} final candidates")
        
        return final_candidates, metrics
    
    def _prepare_window_context(
        self,
        window: DocumentWindow,
        units_metadata: List[Dict[str, Any]],
        all_tables: List[Dict[str, Any]]
    ) -> Dict[str, Any]:
        """Prepare comprehensive context for window"""
        
        # Find units in this window
        window_units = []
        for unit in units_metadata:
            unit_id = unit.get('unit_id', '')
            if unit_id in window.unit_ids:
                window_units.append(unit)
        
        # Extract patterns from window content
        numerical_patterns = self.context_extractor.extract_numerical_patterns(window.text_preview)
        calculation_examples = self.context_extractor.find_calculation_examples(window.text_preview)
        
        # Find relevant tables for this window
        relevant_tables = [
            table for table in all_tables
            if table['unit_id'] in window.unit_ids
        ]
        
        return {
            'window_units': window_units,
            'tables': relevant_tables,
            'numerical_patterns': numerical_patterns,
            'calculation_examples': calculation_examples,
            'content_length': len(window.text_preview),
            'token_count': window.total_tokens
        }
    
    async def _plan_window(
        self,
        window: DocumentWindow,
        window_number: int,
        total_windows: int,
        context: Dict[str, Any],
        doc_id: str
    ) -> List[EnhancementCandidate]:
        """Plan enhancements for a single window"""
        
        # Prepare prompt with limited content to prevent overwhelm
        limited_content = window.text_preview[:20000]  # Reduced from 50k to 20k
        limited_metadata = json.dumps(context['window_units'][:30], ensure_ascii=False, indent=1)[:8000]  # Reduced and limit units
        
        user_prompt = USER_PROMPT.format(
            window_number=window_number,
            total_windows=total_windows,
            window_content=limited_content,
            units_metadata=limited_metadata
        )
        
        # Count tokens
        prompt_tokens = len(self.encoder.encode(SYSTEM_PROMPT + user_prompt))
        window_content_tokens = window.total_tokens
        logger.info(f"Window {window_number} prompt tokens: {prompt_tokens}")
        logger.info(f"Window {window_number} content tokens: {window_content_tokens}")
        logger.info(f"Window {window_number} has {len(context['window_units'])} units, {len(context['tables'])} tables")
        
        # Call LLM with retry
        max_retries = 3
        for attempt in range(max_retries):
            try:
                await self.rate_limiter.acquire()
                response = await self.client.chat.completions.create(
                    model=self.config.planner_model,
                    messages=[
                        {"role": "system", "content": SYSTEM_PROMPT},
                        {"role": "user", "content": user_prompt}
                    ],
                    temperature=0.1,  # Reduced for consistency
                    max_tokens=2500,  # Reduced to prevent truncation
                    response_format={"type": "json_object"},
                    seed=42  # For consistency
                )
                
                # Parse response
                content = response.choices[0].message.content
                logger.info(f"Window {window_number} raw response length: {len(content)} chars")
                if len(content) < 100:
                    logger.warning(f"Window {window_number} suspiciously short response: {content}")
                
                result = self._parse_planning_response(content)
                
                # Debug: Log parsed result structure
                if result:
                    logger.info(f"Window {window_number} parsed result keys: {list(result.keys())}")
                    if 'candidates' in result:
                        candidates_list = result['candidates']
                        if isinstance(candidates_list, list):
                            logger.info(f"Window {window_number} candidates count: {len(candidates_list)}")
                            if len(candidates_list) == 0:
                                logger.warning(f"Window {window_number} has empty candidates list")
                        else:
                            logger.error(f"Window {window_number} 'candidates' is not a list: {type(candidates_list)}")
                            raise ValueError("'candidates' must be a list")
                    else:
                        logger.warning(f"Window {window_number} missing 'candidates' key. Available keys: {list(result.keys())}")
                        logger.warning(f"Window {window_number} result content preview: {str(result)[:500]}...")
                        # Try to fix missing candidates
                        result['candidates'] = []
                
                if not result:
                    raise ValueError("No result from JSON parsing")
                
                # Ensure candidates exists and is a list  
                if 'candidates' not in result:
                    logger.warning(f"Window {window_number} adding missing 'candidates' key")
                    result['candidates'] = []
                elif not isinstance(result['candidates'], list):
                    logger.error(f"Window {window_number} 'candidates' is not a list, converting")
                    result['candidates'] = []
                
                # Convert to EnhancementCandidate objects
                candidates = []
                for i, cand_data in enumerate(result['candidates']):
                    if not isinstance(cand_data, dict):
                        logger.warning(f"Window {window_number} candidate {i} is not a dict: {type(cand_data)}")
                        continue
                    
                    candidate = self._create_candidate(cand_data, window, doc_id)
                    if candidate:
                        candidates.append(candidate)
                    else:
                        logger.warning(f"Window {window_number} failed to create candidate {i}: {cand_data.get('title', 'Unknown')}")
                
                logger.info(f"Window {window_number} planning successful: {len(candidates)} candidates")
                return candidates
                
            except Exception as e:
                logger.error(f"Planning attempt {attempt + 1} failed: {e}")
                if attempt == max_retries - 1:
                    logger.error(f"All planning attempts failed for window {window_number}")
                    return []
                await asyncio.sleep(2 ** attempt)
        
        return []
    
    def _parse_planning_response(self, content: str) -> Optional[Dict]:
        """Parse LLM response with robust error handling"""
        try:
            # Direct parsing
            return json.loads(content)
        except json.JSONDecodeError as e:
            logger.warning(f"Initial parse failed: {e}")
            
            # Try multiple strategies to fix JSON
            strategies = [
                # Remove markdown blocks
                lambda x: re.sub(r'^```(?:json)?\s*|\s*```$', '', x, flags=re.MULTILINE),
                # Fix trailing commas
                lambda x: re.sub(r',\s*([}\]])', r'\1', x),
                # Remove comments and extra text
                lambda x: re.sub(r'//[^\n]*', '', x),
                # Fix unterminated strings by finding incomplete quotes
                lambda x: self._fix_unterminated_strings(x),
                # Extract clean JSON object
                lambda x: self._extract_clean_json(x),
                # Try to complete truncated JSON
                lambda x: self._complete_truncated_json(x)
            ]
            
            cleaned = content
            for strategy in strategies:
                try:
                    cleaned = strategy(cleaned)
                    result = json.loads(cleaned)
                    logger.info("JSON parsing succeeded after cleaning")
                    return result
                except:
                    continue
            
            # Last resort: try to extract just candidates array if exists
            candidates_match = re.search(r'"candidates"\s*:\s*\[(.*?)\]', content, re.DOTALL)
            if candidates_match:
                try:
                    candidates_json = f'{{"candidates": [{candidates_match.group(1)}]}}'
                    result = json.loads(candidates_json)
                    logger.info("Extracted candidates array successfully")
                    return {
                        "document_profile": {
                            "dominant_type": "mixed",
                            "detected_patterns": ["extracted"],
                            "complexity": "medium"
                        },
                        "candidates": result.get("candidates", [])
                    }
                except:
                    pass
            
            # Final fallback: return minimal valid structure
            logger.error(f"All parsing strategies failed. Content preview: {content[:300]}...")
            return {
                "document_profile": {
                    "dominant_type": "unknown",
                    "detected_patterns": [],
                    "complexity": "medium"
                },
                "candidates": []
            }
    
    def _fix_unterminated_strings(self, content: str) -> str:
        """Fix unterminated string literals in JSON"""
        lines = content.split('\n')
        fixed_lines = []
        in_string = False
        
        for i, line in enumerate(lines):
            # Track if we're inside a string value
            quote_positions = []
            for j, char in enumerate(line):
                if char == '"' and (j == 0 or line[j-1] != '\\'):
                    quote_positions.append(j)
            
            # If odd number of quotes, we have unterminated string
            if len(quote_positions) % 2 == 1:
                # Check if line ends abruptly
                if line.strip().endswith(('",', '"', '}', ']')):
                    fixed_lines.append(line)
                else:
                    # Add closing quote before any JSON syntax
                    if re.search(r'[,}\]]$', line.strip()):
                        line = re.sub(r'([^",}\]]*?)([,}\]]*)$', r'\1"\2', line.rstrip())
                    else:
                        line = line.rstrip() + '"'
                    fixed_lines.append(line)
            else:
                fixed_lines.append(line)
        
        return '\n'.join(fixed_lines)
    
    def _extract_clean_json(self, content: str) -> str:
        """Extract clean JSON object from mixed content"""
        # Find the main JSON object
        start = content.find('{')
        if start == -1:
            return content
        
        # Find matching closing brace
        brace_count = 0
        end = start
        
        for i, char in enumerate(content[start:], start):
            if char == '{':
                brace_count += 1
            elif char == '}':
                brace_count -= 1
                if brace_count == 0:
                    end = i
                    break
        
        return content[start:end+1]
    
    def _complete_truncated_json(self, content: str) -> str:
        """Complete truncated JSON by adding missing closing braces/brackets"""
        content = content.strip()
        
        # Count open braces/brackets
        open_braces = content.count('{') - content.count('}')
        open_brackets = content.count('[') - content.count(']')
        
        # Add missing closures
        for _ in range(open_brackets):
            content += ']'
        for _ in range(open_braces):
            content += '}'
        
        return content
    
    def _create_candidate(
        self,
        cand_data: Dict[str, Any],
        window: DocumentWindow,
        doc_id: str
    ) -> Optional[EnhancementCandidate]:
        """Create EnhancementCandidate from raw data with robust validation"""
        try:
            # Map enhancement type
            type_str = cand_data.get('enhancement_type', 'formula_discovery')
            try:
                enhancement_type = EnhancementType(type_str)
            except:
                enhancement_type = EnhancementType.FORMULA_DISCOVERY
            
            # Clean and validate source_references
            source_refs = cand_data.get('source_references', [])
            cleaned_refs = []
            
            if isinstance(source_refs, list):
                for ref in source_refs:
                    if isinstance(ref, dict):
                        cleaned_refs.append(ref)
                    elif isinstance(ref, str):
                        # Convert string to dict format
                        cleaned_refs.append({
                            "reference": ref,
                            "type": "text"
                        })
                    else:
                        # Skip invalid references
                        continue
            elif isinstance(source_refs, str):
                # Single string reference
                cleaned_refs = [{
                    "reference": source_refs,
                    "type": "text"
                }]
            
            # Ensure we have at least one reference
            if not cleaned_refs:
                cleaned_refs = [{
                    "reference": f"Window {window.window_index + 1}",
                    "type": "window"
                }]
            
            # Clean required_context
            required_ctx = cand_data.get('required_context', {})
            if not isinstance(required_ctx, dict):
                required_ctx = {}
            
            return EnhancementCandidate(
                enhancement_type=enhancement_type,
                title=cand_data.get('title', 'Unknown Enhancement'),
                target_info=cand_data.get('target_info', ''),
                rationale=cand_data.get('rationale', ''),
                source_references=cleaned_refs,
                required_context=required_ctx,
                priority=int(cand_data.get('priority', 5)),
                confidence=float(cand_data.get('confidence', 0.8)),
                applicability=cand_data.get('applicability', [])
            )
            
        except Exception as e:
            logger.error(f"Failed to create candidate: {e}")
            logger.error(f"Candidate data: {cand_data}")
            return None
    
    def _deduplicate_candidates(
        self,
        candidates: List[EnhancementCandidate]
    ) -> List[EnhancementCandidate]:
        """Remove duplicate candidates based on content similarity"""
        seen = set()
        unique = []
        
        for candidate in candidates:
            # Create dedup key based on type and title
            dup_key = f"{candidate.enhancement_type}_{candidate.title.lower()}"
            
            if dup_key not in seen:
                seen.add(dup_key)
                unique.append(candidate)
            else:
                # If duplicate, keep the one with higher confidence
                for i, existing in enumerate(unique):
                    existing_key = f"{existing.enhancement_type}_{existing.title.lower()}"
                    if existing_key == dup_key and candidate.confidence > existing.confidence:
                        unique[i] = candidate
                        break
        
        return unique
    
    def _prioritize_candidates(
        self,
        candidates: List[EnhancementCandidate]
    ) -> List[EnhancementCandidate]:
        """Prioritize candidates based on type and confidence"""
        
        for candidate in candidates:
            # Get base priority from enhancement type using EnhancementSelector
            # High priority for implicit analysis types
            if candidate.enhancement_type in [
                EnhancementType.FORMULA_DISCOVERY,
                EnhancementType.PROJECTION_ANALYSIS,
                EnhancementType.IMPLICATION_ANALYSIS,
                EnhancementType.PROCESS_COMPLETION
            ]:
                type_priority = 10
            elif candidate.enhancement_type in [
                EnhancementType.PATTERN_EXTRACTION,
                EnhancementType.RELATIONSHIP_MAPPING,
                EnhancementType.SCENARIO_MODELING
            ]:
                type_priority = 8
            else:
                type_priority = 6
            
            # Adjust by confidence
            candidate.priority = type_priority * candidate.confidence
        
        # Sort by priority (highest first)
        candidates.sort(key=lambda x: x.priority, reverse=True)
        
        return candidates
    
    def _count_by_type(self, candidates: List[EnhancementCandidate]) -> Dict[str, int]:
        """Count candidates by enhancement type"""
        counts = {}
        for candidate in candidates:
            type_name = candidate.enhancement_type.value
            counts[type_name] = counts.get(type_name, 0) + 1
        return counts
