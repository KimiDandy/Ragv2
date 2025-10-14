"""
Admin Routes

Administrative endpoints for managing active namespace and system configuration.
These endpoints are intended for product team/admin use to switch between clients.
"""

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from typing import Optional
from loguru import logger

from ..core.enhancement_profiles import ProfileLoader
from ..core import namespaces_config


router = APIRouter(prefix="/admin", tags=["admin"])


class SetActiveNamespaceRequest(BaseModel):
    """Request to set active namespace"""
    namespace_id: str
    updated_by: Optional[str] = "admin"


class ActiveNamespaceResponse(BaseModel):
    """Response with active namespace info"""
    active_namespace: str
    namespace_name: str
    namespace_description: str
    client_profile: str
    client_name: str
    enabled_enhancement_count: int


class NamespaceListItem(BaseModel):
    """Single namespace in the list"""
    id: str
    name: str
    description: str
    client_profile: str
    type: str
    status: str


class NamespaceListResponse(BaseModel):
    """Response with list of all namespaces"""
    namespaces: list[NamespaceListItem]
    total_count: int
    active_namespace: str


class ConfigSummaryResponse(BaseModel):
    """Response with configuration summary"""
    global_config: dict
    active_namespace: str
    active_client: str
    active_enabled_types: int
    available_profiles: list[str]
    total_profiles: int


@router.post("/set-active-namespace")
async def set_active_namespace(request: SetActiveNamespaceRequest):
    """
    Set the active namespace for the system
    
    This endpoint allows admins/product team to switch between clients
    by changing the active namespace. The system will automatically load
    the corresponding client profile for all subsequent document processing.
    
    Args:
        request: Contains namespace_id and updated_by
        
    Returns:
        Success message with new active namespace info
        
    Raises:
        HTTPException: If namespace is invalid or operation fails
    """
    try:
        # Validate namespace exists
        if not namespaces_config.validate_namespace(request.namespace_id):
            raise HTTPException(
                status_code=404,
                detail=f"Namespace '{request.namespace_id}' not found in configuration"
            )
        
        # Initialize profile loader
        profile_loader = ProfileLoader()
        
        # Set active namespace
        profile_loader.set_active_namespace(
            namespace_id=request.namespace_id,
            updated_by=request.updated_by
        )
        
        # Get namespace info for response
        ns_info = namespaces_config.get_namespace_display_info(request.namespace_id)
        
        # Try to get client profile info
        try:
            client_profile = profile_loader.get_profile_for_namespace(request.namespace_id)
            client_name = client_profile.client_name
            enabled_count = client_profile.get_enabled_count()
        except Exception as e:
            logger.warning(f"Could not load client profile: {e}")
            client_name = "Unknown"
            enabled_count = 0
        
        logger.info(f"✓ Active namespace changed to: {request.namespace_id}")
        
        return {
            "status": "success",
            "message": f"Active namespace changed to '{request.namespace_id}'",
            "active_namespace": request.namespace_id,
            "namespace_name": ns_info.get("name", "Unknown"),
            "client_profile": ns_info.get("client_profile", "N/A"),
            "client_name": client_name,
            "enabled_enhancement_count": enabled_count,
            "updated_by": request.updated_by
        }
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Failed to set active namespace: {e}", exc_info=True)
        raise HTTPException(
            status_code=500,
            detail=f"Failed to set active namespace: {str(e)}"
        )


@router.get("/get-active-namespace", response_model=ActiveNamespaceResponse)
async def get_active_namespace():
    """
    Get currently active namespace and its configuration
    
    Returns detailed information about the active namespace including
    client profile and enabled enhancement types.
    
    Returns:
        ActiveNamespaceResponse with full namespace details
        
    Raises:
        HTTPException: If configuration cannot be loaded
    """
    try:
        # Initialize profile loader
        profile_loader = ProfileLoader()
        
        # Get active namespace
        active_namespace = profile_loader.get_active_namespace()
        
        # Get namespace info
        ns_info = namespaces_config.get_namespace_display_info(active_namespace)
        
        # Get client profile
        try:
            client_profile = profile_loader.get_profile_for_namespace(active_namespace)
            client_name = client_profile.client_name
            enabled_count = client_profile.get_enabled_count()
        except Exception as e:
            logger.warning(f"Could not load client profile: {e}")
            client_name = "Unknown"
            enabled_count = 0
        
        return ActiveNamespaceResponse(
            active_namespace=active_namespace,
            namespace_name=ns_info.get("name", "Unknown"),
            namespace_description=ns_info.get("description", ""),
            client_profile=ns_info.get("client_profile", "N/A"),
            client_name=client_name,
            enabled_enhancement_count=enabled_count
        )
        
    except Exception as e:
        logger.error(f"Failed to get active namespace: {e}", exc_info=True)
        raise HTTPException(
            status_code=500,
            detail=f"Failed to get active namespace: {str(e)}"
        )


@router.get("/list-namespaces", response_model=NamespaceListResponse)
async def list_namespaces():
    """
    List all available namespaces
    
    Returns a complete list of all namespaces defined in the system,
    along with their client profiles and current active status.
    
    Returns:
        NamespaceListResponse with all namespaces
    """
    try:
        # Get all namespaces
        all_namespaces = namespaces_config.get_all_namespaces()
        
        # Get active namespace
        profile_loader = ProfileLoader()
        active_namespace = profile_loader.get_active_namespace()
        
        # Build response list
        namespace_list = []
        for ns in all_namespaces:
            namespace_list.append(NamespaceListItem(
                id=ns["id"],
                name=ns["name"],
                description=ns.get("description", ""),
                client_profile=ns.get("client_profile", "N/A"),
                type=ns.get("type", "unknown"),
                status="production" if ns.get("type") == "final" else "testing"
            ))
        
        return NamespaceListResponse(
            namespaces=namespace_list,
            total_count=len(namespace_list),
            active_namespace=active_namespace
        )
        
    except Exception as e:
        logger.error(f"Failed to list namespaces: {e}", exc_info=True)
        raise HTTPException(
            status_code=500,
            detail=f"Failed to list namespaces: {str(e)}"
        )


@router.get("/config-summary", response_model=ConfigSummaryResponse)
async def get_config_summary():
    """
    Get comprehensive configuration summary
    
    Returns summary of global configuration, active namespace,
    and available client profiles.
    
    Returns:
        ConfigSummaryResponse with full configuration overview
    """
    try:
        profile_loader = ProfileLoader()
        summary = profile_loader.get_summary()
        
        return ConfigSummaryResponse(**summary)
        
    except Exception as e:
        logger.error(f"Failed to get config summary: {e}", exc_info=True)
        raise HTTPException(
            status_code=500,
            detail=f"Failed to get configuration summary: {str(e)}"
        )


@router.post("/reload-configs")
async def reload_all_configs():
    """
    Force reload all configurations from disk
    
    Useful when configuration files are updated externally
    (e.g., by product team editing JSON files).
    
    Returns:
        Success message
    """
    try:
        profile_loader = ProfileLoader()
        profile_loader.reload_all()
        
        return {
            "status": "success",
            "message": "All configurations reloaded from disk"
        }
        
    except Exception as e:
        logger.error(f"Failed to reload configs: {e}", exc_info=True)
        raise HTTPException(
            status_code=500,
            detail=f"Failed to reload configurations: {str(e)}"
        )
