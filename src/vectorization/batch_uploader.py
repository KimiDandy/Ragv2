"""
Batch Markdown Upload to Pinecone with Namespace Support

This module handles uploading multiple enhanced markdown files to a specific
Pinecone namespace for production deployment.

Features:
- Batch processing of multiple markdown files
- Namespace-specific vectorization
- Progress tracking
- Error handling and validation
- Metadata tracking

Usage:
    from src.vectorization.batch_uploader import batch_upload_to_namespace
    
    result = batch_upload_to_namespace(
        markdown_files=["file1.md", "file2.md"],
        namespace_id="client-a-final",
        pinecone_index=index,
        embeddings=embeddings_function
    )
"""

from pathlib import Path
from typing import List, Dict, Any, Optional, Tuple
import uuid
from datetime import datetime
from langchain.text_splitter import RecursiveCharacterTextSplitter
from langchain_openai import OpenAIEmbeddings
from loguru import logger

from ..core.config import EMBEDDING_MODEL
from ..core.namespaces_config import (
    validate_namespace,
    get_namespace_by_id,
    update_namespace_stats,
    get_namespace_display_info,
    is_production_namespace
)
from ..observability.token_ledger import log_tokens
from ..observability.token_counter import count_tokens


def validate_markdown_file(file_path: Path) -> Tuple[bool, Optional[str]]:
    """
    Validate a markdown file before processing.
    
    Args:
        file_path (Path): Path to markdown file
        
    Returns:
        Tuple[bool, Optional[str]]: (is_valid, error_message)
    """
    if not file_path.exists():
        return False, f"File tidak ditemukan: {file_path}"
    
    if not file_path.suffix.lower() in ['.md', '.markdown']:
        return False, f"File bukan markdown: {file_path.suffix}"
    
    try:
        with open(file_path, 'r', encoding='utf-8') as f:
            content = f.read()
        
        if len(content.strip()) == 0:
            return False, "File kosong"
        
        if len(content) < 100:
            logger.warning(f"File sangat pendek ({len(content)} chars): {file_path.name}")
        
        return True, None
    
    except Exception as e:
        return False, f"Error membaca file: {str(e)}"


