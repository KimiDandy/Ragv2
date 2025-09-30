"""
Vectorization and indexing for enhanced documents.
"""

import json
import hashlib
from typing import List, Dict, Any, Optional
from pathlib import Path
import asyncio

from pinecone import Pinecone
import openai
from openai import AsyncOpenAI
from loguru import logger
import numpy as np

from .config import EnhancementConfig
from ..core.rate_limiter import AsyncLeakyBucket


class EnhancementIndexer:
    """Indexes enhanced documents in Pinecone for RAG retrieval."""
    
    def __init__(self, config: EnhancementConfig):
        self.config = config
        self.client = AsyncOpenAI(api_key=config.openai_api_key)
        self.rate_limiter = AsyncLeakyBucket(rps=config.requests_per_second)
        
        # Initialize Pinecone client
        pc = Pinecone(api_key=config.pinecone_api_key)
        self.pinecone_index = pc.Index(config.pinecone_index_name)
    
    async def index_enhanced_document(
        self,
        doc_id: str,
        units_metadata: List[Dict[str, Any]],
        enhancements: List[Dict[str, Any]],
        collection_name: Optional[str] = None
    ) -> Dict[str, Any]:
        """
        Index both original units and enhancements.
        
        Args:
            doc_id: Document identifier
            units_metadata: Original units from extraction
            enhancements: Enhancement items
            collection_name: Chroma collection name
            
        Returns:
            Indexing metrics
        """
        if not collection_name:
            collection_name = f"enhanced_{doc_id}"
        
        logger.info(f"Indexing {doc_id} to collection {collection_name}")
        
        # Clean up existing vectors for this document
        try:
            # Delete by metadata filter (more efficient for Pinecone v3+)
            self.pinecone_index.delete(
                filter={"doc_id": doc_id}
            )
            logger.info(f"Cleaned up existing vectors for doc {doc_id}")
        except Exception as e:
            logger.warning(f"Error cleaning up existing vectors: {e}")
        
        # Prepare documents for indexing
        all_documents = []
        all_metadatas = []
        all_ids = []
        
        # 1. Index original units
        for unit in units_metadata:
            doc = self._prepare_unit_document(unit, doc_id, derived=False)
            all_documents.append(doc['text'])
            all_metadatas.append(doc['metadata'])
            all_ids.append(doc['id'])
        
        # 2. Index enhancements
        for enh in enhancements:
            doc = self._prepare_enhancement_document(enh, doc_id)
            all_documents.append(doc['text'])
            all_metadatas.append(doc['metadata'])
            all_ids.append(doc['id'])
        
        logger.info(f"Prepared {len(all_documents)} documents for indexing")
        
        # Generate embeddings in batches
        embeddings = await self._generate_embeddings_batch(all_documents)
        
        # Index to Pinecone in batches
        batch_size = 100
        total_indexed = 0
        
        for i in range(0, len(all_documents), batch_size):
            batch_end = min(i + batch_size, len(all_documents))
            
            try:
                # Prepare vectors for Pinecone
                vectors_to_upsert = []
                for j in range(i, batch_end):
                    vectors_to_upsert.append({
                        "id": all_ids[j],
                        "values": embeddings[j] if embeddings else None,
                        "metadata": {
                            **all_metadatas[j],
                            "text": all_documents[j]  # Store text content in metadata
                        }
                    })
                
                self.pinecone_index.upsert(vectors=vectors_to_upsert)
                total_indexed += (batch_end - i)
                logger.info(f"Indexed batch {i//batch_size + 1}: {batch_end - i} documents")
                
            except Exception as e:
                logger.error(f"Error indexing batch {i//batch_size + 1}: {e}")
        
        metrics = {
            "doc_id": doc_id,
            "collection_name": collection_name,
            "total_units": len(units_metadata),
            "total_enhancements": len(enhancements),
            "total_indexed": total_indexed,
            "embedding_model": self.config.embedding_model
        }
        
        logger.info(f"Indexing complete: {metrics}")
        
        return metrics
    
    def _prepare_unit_document(
        self,
        unit: Dict[str, Any],
        doc_id: str,
        derived: bool = False
    ) -> Dict[str, Any]:
        """Prepare an original unit for indexing."""
        unit_id = unit.get('unit_id', '')
        content = unit.get('content', '')
        
        # Create searchable text
        text = content
        
        # Prepare metadata
        metadata = {
            'doc_id': doc_id,
            'unit_id': unit_id,
            'unit_type': unit.get('unit_type', 'unknown'),
            'page': unit.get('page', 0),
            'derived': derived,
            'bbox': json.dumps(unit.get('bbox', [])),
            'anchor': unit.get('anchor', f'ref://{unit_id}')
        }
        
        # Add section if available
        if 'section' in unit:
            metadata['section'] = unit['section']
        
        return {
            'id': unit_id,
            'text': text,
            'metadata': metadata
        }
    
    def _prepare_enhancement_document(
        self,
        enhancement: Dict[str, Any],
        doc_id: str
    ) -> Dict[str, Any]:
        """Prepare an enhancement for indexing."""
        enh_id = enhancement.get('enh_id', '')
        title = enhancement.get('title', '')
        text = enhancement.get('text', '')
        
        # Create searchable text (combine title and text)
        searchable_text = f"{title}: {text}" if title else text
        
        # Prepare metadata
        metadata = {
            'doc_id': doc_id,
            'unit_id': enh_id,
            'unit_type': f"enh_{enhancement.get('type', 'unknown')}",
            'page': min(enhancement.get('pages', [0])) if enhancement.get('pages') else 0,
            'derived': True,
            'source_unit_ids': json.dumps(enhancement.get('source_unit_ids', [])),
            'section': enhancement.get('section', 'Enhancement'),
            'anchor': f'ref://{enh_id}',
            'confidence': enhancement.get('confidence', 0.8)
        }
        
        # Add as_of_date if present
        if enhancement.get('as_of_date'):
            metadata['as_of_date'] = enhancement['as_of_date']
        
        # Add server calculations if present
        if enhancement.get('server_calcs'):
            metadata['has_calculations'] = True
        
        return {
            'id': enh_id,
            'text': searchable_text,
            'metadata': metadata
        }
    
    async def _generate_embeddings_batch(
        self,
        texts: List[str]
    ) -> Optional[List[List[float]]]:
        """Generate embeddings for a batch of texts."""
        if not texts:
            return None
        
        logger.info(f"Generating embeddings for {len(texts)} texts")
        
        embeddings = []
        batch_size = self.config.embedding_batch_size
        
        for i in range(0, len(texts), batch_size):
            batch = texts[i:i + batch_size]
            
            # Clean texts (remove excessive whitespace, limit length)
            cleaned_batch = []
            for text in batch:
                # Limit to ~8000 tokens (rough estimate)
                if len(text) > 30000:
                    text = text[:30000] + "..."
                cleaned_batch.append(text.replace('\n', ' ').strip())
            
            await self.rate_limiter.acquire()
            
            try:
                response = await self.client.embeddings.create(
                    model=self.config.embedding_model,
                    input=cleaned_batch
                )
                
                batch_embeddings = [e.embedding for e in response.data]
                embeddings.extend(batch_embeddings)
                
                logger.info(f"Generated embeddings for batch {i//batch_size + 1}")
                
            except Exception as e:
                logger.error(f"Error generating embeddings: {e}")
                # Return None to use Chroma's default embedding
                return None
        
        return embeddings
    
    def _create_indices(self, index):
        """Create indices for efficient retrieval."""
        # Note: Pinecone automatically creates indices
        # This is a placeholder for any custom index configuration
        pass
    
    async def search(
        self,
        doc_id: str,
        query: str,
        filter_dict: Optional[Dict[str, Any]] = None,
        top_k: int = 10
    ) -> List[Dict[str, Any]]:
        """
        Search the indexed vectors in Pinecone.
        
        Args:
            doc_id: Document ID to search within
            query: Search query
            filter_dict: Optional metadata filters
            top_k: Number of results to return
            
        Returns:
            List of search results with metadata
        """
        try:
            # Generate query embedding
            async with self.rate_limiter:
                response = await self.client.embeddings.create(
                    model=self.config.embedding_model,
                    input=query
                )
                query_embedding = response.data[0].embedding
            
            # Prepare filter combining doc_id with additional filters
            filter_condition = {"doc_id": doc_id}
            if filter_dict:
                filter_condition.update(filter_dict)
            
            # Perform search
            results = self.pinecone_index.query(
                vector=query_embedding,
                top_k=top_k,
                filter=filter_condition,
                include_metadata=True
            )
            
            # Format results
            formatted_results = []
            for match in results['matches']:
                formatted_results.append({
                    'id': match['id'],
                    'text': match['metadata'].get('text', ''),
                    'metadata': match['metadata'],
                    'score': match['score']
                })
            
            return formatted_results
            
        except Exception as e:
            logger.error(f"Error searching vectors for doc {doc_id}: {e}")
            return []
    
    async def search_by_type(
        self,
        doc_id: str,
        query: str,
        unit_types: List[str],
        top_k: int = 10
    ) -> List[Dict[str, Any]]:
        """Search filtering by unit types."""
        # Create filter for unit types
        filter_dict = {
            'unit_type': {"$in": unit_types}
        }
        
        return await self.search(doc_id, query, filter_dict, top_k)
    
    def get_unit_by_id(
        self,
        unit_id: str
    ) -> Optional[Dict[str, Any]]:
        """Retrieve a specific unit by ID."""
        try:
            # Query Pinecone by specific ID
            result = self.pinecone_index.fetch(ids=[unit_id])
            
            if result['vectors'] and unit_id in result['vectors']:
                vector_data = result['vectors'][unit_id]
                return {
                    'id': unit_id,
                    'text': vector_data['metadata'].get('text', ''),
                    'metadata': vector_data['metadata']
                }
            
            return None
            
        except Exception as e:
            logger.error(f"Error retrieving unit {unit_id}: {e}")
            return None
