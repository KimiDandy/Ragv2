"""
Models for Enhancement System
"""

from typing import List, Dict, Any, Optional
from pydantic import BaseModel, Field


class UniversalEnhancement(BaseModel):
    """Universal enhancement model for single-step processing"""
    
    enhancement_id: str
    doc_id: str
    enhancement_type: str
    title: str
    original_context: str
    generated_content: str
    source_units: List[str] = Field(default_factory=list)
    source_previews: List[str] = Field(default_factory=list)  
    confidence_score: float = Field(default=0.8, ge=0.0, le=1.0)
    priority: int = Field(default=5, ge=1, le=10)
    metadata: Dict[str, Any] = Field(default_factory=dict)
    
    def dict(self, *args, **kwargs):
        """Override dict to ensure proper serialization"""
        data = super().dict(*args, **kwargs)
        # Ensure all fields are properly serialized
        return data
