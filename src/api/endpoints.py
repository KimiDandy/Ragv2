import shutil
import tempfile
import asyncio
import json
from pathlib import Path
import hashlib
import uuid
from typing import Union

from fastapi import APIRouter, UploadFile, File, HTTPException, Request
from pydantic import BaseModel
from loguru import logger
from langchain_chroma import Chroma
from langchain_core.prompts import PromptTemplate
from ..core.config import (
    CHROMA_DB_PATH,
    EMBEDDING_MODEL,
    CHAT_MODEL,
    CHROMA_MODE,
    CHROMA_SERVER_HOST,
    CHROMA_SERVER_PORT,
    PIPELINE_ARTEFACTS_DIR,
    RAG_PROMPT_TEMPLATE,
    CHROMA_COLLECTION,
)
from ..core.rate_limiter import AsyncLeakyBucket
from ..extract.extractor_v2 import extract_pdf_to_markdown
from ..pipeline.phase_3_synthesis import synthesize_final_markdown
from ..pipeline.phase_4_vectorization import vectorize_and_store
from ..core.rag_builder import build_rag_chain, answer_with_sources, create_filtered_retriever
from ..obs.token_ledger import get_token_ledger, log_tokens
from .models import (
    SuggestionItem,
    CuratedSuggestions,
    UploadResponse,
    UploadPdfResponse,
    StartConversionRequest,
    ConversionProgress,
    ConversionResult,
    EnhancementResponse,
    RetrievedSource,
    AskSingleVersionResponse,
    AskBothVersionsResponse,
)

router = APIRouter()

class QueryRequest(BaseModel):
    document_id: str
    prompt: str
    version: str | None = "both"  # 'v1' | 'v2' | 'both'
    trace: bool | None = False
    k: int | None = 5


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
        client = request.app.state.chroma_client
        embeddings = request.app.state.embedding_function
        llm = request.app.state.chat_model

        if not all([client, embeddings, llm]):
            raise ValueError("Koneksi database atau model AI tidak tersedia. Periksa log startup.")

        collection_name = f"{CHROMA_COLLECTION}__{EMBEDDING_MODEL.replace(':','_').replace('-','_')}"
        vector_store = Chroma(
            client=client,
            collection_name=collection_name,
            embedding_function=embeddings,
        )

        # Buat retriever dengan filter
        retriever = create_filtered_retriever(vector_store, doc_id, version, k)
        
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


@router.post("/upload-document/", status_code=201, response_model=UploadResponse)
async def upload_document(request: Request, file: UploadFile = File(...)):
    """
    Unggah PDF dan jalankan Fase 0 saja (ekstraksi). Mengembalikan konten markdown_v1 dan document_id.
    """
    if file.content_type != 'application/pdf':
        raise HTTPException(status_code=400, detail="Tipe file tidak valid. Hanya file PDF yang diterima.")

    with tempfile.TemporaryDirectory() as temp_dir:
        temp_pdf_path = Path(temp_dir) / file.filename
        with open(temp_pdf_path, "wb") as buffer:
            shutil.copyfileobj(file.file, buffer)

        logger.info(f"File sementara disimpan di: {temp_pdf_path}")

        try:
            logger.info("--- Memulai PDF Extraction dengan Workflow Baru ---")
            # Use new extractor_v2
            extraction_results = extract_pdf_to_markdown(
                doc_id=hashlib.md5(temp_pdf_path.read_bytes()).hexdigest()[:16],
                pdf_path=str(temp_pdf_path),
                out_dir=str(PIPELINE_ARTEFACTS_DIR)
            )
            doc_output_dir = extraction_results.artefacts_dir
            doc_id = extraction_results.doc_id
            logger.info(f"--- PDF Extraction Selesai. Artefak di: {doc_output_dir} ---")

            md_path = Path(doc_output_dir) / "markdown_v1.md"
            try:
                markdown_text = md_path.read_text(encoding='utf-8')
            except Exception as e:
                logger.error(f"Gagal membaca markdown_v1.md: {e}")
                raise HTTPException(status_code=500, detail="Gagal membaca markdown hasil ekstraksi.")

            return UploadResponse(document_id=doc_id, markdown_content=markdown_text)

        except Exception as e:
            logger.error(f"Terjadi error saat menjalankan pipeline: {e}")
            raise HTTPException(status_code=500, detail=f"Pipeline gagal: {e}")


