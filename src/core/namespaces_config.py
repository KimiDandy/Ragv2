"""
Namespace Configuration for Pinecone Vector Store

This module manages STATIC configuration for namespaces in Pinecone.
Namespaces provide data isolation within a single Pinecone index for multi-tenancy.

DESIGN PRINCIPLE:
- This file contains STATIC configuration only (namespace definitions)
- DYNAMIC data (document counts, upload timestamps) stored in metadata JSON file
- Separation of concerns: Config vs Runtime Data

NAMESPACE STRUCTURE:
{
    "id": str              # Unique identifier, empty string "" for default
    "name": str            # Human-readable name
    "description": str     # Purpose and usage description
    "client": str | None   # Client identifier (None for shared namespaces)
    "type": str            # "testing" or "final" - determines behavior
    "is_active": bool      # Enable/disable without deleting config
}

FIELD EXPLANATIONS:

1. "type" - Business Logic Control
   - "testing": Temporary data, can be cleared anytime
   - "final": Production data, protected from accidental deletion
   - Used for: Routing, filtering, safety checks

2. "is_active" - Soft Delete / Archive
   - True: Namespace available for use
   - False: Hidden from UI, cannot be used (but config preserved)
   - Use case: Temporarily disable without losing configuration

Naming Convention:
- Default: "" (empty string) - shared testing namespace
- Client format: "client-{name}-{type}-{number}"
  Examples: 
    - "client-a-testing-1" - Testing environment
    - "client-a-final"      - Production environment

Usage:
    from src.core.namespaces_config import get_all_namespaces, get_namespace_by_id
    
    # Get all namespaces
    namespaces = get_all_namespaces()
    
    # Get specific namespace
    namespace = get_namespace_by_id("client-a-final")
    
    # Get runtime data (document counts, etc)
    from src.core.namespaces_config import load_namespace_metadata
    metadata = load_namespace_metadata()
    doc_count = metadata.get("client-a-final", {}).get("document_count", 0)
"""

from typing import List, Dict, Optional, Any
from datetime import datetime
import json
from pathlib import Path
from loguru import logger

# ============================================================================
# NAMESPACE DEFINITIONS
# ============================================================================

NAMESPACES: List[Dict[str, any]] = [
    {
        "id": "",
        "name": "Default Testing Namespace",
        "description": "Namespace default untuk testing umum dan development",
        "client": None,
        "type": "testing",
        "is_active": True,
    },
    {
        "id": "client-a-testing-1",
        "name": "Client A - Testing Batch 1",
        "description": "Testing environment pertama untuk Client A",
        "client": "client-a",
        "type": "testing",
        "is_active": True,
    },
    {
        "id": "client-a-testing-2",
        "name": "Client A - Testing Batch 2",
        "description": "Testing environment kedua untuk Client A",
        "client": "client-a",
        "type": "testing",
        "is_active": True,
    },
    {
        "id": "client-a-testing-3",
        "name": "Client A - Testing Batch 3",
        "description": "Testing environment ketiga untuk Client A",
        "client": "client-a",
        "type": "testing",
        "is_active": True,
    },
    {
        "id": "client-a-testing-4",
        "name": "Client A - Testing Batch 4",
        "description": "Testing environment keempat untuk Client A",
        "client": "client-a",
        "type": "testing",
        "is_active": True,
    },
    {
        "id": "danamon-final-1",
        "name": "Danamon - Production Final 1",
        "description": "Production environment final untuk Client Danamon V1 2 Oktober 2025",
        "client": "danamon-1",
        "type": "final",
        "is_active": True,
    },
    {
        "id": "danamon-final-2",
        "name": "Danamon - Production Final 2",
        "description": "Production environment final untuk Client Danamon V1 10 Oktober 2025",
        "client": "danamon-1",
        "type": "final",
        "is_active": True,
    },
    {
        "id": "danamon-final-3",
        "name": "Danamon - Production Final 3",
        "description": "Production environment final untuk Client Danamon 10 Oktober 2025",
        "client": "danamon-1",
        "type": "final",
        "is_active": True,
    },
]

# ============================================================================
# HELPER FUNCTIONS
# ============================================================================

