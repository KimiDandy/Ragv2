"""
Simple script to delete Pinecone namespace(s).

Usage:
    python scripts/delete_namespace.py
    
Then manually specify namespace IDs to delete in NAMESPACES_TO_DELETE list below.
"""

from pinecone import Pinecone
from loguru import logger
import os
from dotenv import load_dotenv

# Load environment variables
load_dotenv()

# ============================================
# CONFIGURATION: EDIT THIS LIST
# ============================================
NAMESPACES_TO_DELETE = [
    "danamon-test-1"
]

# ============================================
# Pinecone Configuration
# ============================================
PINECONE_API_KEY = os.getenv("PINECONE_API_KEY")
PINECONE_INDEX_NAME = os.getenv("PINECONE_INDEX_NAME", "inspigo-pinecone")


def delete_namespace(index, namespace: str) -> bool:
    """
    Delete all vectors in a namespace, effectively removing the namespace.
    
    Note: Pinecone doesn't have a direct "delete namespace" API.
    Instead, we delete all vectors in that namespace, which removes it.
    """
    try:
        logger.info(f"🗑️  Deleting namespace: '{namespace}'")
        
        # Delete all vectors in namespace (no filter = delete all)
        index.delete(delete_all=True, namespace=namespace)
        
        logger.success(f"✓ Namespace '{namespace}' deleted successfully")
        return True
        
    except Exception as e:
        logger.error(f"✗ Failed to delete namespace '{namespace}': {e}")
        return False


def main():
    """Main execution function"""
    
    if not NAMESPACES_TO_DELETE:
        logger.warning("⚠️  No namespaces specified in NAMESPACES_TO_DELETE list!")
        logger.info("Edit this script and add namespace IDs to delete.")
        return
    
    logger.info(f"Connecting to Pinecone index: {PINECONE_INDEX_NAME}")
    
    # Initialize Pinecone
    pc = Pinecone(api_key=PINECONE_API_KEY)
    index = pc.Index(PINECONE_INDEX_NAME)
    
    # Get index stats before deletion
    stats = index.describe_index_stats()
    logger.info(f"Index stats before deletion:")
    logger.info(f"  Total vectors: {stats.get('total_vector_count', 0)}")
    logger.info(f"  Namespaces: {list(stats.get('namespaces', {}).keys())}")
    
    # Confirm deletion
    logger.warning(f"\n⚠️  You are about to DELETE {len(NAMESPACES_TO_DELETE)} namespace(s):")
    for ns in NAMESPACES_TO_DELETE:
        ns_stats = stats.get('namespaces', {}).get(ns, {})
        vector_count = ns_stats.get('vector_count', 0)
        logger.warning(f"  - '{ns}' ({vector_count} vectors)")
    
    confirmation = input("\nType 'DELETE' to confirm: ")
    if confirmation != "DELETE":
        logger.info("Deletion cancelled.")
        return
    
    # Delete namespaces
    logger.info("\n🚀 Starting deletion...")
    success_count = 0
    
    for namespace in NAMESPACES_TO_DELETE:
        if delete_namespace(index, namespace):
            success_count += 1
    
    # Get index stats after deletion
    logger.info("\n" + "="*50)
    stats_after = index.describe_index_stats()
    logger.info(f"Index stats after deletion:")
    logger.info(f"  Total vectors: {stats_after.get('total_vector_count', 0)}")
    logger.info(f"  Namespaces: {list(stats_after.get('namespaces', {}).keys())}")
    
    logger.info(f"\n✓ Deletion complete: {success_count}/{len(NAMESPACES_TO_DELETE)} namespaces deleted")


if __name__ == "__main__":
    main()
