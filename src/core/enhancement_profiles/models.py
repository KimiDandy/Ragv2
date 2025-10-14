"""
Pydantic Models for Enhancement Profiles

This module defines the data models for client profiles and global configuration.
"""

from typing import Dict, List, Optional, Any
from pydantic import BaseModel, Field, validator


class ClientProfile(BaseModel):
    """
    Client-specific enhancement profile
    
    Defines which enhancement types are enabled for a specific client
    and their primary domain focus.
    """
    model_config = {"extra": "allow"}  # Allow extra fields like comments
    
    client_name: str = Field(..., description="Display name of the client")
    client_id: str = Field(..., description="Unique identifier for this client")
    description: str = Field(..., description="Description of this client profile")
    version: str = Field(default="1", description="Profile version")
    
    enhancement_types: Dict[str, Any] = Field(
        ...,
        description="Enhancement types with 0 (disabled) or 1 (enabled). Comment fields (starting with _) allowed for readability."
    )
    
    domains: Dict[str, Any] = Field(
        ...,
        description="Domain flags with 0 (disabled) or 1 (enabled). Comment fields (starting with _) allowed for readability."
    )
    
    @validator('enhancement_types', 'domains')
    def validate_binary_values(cls, v):
        """Ensure all non-comment values are 0 or 1"""
        for key, val in v.items():
            # Skip comment fields (starting with underscore)
            if key.startswith('_'):
                continue
            
            if val not in [0, 1]:
                raise ValueError(f"Field '{key}' must be 0 or 1, got {val}")
        
        return v
    
    @validator('version')
    def validate_version_format(cls, v):
        """Ensure version is simple integer string"""
        if not v.isdigit():
            raise ValueError(f"Version must be simple integer string (e.g., '1', '2'), got '{v}'")
        return v
    
    def get_enabled_types(self) -> List[str]:
        """
        Get list of enabled enhancement types
        
        Returns:
            List of type IDs where value=1 (excluding comment fields)
        """
        return [
            type_id 
            for type_id, enabled in self.enhancement_types.items()
            if not type_id.startswith('_') and enabled == 1
        ]
    
    def get_primary_domain(self) -> str:
        """
        Get the primary enabled domain
        
        Returns:
            Domain ID where value=1, or 'other' if none/multiple enabled
        """
        enabled_domains = [
            domain_id 
            for domain_id, enabled in self.domains.items()
            if not domain_id.startswith('_') and enabled == 1
        ]
        
        if len(enabled_domains) == 1:
            return enabled_domains[0]
        elif len(enabled_domains) > 1:
            # Multiple domains enabled - return first one
            return enabled_domains[0]
        else:
            # No domain enabled - return default
            return 'other'
    
    def is_type_enabled(self, type_id: str) -> bool:
        """Check if a specific enhancement type is enabled"""
        return self.enhancement_types.get(type_id, 0) == 1
    
    def get_enabled_count(self) -> int:
        """Get count of enabled enhancement types"""
        return len(self.get_enabled_types())


class LLMConfig(BaseModel):
    """LLM configuration section"""
    model: str = Field(description="LLM model name (e.g., 'gpt-4.1')")
    max_tokens: int = Field(description="Maximum tokens for generation", gt=0)
    temperature: float = Field(description="Sampling temperature", ge=0.0, le=2.0)
    window_size: int = Field(description="Token window size for processing", gt=0)
    window_overlap_tokens: int = Field(default=1500, description="Token overlap between windows", ge=0)
    retry_attempts: int = Field(description="Number of retry attempts on failure", ge=1)
    timeout_seconds: int = Field(description="Request timeout in seconds", gt=0)
    requests_per_second: float = Field(default=2.0, description="API rate limit", gt=0)


class EmbeddingConfig(BaseModel):
    """Embedding configuration section"""
    model: str = Field(description="Embedding model name")
    dimension: int = Field(description="Embedding dimension", gt=0)
    batch_size: int = Field(description="Batch size for embedding", gt=0)


class VectorStoreConfig(BaseModel):
    """Vector store configuration section"""
    pinecone_index: str = Field(description="Pinecone index name")
    vector_version_v1_enabled: bool = Field(description="Enable v1 vectorization")
    vector_version_v2_enabled: bool = Field(description="Enable v2 vectorization")
    upload_batch_size: int = Field(default=100, description="Batch size for vector uploads", gt=0)
    upload_threads: int = Field(default=4, description="Number of parallel upload threads", ge=1)
    max_concurrent_batches: int = Field(default=2, description="Max concurrent batch uploads", ge=1)
    enable_async_upsert: bool = Field(default=True, description="Enable async Pinecone upserts")


