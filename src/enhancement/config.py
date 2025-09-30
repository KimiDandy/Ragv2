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
    
    # Window configuration optimized for GPT-4.1 (reduced for JSON stability)
    # Smaller windows for better JSON output consistency
    window_tokens: int = Field(default=12000, env='ENH_WINDOW_TOKENS')  # Reduced from 32k to 12k
    window_overlap_tokens: int = Field(default=1500, env='ENH_WINDOW_OVERLAP_TOKENS')
    
    planner_parallelism: int = Field(default=1, env='ENH_PLANNER_PARALLELISM')
    planner_model: str = Field(default="gpt-4.1", env='ENH_PLANNER_MODEL')
    max_candidates_per_window: int = Field(default=0, env='ENH_MAX_CANDIDATES_PER_WINDOW')  # 0 = no limit
    
    # Generation configuration - MAXIMIZE GPT-4.1 OUTPUT
    gen_microbatch_size: int = Field(default=6, env='ENH_GEN_MICROBATCH_SIZE')  # Quality over quantity
    gen_model: str = Field(default="gpt-4.1", env='ENH_GEN_MODEL')
    target_items: int = Field(default=0, env='ENH_TARGET_ITEMS')  # 0 = no artificial limit
    max_generation_tokens: int = Field(default=3000, env='ENH_MAX_GEN_TOKENS')  # Safe limit for GPT-4.1
    
    # Enhancement types toggles - PRIORITIZE IMPLICIT INFO
    enable_formula_discovery: bool = Field(default=True, env='ENH_ENABLE_FORMULA')
    enable_scenario_analysis: bool = Field(default=True, env='ENH_ENABLE_SCENARIO')
    enable_pattern_recognition: bool = Field(default=True, env='ENH_ENABLE_PATTERN')
    enable_projection: bool = Field(default=True, env='ENH_ENABLE_PROJECTION')
    enable_requirement_synthesis: bool = Field(default=True, env='ENH_ENABLE_REQUIREMENT')
    # Legacy (for backward compatibility)
    enable_glossary: bool = Field(default=False, env='ENH_ENABLE_GLOSSARY')
    enable_highlight: bool = Field(default=False, env='ENH_ENABLE_HIGHLIGHT')
    enable_faq: bool = Field(default=False, env='ENH_ENABLE_FAQ')
    
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
    
    # Pinecone configuration
    pinecone_api_key: str = Field(env='PINECONE_API_KEY')
    pinecone_index_name: str = Field(env='PINECONE_INDEX_NAME')
    
    # Rate limiting
    requests_per_second: float = Field(default=2.0, env='ENH_REQUESTS_PER_SECOND')
    
    # Caching and storage
    cache_dir: str = Field(default="./cache/enhancement", env='ENH_CACHE_DIR')
    artifacts_dir: str = Field(default="./artefacts", env='ENH_ARTIFACTS_DIR')
    
    # Universal priority indicators (not domain-specific)
    priority_indicators: List[str] = Field(
        default=[],  # Empty by default - let system detect from content
        description="Dynamic list populated based on document analysis"
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
        data.pop('pinecone_api_key', None)
        return data
