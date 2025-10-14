"""
Configuration module for the Enhancement system.

Now loads from global_config.json for centralized configuration management.
"""

from pydantic_settings import BaseSettings
from pydantic import Field
from typing import Optional, List, Dict, Any
import os
from pathlib import Path
import json
from dotenv import load_dotenv

load_dotenv()


def load_global_config() -> Dict[str, Any]:
    """
    Load global configuration from JSON file.
    
    Returns:
        Dict containing global configuration
    """
    config_path = Path(__file__).parent.parent / "core" / "enhancement_profiles" / "global_config.json"
    
    if not config_path.exists():
        raise FileNotFoundError(f"Global config not found: {config_path}")
    
    with open(config_path, 'r', encoding='utf-8') as f:
        return json.load(f)


class EnhancementConfig(BaseSettings):
    """
    Configuration for the Enhancement system.
    
    Loads default values from global_config.json, can be overridden by environment variables.
    """
    
    # Load global config
    _global_config: Dict[str, Any] = load_global_config()
    
    # Window configuration from global_config.llm
    window_tokens: int = Field(default=_global_config["llm"]["window_size"], env='ENH_WINDOW_TOKENS')
    window_overlap_tokens: int = Field(default=_global_config["llm"]["window_overlap_tokens"], env='ENH_WINDOW_OVERLAP_TOKENS')
    
    # Parallel processing from global_config.enhancement
    planner_parallelism: int = Field(default=_global_config["enhancement"]["max_parallel_windows"], env='ENH_PLANNER_PARALLELISM')
    planner_model: str = Field(default=_global_config["llm"]["model"], env='ENH_PLANNER_MODEL')
    max_candidates_per_window: int = Field(default=0, env='ENH_MAX_CANDIDATES_PER_WINDOW')  # 0 = no limit
    
    # Generation configuration from global_config.llm
    gen_microbatch_size: int = Field(default=6, env='ENH_GEN_MICROBATCH_SIZE')  # Quality over quantity
    gen_model: str = Field(default=_global_config["llm"]["model"], env='ENH_GEN_MODEL')
    target_items: int = Field(default=0, env='ENH_TARGET_ITEMS')  # 0 = no artificial limit
    max_generation_tokens: int = Field(default=_global_config["llm"]["max_tokens"], env='ENH_MAX_GEN_TOKENS')
    
    # NOTE: Enhancement types are NOT configured here!
    # They are dynamically selected from client profiles (client_profile_*.json)
    # See: src/core/enhancement_profiles/client_profile_danamon.json
    # The client profile's enhancement_types section defines which of the 32 
    # available types should be enabled for each client.
    
    # Embedding configuration from global_config.embedding
    embedding_model: str = Field(default=_global_config["embedding"]["model"], env='ENH_EMBEDDING_MODEL')
    embedding_batch_size: int = Field(default=_global_config["embedding"]["batch_size"], env='ENH_EMBEDDING_BATCH_SIZE')
    
    # Retrieval configuration
    retrieval_top_k: int = Field(default=10, env='ENH_RETRIEVAL_TOP_K')
    retrieval_rerank_top_k: int = Field(default=5, env='ENH_RETRIEVAL_RERANK_TOP_K')
    
    # OpenAI configuration from global_config.llm
    openai_api_key: Optional[str] = Field(default=None, env='OPENAI_API_KEY')
    openai_temperature: float = Field(default=_global_config["llm"]["temperature"], env='ENH_OPENAI_TEMPERATURE')
    openai_max_retries: int = Field(default=_global_config["llm"]["retry_attempts"], env='ENH_OPENAI_MAX_RETRIES')
    openai_timeout: int = Field(default=_global_config["llm"]["timeout_seconds"], env='ENH_OPENAI_TIMEOUT')
    
    # Pinecone configuration from global_config.vectorstore
    pinecone_api_key: Optional[str] = Field(default=None, env='PINECONE_API_KEY')
    pinecone_index_name: Optional[str] = Field(default=_global_config["vectorstore"]["pinecone_index"], env='PINECONE_INDEX_NAME')
    
    # Rate limiting from global_config.llm
    requests_per_second: float = Field(default=_global_config["llm"]["requests_per_second"], env='ENH_REQUESTS_PER_SECOND')
    
    # Caching and storage
    cache_dir: str = Field(default="./cache/enhancement", env='ENH_CACHE_DIR')
    artifacts_dir: str = Field(default="./artefacts", env='ENH_ARTIFACTS_DIR')
    
    # Universal priority indicators (not domain-specific)
    priority_indicators: List[str] = Field(
        default=[],  # Empty by default - let system detect from content
        description="Dynamic list populated based on document analysis"
    )
    
    # Numeric calculation settings from global_config.enhancement
    enable_server_calc: bool = Field(default=_global_config.get("enhancement", {}).get("enable_server_calc", True), env='ENH_ENABLE_SERVER_CALC')
    calc_precision: int = Field(default=_global_config.get("enhancement", {}).get("calc_precision", 2), env='ENH_CALC_PRECISION')
    
    class Config:
        env_file = ".env"
        case_sensitive = False
        extra = "ignore"  # Ignore extra environment variables
        arbitrary_types_allowed = True  # Allow dict types
    
    def __init__(self, **kwargs):
        """Initialize with automatic environment loading"""
        # Auto-load from environment if not provided
        if 'openai_api_key' not in kwargs:
            kwargs['openai_api_key'] = os.getenv('OPENAI_API_KEY')
        if 'pinecone_api_key' not in kwargs:
            kwargs['pinecone_api_key'] = os.getenv('PINECONE_API_KEY')
        if 'pinecone_index_name' not in kwargs:
            pinecone_index = os.getenv('PINECONE_INDEX_NAME')
            if not pinecone_index:
                # Load from global_config if not in env
                global_cfg = load_global_config()
                pinecone_index = global_cfg.get("vectorstore", {}).get("pinecone_index", "inspigo-pinecone")
            kwargs['pinecone_index_name'] = pinecone_index
        
        super().__init__(**kwargs)
    
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
        data.pop('pinecone_api_key', None)
        return data