@router.post("/start-enhancement/{document_id}")
async def start_enhancement(document_id: str):
    """Menjalankan tugas latar belakang Enhancement V2 (windowing + map-reduce)."""
    doc_dir = Path(PIPELINE_ARTEFACTS_DIR) / document_id
    md_path = doc_dir / "markdown_v1.md"
    if not md_path.exists():
        raise HTTPException(status_code=404, detail="Dokumen tidak ditemukan atau belum diunggah.")

    async def _run():
        try:
            logger.info(f"[Enhancement V2] Mulai untuk dokumen: {document_id}")
            
            # Import Enhancement V2 components
            from ..enhancement import (
                EnhancementConfig, EnhancementPlanner, EnhancementGenerator, 
                MarkdownSynthesizer, EnhancementIndexer
            )
            from ..enhancement.windowing import TokenWindowManager
            
            # Initialize components
            config = EnhancementConfig()
            window_manager = TokenWindowManager(
                window_size=config.window_tokens,
                overlap_size=config.window_overlap_tokens
            )
            planner = EnhancementPlanner(config)
            generator = EnhancementGenerator(config)
            synthesizer = MarkdownSynthesizer()
            
            # Load units metadata
            units_meta_path = doc_dir / "units_metadata.json"
            if not units_meta_path.exists():
                units_meta_path = doc_dir / "meta" / "units_metadata.json"
            
            if not units_meta_path.exists():
                logger.error(f"Units metadata not found for {document_id}")
                return
            
            with open(units_meta_path, 'r', encoding='utf-8') as f:
                units_metadata = json.load(f)
            
            # Step 1: Create windows and plan
            logger.info(f"[Enhancement V2] Creating windows for {document_id}")
            windows = window_manager.create_windows(
                doc_id=document_id,
                units_metadata=units_metadata,
                markdown_path=str(md_path)
            )
            
            logger.info(f"[Enhancement V2] Planning enhancements for {document_id}")
            planning_result = await planner.plan_enhancements(
                doc_id=document_id,
                windows=windows,
                units_metadata=units_metadata
            )
            
            # Save plan in both old and new format for compatibility
            plan_file_new = doc_dir / "enhancement_plan.json"
            with open(plan_file_new, 'w', encoding='utf-8') as f:
                json.dump(planning_result, f, ensure_ascii=False, indent=2)
            
            # Convert to old format for backward compatibility
            old_format_plan = _convert_to_old_format(planning_result)
            enrichment_plan_file = doc_dir / "enrichment_plan.json"
            with open(enrichment_plan_file, 'w', encoding='utf-8') as f:
                json.dump(old_format_plan, f, ensure_ascii=False, indent=2)
            
            # Step 2: Generate enhancements
            logger.info(f"[Enhancement V2] Generating content for {document_id}")
            from ..enhancement.planner import EnhancementCandidate
            
            candidate_objects = [
                EnhancementCandidate(**c) for c in planning_result['selected_candidates']
            ]
            
            generation_result = await generator.generate_enhancements(
                candidates=candidate_objects,
                units_metadata=units_metadata,
                doc_id=document_id
            )
            
            # Step 3: Create suggestions.json for frontend compatibility
            suggestions = _convert_to_suggestions(generation_result['items'], units_metadata)
            suggestions_file = doc_dir / "suggestions.json"
            with open(suggestions_file, 'w', encoding='utf-8') as f:
                json.dump(suggestions, f, ensure_ascii=False, indent=2)
            
            # Step 4: Synthesize Markdown v2
            logger.info(f"[Enhancement V2] Synthesizing Markdown v2 for {document_id}")
            markdown_v2_path = doc_dir / "markdown_v2.md"
            synthesizer.synthesize(
                doc_id=document_id,
                markdown_v1_path=str(md_path),
                enhancements=generation_result['items'],
                units_metadata=units_metadata,
                output_path=str(markdown_v2_path)
            )
            
            # Save complete enhancements data
            enhancements_file = doc_dir / "enhancements.json"
            synthesizer.save_enhancements_json(
                doc_id=document_id,
                planning_result=planning_result,
                generation_result=generation_result,
                output_path=str(enhancements_file)
            )
            
            logger.info(f"[Enhancement V2] Selesai untuk dokumen: {document_id}")
            
        except Exception as e:
            logger.error(f"[Enhancement V2] Gagal untuk dokumen {document_id}: {e}")
            # Create empty suggestions for frontend
            suggestions_file = doc_dir / "suggestions.json"
            with open(suggestions_file, 'w', encoding='utf-8') as f:
                json.dump([], f)

    asyncio.create_task(_run())
    return {"message": "Proses peningkatan V2 dimulai", "document_id": document_id}


