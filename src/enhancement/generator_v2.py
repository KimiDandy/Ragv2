"""
Enhancement Generator V2 - Context-Aware Generation dengan Data Precision
Generates enhancements yang mengekstrak informasi tersirat dari dokumen
"""

import json
import hashlib
import asyncio
from typing import List, Dict, Any, Optional, Tuple
from datetime import datetime
import re

from openai import AsyncOpenAI
from loguru import logger
import tiktoken
import numpy as np

from .config import EnhancementConfig
from .enhancement_types import EnhancementCandidate, EnhancementType
from ..core.rate_limiter import AsyncLeakyBucket
from .prompts_universal import UNIVERSAL_GENERATION_SYSTEM as SYSTEM_PROMPT, UNIVERSAL_GENERATION_USER as USER_PROMPT


class DataProcessor:
    """Process and prepare data for enhancement generation"""
    
    @staticmethod
    def extract_table_data(tables: List[Dict]) -> Dict[str, Any]:
        """Extract structured data from tables"""
        structured_data = {
            'numerical_tables': [],
            'text_tables': [],
            'mixed_tables': []
        }
        
        for table in tables:
            parsed = table.get('parsed_data', {})
            if not parsed:
                continue
            
            # Analyze table type
            headers = parsed.get('headers', [])
            rows = parsed.get('rows', [])
            
            if not rows:
                continue
            
            # Check if numerical
            numerical_cols = 0
            for header in headers:
                # Sample first row to check data type
                sample_value = rows[0].get(header, '')
                if DataProcessor._is_numerical(sample_value):
                    numerical_cols += 1
            
            # Categorize table
            if numerical_cols > len(headers) / 2:
                structured_data['numerical_tables'].append({
                    'headers': headers,
                    'data': rows,
                    'unit_id': table.get('unit_id')
                })
            elif numerical_cols > 0:
                structured_data['mixed_tables'].append({
                    'headers': headers,
                    'data': rows,
                    'unit_id': table.get('unit_id')
                })
            else:
                structured_data['text_tables'].append({
                    'headers': headers,
                    'data': rows,
                    'unit_id': table.get('unit_id')
                })
        
        return structured_data
    
    @staticmethod
    def _is_numerical(value: str) -> bool:
        """Check if a value is numerical"""
        # Remove common formatting
        clean_value = re.sub(r'[Rp,.\s%]', '', value)
        clean_value = re.sub(r'(ribu|juta|miliar)', '', clean_value)
        
        try:
            float(clean_value)
            return True
        except:
            return False
    
    @staticmethod
    def extract_numerical_values(content: str) -> List[Dict[str, Any]]:
        """Extract all numerical values with context"""
        values = []
        
        # Currency values
        currency_pattern = r'(Rp|IDR|USD)\s*([\d,.]+)\s*(?:(ribu|juta|miliar))?'
        for match in re.finditer(currency_pattern, content):
            amount = match.group(2).replace(',', '').replace('.', '')
            multiplier = 1
            if match.group(3):
                if match.group(3) == 'ribu':
                    multiplier = 1000
                elif match.group(3) == 'juta':
                    multiplier = 1000000
                elif match.group(3) == 'miliar':
                    multiplier = 1000000000
            
            try:
                value = float(amount) * multiplier
                values.append({
                    'type': 'currency',
                    'raw': match.group(0),
                    'value': value,
                    'currency': match.group(1)
                })
            except:
                pass
        
        # Percentages
        percentage_pattern = r'(\d+(?:\.\d+)?)\s*%'
        for match in re.finditer(percentage_pattern, content):
            try:
                value = float(match.group(1))
                values.append({
                    'type': 'percentage',
                    'raw': match.group(0),
                    'value': value / 100  # Convert to decimal
                })
            except:
                pass
        
        # Time periods
        period_pattern = r'(\d+)\s*(tahun|bulan|hari)'
        for match in re.finditer(period_pattern, content):
            try:
                value = int(match.group(1))
                unit = match.group(2)
                values.append({
                    'type': 'period',
                    'raw': match.group(0),
                    'value': value,
                    'unit': unit
                })
            except:
                pass
        
        return values


