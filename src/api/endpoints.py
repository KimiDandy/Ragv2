import shutil
import tempfile
import asyncio
import json
from pathlib import Path

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
    PIPELINE_ARTEFACTS_DIR
)
from .models import SuggestionItem, CuratedSuggestions, UploadResponse, EnhancementResponse

router = APIRouter()

class QueryRequest(BaseModel):
    document_id: str
    prompt: str
    version: str | None = "both"  # 'v1' | 'v2' | 'both'

async def perform_rag_query(prompt: str, doc_id: str, version: str, request: Request) -> str:
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

    Returns:
        str: Jawaban yang dihasilkan oleh model LLM.
    """
    try:
        client = request.app.state.chroma_client
        embeddings = request.app.state.embedding_function
        llm = request.app.state.chat_model

        if not all([client, embeddings, llm]):
            raise ValueError("Koneksi database atau model AI tidak tersedia. Periksa log startup.")

        vector_store = Chroma(
            client=client,
            collection_name=CHROMA_COLLECTION,
            embedding_function=embeddings,
        )

        retriever = vector_store.as_retriever(
            search_kwargs={
                'k': 5,
                'filter': {
                    '$and': [
                        {'source_document': {'$eq': doc_id}},
                        {'version': {'$eq': version}}
                    ]
                }
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

        return answer.strip()

    except Exception as e:
        logger.error(f"Error dalam RAG query untuk versi {version}: {e}")
        return f"Terjadi kesalahan teknis saat mengambil jawaban: {str(e)}"

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
            create_enrichment_plan(str(md_path), str(doc_dir))
            generate_bulk_content(str(doc_dir))
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

    ok_v1 = vectorize_and_store(str(doc_dir), chroma_client, "markdown_v1.md", "v1")
    ok_v2 = vectorize_and_store(str(doc_dir), chroma_client, "markdown_v2.md", "v2")
    if not (ok_v1 and ok_v2):
        raise HTTPException(status_code=500, detail="Vektorisasi gagal untuk salah satu versi.")

    return {"message": "Dokumen difinalisasi dan divektorisasi.", "document_id": doc_id}

@router.post("/ask/")
async def ask_question(request: Request, query: QueryRequest):
    """
    Ajukan pertanyaan terhadap versi v1, v2, atau keduanya dari dokumen.
    """
    try:
        version = (query.version or "both").lower()
        if version in ("v1", "v2"):
            ans = await perform_rag_query(query.prompt, query.document_id, version, request)
            return {"answer": ans, "version": version, "prompt": query.prompt}
        else:
            v1_task = perform_rag_query(query.prompt, query.document_id, "v1", request)
            v2_task = perform_rag_query(query.prompt, query.document_id, "v2", request)
            v1_answer, v2_answer = await asyncio.gather(v1_task, v2_task)
            return {
                "unenriched_answer": v1_answer,
                "enriched_answer": v2_answer,
                "prompt": query.prompt
            }
    except Exception as e:
        logger.error(f"Error di endpoint /ask/: {e}")
        raise HTTPException(status_code=500, detail=str(e))