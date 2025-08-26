import os
from dotenv import load_dotenv
from pydantic_settings import BaseSettings

# Load environment variables
load_dotenv()

class Settings(BaseSettings):
    # OpenAI
    OPENAI_API_KEY: str = os.getenv("OPENAI_API_KEY", "")
    LLM_MODEL: str = os.getenv("LLM_MODEL", "gpt-4.1")
    EMBEDDING_MODEL: str = os.getenv("EMBEDDING_MODEL", "text-embedding-3-large")

    # Chroma
    CHROMA_COLLECTION: str = os.getenv("CHROMA_COLLECTION", "genesis_rag_collection")
    CHROMA_DB_PATH: str = os.getenv("CHROMA_DB_PATH", "./chroma_db")
    CHROMA_MODE: str = os.getenv("CHROMA_MODE", "embedded")  # or "server"
    CHROMA_SERVER_HOST: str = os.getenv("CHROMA_SERVER_HOST", "localhost")
    CHROMA_SERVER_PORT: int = int(os.getenv("CHROMA_SERVER_PORT", "8000"))

    # Pipeline directories
    PIPELINE_ARTEFACTS_DIR: str = os.getenv("PIPELINE_ARTEFACTS_DIR", "artefacts")

    # Budgets (tokens)
    PHASE1_TOKEN_BUDGET: int = int(os.getenv("PHASE1_TOKEN_BUDGET", "35000"))
    PHASE2_TOKEN_BUDGET: int = int(os.getenv("PHASE2_TOKEN_BUDGET", "50000"))

    # Concurrency & rate limit
    PHASE1_CONCURRENCY: int = int(os.getenv("PHASE1_CONCURRENCY", "7"))
    PHASE1_RPS: float = float(os.getenv("PHASE1_RPS", "3"))
    PHASE2_CONCURRENCY: int = int(os.getenv("PHASE2_CONCURRENCY", "4"))
    PHASE2_RPS: float = float(os.getenv("PHASE2_RPS", "2"))

    # RAG prompt template (LangChain PromptTemplate expects {context} and {question})
    RAG_PROMPT_TEMPLATE: str = (
        "You are a precise assistant. Use only the CONTEXT to answer.\n\n"
        "CONTEXT:\n{context}\n\n"
        "QUESTION: {question}\n\n"
        "If insufficient context, say you don't know."
    )

settings = Settings()

# Backwards-compatibility constants (minimize churn in existing modules)
OPENAI_API_KEY = settings.OPENAI_API_KEY
EMBEDDING_MODEL = settings.EMBEDDING_MODEL
CHAT_MODEL = settings.LLM_MODEL
CHROMA_MODE = settings.CHROMA_MODE
CHROMA_DB_PATH = settings.CHROMA_DB_PATH
CHROMA_SERVER_HOST = settings.CHROMA_SERVER_HOST
CHROMA_SERVER_PORT = settings.CHROMA_SERVER_PORT
CHROMA_COLLECTION = settings.CHROMA_COLLECTION
PIPELINE_ARTEFACTS_DIR = settings.PIPELINE_ARTEFACTS_DIR
RAG_PROMPT_TEMPLATE = settings.RAG_PROMPT_TEMPLATE

# Performance budgets and limits
PHASE1_TOKEN_BUDGET = settings.PHASE1_TOKEN_BUDGET
PHASE2_TOKEN_BUDGET = settings.PHASE2_TOKEN_BUDGET
PHASE1_CONCURRENCY = settings.PHASE1_CONCURRENCY
PHASE1_RPS = settings.PHASE1_RPS
PHASE2_CONCURRENCY = settings.PHASE2_CONCURRENCY
PHASE2_RPS = settings.PHASE2_RPS