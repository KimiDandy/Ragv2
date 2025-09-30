"""
Vectorization Module - Phase 4

Professional document vectorization and storage in Pinecone.
Handles both original and enhanced document versions.
"""

from .vectorizer import vectorize_and_store

__all__ = ['vectorize_and_store']
