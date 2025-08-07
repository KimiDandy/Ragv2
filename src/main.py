import uvicorn
import chromadb
import time
import asyncio
from contextlib import asynccontextmanager
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from loguru import logger

from src.api.endpoints import router as api_router
from src.core.config import CHROMA_DB_PATH
from src.core.logging_config import setup_logging

# Setup logging as soon as the application starts
setup_logging()

@asynccontextmanager
async def lifespan(app: FastAPI):
    """
    Handles application startup and shutdown events.
    Initializes the ChromaDB client on startup and cleans it up on shutdown.
    """
    logger.info("Lifespan startup: Initializing ChromaDB client...")
    try:
        # Using PersistentClient for local, file-based storage
        app.state.chroma_client = chromadb.PersistentClient(path=CHROMA_DB_PATH)
        logger.info(f"ChromaDB client initialized from path: {CHROMA_DB_PATH}")
    except Exception as e:
        logger.error(f"Failed to initialize ChromaDB client from path {CHROMA_DB_PATH}. Error: {e}")
        app.state.chroma_client = None
    
    yield
    
    logger.info("Lifespan shutdown: Cleaning up resources...")
    app.state.chroma_client = None
    logger.info("Resources cleaned up.")

app = FastAPI(
    title="Genesis-RAG v3.0 API",
    description="A refactored, high-fidelity document enrichment and comparison engine.",
    version="3.0.0",
    lifespan=lifespan
)

# Include the API router
app.include_router(api_router)

# Set up CORS middleware
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # Allows all origins
    allow_credentials=True,
    allow_methods=["*"],  # Allows all methods
    allow_headers=["*"],  # Allows all headers
)

# Mount static files
app.mount("/static", StaticFiles(directory="static"), name="static")

if __name__ == "__main__":
    logger.info("--- Starting Genesis-RAG FastAPI Server ---")
    logger.info(f"Access the interactive docs at http://127.0.0.1:8000/docs")
    uvicorn.run("main:app", host="127.0.0.1", port=8000, reload=True)
