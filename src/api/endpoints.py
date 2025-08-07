import shutil
import tempfile
import asyncio
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
    CHROMA_COLLECTION
)

router = APIRouter()

class QueryRequest(BaseModel):
    document_id: str
    prompt: str

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

@router.post("/upload-document/", status_code=201)
async def upload_document(request: Request, file: UploadFile = File(...)):
    """
    Endpoint untuk mengunggah dan memproses file PDF.
    Menerima file, menyimpannya sementara, dan menjalankan pipeline pemrosesan end-to-end
    (ekstraksi, perencanaan, generasi, sintesis, dan vektorisasi).
    """
    if file.content_type != 'application/pdf':
        raise HTTPException(status_code=400, detail="Tipe file tidak valid. Hanya file PDF yang diterima.")

    chroma_client = request.app.state.chroma_client
    if not chroma_client:
        logger.error("Klien ChromaDB tidak tersedia. Proses unggah dibatalkan.")
        raise HTTPException(status_code=503, detail="Koneksi database tidak tersedia.")

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

            logger.info("--- Memulai Fase 1: Perencanaan ---")
            create_enrichment_plan(phase_0_results["markdown_path"], doc_output_dir)
            logger.info("--- Fase 1 Selesai. Rencana enrichment dibuat. ---")

            logger.info("--- Memulai Fase 2: Generasi Konten ---")
            generate_bulk_content(doc_output_dir)
            logger.info("--- Fase 2 Selesai. Konten berhasil digenerasi. ---")

            logger.info("--- Memulai Fase 3: Sintesis ---")
            final_markdown_path = synthesize_final_markdown(doc_output_dir)
            if not final_markdown_path:
                logger.error("Fase 3: Sintesis gagal menghasilkan file markdown final.")
                raise HTTPException(status_code=500, detail="Fase 3: Sintesis gagal")
            logger.info("--- Fase 3 Selesai. Markdown final berhasil disintesis. ---")
            
            logger.info("--- Memulai Fase 4: Vektorisasi ---")
            success_v1 = vectorize_and_store(doc_output_dir, chroma_client, "markdown_v1.md", "v1")
            success_v2 = vectorize_and_store(doc_output_dir, chroma_client, "markdown_v2.md", "v2")
            if not success_v1 or not success_v2:
                logger.error("Fase 4: Vektorisasi gagal untuk satu atau kedua versi.")
                raise Exception("Fase 4 Vektorisasi gagal.")

            logger.info("--- Fase 4 Selesai. Kedua versi berhasil divektorisasi. ---")
            return {"message": "Dokumen berhasil diproses.", "document_id": doc_id}

        except Exception as e:
            logger.error(f"Terjadi error saat menjalankan pipeline: {e}")
            raise HTTPException(status_code=500, detail=f"Pipeline gagal: {e}")

@router.post("/ask/")
async def ask_question(request: Request, query: QueryRequest):
    """
    Endpoint utama untuk mengajukan pertanyaan dan mendapatkan perbandingan jawaban.
    Menerima pertanyaan dan ID dokumen, lalu secara paralel menjalankan kueri RAG
    pada versi asli (v1) dan versi yang diperkaya (v2) dari dokumen tersebut.
    """
    try:
        # Jalankan kedua proses RAG secara bersamaan untuk efisiensi
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