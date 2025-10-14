from pydantic import BaseModel
from typing import List, Optional, Dict, Any

class SuggestionItem(BaseModel):
    id: str
    type: str
    original_context: str
    generated_content: str
    # Jadikan opsional agar kita tidak mengirim skor dummy ke UI
    confidence_score: Optional[float] = None
    status: str = "pending"
    # Field tambahan untuk keterbacaan manusia di UI
    source_units: Optional[List[str]] = None
    source_previews: Optional[List[Dict[str, Any]]] = None

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

class QueryRequest(BaseModel):
    document_id: str
    prompt: str
    version: str | None = "both"  # 'v1' | 'v2' | 'both'
    trace: bool | None = False  
    k: int | None = 15  # Increased from 5 to 15 for better multi-document coverage

# --- Enhancement Configuration Models (NEW - Universal System) ---

class EnhancementConfigRequest(BaseModel):
    """
    User configuration for enhancement processing
    
    This enables per-document configuration with user-selected enhancement types,
    replacing the one-size-fits-all hardcoded approach.
    """
    selected_types: List[str]  # List of enhancement type IDs (from registry)
    domain_hint: Optional[str] = None  # Optional domain hint: financial, legal, operational, etc.
    custom_instructions: Optional[str] = None  # Optional custom instructions for AI
    priority_overrides: Optional[Dict[str, int]] = None  # Optional priority overrides per type

class DocumentAnalysisSummary(BaseModel):
    """
    Document analysis summary for frontend display
    
    Provides content analysis to help user make informed choices
    about which enhancement types to select.
    """
    pages: int
    tables: int
    has_numerical_data: bool
    has_legal_terms: bool
    has_procedural_content: bool
    detected_domain: Optional[str] = None  # Auto-detected domain
    content_characteristics: Dict[str, Any] = {}

class EnhancementTypeInfo(BaseModel):
    """Enhancement type information for frontend"""
    id: str
    category: str
    name: str
    description: str
    applicable_domains: List[str]
    default_enabled: bool
    default_priority: int

class EnhancementCategoryInfo(BaseModel):
    """Enhancement category information for frontend"""
    id: str
    name: str
    description: str
    icon: str
    display_order: int

class EnhancementTypeRegistryResponse(BaseModel):
    """
    Complete type registry for frontend consumption
    
    This provides all available enhancement types, categories,
    and domain-specific recommendations for UI rendering.
    """
    metadata: Dict[str, Any]
    categories: List[EnhancementCategoryInfo]
    types: List[EnhancementTypeInfo]
    domain_recommendations: Dict[str, Dict[str, List[str]]]