def get_all_namespaces() -> List[Dict[str, any]]:
    """
    Mengembalikan semua namespace yang tersedia.
    
    Returns:
        List[Dict]: List of all namespace configurations
    """
    return NAMESPACES.copy()


def get_active_namespaces() -> List[Dict[str, any]]:
    """
    Mengembalikan hanya namespace yang aktif.
    
    Returns:
        List[Dict]: List of active namespaces
    """
    return [ns for ns in NAMESPACES if ns.get("is_active", True)]


def get_production_namespaces() -> List[Dict[str, any]]:
    """
    Mengembalikan hanya namespace production (type="final").
    
    Returns:
        List[Dict]: List of production namespaces
    """
    return [ns for ns in NAMESPACES if ns.get("type") == "final"]


def get_testing_namespaces() -> List[Dict[str, any]]:
    """
    Mengembalikan hanya namespace testing (type="testing").
    
    Returns:
        List[Dict]: List of testing namespaces
    """
    return [ns for ns in NAMESPACES if ns.get("type") == "testing"]


def get_namespace_by_id(namespace_id: str) -> Optional[Dict[str, any]]:
    """
    Mendapatkan konfigurasi namespace berdasarkan ID.
    
    Args:
        namespace_id (str): ID namespace yang dicari
        
    Returns:
        Optional[Dict]: Namespace config jika ditemukan, None jika tidak
    """
    for ns in NAMESPACES:
        if ns["id"] == namespace_id:
            return ns.copy()
    return None


def get_namespaces_by_client(client: str) -> List[Dict[str, any]]:
    """
    Mendapatkan semua namespace untuk client tertentu.
    
    Args:
        client (str): Client identifier (e.g., "client-a")
        
    Returns:
        List[Dict]: List of namespaces for the client
    """
    return [ns for ns in NAMESPACES if ns.get("client") == client]


def validate_namespace(namespace_id: str) -> bool:
    """
    Memvalidasi apakah namespace ID tersedia dan aktif.
    
    Args:
        namespace_id (str): ID namespace yang akan divalidasi
        
    Returns:
        bool: True jika valid dan aktif, False jika tidak
    """
    ns = get_namespace_by_id(namespace_id)
    if not ns:
        logger.warning(f"Namespace '{namespace_id}' tidak ditemukan dalam konfigurasi")
        return False
    
    if not ns.get("is_active", True):
        logger.warning(f"Namespace '{namespace_id}' tidak aktif")
        return False
    
    return True


def get_default_testing_namespace() -> str:
    """
    Mengembalikan namespace default untuk testing.
    
    Returns:
        str: Default testing namespace ID (empty string)
    """
    return ""


def get_namespace_display_info(namespace_id: str) -> Dict[str, str]:
    """
    Mendapatkan informasi display-friendly untuk namespace.
    
    Args:
        namespace_id (str): ID namespace
        
    Returns:
        Dict: Display information
    """
    ns = get_namespace_by_id(namespace_id)
    if not ns:
        return {
            "id": namespace_id,
            "name": "Unknown Namespace",
            "status": "not_found"
        }
    
    return {
        "id": ns["id"],
        "name": ns["name"],
        "description": ns.get("description", ""),
        "client": ns.get("client", "N/A"),
        "type": ns.get("type", "unknown"),
        "status": "production" if ns.get("type") == "final" else "testing",
        "is_active": ns.get("is_active", True)
    }


def is_production_namespace(namespace_id: str) -> bool:
    """
    Check if a namespace is production (type="final").
    
    Args:
        namespace_id (str): Namespace ID to check
        
    Returns:
        bool: True if production namespace, False otherwise
    """
    ns = get_namespace_by_id(namespace_id)
    return ns.get("type") == "final" if ns else False


# ============================================================================
# METADATA PERSISTENCE (Optional - for tracking document counts)
# ============================================================================

def get_metadata_file_path() -> Path:
    """Get path to namespace metadata file."""
    from .config import PIPELINE_ARTEFACTS_DIR
    return Path(PIPELINE_ARTEFACTS_DIR) / "namespace_metadata.json"


