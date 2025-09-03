import shutil
import tempfile
import asyncio
import json
from pathlib import Path
import hashlib
import time
from typing import Union, List

from fastapi import APIRouter, UploadFile, File, HTTPException, Request
from pydantic import BaseModel
from loguru import logger
from langchain_chroma import Chroma
from langchain_core.prompts import PromptTemplate
from langchain.chains import RetrievalQA, LLMChain

from ..pipeline.phase_0_extraction import process_pdf_local
from ..ingest.segmenter import segment_pdf
from ..ingest.index_bm25 import build_bm25_index, bm25_search
from ..ingest.sharding import build_shards
from ..pipeline.phase_1_planning import create_enrichment_plan
from ..pipeline.phase_2_generation import generate_bulk_content
from ..pipeline.phase_3_synthesis import synthesize_final_markdown
from ..pipeline.phase_4_vectorization import vectorize_and_store
from ..vectorize.chunker import chunk_markdown
from ..vectorize.store import delete_before_upsert, upsert_chunks
from ..retrieve.router import search_evidence
from ..core.config import (
    RAG_PROMPT_TEMPLATE,
    CHROMA_COLLECTION,
    PIPELINE_ARTEFACTS_DIR,
    EMBEDDING_MODEL,
)
from ..planner.run import run_phase1_planner
from ..enrich.run import run_phase2_enrichment
from ..synthesis.run import run_phase3_synthesis
from .models import (
    SuggestionItem,
    CuratedSuggestions,
    UploadResponse,
    EnhancementResponse,
    RetrievedSource,
    AskSingleVersionResponse,
    AskBothVersionsResponse,
)
from ..obs.metrics import get_summary, set_cancel, clear_cancel, is_cancelled

router = APIRouter()

class QueryRequest(BaseModel):
    document_id: str
    prompt: str
    version: str | None = "both"  # 'v1' | 'v2' | 'both'
    trace: bool | None = False
    k: int | None = 5
    use_router: bool | None = False


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
            docs_with_scores = []
            try:
                pairs = vector_store.similarity_search_with_relevance_scores(
                    prompt, k=k, filter=retrieval_filter
                )
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


async def perform_rag_query_router(prompt: str, doc_id: str, version: str, request: Request, trace: bool = False, k: int = 5) -> tuple[str, list[RetrievedSource]]:
    """Run Sprint-5 router (BM25->Shard->Dense->Rerank), then compose answer with LLMChain.

    Returns (answer, sources). Sources are included only when trace=True.
    """
    try:
        client = request.app.state.chroma_client
        embeddings = request.app.state.embedding_function
        llm = request.app.state.chat_model

        if not all([client, embeddings, llm]):
            raise ValueError("Koneksi database atau model AI tidak tersedia. Periksa log startup.")

        # Retrieve evidence (pull a bit more than k for answer context)
        ev = search_evidence(
            chroma_client=client,
            embeddings=embeddings,
            document_id=doc_id,
            query=prompt,
            version=version,
            bm25_top_k=50,
            max_shards=4,
            dense_k_per_shard=max(3, int(k)),
            page=1,
            page_size=max(10, int(k) * 2),
        )
        items = ev.get("items", [])

        # Build context from top-k items
        top_items = items[: int(k)]
        context_blocks = []
        for it in top_items:
            meta = it.get("metadata", {}) or {}
            header = meta.get("section_title") or ", ".join(meta.get("header_path", []) or [])
            shard = meta.get("shard_title") or meta.get("shard_id") or ""
            pages = meta.get("page_start")
            if pages is None:
                pages = meta.get("pages")
            prefix = f"[Section: {header}] [Shard: {shard}]"
            context_blocks.append(f"{prefix}\n{it.get('snippet','')}")
        context = "\n\n---\n\n".join(context_blocks) if context_blocks else ""

        prompt_template = PromptTemplate(
            input_variables=["context", "question"],
            template=RAG_PROMPT_TEMPLATE,
        )
        chain = LLMChain(llm=llm, prompt=prompt_template)
        res = await chain.ainvoke({"context": context, "question": prompt})
        answer = (res.get("text") if isinstance(res, dict) else str(res)).strip()

        sources: list[RetrievedSource] = []
        if trace and items:
            sources = [
                RetrievedSource(
                    id=str(it.get("id")),
                    score=float(it.get("score", 0.0)),
                    snippet=str(it.get("snippet", "")),
                    metadata=dict(it.get("metadata", {}) or {}),
                )
                for it in items[: int(k)]
            ]

        return answer, sources
    except Exception as e:
        logger.error(f"Error dalam RAG router query untuk versi {version}: {e}")
        return f"Terjadi kesalahan teknis saat mengambil jawaban (router): {str(e)}", []


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

            # Sprint-1 ingest: segmenter (compat), BM25, sharding
            try:
                ingest_start = time.time()
                segments = segment_pdf(str(temp_pdf_path), doc_id)
                seg_count = len(segments)
                if seg_count:
                    build_bm25_index(doc_id, segments)
                    shards_path = build_shards(doc_id, segments)
                    try:
                        shards_obj = json.loads(Path(shards_path).read_text(encoding='utf-8'))
                        shard_count = len(shards_obj.get("shards", []))
                    except Exception:
                        shard_count = 0
                else:
                    shard_count = 0
                ingest_dur = time.time() - ingest_start
                logger.info(f"[Ingest] doc_id={doc_id} segments={seg_count} shards={shard_count} duration_sec={round(ingest_dur,3)}")
            except Exception as ie:
                # Do not fail upload if ingest fails; just log
                logger.error(f"[Ingest] Gagal menjalankan ingest Sprint-1 untuk {doc_id}: {ie}")

            return UploadResponse(document_id=doc_id, markdown_content=markdown_text)

        except Exception as e:
            logger.error(f"Terjadi error saat menjalankan pipeline: {e}")
            raise HTTPException(status_code=500, detail=f"Pipeline gagal: {e}")


