import shutil
import tempfile
import chromadb
from contextlib import asynccontextmanager
from pathlib import Path
from fastapi import FastAPI, UploadFile, File, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse
from pydantic import BaseModel
import os
import asyncio
import json

from .phase_0_extraction import process_pdf_local
from .phase_1_planning import create_enrichment_plan
from .phase_2_generation import generate_bulk_content
from .phase_3_synthesis import synthesize_final_markdown
from .phase_4_vectorization import vectorize_and_store

@asynccontextmanager
async def lifespan(app: FastAPI):
    print("INFO:     Lifespan startup: Initializing ChromaDB client...")
    app.state.chroma_client = chromadb.HttpClient(host="localhost", port=8001)
    print("INFO:     ChromaDB HttpClient connected.")
    
    yield
    
    print("INFO:     Lifespan shutdown: Cleaning up resources...")
    app.state.chroma_client = None
    print("INFO:     Resources cleaned up.")

app = FastAPI(
    title="Genesis-RAG v2.0 API",
    description="High-fidelity document enrichment and comparison engine",
    version="2.0.0",
    lifespan=lifespan
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.mount("/static", StaticFiles(directory="."), name="static")

@app.get("/")
async def serve_index():
    return FileResponse("index.html")

@app.post("/upload-document/", status_code=201)
async def upload_document(request: Request, file: UploadFile = File(...)):
    if file.content_type != 'application/pdf':
        raise HTTPException(status_code=400, detail="Invalid file type. Only PDFs are accepted.")

    with tempfile.TemporaryDirectory() as temp_dir:
        temp_pdf_path = Path(temp_dir) / file.filename
        with open(temp_pdf_path, "wb") as buffer:
            shutil.copyfileobj(file.file, buffer)

        print(f"Temporary file saved at: {temp_pdf_path}")

        try:
            print("--- Starting Phase 0: Extraction ---")
            phase_0_results = process_pdf_local(str(temp_pdf_path))
            doc_output_dir = phase_0_results["output_dir"]
            doc_id = Path(doc_output_dir).name
            print(f"--- Completed Phase 0. Artefacts in: {doc_output_dir} ---")

            print("--- Starting Phase 1: Planning ---")
            create_enrichment_plan(phase_0_results["markdown_path"], doc_output_dir)
            print("--- Completed Phase 1. Enrichment plan created. ---")

            print("--- Starting Phase 2: Generation ---")
            generated_content_path = generate_bulk_content(doc_output_dir)
            print("--- Completed Phase 2. Generated content created. ---")

            final_markdown_path = synthesize_final_markdown(doc_output_dir)
            if not final_markdown_path:
                raise HTTPException(status_code=500, detail="Phase 3: Synthesis failed")
            print("--- Starting Phase 4: Vectorization ---")
            try:
                from src.phase_4_vectorization import vectorize_and_store
                
                success_v1 = vectorize_and_store(doc_output_dir, app.state.chroma_client, "markdown_v1.md", "v1")
                if not success_v1:
                    raise Exception("Phase 4 vectorization failed for v1.")
                
                success_v2 = vectorize_and_store(doc_output_dir, app.state.chroma_client, "markdown_v2.md", "v2")
                if not success_v2:
                    raise Exception("Phase 4 vectorization failed for v2.")
                    
            except Exception as e:
                print(f"Error during Phase 4: {e}")
                raise HTTPException(status_code=500, detail="Vectorization failed.")

            print("--- Completed Phase 4. Both versions vectorized. ---")
            return {"message": "Document processed successfully.", "document_id": doc_id}

        except Exception as e:
            print(f"An error occurred during the pipeline: {e}")
            raise HTTPException(status_code=500, detail=f"Pipeline failed: {e}")

class QueryRequest(BaseModel):
    document_id: str
    prompt: str

async def perform_rag_query(prompt: str, doc_id: str, version: str, client: chromadb.Client, embeddings) -> str:
    try:
        from langchain_chroma import Chroma
        
        vector_store = Chroma(
            client=client,
            collection_name="genesis_rag_collection",
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
        
        print(f"\n--- CONTEXT FOR VERSION: {version} ---")
        print(context)
        print("--- END OF CONTEXT ---\n")
        
        from langchain_google_genai import ChatGoogleGenerativeAI
        from langchain.prompts import PromptTemplate
        from langchain.chains import LLMChain
        
        llm = ChatGoogleGenerativeAI(
            model="gemini-2.5-flash",
            google_api_key=os.getenv("GOOGLE_API_KEY"),
            temperature=0.7
        )
        
        prompt_template = PromptTemplate(
            input_variables=["context", "question"],
            template="""
            Anda adalah asisten yang membantu menjawab pertanyaan berdasarkan konteks yang diberikan.
            
            Konteks:
            {context}
            
            Pertanyaan: {question}
            
            Jawaban:
            """
        )
        
        chain = LLMChain(llm=llm, prompt=prompt_template)
        answer = chain.run(context=context, question=prompt)
        
        return answer.strip()
        
    except Exception as e:
        print(f"Error in RAG query for {version}: {e}")
        return f"Error retrieving answer: {str(e)}"

@app.post("/ask/")
async def ask_question(request: QueryRequest):
    """
    Endpoint untuk mengajukan pertanyaan dan mendapatkan perbandingan jawaban v1 vs v2.
    """
    try:
        from langchain_google_genai import GoogleGenerativeAIEmbeddings
        
        # Initialize embeddings
        api_key = os.getenv("GOOGLE_API_KEY")
        if not api_key:
            raise HTTPException(status_code=500, detail="Google API key not configured.")
        
        embeddings = GoogleGenerativeAIEmbeddings(
            model="models/embedding-001",
            google_api_key=api_key
        )
        
        v1_task = perform_rag_query(
            request.prompt, 
            request.document_id, 
            "v1", 
            app.state.chroma_client, 
            embeddings
        )
        v2_task = perform_rag_query(
            request.prompt, 
            request.document_id, 
            "v2", 
            app.state.chroma_client, 
            embeddings
        )
        
        v1_answer, v2_answer = await asyncio.gather(v1_task, v2_task)
        
        return {
            "unenriched_answer": v1_answer,
            "enriched_answer": v2_answer,
            "prompt": request.prompt
        }
        
    except Exception as e:
        print(f"Error in /ask/ endpoint: {e}")
        raise HTTPException(status_code=500, detail=str(e))

if __name__ == "__main__":
    import uvicorn
    print("Starting Genesis-RAG FastAPI server...")
    print("Access the interactive docs at http://127.0.0.1:8000/docs")
    uvicorn.run(app, host="127.0.0.1", port=8000)
