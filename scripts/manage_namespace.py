"""
Simple namespace management utility.

Commands:
    list    - List all namespaces and their stats
    clean   - Delete all vectors in specified namespace(s)
    delete  - Delete namespace(s) completely
"""

from pinecone import Pinecone
from loguru import logger
import os
from dotenv import load_dotenv
from typing import List

# Load environment
load_dotenv()

PINECONE_API_KEY = os.getenv("PINECONE_API_KEY")
PINECONE_INDEX_NAME = os.getenv("PINECONE_INDEX_NAME", "inspigo-pinecone")


def list_namespaces(index):
    """List all namespaces with stats"""
    stats = index.describe_index_stats()
    namespaces = stats.get('namespaces', {})
    
    logger.info(f"\n{'='*60}")
    logger.info(f"Index: {PINECONE_INDEX_NAME}")
    logger.info(f"Total Vectors: {stats.get('total_vector_count', 0):,}")
    logger.info(f"Total Namespaces: {len(namespaces)}")
    logger.info(f"{'='*60}\n")
    
    if not namespaces:
        logger.warning("No namespaces found!")
        return
    
    for ns_name, ns_stats in sorted(namespaces.items()):
        vector_count = ns_stats.get('vector_count', 0)
        logger.info(f"  📦 {ns_name}: {vector_count:,} vectors")


def delete_namespaces(index, namespace_ids: List[str]):
    """Delete specified namespaces"""
    if not namespace_ids:
        logger.warning("No namespaces specified!")
        return
    
    stats = index.describe_index_stats()
    existing_ns = stats.get('namespaces', {})
    
    logger.info(f"\n🗑️  Deleting {len(namespace_ids)} namespace(s)...")
    
    success = 0
    for ns_id in namespace_ids:
        try:
            if ns_id not in existing_ns:
                logger.warning(f"  ⚠️  Namespace '{ns_id}' not found, skipping")
                continue
            
            vector_count = existing_ns[ns_id].get('vector_count', 0)
            logger.info(f"  Deleting '{ns_id}' ({vector_count} vectors)...")
            
            index.delete(delete_all=True, namespace=ns_id)
            logger.success(f"  ✓ Deleted '{ns_id}'")
            success += 1
            
        except Exception as e:
            logger.error(f"  ✗ Failed to delete '{ns_id}': {e}")
    
    logger.info(f"\n✓ Deletion complete: {success}/{len(namespace_ids)} namespaces deleted")


def main():
    """Main CLI"""
    print("\n" + "="*60)
    print("  PINECONE NAMESPACE MANAGER")
    print("="*60)
    
    # Connect to Pinecone
    logger.info(f"Connecting to index: {PINECONE_INDEX_NAME}...")
    pc = Pinecone(api_key=PINECONE_API_KEY)
    index = pc.Index(PINECONE_INDEX_NAME)
    
    while True:
        print("\nCommands:")
        print("  1. List namespaces")
        print("  2. Delete namespace(s)")
        print("  3. Exit")
        
        choice = input("\nEnter choice (1-3): ").strip()
        
        if choice == "1":
            list_namespaces(index)
            
        elif choice == "2":
            list_namespaces(index)
            print("\nEnter namespace IDs to delete (comma-separated):")
            print("Example: client-a-testing-1, client-a-testing-2")
            ns_input = input("> ").strip()
            
            if not ns_input:
                logger.warning("No namespaces specified")
                continue
            
            # Parse input
            namespaces = [ns.strip() for ns in ns_input.split(",") if ns.strip()]
            
            if not namespaces:
                logger.warning("No valid namespaces specified")
                continue
            
            # Confirm
            logger.warning(f"\n⚠️  About to DELETE {len(namespaces)} namespace(s):")
            for ns in namespaces:
                print(f"    - {ns}")
            
            confirm = input("\nType 'YES' to confirm: ").strip()
            
            if confirm == "YES":
                delete_namespaces(index, namespaces)
            else:
                logger.info("Cancelled")
                
        elif choice == "3":
            logger.info("Goodbye!")
            break
            
        else:
            logger.warning("Invalid choice")


if __name__ == "__main__":
    main()