@router.post("/start-enhancement/{document_id}")
async def start_enhancement(document_id: str, eager_top_n: int = 100, refine_top_n: int = 60):
    """Mulai alur baru: Phase-1 Planner lalu Phase-2 Enrichment (background).

    Endpoint ini dipertahankan agar kompatibel dengan frontend yang sudah ada,
    tetapi implementasinya menggunakan runner baru (`run_phase1_planner` dan `run_phase2_enrichment`).
    """
    try:
        doc_dir = Path(PIPELINE_ARTEFACTS_DIR) / document_id
        # Pastikan artefak Phase-0/Sprint-1 tersedia
        md_path = doc_dir / "markdown_v1.md"
        seg_path = doc_dir / "segments.json"
        shards_path = doc_dir / "shards.json"
        if not md_path.exists():
            raise HTTPException(status_code=404, detail="Dokumen tidak ditemukan atau belum diunggah.")
        if not seg_path.exists() or not shards_path.exists():
            raise HTTPException(status_code=404, detail="Artefak Sprint-1 tidak lengkap (segments.json/shards.json). Jalankan upload terlebih dahulu.")

        async def _run():
            try:
                logger.info(f"[Enhancement v2] Mulai Phase-1 Planner untuk dokumen: {document_id}")
                await run_phase1_planner(document_id, prompt_version="v1", force=False)
                logger.info(f"[Enhancement v2] Phase-1 selesai untuk dokumen: {document_id}")

                logger.info(f"[Enhancement v2] Mulai Phase-2 Enrichment untuk dokumen: {document_id}")
                await run_phase2_enrichment(
                    document_id,
                    prompt_version="v1",
                    eager_top_n=int(eager_top_n or 100),
                    refine_top_n=int(refine_top_n or 60),
                )
                logger.info(f"[Enhancement v2] Phase-2 selesai untuk dokumen: {document_id}")
            except Exception as e:
                logger.error(f"[Enhancement v2] Gagal untuk dokumen {document_id}: {e}")

        asyncio.create_task(_run())
        return {
            "message": "Alur baru (Phase-1 + Phase-2) dimulai",
            "document_id": document_id,
            "eager_top_n": int(eager_top_n or 100),
            "refine_top_n": int(refine_top_n or 60),
        }
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error di endpoint /start-enhancement/: {e}")
        raise HTTPException(status_code=500, detail=str(e))


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

    # Read artefacts for Sprint-5 chunker metadata enrichment
    seg_path = doc_dir / "segments.json"
    shards_path = doc_dir / "shards.json"
    try:
        segments = json.loads(seg_path.read_text(encoding="utf-8")) if seg_path.exists() else []
    except Exception:
        segments = []
    try:
        shards_obj = json.loads(shards_path.read_text(encoding="utf-8")) if shards_path.exists() else {"shards": []}
    except Exception:
        shards_obj = {"shards": []}

    # Helper to run Sprint-5 vectorization hygiene per version
    def _vectorize_v5(md_filename: str, version: str) -> bool:
        md_path = doc_dir / md_filename
        try:
            md_text = md_path.read_text(encoding="utf-8")
        except Exception as e:
            logger.error(f"Gagal membaca {md_filename} untuk {doc_id}: {e}")
            return False
        try:
            chunks = chunk_markdown(doc_id, version, md_text, segments, shards_obj)
            delete_before_upsert(chroma_client, doc_id, version)
            upsert_chunks(chroma_client, embeddings, doc_id, version, chunks)
            return True
        except Exception as e:
            logger.error(f"Vectorization v5 gagal untuk {doc_id} {version}: {e}")
            return False

    # Prefer Sprint-5 hygiene; if it fails, fallback to Sprint-4 for robustness
    ok_v1 = _vectorize_v5("markdown_v1.md", "v1") or vectorize_and_store(str(doc_dir), chroma_client, "markdown_v1.md", "v1", embeddings=embeddings)
    ok_v2 = _vectorize_v5("markdown_v2.md", "v2") or vectorize_and_store(str(doc_dir), chroma_client, "markdown_v2.md", "v2", embeddings=embeddings)
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
            if bool(query.use_router):
                ans, sources = await perform_rag_query_router(
                    query.prompt, query.document_id, version, request, trace=bool(query.trace), k=int(query.k or 5)
                )
            else:
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
            if bool(query.use_router):
                v1_task = perform_rag_query_router(query.prompt, query.document_id, "v1", request, trace=bool(query.trace), k=int(query.k or 5))
                v2_task = perform_rag_query_router(query.prompt, query.document_id, "v2", request, trace=bool(query.trace), k=int(query.k or 5))
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


