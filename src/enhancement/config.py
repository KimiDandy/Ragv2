"""
Configuration module for the Enhancement system.
"""

from pydantic_settings import BaseSettings
from pydantic import Field
from typing import Optional, List, Dict, Any
import os
from dotenv import load_dotenv

load_dotenv()


class EnhancementConfig(BaseSettings):
    """Configuration for the Enhancement system."""
    
    # Window configuration for planning
    # Lebih besar agar dokumen kecil tidak terbelah jadi 2 window tanpa perlu
    window_tokens: int = Field(default=8000, env='ENH_WINDOW_TOKENS')
    window_overlap_tokens: int = Field(default=400, env='ENH_WINDOW_OVERLAP_TOKENS')
    
    # Planning configuration
    planner_parallelism: int = Field(default=1, env='ENH_PLANNER_PARALLELISM')
    planner_model: str = Field(default="gpt-4.1", env='ENH_PLANNER_MODEL')
    max_candidates_per_window: int = Field(default=30, env='ENH_MAX_CANDIDATES_PER_WINDOW')
    
    # Generation configuration
    gen_microbatch_size: int = Field(default=6, env='ENH_GEN_MICROBATCH_SIZE')
    gen_model: str = Field(default="gpt-4.1", env='ENH_GEN_MODEL')
    # Target total item per dokumen (akan dipangkas oleh dedup). Naikkan untuk dokumen kaya informasi
    target_items: int = Field(default=120, env='ENH_TARGET_ITEMS')
    max_generation_tokens: int = Field(default=150, env='ENH_MAX_GEN_TOKENS')
    
    # Enhancement types toggles
    enable_glossary: bool = Field(default=True, env='ENH_ENABLE_GLOSSARY')
    enable_highlight: bool = Field(default=True, env='ENH_ENABLE_HIGHLIGHT')
    enable_faq: bool = Field(default=True, env='ENH_ENABLE_FAQ')
    enable_caption: bool = Field(default=True, env='ENH_ENABLE_CAPTION')
    enable_ondemand: bool = Field(default=True, env='ENH_ENABLE_ONDEMAND')
    
    # Embedding configuration
    embedding_model: str = Field(default="text-embedding-3-small", env='ENH_EMBEDDING_MODEL')
    embedding_batch_size: int = Field(default=100, env='ENH_EMBEDDING_BATCH_SIZE')
    
    # Retrieval configuration
    retrieval_top_k: int = Field(default=10, env='ENH_RETRIEVAL_TOP_K')
    retrieval_rerank_top_k: int = Field(default=5, env='ENH_RETRIEVAL_RERANK_TOP_K')
    
    # OpenAI configuration
    openai_api_key: str = Field(env='OPENAI_API_KEY')
    openai_temperature: float = Field(default=0.3, env='ENH_OPENAI_TEMPERATURE')
    openai_max_retries: int = Field(default=3, env='ENH_OPENAI_MAX_RETRIES')
    openai_timeout: int = Field(default=60, env='ENH_OPENAI_TIMEOUT')
    
    # Rate limiting
    requests_per_second: float = Field(default=2.0, env='ENH_REQUESTS_PER_SECOND')
    
    # Caching and storage
    cache_dir: str = Field(default="./cache/enhancement", env='ENH_CACHE_DIR')
    artifacts_dir: str = Field(default="./artefacts", env='ENH_ARTIFACTS_DIR')
    
    # Priority terms for financial/insurance domain
    priority_terms: List[str] = Field(
        default=[
            "premi", "polis", "klaim", "pengecualian", "underwriting",
            "tenor", "yield", "bps", "bunga", "suku bunga", "investasi",
            "pertanggungan", "manfaat", "risiko", "asuransi", "nasabah",
            "deposito", "tabungan", "giro", "kredit", "dana", "valas",
            "OJK", "Bank Indonesia", "BI", "BPJS", "LPS"
        ]
    )
    
    # Numeric calculation settings
    enable_server_calc: bool = Field(default=True, env='ENH_ENABLE_SERVER_CALC')
    calc_precision: int = Field(default=2, env='ENH_CALC_PRECISION')
    
    class Config:
        env_file = ".env"
        case_sensitive = False
        extra = "ignore"  # Ignore extra environment variables
    
    def get_enabled_enhancement_types(self) -> List[str]:
        """Get list of enabled enhancement types."""
        types = []
        if self.enable_glossary:
            types.append("glossary")
        if self.enable_highlight:
            types.append("highlight")
        if self.enable_faq:
            types.append("faq")
        if self.enable_caption:
            types.append("caption")
        return types
    
    def to_dict(self) -> Dict[str, Any]:
        """Convert configuration to dictionary (excluding sensitive data)."""
        data = self.dict()
        # Remove sensitive keys
        data.pop('openai_api_key', None)
        return data
