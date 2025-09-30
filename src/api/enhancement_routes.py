"""
API endpoints for the Enhancement system.
"""

from fastapi import APIRouter, HTTPException, BackgroundTasks, Query
from pydantic import BaseModel, Field
from typing import List, Dict, Any, Optional
import json
from pathlib import Path
from datetime import datetime

from loguru import logger

from ..enhancement import (
    EnhancementConfig,
    MarkdownSynthesizer,
    EnhancementIndexer,
    RAGAnswering
)
from ..enhancement.planner_v2 import EnhancementPlannerV2
from ..enhancement.generator_v2 import EnhancementGeneratorV2
from ..enhancement.windowing import TokenWindowManager
from ..utils.doc_meta import get_markdown_path


router = APIRouter(prefix="/enhancement", tags=["enhancement"])

# Global instances
config = EnhancementConfig()
planner = EnhancementPlannerV2(config)
generator = EnhancementGeneratorV2(config)
synthesizer = MarkdownSynthesizer()
indexer = EnhancementIndexer(config)
answering = RAGAnswering(config)
window_manager = TokenWindowManager(
    window_size=config.window_tokens,
    overlap_size=config.window_overlap_tokens
)


class PlanRequest(BaseModel):
    """Request for planning enhancements."""
    doc_id: str = Field(..., description="Document ID to process")
    force_replan: bool = Field(default=False, description="Force replanning even if plan exists")


class PlanResponse(BaseModel):
    """Response from planning."""
    success: bool
    doc_id: str
    total_windows: int
    total_candidates: int
    selected_candidates: int
    plan_file: str
    message: str


class GenerateRequest(BaseModel):
    """Request for generating enhancements."""
    doc_id: str = Field(..., description="Document ID")
    candidate_ids: Optional[List[str]] = Field(None, description="Specific candidates to generate")
    max_items: Optional[int] = Field(None, description="Maximum items to generate")


class GenerateResponse(BaseModel):
    """Response from generation."""
    success: bool
    doc_id: str
    total_generated: int
    generation_file: str
    message: str


class SynthesizeRequest(BaseModel):
    """Request for synthesizing Markdown v2."""
    doc_id: str = Field(..., description="Document ID")
    include_global_sections: bool = Field(default=True, description="Include global enhancement sections")


class SynthesizeResponse(BaseModel):
    """Response from synthesis."""
    success: bool
    doc_id: str
    markdown_v2_path: str
    total_pages: int
    enhanced_pages: int
    message: str


class IndexRequest(BaseModel):
    """Request for indexing enhanced document."""
    doc_id: str = Field(..., description="Document ID")
    collection_name: Optional[str] = Field(None, description="Chroma collection name")


class IndexResponse(BaseModel):
    """Response from indexing."""
    success: bool
    doc_id: str
    collection_name: str
    total_indexed: int
    message: str


class AskRequest(BaseModel):
    """Request for answering a question."""
    query: str = Field(..., description="User's question")
    doc_id: str = Field(..., description="Document ID")
    collection_name: Optional[str] = Field(None, description="Chroma collection name")


class AskResponse(BaseModel):
    """Response with answer."""
    success: bool
    query: str
    answer: str
    citations: List[str]
    confidence: float
    intent: str
    message: str


@router.post("/plan", response_model=PlanResponse)
async def plan_enhancements(request: PlanRequest):
    """
    Plan enhancement candidates for a document.
    
    This endpoint:
    1. Creates token-based windows
    2. Runs map-reduce planning
    3. Selects top candidates
    """
    try:
        doc_id = request.doc_id
        logger.info(f"Planning enhancements for {doc_id}")
        
        # Check if document exists
        base_dir = Path(config.artifacts_dir) / doc_id
        if not base_dir.exists():
            raise HTTPException(
                status_code=404,
                detail=f"Document {doc_id} not found"
            )
        
        # Check for existing plan
        plan_file = base_dir / "enhancement_plan.json"
        if plan_file.exists() and not request.force_replan:
            with open(plan_file, 'r', encoding='utf-8') as f:
                existing_plan = json.load(f)
            
            return PlanResponse(
                success=True,
                doc_id=doc_id,
                total_windows=len(existing_plan.get('windows', [])),
                total_candidates=len(existing_plan.get('all_candidates', [])),
                selected_candidates=len(existing_plan.get('selected_candidates', [])),
                plan_file=str(plan_file),
                message="Using existing plan"
            )
        
        # Load units metadata
        units_meta_path = base_dir / "units_metadata.json"
        if not units_meta_path.exists():
            # Try alternate location
            units_meta_path = base_dir / "meta" / "units_metadata.json"
        
        if not units_meta_path.exists():
            raise HTTPException(
                status_code=404,
                detail=f"Units metadata not found for {doc_id}"
            )
        
        with open(units_meta_path, 'r', encoding='utf-8') as f:
            units_metadata = json.load(f)
        
        # Load markdown
        markdown_path = get_markdown_path(base_dir, "v1")
        if not markdown_path.exists():
            raise HTTPException(
                status_code=404,
                detail=f"Markdown not found for {doc_id}"
            )
        
        # Create windows
        windows = window_manager.create_windows(
            doc_id=doc_id,
            units_metadata=units_metadata,
            markdown_path=str(markdown_path)
        )
        
        # Save windows
        windows_file = base_dir / "windows.json"
        window_manager.save_windows(windows, str(windows_file))
        
        # Plan enhancements
        candidates, metrics = await planner.plan_enhancements(
            doc_id=doc_id,
            windows=windows,
            units_metadata=units_metadata
        )
        
        # Prepare plan data structure
        planning_result = {
            "doc_id": doc_id,
            "windows": [w.to_dict() for w in windows],
            "all_candidates": [c.dict() for c in candidates],
            "selected_candidates": [c.dict() for c in candidates],  # V2 already prioritized
            "final_candidates": [c.dict() for c in candidates],
            "metrics": metrics
        }
        
        # Save plan
        with open(plan_file, 'w', encoding='utf-8') as f:
            json.dump(planning_result, f, ensure_ascii=False, indent=2, default=str)
        
        return PlanResponse(
            success=True,
            doc_id=doc_id,
            total_windows=len(windows),
            total_candidates=len(candidates),
            selected_candidates=len(candidates),
            plan_file=str(plan_file),
            message=f"Successfully planned {len(candidates)} enhancements"
        )
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error planning enhancements: {e}")
        raise HTTPException(
            status_code=500,
            detail=str(e)
        )