@router.get("/search/evidence/{document_id}")
async def search_evidence_endpoint(
    request: Request,
    document_id: str,
    q: str,
    version: str | None = "v2",
    page: int | None = 1,
    page_size: int | None = 10,
    bm25_top_k: int | None = 50,
    max_shards: int | None = 4,
    dense_k_per_shard: int | None = 5,
):
    """Return paginated evidence items using the Sprint-5 retrieval router.

    Returns a lightweight object for auditing in the frontend.
    """
    try:
        client = request.app.state.chroma_client
        embeddings = request.app.state.embedding_function
        if not all([client, embeddings]):
            raise HTTPException(status_code=503, detail="Chroma/Embedding tidak tersedia.")

        result = search_evidence(
            chroma_client=client,
            embeddings=embeddings,
            document_id=document_id,
            query=q,
            version=(version or "v2").lower(),
            bm25_top_k=int(bm25_top_k or 50),
            max_shards=int(max_shards or 4),
            dense_k_per_shard=int(dense_k_per_shard or 5),
            page=int(page or 1),
            page_size=int(page_size or 10),
        )
        return result
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error di endpoint /search/evidence/: {e}")
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


@router.get("/search-bm25/{document_id}")
async def search_bm25(document_id: str, q: str, top: int = 10):
    """Lightweight verification search over BM25 index built during upload.

    Returns:
      { "document_id": str, "results": [ {id, score, snippet, metadata} ] }
    """
    try:
        if not q or not q.strip():
            raise HTTPException(status_code=400, detail="Parameter 'q' wajib diisi.")

        doc_dir = Path(PIPELINE_ARTEFACTS_DIR) / document_id
        seg_path = doc_dir / "segments.json"
        if not seg_path.exists():
            raise HTTPException(status_code=404, detail="segments.json tidak ditemukan untuk dokumen ini.")

        try:
            segments = json.loads(seg_path.read_text(encoding="utf-8"))
        except Exception as e:
            logger.error(f"Gagal membaca segments.json: {e}")
            raise HTTPException(status_code=500, detail="Gagal membaca segmen.")

        seg_by_id = {}
        for s in segments:
            sid = s.get("segment_id") or s.get("id")
            if sid:
                seg_by_id[sid] = s

        ranked = bm25_search(document_id, q, top_k=int(top or 10))
        results = []
        for sid, score, _ in ranked:
            s = seg_by_id.get(sid)
            if not s:
                continue
            text = s.get("text", "")
            meta = {
                "segment_id": sid,
                "page": int(s.get("page") or 0),
                "header_path": list(s.get("header_path") or []),
                "char": [int(s.get("char_start") or 0), int(s.get("char_end") or 0)],
            }
            results.append({
                "id": sid,
                "score": float(round(score, 6)),
                "snippet": _build_snippet(text, 300),
                "metadata": meta,
            })

        return {"document_id": document_id, "results": results}
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error di endpoint /search-bm25/: {e}")
        raise HTTPException(status_code=500, detail=str(e))


