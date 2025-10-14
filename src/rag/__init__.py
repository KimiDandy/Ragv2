"""
RAG (Retrieval-Augmented Generation) Module

Professional RAG implementation with retrieval, caching, and answer generation.

Active retrievers:
- build_rag_chain: Build LangChain RAG chain
- answer_with_sources: Execute RAG with token tracking
- CustomPineconeRetriever: Direct Pinecone retrieval (in custom_pinecone_retriever.py)
"""

from .retriever import build_rag_chain, answer_with_sources

__all__ = ['build_rag_chain', 'answer_with_sources']
