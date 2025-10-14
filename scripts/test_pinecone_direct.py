"""
Direct Pinecone Test Script
Tests if vectors can be retrieved directly without LangChain
"""

import os
from pathlib import Path
import sys

# Add project root to path
project_root = Path(__file__).parent.parent
sys.path.insert(0, str(project_root))

from pinecone import Pinecone
from dotenv import load_dotenv
from langchain_openai import OpenAIEmbeddings

load_dotenv()

def test_direct_pinecone_query():
    """Test direct Pinecone query without LangChain wrapper"""
    
    # Initialize Pinecone
    pc = Pinecone(api_key=os.getenv("PINECONE_API_KEY"))
    index = pc.Index("inspigo-pinecone")
    
    # Get namespace stats
    stats = index.describe_index_stats()
    print(f"\n📊 PINECONE STATS:")
    print(f"Total vectors: {stats.total_vector_count}")
    print(f"\nNamespaces:")
    for ns_name, ns_stats in stats.namespaces.items():
        print(f"  - {ns_name}: {ns_stats.vector_count} vectors")
    
    # Test namespace
    test_namespace = "danamon-test-1"
    print(f"\n🔍 TESTING NAMESPACE: {test_namespace}")
    
    if test_namespace not in stats.namespaces:
        print(f"❌ Namespace '{test_namespace}' not found!")
        return
    
    print(f"✅ Namespace exists with {stats.namespaces[test_namespace].vector_count} vectors")
    
    # Create embedding for test query
    embeddings = OpenAIEmbeddings(model="text-embedding-3-small")
    test_query = "ada tanggal apa di dokumen ini?"
    query_embedding = embeddings.embed_query(test_query)
    
    print(f"\n🧪 TEST QUERY: '{test_query}'")
    print(f"Embedding dimension: {len(query_embedding)}")
    
    # Query 1: No filter - get all vectors in namespace
    print(f"\n📤 QUERY 1: No filter (get all from namespace)")
    results_no_filter = index.query(
        namespace=test_namespace,
        vector=query_embedding,
        top_k=10,
        include_metadata=True
    )
    
    print(f"Results: {len(results_no_filter['matches'])} documents")
    for i, match in enumerate(results_no_filter['matches'][:3]):
        print(f"\n  Match {i+1}:")
        print(f"    ID: {match['id']}")
        print(f"    Score: {match['score']:.4f}")
        metadata = match.get('metadata', {})
        print(f"    source_document: {metadata.get('source_document', 'N/A')}")
        print(f"    version: {metadata.get('version', 'N/A')}")
        text_preview = metadata.get('text', '')[:100] if metadata.get('text') else 'N/A'
        print(f"    text preview: {text_preview}...")
    
    # Query 2: With filter
    print(f"\n📤 QUERY 2: With filter (source_document + version)")
    
    # Get the first doc_id from results to test filtering
    if results_no_filter['matches']:
        test_doc_id = results_no_filter['matches'][0]['metadata'].get('source_document')
        test_version = results_no_filter['matches'][0]['metadata'].get('version', 'v2')
        
        print(f"Filtering for: doc_id={test_doc_id}, version={test_version}")
        
        # Test different filter syntaxes
        filters = [
            # Syntax 1: Simple equality
            {
                "source_document": test_doc_id,
                "version": test_version
            },
            # Syntax 2: MongoDB-style $eq
            {
                "source_document": {"$eq": test_doc_id},
                "version": {"$eq": test_version}
            },
            # Syntax 3: $and wrapper
            {
                "$and": [
                    {"source_document": {"$eq": test_doc_id}},
                    {"version": {"$eq": test_version}}
                ]
            }
        ]
        
        for idx, test_filter in enumerate(filters, 1):
            print(f"\n  Filter Syntax {idx}: {test_filter}")
            try:
                results_filtered = index.query(
                    namespace=test_namespace,
                    vector=query_embedding,
                    top_k=10,
                    filter=test_filter,
                    include_metadata=True
                )
                print(f"  ✅ Results: {len(results_filtered['matches'])} documents")
                if results_filtered['matches']:
                    first_match = results_filtered['matches'][0]
                    print(f"     First match ID: {first_match['id']}, Score: {first_match['score']:.4f}")
            except Exception as e:
                print(f"  ❌ Error: {e}")
    
    print(f"\n✅ TEST COMPLETED")

if __name__ == "__main__":
    test_direct_pinecone_query()