# --- Sprint-2 Planner Endpoints ---

@router.post("/start-planner/{document_id}")
async def start_planner(document_id: str, force: bool = False):
    """Mulai Phase-1 Planner secara asynchronous untuk dokumen tertentu.

    Syarat: artefak Sprint-1 harus tersedia (segments.json dan shards.json).
    """
    try:
        doc_dir = Path(PIPELINE_ARTEFACTS_DIR) / document_id
        seg_path = doc_dir / "segments.json"
        shards_path = doc_dir / "shards.json"
        if not seg_path.exists() or not shards_path.exists():
            raise HTTPException(status_code=404, detail="Artefak Sprint-1 tidak lengkap (segments.json/shards.json). Jalankan upload terlebih dahulu.")

        async def _run():
            try:
                await run_phase1_planner(document_id, prompt_version="v1", force=bool(force))
            except Exception as e:
                logger.error(f"[Planner] Gagal menjalankan Phase-1 untuk {document_id}: {e}")

        asyncio.create_task(_run())
        return {"message": "Planner dimulai", "document_id": document_id, "force": bool(force)}
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error di endpoint /start-planner/: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/planner-progress/{document_id}")
async def planner_progress(document_id: str):
    """Kembalikan progres Phase-1 Planner."""
    try:
        doc_dir = Path(PIPELINE_ARTEFACTS_DIR) / document_id
        if not doc_dir.exists():
            raise HTTPException(status_code=404, detail="Dokumen tidak ditemukan.")

        progress_path = doc_dir / "phase_1_progress.json"
        plan_path = doc_dir / "plan.json"

        progress = {}
        if progress_path.exists():
            try:
                progress = json.loads(progress_path.read_text(encoding="utf-8"))
            except Exception:
                progress = {}

        done = plan_path.exists()
        status = "done" if done else (progress.get("status") or "idle")
        return {
            "document_id": document_id,
            "status": status,
            "progress": progress,
            "plan_ready": done,
        }
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error di endpoint /planner-progress/: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/plan/{document_id}")
async def get_plan(document_id: str, page: int | None = None, page_size: int | None = None):
    """Ambil plan.json final. Jika page & page_size diberikan, kembalikan hasil paginasi gabungan.

    Paginasi dilakukan terhadap gabungan terms_to_define dan concepts_to_simplify, diurutkan menurun berdasarkan score.
    Output menyertakan `type` untuk setiap item ("term" atau "concept").
    """
    try:
        doc_dir = Path(PIPELINE_ARTEFACTS_DIR) / document_id
        plan_path = doc_dir / "plan.json"
        if not plan_path.exists():
            raise HTTPException(status_code=404, detail="Plan belum tersedia. Jalankan /start-planner lebih dulu atau tunggu proses selesai.")

        plan = json.loads(plan_path.read_text(encoding="utf-8"))
        if not page or not page_size:
            return {"document_id": document_id, "plan": plan}

        page = max(int(page), 1)
        page_size = max(int(page_size), 1)
        terms = [("term", x) for x in (plan.get("terms_to_define") or [])]
        concepts = [("concept", x) for x in (plan.get("concepts_to_simplify") or [])]
        combined = terms + concepts
        combined.sort(key=lambda t: float(t[1].get("score", 0.0)), reverse=True)

        total = len(combined)
        start = (page - 1) * page_size
        end = min(start + page_size, total)
        items = [
            {"type": t, **itm} for (t, itm) in combined[start:end]
        ]
        return {
            "document_id": document_id,
            "page": page,
            "page_size": page_size,
            "total": total,
            "items": items,
        }
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error di endpoint /plan/: {e}")
        raise HTTPException(status_code=500, detail=str(e))


