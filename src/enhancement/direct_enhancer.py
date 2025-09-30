"""
Direct Enhancement System - Single Step Processing
No separate planning phase, direct generation with smaller windows
"""

import asyncio
import json
import logging
from typing import List, Dict, Any, Optional
from datetime import datetime
from pathlib import Path
import tiktoken

from openai import AsyncOpenAI
from pydantic import BaseModel

from src.enhancement.config import EnhancementConfig
from src.enhancement.simple_windowing import SimpleWindow, SimpleWindowManager
from src.enhancement.models import UniversalEnhancement
from src.enhancement.prompts_direct import (
    DIRECT_ENHANCEMENT_SYSTEM_PROMPT,
    DIRECT_ENHANCEMENT_USER_PROMPT
)
from src.core.rate_limiter import AsyncLeakyBucket

logger = logging.getLogger(__name__)


class DirectEnhancement(BaseModel):
    """Single enhancement with all required fields"""
    enhancement_type: str
    title: str
    content: str
    source_units: List[str]
    confidence: float = 0.8
    priority: int = 5


class DirectEnhancer:
    """Direct single-step enhancement generator"""
    
    def __init__(self, config: EnhancementConfig):
        self.config = config
        self.client = AsyncOpenAI(api_key=config.openai_api_key)
        self.encoder = tiktoken.get_encoding("cl100k_base")
        self.rate_limiter = AsyncLeakyBucket(rps=2.0)
    
    async def enhance_document(
        self,
        doc_id: str,
        markdown_path: str,
        units_metadata: Dict[str, Any],
        tables_data: List[Dict[str, Any]]
    ) -> List[UniversalEnhancement]:
        """Main entry point for direct enhancement"""
        
        logger.info(f"Starting direct enhancement for {doc_id}")
        
        # Create smaller windows (5000 tokens each)
        window_manager = SimpleWindowManager(
            max_tokens=5000,  # Much smaller windows
            overlap_tokens=500  # Small overlap
        )
        
        # Create windows
        markdown_content = Path(markdown_path).read_text(encoding='utf-8')
        windows = window_manager.create_windows(
            doc_id=doc_id,
            markdown_content=markdown_content,
            units_metadata=units_metadata
        )
        
        logger.info(f"Created {len(windows)} windows for processing")
        
        # Process each window directly
        all_enhancements = []
        
        for i, window in enumerate(windows, 1):
            logger.info(f"Processing window {i}/{len(windows)}")
            
            # Get enhancements for this window
            window_enhancements = await self._enhance_window(
                window=window,
                window_number=i,
                total_windows=len(windows),
                tables_data=tables_data,
                doc_id=doc_id
            )
            
            all_enhancements.extend(window_enhancements)
            logger.info(f"Window {i} generated {len(window_enhancements)} enhancements")
        
        logger.info(f"Total enhancements generated: {len(all_enhancements)}")
        return all_enhancements
    
    async def _enhance_window(
        self,
        window: SimpleWindow,
        window_number: int,
        total_windows: int,
        tables_data: List[Dict[str, Any]],
        doc_id: str
    ) -> List[UniversalEnhancement]:
        """Enhance a single window directly"""
        
        # Prepare context
        window_tables = self._get_window_tables(window, tables_data)
        
        # Build prompt using professional prompt engineering
        user_prompt = self._build_user_prompt(
            window=window,
            window_number=window_number,
            total_windows=total_windows,
            tables=window_tables
        )
        
        # Call LLM with retries
        max_retries = 3
        for attempt in range(max_retries):
            try:
                await self.rate_limiter.acquire()
                
                response = await self.client.chat.completions.create(
                    model="gpt-4.1",
                    messages=[
                        {"role": "system", "content": DIRECT_ENHANCEMENT_SYSTEM_PROMPT},
                        {"role": "user", "content": user_prompt}
                    ],
                    temperature=0.2,  # Lower for more consistency
                    max_tokens=4000,  # Higher for detailed content
                    response_format={"type": "json_object"}
                )
                
                content = response.choices[0].message.content
                result = self._parse_response(content)
                
                # Convert to UniversalEnhancement objects
                enhancements = []
                for enh_data in result.get('enhancements', []):
                    enhancement = self._create_enhancement(enh_data, window, doc_id)
                    if enhancement:
                        enhancements.append(enhancement)
                
                return enhancements
                
            except Exception as e:
                logger.error(f"Attempt {attempt + 1} failed: {e}")
                if attempt == max_retries - 1:
                    logger.error(f"All attempts failed for window {window_number}")
                    return []
                await asyncio.sleep(2 ** attempt)
        
        return []
    
    
    def _build_user_prompt(
        self,
        window: SimpleWindow,
        window_number: int,
        total_windows: int,
        tables: List[Dict[str, Any]]
    ) -> str:
        """Build user prompt using professional prompt engineering"""
        
        # Prepare window content (limit to prevent token overflow)
        window_content = window.text_content[:12000]  # Increased limit for better analysis
        
        # Format tables information with more detail
        tables_info = ""
        if tables:
            tables_info = "\n=== DATA TABEL ===\n"
            for i, table in enumerate(tables[:3], 1):  # Limit to 3 most relevant tables
                headers = table.get('headers', [])
                rows = table.get('rows', [])[:5]  # Show more rows for better analysis
                
                tables_info += f"\nTABEL {i}:\n"
                tables_info += f"Headers: {headers}\n"
                tables_info += "Data:\n"
                for row in rows:
                    tables_info += f"  {row}\n"
                tables_info += "---\n"
        
        # Use professional prompt template
        return DIRECT_ENHANCEMENT_USER_PROMPT.format(
            window_number=window_number,
            total_windows=total_windows,
            window_content=window_content,
            tables_info=tables_info
        )
    
    def _parse_response(self, content: str) -> Dict[str, Any]:
        """Parse LLM response with error recovery"""
        try:
            return json.loads(content)
        except json.JSONDecodeError as e:
            logger.warning(f"JSON parse error: {e}")
            
            # Try to fix common issues
            content = content.replace('```json', '').replace('```', '')
            content = content.strip()
            
            try:
                return json.loads(content)
            except:
                # Return empty structure
                return {"enhancements": []}
    
    def _create_enhancement(
        self,
        data: Dict[str, Any],
        window: SimpleWindow,
        doc_id: str
    ) -> Optional[UniversalEnhancement]:
        """Create UniversalEnhancement from parsed data"""
        try:
            # Ensure we have actual content
            content = data.get('content', '').strip()
            if not content or len(content) < 50:
                logger.warning(f"Enhancement has insufficient content: {data.get('title', 'Unknown')}")
                return None
            
            # Get source units from window
            source_units = [u.get('unit_id', f"unit_{i}") for i, u in enumerate(window.units[:5])]  # First 5 units
            
            return UniversalEnhancement(
                enhancement_id=f"enh_{doc_id}_{window.window_id}_{data.get('title', '')[:20]}",
                doc_id=doc_id,
                enhancement_type=data.get('enhancement_type', 'unknown'),
                title=data.get('title', 'Enhancement'),
                original_context=window.text_content[:500],
                generated_content=content,  # Use the actual content!
                source_units=source_units,
                source_previews=[window.text_content[:200]],
                confidence_score=data.get('confidence', 0.8),
                priority=5,
                metadata={
                    'window_id': window.window_id,
                    'window_number': window.window_id
                }
            )
            
        except Exception as e:
            logger.error(f"Failed to create enhancement: {e}")
            return None
    
    def _get_window_tables(
        self,
        window: SimpleWindow,
        tables_data: List[Dict[str, Any]]
    ) -> List[Dict[str, Any]]:
        """Get tables that belong to current window"""
        window_tables = []
        
        for unit in window.units:
            if unit.get('type') == 'table':
                # Find matching table data
                unit_id = unit.get('unit_id', '')
                for table in tables_data:
                    if table.get('table_id', '') == unit_id:
                        window_tables.append(table)
                        break
        
        return window_tables
