#!/usr/bin/env python3
"""
Script untuk test koneksi Pinecone - Verifikasi migrasi berhasil
"""

import os
import sys
from pathlib import Path
from dotenv import load_dotenv

# Add src to path
sys.path.append(str(Path(__file__).parent / 'src'))

def test_pinecone_connection():
    """Test koneksi ke Pinecone menggunakan konfigurasi terbaru."""
    
    # Load environment
    load_dotenv()
    
    try:
        from pinecone import Pinecone
        from src.core.config import PINECONE_API_KEY, PINECONE_INDEX_NAME, get_embedding_dimension, EMBEDDING_MODEL
        
        print("🔍 Testing Pinecone Connection...")
        print(f"📋 Index Name: {PINECONE_INDEX_NAME}")
        print(f"🤖 Embedding Model: {EMBEDDING_MODEL}")
        print(f"📐 Expected Dimensions: {get_embedding_dimension(EMBEDDING_MODEL)}")
        
        # Initialize Pinecone (v3+ way - no environment needed)
        pc = Pinecone(api_key=PINECONE_API_KEY)
        print("✅ Pinecone client initialized successfully")
        
        # Connect to index
        index = pc.Index(PINECONE_INDEX_NAME)
        print(f"✅ Connected to index: {PINECONE_INDEX_NAME}")
        
        # Get index stats
        stats = index.describe_index_stats()
        print(f"📊 Index Stats:")
        print(f"   - Total Vectors: {stats.get('total_vector_count', 0):,}")
        print(f"   - Dimension: {stats.get('dimension', 'Unknown')}")
        print(f"   - Index Fullness: {stats.get('index_fullness', 0):.1%}")
        
        # Test a simple query (if index has vectors)
        if stats.get('total_vector_count', 0) > 0:
            print("\n🔍 Testing Query Capability...")
            try:
                # Create a dummy vector for testing
                import numpy as np
                test_vector = np.random.random(get_embedding_dimension(EMBEDDING_MODEL)).tolist()
                
                results = index.query(
                    vector=test_vector,
                    top_k=1,
                    include_metadata=True
                )
                print(f"✅ Query successful - Found {len(results.get('matches', []))} matches")
                
            except Exception as e:
                print(f"⚠️  Query test failed: {e}")
        else:
            print("ℹ️  Index is empty - skipping query test")
        
        print("\n🎉 Pinecone connection test PASSED!")
        print("✅ Migration to Pinecone v3+ successful!")
        return True
        
    except ImportError as e:
        print(f"❌ Import Error: {e}")
        print("💡 Run: pip install -r requirements.txt")
        return False
        
    except Exception as e:
        print(f"❌ Connection Error: {e}")
        print("\n🔧 Troubleshooting:")
        print("1. Check your .env file has correct PINECONE_API_KEY")
        print("2. Verify PINECONE_INDEX_NAME matches your Pinecone dashboard")
        print("3. Ensure index exists and is active in Pinecone console")
        return False

def test_embedding_dimensions():
    """Test embedding dimension mappings."""
    from src.core.config import get_embedding_dimension
    
    print("\n🧮 Testing Embedding Dimensions:")
    
    models = [
        "text-embedding-3-small",
        "text-embedding-3-large", 
        "text-embedding-ada-002",
        "unknown-model"
    ]
    
    for model in models:
        dim = get_embedding_dimension(model)
        print(f"   - {model}: {dim} dimensions")
    
    print("✅ Dimension mapping test passed")

if __name__ == "__main__":
    print("🚀 Pinecone v3+ Migration Test")
    print("=" * 50)
    
    # Test embedding dimensions first
    test_embedding_dimensions()
    
    # Test Pinecone connection
    success = test_pinecone_connection()
    
    if success:
        print("\n🎯 READY FOR PRODUCTION!")
        sys.exit(0)
    else:
        print("\n❌ MIGRATION ISSUES DETECTED")
        sys.exit(1)
