import uvicorn
import chromadb
import time
import asyncio
from contextlib import asynccontextmanager
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from langchain_google_genai import GoogleGenerativeAIEmbeddings, ChatGoogleGenerativeAI
from loguru import logger

from src.core.config import GOOGLE_API_KEY, EMBEDDING_MODEL, CHAT_MODEL
from src.core.logging_config import setup_logging
from src.api.endpoints import router as api_router
from src.core.config import CHROMA_DB_PATH

# Setup logging as soon as the application starts
setup_logging()

@asynccontextmanager
async def lifespan(app: FastAPI):
    """
    Manages startup and shutdown events for the FastAPI application.
    Initializes the ChromaDB client and AI models.
    """
    from loguru import logger # Re-import logger to fix scope issue with uvicorn reloader
    logger.info("Lifespan startup: Connecting to ChromaDB server...")
    try:
        app.state.chroma_client = chromadb.HttpClient(host="127.0.0.1", port=8001)
        app.state.chroma_client.heartbeat()
        logger.info("Successfully connected to ChromaDB server.")
    except Exception as e:
        logger.error(f"Failed to connect to ChromaDB server. Ensure the server is running with 'chroma run --path chroma_db --port 8001'. Error: {e}")
        app.state.chroma_client = None

    logger.info("Lifespan startup: Initializing AI models...")
    try:
        app.state.embedding_function = GoogleGenerativeAIEmbeddings(model=EMBEDDING_MODEL, google_api_key=GOOGLE_API_KEY)
        app.state.chat_model = ChatGoogleGenerativeAI(model=CHAT_MODEL, google_api_key=GOOGLE_API_KEY, temperature=0.7)
        logger.info("AI models initialized successfully.")
    except Exception as e:
        logger.error(f"Failed to initialize AI models: {e}")
        app.state.embedding_function = None
        app.state.chat_model = None

    yield

    logger.info("Lifespan shutdown: Cleaning up resources...")
    # No explicit cleanup required for HttpClient or LangChain models
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
