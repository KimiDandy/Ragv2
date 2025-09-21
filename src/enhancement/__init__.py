"""
Enhancement module for adding intelligent annotations to extracted PDF documents.

This module provides:
- Token-based windowing for scalable document processing
- Map-reduce planning for enhancement candidates
- Micro-batch generation of enhancements
- Markdown v2 synthesis with precise anchoring
- Vectorization and RAG integration
"""

from .config import EnhancementConfig
from .planner import EnhancementPlanner
from .generator import EnhancementGenerator
from .synthesizer import MarkdownSynthesizer
from .indexer import EnhancementIndexer
from .answering import RAGAnswering

__all__ = [
    'EnhancementConfig',
    'EnhancementPlanner',
    'EnhancementGenerator',
    'MarkdownSynthesizer',
    'EnhancementIndexer',
    'RAGAnswering'
]
