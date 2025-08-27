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

class AskSingleVersionResponse(BaseModel):
    answer: str
    version: str
    prompt: str
    sources: List[RetrievedSource]

class AskBothVersionsResponse(BaseModel):
    prompt: str
    unenriched_answer: str
    enriched_answer: str
    unenriched_sources: List[RetrievedSource]
    enriched_sources: List[RetrievedSource]
