"""
Direct Enhancement System V2 - Production-Ready Single-Step Enhancement
Professional implementation with proper windowing, parallel processing, and robust error handling
Matches quality standards of planner_v2.py and generator_v2.py
"""

import asyncio
import json
import re
import hashlib
from typing import List, Dict, Any, Optional, Tuple, Set
from datetime import datetime
from pathlib import Path
import tiktoken
from dataclasses import dataclass, asdict

from openai import AsyncOpenAI
from pydantic import BaseModel, Field
from loguru import logger

from src.enhancement.config import EnhancementConfig
from src.enhancement.models import UniversalEnhancement
from src.enhancement.prompts_direct import (
    DIRECT_ENHANCEMENT_SYSTEM_PROMPT,
    DIRECT_ENHANCEMENT_USER_PROMPT
)
from src.core.rate_limiter import AsyncLeakyBucket


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
        
        # Use model from config with fallback to valid model names
        self.model = self.config.gen_model
        # Fix model name if needed
        
        self.temperature = self.config.openai_temperature
        self.max_retries = self.config.openai_max_retries
        
        # Window configuration from config
        self.max_window_tokens = self.config.window_tokens
        self.overlap_tokens = self.config.window_overlap_tokens
        
        logger.info(f"DirectEnhancerV2 initialized with model: {self.model}, window size: {self.max_window_tokens}")
    
    async def enhance_document(
        self,
        doc_id: str,
        markdown_path: str,
        units_metadata: Dict[str, Any],
        tables_data: Optional[List[Dict[str, Any]]] = None
    ) -> List[UniversalEnhancement]:
        """
        Main entry point for direct enhancement with professional implementation
        
        Args:
            doc_id: Document identifier
            markdown_path: Path to markdown file
            units_metadata: Metadata about document units
            tables_data: Optional pre-extracted table data
            
        Returns:
            List of UniversalEnhancement objects
        """
        try:
            logger.info(f"[DirectEnhancerV2] Starting enhancement for document: {doc_id}")
            
            # Read document content
            markdown_content = Path(markdown_path).read_text(encoding='utf-8')
            logger.info(f"[DirectEnhancerV2] Document size: {len(markdown_content)} chars")
            
            # Create enhancement windows
            windows = self._create_enhancement_windows(
                doc_id=doc_id,
                content=markdown_content,
                units_metadata=units_metadata,
                tables_data=tables_data
            )
            logger.info(f"[DirectEnhancerV2] Created {len(windows)} windows for processing")
            
            # Process windows with controlled parallelism
            all_enhancements = await self._process_windows_parallel(
                windows=windows,
                doc_id=doc_id,
                max_parallel=self.config.planner_parallelism
            )
            
            # Deduplicate and rank enhancements
            final_enhancements = self._deduplicate_and_rank(all_enhancements)
            
            logger.info(f"[DirectEnhancerV2] Completed: {len(final_enhancements)} final enhancements")
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
        max_parallel: int = 3
    ) -> List[UniversalEnhancement]:
        """Process windows with controlled parallelism"""
        all_enhancements = []
        
        # Process in batches
        for i in range(0, len(windows), max_parallel):
            batch = windows[i:i + max_parallel]
            
            # Create tasks for parallel processing
            tasks = [
                self._enhance_window(window, doc_id)
                for window in batch
            ]
            
            # Wait for batch to complete
            batch_results = await asyncio.gather(*tasks, return_exceptions=True)
            
            # Process results
            for j, result in enumerate(batch_results):
                if isinstance(result, Exception):
                    logger.error(f"Window {i+j+1} failed: {result}")
                elif result:
                    all_enhancements.extend(result)
                    logger.info(f"Window {i+j+1} generated {len(result)} enhancements")
        
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
                
                response = await self.client.chat.completions.create(
                    model=self.model,
                    messages=[
                        {"role": "system", "content": DIRECT_ENHANCEMENT_SYSTEM_PROMPT},
                        {"role": "user", "content": user_prompt}
                    ],
                    temperature=self.temperature,
                    max_tokens=self.config.max_generation_tokens,
                    response_format={"type": "json_object"}
                )
                
                # Parse response
                content = response.choices[0].message.content
                result = self._parse_and_validate_response(content)
                
                # Convert to UniversalEnhancement objects
                enhancements = []
                for enh_data in result.get('enhancements', []):
                    enhancement = self._create_enhancement(
                        data=enh_data,
                        window=window,
                        doc_id=doc_id
                    )
                    if enhancement:
                        enhancements.append(enhancement)
                
                return enhancements
                
            except Exception as e:
                logger.warning(f"Attempt {attempt + 1} failed for window {window.window_number}: {e}")
                if attempt == self.max_retries - 1:
                    logger.error(f"All attempts failed for window {window.window_number}")
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
        try:
            # Direct JSON parse
            result = json.loads(content)
            
            # Validate structure
            if not isinstance(result, dict):
                raise ValueError("Response is not a dictionary")
            
            if 'enhancements' not in result:
                result['enhancements'] = []
            
            if not isinstance(result['enhancements'], list):
                raise ValueError("enhancements is not a list")
            
            return result
            
        except json.JSONDecodeError as e:
            logger.warning(f"JSON parse error: {e}")
            
            # Try to extract JSON from markdown code blocks
            json_match = re.search(r'```(?:json)?\s*(\{.*?\})\s*```', content, re.DOTALL)
            if json_match:
                try:
                    return json.loads(json_match.group(1))
                except:
                    pass
            
            # Try to find JSON object directly
            json_match = re.search(r'\{.*"enhancements".*\}', content, re.DOTALL)
            if json_match:
                try:
                    return json.loads(json_match.group(0))
                except:
                    pass
            
            # Return empty structure
            logger.error("Failed to parse response, returning empty structure")
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
            
            # Create enhancement ID
            title_slug = re.sub(r'[^a-zA-Z0-9]+', '_', data.get('title', 'enhancement')[:30])
            enhancement_id = f"{doc_id}_w{window.window_number}_{title_slug}"
            
            return UniversalEnhancement(
                enhancement_id=enhancement_id,
                doc_id=doc_id,
                enhancement_type=data.get('enhancement_type', 'general'),
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