@router.post("/generate", response_model=GenerateResponse)
async def generate_enhancements(request: GenerateRequest):
    """
    Generate enhancement narratives from planned candidates.
    
    This endpoint:
    1. Loads planned candidates
    2. Runs micro-batch generation
    3. Validates and saves enhancements
    """
    try:
        doc_id = request.doc_id
        logger.info(f"Generating enhancements for {doc_id}")
        
        # Load plan
        base_dir = Path(config.artifacts_dir) / doc_id
        plan_file = base_dir / "enhancement_plan.json"
        
        if not plan_file.exists():
            raise HTTPException(
                status_code=404,
                detail=f"Enhancement plan not found for {doc_id}. Run /plan first."
            )
        
        with open(plan_file, 'r', encoding='utf-8') as f:
            plan_data = json.load(f)
        
        # Get candidates to generate
        if request.candidate_ids:
            # Filter specific candidates
            all_candidates = plan_data.get('selected_candidates', [])
            candidates = [c for c in all_candidates if c.get('cand_id') in request.candidate_ids]
        else:
            # Use selected candidates
            candidates = plan_data.get('selected_candidates', [])
        
        # Limit if specified
        if request.max_items:
            candidates = candidates[:request.max_items]
        
        # Convert to EnhancementCandidate objects
        from ..enhancement.enhancement_types import EnhancementCandidate
        candidate_objects = [
            EnhancementCandidate(**c) for c in candidates
        ]
        
        # Load units metadata
        units_meta_path = base_dir / "units_metadata.json"
        if not units_meta_path.exists():
            units_meta_path = base_dir / "meta" / "units_metadata.json"
        
        with open(units_meta_path, 'r', encoding='utf-8') as f:
            units_metadata = json.load(f)
        
        # Generate enhancements
        enhancements, gen_metrics = await generator.generate_enhancements(
            candidates=candidate_objects,
            units_metadata=units_metadata,
            doc_id=doc_id
        )
        
        # Save generation result
        generation_file = base_dir / "enhancements.json"
        
        # Merge with plan data
        complete_data = {
            "doc_id": doc_id,
            "run_id": f"{doc_id}_{datetime.now().strftime('%Y%m%d_%H%M%S')}",
            "windows": plan_data.get('windows', []),
            "candidates": plan_data.get('final_candidates', []),
            "items": enhancements,
            "metrics": {
                "planning": plan_data.get('metrics', {}),
                "generation": gen_metrics,
                "version": "v2.0"
            }
        }
        
        with open(generation_file, 'w', encoding='utf-8') as f:
            json.dump(complete_data, f, ensure_ascii=False, indent=2)
        
        return GenerateResponse(
            success=True,
            doc_id=doc_id,
            total_generated=len(enhancements),
            generation_file=str(generation_file),
            message=f"Successfully generated {len(enhancements)} enhancements"
        )
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error generating enhancements: {e}")
        raise HTTPException(
            status_code=500,
            detail=str(e)
        )