def load_namespace_metadata() -> Dict[str, Dict]:
    """
    Load namespace metadata from file (document counts, last update, etc).
    
    Returns:
        Dict: Metadata for each namespace
    """
    metadata_path = get_metadata_file_path()
    
    if not metadata_path.exists():
        return {}
    
    try:
        with open(metadata_path, 'r', encoding='utf-8') as f:
            return json.load(f)
    except Exception as e:
        logger.error(f"Failed to load namespace metadata: {e}")
        return {}


def save_namespace_metadata(metadata: Dict[str, Dict]):
    """
    Save namespace metadata to file.
    
    Args:
        metadata (Dict): Metadata to save
    """
    metadata_path = get_metadata_file_path()
    metadata_path.parent.mkdir(parents=True, exist_ok=True)
    
    try:
        with open(metadata_path, 'w', encoding='utf-8') as f:
            json.dump(metadata, f, indent=2, ensure_ascii=False)
        logger.info(f"Namespace metadata saved to {metadata_path}")
    except Exception as e:
        logger.error(f"Failed to save namespace metadata: {e}")


def update_namespace_document_count(namespace_id: str, count: int):
    """
    Update document count for a namespace (adds to existing count).
    
    Args:
        namespace_id (str): Namespace ID
        count (int): Number of documents to add
    """
    metadata = load_namespace_metadata()
    
    if namespace_id not in metadata:
        metadata[namespace_id] = {
            "document_count": 0,
            "total_chunks": 0,
            "total_tokens": 0,
            "created_at": datetime.now().isoformat(),
        }
    
    # Add to existing count
    metadata[namespace_id]["document_count"] = metadata[namespace_id].get("document_count", 0) + count
    metadata[namespace_id]["last_updated"] = datetime.now().isoformat()
    
    save_namespace_metadata(metadata)
    logger.info(f"Updated namespace '{namespace_id}' document count: +{count} → {metadata[namespace_id]['document_count']} total")


def update_namespace_stats(namespace_id: str, documents_added: int, chunks_added: int, tokens_used: int) -> Dict[str, Any]:
    """
    Update comprehensive statistics for a namespace after upload.
    
    Args:
        namespace_id (str): Namespace ID
        documents_added (int): Number of documents uploaded
        chunks_added (int): Number of chunks/vectors added
        tokens_used (int): Number of tokens used for embeddings
    
    Returns:
        Dict: Updated metadata for the namespace
    """
    metadata = load_namespace_metadata()
    
    if namespace_id not in metadata:
        metadata[namespace_id] = {
            "document_count": 0,
            "total_chunks": 0,
            "total_tokens": 0,
            "created_at": datetime.now().isoformat(),
        }
    
    # Update all stats
    metadata[namespace_id]["document_count"] = metadata[namespace_id].get("document_count", 0) + documents_added
    metadata[namespace_id]["total_chunks"] = metadata[namespace_id].get("total_chunks", 0) + chunks_added
    metadata[namespace_id]["total_tokens"] = metadata[namespace_id].get("total_tokens", 0) + tokens_used
    metadata[namespace_id]["last_updated"] = datetime.now().isoformat()
    
    save_namespace_metadata(metadata)
    logger.info(
        f"Updated namespace '{namespace_id}' stats: "
        f"+{documents_added} docs, +{chunks_added} chunks, +{tokens_used} tokens"
    )
    
    # Return the updated namespace metadata
    return metadata[namespace_id]


def get_namespace_statistics() -> Dict[str, any]:
    """
    Get statistics about namespace usage.
    
    Returns:
        Dict: Statistics summary
    """
    metadata = load_namespace_metadata()
    active_ns = get_active_namespaces()
    prod_ns = get_production_namespaces()
    
    total_docs = sum(
        metadata.get(ns["id"], {}).get("document_count", 0) 
        for ns in active_ns
    )
    
    return {
        "total_namespaces": len(NAMESPACES),
        "active_namespaces": len(active_ns),
        "production_namespaces": len(prod_ns),
        "testing_namespaces": len(active_ns) - len(prod_ns),
        "total_documents": total_docs,
        "metadata": metadata
    }


# ============================================================================
# INITIALIZATION
# ============================================================================

logger.info(f"Namespace configuration loaded: {len(NAMESPACES)} namespaces defined")
logger.debug(f"Active namespaces: {len(get_active_namespaces())}")
logger.debug(f"Production namespaces: {len(get_production_namespaces())}")
