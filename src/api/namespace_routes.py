"""
API Routes for Namespace Management and Batch Upload

This module provides REST API endpoints for:
- Listing available namespaces
- Getting namespace information
- Batch uploading markdown files to specific namespaces
- Namespace statistics and metadata

Endpoints:
    GET /namespaces/ - List all namespaces
    GET /namespaces/{namespace_id} - Get specific namespace info
    POST /namespaces/batch-upload - Upload multiple markdown files to namespace
    GET /namespaces/statistics - Get namespace usage statistics
"""

from fastapi import APIRouter, UploadFile, File, HTTPException, Request, Form
from typing import List, Optional
from pathlib import Path
import tempfile
from pydantic import BaseModel
from loguru import logger

from ..core.namespaces_config import (
    get_all_namespaces,
    get_active_namespaces,
    get_production_namespaces,
    get_testing_namespaces,
    get_namespace_by_id,
    get_namespace_display_info,
    validate_namespace,
    get_namespace_statistics,
    load_namespace_metadata
)
from ..vectorization.batch_uploader import batch_upload_to_namespace, clear_namespace


router = APIRouter(prefix="/namespaces", tags=["namespaces"])


# ============================================================================
# PYDANTIC MODELS
# ============================================================================

class NamespaceInfo(BaseModel):
    """Namespace information model."""
    id: str
    name: str
    description: str
    client: Optional[str]
    type: str
    is_active: bool
    document_count: int = 0
    total_chunks: int = 0
    total_tokens: int = 0
    last_updated: Optional[str] = None


class NamespaceListResponse(BaseModel):
    """Response model for namespace list."""
    total: int
    active: int
    production: int
    testing: int
    namespaces: List[NamespaceInfo]


class NamespaceDetailResponse(BaseModel):
    """Response model for single namespace detail."""
    namespace: NamespaceInfo
    display_info: dict


class BatchUploadResponse(BaseModel):
    """Response model for batch upload operation."""
    success: bool
    namespace_id: str
    namespace_name: str
    files_processed: int
    files_succeeded: int
    files_failed: int
    total_chunks_uploaded: int
    total_input_tokens: int
    upload_timestamp: str
    detailed_results: List[dict]
    error: Optional[str] = None
    namespace_accumulated_stats: Optional[dict] = None  # ← CRITICAL FIX!


class NamespaceStatisticsResponse(BaseModel):
    """Response model for namespace statistics."""
    total_namespaces: int
    active_namespaces: int
    production_namespaces: int
    testing_namespaces: int
    total_documents: int
    metadata: dict


# ============================================================================
# API ENDPOINTS
# ============================================================================

@router.get("/", response_model=NamespaceListResponse)
async def list_namespaces(
    active_only: bool = False,
    production_only: bool = False,
    testing_only: bool = False
):
    """
    Get list of all namespaces with optional filtering.
    
    Query Parameters:
        - active_only: Return only active namespaces
        - production_only: Return only production namespaces
        - testing_only: Return only testing namespaces
        
    Returns:
        NamespaceListResponse with list of namespaces
    """
    try:
        # Determine which namespaces to return
        if production_only:
            namespaces = get_production_namespaces()
        elif testing_only:
            namespaces = get_testing_namespaces()
        elif active_only:
            namespaces = get_active_namespaces()
        else:
            namespaces = get_all_namespaces()
        
        # Load runtime metadata
        metadata = load_namespace_metadata()
        
        # Convert to response model with metadata
        namespace_infos = []
        for ns in namespaces:
            ns_metadata = metadata.get(ns["id"], {})
            namespace_infos.append(NamespaceInfo(
                id=ns["id"],
                name=ns["name"],
                description=ns.get("description", ""),
                client=ns.get("client"),
                type=ns.get("type", "unknown"),
                is_active=ns.get("is_active", True),
                document_count=ns_metadata.get("document_count", 0),
                total_chunks=ns_metadata.get("total_chunks", 0),
                total_tokens=ns_metadata.get("total_tokens", 0),
                last_updated=ns_metadata.get("last_updated")
            ))
        
        return NamespaceListResponse(
            total=len(get_all_namespaces()),
            active=len(get_active_namespaces()),
            production=len(get_production_namespaces()),
            testing=len(get_testing_namespaces()),
            namespaces=namespace_infos
        )
    
    except Exception as e:
        logger.error(f"Error listing namespaces: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/{namespace_id}", response_model=NamespaceDetailResponse)