@router.get("/get-suggestions/{document_id}", response_model=EnhancementResponse)
async def get_suggestions(document_id: str):
    """Mengembalikan daftar saran ketika siap (polling)."""
    doc_dir = Path(PIPELINE_ARTEFACTS_DIR) / document_id
    sugg_path = doc_dir / "suggestions.json"
    if not sugg_path.exists():
        partial = doc_dir / "suggestions_partial.json"
        if partial.exists():
            try:
                data = json.loads(partial.read_text(encoding='utf-8'))
                suggestions = [SuggestionItem(**item) for item in (data or [])]
                return EnhancementResponse(document_id=document_id, suggestions=suggestions)
            except Exception as e:
                logger.error(f"Gagal membaca suggestions_partial.json: {e}")
                return EnhancementResponse(document_id=document_id, suggestions=[])
        return EnhancementResponse(document_id=document_id, suggestions=[])

    try:
        data = json.loads(sugg_path.read_text(encoding='utf-8'))
        suggestions = [SuggestionItem(**item) for item in (data or [])]
    except Exception as e:
        logger.error(f"Gagal membaca suggestions.json: {e}")
        suggestions = []
    return EnhancementResponse(document_id=document_id, suggestions=suggestions)


@router.post("/finalize-document/")
async def finalize_document(request: Request, payload: CuratedSuggestions):
    """
    Menerima saran terkurasi dan menyintesis markdown_v2.md, lalu melakukan vektorisasi untuk v1 dan v2.
    """
    doc_id = payload.document_id
    doc_dir = Path(PIPELINE_ARTEFACTS_DIR) / doc_id
    if not (doc_dir / "markdown_v1.md").exists():
        raise HTTPException(status_code=404, detail="Dokumen tidak ditemukan.")

    curated = [s.dict() for s in payload.suggestions if (s.status or "").lower() in ("approved", "edited")]
    if not curated:
        logger.warning("Tidak ada saran yang disetujui/diedit untuk disintesis.")

    final_path = synthesize_final_markdown(str(doc_dir), curated)
    if not final_path:
        raise HTTPException(status_code=500, detail="Sintesis dokumen gagal.")

    chroma_client = request.app.state.chroma_client
    if not chroma_client:
        raise HTTPException(status_code=503, detail="Chroma client tidak tersedia.")

    embeddings = request.app.state.embedding_function
    ok_v1 = vectorize_and_store(str(doc_dir), chroma_client, "markdown_v1.md", "v1", embeddings=embeddings)
    ok_v2 = vectorize_and_store(str(doc_dir), chroma_client, "markdown_v2.md", "v2", embeddings=embeddings)
    if not (ok_v1 and ok_v2):
        raise HTTPException(status_code=500, detail="Vektorisasi gagal untuk salah satu versi.")

    return {"message": "Dokumen difinalisasi dan divektorisasi.", "document_id": doc_id}


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


