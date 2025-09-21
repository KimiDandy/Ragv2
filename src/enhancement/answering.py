"""
RAG answering system with intelligent routing and precise citing.
"""

import re
import json
import asyncio
from typing import List, Dict, Any, Optional, Tuple
from enum import Enum
from dataclasses import dataclass, asdict

from openai import AsyncOpenAI
from loguru import logger

from .config import EnhancementConfig
from .indexer import EnhancementIndexer
from .generator import NumericCalculator
from ..core.rate_limiter import AsyncLeakyBucket


class QueryIntent(Enum):
    """Types of query intent."""
    NUMERIC = "numeric"  # Questions about numbers, calculations
    CONCEPTUAL = "conceptual"  # Questions about concepts, procedures
    COMPARISON = "comparison"  # Comparing values or concepts
    DEFINITION = "definition"  # What is X?
    PROCEDURE = "procedure"  # How to do X?
    MIXED = "mixed"  # Combination of intents


@dataclass
class QueryRoute:
    """Routing decision for a query."""
    intent: QueryIntent
    primary_retrieval: str  # 'table', 'paragraph', 'enhancement'
    secondary_retrieval: Optional[str] = None
    requires_calculation: bool = False
    confidence: float = 0.8


@dataclass
class Answer:
    """Structured answer with citations."""
    query: str
    answer_text: str
    citations: List[str]  # List of anchor references
    source_units: List[Dict[str, Any]]
    calculations: Optional[Dict[str, Any]] = None
    confidence: float = 0.8
    route_used: Optional[QueryRoute] = None
    
    def to_dict(self) -> Dict[str, Any]:
        data = asdict(self)
        if self.route_used:
            data['route_used'] = asdict(self.route_used)
        return data


