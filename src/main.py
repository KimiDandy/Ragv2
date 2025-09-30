import uvicorn
import sys
import os
from contextlib import asynccontextmanager

from pinecone import Pinecone
from dotenv import load_dotenv
from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from langchain_openai import ChatOpenAI, OpenAIEmbeddings
from loguru import logger

from src.api.routes import router as api_router
from src.core.config import (
    EMBEDDING_MODEL,
    CHAT_MODEL,
    PINECONE_API_KEY,
    PINECONE_INDEX_NAME,
    PIPELINE_ARTEFACTS_DIR,
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
    Fungsi ini menginisialisasi client Pinecone dan model-model AI
    saat aplikasi pertama kali dijalankan untuk memastikan semua sumber daya siap pakai.
    """
    logger.info("Startup Aplikasi: Menginisialisasi semua sumber daya...")
    try:
        # 1. Inisialisasi Pinecone
        pc = Pinecone(api_key=PINECONE_API_KEY)
        app.state.pinecone_client = pc
        app.state.pinecone_index = pc.Index(PINECONE_INDEX_NAME)
        logger.info(f"Pinecone client terhubung ke index: {PINECONE_INDEX_NAME}")

        # 2. Initialize AI models
        app.state.embedding_function = OpenAIEmbeddings(model=EMBEDDING_MODEL)
        app.state.chat_model = ChatOpenAI(model=CHAT_MODEL, temperature=0.7)
        logger.info("Model AI berhasil diinisialisasi.")

    except Exception as e:
        logger.critical(f"GAGAL TOTAL SAAT STARTUP! Tidak dapat menginisialisasi sumber daya. Error: {e}")
        app.state.pinecone_client = None
        app.state.pinecone_index = None
        app.state.embedding_function = None
        app.state.chat_model = None

    yield

    logger.info("Shutdown Aplikasi: Membersihkan sumber daya...")
    app.state.pinecone_client = None
    app.state.pinecone_index = None
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
app.mount("/artefacts", StaticFiles(directory=PIPELINE_ARTEFACTS_DIR), name="artefacts")
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
