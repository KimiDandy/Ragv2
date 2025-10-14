"""
Core configuration module - Loads from global_config.json and .env

Architecture:
- API Keys: From .env file (secrets)
- Business Config: From global_config.json (LLM model, embedding, etc.)
- No hardcoded defaults for business logic
"""

import os
import json
from pathlib import Path
from dotenv import load_dotenv

# Load environment variables (API keys only)
load_dotenv()


def _load_global_config():
    """Load global configuration from JSON file."""
    config_path = Path(__file__).parent / "enhancement_profiles" / "global_config.json"
    
    if not config_path.exists():
        raise FileNotFoundError(
            f"❌ global_config.json not found at: {config_path}\n"
            f"This file is REQUIRED for application startup."
        )
    
    with open(config_path, 'r', encoding='utf-8') as f:
        return json.load(f)


# Load global config (single source of truth for business logic)
_global_config = _load_global_config()

# ============================================
# SECRETS (from .env file)
# ============================================
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY", "")
PINECONE_API_KEY = os.getenv("PINECONE_API_KEY", "")

# ============================================
# BUSINESS CONFIG (from global_config.json)
# ============================================
# LLM Configuration
CHAT_MODEL = _global_config["llm"]["model"]
LLM_MAX_TOKENS = _global_config["llm"]["max_tokens"]
LLM_TEMPERATURE = _global_config["llm"]["temperature"]

# Embedding Configuration
EMBEDDING_MODEL = _global_config["embedding"]["model"]
EMBEDDING_DIMENSION = _global_config["embedding"]["dimension"]

# Pinecone Configuration
PINECONE_INDEX_NAME = _global_config["vectorstore"]["pinecone_index"]

# ============================================
# PATHS (can override via .env)
# ============================================
PIPELINE_ARTEFACTS_DIR = os.getenv("PIPELINE_ARTEFACTS_DIR", "artefacts")


def get_embedding_dimension(model_name: str) -> int:
    """
    Get embedding dimension for a given model.
    
    Returns dimension from global_config.json by default.
    Fallback to 1536 only if model not recognized.
    """
    # Use dimension from global config
    if model_name == EMBEDDING_MODEL:
        return EMBEDDING_DIMENSION
    
    # Fallback for unknown models
    return 1536