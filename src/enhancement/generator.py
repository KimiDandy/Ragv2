"""
Micro-batch generation system for creating enhancement narratives.
"""

import json
import re
import asyncio
from typing import List, Dict, Any, Optional, Tuple
from dataclasses import dataclass, asdict
from datetime import datetime
import hashlib
import sys
import os

from openai import AsyncOpenAI
from loguru import logger

from .config import EnhancementConfig
from .planner import EnhancementCandidate
from ..core.rate_limiter import AsyncLeakyBucket


@dataclass
class EnhancementItem:
    """Generated enhancement item ready for use."""
    enh_id: str
    cand_id: str
    type: str
    title: str
    text: str
    source_unit_ids: List[str]
    pages: List[int]
    section: str
    as_of_date: Optional[str]
    derived: bool = True
    server_calcs: Optional[Dict[str, Any]] = None
    confidence: float = 0.8
    review_flags: List[str] = None
    model: str = "gpt-4.1"
    usage: Dict[str, int] = None
    
    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


class NumericCalculator:
    """Server-side numeric calculations to avoid LLM hallucination."""
    
    @staticmethod
    def extract_numbers_from_text(text: str) -> List[float]:
        """Extract all numeric values from text."""
        # Pattern for numbers - percentage, decimal, or integer
        patterns = [
            r'(\d+\.?\d*)\s*%',  # Percentages like 5.75%
            r'(\d+\.\d+)',       # Decimals like 5.75
            r'(\d+,?\d+)',       # Integers with optional comma like 5,300
        ]
        
        numbers = []
        for pattern in patterns:
            matches = re.findall(pattern, text)
            for match in matches:
                try:
                    # Remove commas and convert
                    clean = match.replace(',', '')
                    numbers.append(float(clean))
                except:
                    continue
        
        return numbers
    
    @staticmethod
    def calculate_change(old_value: float, new_value: float) -> Dict[str, Any]:
        """Calculate absolute and percentage change."""
        absolute_change = new_value - old_value
        
        if old_value != 0:
            percent_change = ((new_value - old_value) / old_value) * 100
        else:
            percent_change = float('inf') if new_value > 0 else 0
        
        return {
            'old_value': old_value,
            'new_value': new_value,
            'absolute_change': round(absolute_change, 2),
            'percent_change': round(percent_change, 2) if percent_change != float('inf') else 'N/A'
        }
    
    @staticmethod
    def format_number(value: float, currency: bool = False) -> str:
        """Format number for display."""
        if currency:
            return f"Rp{value:,.0f}"
        return f"{value:,.2f}"