def process_single_markdown(
    file_path: Path,
    namespace_id: str,
    pinecone_index,
    embeddings: OpenAIEmbeddings,
    chunk_size: int = 1000,
    chunk_overlap: int = 150
) -> Tuple[bool, Dict[str, Any]]:
    """
    Process and upload a single markdown file to Pinecone namespace.
    
    Args:
        file_path (Path): Path to markdown file
        namespace_id (str): Target namespace ID
        pinecone_index: Pinecone index instance
        embeddings (OpenAIEmbeddings): Embedding function
        chunk_size (int): Size of text chunks
        chunk_overlap (int): Overlap between chunks
        
    Returns:
        Tuple[bool, Dict]: (success, result_info)
    """
    # Validate file
    is_valid, error_msg = validate_markdown_file(file_path)
    if not is_valid:
        logger.error(f"Validation failed for {file_path.name}: {error_msg}")
        return False, {
            "filename": file_path.name,
            "status": "failed",
            "error": error_msg,
            "chunks_uploaded": 0
        }
    
    try:
        # Read content
        with open(file_path, 'r', encoding='utf-8') as f:
            content = f.read()
        
        # Generate unique document ID for this upload
        doc_id = f"batch_{uuid.uuid4().hex[:12]}"
        filename = file_path.name
        
        logger.info(f"Processing: {filename} → namespace '{namespace_id}' (doc_id: {doc_id})")
        
        # Split into chunks
        text_splitter = RecursiveCharacterTextSplitter(
            chunk_size=chunk_size,
            chunk_overlap=chunk_overlap,
            separators=["\n\n", "\n", " ", ""],
        )
        chunks = text_splitter.split_text(content)
        
        if not chunks:
            logger.warning(f"No chunks generated from {filename}")
            return False, {
                "filename": filename,
                "status": "failed",
                "error": "No chunks generated",
                "chunks_uploaded": 0
            }
        
        logger.info(f"Split {filename} into {len(chunks)} chunks")
        
        # Track tokens for embeddings
        total_text = " ".join(chunks)
        input_tokens = count_tokens(total_text)
        
        # Generate embeddings
        embeddings_list = embeddings.embed_documents(chunks)
        
        # Prepare vectors with metadata
        vectors_to_upsert = []
        for i, (chunk, embedding) in enumerate(zip(chunks, embeddings_list)):
            vector_id = f"{doc_id}_chunk_{i}"
            
            metadata = {
                "source_document": doc_id,
                "source_filename": filename,
                "chunk_index": i,
                "total_chunks": len(chunks),
                "namespace": namespace_id,
                "upload_timestamp": datetime.now().isoformat(),
                "text": chunk,  # Store actual text for retrieval
                "char_count": len(chunk)
            }
            
            vectors_to_upsert.append({
                "id": vector_id,
                "values": embedding,
                "metadata": metadata
            })
        
        # Upload to Pinecone in batches (with namespace)
        batch_size = 100
        uploaded_count = 0
        
        for i in range(0, len(vectors_to_upsert), batch_size):
            batch = vectors_to_upsert[i:i + batch_size]
            
            try:
                # Upsert with namespace parameter
                pinecone_index.upsert(
                    vectors=batch,
                    namespace=namespace_id  # ← KEY PARAMETER!
                )
                uploaded_count += len(batch)
                logger.debug(f"Uploaded batch {i//batch_size + 1}/{(len(vectors_to_upsert) + batch_size - 1)//batch_size}")
            
            except Exception as e:
                logger.error(f"Failed to upsert batch {i//batch_size + 1}: {e}")
                # Continue with other batches instead of failing completely
        
        # Log token usage
        log_tokens(
            step="embed",
            model=EMBEDDING_MODEL,
            input_tokens=input_tokens,
            output_tokens=0,
            phase="batch_upload",
            namespace=namespace_id,
            filename=filename,
            num_chunks=len(chunks),
            total_chars=len(total_text)
        )
        
        success = uploaded_count == len(vectors_to_upsert)
        
        result = {
            "filename": filename,
            "doc_id": doc_id,
            "status": "success" if success else "partial",
            "chunks_total": len(chunks),
            "chunks_uploaded": uploaded_count,
            "input_tokens": input_tokens,
            "namespace": namespace_id
        }
        
        if success:
            logger.info(f"✓ Successfully uploaded {filename}: {uploaded_count} chunks → '{namespace_id}'")
        else:
            logger.warning(f"⚠ Partially uploaded {filename}: {uploaded_count}/{len(vectors_to_upsert)} chunks")
        
        return success, result
    
    except Exception as e:
        logger.error(f"Error processing {file_path.name}: {e}")
        return False, {
            "filename": file_path.name,
            "status": "failed",
            "error": str(e),
            "chunks_uploaded": 0
        }


