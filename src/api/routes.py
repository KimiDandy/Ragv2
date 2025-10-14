import shutil
import tempfile
import asyncio
import json
from pathlib import Path
import hashlib
import uuid
from typing import Union, Optional, List
from datetime import datetime
from fastapi import APIRouter, UploadFile, File, HTTPException, Request
from pydantic import BaseModel
from loguru import logger
from langchain_pinecone import PineconeVectorStore
from langchain_core.prompts import PromptTemplate
from ..core.config import (
    EMBEDDING_MODEL,
    CHAT_MODEL,
    PINECONE_INDEX_NAME,
    PIPELINE_ARTEFACTS_DIR,
)
from ..core.rate_limiter import AsyncLeakyBucket
from ..extraction.extractor import extract_pdf_to_markdown
from ..synthesis.synthesizer import synthesize_final_markdown
from ..vectorization.vectorizer import vectorize_and_store
from ..shared.document_meta import (
    get_markdown_path,
    get_markdown_relative_path,
    get_base_name,
    default_markdown_filename,
    set_original_pdf_filename,
    get_original_pdf_filename,
)
from ..rag.retriever import build_rag_chain, answer_with_sources
from ..observability.token_ledger import get_token_ledger, log_tokens
from .schemas import (
    RetrievedSource,
    TokenUsage,
    AskSingleVersionResponse,
    AskBothVersionsResponse,
    QueryRequest,
)

# Global progress tracking for enhancement tasks
progress_state = {}

# Global rate limiter instance
rate_limiter = AsyncLeakyBucket(rps=2.0)  # 2 requests per second

router = APIRouter()


def _build_snippet(text: str, max_len: int = 280) -> str:
    """Return a short snippet for display, cut on word boundary."""
    t = (text or "").strip()
    if len(t) <= max_len:
        return t
    cut = t[: max_len].rsplit(" ", 1)[0]
    return (cut or t[: max_len]).strip() + "…"


def _minmax_normalize(values: list[float], invert: bool = False) -> list[float]:
    if not values:
        return []
    vmin, vmax = min(values), max(values)
    if vmax == vmin:
        return [1.0 for _ in values]
    norm = [ (v - vmin) / (vmax - vmin) for v in values ]
    return [1.0 - n if invert else n for n in norm]


def _docs_to_sources(docs_with_scores: list[tuple], already_relevance: bool) -> list[RetrievedSource]:
    """Convert (Document, score) to RetrievedSource list with normalized scores and snippets."""
    docs = [d for d, _ in docs_with_scores]
    scores = [s for _, s in docs_with_scores]
    if already_relevance:
        norm_scores = _minmax_normalize(scores, invert=False) if (min(scores) < 0 or max(scores) > 1) else scores
    else:
        norm_scores = _minmax_normalize(scores, invert=True)

    sources: list[RetrievedSource] = []
    for (doc, _), ns in zip(docs_with_scores, norm_scores):
        content = getattr(doc, "page_content", "") or ""
        metadata = getattr(doc, "metadata", {}) or {}
        sid = hashlib.md5(content.encode("utf-8")).hexdigest()[:12]
        sources.append(
            RetrievedSource(
                id=sid,
                score=float(round(ns, 4)),
                snippet=_build_snippet(content, 300),
                metadata=metadata,
            )
        )
    sources.sort(key=lambda x: x.score, reverse=True)
    return sources

