"""
Vectorization and indexing for enhanced documents.
"""

import json
import hashlib
from typing import List, Dict, Any, Optional
from pathlib import Path
import asyncio

import chromadb
from chromadb.config import Settings
import openai
from openai import AsyncOpenAI
from loguru import logger
import numpy as np

from .config import EnhancementConfig
from ..core.rate_limiter import AsyncLeakyBucket


class EnhancementIndexer:
    """Indexes enhanced documents in Chroma for RAG retrieval."""
    
    def __init__(self, config: EnhancementConfig):
        self.config = config
        self.client = AsyncOpenAI(api_key=config.openai_api_key)
        self.rate_limiter = AsyncLeakyBucket(rps=config.requests_per_second)
        
        # Initialize Chroma client
        self.chroma_client = chromadb.HttpClient(
            host='localhost',
            port=8001,
            settings=Settings(anonymized_telemetry=False)
        )
    
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
        
        # Get or create collection
        try:
            collection = self.chroma_client.get_collection(collection_name)
            logger.info(f"Using existing collection: {collection_name}")
        except:
            collection = self.chroma_client.create_collection(
                name=collection_name,
                metadata={"doc_id": doc_id, "type": "enhanced"}
            )
            logger.info(f"Created new collection: {collection_name}")
        
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
        
        # Index to Chroma in batches
        batch_size = 100
        total_indexed = 0
        
        for i in range(0, len(all_documents), batch_size):
            batch_end = min(i + batch_size, len(all_documents))
            
            try:
                collection.upsert(
                    ids=all_ids[i:batch_end],
                    documents=all_documents[i:batch_end],
                    metadatas=all_metadatas[i:batch_end],
                    embeddings=embeddings[i:batch_end] if embeddings else None
                )
                total_indexed += (batch_end - i)
                logger.info(f"Indexed batch {i//batch_size + 1}: {batch_end - i} documents")
                
            except Exception as e:
                logger.error(f"Error indexing batch {i//batch_size + 1}: {e}")
        
        # Create indices for efficient retrieval
        self._create_indices(collection)
        
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
    
    def _create_indices(self, collection):
        """Create indices for efficient retrieval."""
        # Note: Chroma automatically creates indices
        # This is a placeholder for any custom index configuration
        pass
    
    def search(
        self,
        collection_name: str,
        query: str,
        filter_dict: Optional[Dict[str, Any]] = None,
        top_k: int = 10
    ) -> List[Dict[str, Any]]:
        """
        Search the indexed collection.
        
        Args:
            collection_name: Name of the collection
            query: Search query
            filter_dict: Optional metadata filters
            top_k: Number of results to return
            
        Returns:
            List of search results with metadata
        """
        try:
            collection = self.chroma_client.get_collection(collection_name)
            
            # Prepare where clause for filtering
            where_clause = filter_dict if filter_dict else None
            
            # Perform search
            results = collection.query(
                query_texts=[query],
                n_results=top_k,
                where=where_clause,
                include=['documents', 'metadatas', 'distances']
            )
            
            # Format results
            formatted_results = []
            if results['ids'] and len(results['ids']) > 0:
                for i in range(len(results['ids'][0])):
                    formatted_results.append({
                        'id': results['ids'][0][i],
                        'text': results['documents'][0][i],
                        'metadata': results['metadatas'][0][i],
                        'score': 1.0 - results['distances'][0][i]  # Convert distance to similarity
                    })
            
            return formatted_results
            
        except Exception as e:
            logger.error(f"Error searching collection {collection_name}: {e}")
            return []
    
    def search_by_type(
        self,
        collection_name: str,
        query: str,
        unit_types: List[str],
        top_k: int = 10
    ) -> List[Dict[str, Any]]:
        """Search filtering by unit types."""
        # Create filter for unit types
        filter_dict = {
            'unit_type': {'$in': unit_types}
        }
        
        return self.search(collection_name, query, filter_dict, top_k)
    
    def get_unit_by_id(
        self,
        collection_name: str,
        unit_id: str
    ) -> Optional[Dict[str, Any]]:
        """Retrieve a specific unit by ID."""
        try:
            collection = self.chroma_client.get_collection(collection_name)
            
            result = collection.get(
                ids=[unit_id],
                include=['documents', 'metadatas']
            )
            
            if result['ids']:
                return {
                    'id': result['ids'][0],
                    'text': result['documents'][0] if result['documents'] else '',
                    'metadata': result['metadatas'][0] if result['metadatas'] else {}
                }
            
            return None
            
        except Exception as e:
            logger.error(f"Error getting unit {unit_id}: {e}")
            return None
