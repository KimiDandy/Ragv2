import os
from dotenv import load_dotenv
from pydantic_settings import BaseSettings

# Load environment variables
load_dotenv()

class Settings(BaseSettings):
    OPENAI_API_KEY: str = os.getenv("OPENAI_API_KEY", "")
    LLM_MODEL: str = os.getenv("LLM_MODEL", "gpt-4.1")
    EMBEDDING_MODEL: str = os.getenv("EMBEDDING_MODEL", "text-embedding-3-small")

    # Pinecone Configuration
    PINECONE_API_KEY: str = os.getenv("PINECONE_API_KEY", "")
    PINECONE_INDEX_NAME: str = os.getenv("PINECONE_INDEX_NAME", "inspigo-pinecone")

    PIPELINE_ARTEFACTS_DIR: str = os.getenv("PIPELINE_ARTEFACTS_DIR", "artefacts")

    PHASE1_TOKEN_BUDGET: int = int(os.getenv("PHASE1_TOKEN_BUDGET", "35000"))
    PHASE2_TOKEN_BUDGET: int = int(os.getenv("PHASE2_TOKEN_BUDGET", "50000"))

    PHASE1_CONCURRENCY: int = int(os.getenv("PHASE1_CONCURRENCY", "7"))
    PHASE1_RPS: float = float(os.getenv("PHASE1_RPS", "3"))
    PHASE2_CONCURRENCY: int = int(os.getenv("PHASE2_CONCURRENCY", "4"))
    PHASE2_RPS: float = float(os.getenv("PHASE2_RPS", "2"))

    RAG_PROMPT_TEMPLATE: str = (
        "Anda adalah AI Assistant yang ahli dalam analisis dokumen dan dapat menggunakan informasi tersurat maupun tersirat untuk menjawab pertanyaan.\n\n"
        "INSTRUKSI ANALISIS:\n"
        "1. **GUNAKAN SEMUA KONTEKS**: Informasi eksplisit, enhancement results, formula, dan pola yang ditemukan\n"
        "2. **BERIMPROVIASI DENGAN DATA**: Jika ada rumus/pola, gunakan untuk membuat proyeksi/estimasi\n"
        "3. **ANALISIS TERSIRAT**: Ekstrak insight yang tidak langsung tertulis tapi bisa disimpulkan\n"
        "4. **JAWABAN KOMPREHENSIF**: Berikan jawaban sedetail mungkin dengan reasoning yang jelas\n\n"
        "KONTEKS DOKUMEN:\n{context}\n\n"
        "PERTANYAAN: {question}\n\n"
        "PANDUAN JAWABAN:\n"
        "- Jika ada data/formula relevan, gunakan untuk kalkulasi dan proyeksi\n"
        "- Jika ada pola/trend, extrapolate untuk scenario yang ditanyakan\n"
        "- Jika ada enhancement tentang topik terkait, integrasikan dalam jawaban\n"
        "- Berikan reasoning step-by-step untuk projections/estimates\n"
        "- Sebutkan asumsi yang digunakan dalam kalkulasi\n"
        "- JAWAB dalam bahasa Indonesia yang professional\n\n"
        "Jika benar-benar tidak ada informasi yang relevan, jelaskan secara spesifik data apa yang dibutuhkan."
    )

settings = Settings()

OPENAI_API_KEY = settings.OPENAI_API_KEY
EMBEDDING_MODEL = settings.EMBEDDING_MODEL
CHAT_MODEL = settings.LLM_MODEL
PINECONE_API_KEY = settings.PINECONE_API_KEY
PINECONE_INDEX_NAME = settings.PINECONE_INDEX_NAME
PIPELINE_ARTEFACTS_DIR = settings.PIPELINE_ARTEFACTS_DIR
RAG_PROMPT_TEMPLATE = settings.RAG_PROMPT_TEMPLATE

PHASE1_TOKEN_BUDGET = settings.PHASE1_TOKEN_BUDGET
PHASE2_TOKEN_BUDGET = settings.PHASE2_TOKEN_BUDGET
PHASE1_CONCURRENCY = settings.PHASE1_CONCURRENCY
PHASE1_RPS = settings.PHASE1_RPS
PHASE2_CONCURRENCY = settings.PHASE2_CONCURRENCY
PHASE2_RPS = settings.PHASE2_RPS

# Embedding dimensions mapping for OpenAI models
EMBEDDING_DIMENSIONS = {
    "text-embedding-3-small": 1536,
    "text-embedding-3-large": 3072,
    "text-embedding-ada-002": 1536,
}

def get_embedding_dimension(model_name: str) -> int:
    """Get embedding dimension for a given model."""
    return EMBEDDING_DIMENSIONS.get(model_name, 1536)  # Default to 1536