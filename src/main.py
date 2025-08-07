import uvicorn
import time
import asyncio
from contextlib import asynccontextmanager
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
import sys
import os
from contextlib import asynccontextmanager

import chromadb
import uvicorn
from dotenv import load_dotenv
from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from loguru import logger
from langchain_google_genai import GoogleGenerativeAIEmbeddings, ChatGoogleGenerativeAI

from src.api.endpoints import router as api_router
from src.core.config import CHROMA_DB_PATH, GOOGLE_API_KEY, EMBEDDING_MODEL, CHAT_MODEL

# --- Simplified Loguru Configuration ---
logger.remove()
logger.add(
    sys.stderr,
    level="INFO",
    format="<green>{time:YYYY-MM-DD HH:mm:ss}</green> | <level>{level: <8}</level> | <cyan>{name}</cyan>:<cyan>{function}</cyan> - <level>{message}</level>"
)
# --- End of Logging Configuration ---

# Load environment variables from .env file
load_dotenv()

@asynccontextmanager
async def lifespan(app: FastAPI):
    logger.info("Startup Aplikasi: Menginisialisasi semua sumber daya...")
    try:
        # 1. Use PersistentClient for a reliable embedded mode
        app.state.chroma_client = chromadb.PersistentClient(path=CHROMA_DB_PATH)
        logger.info(f"ChromaDB client diinisialisasi dari path: {CHROMA_DB_PATH}")

        # 2. Initialize AI models once
        app.state.embedding_function = GoogleGenerativeAIEmbeddings(model=EMBEDDING_MODEL, google_api_key=GOOGLE_API_KEY)
        app.state.chat_model = ChatGoogleGenerativeAI(model=CHAT_MODEL, google_api_key=GOOGLE_API_KEY, temperature=0.7)
        logger.info("Model AI berhasil diinisialisasi.")

    except Exception as e:
        logger.critical(f"GAGAL TOTAL SAAT STARTUP! Tidak dapat menginisialisasi sumber daya. Error: {e}")
        app.state.chroma_client = None
        app.state.embedding_function = None
        app.state.chat_model = None

    yield

    logger.info("Shutdown Aplikasi: Membersihkan sumber daya...")
    app.state.chroma_client = None
    app.state.embedding_function = None
    app.state.chat_model = None
    logger.info("Sumber daya berhasil dibersihkan.")


# Create FastAPI app
app = FastAPI(
    title="Genesis RAG API",
    description="API for document enrichment and comparison using RAG",
    version="3.1",
    lifespan=lifespan
)

# Mount static files (for frontend)
if not os.path.exists("static"):
    os.makedirs("static")
app.mount("/static", StaticFiles(directory="static"), name="static")

# Add CORS middleware
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # Allows all origins
    allow_credentials=True,
    allow_methods=["*"],  # Allows all methods
    allow_headers=["*"],  # Allows all headers
)

# Include API router
app.include_router(api_router)

# Root endpoint to serve the main page
@app.get("/")
async def read_root(request: Request):
    from fastapi.responses import FileResponse
    return FileResponse('index.html')


if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=8000)