def batch_upload_to_namespace(
    markdown_files: List[Path],
    namespace_id: str,
    pinecone_index,
    embeddings: Optional[OpenAIEmbeddings] = None,
    chunk_size: int = 1000,
    chunk_overlap: int = 150
) -> Dict[str, Any]:
    """
    Upload multiple markdown files to a specific Pinecone namespace.
    
    Args:
        markdown_files (List[Path]): List of markdown file paths
        namespace_id (str): Target namespace ID
        pinecone_index: Pinecone index instance
        embeddings (Optional[OpenAIEmbeddings]): Embedding function (will create if None)
        chunk_size (int): Chunk size for text splitting
        chunk_overlap (int): Chunk overlap
        
    Returns:
        Dict: Upload results with statistics
    """
    # Validate namespace
    if not validate_namespace(namespace_id):
        return {
            "success": False,
            "error": f"Invalid namespace: {namespace_id}",
            "files_processed": 0,
            "files_succeeded": 0,
            "files_failed": 0
        }
    
    namespace_info = get_namespace_display_info(namespace_id)
    logger.info(f"Starting batch upload to namespace: {namespace_info['name']}")
    logger.info(f"Files to process: {len(markdown_files)}")
    
    # Initialize embeddings if not provided
    if embeddings is None:
        logger.info("Initializing OpenAI embeddings...")
        embeddings = OpenAIEmbeddings(model=EMBEDDING_MODEL)
    
    # Process each file
    results = []
    total_chunks = 0
    total_tokens = 0
    succeeded = 0
    failed = 0
    
    for idx, file_path in enumerate(markdown_files, start=1):
        logger.info(f"Processing file {idx}/{len(markdown_files)}: {file_path.name}")
        
        success, result = process_single_markdown(
            file_path=file_path,
            namespace_id=namespace_id,
            pinecone_index=pinecone_index,
            embeddings=embeddings,
            chunk_size=chunk_size,
            chunk_overlap=chunk_overlap
        )
        
        results.append(result)
        
        if success:
            succeeded += 1
            total_chunks += result.get("chunks_uploaded", 0)
            total_tokens += result.get("input_tokens", 0)
        else:
            failed += 1
    
    # Update namespace metadata and get updated stats directly
    accumulated_metadata = update_namespace_stats(
        namespace_id=namespace_id,
        documents_added=succeeded,
        chunks_added=total_chunks,
        tokens_used=total_tokens
    )
    
    # Prepare summary with accumulated stats
    summary = {
        "success": failed == 0,
        "namespace_id": namespace_id,
        "namespace_name": namespace_info["name"],
        "files_processed": len(markdown_files),
        "files_succeeded": succeeded,
        "files_failed": failed,
        "total_chunks_uploaded": total_chunks,
        "total_input_tokens": total_tokens,
        "upload_timestamp": datetime.now().isoformat(),
        "detailed_results": results,
        # NEW: Accumulated namespace stats
        "namespace_accumulated_stats": {
            "total_documents": accumulated_metadata.get("document_count", 0),
            "total_chunks": accumulated_metadata.get("total_chunks", 0),
            "total_tokens": accumulated_metadata.get("total_tokens", 0),
            "created_at": accumulated_metadata.get("created_at"),
            "last_updated": accumulated_metadata.get("last_updated")
        }
    }
    
    if failed == 0:
        logger.info(f"✅ Batch upload completed successfully!")
        logger.info(f"   Namespace: {namespace_info['name']}")
        logger.info(f"   Files: {succeeded}/{len(markdown_files)}")
        logger.info(f"   Chunks: {total_chunks}")
    else:
        logger.warning(f"⚠️ Batch upload completed with errors!")
        logger.warning(f"   Succeeded: {succeeded}, Failed: {failed}")
    
    return summary


def clear_namespace(namespace_id: str, pinecone_index) -> Dict[str, Any]:
    """
    Clear all vectors from a specific namespace.
    
    WARNING: This will delete ALL vectors in the namespace!
    
    Args:
        namespace_id (str): Namespace to clear
        pinecone_index: Pinecone index instance
        
    Returns:
        Dict: Operation result
    """
    if not validate_namespace(namespace_id):
        return {
            "success": False,
            "error": f"Invalid namespace: {namespace_id}"
        }
    
    namespace_info = get_namespace_display_info(namespace_id)
    
    # Safety check for production namespaces
    if is_production_namespace(namespace_id):
        logger.error(f"Cannot clear production namespace: {namespace_id}")
        return {
            "success": False,
            "error": "Cannot clear production namespace (type='final'). Disable this check if needed."
        }
    
    try:
        logger.warning(f"Clearing namespace: {namespace_info['name']} ({namespace_id})")
        
        # Delete all vectors in namespace
        pinecone_index.delete(delete_all=True, namespace=namespace_id)
        
        # Reset metadata
        update_namespace_stats(
            namespace_id=namespace_id,
            documents_added=0,
            chunks_added=0,
            tokens_used=0
        )
        
        logger.info(f"✓ Namespace cleared: {namespace_id}")
        
        return {
            "success": True,
            "namespace_id": namespace_id,
            "message": f"Namespace cleared: {namespace_info['name']}"
        }
    
    except Exception as e:
        logger.error(f"Failed to clear namespace {namespace_id}: {e}")
        return {
            "success": False,
            "error": str(e)
        }