class FormulaDiscovery:
    """Discover formulas from data patterns"""
    
    @staticmethod
    def find_linear_relationship(x_values: List[float], y_values: List[float]) -> Optional[Dict]:
        """Find linear relationship y = mx + b"""
        if len(x_values) != len(y_values) or len(x_values) < 2:
            return None
        
        try:
            # Calculate linear regression
            x = np.array(x_values)
            y = np.array(y_values)
            
            # Compute coefficients
            n = len(x)
            sum_x = np.sum(x)
            sum_y = np.sum(y)
            sum_xy = np.sum(x * y)
            sum_x2 = np.sum(x ** 2)
            
            # Calculate slope (m) and intercept (b)
            m = (n * sum_xy - sum_x * sum_y) / (n * sum_x2 - sum_x ** 2)
            b = (sum_y - m * sum_x) / n
            
            # Calculate R-squared
            y_pred = m * x + b
            ss_tot = np.sum((y - np.mean(y)) ** 2)
            ss_res = np.sum((y - y_pred) ** 2)
            r_squared = 1 - (ss_res / ss_tot) if ss_tot > 0 else 0
            
            return {
                'type': 'linear',
                'formula': f'y = {m:.4f}x + {b:.4f}',
                'slope': m,
                'intercept': b,
                'r_squared': r_squared,
                'confidence': r_squared
            }
            
        except Exception as e:
            logger.error(f"Error in linear regression: {e}")
            return None
    
    @staticmethod
    def find_exponential_relationship(x_values: List[float], y_values: List[float]) -> Optional[Dict]:
        """Find exponential relationship y = a * e^(bx)"""
        if len(x_values) != len(y_values) or len(x_values) < 2:
            return None
        
        try:
            # Take log of y values
            y_log = [np.log(y) if y > 0 else None for y in y_values]
            
            # Remove None values
            valid_pairs = [(x, yl) for x, yl in zip(x_values, y_log) if yl is not None]
            if len(valid_pairs) < 2:
                return None
            
            x_valid = [p[0] for p in valid_pairs]
            y_log_valid = [p[1] for p in valid_pairs]
            
            # Linear regression on log values
            linear_result = FormulaDiscovery.find_linear_relationship(x_valid, y_log_valid)
            
            if linear_result and linear_result['r_squared'] > 0.8:
                a = np.exp(linear_result['intercept'])
                b = linear_result['slope']
                
                return {
                    'type': 'exponential',
                    'formula': f'y = {a:.4f} * e^({b:.4f}x)',
                    'a': a,
                    'b': b,
                    'r_squared': linear_result['r_squared'],
                    'confidence': linear_result['r_squared']
                }
            
            return None
            
        except Exception as e:
            logger.error(f"Error in exponential regression: {e}")
            return None
    
    @staticmethod
    def find_compound_interest_formula(data_points: List[Tuple[float, float, float]]) -> Optional[Dict]:
        """Find compound interest formula from (principal, time, amount) data"""
        if len(data_points) < 2:
            return None
        
        try:
            # Estimate interest rate
            rates = []
            for principal, time, amount in data_points:
                if principal > 0 and time > 0:
                    # A = P(1 + r)^t => r = (A/P)^(1/t) - 1
                    rate = (amount / principal) ** (1 / time) - 1
                    rates.append(rate)
            
            if rates:
                avg_rate = np.mean(rates)
                std_rate = np.std(rates)
                
                # Check consistency
                if std_rate < 0.01:  # Less than 1% standard deviation
                    return {
                        'type': 'compound_interest',
                        'formula': f'A = P * (1 + {avg_rate:.4f})^t',
                        'rate': avg_rate,
                        'rate_percentage': avg_rate * 100,
                        'confidence': 1 - (std_rate / avg_rate) if avg_rate > 0 else 0
                    }
            
            return None
            
        except Exception as e:
            logger.error(f"Error in compound interest discovery: {e}")
            return None