class RAGAnswering:
    """Handles question answering with routing and precise citing."""
    
    def __init__(self, config: EnhancementConfig):
        self.config = config
        self.client = AsyncOpenAI(api_key=config.openai_api_key)
        self.rate_limiter = AsyncLeakyBucket(rps=config.requests_per_second)
        self.indexer = EnhancementIndexer(config)
        self.calculator = NumericCalculator()
        
        # Keywords for intent detection
        self.numeric_keywords = {
            'berapa', 'hitung', 'total', 'jumlah', 'selisih', 'perubahan',
            'persen', 'persentase', 'nilai', 'angka', 'rate', 'bunga',
            'premi', 'biaya', 'harga', 'tarif', 'yield', 'return'
        }
        
        self.conceptual_keywords = {
            'apa', 'mengapa', 'bagaimana', 'jelaskan', 'definisi',
            'maksud', 'pengertian', 'prosedur', 'syarat', 'ketentuan'
        }
    
    async def answer(
        self,
        query: str,
        doc_id: str,
        collection_name: Optional[str] = None
    ) -> Answer:
        """
        Answer a query using enhanced RAG.
        
        Args:
            query: User's question
            doc_id: Document identifier
            collection_name: Chroma collection name
            
        Returns:
            Structured answer with citations
        """
        if not collection_name:
            collection_name = f"enhanced_{doc_id}"
        
        logger.info(f"Answering query: {query[:100]}...")
        
        # 1. Route the query
        route = await self._route_query(query)
        logger.info(f"Query routed: {route.intent.value}, primary: {route.primary_retrieval}")
        
        # 2. Retrieve relevant content
        retrieved_units = await self._retrieve_content(
            query=query,
            route=route,
            collection_name=collection_name
        )
        
        # 3. Extract and calculate if needed
        calculations = None
        if route.requires_calculation:
            calculations = await self._perform_calculations(
                query=query,
                units=retrieved_units
            )
        
        # 4. Compose answer
        answer = await self._compose_answer(
            query=query,
            route=route,
            units=retrieved_units,
            calculations=calculations
        )
        
        # 5. Extract citations
        citations = self._extract_citations(retrieved_units)
        
        return Answer(
            query=query,
            answer_text=answer,
            citations=citations,
            source_units=retrieved_units,
            calculations=calculations,
            route_used=route,
            confidence=route.confidence
        )
    
    async def _route_query(self, query: str) -> QueryRoute:
        """Determine query intent and routing strategy."""
        query_lower = query.lower()
        
        # Check for numeric intent
        numeric_score = sum(1 for kw in self.numeric_keywords if kw in query_lower)
        conceptual_score = sum(1 for kw in self.conceptual_keywords if kw in query_lower)
        
        # Determine primary intent
        if numeric_score > conceptual_score:
            intent = QueryIntent.NUMERIC
            primary = 'table'
            secondary = 'enhancement'
            requires_calc = True
        elif conceptual_score > numeric_score:
            intent = QueryIntent.CONCEPTUAL
            primary = 'paragraph'
            secondary = 'enhancement'
            requires_calc = False
        else:
            # Use LLM for complex routing
            route = await self._llm_route_query(query)
            return route
        
        # Check for specific patterns
        if 'bandingkan' in query_lower or 'perbedaan' in query_lower:
            intent = QueryIntent.COMPARISON
            requires_calc = numeric_score > 0
        elif 'apa itu' in query_lower or 'definisi' in query_lower:
            intent = QueryIntent.DEFINITION
            primary = 'enhancement'  # Glossary first
            secondary = 'paragraph'
        elif 'bagaimana' in query_lower or 'cara' in query_lower:
            intent = QueryIntent.PROCEDURE
        
        return QueryRoute(
            intent=intent,
            primary_retrieval=primary,
            secondary_retrieval=secondary,
            requires_calculation=requires_calc,
            confidence=0.85
        )
    
    async def _llm_route_query(self, query: str) -> QueryRoute:
        """Use LLM for complex query routing."""
        prompt = f"""Analyze this query and determine the routing strategy.

Query: {query}

Determine:
1. Intent: numeric, conceptual, comparison, definition, procedure, or mixed
2. Primary retrieval: table (for numbers), paragraph (for text), or enhancement (for summaries)
3. Requires calculation: true if needs math operations

Output JSON:
{{
  "intent": "...",
  "primary": "table|paragraph|enhancement",
  "secondary": "table|paragraph|enhancement|null",
  "requires_calculation": true|false
}}"""
        
        await self.rate_limiter.acquire()
        
        try:
            response = await self.client.chat.completions.create(
                model="gpt-3.5-turbo",  # Faster model for routing
                messages=[
                    {"role": "system", "content": "You are a query routing expert."},
                    {"role": "user", "content": prompt}
                ],
                temperature=0,
                max_tokens=100,
                response_format={"type": "json_object"}
            )
            
            data = json.loads(response.choices[0].message.content)
            
            intent_map = {
                'numeric': QueryIntent.NUMERIC,
                'conceptual': QueryIntent.CONCEPTUAL,
                'comparison': QueryIntent.COMPARISON,
                'definition': QueryIntent.DEFINITION,
                'procedure': QueryIntent.PROCEDURE,
                'mixed': QueryIntent.MIXED
            }
            
            return QueryRoute(
                intent=intent_map.get(data.get('intent', 'mixed'), QueryIntent.MIXED),
                primary_retrieval=data.get('primary', 'paragraph'),
                secondary_retrieval=data.get('secondary'),
                requires_calculation=data.get('requires_calculation', False),
                confidence=0.75
            )
            
        except Exception as e:
            logger.error(f"Error in LLM routing: {e}")
            # Fallback to conceptual
            return QueryRoute(
                intent=QueryIntent.CONCEPTUAL,
                primary_retrieval='paragraph',
                secondary_retrieval='enhancement',
                requires_calculation=False,
                confidence=0.5
            )
    
    async def _retrieve_content(
        self,
        query: str,
        route: QueryRoute,
        collection_name: str
    ) -> List[Dict[str, Any]]:
        """Retrieve relevant content based on routing."""
        all_results = []
        
        # Map retrieval types to unit types
        type_map = {
            'table': ['table'],
            'paragraph': ['paragraph'],
            'enhancement': ['enh_glossary', 'enh_highlight', 'enh_faq', 'enh_caption']
        }
        
        # Primary retrieval
        if route.primary_retrieval:
            unit_types = type_map.get(route.primary_retrieval, ['paragraph'])
            primary_results = self.indexer.search_by_type(
                collection_name=collection_name,
                query=query,
                unit_types=unit_types,
                top_k=self.config.retrieval_top_k
            )
            all_results.extend(primary_results)
        
        # Secondary retrieval
        if route.secondary_retrieval:
            unit_types = type_map.get(route.secondary_retrieval, ['enhancement'])
            secondary_results = self.indexer.search_by_type(
                collection_name=collection_name,
                query=query,
                unit_types=unit_types,
                top_k=5  # Fewer secondary results
            )
            all_results.extend(secondary_results)
        
        # Re-rank combined results
        reranked = self._rerank_results(query, all_results)
        
        return reranked[:self.config.retrieval_rerank_top_k]
    
    def _rerank_results(
        self,
        query: str,
        results: List[Dict[str, Any]]
    ) -> List[Dict[str, Any]]:
        """Re-rank results based on relevance."""
        # Simple re-ranking based on score and type preference
        scored_results = []
        
        for result in results:
            score = result.get('score', 0.5)
            metadata = result.get('metadata', {})
            
            # Boost derived content (enhancements)
            if metadata.get('derived', False):
                score += 0.1
            
            # Boost if query terms appear in text
            text = result.get('text', '').lower()
            query_terms = query.lower().split()
            term_matches = sum(1 for term in query_terms if term in text)
            score += term_matches * 0.05
            
            scored_results.append((score, result))
        
        # Sort by score
        scored_results.sort(key=lambda x: x[0], reverse=True)
        
        return [result for _, result in scored_results]
    
    async def _perform_calculations(
        self,
        query: str,
        units: List[Dict[str, Any]]
    ) -> Optional[Dict[str, Any]]:
        """Perform calculations on retrieved numeric data."""
        # Extract numbers from table units
        all_numbers = []
        sources = []
        
        for unit in units:
            if 'table' in unit.get('metadata', {}).get('unit_type', ''):
                text = unit.get('text', '')
                numbers = self.calculator.extract_numbers_from_text(text)
                
                for num in numbers:
                    all_numbers.append(num)
                    sources.append(unit.get('metadata', {}).get('unit_id', 'unknown'))
        
        if not all_numbers:
            return None
        
        # Determine what calculation is needed
        calculations = {
            'extracted_numbers': all_numbers,
            'sources': sources
        }
        
        query_lower = query.lower()
        
        if 'total' in query_lower or 'jumlah' in query_lower:
            calculations['total'] = sum(all_numbers)
            calculations['formatted_total'] = self.calculator.format_number(
                calculations['total'],
                currency=True
            )
        
        if 'rata-rata' in query_lower or 'average' in query_lower:
            calculations['average'] = sum(all_numbers) / len(all_numbers)
            calculations['formatted_average'] = self.calculator.format_number(
                calculations['average'],
                currency=True
            )
        
        if 'selisih' in query_lower or 'perubahan' in query_lower:
            if len(all_numbers) >= 2:
                change = self.calculator.calculate_change(all_numbers[0], all_numbers[-1])
                calculations['change'] = change
        
        return calculations
    
    async def _compose_answer(
        self,
        query: str,
        route: QueryRoute,
        units: List[Dict[str, Any]],
        calculations: Optional[Dict[str, Any]]
    ) -> str:
        """Compose the final answer."""
        # Prepare context from retrieved units
        context_parts = []
        
        for unit in units[:3]:  # Limit context size
            text = unit.get('text', '')
            metadata = unit.get('metadata', {})
            unit_type = metadata.get('unit_type', 'unknown')
            
            # Format based on type
            if 'enh_' in unit_type:
                context_parts.append(f"Enhancement: {text}")
            elif unit_type == 'table':
                context_parts.append(f"Table data: {text[:200]}...")
            else:
                context_parts.append(f"Document: {text[:300]}...")
        
        context = '\n\n'.join(context_parts)
        
        # Build prompt
        prompt = f"""Answer this question based on the provided context.

Question: {query}

Context:
{context}
"""
        
        if calculations:
            prompt += f"""
Available calculations:
{json.dumps(calculations, indent=2, ensure_ascii=False)}

Use these exact calculated values in your answer.
"""
        
        prompt += """
Provide a clear, concise answer. If the context doesn't contain enough information, say so.
For numeric answers, always show the exact values from calculations or source.
"""
        
        await self.rate_limiter.acquire()
        
        try:
            response = await self.client.chat.completions.create(
                model=self.config.gen_model,
                messages=[
                    {"role": "system", "content": self._get_answering_system_prompt()},
                    {"role": "user", "content": prompt}
                ],
                temperature=0.3,
                max_tokens=500
            )
            
            return response.choices[0].message.content
            
        except Exception as e:
            logger.error(f"Error composing answer: {e}")
            return "Maaf, terjadi kesalahan dalam menyusun jawaban."
    
    def _get_answering_system_prompt(self) -> str:
        """System prompt for answer composition."""
        return """You are a precise financial document question-answering system.

Rules:
1. Answer in Bahasa Indonesia unless the question is in English
2. Use ONLY information from the provided context
3. For numbers, use EXACTLY the values provided in calculations or source
4. Be concise but complete
5. If information is insufficient, clearly state what's missing
6. Never make up information"""
    
    def _extract_citations(self, units: List[Dict[str, Any]]) -> List[str]:
        """Extract citation anchors from retrieved units."""
        citations = []
        
        for unit in units:
            metadata = unit.get('metadata', {})
            
            # Get anchor
            anchor = metadata.get('anchor')
            if anchor:
                citations.append(anchor)
            else:
                # Construct anchor from unit_id
                unit_id = metadata.get('unit_id')
                if unit_id:
                    citations.append(f"ref://{unit_id}")
            
            # Also get source unit references for enhancements
            if metadata.get('derived', False):
                source_ids_str = metadata.get('source_unit_ids', '[]')
                try:
                    source_ids = json.loads(source_ids_str)
                    for source_id in source_ids[:2]:  # Limit citations
                        citations.append(f"ref://{source_id}")
                except:
                    pass
        
        # Remove duplicates while preserving order
        seen = set()
        unique_citations = []
        for citation in citations:
            if citation not in seen:
                seen.add(citation)
                unique_citations.append(citation)
        
        return unique_citations[:5]  # Limit to 5 citations
