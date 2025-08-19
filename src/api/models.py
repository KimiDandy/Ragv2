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