async def perform_rag_query(prompt: str, doc_id: str, version: str, request: Request, trace: bool = False, k: int = 5) -> tuple[str, list[RetrievedSource], dict]:
    """
    Menjalankan kueri RAG terhadap versi dokumen tertentu (v1 atau v2).
    
    UPDATED: Menggunakan create_retrieval_chain untuk mendapatkan evidence yang tepat
    dari dokumen yang benar-benar digunakan LLM.

    Args:
        prompt (str): Pertanyaan dari pengguna.
        doc_id (str): ID unik dokumen yang akan dikueri.
        version (str): Versi dokumen ('v1' untuk asli, 'v2' untuk diperkaya).
        request (Request): Objek request FastAPI untuk mengakses state aplikasi.
        trace (bool): Jika True, kembalikan juga sumber evidence hasil retrieval.
        k (int): Top-k dokumen yang diambil untuk evidence.

    Returns:
        tuple[str, list[RetrievedSource]]: Jawaban dan daftar sumber yang tepat.
    """
    try:
        from ..core.enhancement_profiles.profile_loader import ProfileLoader
        from ..rag.custom_pinecone_retriever import create_custom_filtered_retriever
        
        pinecone_index = request.app.state.pinecone_index
        embeddings = request.app.state.embedding_function
        llm = request.app.state.chat_model

        if not all([pinecone_index, embeddings, llm]):
            raise ValueError("Koneksi database atau model AI tidak tersedia. Periksa log startup.")

        # CRITICAL FIX: Get active namespace from config
        profile_loader = ProfileLoader()
        active_namespace = profile_loader.get_active_namespace()
        logger.info(f"Querying namespace: {active_namespace} for document: {doc_id} (version: {version})")

        # CRITICAL FIX: Use custom retriever that directly calls Pinecone
        # LangChain's PineconeVectorStore.as_retriever() has namespace issues
        logger.info(f"Using CustomPineconeRetriever (bypasses LangChain wrapper)")
        
        # IMPORTANT: Search ALL documents in namespace (not just specific doc_id)
        # This allows queries to find information across multiple uploaded documents
        logger.info(f"Search mode: ALL documents in namespace (not filtered by doc_id)")
        retriever = create_custom_filtered_retriever(
            pinecone_index=pinecone_index,
            embeddings=embeddings,
            namespace=active_namespace,
            doc_id=None,  # None = search all documents in namespace
            version=version,
            k=k
        )
        
        # Buat RAG chain menggunakan create_retrieval_chain
        rag_chain = build_rag_chain(retriever, model=llm.model_name or "gpt-4.1")
        
        # Jalankan RAG dengan token tracking (jawaban diambil dari chain)
        logger.info(f"Menjalankan RAG chain untuk dokumen '{doc_id}' versi '{version}'...")
        result = answer_with_sources(
            rag_chain=rag_chain,
            question=prompt,
            model=llm.model_name or "gpt-4.1",
            trace_id=f"{doc_id}_{version}"
        )
        answer = result["answer"]
        token_usage = result.get("token_usage", {"input_tokens": 0, "output_tokens": 0, "total_tokens": 0})

        # Ambil evidence dengan skor relevansi langsung dari Chroma agar skor tidak 0%
        sources: list[RetrievedSource] = []
        if trace:
            retrieval_filter = {
                '$and': [
                    {'source_document': {'$eq': doc_id}},
                    {'version': {'$eq': version}}
                ]
            }
            try:
                docs_with_scores = vector_store.similarity_search_with_relevance_scores(
                    prompt, k=k, filter=retrieval_filter
                )
                # True karena Chroma mengembalikan skor 0..1 (semakin tinggi semakin relevan)
                sources = _docs_to_sources(docs_with_scores, already_relevance=True)
            except Exception:
                # Fallback: tanpa skor, gunakan hasil dari chain jika ada
                sources_data = result.get("sources", [])
                for si in sources_data:
                    try:
                        sources.append(RetrievedSource(
                            id=si.get("id", ""),
                            score=float(si.get("score") or 0.0),
                            snippet=str(si.get("snippet") or ""),
                            metadata=dict(si.get("metadata") or {})
                        ))
                    except Exception:
                        continue

        return answer.strip(), sources, token_usage

    except Exception as e:
        logger.error(f"Error dalam RAG query untuk versi {version}: {e}")
        return f"Terjadi kesalahan teknis saat mengambil jawaban: {str(e)}", [], {"input_tokens": 0, "output_tokens": 0, "total_tokens": 0}