@router.get("/progress/{document_id}")
async def get_progress(document_id: str):
    """
    Mengembalikan status progres pipeline untuk sebuah dokumen.
    Mencoba membaca artefak progres untuk Fase-1 dan Fase-2.
    Output ringan agar mudah dipakai front-end.
    """
    try:
        doc_dir = Path(PIPELINE_ARTEFACTS_DIR) / document_id
        if not doc_dir.exists():
            raise HTTPException(status_code=404, detail="Dokumen tidak ditemukan.")
        p0_done = (doc_dir / "markdown_v1.md").exists()

        p1_progress = {}
        p1_progress_path = doc_dir / "phase_1_progress.json"
        if p1_progress_path.exists():
            try:
                p1_progress = json.loads(p1_progress_path.read_text(encoding="utf-8"))
            except Exception:
                p1_progress = {}
        p1_done = (doc_dir / "plan.json").exists() or (p1_progress.get("processed", 0) >= p1_progress.get("preselected", 0) and p1_progress.get("preselected", 0) > 0)

        p2_progress = {}
        p2_progress_path = doc_dir / "phase_2_progress.json"
        if p2_progress_path.exists():
            try:
                p2_progress = json.loads(p2_progress_path.read_text(encoding="utf-8"))
            except Exception:
                p2_progress = {}
        p2_done = (doc_dir / "suggestions.json").exists()

        w0, w1, w2 = 0.2, 0.4, 0.4
        p0 = 1.0 if p0_done else 0.0
        if p1_progress:
            pre = max(1, int(p1_progress.get("preselected", 1)))
            proc = int(p1_progress.get("processed", 0))
            p1 = min(1.0, max(0.0, proc / pre))
        else:
            p1 = 1.0 if p1_done else 0.0
        if p2_progress:
            p2 = float(p2_progress.get("percent", 0.0))
        else:
            p2 = 1.0 if p2_done else 0.0

        overall = min(1.0, w0 * p0 + w1 * p1 + w2 * p2)
        status = "complete" if p2_done else ("running" if overall > 0.0 else "idle")

        return {
            "document_id": document_id,
            "percent": overall,
            "phase0": {"done": p0_done},
            "phase1": {"done": p1_done, "progress": p1_progress},
            "phase2": {"done": p2_done, "progress": p2_progress},
            "status": status,
        }
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error di endpoint /progress/: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/artefacts/token-usage/summary")
async def get_token_usage_summary():
    """
    Mengembalikan ringkasan penggunaan token dalam format markdown.
    """
    try:
        ledger = get_token_ledger(PIPELINE_ARTEFACTS_DIR)
        summary_path = ledger.summary_path
        
        if not summary_path.exists():
            # Generate summary jika belum ada
            ledger._update_summary()
        
        if summary_path.exists():
            content = summary_path.read_text(encoding="utf-8")
            return {"content": content, "type": "markdown"}
        else:
            return {"content": "# Token Usage Summary\n\nBelum ada data penggunaan token.", "type": "markdown"}
    
    except Exception as e:
        logger.error(f"Error di endpoint token usage summary: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/artefacts/token-usage/raw")