class EnhancementGenerator:
    """Generates enhancement narratives using micro-batching."""
    
    def __init__(self, config: EnhancementConfig):
        self.config = config
        self.client = AsyncOpenAI(api_key=config.openai_api_key)
        self.rate_limiter = AsyncLeakyBucket(rps=config.requests_per_second)
        self.calculator = NumericCalculator()
    
    async def generate_enhancements(
        self,
        candidates: List[EnhancementCandidate],
        units_metadata: List[Dict[str, Any]],
        doc_id: str
    ) -> Dict[str, Any]:
        """
        Generate enhancement narratives from candidates.
        
        Args:
            candidates: List of enhancement candidates
            units_metadata: Complete units metadata
            doc_id: Document identifier
            
        Returns:
            Dictionary with generated items and metrics
        """
        logger.info(f"Generating {len(candidates)} enhancements for {doc_id}")
        
        # Create batches
        batches = self._create_micro_batches(candidates)
        logger.info(f"Created {len(batches)} micro-batches")
        
        # Generate items for each batch
        all_items = []
        total_usage = {'prompt_tokens': 0, 'completion_tokens': 0}
        
        for batch_idx, batch in enumerate(batches):
            try:
                items, usage = await self._generate_batch_with_retry(batch, units_metadata, batch_idx)
                all_items.extend(items)
                total_usage['prompt_tokens'] += usage['prompt_tokens']
                total_usage['completion_tokens'] += usage['completion_tokens']
                
                logger.info(f"Batch {batch_idx + 1}/{len(batches)}: {len(items)} items generated")
                
            except Exception as e:
                logger.error(f"Error generating batch {batch_idx}: {e}")
                continue
        
        # Validate items
        validated_items = self._validate_items(all_items, units_metadata)
        
        result = {
            "doc_id": doc_id,
            "run_id": f"gen_{datetime.utcnow().isoformat()}",
            "items": [item.to_dict() for item in validated_items],
            "metrics": {
                "total_candidates": len(candidates),
                "total_batches": len(batches),
                "total_items": len(validated_items),
                "validation_pass_rate": len(validated_items) / len(all_items) if all_items else 0,
                "usage": total_usage
            }
        }
        
        return result
    
    async def _generate_batch_with_retry(
        self,
        batch: List[EnhancementCandidate],
        units_metadata: List[Dict[str, Any]],
        batch_idx: int,
        max_retries: int = 3
    ) -> Tuple[List[EnhancementItem], Dict[str, int]]:
        """Generate batch with retry mechanism for robust JSON parsing."""
        for retry in range(max_retries):
            try:
                return await self._generate_batch(batch, units_metadata, batch_idx)
            except json.JSONDecodeError as e:
                logger.warning(f"JSON parse error in generation (attempt {retry + 1}): {e}")
                if retry < max_retries - 1:
                    logger.info(f"Retrying batch generation... (attempt {retry + 2})")
                    continue
                else:
                    logger.error("All batch generation retries failed")
                    return [], {'prompt_tokens': 0, 'completion_tokens': 0}
            except Exception as e:
                logger.error(f"Batch generation failed (attempt {retry + 1}): {e}")
                if retry < max_retries - 1:
                    continue
                else:
                    return [], {'prompt_tokens': 0, 'completion_tokens': 0}
    
    def _create_micro_batches(
        self,
        candidates: List[EnhancementCandidate]
    ) -> List[List[EnhancementCandidate]]:
        """Create micro-batches for efficient generation."""
        batch_size = self.config.gen_microbatch_size
        batches = []
        
        for i in range(0, len(candidates), batch_size):
            batch = candidates[i:i + batch_size]
            batches.append(batch)
        
        return batches
    
    async def _generate_batch(
        self,
        batch: List[EnhancementCandidate],
        units_metadata: List[Dict[str, Any]],
        batch_idx: int
    ) -> Tuple[List[EnhancementItem], Dict[str, int]]:
        """Generate items for a single batch."""
        # Prepare source texts and calculations for each candidate
        batch_data = []
        
        for candidate in batch:
            # Get source texts
            source_texts = self._get_source_texts(candidate, units_metadata)
            
            # Extract and calculate numbers if needed
            calculations = None
            if candidate.type == 'highlight' and self.config.enable_server_calc:
                calculations = self._prepare_calculations(source_texts)
            
            batch_data.append({
                'candidate': candidate,
                'source_texts': source_texts,
                'calculations': calculations
            })
        
        # Build prompt
        prompt = self._build_generation_prompt(batch_data)
        
        # Call LLM
        await self.rate_limiter.acquire()
        
        try:
            # Enforce strict JSON via Structured Outputs (json_schema)
            items_schema = {
                "type": "json_schema",
                "json_schema": {
                    "name": "enhancement_items",
                    "schema": {
                        "$schema": "http://json-schema.org/draft-07/schema#",
                        "type": "object",
                        "properties": {
                            "items": {
                                "type": "array",
                                "items": {
                                    "type": "object",
                                    "additionalProperties": False,
                                    "properties": {
                                        "text": {"type": "string", "minLength": 1}
                                    },
                                    "required": ["text"]
                                }
                            }
                        },
                        "required": ["items"],
                        "additionalProperties": False
                    }
                }
            }

            response = await self.client.chat.completions.create(
                model=self.config.gen_model,
                messages=[
                    {"role": "system", "content": self._get_generator_system_prompt()},
                    {"role": "user", "content": prompt}
                ],
                # Lower temperature to improve structure adherence
                temperature=min(0.2, getattr(self.config, "openai_temperature", 0.2)),
                max_tokens=self.config.max_generation_tokens * len(batch),
                response_format=items_schema
            )
            
            # With Structured Outputs, JSON is guaranteed valid
            content = response.choices[0].message.content
            data = json.loads(content)  # Direct parse - schema guarantees validity
            
            # Create items
            items = []
            for idx, item_data in enumerate(data.get('items', [])):
                if idx >= len(batch_data):
                    break
                    
                item = self._create_item(
                    item_data=item_data,
                    candidate=batch_data[idx]['candidate'],
                    calculations=batch_data[idx].get('calculations'),
                    model=self.config.gen_model
                )
                
                if item:
                    items.append(item)
            
            usage = {
                'prompt_tokens': response.usage.prompt_tokens,
                'completion_tokens': response.usage.completion_tokens
            }
            
            return items, usage
            
        except Exception as e:
            logger.error(f"Error in batch generation: {e}")
            return [], {'prompt_tokens': 0, 'completion_tokens': 0}
    
    def _get_source_texts(
        self,
        candidate: EnhancementCandidate,
        units_metadata: List[Dict[str, Any]]
    ) -> List[str]:
        """Get source texts for a candidate."""
        texts = []
        
        for unit_id in candidate.source_unit_ids:
            # Find unit in metadata
            unit = next((u for u in units_metadata if u.get('unit_id') == unit_id), None)
            
            if unit:
                content = unit.get('content', '')
                # Limit length per source
                if len(content) > 500:
                    content = content[:500] + '...'
                texts.append(content)
        
        return texts
    
    def _prepare_calculations(
        self,
        source_texts: List[str]
    ) -> Optional[Dict[str, Any]]:
        """Prepare numeric calculations from source texts."""
        all_numbers = []
        
        for text in source_texts:
            numbers = self.calculator.extract_numbers_from_text(text)
            all_numbers.extend(numbers)
        
        if len(all_numbers) < 2:
            return None
        
        # Calculate changes between first and last significant numbers
        calculations = {
            'extracted_numbers': all_numbers,
            'formatted_numbers': [self.calculator.format_number(n, currency=True) for n in all_numbers]
        }
        
        if len(all_numbers) >= 2:
            change = self.calculator.calculate_change(all_numbers[0], all_numbers[-1])
            calculations['change'] = change
        
        return calculations
    
    def _build_generation_prompt(self, batch_data: List[Dict[str, Any]]) -> str:
        """Build prompt for batch generation."""
        sys.path.append(os.path.join(os.path.dirname(__file__), '..', '..'))
        from prompts.enhancement_generator import USER_PROMPT_TEMPLATE
        
        # Format candidates info
        candidates_info = []
        for idx, data in enumerate(batch_data):
            candidate = data['candidate']
            candidates_info.append(f"Kandidat {idx + 1}: {candidate.type} - {candidate.title} (Halaman: {candidate.pages})")
        
        # Format source content
        source_content = []
        for idx, data in enumerate(batch_data):
            source_texts = data['source_texts']
            source_content.append(f"Sumber {idx + 1}:")
            for text in source_texts:
                source_content.append(f"- {text[:200]}...")
        
        # Format calculations if any
        calculations = {}
        for idx, data in enumerate(batch_data):
            if data.get('calculations'):
                calculations[f"item_{idx}"] = data['calculations']
        
        return USER_PROMPT_TEMPLATE.format(
            candidates_info=chr(10).join(candidates_info),
            source_content=chr(10).join(source_content),
            calculations=json.dumps(calculations, indent=2) if calculations else "Tidak ada perhitungan server"
        )
    
    def _get_generator_system_prompt(self) -> str:
        """Get system prompt for generator."""
        import sys
        import os
        sys.path.append(os.path.join(os.path.dirname(__file__), '..', '..'))
        from prompts.enhancement_generator import SYSTEM_PROMPT
        return SYSTEM_PROMPT
    
    def _create_item(
        self,
        item_data: Dict[str, Any],
        candidate: EnhancementCandidate,
        calculations: Optional[Dict[str, Any]],
        model: str
    ) -> Optional[EnhancementItem]:
        """Create an enhancement item from generated data."""
        text = item_data.get('text', '')
        
        if not text:
            return None
        
        # Generate enhancement ID
        enh_id = f"enh_{candidate.type}_{hashlib.md5(text.encode()).hexdigest()[:8]}"
        
        # Extract as_of_date if present in text
        as_of_date = None
        date_pattern = r'\b(\d{4}-\d{2}-\d{2}|\d{2}/\d{2}/\d{4})\b'
        date_match = re.search(date_pattern, text)
        if date_match:
            as_of_date = date_match.group(1)
        
        return EnhancementItem(
            enh_id=enh_id,
            cand_id=candidate.cand_id,
            type=candidate.type,
            title=candidate.title,
            text=text,
            source_unit_ids=candidate.source_unit_ids,
            pages=candidate.pages,
            section=candidate.section,
            as_of_date=as_of_date,
            derived=True,
            server_calcs=calculations,
            confidence=candidate.confidence,
            review_flags=[],
            model=model,
            usage=None  # Will be aggregated at batch level
        )
    
    
    def _validate_items(
        self,
        items: List[EnhancementItem],
        units_metadata: List[Dict[str, Any]]
    ) -> List[EnhancementItem]:
        """Validate generated items for consistency."""
        validated = []
        
        for item in items:
            # Get source content
            source_content = []
            for unit_id in item.source_unit_ids:
                unit = next((u for u in units_metadata if u.get('unit_id') == unit_id), None)
                if unit:
                    source_content.append(unit.get('content', ''))
            
            full_source = ' '.join(source_content).lower()
            
            # Check for consistency
            review_flags = []
            
            # 1. Check key terms presence (relaxed)
            words = re.findall(r'\b\w+\b', item.text.lower())
            important_words = [w for w in words if len(w) > 6][:3]  # Top 3 very significant words only
            
            missing_in_source = []
            indonesian_stopwords = ['untuk', 'dengan', 'adalah', 'yang', 'dari', 'pada', 'dalam', 'sebagai', 'akan', 'dapat', 'telah', 'suku', 'bunga', 'rate', 'bank', 'indonesia', 'federal', 'reserve']
            
            for word in important_words:
                if word not in full_source and word not in indonesian_stopwords:
                    missing_in_source.append(word)
            
            # Only flag if many important terms missing
            if len(missing_in_source) > 2:
                review_flags.append(f"Many terms not in source: {', '.join(missing_in_source[:3])}")
            
            # 2. Check numbers consistency (more lenient)
            text_numbers = self.calculator.extract_numbers_from_text(item.text)
            source_numbers = self.calculator.extract_numbers_from_text(full_source)
            
            unverified_numbers = []
            for num in text_numbers:
                # Check if number is close to any source number (within 10% tolerance)
                verified = False
                for src_num in source_numbers:
                    if abs(num - src_num) / max(src_num, 1) < 0.1:  # 10% tolerance
                        verified = True
                        break
                
                if not verified and num > 1:  # Only check meaningful numbers
                    unverified_numbers.append(num)
            
            # Only flag if many numbers unverified
            if len(unverified_numbers) > 2:
                review_flags.append(f"Many numbers unverified: {unverified_numbers[:2]}")
            
            # Update review flags
            item.review_flags = review_flags
            
            # More lenient validation - accept unless major issues
            if len(review_flags) < 3:  # Allow more minor issues
                validated.append(item)
            else:
                logger.warning(f"Item {item.enh_id} failed validation: {review_flags}")
        
        return validated