# ===============================================
# LEGACY MANUAL WORKFLOW ENDPOINTS - REMOVED
# ===============================================
# Old 3-phase manual workflow has been replaced by
# automated DocumentOrchestrator pipeline.
#
# Removed endpoints:
# - POST /upload-document/ (manual Phase 0)
# - POST /start-enhancement/{id} (manual Phase 1)
# - GET /get-suggestions/{id} (polling)
# - POST /finalize-document/ (manual Phase 2)
# - GET /progress/{id} (old progress tracking)
# - GET /artefacts/token-usage/* (monitoring)
# - POST /upload-pdf (alt workflow)
# - POST /start-conversion (alt workflow)
# - GET /conversion-progress/{id} (alt workflow)
# - GET /conversion-result/{id} (alt workflow)
# - GET /debug/ocr-test (debug utility)
# ===============================================


@router.post("/ask/", response_model=Union[AskSingleVersionResponse, AskBothVersionsResponse])
async def ask_question(request: Request, query: QueryRequest):
    """
    Ajukan pertanyaan terhadap versi v1, v2, atau keduanya dari dokumen.
    """
    try:
        version = (query.version or "both").lower()
        if version in ("v1", "v2"):
            ans, sources, token_usage = await perform_rag_query(
                query.prompt, query.document_id, version, request, trace=bool(query.trace), k=int(query.k or 5)
            )
            return {
                "answer": ans,
                "version": version,
                "prompt": query.prompt,
                "sources": sources if query.trace else [],
                "token_usage": token_usage,
            }
        else:
            v1_task = perform_rag_query(query.prompt, query.document_id, "v1", request, trace=bool(query.trace), k=int(query.k or 5))
            v2_task = perform_rag_query(query.prompt, query.document_id, "v2", request, trace=bool(query.trace), k=int(query.k or 5))
            (v1_answer, v1_sources, v1_tokens), (v2_answer, v2_sources, v2_tokens) = await asyncio.gather(v1_task, v2_task)
            return {
                "unenriched_answer": v1_answer,
                "enriched_answer": v2_answer,
                "prompt": query.prompt,
                "unenriched_sources": v1_sources if query.trace else [],
                "enriched_sources": v2_sources if query.trace else [],
                "unenriched_token_usage": v1_tokens,
                "enriched_token_usage": v2_tokens,
            }
    except Exception as e:
        logger.error(f"Error di endpoint /ask/: {e}")
        raise HTTPException(status_code=500, detail=str(e))




# AUTOMATED PIPELINE ENDPOINTS (Using DocumentOrchestrator)
# ===============================================

@router.post("/documents/upload-auto")
async def upload_document_auto(request: Request, file: UploadFile = File(...)):
    """
    AUTOMATED PIPELINE: Upload PDF and start full automated processing
    
    This endpoint triggers the complete pipeline automatically:
    1. Upload & OCR
    2. Enhancement (with client profile configuration)
    3. Auto-approval
    4. Synthesis
    5. Vectorization
    
    No manual steps required - runs entirely in background.
    
    Returns:
        document_id and status URL for monitoring progress
    """
    if file.content_type != 'application/pdf':
        raise HTTPException(status_code=400, detail="Only PDF files accepted")
    
    doc_id = str(uuid.uuid4())
    doc_dir = Path(PIPELINE_ARTEFACTS_DIR) / doc_id
    doc_dir.mkdir(parents=True, exist_ok=True)
    
    # Save PDF
    pdf_path = doc_dir / "source.pdf"
    try:
        with open(pdf_path, "wb") as buffer:
            shutil.copyfileobj(file.file, buffer)
        set_original_pdf_filename(doc_dir, file.filename)
        logger.info(f"[AUTO] Uploaded '{file.filename}' as {doc_id}")
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to save PDF: {e}")
    
    # Start automated pipeline in background
    asyncio.create_task(_run_automated_pipeline(doc_id, request))
    
    return {
        "status": "started",
        "message": "Automated pipeline started",
        "document_id": doc_id,
        "filename": file.filename,
        "status_url": f"/documents/{doc_id}/status",
        "result_url": f"/documents/{doc_id}/result"
    }


