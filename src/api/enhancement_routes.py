"""
Enhancement-specific API routes

New endpoints for universal enhancement type system with user configuration.
"""

from fastapi import APIRouter, HTTPException
from loguru import logger
from pathlib import Path
from typing import Optional, Union
import json

from ..enhancement.type_registry import get_type_registry
from ..core.config import PIPELINE_ARTEFACTS_DIR
from ..shared.document_meta import get_markdown_path
from .schemas import (
    EnhancementTypeRegistryResponse,
    DocumentAnalysisSummary,
    EnhancementTypeInfo,
    EnhancementCategoryInfo
)

router = APIRouter(prefix="/enhancement", tags=["enhancement"])


@router.get("/get-type-registry", response_model=EnhancementTypeRegistryResponse)
async def get_enhancement_type_registry():
    """
    Get complete enhancement type registry for frontend
    
    Returns all available enhancement types, categories, and domain-specific
    recommendations. Used by frontend to render configuration UI.
    """
    try:
        registry = get_type_registry()
        registry_data = registry.to_frontend_config()
        
        # Convert to Pydantic models
        categories = [
            EnhancementCategoryInfo(**cat)
            for cat in registry_data['categories']
        ]
        
        types = [
            EnhancementTypeInfo(**typ)
            for typ in registry_data['types']
        ]
        
        response = EnhancementTypeRegistryResponse(
            metadata=registry_data['metadata'],
            categories=categories,
            types=types,
            domain_recommendations=registry_data['domain_recommendations']
        )
        
        logger.info(f"Type registry served: {len(categories)} categories, {len(types)} types")
        return response
        
    except Exception as e:
        logger.error(f"Failed to load type registry: {e}")
        raise HTTPException(status_code=500, detail=f"Failed to load enhancement types: {str(e)}")


@router.get("/analyze-document/{document_id}", response_model=DocumentAnalysisSummary)
async def analyze_document_for_enhancement(document_id: str):
    """
    Analyze document content to provide smart enhancement type recommendations
    
    Returns document characteristics that help user choose appropriate
    enhancement types (e.g., has tables, legal terms, procedural content).
    """
    try:
        doc_dir = Path(PIPELINE_ARTEFACTS_DIR) / document_id
        
        # Check document exists
        md_path = get_markdown_path(doc_dir, "v1")
        if not md_path.exists():
            raise HTTPException(status_code=404, detail="Document not found")
        
        # Read markdown content
        content = md_path.read_text(encoding='utf-8')
        
        # Load metadata if available
        metadata_path = doc_dir / "units_metadata.json"
        if not metadata_path.exists():
            metadata_path = doc_dir / "meta" / "units_metadata.json"
        
        metadata = {}
        if metadata_path.exists():
            with open(metadata_path, 'r', encoding='utf-8') as f:
                metadata = json.load(f)
        
        # Load tables
        tables_path = doc_dir / "tables.json"
        tables_data = []
        if tables_path.exists():
            with open(tables_path, 'r', encoding='utf-8') as f:
                tables_data = json.load(f)
        
        # Analyze content
        analysis = _analyze_content_characteristics(content, metadata, tables_data)
        
        logger.info(f"Document analysis for {document_id}: {analysis}")
        return analysis
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Failed to analyze document {document_id}: {e}")
        raise HTTPException(status_code=500, detail=f"Analysis failed: {str(e)}")


def _analyze_content_characteristics(
    content: str,
    metadata: Union[dict, list],
    tables_data: list
) -> DocumentAnalysisSummary:
    """
    Analyze document content to determine characteristics
    
    This helps provide smart recommendations for enhancement types.
    """
    content_lower = content.lower()
    
    # Count pages - handle both dict and list formats
    pages = 0
    if isinstance(metadata, dict):
        # Old format: dict with total_pages
        pages = metadata.get('total_pages', 0)
    elif isinstance(metadata, list):
        # New format: list of units, extract unique pages
        unique_pages = set()
        for unit in metadata:
            if isinstance(unit, dict) and 'page' in unit:
                unique_pages.add(unit['page'])
        pages = len(unique_pages)
    
    if pages == 0:
        # Estimate from content length
        pages = max(1, len(content) // 3000)
    
    # Count tables
    tables = len(tables_data)
    
    # Check for numerical data
    has_numerical = (
        tables > 0 or
        any(char.isdigit() for char in content[:5000]) and 
        '%' in content or 'rp' in content_lower or '$' in content
    )
    
    # Check for legal terms (Indonesian)
    legal_keywords = ['pasal', 'ayat', 'undang', 'peraturan', 'perjanjian', 'kontrak', 'klausul']
    has_legal_terms = any(keyword in content_lower for keyword in legal_keywords)
    
    # Check for procedural content
    procedural_keywords = ['langkah', 'prosedur', 'tahap', 'proses', 'cara', 'metode']
    has_procedural = any(keyword in content_lower for keyword in procedural_keywords)
    
    # Auto-detect domain
    detected_domain = None
    if tables >= 2 or (has_numerical and 'revenue' in content_lower or 'profit' in content_lower):
        detected_domain = 'financial'
    elif has_legal_terms:
        detected_domain = 'legal'
    elif has_procedural:
        detected_domain = 'operational'
    
    # Additional characteristics
    characteristics = {
        'word_count': len(content.split()),
        'has_images': 'image' in content_lower or '![' in content,
        'estimated_reading_time_minutes': len(content.split()) // 200,
        'table_count': tables,
        'has_headings': '#' in content,
    }
    
    return DocumentAnalysisSummary(
        pages=pages,
        tables=tables,
        has_numerical_data=has_numerical,
        has_legal_terms=has_legal_terms,
        has_procedural_content=has_procedural,
        detected_domain=detected_domain,
        content_characteristics=characteristics
    )