# --- Sprint-3 Enrichment Endpoints ---

@router.post("/start-enrichment/{document_id}")
async def start_enrichment(document_id: str, eager_top_n: int = 100, refine_top_n: int = 60):
    """Mulai Phase-2 Enrichment secara asynchronous untuk dokumen tertentu.

    Syarat: artefak Phase-1 tersedia (plan.json).
    """
    try:
        doc_dir = Path(PIPELINE_ARTEFACTS_DIR) / document_id
        plan_path = doc_dir / "plan.json"
        if not plan_path.exists():
            raise HTTPException(status_code=404, detail="Plan belum tersedia. Jalankan /start-planner lebih dulu atau tunggu proses selesai.")

        async def _run():
            try:
                await run_phase2_enrichment(
                    document_id,
                    prompt_version="v1",
                    eager_top_n=int(eager_top_n or 100),
                    refine_top_n=int(refine_top_n or 60),
                )
            except Exception as e:
                logger.error(f"[Enrichment] Gagal menjalankan Phase-2 untuk {document_id}: {e}")

        asyncio.create_task(_run())
        return {"message": "Enrichment dimulai", "document_id": document_id, "eager_top_n": int(eager_top_n or 100), "refine_top_n": int(refine_top_n or 60)}
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error di endpoint /start-enrichment/: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/get-suggestions-paged/{document_id}")
async def get_suggestions_paged(document_id: str, page: int | None = 1, page_size: int | None = 20):
    """Ambil suggestions dengan paginasi. Mengutamakan suggestions.json lalu suggestions_partial.json."""
    try:
        doc_dir = Path(PIPELINE_ARTEFACTS_DIR) / document_id
        if not doc_dir.exists():
            raise HTTPException(status_code=404, detail="Dokumen tidak ditemukan.")

        sugg_path = doc_dir / "suggestions.json"
        items = []
        if sugg_path.exists():
            try:
                items = json.loads(sugg_path.read_text(encoding="utf-8")) or []
            except Exception as e:
                logger.error(f"Gagal membaca suggestions.json: {e}")
                items = []
        else:
            partial = doc_dir / "suggestions_partial.json"
            if partial.exists():
                try:
                    items = json.loads(partial.read_text(encoding='utf-8')) or []
                except Exception as e:
                    logger.error(f"Gagal membaca suggestions_partial.json: {e}")
                    items = []

        page = max(int(page or 1), 1)
        page_size = max(int(page_size or 20), 1)
        total = len(items)
        start = (page - 1) * page_size
        end = min(start + page_size, total)
        sliced = items[start:end]

        return {
            "document_id": document_id,
            "page": page,
            "page_size": page_size,
            "total": total,
            "items": sliced,
        }
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error di endpoint /get-suggestions-paged/: {e}")
        raise HTTPException(status_code=500, detail=str(e))


# --- Sprint-4 Synthesis Endpoints ---

