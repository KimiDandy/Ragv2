import shutil
import tempfile
import asyncio
from pathlib import Path

from fastapi import APIRouter, UploadFile, File, HTTPException, Request
from fastapi.responses import FileResponse
from pydantic import BaseModel
import chromadb
from loguru import logger

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

async def perform_rag_query(prompt: str, doc_id: str, version: str, client: chromadb.Client, embeddings, llm, prompt_template) -> str:
    try:
        from langchain_chroma import Chroma
        from langchain.chains import RetrievalQA

        vector_store = Chroma(
            client=client,
            collection_name=CHROMA_COLLECTION,
            embedding_function=embeddings,
        )
        
        results = vector_store.similarity_search(
            query=prompt,
            k=5,
            filter={
                "$and": [
                    {"source_document": {"$eq": doc_id}},
                    {"version": {"$eq": version}}
                ]
            }
        )
        
        context = "\n\n".join([doc.page_content for doc in results])
        
        logger.debug(f"--- CONTEXT FOR VERSION: {version} ---")
        logger.debug(context)
        logger.debug("--- END OF CONTEXT ---")
        
        prompt_template_obj = PromptTemplate(
            input_variables=["context", "question"],
            template=prompt_template
        )
        
        chain = RetrievalQA.from_chain_type(
            llm=llm,
            chain_type="stuff",
            retriever=retriever,
            return_source_documents=True,
            chain_type_kwargs={"prompt": prompt_template_obj}
        )
        result = await chain.ainvoke({"query": prompt})
        answer = result.get("result", "No answer found.")
        
        return answer.strip()
        
    except Exception as e:
        logger.error(f"Error in RAG query for {version}: {e}")
        return f"Error retrieving answer: {str(e)}"

@router.get("/")
async def serve_index():
    return FileResponse("index.html")

@router.post("/upload-document/", status_code=201)
async def upload_document(request: Request, file: UploadFile = File(...)):
    if file.content_type != 'application/pdf':
        raise HTTPException(status_code=400, detail="Invalid file type. Only PDFs are accepted.")

    chroma_client = request.app.state.chroma_client
    if not chroma_client:
        logger.error("ChromaDB client not available. Aborting upload.")
        raise HTTPException(status_code=503, detail="Database connection is not available.")

    with tempfile.TemporaryDirectory() as temp_dir:
        temp_pdf_path = Path(temp_dir) / file.filename
        with open(temp_pdf_path, "wb") as buffer:
            shutil.copyfileobj(file.file, buffer)

        logger.info(f"Temporary file saved at: {temp_pdf_path}")

        try:
            logger.info("--- Starting Phase 0: Extraction ---")
            phase_0_results = process_pdf_local(str(temp_pdf_path))
            doc_output_dir = phase_0_results["output_dir"]
            doc_id = Path(doc_output_dir).name
            logger.info(f"--- Completed Phase 0. Artefacts in: {doc_output_dir} ---")

            logger.info("--- Starting Phase 1: Planning ---")
            create_enrichment_plan(phase_0_results["markdown_path"], doc_output_dir)
            logger.info("--- Completed Phase 1. Enrichment plan created. ---")

            logger.info("--- Starting Phase 2: Generation ---")
            generate_bulk_content(doc_output_dir)
            logger.info("--- Completed Phase 2. Generated content created. ---")

            logger.info("--- Starting Phase 3: Synthesis ---")
            final_markdown_path = synthesize_final_markdown(doc_output_dir)
            if not final_markdown_path:
                logger.error("Phase 3: Synthesis failed to produce a final markdown file.")
                raise HTTPException(status_code=500, detail="Phase 3: Synthesis failed")
            logger.info("--- Completed Phase 3. Final markdown synthesized. ---")
            
            logger.info("--- Starting Phase 4: Vectorization ---")
            success_v1 = vectorize_and_store(doc_output_dir, chroma_client, "markdown_v1.md", "v1")
            success_v2 = vectorize_and_store(doc_output_dir, chroma_client, "markdown_v2.md", "v2")
            if not success_v1 or not success_v2:
                logger.error("Phase 4: Vectorization failed for one or both versions.")
                raise Exception("Phase 4 vectorization failed.")

            logger.info("--- Completed Phase 4. Both versions vectorized. ---")
            return {"message": "Document processed successfully.", "document_id": doc_id}

        except Exception as e:
            logger.error(f"An error occurred during the pipeline: {e}")
            raise HTTPException(status_code=500, detail=f"Pipeline failed: {e}")

@router.post("/ask/")
async def ask_question(request: Request, query: QueryRequest):
    try:
        chroma_client = request.app.state.chroma_client
        embeddings = request.app.state.embedding_function
        chat_model = request.app.state.chat_model
        
        if not embeddings or not chat_model:
            raise HTTPException(status_code=503, detail="AI models not initialized")
        
        v1_task = perform_rag_query(
            query.prompt, 
            query.document_id, 
            "v1", 
            chroma_client, 
            embeddings, 
            chat_model, 
            RAG_PROMPT_TEMPLATE
        )
        v2_task = perform_rag_query(
            query.prompt, 
            query.document_id, 
            "v2", 
            chroma_client, 
            embeddings, 
            chat_model, 
            RAG_PROMPT_TEMPLATE
        )
        
        v1_answer, v2_answer = await asyncio.gather(v1_task, v2_task)
        
        return {
            "unenriched_answer": v1_answer,
            "enriched_answer": v2_answer,
            "prompt": query.prompt
        }
        
    except Exception as e:
        logger.error(f"Error in /ask/ endpoint: {e}")
        raise HTTPException(status_code=500, detail=str(e))