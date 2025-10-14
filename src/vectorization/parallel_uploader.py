"""
Parallel Vectorization Uploader

Optimized batch uploader with parallel threading for faster Pinecone uploads.
Uses global_config.json for configuration.
"""

import asyncio
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import List, Dict, Any, Optional, Tuple
from datetime import datetime
from loguru import logger

from ..core.enhancement_profiles import ProfileLoader


async def upload_vectors_parallel(
    vectors: List[Dict[str, Any]],
    pinecone_index,
    namespace: str,
    batch_size: int = 100,
    max_threads: int = 4,
    max_concurrent_batches: int = 2
) -> Tuple[int, int]:
    """
    Upload vectors to Pinecone with parallel threading.
    
    Args:
        vectors: List of vectors to upload
        pinecone_index: Pinecone index instance
        namespace: Namespace to upload to
        batch_size: Size of each batch
        max_threads: Maximum number of parallel threads
        max_concurrent_batches: Maximum concurrent batch uploads
        
    Returns:
        Tuple of (successful_count, failed_count)
    """
    total_vectors = len(vectors)
    
    if total_vectors == 0:
        logger.warning("No vectors to upload")
        return 0, 0
    
    logger.info(f"[Parallel Upload] Starting upload of {total_vectors} vectors to namespace '{namespace}'")
    logger.info(f"[Parallel Upload] Batch size: {batch_size}, Threads: {max_threads}, Max concurrent batches: {max_concurrent_batches}")
    
    # Split into batches
    batches = []
    for i in range(0, total_vectors, batch_size):
        batch = vectors[i:i + batch_size]
        batches.append((i // batch_size + 1, batch))
    
    total_batches = len(batches)
    logger.info(f"[Parallel Upload] Total batches: {total_batches}")
    
    successful_count = 0
    failed_count = 0
    
    # Process batches in parallel with concurrency limit
    semaphore = asyncio.Semaphore(max_concurrent_batches)
    
    async def upload_batch(batch_num: int, batch_vectors: List[Dict]) -> bool:
        """Upload a single batch"""
        async with semaphore:
            try:
                # Use ThreadPoolExecutor for Pinecone I/O
                loop = asyncio.get_event_loop()
                await loop.run_in_executor(
                    None,
                    lambda: pinecone_index.upsert(
                        vectors=batch_vectors,
                        namespace=namespace
                    )
                )
                
                logger.debug(f"[Parallel Upload] Batch {batch_num}/{total_batches} completed ({len(batch_vectors)} vectors)")
                return True
                
            except Exception as e:
                logger.error(f"[Parallel Upload] Batch {batch_num}/{total_batches} failed: {e}")
                return False
    
    # Execute all batches concurrently
    start_time = asyncio.get_event_loop().time()
    tasks = [upload_batch(batch_num, batch_vectors) for batch_num, batch_vectors in batches]
    results = await asyncio.gather(*tasks, return_exceptions=True)
    duration = asyncio.get_event_loop().time() - start_time
    
    # Count successes and failures
    for i, result in enumerate(results):
        if isinstance(result, Exception):
            failed_count += len(batches[i][1])
            logger.error(f"Batch {i+1} exception: {result}")
        elif result:
            successful_count += len(batches[i][1])
        else:
            failed_count += len(batches[i][1])
    
    logger.info(f"[Parallel Upload] Completed in {duration:.1f}s")
    logger.info(f"[Parallel Upload] Success: {successful_count}/{total_vectors}, Failed: {failed_count}/{total_vectors}")
    
    return successful_count, failed_count


async def vectorize_and_upload_optimized(
    chunks: List[str],
    embeddings_function,
    pinecone_index,
    namespace: str,
    doc_id: str,
    metadata_base: Optional[Dict[str, Any]] = None,
    progress_callback: Optional[callable] = None
) -> Dict[str, Any]:
    """
    Vectorize chunks and upload with optimized parallel processing.
    
    Args:
        chunks: List of text chunks to vectorize
        embeddings_function: Function to generate embeddings
        pinecone_index: Pinecone index instance
        namespace: Namespace to upload to
        doc_id: Document ID
        metadata_base: Base metadata to attach to vectors
        progress_callback: Optional callback(current, total, percentage)
        
    Returns:
        Dict with upload results
    """
    # Load configuration
    profile_loader = ProfileLoader()
    global_config = profile_loader.load_global_config()
    
    batch_size = global_config.vectorstore.upload_batch_size
    upload_threads = global_config.vectorstore.upload_threads
    max_concurrent_batches = global_config.vectorstore.max_concurrent_batches
    
    logger.info(f"[Optimized Vectorizer] Processing {len(chunks)} chunks for doc: {doc_id}")
    logger.info(f"[Optimized Vectorizer] Config: batch_size={batch_size}, threads={upload_threads}, concurrent_batches={max_concurrent_batches}")
    
    start_time = datetime.now()
    
    # Phase 1: Generate embeddings
    logger.info(f"[Optimized Vectorizer] Generating embeddings...")
    embeddings_list = embeddings_function.embed_documents(chunks)
    
    if progress_callback:
        progress_callback(len(chunks), len(chunks), 50)  # Embeddings 50% of total work
    
    # Phase 2: Prepare vectors with metadata
    vectors = []
    for i, (chunk, embedding) in enumerate(zip(chunks, embeddings_list)):
        vector_id = f"{doc_id}_chunk_{i}"
        
        metadata = {
            **(metadata_base or {}),
            "chunk_index": i,
            "total_chunks": len(chunks),
            "upload_timestamp": datetime.now().isoformat(),
            "text": chunk,
            "char_count": len(chunk)
        }
        
        vectors.append({
            "id": vector_id,
            "values": embedding,
            "metadata": metadata
        })
    
    # Phase 3: Parallel upload
    logger.info(f"[Optimized Vectorizer] Uploading {len(vectors)} vectors in parallel...")
    successful, failed = await upload_vectors_parallel(
        vectors=vectors,
        pinecone_index=pinecone_index,
        namespace=namespace,
        batch_size=batch_size,
        max_threads=upload_threads,
        max_concurrent_batches=max_concurrent_batches
    )
    
    if progress_callback:
        progress_callback(len(chunks), len(chunks), 100)
    
    duration = (datetime.now() - start_time).total_seconds()
    
    result = {
        "doc_id": doc_id,
        "namespace": namespace,
        "total_chunks": len(chunks),
        "successful_uploads": successful,
        "failed_uploads": failed,
        "duration_seconds": duration,
        "chunks_per_second": len(chunks) / duration if duration > 0 else 0
    }
    
    logger.info(f"[Optimized Vectorizer] Complete: {successful}/{len(chunks)} vectors uploaded in {duration:.1f}s ({result['chunks_per_second']:.1f} chunks/sec)")
    
    return result
