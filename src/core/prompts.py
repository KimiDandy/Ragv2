# src/core/prompts.py
from pathlib import Path
from .config import BASE_DIR
from loguru import logger

PROMPTS_DIR = BASE_DIR / "prompts"


def load_prompt(filename: str, default_text: str) -> str:
    """Load a prompt template from src/prompts/<filename>.
    If the file is missing, return default_text.
    """
    try:
        path = PROMPTS_DIR / filename
        if path.exists():
            return path.read_text(encoding="utf-8")
    except Exception as e:
        logger.warning(f"Gagal membaca prompt {filename}: {e}")
    return default_text
