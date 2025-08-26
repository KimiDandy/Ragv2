# src/core/config.py
import os
from dotenv import load_dotenv
from pathlib import Path

# Muat variabel dari file .env
load_dotenv()

# Konfigurasi Path
BASE_DIR = Path(__file__).resolve().parent.parent
PIPELINE_ARTEFACTS_DIR = "pipeline_artefacts"

# Konfigurasi Kunci API
GOOGLE_API_KEY = os.getenv("GOOGLE_API_KEY")

# Konfigurasi Model
PLANNING_MODEL = "gemini-2.5-flash"
GENERATION_MODEL = "gemini-2.5-flash"
CHAT_MODEL = "gemini-2.5-flash"
EMBEDDING_MODEL = "models/embedding-001"

# Backend Embedding: 'google' atau 'local'
EMBEDDING_BACKEND = os.getenv("EMBEDDING_BACKEND", "google").lower()
# Model lokal HuggingFace (butuh paket sentence-transformers terpasang)
EMBEDDING_LOCAL_MODEL = os.getenv("EMBEDDING_LOCAL_MODEL", "all-MiniLM-L6-v2")

# Konfigurasi ChromaDB
CHROMA_DB_PATH = "chroma_db"  # digunakan saat embedded mode
CHROMA_COLLECTION = "genesis_rag_collection"

# Mode koneksi Chroma: 'server' (HTTP client) atau 'embedded'
CHROMA_MODE = os.getenv("CHROMA_MODE", "server").lower()
CHROMA_SERVER_HOST = os.getenv("CHROMA_SERVER_HOST", "localhost")
CHROMA_SERVER_PORT = int(os.getenv("CHROMA_SERVER_PORT", "8001"))

# Konfigurasi Prompt Template
RAG_PROMPT_TEMPLATE = """
Anda adalah asisten yang membantu menjawab pertanyaan berdasarkan konteks yang diberikan.

Konteks:
{context}

Pertanyaan: {question}

Jawaban:
"""