async def get_token_usage_raw():
    """
    Mengembalikan data raw penggunaan token dalam format JSONL.
    """
    try:
        ledger = get_token_ledger(PIPELINE_ARTEFACTS_DIR)
        jsonl_path = ledger.jsonl_path
        
        if not jsonl_path.exists():
            return {"events": [], "total": 0}
        
        events = []
        with open(jsonl_path, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line:
                    events.append(json.loads(line))
        
        return {"events": events, "total": len(events)}
    
    except Exception as e:
        logger.error(f"Error di endpoint token usage raw: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/artefacts/token-usage/stats")
async def get_token_usage_stats():
    """
    Mengembalikan statistik penggunaan token dalam format JSON.
    """
    try:
        ledger = get_token_ledger(PIPELINE_ARTEFACTS_DIR)
        stats = ledger.get_stats()
        return stats
    except Exception as e:
        logger.error(f"Error di endpoint token usage stats: {e}")
        raise HTTPException(status_code=500, detail=str(e))


# ---------------- PDF → Markdown++ conversion endpoints ----------------

@router.post("/upload-pdf", response_model=UploadPdfResponse)
async def upload_pdf(file: UploadFile = File(...)):
    """
    Simpan PDF mentah ke artefak dan kembalikan document_id. Tidak memproses apa pun.
    """
    if file.content_type != 'application/pdf':
        raise HTTPException(status_code=400, detail="Tipe file tidak valid. Hanya file PDF yang diterima.")

    doc_id = str(uuid.uuid4())
    doc_dir = Path(PIPELINE_ARTEFACTS_DIR) / doc_id
    doc_dir.mkdir(parents=True, exist_ok=True)
    pdf_path = doc_dir / "source.pdf"
    try:
        with open(pdf_path, "wb") as buffer:
            shutil.copyfileobj(file.file, buffer)
        logger.info(f"[PDF++] Uploaded '{file.filename}' as {pdf_path}")
        return UploadPdfResponse(document_id=doc_id, file_name=file.filename)
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Gagal menyimpan PDF: {e}")


@router.post("/start-conversion")
async def start_conversion(payload: StartConversionRequest):
    """Mulai konversi PDF→Markdown++ secara async berdasarkan mode ('basic'|'smart')."""
    mode = (payload.mode or "basic").lower()
    if mode not in ("basic", "smart"):
        raise HTTPException(status_code=400, detail="Mode tidak dikenal. Gunakan 'basic' atau 'smart'.")
    doc_dir = Path(PIPELINE_ARTEFACTS_DIR) / payload.document_id
    pdf_path = doc_dir / "source.pdf"
    if not pdf_path.exists():
        raise HTTPException(status_code=404, detail="PDF sumber tidak ditemukan. Unggah terlebih dahulu.")

    async def _run():
        try:
            # Use new extractor_v2 with mode support
            await asyncio.to_thread(
                extract_pdf_to_markdown,
                doc_id=payload.document_id,
                pdf_path=str(pdf_path),
                out_dir=str(PIPELINE_ARTEFACTS_DIR),
                # Smart mode uses better OCR settings
                ocr_primary_psm=3 if mode == "smart" else 6,
                ocr_fallback_psm=[6, 11, 3] if mode == "smart" else [11]
            )
        except Exception as e:
            logger.error(f"[PDF++] Konversi gagal untuk {payload.document_id}: {e}")
            progress_path = doc_dir / "conversion_progress.json"
            try:
                with open(progress_path, "w", encoding="utf-8") as f:
                    json.dump({"status": "error", "percent": 1.0, "message": str(e)}, f)
            except Exception:
                pass

    asyncio.create_task(_run())
    return {"message": "Konversi dimulai", "document_id": payload.document_id, "mode": mode}


@router.get("/conversion-progress/{document_id}", response_model=ConversionProgress)
async def conversion_progress(document_id: str):
    doc_dir = Path(PIPELINE_ARTEFACTS_DIR) / document_id
    progress_path = doc_dir / "conversion_progress.json"
    if not progress_path.exists():
        return ConversionProgress(status="idle", percent=0.0, message="Belum mulai")
    try:
        data = json.loads(progress_path.read_text(encoding="utf-8"))
        return ConversionProgress(**{
            "status": data.get("status", "running"),
            "percent": float(data.get("percent", 0.0)),
            "message": data.get("message"),
        })
    except Exception:
        return ConversionProgress(status="running", percent=0.0, message="Memuat progres...")


@router.get("/conversion-result/{document_id}", response_model=ConversionResult)
async def conversion_result(document_id: str):
    doc_dir = Path(PIPELINE_ARTEFACTS_DIR) / document_id
    md_path = doc_dir / "markdown_v1.md"
    meta_path = doc_dir / "meta" / "units_metadata.json"
    if not md_path.exists():
        raise HTTPException(status_code=404, detail="Hasil markdown belum tersedia.")
    try:
        artefacts: list[str] = []
        for sub in (doc_dir / "pages", doc_dir / "figures"):
            if sub.exists():
                for p in sub.glob("**/*"):
                    if p.is_file():
                        artefacts.append(p.relative_to(doc_dir).as_posix())
        content = md_path.read_text(encoding="utf-8")
        return ConversionResult(
            document_id=document_id,
            markdown_content=content,
            artefacts=artefacts,
            metadata_path=meta_path.relative_to(doc_dir).as_posix() if meta_path.exists() else None,
        )
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Gagal membaca hasil: {e}")


@router.get("/debug/ocr-test")
async def test_ocr_components():
    """Debug endpoint to test OCR components availability."""
    import os
    
    debug_info = {
        "tesseract_available": False,
        "tesseract_path": None,
        "openai_api_key_set": False,
        "api_key_length": 0,
        "system_path": os.environ.get("PATH", "").split(os.pathsep)[:5]  # First 5 PATH entries
    }
    
    # Test Tesseract
    try:
        import pytesseract
        from PIL import Image
        debug_info["tesseract_available"] = True
        debug_info["tesseract_path"] = pytesseract.pytesseract.tesseract_cmd
        
        # Try to run a simple test
        try:
            pytesseract.get_tesseract_version()
            debug_info["tesseract_working"] = True
        except Exception as e:
            debug_info["tesseract_working"] = False
            debug_info["tesseract_error"] = str(e)
    except Exception as e:
        debug_info["tesseract_import_error"] = str(e)
    
    # Test OpenAI API key
    api_key = os.getenv("OPENAI_API_KEY")
    if api_key:
        debug_info["openai_api_key_set"] = True
        debug_info["api_key_length"] = len(api_key)
        debug_info["api_key_prefix"] = api_key[:10] + "..." if len(api_key) > 10 else api_key
        
        # Test OpenAI connection
        try:
            from openai import OpenAI
            client = OpenAI(api_key=api_key)
            debug_info["openai_client_created"] = True
        except Exception as e:
            debug_info["openai_client_error"] = str(e)
    
    return debug_info


# ===============================================
# CONVERSION FUNCTIONS FOR BACKWARD COMPATIBILITY
# ===============================================

def _convert_to_old_format(planning_result: dict) -> list:
    """Convert Enhancement V2 planning result to old enrichment_plan.json format."""
    old_format = []
    
    for candidate in planning_result.get('selected_candidates', []):
        # Convert Enhancement V2 candidate to old format
        old_item = {
            "kind": candidate.get('type', 'unknown'),
            "key": candidate.get('title', 'Unknown'),
            "original_context": f"Pages {candidate.get('pages', [])}: {candidate.get('rationale', '')}",
            "confidence_score": candidate.get('priority', 0.5),
            "source_unit_ids": candidate.get('source_unit_ids', []),
            "section": candidate.get('section', 'General')
        }
        old_format.append(old_item)
    
    return old_format


def _convert_to_suggestions(enhancement_items: list, units_metadata: list) -> list:
    """Convert Enhancement V2 items to suggestions.json format used by the frontend.

    Tambahkan field yang mudah dibaca manusia: source_units dan source_previews,
    serta kosongkan confidence_score agar UI tidak menampilkan skor dummy.
    """
    # Build quick lookup for units metadata
    unit_map = {u.get('unit_id'): u for u in (units_metadata or [])}

    suggestions = []

    for item in enhancement_items:
        unit_ids = item.get('source_unit_ids', []) or []

        # Helpers to create better, precise previews
        def _extract_numbers(text: str):
            import re
            return re.findall(r"\d[\d\.,]*%?", text or "")

        def _digits_only(s: str) -> str:
            import re
            return re.sub(r"[^0-9]", "", s or "")

        def _select_terms(text: str, title: str):
            import re
            words = re.findall(r"[A-Za-z]{4,}", (text or "") + " " + (title or ""))
            nums = _extract_numbers(text or "")
            # Prioritaskan angka dan kata kunci penting (maks 6)
            key = [w.lower() for w in words[:6]] + [n.replace(',', '').lower() for n in nums[:6]]
            # Tambah istilah umum domain agar baris tabel mudah ditemukan
            domain = ['bi', 'rate', 'fed', 'ihsg', 'obligasi', 'usd', 'idr']
            return key + domain

        terms = _select_terms(item.get('text', ''), item.get('title', ''))

        # Build human-readable previews
        previews = []
        for uid in unit_ids:
            meta = unit_map.get(uid, {})
            utype = meta.get('unit_type', 'paragraph')
            label = 'Tabel' if utype == 'table' else 'Paragraf'
            page = meta.get('page')
            content = (meta.get('content') or '').strip()

            # Buat snippet yang lebih presisi
            locator = None
            snippet = ""
            if utype == 'table' and content:
                lines = [ln.strip() for ln in content.splitlines() if ln.strip().startswith('|')]
                # Cari baris yang mengandung salah satu terms
                hit_line = None
                header_line = None
                align_line = None
                for ln in lines:
                    ln_low = ln.lower()
                    # 1) Cocokkan kata kunci text/title
                    word_hit = any(t for t in terms if t and t.isalpha() and t in ln_low)
                    # 2) Cocokkan angka dengan normalisasi digit-only
                    ln_digits = _digits_only(ln_low)
                    num_hit = any(_digits_only(t) in ln_digits for t in terms if any(ch.isdigit() for ch in t))
                    # Tangkap header & alignment (biasanya 2 baris pertama pada blok tabel)
                    if header_line is None:
                        header_line = ln
                        continue
                    if align_line is None and set(ln.replace('|','').strip()) <= set(': -') and ':' in ln:
                        align_line = ln
                        continue
                    if word_hit or num_hit:
                        hit_line = ln
                        break
                if hit_line:
                    # Estimasikan nama baris dari kolom pertama setelah '|'
                    cells = [c.strip() for c in hit_line.strip('|').split('|')]
                    if cells:
                        locator = f"Baris '{cells[0]}'"
                    snippet = hit_line[:200] + ('...' if len(hit_line) > 200 else '')
                    # Buat preview tabel minimal: header + alignment + baris yang kena
                    table_preview = None
                    if header_line and align_line:
                        table_preview = "\n".join([header_line, align_line, hit_line])
                else:
                    snippet = content[:160] + ('...' if len(content) > 160 else '')
                    table_preview = None
            else:
                # Paragraf: cuplikan di sekitar kata kunci/angka pertama yang cocok
                low = content.lower()
                idx = -1
                for t in terms:
                    if not t:
                        continue
                    # 1) Cari kata langsung
                    idx = low.find(t)
                    if idx != -1:
                        break
                    # 2) Jika t numerik, cari dengan normalisasi digit-only
                    if any(ch.isdigit() for ch in t):
                        low_digits = _digits_only(low)
                        if _digits_only(t) and _digits_only(t) in low_digits:
                            # mapping indeks ke posisi kasar di teks asli
                            idx = max(0, low_digits.find(_digits_only(t)) - 1)
                            break
                if idx == -1:
                    snippet = content[:200] + ('...' if len(content) > 200 else '')
                else:
                    start = max(0, idx - 60)
                    end = min(len(content), idx + 120)
                    snippet = (('...' if start > 0 else '') + content[start:end].strip() + ('...' if end < len(content) else ''))

            anchor = meta.get('anchor')
            previews.append({
                'unit_id': uid,
                'page': page,
                'type': utype,
                'label': label,
                'snippet': snippet,
                'locator': locator,
                'anchor': anchor,
                'table_preview': table_preview if utype == 'table' else None
            })

        # Compose original context in Bahasa Indonesia
        if previews:
            def _fmt(p):
                base = f"{p['label']} (Hal {p['page']})" if p.get('page') else p['label']
                return base + (f" → {p['locator']}" if p.get('locator') else '')
            human_sources = [_fmt(p) for p in previews]
            ctx_sources = ', '.join(human_sources)
            original_context = f"Rujukan: {ctx_sources}."
        else:
            original_context = "Rujukan: tidak tersedia."

        suggestion_item = {
            "id": item.get('enh_id', f"enh_{len(suggestions)}"),
            "type": item.get('type', 'unknown'),
            "original_context": original_context,
            "generated_content": item.get('text', ''),  # Direct field mapping
            "confidence_score": None,
            "status": "pending",
            "source_units": unit_ids,
            "source_previews": previews
        }

        suggestions.append(suggestion_item)

    return suggestions