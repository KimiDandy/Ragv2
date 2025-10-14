"""
Document Enhancement System

Production-ready single-step enhancement with professional implementation,
proper windowing, parallel processing, and robust error handling.
"""

import asyncio
import json
import re
import logging
import os
import sys
import time
import hashlib
from typing import List, Dict, Any, Optional, Tuple, Set
from datetime import datetime
from pathlib import Path
import tiktoken
from dataclasses import dataclass, asdict

from openai import AsyncOpenAI
from pydantic import BaseModel, Field
from loguru import logger
from typing_extensions import Literal

from .config import EnhancementConfig
from .models import UniversalEnhancement
from .type_registry import get_type_registry, EnhancementTypeRegistry
from ..prompts.enhancement import (
    DIRECT_ENHANCEMENT_SYSTEM_PROMPT,
    DIRECT_ENHANCEMENT_USER_PROMPT
)
from ..core.rate_limiter import AsyncLeakyBucket


# Pydantic models for Structured Outputs (OpenAI guaranteed JSON)
class EnhancementItem(BaseModel):
    """Single enhancement with strict schema for OpenAI Structured Outputs
    
    NOTE: enhancement_id is NOT required from LLM - backend will generate it!
    This reduces cognitive load on LLM and ensures truly unique IDs.
    """
    type: str = Field(description="Enhancement type: formula_discovery, implication_analysis, pattern_recognition, etc.")
    title: str = Field(description="Clear, descriptive title in Indonesian")
    content: str = Field(description="Detailed enhancement content in Indonesian", min_length=100)
    source_units: list[str] = Field(description="List of source unit IDs referenced", default_factory=list)
    confidence: float = Field(description="Confidence score 0.0-1.0", ge=0.0, le=1.0, default=0.8)
    priority: int = Field(description="Priority level 1-10, higher is more important", ge=1, le=10, default=5)

class EnhancementResponse(BaseModel):
    """Response schema for structured outputs - OpenAI will guarantee this format"""
    enhancements: list[EnhancementItem] = Field(description="List of enhancements")
    metadata: dict = Field(default_factory=dict, description="Optional metadata")

@dataclass
class EnhancementWindow:
    """Professional window structure for enhancement processing"""
    window_id: str
    window_number: int
    total_windows: int
    content: str
    units: List[Dict[str, Any]]
    tables: List[Dict[str, Any]]
    numerical_patterns: List[Dict[str, Any]]
    token_count: int
    metadata: Dict[str, Any]
    
    def get_context_hash(self) -> str:
        """Generate unique hash for caching"""
        content_hash = hashlib.md5(self.content.encode()).hexdigest()
        return f"window_{self.window_id}_{content_hash[:8]}"


