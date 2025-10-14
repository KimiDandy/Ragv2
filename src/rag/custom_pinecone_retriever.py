"""
Custom Pinecone Retriever - Direct Implementation
=================================================

Bypasses LangChain PineconeVectorStore wrapper which has namespace issues.
Directly uses Pinecone client for reliable retrieval.
"""

from typing import List, Dict, Any
from langchain_core.documents import Document
from langchain_core.retrievers import BaseRetriever
from langchain_core.embeddings import Embeddings
from loguru import logger


class CustomPineconeRetriever(BaseRetriever):
    """
    Custom retriever that directly uses Pinecone client.
    
    LangChain's PineconeVectorStore has issues with namespace handling
    in as_retriever() method. This custom retriever bypasses that by
    directly calling Pinecone's query API.
    """
    
    pinecone_index: Any
    embeddings: Embeddings
    namespace: str
    k: int = 5
    filter: Dict[str, Any] = {}
    
    class Config:
        arbitrary_types_allowed = True
    
    def _get_relevant_documents(self, query: str) -> List[Document]:
        """
        Retrieve documents using direct Pinecone query.
        
        Args:
            query: User's question
            
        Returns:
            List of Document objects with metadata
        """
        logger.info(f"[CustomRetriever] Retrieving documents for query: '{query[:50]}...'")
        logger.info(f"[CustomRetriever] Namespace: {self.namespace}, k={self.k}")
        logger.info(f"[CustomRetriever] Filter: {self.filter}")
        
        try:
            # Generate query embedding
            query_embedding = self.embeddings.embed_query(query)
            logger.info(f"[CustomRetriever] Generated embedding ({len(query_embedding)} dims)")
            
            # Direct Pinecone query
            results = self.pinecone_index.query(
                namespace=self.namespace,
                vector=query_embedding,
                top_k=self.k,
                filter=self.filter if self.filter else None,
                include_metadata=True
            )
            
            matches = results.get('matches', [])
            logger.info(f"[CustomRetriever] Retrieved {len(matches)} documents from Pinecone")
            
            # Convert to LangChain Documents
            documents = []
            for match in matches:
                metadata = match.get('metadata', {})
                
                # Extract text from metadata (stored with key "text")
                text = metadata.get('text', '')
                
                # Remove 'text' from metadata to avoid duplication
                clean_metadata = {k: v for k, v in metadata.items() if k != 'text'}
                clean_metadata['score'] = match.get('score', 0.0)
                clean_metadata['id'] = match.get('id', '')
                
                doc = Document(
                    page_content=text,
                    metadata=clean_metadata
                )
                documents.append(doc)
                
                # Log first doc for debugging
                if len(documents) == 1:
                    logger.info(f"[CustomRetriever] First doc preview: {text[:100]}...")
            
            logger.info(f"[CustomRetriever] Successfully created {len(documents)} Document objects")
            return documents
            
        except Exception as e:
            logger.error(f"[CustomRetriever] Error during retrieval: {e}")
            logger.exception(e)
            return []
    
    async def _aget_relevant_documents(self, query: str) -> List[Document]:
        """Async version - for now just call sync version"""
        return self._get_relevant_documents(query)


def create_custom_filtered_retriever(
    pinecone_index: Any,
    embeddings: Embeddings,
    namespace: str,
    doc_id: str | None = None,
    version: str = "v2",
    k: int = 15
) -> CustomPineconeRetriever:
    """
    Create custom retriever with filter.
    
    Args:
        pinecone_index: Direct Pinecone Index object
        embeddings: Embeddings function
        namespace: Pinecone namespace to query
        doc_id: Document ID for filtering (None = search all documents in namespace)
        version: Version for filtering (v1/v2)
        k: Number of documents to retrieve (increased to 15 for better coverage)
        
    Returns:
        CustomPineconeRetriever instance
    """
    # Build filter dict - if doc_id is None, search ALL documents in namespace
    filter_dict = {"version": version}
    if doc_id is not None:
        filter_dict["source_document"] = doc_id
    
    logger.info(f"[CustomRetriever] Creating retriever:")
    logger.info(f"  - Namespace: {namespace}")
    logger.info(f"  - Filter: {filter_dict}")
    logger.info(f"  - k: {k}")
    
    retriever = CustomPineconeRetriever(
        pinecone_index=pinecone_index,
        embeddings=embeddings,
        namespace=namespace,
        k=k,
        filter=filter_dict
    )
    
    return retriever