class OCRConfig(BaseModel):
    """OCR configuration section"""
    engine: str = Field(description="OCR engine name")
    language: str = Field(description="OCR language settings")
    dpi: int = Field(description="DPI for OCR processing", gt=0)
    primary_psm: int = Field(default=3, description="Primary PSM mode for Tesseract")
    fallback_psm: List[int] = Field(default=[6, 11], description="Fallback PSM modes")


class EnhancementGlobalConfig(BaseModel):
    """Enhancement processing configuration"""
    auto_approve_all: bool = Field(description="Auto-approve all enhancements")
    parallel_windows: bool = Field(description="Enable parallel window processing")
    max_parallel_windows: int = Field(default=5, description="Max parallel windows per batch", ge=1)
    enable_pipeline_overlap: bool = Field(default=True, description="Enable enhancement-vectorization pipeline overlap")
    enable_server_calc: bool = Field(default=True, description="Enable server-side calculations")
    calc_precision: int = Field(default=2, description="Calculation precision (decimal places)", ge=0)


class SynthesisConfig(BaseModel):
    """Synthesis configuration section"""
    include_footnotes: bool = Field(description="Include footnotes in synthesis")
    include_metadata: bool = Field(description="Include metadata in synthesis")


class MultiFileProcessingConfig(BaseModel):
    """Multi-file processing configuration"""
    enabled: bool = Field(default=True, description="Enable multi-file parallel processing")
    max_concurrent_files: int = Field(default=2, description="Max files to process concurrently", ge=1)
    max_cpu_cores_dedicated: int = Field(default=4, description="Max CPU cores dedicated to processing", ge=1)
    enable_cpu_monitoring: bool = Field(default=True, description="Monitor CPU usage and fallback if needed")
    cpu_threshold_fallback_percent: int = Field(default=85, description="CPU % threshold to fallback to sequential", ge=1, le=100)
    memory_limit_per_file_mb: int = Field(default=1024, description="Memory limit per file in MB", gt=0)


class ProgressTrackingConfig(BaseModel):
    """Progress tracking and ETA configuration"""
    enable_eta_estimation: bool = Field(default=True, description="Enable ETA calculation")
    update_interval_seconds: int = Field(default=2, description="Progress update interval in seconds", ge=1)
    show_stage_details: bool = Field(default=True, description="Show detailed stage information")
    calculate_avg_timing: bool = Field(default=True, description="Calculate average timing for ETA")
    show_percentage: bool = Field(default=True, description="Show percentage completion")


class PipelineOptimizationConfig(BaseModel):
    """Pipeline optimization configuration"""
    enable_enhancement_vectorization_overlap: bool = Field(default=True, description="Enable overlap between enhancement and vectorization")
    vectorization_batch_queue_size: int = Field(default=3, description="Queue size for vectorization batches", ge=1)
    enable_parallel_vectorization: bool = Field(default=True, description="Enable parallel vectorization within batches")


class PerformanceConfig(BaseModel):
    """Performance optimization configuration"""
    multi_file_processing: MultiFileProcessingConfig = Field(description="Multi-file processing settings")
    progress_tracking: ProgressTrackingConfig = Field(description="Progress tracking settings")
    pipeline_optimization: PipelineOptimizationConfig = Field(description="Pipeline optimization settings")


class GlobalConfig(BaseModel):
    """
    Global configuration applied to all clients
    
    Contains LLM settings, embedding settings, vectorstore settings, OCR settings,
    and other global parameters that are shared across all clients.
    """
    config_name: str = Field(description="Configuration name")
    config_version: str = Field(description="Configuration version")
    description: str = Field(description="Configuration description")
    
    llm: LLMConfig = Field(description="LLM configuration")
    embedding: EmbeddingConfig = Field(description="Embedding configuration")
    vectorstore: VectorStoreConfig = Field(description="Vector store configuration")
    ocr: OCRConfig = Field(description="OCR configuration")
    enhancement: EnhancementGlobalConfig = Field(description="Enhancement configuration")
    synthesis: SynthesisConfig = Field(description="Synthesis configuration")
    performance: PerformanceConfig = Field(description="Performance optimization configuration")
    
    class Config:
        """Pydantic config"""
        extra = 'allow'  # Allow extra fields like _notes, _version_changes


class ActiveConfig(BaseModel):
    """
    Active namespace configuration
    
    Determines which namespace/client profile is currently active.
    """
    active_namespace: str = Field(description="Currently active namespace ID")
    last_updated: Optional[str] = Field(default=None, description="Last update timestamp")
    updated_by: Optional[str] = Field(default=None, description="Last updated by")
    
    class Config:
        """Pydantic config"""
        extra = 'allow'  # Allow extra fields like _instructions