@router.post("/synthesize", response_model=SynthesizeResponse)
async def synthesize_markdown(request: SynthesizeRequest):
    """
    Synthesize Markdown v2 with enhancements.
    
    This endpoint:
    1. Loads original markdown and enhancements
    2. Creates enhanced markdown with anchors
    3. Saves Markdown v2
    """
    try:
        doc_id = request.doc_id
        logger.info(f"Synthesizing Markdown v2 for {doc_id}")
        
        # Load paths
        base_dir = Path(config.artifacts_dir) / doc_id
        markdown_v1_path = get_markdown_path(base_dir, "v1")
        enhancements_file = base_dir / "enhancements.json"
        
        if not markdown_v1_path.exists():
            raise HTTPException(
                status_code=404,
                detail=f"Original markdown not found for {doc_id}"
            )
        
        if not enhancements_file.exists():
            raise HTTPException(
                status_code=404,
                detail=f"Enhancements not found for {doc_id}. Run /generate first."
            )
        
        # Load data
        with open(enhancements_file, 'r', encoding='utf-8') as f:
            enhancements_data = json.load(f)
        
        units_meta_path = base_dir / "units_metadata.json"
        if not units_meta_path.exists():
            units_meta_path = base_dir / "meta" / "units_metadata.json"
        
        with open(units_meta_path, 'r', encoding='utf-8') as f:
            units_metadata = json.load(f)
        
        # Synthesize
        markdown_v2_path = base_dir / "markdown_v2.md"
        
        synthesis_result = synthesizer.synthesize(
            doc_id=doc_id,
            markdown_v1_path=str(markdown_v1_path),
            enhancements=enhancements_data.get('items', []),
            units_metadata=units_metadata,
            output_path=str(markdown_v2_path)
        )
        
        return SynthesizeResponse(
            success=True,
            doc_id=doc_id,
            markdown_v2_path=str(markdown_v2_path),
            total_pages=synthesis_result.get('total_pages', 0),
            enhanced_pages=synthesis_result.get('enhanced_pages', 0),
            message="Successfully synthesized Markdown v2"
        )
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error synthesizing markdown: {e}")
        raise HTTPException(
            status_code=500,
            detail=str(e)
        )


@router.post("/index", response_model=IndexResponse)
async def index_document(request: IndexRequest):
    """
    Index enhanced document to Chroma.
    
    This endpoint:
    1. Loads units and enhancements
    2. Generates embeddings
    3. Indexes to Chroma collection
    """
    try:
        doc_id = request.doc_id
        collection_name = request.collection_name or f"enhanced_{doc_id}"
        
        logger.info(f"Indexing {doc_id} to collection {collection_name}")
        
        # Load data
        base_dir = Path(config.artifacts_dir) / doc_id
        
        units_meta_path = base_dir / "units_metadata.json"
        if not units_meta_path.exists():
            units_meta_path = base_dir / "meta" / "units_metadata.json"
        
        with open(units_meta_path, 'r', encoding='utf-8') as f:
            units_metadata = json.load(f)
        
        enhancements_file = base_dir / "enhancements.json"
        enhancements = []
        
        if enhancements_file.exists():
            with open(enhancements_file, 'r', encoding='utf-8') as f:
                enhancements_data = json.load(f)
                enhancements = enhancements_data.get('items', [])
        
        # Index
        index_result = await indexer.index_enhanced_document(
            doc_id=doc_id,
            units_metadata=units_metadata,
            enhancements=enhancements,
            collection_name=collection_name
        )
        
        return IndexResponse(
            success=True,
            doc_id=doc_id,
            collection_name=collection_name,
            total_indexed=index_result.get('total_indexed', 0),
            message=f"Successfully indexed {index_result.get('total_indexed', 0)} items"
        )
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error indexing document: {e}")
        raise HTTPException(
            status_code=500,
            detail=str(e)
        )


@router.post("/ask", response_model=AskResponse)
async def ask_question(request: AskRequest):
    """
    Answer a question using enhanced RAG.
    
    This endpoint:
    1. Routes the query
    2. Retrieves relevant content
    3. Composes answer with citations
    """
    try:
        logger.info(f"Answering question: {request.query[:100]}...")
        
        # Answer question
        answer = await answering.answer(
            query=request.query,
            doc_id=request.doc_id,
            collection_name=request.collection_name
        )
        
        return AskResponse(
            success=True,
            query=request.query,
            answer=answer.answer_text,
            citations=answer.citations,
            confidence=answer.confidence,
            intent=answer.route_used.intent.value if answer.route_used else "unknown",
            message="Successfully answered question"
        )
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error answering question: {e}")
        raise HTTPException(
            status_code=500,
            detail=str(e)
        )


@router.get("/status/{doc_id}")
async def get_enhancement_status(doc_id: str):
    """Get the enhancement status for a document."""
    try:
        base_dir = Path(config.artifacts_dir) / doc_id
        
        if not base_dir.exists():
            raise HTTPException(
                status_code=404,
                detail=f"Document {doc_id} not found"
            )
        
        status = {
            "doc_id": doc_id,
            "has_extraction": (base_dir / "units_metadata.json").exists() or 
                            (base_dir / "meta" / "units_metadata.json").exists(),
            "has_windows": (base_dir / "windows.json").exists(),
            "has_plan": (base_dir / "enhancement_plan.json").exists(),
            "has_enhancements": (base_dir / "enhancements.json").exists(),
            "has_markdown_v2": (base_dir / "markdown_v2.md").exists()
        }
        
        # Load metrics if available
        if status["has_enhancements"]:
            with open(base_dir / "enhancements.json", 'r', encoding='utf-8') as f:
                enh_data = json.load(f)
                status["metrics"] = enh_data.get("metrics", {})
        
        return status
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error getting status: {e}")
        raise HTTPException(
            status_code=500,
            detail=str(e)
        )