@router.post("/documents/upload-batch")
async def upload_documents_batch(request: Request, files: List[UploadFile] = File(...)):
    """
    BATCH UPLOAD: Upload multiple PDFs and process them in parallel
    
    Accepts multiple files and processes them concurrently based on
    global_config.json performance settings.
    
    Returns:
        List of document IDs and batch processing status
    """
    if not files:
        raise HTTPException(status_code=400, detail="No files provided")
    
    # Validate all files are PDFs
    for file in files:
        if file.content_type != 'application/pdf':
            raise HTTPException(
                status_code=400, 
                detail=f"File '{file.filename}' is not a PDF (got {file.content_type})"
            )
    
    # Upload all files first and get doc_ids
    doc_ids = []
    doc_infos = []
    
    for file in files:
        doc_id = str(uuid.uuid4())
        doc_dir = Path(PIPELINE_ARTEFACTS_DIR) / doc_id
        doc_dir.mkdir(parents=True, exist_ok=True)
        
        # Save PDF
        pdf_path = doc_dir / "source.pdf"
        try:
            with open(pdf_path, "wb") as buffer:
                shutil.copyfileobj(file.file, buffer)
            set_original_pdf_filename(doc_dir, file.filename)
            logger.info(f"[BATCH] Uploaded '{file.filename}' as {doc_id}")
            
            doc_ids.append(doc_id)
            doc_infos.append({
                "document_id": doc_id,
                "filename": file.filename,
                "status": "uploaded",
                "status_url": f"/documents/{doc_id}/status"
            })
            
        except Exception as e:
            logger.error(f"[BATCH] Failed to save '{file.filename}': {e}")
            raise HTTPException(status_code=500, detail=f"Failed to save '{file.filename}': {e}")
    
    logger.info(f"[BATCH] Starting parallel processing of {len(doc_ids)} documents")
    
    # Start multi-file processing in background
    asyncio.create_task(_run_multi_file_pipeline(doc_ids, request))
    
    return {
        "status": "batch_started",
        "message": f"Batch processing started for {len(doc_ids)} documents",
        "total_files": len(doc_ids),
        "documents": doc_infos,
        "batch_status_url": "/documents/batch-status"
    }


async def _run_multi_file_pipeline(doc_ids: List[str], request: Request):
    """Background task for multi-file parallel processing"""
    try:
        from ..orchestration.multi_file_orchestrator import MultiFileOrchestrator
        import json
        from datetime import datetime
        
        logger.info(f"[MULTI-FILE] Starting parallel processing of {len(doc_ids)} documents")
        
        # Initialize multi-file orchestrator
        orchestrator = MultiFileOrchestrator(
            namespace=None,  # Will use active namespace
            artefacts_dir=str(PIPELINE_ARTEFACTS_DIR)
        )
        
        # Run parallel processing
        result = await orchestrator.process_multiple_files(doc_ids)
        
        logger.info(f"[MULTI-FILE] Batch processing completed")
        logger.info(f"  Total files: {result.get('total_files', 0)}")
        logger.info(f"  Completed: {result.get('completed', 0)}")
        logger.info(f"  Failed: {result.get('failed', 0)}")
        logger.info(f"  Duration: {result.get('total_duration_seconds', 0):.2f}s")
        logger.info(f"  Concurrency used: {result.get('concurrency_used', 1)}")
        
    except Exception as e:
        logger.error(f"[MULTI-FILE] Batch processing failed: {e}", exc_info=True)


