"""
API Schemas - Cleaned Version
Only includes schemas used by active automated pipeline endpoints
"""

from pydantic import BaseModel
from typing import List, Optional, Dict, Any


class RetrievedSource(BaseModel):
    """Source document retrieved from RAG query"""
    id: str
    score: float
    snippet: str
    metadata: dict


class TokenUsage(BaseModel):
    """Token usage statistics"""
    input_tokens: int
    output_tokens: int
    total_tokens: int


class AskSingleVersionResponse(BaseModel):
    """Response for single version query"""
    answer: str
    version: str
    prompt: str
    sources: List[RetrievedSource]
    token_usage: TokenUsage | None = None


class AskBothVersionsResponse(BaseModel):
    """Response for comparison query (v1 vs v2)"""
    prompt: str
    unenriched_answer: str
    enriched_answer: str
    unenriched_sources: List[RetrievedSource]
    enriched_sources: List[RetrievedSource]
    unenriched_token_usage: TokenUsage | None = None
    enriched_token_usage: TokenUsage | None = None


class QueryRequest(BaseModel):
    """Request model for /ask/ endpoint"""
    document_id: str
    prompt: str
    version: str | None = "both"  # 'v1' | 'v2' | 'both'
    trace: bool | None = False  
    k: int | None = 15  # Number of chunks to retrieve
