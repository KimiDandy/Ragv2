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
from langchain_openai import OpenAIEmbeddings, ChatOpenAI

from src.api.endpoints import router as api_router
from src.core.config import (
    CHROMA_DB_PATH,
    EMBEDDING_MODEL,
    CHAT_MODEL,
    CHROMA_MODE,
    CHROMA_SERVER_HOST,
    CHROMA_SERVER_PORT,
)

logger.remove()
logger.add(
    sys.stderr,
    level="INFO",
    format="<green>{time:YYYY-MM-DD HH:mm:ss}</green> | <level>{level: <8}</level> | <cyan>{name}</cyan>:<cyan>{function}</cyan> - <level>{message}</level>"
)

load_dotenv()

@asynccontextmanager
async def lifespan(app: FastAPI):
    """
    Mengelola event startup dan shutdown untuk aplikasi FastAPI.
    Fungsi ini menginisialisasi client ChromaDB dan model-model AI
    saat aplikasi pertama kali dijalankan untuk memastikan semua sumber daya siap pakai.
    """
    logger.info("Startup Aplikasi: Menginisialisasi semua sumber daya...")
    try:
        # 1. Inisialisasi ChromaDB sesuai mode
        if CHROMA_MODE == "server":
            app.state.chroma_client = chromadb.HttpClient(host=CHROMA_SERVER_HOST, port=CHROMA_SERVER_PORT)
            logger.info(f"ChromaDB HTTP client terhubung ke http://{CHROMA_SERVER_HOST}:{CHROMA_SERVER_PORT}")
        else:
            try:
                app.state.chroma_client = chromadb.PersistentClient(path=CHROMA_DB_PATH)
                logger.info(f"ChromaDB embedded diinisialisasi dari path: {CHROMA_DB_PATH}")
            except Exception as emb_err:
                logger.warning(f"Embedded mode gagal: {emb_err}. Mencoba HttpClient ke http://{CHROMA_SERVER_HOST}:{CHROMA_SERVER_PORT}...")
                app.state.chroma_client = chromadb.HttpClient(host=CHROMA_SERVER_HOST, port=CHROMA_SERVER_PORT)
                logger.info(f"ChromaDB HTTP client terhubung ke http://{CHROMA_SERVER_HOST}:{CHROMA_SERVER_PORT}")

        # 2. Initialize AI models
        app.state.embedding_function = OpenAIEmbeddings(model=EMBEDDING_MODEL)
        app.state.chat_model = ChatOpenAI(model=CHAT_MODEL, temperature=0.7)
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


app = FastAPI(
    title="Genesis RAG API",
    description="API untuk enrichment dan perbandingan dokumen menggunakan arsitektur RAG",
    version="3.2",
    lifespan=lifespan
)

if not os.path.exists("static"):
    os.makedirs("static")
app.mount("/static", StaticFiles(directory="static"), name="static")
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(api_router)

@app.get("/")
async def read_root(request: Request):
    from fastapi.responses import FileResponse
    return FileResponse('index.html')


if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=8000)
