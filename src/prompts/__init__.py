"""
Prompts Repository

Centralized prompt templates for all AI/LLM interactions.

Structure:
- enhancement.py: Enhancement generation prompts
- rag_system_prompt.py: RAG question-answering prompt

Professional prompt engineering for production use.
"""

from .enhancement import (
    DIRECT_ENHANCEMENT_SYSTEM_PROMPT,
    DIRECT_ENHANCEMENT_USER_PROMPT
)

from .rag_system_prompt import (
    get_rag_prompt_template,
    RAG_SYSTEM_PROMPT
)

__all__ = [
    # Enhancement prompts
    'DIRECT_ENHANCEMENT_SYSTEM_PROMPT',
    'DIRECT_ENHANCEMENT_USER_PROMPT',
    
    # RAG prompts
    'get_rag_prompt_template',
    'RAG_SYSTEM_PROMPT',
]
