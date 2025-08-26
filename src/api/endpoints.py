import shutil
import tempfile
import asyncio
import json
from pathlib import Path
import hashlib
from typing import Union

from fastapi import APIRouter, UploadFile, File, HTTPException, Request
from pydantic import BaseModel
from loguru import logger
from langchain_chroma import Chroma
from langchain_core.prompts import PromptTemplate
from langchain.chains import RetrievalQA

from ..pipeline.phase_0_extraction import process_pdf_local
from ..pipeline.phase_1_planning import create_enrichment_plan
from ..pipeline.phase_2_generation import generate_bulk_content
from ..pipeline.phase_3_synthesis import synthesize_final_markdown
from ..pipeline.phase_4_vectorization import vectorize_and_store
from ..core.config import (
    RAG_PROMPT_TEMPLATE,
    CHROMA_COLLECTION,
    PIPELINE_ARTEFACTS_DIR,
    EMBEDDING_MODEL,
)
from .models import (
    SuggestionItem,
    CuratedSuggestions,
    UploadResponse,
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
    # If scores are already 0..1 relevance (higher is better), keep; if distances (lower is better), invert
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
    # Sort by score desc
    sources.sort(key=lambda x: x.score, reverse=True)
    return sources

async def perform_rag_query(prompt: str, doc_id: str, version: str, request: Request, trace: bool = False, k: int = 5) -> tuple[str, list[RetrievedSource]]:
    """
    Menjalankan kueri RAG terhadap versi dokumen tertentu (v1 atau v2).

    Fungsi ini mengambil sumber daya yang sudah diinisialisasi (client DB, model) dari
    state aplikasi, membuat retriever dengan filter metadata yang sesuai, dan menjalankan
    chain RetrievalQA untuk mendapatkan jawaban.

    Args:
        prompt (str): Pertanyaan dari pengguna.
        doc_id (str): ID unik dokumen yang akan dikueri.
        version (str): Versi dokumen ('v1' untuk asli, 'v2' untuk diperkaya).
        request (Request): Objek request FastAPI untuk mengakses state aplikasi.
        trace (bool): Jika True, kembalikan juga sumber evidence hasil retrieval.
        k (int): Top-k dokumen yang diambil untuk evidence.

    Returns:
        tuple[str, list[RetrievedSource]]: Jawaban dan daftar sumber (bila trace=True).
    """
    try:
        client = request.app.state.chroma_client
        embeddings = request.app.state.embedding_function
        llm = request.app.state.chat_model

        if not all([client, embeddings, llm]):
            raise ValueError("Koneksi database atau model AI tidak tersedia. Periksa log startup.")

        # Koleksi spesifik per model embedding untuk menghindari mismatch dimensi
        collection_name = f"{CHROMA_COLLECTION}__{EMBEDDING_MODEL.replace(':','_').replace('-','_')}"
        vector_store = Chroma(
            client=client,
            collection_name=collection_name,
            embedding_function=embeddings,
        )

        retrieval_filter = {
            '$and': [
                {'source_document': {'$eq': doc_id}},
                {'version': {'$eq': version}}
            ]
        }

        retriever = vector_store.as_retriever(
            search_kwargs={
                'k': k,
                'filter': retrieval_filter
            }
        )

        prompt_template = PromptTemplate(
            input_variables=["context", "question"],
            template=RAG_PROMPT_TEMPLATE
        )

        qa_chain = RetrievalQA.from_chain_type(
            llm=llm,
            chain_type="stuff",
            retriever=retriever,
            return_source_documents=False,
            chain_type_kwargs={"prompt": prompt_template}
        )

        logger.info(f"Menjalankan RAG chain untuk dokumen '{doc_id}' versi '{version}'...")
        result = await qa_chain.ainvoke({"query": prompt})
        answer = result.get("result", "Tidak ditemukan jawaban yang relevan dari dokumen.")

        sources: list[RetrievedSource] = []
        if trace:
            # Try to get docs with relevance scores; fallback to distance scores
            docs_with_scores = []
            try:
                pairs = vector_store.similarity_search_with_relevance_scores(
                    prompt, k=k, filter=retrieval_filter
                )
                # expected [(Document, score)] where score ~ [0..1]
                docs_with_scores = list(pairs)
                sources = _docs_to_sources(docs_with_scores, already_relevance=True)
            except Exception as e1:
                try:
                    pairs2 = vector_store.similarity_search_with_score(
                        prompt, k=k, filter=retrieval_filter
                    )
                    docs_with_scores = list(pairs2)
                    sources = _docs_to_sources(docs_with_scores, already_relevance=False)
                except Exception as e2:
                    logger.warning(f"Gagal mengambil evidence (dua metode gagal): {e1} | {e2}")

        return answer.strip(), sources

    except Exception as e:
        logger.error(f"Error dalam RAG query untuk versi {version}: {e}")
        return f"Terjadi kesalahan teknis saat mengambil jawaban: {str(e)}", []


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
            logger.info("--- Memulai Fase 0: Ekstraksi ---")
            phase_0_results = process_pdf_local(str(temp_pdf_path))
            doc_output_dir = phase_0_results["output_dir"]
            doc_id = Path(doc_output_dir).name
            logger.info(f"--- Fase 0 Selesai. Artefak di: {doc_output_dir} ---")

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
    """Menjalankan tugas latar belakang untuk Fase 1 (perencanaan) dan Fase 2 (generasi)."""
    doc_dir = Path(PIPELINE_ARTEFACTS_DIR) / document_id
    md_path = doc_dir / "markdown_v1.md"
    if not md_path.exists():
        raise HTTPException(status_code=404, detail="Dokumen tidak ditemukan atau belum diunggah.")

    async def _run():
        try:
            logger.info(f"[Enhancement] Mulai untuk dokumen: {document_id}")
            # Phase-1: Gate→Map→Reduce over segments.json (async)
            await create_enrichment_plan(str(doc_dir))
            await generate_bulk_content(str(doc_dir))
            logger.info(f"[Enhancement] Selesai untuk dokumen: {document_id}")
        except Exception as e:
            logger.error(f"[Enhancement] Gagal untuk dokumen {document_id}: {e}")

    asyncio.create_task(_run())
    return {"message": "Proses peningkatan dimulai", "document_id": document_id}


@router.get("/get-suggestions/{document_id}", response_model=EnhancementResponse)
async def get_suggestions(document_id: str):
    """Mengembalikan daftar saran ketika siap (polling)."""
    doc_dir = Path(PIPELINE_ARTEFACTS_DIR) / document_id
    sugg_path = doc_dir / "suggestions.json"
    if not sugg_path.exists():
        # Fallback to partial suggestions if Phase-2 still running
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

    # Synthesize v2
    final_path = synthesize_final_markdown(str(doc_dir), curated)
    if not final_path:
        raise HTTPException(status_code=500, detail="Sintesis dokumen gagal.")

    # Vectorize v1 and v2
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
            ans, sources = await perform_rag_query(
                query.prompt, query.document_id, version, request, trace=bool(query.trace), k=int(query.k or 5)
            )
            return {
                "answer": ans,
                "version": version,
                "prompt": query.prompt,
                "sources": sources if query.trace else [],
            }
        else:
            v1_task = perform_rag_query(query.prompt, query.document_id, "v1", request, trace=bool(query.trace), k=int(query.k or 5))
            v2_task = perform_rag_query(query.prompt, query.document_id, "v2", request, trace=bool(query.trace), k=int(query.k or 5))
            (v1_answer, v1_sources), (v2_answer, v2_sources) = await asyncio.gather(v1_task, v2_task)
            return {
                "unenriched_answer": v1_answer,
                "enriched_answer": v2_answer,
                "prompt": query.prompt,
                "unenriched_sources": v1_sources if query.trace else [],
                "enriched_sources": v2_sources if query.trace else [],
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

        # Heuristik progres per fase
        # Phase-0 dianggap selesai bila markdown_v1.md ada
        p0_done = (doc_dir / "markdown_v1.md").exists()

        # Phase-1
        p1_progress = {}
        p1_progress_path = doc_dir / "phase_1_progress.json"
        if p1_progress_path.exists():
            try:
                p1_progress = json.loads(p1_progress_path.read_text(encoding="utf-8"))
            except Exception:
                p1_progress = {}
        p1_done = (doc_dir / "plan.json").exists() or (p1_progress.get("processed", 0) >= p1_progress.get("preselected", 0) and p1_progress.get("preselected", 0) > 0)

        # Phase-2
        p2_progress = {}
        p2_progress_path = doc_dir / "phase_2_progress.json"
        if p2_progress_path.exists():
            try:
                p2_progress = json.loads(p2_progress_path.read_text(encoding="utf-8"))
            except Exception:
                p2_progress = {}
        p2_done = (doc_dir / "suggestions.json").exists()

        # Hitung persen gabungan sederhana: P0(0.2) + P1(0.4) + P2(0.4)
        w0, w1, w2 = 0.2, 0.4, 0.4
        p0 = 1.0 if p0_done else 0.0
        # Phase-1 percent: dari progress file jika ada; else 1 jika plan.json ada
        if p1_progress:
            pre = max(1, int(p1_progress.get("preselected", 1)))
            proc = int(p1_progress.get("processed", 0))
            p1 = min(1.0, max(0.0, proc / pre))
        else:
            p1 = 1.0 if p1_done else 0.0
        # Phase-2 percent
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