@router.post("/start-synthesis/{document_id}")
async def start_synthesis(document_id: str, prefer_final_suggestions: bool = True, max_sentence_len: int = 400):
    """Mulai Sprint-4 Synthesis secara asynchronous untuk dokumen tertentu.

    Menghasilkan artefak: markdown_v2.md, anchors_map.json, synthesis_report.json
    """
    try:
        doc_dir = Path(PIPELINE_ARTEFACTS_DIR) / document_id
        md_path = doc_dir / "markdown_v1.md"
        if not md_path.exists():
            raise HTTPException(status_code=404, detail="markdown_v1.md tidak ditemukan. Jalankan upload terlebih dahulu.")

        async def _run():
            try:
                run_phase3_synthesis(
                    document_id,
                    prefer_final_suggestions=bool(prefer_final_suggestions),
                    max_sentence_len=int(max_sentence_len or 400),
                )
            except Exception as e:
                logger.error(f"[Synthesis] Gagal menjalankan Sprint-4 untuk {document_id}: {e}")

        asyncio.create_task(_run())
        return {
            "message": "Synthesis dimulai",
            "document_id": document_id,
            "prefer_final_suggestions": bool(prefer_final_suggestions),
            "max_sentence_len": int(max_sentence_len or 400),
        }
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error di endpoint /start-synthesis/: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/synthesis-report/{document_id}")
async def synthesis_report(document_id: str):
    """Ambil synthesis_report.json jika tersedia, beserta metadata artefak lain."""
    try:
        doc_dir = Path(PIPELINE_ARTEFACTS_DIR) / document_id
        if not doc_dir.exists():
            raise HTTPException(status_code=404, detail="Dokumen tidak ditemukan.")

        report_p = doc_dir / "synthesis_report.json"
        anchors_p = doc_dir / "anchors_map.json"
        v2_p = doc_dir / "markdown_v2.md"

        report = {}
        if report_p.exists():
            try:
                report = json.loads(report_p.read_text(encoding="utf-8"))
            except Exception:
                report = {}

        return {
            "document_id": document_id,
            "report": report,
            "anchors_map_exists": anchors_p.exists(),
            "markdown_v2_exists": v2_p.exists(),
        }
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error di endpoint /synthesis-report/: {e}")
        raise HTTPException(status_code=500, detail=str(e))


# --- Sprint-6 Observability Endpoints ---

@router.get("/metrics/{document_id}")
async def get_metrics(document_id: str):
    """Return aggregated observability metrics and log file paths for a document."""
    try:
        summary = get_summary(document_id)
        return summary
    except Exception as e:
        logger.error(f"Error di endpoint /metrics/: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/cancel/{document_id}")
async def cancel_document(document_id: str, clear: bool | None = False):
    """Set or clear the cooperative cancellation flag for a document.

    - POST /cancel/{document_id}?clear=false -> set cancel flag
    - POST /cancel/{document_id}?clear=true  -> clear cancel flag
    """
    try:
        if bool(clear):
            clear_cancel(document_id)
            action = "cleared"
        else:
            set_cancel(document_id)
            action = "set"
        return {
            "document_id": document_id,
            "cancelled": bool(is_cancelled(document_id)),
            "action": action,
        }
    except Exception as e:
        logger.error(f"Error di endpoint /cancel/: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/health")
async def health(request: Request):
    """Liveness/readiness probe for system components."""
    try:
        artefacts_ok = Path(PIPELINE_ARTEFACTS_DIR).exists()
        # Optional components (may be None depending on startup)
        try:
            chroma_ok = bool(getattr(request.app.state, "chroma_client", None))
        except Exception:
            chroma_ok = False
        try:
            embed_ok = bool(getattr(request.app.state, "embedding_function", None))
        except Exception:
            embed_ok = False
        try:
            model_ok = bool(getattr(request.app.state, "chat_model", None))
        except Exception:
            model_ok = False

        components = {
            "artefacts_dir": artefacts_ok,
            "chroma_client": chroma_ok,
            "embedding_function": embed_ok,
            "chat_model": model_ok,
        }
        all_ok = artefacts_ok
        status = "ok" if all_ok else "degraded"
        return {"status": status, "components": components, "timestamp": time.time()}
    except Exception as e:
        logger.error(f"Error di endpoint /health: {e}")
        raise HTTPException(status_code=500, detail=str(e))