async def get_namespace_detail(namespace_id: str):
    """
    Get detailed information about a specific namespace.
    
    Args:
        namespace_id: The namespace identifier
        
    Returns:
        NamespaceDetailResponse with namespace details
    """
    try:
        ns = get_namespace_by_id(namespace_id)
        
        if not ns:
            raise HTTPException(
                status_code=404,
                detail=f"Namespace '{namespace_id}' tidak ditemukan"
            )
        
        display_info = get_namespace_display_info(namespace_id)
        
        namespace_info = NamespaceInfo(
            id=ns["id"],
            name=ns["name"],
            description=ns.get("description", ""),
            client=ns.get("client"),
            type=ns.get("type", "unknown"),
            is_production=ns.get("is_production", False),
            is_active=ns.get("is_active", True),
            created_at=ns.get("created_at", ""),
            document_count=ns.get("document_count", 0)
        )
        
        return NamespaceDetailResponse(
            namespace=namespace_info,
            display_info=display_info
        )
    
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error getting namespace detail: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/batch-upload", response_model=BatchUploadResponse)
async def batch_upload_markdown(
    request: Request,
    namespace_id: str = Form(...),
    files: List[UploadFile] = File(...)
):
    """
    Upload multiple enhanced markdown files to a specific namespace.
    
    Form Parameters:
        - namespace_id: Target namespace ID
        - files: List of markdown files to upload
        
    Returns:
        BatchUploadResponse with upload results
    """
    try:
        # Validate namespace
        if not validate_namespace(namespace_id):
            raise HTTPException(
                status_code=400,
                detail=f"Namespace '{namespace_id}' tidak valid atau tidak aktif"
            )
        
        # Validate file count
        if not files:
            raise HTTPException(
                status_code=400,
                detail="Tidak ada file yang diunggah"
            )
        
        logger.info(f"Received batch upload request: {len(files)} files → namespace '{namespace_id}'")
        
        # Validate file types
        for file in files:
            if not file.filename.lower().endswith(('.md', '.markdown')):
                raise HTTPException(
                    status_code=400,
                    detail=f"File '{file.filename}' bukan markdown file"
                )
        
        # Save files to temporary directory
        temp_dir = Path(tempfile.mkdtemp(prefix="batch_upload_"))
        file_paths = []
        
        try:
            for file in files:
                temp_file_path = temp_dir / file.filename
                
                # Save uploaded file
                with open(temp_file_path, 'wb') as f:
                    content = await file.read()
                    f.write(content)
                
                file_paths.append(temp_file_path)
                logger.debug(f"Saved temporary file: {file.filename}")
            
            # Get Pinecone index and embeddings from app state
            pinecone_index = request.app.state.pinecone_index
            if not pinecone_index:
                raise HTTPException(
                    status_code=503,
                    detail="Pinecone index tidak tersedia"
                )
            
            embeddings = request.app.state.embedding_function
            
            # Perform batch upload
            result = batch_upload_to_namespace(
                markdown_files=file_paths,
                namespace_id=namespace_id,
                pinecone_index=pinecone_index,
                embeddings=embeddings
            )
            
            # Convert to response model
            response = BatchUploadResponse(
                success=result["success"],
                namespace_id=result["namespace_id"],
                namespace_name=result["namespace_name"],
                files_processed=result["files_processed"],
                files_succeeded=result["files_succeeded"],
                files_failed=result["files_failed"],
                total_chunks_uploaded=result["total_chunks_uploaded"],
                total_input_tokens=result["total_input_tokens"],
                upload_timestamp=result["upload_timestamp"],
                detailed_results=result["detailed_results"],
                error=result.get("error"),
                namespace_accumulated_stats=result.get("namespace_accumulated_stats")  # ← INCLUDE IT!
            )
            
            return response
        
        finally:
            # Cleanup temporary files
            import shutil
            try:
                shutil.rmtree(temp_dir)
                logger.debug(f"Cleaned up temporary directory: {temp_dir}")
            except Exception as e:
                logger.warning(f"Failed to cleanup temp directory: {e}")
    
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error in batch upload: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/statistics", response_model=NamespaceStatisticsResponse)
async def get_statistics():
    """
    Get overall statistics about namespace usage.
    
    Returns:
        NamespaceStatisticsResponse with statistics
    """
    try:
        stats = get_namespace_statistics()
        
        return NamespaceStatisticsResponse(
            total_namespaces=stats["total_namespaces"],
            active_namespaces=stats["active_namespaces"],
            production_namespaces=stats["production_namespaces"],
            testing_namespaces=stats["testing_namespaces"],
            total_documents=stats["total_documents"],
            metadata=stats["metadata"]
        )
    
    except Exception as e:
        logger.error(f"Error getting statistics: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.delete("/{namespace_id}/clear")
async def clear_namespace_endpoint(request: Request, namespace_id: str):
    """
    Clear all vectors from a namespace.
    
    WARNING: This will delete ALL data in the namespace!
    
    Args:
        namespace_id: Namespace to clear
        
    Returns:
        Operation result
    """
    try:
        # Validate namespace
        if not validate_namespace(namespace_id):
            raise HTTPException(
                status_code=400,
                detail=f"Namespace '{namespace_id}' tidak valid"
            )
        
        # Get Pinecone index
        pinecone_index = request.app.state.pinecone_index
        if not pinecone_index:
            raise HTTPException(
                status_code=503,
                detail="Pinecone index tidak tersedia"
            )
        
        # Clear namespace
        result = clear_namespace(namespace_id, pinecone_index)
        
        if not result["success"]:
            raise HTTPException(
                status_code=500,
                detail=result.get("error", "Failed to clear namespace")
            )
        
        return result
    
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error clearing namespace: {e}")
        raise HTTPException(status_code=500, detail=str(e))