class EnhancementGeneratorV2:
    """Generate context-aware enhancements with data precision"""
    
    def __init__(self, config: EnhancementConfig):
        self.config = config
        self.client = AsyncOpenAI(api_key=config.openai_api_key)
        self.rate_limiter = AsyncLeakyBucket(rps=config.requests_per_second)
        self.data_processor = DataProcessor()
        self.formula_discovery = FormulaDiscovery()
        
        # Token counting
        self.encoder = tiktoken.encoding_for_model("gpt-4")
    
    async def generate_enhancements(
        self,
        candidates: List[EnhancementCandidate],
        units_metadata: List[Dict[str, Any]],
        doc_id: str
    ) -> Tuple[List[Dict[str, Any]], Dict[str, Any]]:
        """
        Generate enhancement content for candidates with full context
        
        Returns:
            Tuple of (enhancements, generation_metrics)
        """
        logger.info(f"Generating enhancements for {len(candidates)} candidates")
        
        # Process in micro-batches
        batch_size = self.config.gen_microbatch_size
        all_enhancements = []
        
        for i in range(0, len(candidates), batch_size):
            batch = candidates[i:i + batch_size]
            logger.info(f"Processing batch {i // batch_size + 1} ({len(batch)} candidates)")
            
            # Generate enhancements for batch
            batch_enhancements = await self._generate_batch(batch, units_metadata, doc_id)
            all_enhancements.extend(batch_enhancements)
        
        # Prepare metrics
        metrics = {
            'total_candidates': len(candidates),
            'total_generated': len(all_enhancements),
            'success_rate': len(all_enhancements) / len(candidates) if candidates else 0,
            'enhancements_by_type': self._count_by_type(all_enhancements)
        }
        
        logger.info(f"Generation complete: {len(all_enhancements)} enhancements")
        
        return all_enhancements, metrics
    
    async def _generate_batch(
        self,
        batch: List[EnhancementCandidate],
        units_metadata: List[Dict[str, Any]],
        doc_id: str
    ) -> List[Dict[str, Any]]:
        """Generate enhancements for a batch of candidates"""
        enhancements = []
        
        for candidate in batch:
            try:
                # Prepare context data
                context = self._prepare_context(candidate, units_metadata)
                
                # Try algorithmic generation first for certain types
                algorithmic_result = await self._try_algorithmic_generation(
                    candidate, context
                )
                
                if algorithmic_result:
                    enhancements.append(algorithmic_result)
                    logger.info(f"Generated {candidate.enhancement_type} algorithmically")
                else:
                    # Fall back to LLM generation
                    llm_result = await self._generate_with_llm(candidate, context)
                    if llm_result:
                        enhancements.append(llm_result)
                        logger.info(f"Generated {candidate.enhancement_type} with LLM")
                    
            except Exception as e:
                logger.error(f"Failed to generate enhancement: {e}")
                continue
        
        return enhancements
    
    def _prepare_context(
        self,
        candidate: EnhancementCandidate,
        units_metadata: List[Dict[str, Any]]
    ) -> Dict[str, Any]:
        """Prepare full context for enhancement generation"""
        
        # Extract referenced units
        referenced_units = []
        for ref in candidate.source_references:
            unit_id = ref.get('unit_id')
            if unit_id:
                for unit in units_metadata:
                    if unit.get('unit_id') == unit_id:
                        referenced_units.append(unit)
                        break
        
        # Extract tables from references
        tables = []
        for unit in referenced_units:
            if unit.get('unit_type') == 'table':
                tables.append({
                    'unit_id': unit.get('unit_id'),
                    'content': unit.get('content'),
                    'parsed_data': self.data_processor._parse_table(unit.get('content', ''))
                })
        
        # Process table data
        table_data = self.data_processor.extract_table_data(tables)
        
        # Extract numerical values from all referenced content
        all_content = ' '.join([unit.get('content', '') for unit in referenced_units])
        numerical_values = self.data_processor.extract_numerical_values(all_content)
        
        return {
            'referenced_units': referenced_units,
            'tables': tables,
            'table_data': table_data,
            'numerical_values': numerical_values,
            'raw_content': all_content[:10000]  # Limit for prompt
        }
    
    async def _try_algorithmic_generation(
        self,
        candidate: EnhancementCandidate,
        context: Dict[str, Any]
    ) -> Optional[Dict[str, Any]]:
        """Try to generate enhancement algorithmically without LLM"""
        
        if candidate.enhancement_type == EnhancementType.FORMULA_DISCOVERY:
            return self._generate_formula_algorithmically(candidate, context)
        
        elif candidate.enhancement_type == EnhancementType.PATTERN_RECOGNITION:
            return self._generate_pattern_algorithmically(candidate, context)
        
        return None
    
    def _generate_formula_algorithmically(
        self,
        candidate: EnhancementCandidate,
        context: Dict[str, Any]
    ) -> Optional[Dict[str, Any]]:
        """Generate formula enhancement algorithmically"""
        
        # Try to extract data points from tables
        numerical_tables = context['table_data'].get('numerical_tables', [])
        
        if not numerical_tables:
            return None
        
        for table in numerical_tables:
            # Look for x-y relationships
            headers = table['headers']
            data = table['data']
            
            # Find potential x and y columns
            for i, x_header in enumerate(headers[:-1]):
                for j, y_header in enumerate(headers[i+1:], i+1):
                    x_values = []
                    y_values = []
                    
                    for row in data:
                        x_val = self._parse_number(row.get(x_header, ''))
                        y_val = self._parse_number(row.get(y_header, ''))
                        
                        if x_val is not None and y_val is not None:
                            x_values.append(x_val)
                            y_values.append(y_val)
                    
                    if len(x_values) >= 3:  # Need at least 3 points
                        # Try linear relationship
                        linear = self.formula_discovery.find_linear_relationship(x_values, y_values)
                        if linear and linear['r_squared'] > 0.9:
                            return self._format_formula_enhancement(
                                candidate, linear, x_header, y_header, x_values, y_values
                            )
                        
                        # Try exponential relationship
                        exponential = self.formula_discovery.find_exponential_relationship(x_values, y_values)
                        if exponential and exponential['r_squared'] > 0.9:
                            return self._format_formula_enhancement(
                                candidate, exponential, x_header, y_header, x_values, y_values
                            )
        
        return None
    
    def _generate_pattern_algorithmically(
        self,
        candidate: EnhancementCandidate,
        context: Dict[str, Any]
    ) -> Optional[Dict[str, Any]]:
        """Generate pattern recognition enhancement algorithmically"""
        
        # Extract time series data if available
        numerical_values = context['numerical_values']
        
        # Group by type
        periods = [v for v in numerical_values if v['type'] == 'period']
        currencies = [v for v in numerical_values if v['type'] == 'currency']
        
        if periods and currencies:
            # Try to match periods with values
            if len(periods) == len(currencies):
                x_values = [p['value'] for p in periods]
                y_values = [c['value'] for c in currencies]
                
                # Find pattern
                linear = self.formula_discovery.find_linear_relationship(x_values, y_values)
                
                if linear and linear['r_squared'] > 0.85:
                    return {
                        'type': 'pattern_recognition',
                        'title': candidate.title,
                        'pattern': linear,
                        'data_points': list(zip(x_values, y_values)),
                        'prediction_formula': linear['formula'],
                        'confidence': linear['confidence']
                    }
        
        return None
    
    async def _generate_with_llm(
        self,
        candidate: EnhancementCandidate,
        context: Dict[str, Any]
    ) -> Optional[Dict[str, Any]]:
        """Generate enhancement using LLM"""
        
        # Prepare prompt
        user_prompt = USER_PROMPT.format(
            enhancement_type=candidate.enhancement_type.value,
            title=candidate.title,
            target_info=candidate.target_info,
            rationale=candidate.rationale,
            source_data=context['raw_content'][:5000],
            tables_data=json.dumps(context['table_data'], ensure_ascii=False)[:3000],
            numerical_data=json.dumps(context['numerical_values'], ensure_ascii=False)[:2000],
            calculation_examples=json.dumps(candidate.required_context.get('calculation_examples', []))[:2000]
        )
        
        # Call LLM
        try:
            await self.rate_limiter.acquire()
            response = await self.client.chat.completions.create(
                    model=self.config.gen_model,
                    messages=[
                        {"role": "system", "content": SYSTEM_PROMPT},
                        {"role": "user", "content": user_prompt}
                    ],
                    temperature=0.2,  # Low temperature for precision
                    max_tokens=self.config.max_generation_tokens,
                    response_format={"type": "json_object"}
                )
            
            content = response.choices[0].message.content
            result = json.loads(content)
            
            return result
            
        except Exception as e:
            logger.error(f"LLM generation failed: {e}")
            return None
    
    def _parse_number(self, value: str) -> Optional[float]:
        """Parse number from string"""
        try:
            # Remove formatting
            clean = re.sub(r'[Rp,.\s%]', '', value)
            clean = re.sub(r'(ribu|juta|miliar)', '', clean)
            return float(clean)
        except:
            return None
    
    def _format_formula_enhancement(
        self,
        candidate: EnhancementCandidate,
        formula: Dict,
        x_header: str,
        y_header: str,
        x_values: List[float],
        y_values: List[float]
    ) -> Dict[str, Any]:
        """Format formula discovery as enhancement"""
        return {
            'enhancement_type': 'formula_discovery',
            'title': candidate.title,
            'content': {
                'formula': formula['formula'],
                'formula_type': formula['type'],
                'parameters': {
                    'x': x_header,
                    'y': y_header
                },
                'derivation': f"Ditemukan dari analisis regresi {len(x_values)} data points",
                'r_squared': formula.get('r_squared', 0),
                'data_points': list(zip(x_values, y_values))[:10]  # Sample points
            },
            'source_verification': {
                'data_points_used': len(x_values),
                'calculations_shown': True,
                'confidence_level': formula.get('confidence', 0.8)
            },
            'application_examples': [
                {
                    'scenario': f"Prediksi {y_header} untuk {x_header} = {max(x_values) + 1}",
                    'calculation': f"Menggunakan formula: {formula['formula']}",
                    'result': self._apply_formula(formula, max(x_values) + 1)
                }
            ]
        }
    
    def _apply_formula(self, formula: Dict, x_value: float) -> str:
        """Apply formula to get prediction"""
        try:
            if formula['type'] == 'linear':
                y = formula['slope'] * x_value + formula['intercept']
                return f"{y:.2f}"
            elif formula['type'] == 'exponential':
                y = formula['a'] * np.exp(formula['b'] * x_value)
                return f"{y:.2f}"
            else:
                return "N/A"
        except:
            return "Error"
    
    def _count_by_type(self, enhancements: List[Dict]) -> Dict[str, int]:
        """Count enhancements by type"""
        counts = {}
        for enh in enhancements:
            enh_type = enh.get('enhancement_type', 'unknown')
            counts[enh_type] = counts.get(enh_type, 0) + 1
        return counts
