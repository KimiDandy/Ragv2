import shutil
import tempfile
import chromadb
from contextlib import asynccontextmanager
from pathlib import Path
from fastapi import FastAPI, UploadFile, File, HTTPException, Request
from chromadb.config import Settings

# Import a function from each phase of the pipeline
from .phase_0_extraction import process_pdf_local
from .phase_1_planning import create_enrichment_plan
from .phase_2_generation import generate_bulk_content
from .phase_3_synthesis import synthesize_final_markdown
from .phase_4_vectorization import vectorize_and_store

@asynccontextmanager
async def lifespan(app: FastAPI):
    # --- Startup --- 
    print("INFO:     Lifespan startup: Initializing ChromaDB client...")
    # The key is to initialize the client here, once, when the app starts.
    app.state.chroma_client = chromadb.Client(
        Settings(
            chroma_db_impl="duckdb+parquet",
            persist_directory="chroma_db",
            anonymized_telemetry=False
        )
    )
    print("INFO:     ChromaDB client initialized and stored in app.state.")
    
    yield
    
    # --- Shutdown ---
    print("INFO:     Lifespan shutdown: Cleaning up resources...")
    # ChromaDB client using duckdb+parquet doesn't require explicit shutdown.
    # If we were using a server-based client, we would close the connection here.
    app.state.chroma_client = None
    print("INFO:     Resources cleaned up.")

app = FastAPI(
    title="Genesis-RAG Ingestion Pipeline",
    description="An automated pipeline to process, enrich, and vectorize PDF documents.",
    lifespan=lifespan
)

@app.post("/upload-document/", status_code=201)
async def upload_document(request: Request, file: UploadFile = File(...)):
    """
    Accepts a PDF file, processes it through the full Genesis-RAG pipeline,
    and stores the vectorized result in ChromaDB.
    """
    if file.content_type != 'application/pdf':
        raise HTTPException(status_code=400, detail="Invalid file type. Only PDFs are accepted.")

    # Use a temporary directory to handle the uploaded file securely
    with tempfile.TemporaryDirectory() as temp_dir:
        temp_pdf_path = Path(temp_dir) / file.filename
        with open(temp_pdf_path, "wb") as buffer:
            shutil.copyfileobj(file.file, buffer)

        print(f"Temporary file saved at: {temp_pdf_path}")

        try:
            # Phase 0: Local Extraction
            print("--- Starting Phase 0: Extraction ---")
            phase_0_results = process_pdf_local(str(temp_pdf_path))
            doc_output_dir = phase_0_results["output_dir"]
            doc_id = Path(doc_output_dir).name
            print(f"--- Completed Phase 0. Artefacts in: {doc_output_dir} ---")

            # Phase 1: Planning
            print("--- Starting Phase 1: Planning ---")
            create_enrichment_plan(phase_0_results["markdown_path"], doc_output_dir)
            print("--- Completed Phase 1. Enrichment plan created. ---")

            # Phase 2: Generation
            print("--- Starting Phase 2: Generation ---")
            generate_bulk_content(doc_output_dir)
            print("--- Completed Phase 2. Generated content created. ---")

            # Phase 3: Synthesis
            print("--- Starting Phase 3: Synthesis ---")
            synthesize_final_markdown(doc_output_dir)
            print("--- Completed Phase 3. Final markdown synthesized. ---")

            # Phase 4: Vectorization
            print("--- Starting Phase 4: Vectorization ---")
            # Retrieve the shared client from the application state
            chroma_client = request.app.state.chroma_client
            vectorize_and_store(doc_output_dir, chroma_client)
            print("--- Completed Phase 4. Document vectorized. ---")

        except Exception as e:
            # Basic error handling
            print(f"An error occurred during the pipeline: {e}")
            raise HTTPException(status_code=500, detail=f"Pipeline failed: {e}")

    return {
        "message": "Document processed and vectorized successfully.",
        "document_id": doc_id
    }

if __name__ == "__main__":
    import uvicorn
    print("Starting Genesis-RAG FastAPI server...")
    print("Access the interactive docs at http://127.0.0.1:8000/docs")
    uvicorn.run(app, host="127.0.0.1", port=8000)
