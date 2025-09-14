from pydantic import BaseModel
from typing import List

class SuggestionItem(BaseModel):
    id: str  
    type: str  
    original_context: str  
    generated_content: str  
    confidence_score: float 
    status: str = "pending" 

class CuratedSuggestions(BaseModel):
    document_id: str
    suggestions: List[SuggestionItem]

class UploadResponse(BaseModel):
    document_id: str
    markdown_content: str

class EnhancementResponse(BaseModel):
    document_id: str
    suggestions: List[SuggestionItem]

class RetrievedSource(BaseModel):
    id: str
    score: float
    snippet: str
    metadata: dict

class TokenUsage(BaseModel):
    input_tokens: int
    output_tokens: int
    total_tokens: int

class AskSingleVersionResponse(BaseModel):
    answer: str
    version: str
    prompt: str
    sources: List[RetrievedSource]
    token_usage: TokenUsage | None = None

class AskBothVersionsResponse(BaseModel):
    prompt: str
    unenriched_answer: str
    enriched_answer: str
    unenriched_sources: List[RetrievedSource]
    enriched_sources: List[RetrievedSource]
    unenriched_token_usage: TokenUsage | None = None
    enriched_token_usage: TokenUsage | None = None

# --- PDF→Markdown++ conversion models ---

class UploadPdfResponse(BaseModel):
    document_id: str
    file_name: str

class StartConversionRequest(BaseModel):
    document_id: str
    mode: str  # 'basic' | 'smart'

class ConversionProgress(BaseModel):
    status: str
    percent: float
    message: str | None = None

class ConversionResult(BaseModel):
    document_id: str
    markdown_content: str
    artefacts: List[str]
    metadata_path: str | None = None