async def _run_automated_pipeline(doc_id: str, request: Request):
    """Background task for automated pipeline"""
    try:
        from ..orchestration.document_orchestrator import DocumentOrchestrator
        import json
        from datetime import datetime
        
        logger.info(f"[AUTO] Starting automated pipeline for {doc_id}")
        
        # Initialize orchestrator (uses active namespace from config)
        orchestrator = DocumentOrchestrator(
            doc_id=doc_id,
            namespace=None,  # Will use active namespace
            artefacts_dir=str(PIPELINE_ARTEFACTS_DIR)
        )
        
        # Run full pipeline
        result = await orchestrator.run_full_pipeline()
        
        logger.info(f"[AUTO] Pipeline completed for {doc_id}")
        logger.info(f"  Duration: {result.get('total_duration_seconds', 0):.2f}s")
        logger.info(f"  Status: {result.get('status', 'unknown')}")
        
    except Exception as e:
        logger.error(f"[AUTO] Pipeline failed for {doc_id}: {e}", exc_info=True)
        
        # Save error state properly
        doc_dir = Path(PIPELINE_ARTEFACTS_DIR) / doc_id
        error_file = doc_dir / "processing_state.json"
        try:
            doc_dir.mkdir(parents=True, exist_ok=True)
            error_state = {
                "doc_id": doc_id,
                "current_stage": "error",
                "progress_percentage": 0,
                "is_complete": False,
                "stage_timestamps": {},
                "stage_durations": {},
                "errors": [{
                    "stage": "pipeline",
                    "error": str(e),
                    "timestamp": datetime.now().isoformat()
                }],
                "metadata": {},
                "last_updated": datetime.now().isoformat()
            }
            with open(error_file, 'w', encoding='utf-8') as f:
                json.dump(error_state, f, indent=2, ensure_ascii=False)
        except Exception as save_error:
            logger.error(f"Failed to save error state: {save_error}")


@router.get("/documents/{document_id}/status")
async def get_document_status(document_id: str):
    """
    Get processing status for automated pipeline
    
    Returns detailed progress information including:
    - Current stage
    - Progress percentage
    - Timestamps
    - Errors (if any)
    """
    try:
        from ..orchestration.document_orchestrator import ProcessingState
        
        doc_dir = Path(PIPELINE_ARTEFACTS_DIR) / document_id
        if not doc_dir.exists():
            raise HTTPException(status_code=404, detail="Document not found")
        
        # Load state
        state = ProcessingState.load(document_id, Path(PIPELINE_ARTEFACTS_DIR))
        
        return {
            "document_id": document_id,
            "current_stage": state.current_stage,
            "progress_percentage": state.get_progress_percentage(),
            "is_complete": state.is_complete(),
            "stage_timestamps": state.stage_timestamps,
            "stage_durations": state.stage_durations,
            "errors": state.errors,
            "metadata": state.metadata,
            "stage_progress": state.stage_progress,
            "estimated_remaining_seconds": state.estimated_remaining_seconds
        }
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Failed to get status for {document_id}: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/documents/{document_id}/result")
async def get_document_result(document_id: str):
    """
    Get final result after automated pipeline completes
    
    Returns:
        Markdown content, metadata, and vectorization info
    """
    try:
        doc_dir = Path(PIPELINE_ARTEFACTS_DIR) / document_id
        if not doc_dir.exists():
            raise HTTPException(status_code=404, detail="Document not found")
        
        # Check if processing is complete
        state_file = doc_dir / "processing_state.json"
        if state_file.exists():
            import json
            with open(state_file, 'r', encoding='utf-8') as f:
                state_data = json.load(f)
            
            if not state_data.get("is_complete", False):
                raise HTTPException(
                    status_code=202,
                    detail=f"Processing not complete. Current stage: {state_data.get('current_stage', 'unknown')}"
                )
        
        # Load final markdown
        md_v2_path = get_markdown_path(doc_dir, "v2")
        if not md_v2_path.exists():
            raise HTTPException(status_code=404, detail="Final markdown not found")
        
        content = md_v2_path.read_text(encoding='utf-8')
        
        # Load metadata
        metadata = {}
        meta_file = doc_dir / "processing_state.json"
        if meta_file.exists():
            with open(meta_file, 'r', encoding='utf-8') as f:
                metadata = json.load(f)
        
        return {
            "document_id": document_id,
            "status": "ready",
            "markdown_content": content,
            "metadata": metadata,
            "original_filename": get_original_pdf_filename(doc_dir)
        }
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Failed to get result for {document_id}: {e}")
        raise HTTPException(status_code=500, detail=str(e))


# ===============================================
# OLD CONVERSION FUNCTIONS REMOVED - No longer needed with single-step direct enhancement