class WindowAnalyzer:
    """Analyzes window content to determine enhancement strategy"""
    
    @staticmethod
    def analyze_content_type(window: EnhancementWindow) -> Dict[str, Any]:
        """Analyze window content characteristics for adaptive enhancement"""
        analysis = {
            'has_tables': len(window.tables) > 0,
            'has_numerical': len(window.numerical_patterns) > 0,
            'table_count': len(window.tables),
            'number_count': len(window.numerical_patterns),
            'content_density': len(window.content) / window.token_count if window.token_count > 0 else 0,
            'dominant_type': 'general'
        }
        
        # Determine dominant content type
        if analysis['table_count'] >= 2 or analysis['number_count'] >= 10:
            analysis['dominant_type'] = 'financial'
        elif 'pasal' in window.content.lower() or 'ayat' in window.content.lower():
            analysis['dominant_type'] = 'legal'
        elif 'langkah' in window.content.lower() or 'prosedur' in window.content.lower():
            analysis['dominant_type'] = 'procedural'
        
        return analysis
    
    @staticmethod
    def extract_tables_from_units(units: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """Extract and parse tables from window units"""
        tables = []
        for unit in units:
            if unit.get('type') == 'table' or unit.get('unit_type') == 'table':
                table_content = unit.get('content', '')
                parsed = WindowAnalyzer._parse_markdown_table(table_content)
                if parsed:
                    tables.append({
                        'unit_id': unit.get('unit_id', f"table_{len(tables)}"),
                        'headers': parsed['headers'],
                        'rows': parsed['rows'],
                        'raw_content': table_content[:1000]  # Limit size
                    })
        return tables
    
    @staticmethod
    def _parse_markdown_table(content: str) -> Optional[Dict[str, Any]]:
        """Parse markdown table into structured format"""
        lines = content.strip().split('\n')
        if len(lines) < 3:
            return None
        
        # Extract headers
        header_line = lines[0]
        headers = [h.strip() for h in header_line.split('|') if h.strip()]
        
        # Extract rows (skip separator)
        rows = []
        for line in lines[2:]:
            if '|' in line:
                cells = [c.strip() for c in line.split('|')]
                cells = [c for c in cells if c]  # Remove empty
                if cells:
                    rows.append(cells)
        
        if headers and rows:
            return {'headers': headers, 'rows': rows}
        return None
    
    @staticmethod
    def extract_numerical_patterns(content: str) -> List[Dict[str, Any]]:
        """Extract numerical patterns for formula discovery"""
        patterns = []
        
        # Currency patterns (IDR, USD, etc)
        currency_patterns = [
            (r'(?:Rp|IDR)\s?([\d,.]+)(?:\s*(?:ribu|juta|miliar|triliun))?', 'idr'),
            (r'(?:USD|\$)\s?([\d,.]+)(?:\s*(?:thousand|million|billion))?', 'usd'),
        ]
        
        for pattern, currency_type in currency_patterns:
            for match in re.finditer(pattern, content, re.IGNORECASE):
                patterns.append({
                    'type': 'currency',
                    'currency': currency_type,
                    'value': match.group(0),
                    'position': match.span()
                })
        
        # Percentages
        for match in re.finditer(r'(\d+(?:[.,]\d+)?)\s*%', content):
            patterns.append({
                'type': 'percentage',
                'value': match.group(0),
                'position': match.span()
            })
        
        # Dates and periods
        for match in re.finditer(r'(\d+)\s*(tahun|bulan|hari|minggu|kuartal|semester)', content, re.IGNORECASE):
            patterns.append({
                'type': 'period',
                'value': match.group(0),
                'position': match.span()
            })
        
        # General numbers with context
        for match in re.finditer(r'\b(\d{1,3}(?:[.,]\d{3})*(?:[.,]\d+)?)\b', content):
            # Get surrounding context (20 chars before and after)
            start = max(0, match.start() - 20)
            end = min(len(content), match.end() + 20)
            context = content[start:end]
            
            patterns.append({
                'type': 'number',
                'value': match.group(0),
                'context': context,
                'position': match.span()
            })
        
        return patterns


class DirectEnhancerV2:
    """Production-ready direct enhancement system with professional implementation"""
    
    def __init__(self, config: Optional[EnhancementConfig] = None):
        """Initialize with configuration"""
        self.config = config or EnhancementConfig()
        self.client = AsyncOpenAI(api_key=self.config.openai_api_key)
        self.encoder = tiktoken.get_encoding("cl100k_base")
        self.rate_limiter = AsyncLeakyBucket(rps=self.config.requests_per_second)
        self.window_analyzer = WindowAnalyzer()
        
        # Load type registry (universal enhancement types)
        self.type_registry: EnhancementTypeRegistry = get_type_registry()
        
        # Use model directly from config - NO HARDCODING!
        # Config determines the model, backend just uses it
        self.model = self.config.gen_model
        
        self.temperature = self.config.openai_temperature
        self.max_retries = self.config.openai_max_retries
        
        # Window configuration from config
        self.max_window_tokens = self.config.window_tokens
        self.overlap_tokens = self.config.window_overlap_tokens
        
        # Enhancer initialized (silent mode)
    
    async def enhance_document(
        self,
        doc_id: str,
        markdown_path: str,
        units_metadata: Dict[str, Any],
        tables_data: Optional[List[Dict[str, Any]]] = None,
        selected_types: Optional[List[str]] = None,
        domain_hint: Optional[str] = None,
        custom_instructions: Optional[str] = None
    ) -> List[UniversalEnhancement]:
        """
        Main entry point for direct enhancement with professional implementation
        
        Args:
            doc_id: Document identifier
            markdown_path: Path to markdown file
            units_metadata: Metadata about document units
            tables_data: Optional pre-extracted table data
            selected_types: Optional list of enhancement type IDs to use (from user config)
            domain_hint: Optional domain hint for better prompts (financial, legal, etc.)
            custom_instructions: Optional custom instructions from user
            
        Returns:
            List of UniversalEnhancement objects
        """
        try:
            # Store user configuration for this enhancement session
            self.user_selected_types = selected_types
            self.user_domain_hint = domain_hint
            self.user_custom_instructions = custom_instructions
            
            # Build dynamic system prompt based on user selection
            if selected_types:
                self.dynamic_system_prompt = self.type_registry.build_dynamic_system_prompt(
                    selected_type_ids=selected_types,
                    domain_hint=domain_hint
                )
            else:
                # Fallback to legacy hardcoded prompt if no user selection
                self.dynamic_system_prompt = DIRECT_ENHANCEMENT_SYSTEM_PROMPT
            
            # Read document content
            markdown_content = Path(markdown_path).read_text(encoding='utf-8')
            
            # Create enhancement windows
            windows = self._create_enhancement_windows(
                doc_id=doc_id,
                content=markdown_content,
                units_metadata=units_metadata,
                tables_data=tables_data
            )
            logger.info(f"[{doc_id[:8]}...] Enhancement: {len(markdown_content)} chars → {len(windows)} windows")
            
            # Process windows with controlled parallelism
            all_enhancements = await self._process_windows_parallel(
                windows=windows,
                doc_id=doc_id,
                max_parallel=self.config.planner_parallelism
            )
            
            # Deduplicate and rank enhancements
            final_enhancements = self._deduplicate_and_rank(all_enhancements)
            return final_enhancements
            
        except Exception as e:
            logger.error(f"[DirectEnhancerV2] Fatal error: {e}", exc_info=True)
            raise
    
    def _create_enhancement_windows(
        self,
        doc_id: str,
        content: str,
        units_metadata: Dict[str, Any],
        tables_data: Optional[List[Dict[str, Any]]]
    ) -> List[EnhancementWindow]:
        """Create properly structured windows for enhancement"""
        windows = []
        
        # Extract units list from metadata
        units_list = self._extract_units_list(units_metadata)
        
        # Sort units by position
        units_list.sort(key=lambda x: (
            x.get('page', 0),
            x.get('bbox', [0, 0])[1] if 'bbox' in x else 0
        ))
        
        # Create windows based on token count
        current_units = []
        current_text = []
        current_tokens = 0
        window_num = 0
        
        for unit in units_list:
            unit_text = unit.get('content', '')
            unit_tokens = len(self.encoder.encode(unit_text))
            
            # Check if adding this unit exceeds window limit
            if current_tokens + unit_tokens > self.max_window_tokens and current_units:
                # Create window
                window_num += 1
                window = self._build_window(
                    window_id=f"{doc_id}_w{window_num}",
                    window_number=window_num,
                    content='\n\n'.join(current_text),
                    units=current_units,
                    tables_data=tables_data
                )
                windows.append(window)
                
                # Handle overlap
                if self.overlap_tokens > 0:
                    # Keep last few units for overlap
                    overlap_units = []
                    overlap_text = []
                    overlap_tokens = 0
                    
                    for unit in reversed(current_units):
                        unit_text = unit.get('content', '')
                        unit_tokens = len(self.encoder.encode(unit_text))
                        if overlap_tokens + unit_tokens <= self.overlap_tokens:
                            overlap_units.insert(0, unit)
                            overlap_text.insert(0, unit_text)
                            overlap_tokens += unit_tokens
                        else:
                            break
                    
                    current_units = overlap_units
                    current_text = overlap_text
                    current_tokens = overlap_tokens
                else:
                    current_units = []
                    current_text = []
                    current_tokens = 0
            
            # Add unit to current window
            current_units.append(unit)
            current_text.append(unit_text)
            current_tokens += unit_tokens
        
        # Create final window if there's remaining content
        if current_units:
            window_num += 1
            window = self._build_window(
                window_id=f"{doc_id}_w{window_num}",
                window_number=window_num,
                content='\n\n'.join(current_text),
                units=current_units,
                tables_data=tables_data
            )
            windows.append(window)
        
        # Set total windows count
        for window in windows:
            window.total_windows = len(windows)
        
        return windows
    
    def _extract_units_list(self, units_metadata: Dict[str, Any]) -> List[Dict[str, Any]]:
        """Extract units list from various metadata formats"""
        units_list = []
        
        if isinstance(units_metadata, list):
            units_list = units_metadata
        elif isinstance(units_metadata, dict):
            # Try different possible structures
            if 'units' in units_metadata:
                units_list = units_metadata['units']
            elif 'content_units' in units_metadata:
                units_list = units_metadata['content_units']
            else:
                # Iterate through dict values
                for key, value in units_metadata.items():
                    if isinstance(value, dict) and 'content' in value:
                        value['unit_id'] = key
                        units_list.append(value)
                    elif isinstance(value, list):
                        units_list.extend(value)
        
        return units_list
    
    def _build_window(
        self,
        window_id: str,
        window_number: int,
        content: str,
        units: List[Dict[str, Any]],
        tables_data: Optional[List[Dict[str, Any]]]
    ) -> EnhancementWindow:
        """Build a complete enhancement window with analysis"""
        
        # Extract tables from units
        window_tables = self.window_analyzer.extract_tables_from_units(units)
        
        # Extract numerical patterns
        numerical_patterns = self.window_analyzer.extract_numerical_patterns(content)
        
        # Analyze content type
        metadata = self.window_analyzer.analyze_content_type(
            EnhancementWindow(
                window_id=window_id,
                window_number=window_number,
                total_windows=0,  # Will be set later
                content=content,
                units=units,
                tables=window_tables,
                numerical_patterns=numerical_patterns,
                token_count=len(self.encoder.encode(content)),
                metadata={}
            )
        )
        
        return EnhancementWindow(
            window_id=window_id,
            window_number=window_number,
            total_windows=0,  # Will be set later
            content=content,
            units=units,
            tables=window_tables,
            numerical_patterns=numerical_patterns,
            token_count=len(self.encoder.encode(content)),
            metadata=metadata
        )
    
    async def _process_windows_parallel(
        self,
        windows: List[EnhancementWindow],
        doc_id: str,
        max_parallel: int = 5
    ) -> List[UniversalEnhancement]:
        """
        Process windows with controlled parallelism (batched parallel processing).
        
        Strategy:
        - Process max_parallel windows at once (default: 5)
        - Wait for entire batch to complete before starting next batch
        - Example: 15 windows → Batch 1 (1-5), Batch 2 (6-10), Batch 3 (11-15)
        
        Args:
            windows: List of enhancement windows
            doc_id: Document ID
            max_parallel: Max windows to process in parallel per batch
            
        Returns:
            List of all enhancements from all windows
        """
        all_enhancements = []
        total_windows = len(windows)
        
        # Process in batches
        batch_num = 0
        for i in range(0, total_windows, max_parallel):
            batch_num += 1
            batch = windows[i:i + max_parallel]
            batch_size = len(batch)
            batch_start = i + 1
            batch_end = i + batch_size
            
            # Create tasks for parallel processing
            tasks = [
                self._enhance_window(window, doc_id)
                for window in batch
            ]
            
            # Wait for entire batch to complete
            import time
            batch_start_time = time.time()
            batch_results = await asyncio.gather(*tasks, return_exceptions=True)
            batch_duration = time.time() - batch_start_time
            
            # Process results
            batch_enhancements = 0
            for j, result in enumerate(batch_results):
                window_num = i + j + 1
                if isinstance(result, Exception):
                    logger.error(f"[Batch {batch_num}] Window {window_num} failed: {result}")
                elif result:
                    all_enhancements.extend(result)
                    batch_enhancements += len(result)
            
            if total_windows > 1:
                logger.info(f"[Batch {batch_num}] W{batch_start}-{batch_end} → {batch_enhancements} items ({batch_duration:.1f}s)")
        return all_enhancements
    
    async def _enhance_window(
        self,
        window: EnhancementWindow,
        doc_id: str
    ) -> List[UniversalEnhancement]:
        """Enhance a single window with robust error handling"""
        
        for attempt in range(self.max_retries):
            try:
                # Rate limiting
                await self.rate_limiter.acquire()
                
                # Build prompt
                user_prompt = self._build_user_prompt(window)
                
                # Call LLM
                logger.debug(f"Calling {self.model} for window {window.window_number}")
                
                # Use regular JSON mode with manual parsing
                # This is more stable than beta structured outputs
                # Use dynamic_system_prompt which is built from user selection
                response = await self.client.chat.completions.create(
                    model=self.model,
                    messages=[
                        {"role": "system", "content": self.dynamic_system_prompt},
                        {"role": "user", "content": user_prompt}
                    ],
                    temperature=self.temperature,
                    max_tokens=self.config.max_generation_tokens,
                    response_format={"type": "json_object"}
                )
                
                # Parse JSON response
                content = response.choices[0].message.content
                finish_reason = response.choices[0].finish_reason
                
                # Log for debugging
                logger.debug(f"LLM response length: {len(content)} chars, finish_reason: {finish_reason}")
                
                # Warn if response was truncated
                if finish_reason == "length":
                    logger.warning(f"⚠️ Response truncated by max_tokens limit! Consider increasing max_generation_tokens (current: {self.config.max_generation_tokens})")
                elif finish_reason != "stop":
                    logger.warning(f"⚠️ Unusual finish_reason: {finish_reason}")
                
                # Save raw response for debugging (to artefacts folder)
                try:
                    from pathlib import Path
                    debug_dir = Path(self.config.artifacts_dir) / doc_id / "debug"
                    debug_dir.mkdir(parents=True, exist_ok=True)
                    
                    debug_file = debug_dir / f"llm_response_window_{window.window_number}_attempt_{attempt + 1}.json"
                    debug_file.write_text(content, encoding='utf-8')
                except Exception as save_error:
                    pass  # Silent debug save
                
                # Parse and validate
                result = self._parse_and_validate_response(content)
                enhancements_data = result.get('enhancements', [])
                
                # VALIDATION: Check if enhancement types match user selection
                if self.user_selected_types:
                    valid_enhancements = []
                    invalid_count = 0
                    
                    for enh in enhancements_data:
                        enh_type = enh.get('type') or enh.get('enhancement_type', '')
                        if enh_type in self.user_selected_types:
                            valid_enhancements.append(enh)
                        else:
                            invalid_count += 1
                            logger.warning(f"⚠️ Invalid type '{enh_type}' (not in user selection). Expected one of: {self.user_selected_types}")
                    
                    # If more than 30% invalid, retry with stronger enforcement
                    if len(enhancements_data) > 0 and invalid_count / len(enhancements_data) > 0.3:
                        logger.warning(f"⚠️ {invalid_count}/{len(enhancements_data)} enhancements have invalid types. Retrying with stronger prompt...")
                        raise ValueError(f"Too many invalid types: {invalid_count}/{len(enhancements_data)}")
                    
                    enhancements_data = valid_enhancements
                
                # Log success (silent for single window)
                # if enhancements_data and total windows > 1: log
                
                # Convert to UniversalEnhancement objects
                enhancements = []
                for enh_data in enhancements_data:
                    enhancement = self._create_enhancement(
                        data=enh_data,
                        window=window,
                        doc_id=doc_id
                    )
                    if enhancement:
                        enhancements.append(enhancement)
                
                return enhancements
                
            except Exception as e:
                # Log full error details for debugging
                import traceback
                error_trace = traceback.format_exc()
                logger.warning(f"Attempt {attempt + 1} failed for window {window.window_number}: {type(e).__name__}: {e}")
                logger.debug(f"Full traceback:\n{error_trace}")
                
                if attempt == self.max_retries - 1:
                    logger.error(f"All attempts failed for window {window.window_number}")
                    logger.error(f"Last error: {type(e).__name__}: {e}")
                    logger.error(f"Traceback:\n{error_trace}")
                    return []
                
                # Exponential backoff
                await asyncio.sleep(2 ** attempt)
        
        return []
    
    def _build_user_prompt(self, window: EnhancementWindow) -> str:
        """Build comprehensive user prompt with window context"""
        
        # Format tables for prompt
        tables_info = ""
        if window.tables:
            tables_info = "\n=== DATA TABEL TERSTRUKTUR ===\n"
            for i, table in enumerate(window.tables[:3], 1):
                tables_info += f"\nTABEL {i}:\n"
                tables_info += f"Headers: {table['headers']}\n"
                
                # Show sample rows
                rows_to_show = table['rows'][:5]
                tables_info += f"Data ({len(table['rows'])} total rows, showing first {len(rows_to_show)}):\n"
                for row in rows_to_show:
                    tables_info += f"  {row}\n"
                tables_info += "---\n"
        
        # Add numerical patterns summary
        if window.numerical_patterns:
            patterns_summary = self._summarize_numerical_patterns(window.numerical_patterns)
            tables_info += f"\n=== POLA NUMERIK TERDETEKSI ===\n{patterns_summary}\n"
        
        # Add content type hint
        content_hint = ""
        if window.metadata.get('dominant_type'):
            type_hints = {
                'financial': "Dokumen ini mengandung data finansial. Fokus pada formula, kalkulasi, dan proyeksi.",
                'legal': "Dokumen ini bersifat legal/regulatori. Fokus pada implikasi, konsekuensi, dan requirement synthesis.",
                'procedural': "Dokumen ini berisi prosedur. Fokus pada workflow completion dan dependency mapping."
            }
            content_hint = f"\nHINT: {type_hints.get(window.metadata['dominant_type'], '')}\n"
        
        # Use template from prompts_direct
        return DIRECT_ENHANCEMENT_USER_PROMPT.format(
            window_number=window.window_number,
            total_windows=window.total_windows,
            window_content=window.content[:12000],  # Limit content size
            tables_info=tables_info + content_hint
        )
    
    def _summarize_numerical_patterns(self, patterns: List[Dict[str, Any]]) -> str:
        """Summarize numerical patterns for context"""
        summary = []
        
        # Count by type
        type_counts = {}
        for pattern in patterns:
            ptype = pattern['type']
            type_counts[ptype] = type_counts.get(ptype, 0) + 1
        
        for ptype, count in type_counts.items():
            summary.append(f"- {ptype}: {count} instances")
        
        # Show sample values
        if patterns:
            summary.append("\nSample values:")
            for pattern in patterns[:5]:
                summary.append(f"  - {pattern['value']} ({pattern['type']})")
        
        return '\n'.join(summary)
    
    def _parse_and_validate_response(self, content: str) -> Dict[str, Any]:
        """Parse and validate LLM response with comprehensive error recovery"""
        # Strategy 1: Direct JSON parse
        try:
            result = json.loads(content)
            
            # Validate structure
            if not isinstance(result, dict):
                raise ValueError("Response is not a dictionary")
            
            if 'enhancements' not in result:
                result['enhancements'] = []
            
            if not isinstance(result['enhancements'], list):
                raise ValueError("enhancements is not a list")
            
            logger.info(f"✓ Strategy 1 (direct parse): Success, {len(result['enhancements'])} enhancements")
            return result
            
        except json.JSONDecodeError as e:
            logger.warning(f"Strategy 1 failed: {e}")
        
        # Strategy 2: Extract from markdown code blocks
        try:
            json_match = re.search(r'```(?:json)?\s*(\{.*?\})\s*```', content, re.DOTALL)
            if json_match:
                result = json.loads(json_match.group(1))
                if 'enhancements' in result:
                    logger.info(f"✓ Strategy 2 (markdown blocks): Success, {len(result.get('enhancements', []))} enhancements")
                    return result
        except Exception as e:
            logger.warning(f"Strategy 2 failed: {e}")
        
        # Strategy 3: Fix common JSON issues (unterminated strings, trailing commas)
        try:
            # Count quotes and try to fix unterminated strings
            fixed_content = content
            
            # Remove trailing commas before } or ]
            fixed_content = re.sub(r',(\s*[}\]])', r'\1', fixed_content)
            
            # Try to close unterminated strings by adding quotes at line ends
            lines = fixed_content.split('\n')
            fixed_lines = []
            for line in lines:
                # If line has odd number of quotes and ends without quote, add one
                if line.count('"') % 2 != 0 and not line.rstrip().endswith('"'):
                    line = line.rstrip() + '"'
                fixed_lines.append(line)
            fixed_content = '\n'.join(fixed_lines)
            
            result = json.loads(fixed_content)
            if 'enhancements' in result:
                logger.info(f"✓ Strategy 3 (fix JSON): Success, {len(result.get('enhancements', []))} enhancements")
                return result
        except Exception as e:
            logger.warning(f"Strategy 3 failed: {e}")
        
        # Strategy 4: Extract JSON object with regex (greedy)
        try:
            json_match = re.search(r'\{.*"enhancements".*\}', content, re.DOTALL)
            if json_match:
                result = json.loads(json_match.group(0))
                if 'enhancements' in result:
                    logger.info(f"✓ Strategy 4 (regex extract): Success, {len(result.get('enhancements', []))} enhancements")
                    return result
        except Exception as e:
            logger.warning(f"Strategy 4 failed: {e}")
        
        # Strategy 5: Try to extract just the enhancements array
        try:
            array_match = re.search(r'"enhancements"\s*:\s*\[(.*?)\]', content, re.DOTALL)
            if array_match:
                # Reconstruct valid JSON
                reconstructed = '{"enhancements": [' + array_match.group(1) + ']}'
                result = json.loads(reconstructed)
                logger.info(f"✓ Strategy 5 (array extract): Success, {len(result.get('enhancements', []))} enhancements")
                return result
        except Exception as e:
            logger.warning(f"Strategy 5 failed: {e}")
        
        # All strategies failed
        logger.error("✗ All parsing strategies failed, returning empty structure")
        logger.error(f"Content preview (first 500 chars): {content[:500]}")
        return {"enhancements": []}
    
    def _create_enhancement(
        self,
        data: Dict[str, Any],
        window: EnhancementWindow,
        doc_id: str
    ) -> Optional[UniversalEnhancement]:
        """Create UniversalEnhancement with validation"""
        try:
            # Validate required fields
            if not data.get('content'):
                logger.warning(f"Enhancement missing content: {data.get('title', 'Unknown')}")
                return None
            
            # Ensure minimum content length
            content = data.get('content', '').strip()
            if len(content) < 100:  # Minimum 100 characters
                logger.warning(f"Enhancement content too short: {data.get('title', 'Unknown')}")
                return None
            
            # Get source units from window
            source_units = []
            for unit in window.units[:5]:  # First 5 units as reference
                unit_id = unit.get('unit_id') or unit.get('id') or f"unit_{len(source_units)}"
                source_units.append(unit_id)
            
            # Generate truly unique enhancement ID in backend (not from LLM!)
            # This reduces LLM cognitive load and ensures uniqueness
            timestamp = datetime.now().strftime('%Y%m%d%H%M%S')
            content_hash = hashlib.md5(content.encode()).hexdigest()[:8]
            enhancement_id = f"enh_{doc_id[:8]}_w{window.window_number}_{content_hash}_{timestamp}"
            
            return UniversalEnhancement(
                enhancement_id=enhancement_id,
                doc_id=doc_id,
                enhancement_type=data.get('type') or data.get('enhancement_type', 'general'),  # Check both 'type' and 'enhancement_type'
                title=data.get('title', 'Enhancement'),
                original_context=window.content[:500],
                generated_content=content,
                source_units=source_units,
                source_previews=[window.content[:200]],
                confidence_score=float(data.get('confidence', 0.8)),
                priority=self._calculate_priority(data, window),
                metadata={
                    'window_id': window.window_id,
                    'window_number': window.window_number,
                    'content_type': window.metadata.get('dominant_type', 'general'),
                    'has_tables': len(window.tables) > 0,
                    'has_numerical': len(window.numerical_patterns) > 0
                }
            )
            
        except Exception as e:
            logger.error(f"Failed to create enhancement: {e}")
            return None
    
    def _calculate_priority(self, data: Dict[str, Any], window: EnhancementWindow) -> int:
        """Calculate enhancement priority based on type and content"""
        priority = 5  # Default medium priority
        
        # Adjust based on enhancement type
        high_priority_types = ['formula_discovery', 'compliance_mapping', 'risk_identification']
        medium_priority_types = ['implication_analysis', 'pattern_recognition', 'requirement_synthesis']
        
        enhancement_type = data.get('enhancement_type', '')
        
        if enhancement_type in high_priority_types:
            priority = 8
        elif enhancement_type in medium_priority_types:
            priority = 6
        
        # Boost priority for numerical content
        if window.metadata.get('dominant_type') == 'financial':
            priority += 1
        
        # Boost for high confidence
        if float(data.get('confidence', 0)) >= 0.85:
            priority += 1
        
        return min(10, max(1, priority))  # Clamp between 1-10
    
    def _deduplicate_and_rank(
        self,
        enhancements: List[UniversalEnhancement]
    ) -> List[UniversalEnhancement]:
        """Deduplicate and rank enhancements"""
        
        # Deduplicate by content similarity
        seen_contents = set()
        unique_enhancements = []
        
        for enh in enhancements:
            # Create content fingerprint (first 200 chars normalized)
            content_fingerprint = re.sub(r'\s+', ' ', enh.generated_content[:200].lower().strip())
            
            if content_fingerprint not in seen_contents:
                seen_contents.add(content_fingerprint)
                unique_enhancements.append(enh)
            else:
                logger.debug(f"Duplicate enhancement removed: {enh.title}")
        
        # Sort by priority and confidence
        unique_enhancements.sort(
            key=lambda x: (x.priority, x.confidence_score),
            reverse=True
        )
        
        logger.info(f"Deduplicated from {len(enhancements)} to {len(unique_enhancements)} enhancements")
        
        return unique_enhancements
