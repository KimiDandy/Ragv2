"""
Document Orchestrator

Automated end-to-end document processing pipeline that coordinates:
1. OCR & Conversion (PDF → Markdown)
2. Enhancement (with client-specific types)
3. Auto-approval (all enhancements approved automatically)
4. Synthesis (create final enhanced markdown)
5. Vectorization (store in Pinecone)

This orchestrator eliminates all manual steps and runs entirely in the background.
"""

import asyncio
from pathlib import Path
from typing import Optional, Dict, Any, List
from datetime import datetime
from loguru import logger

from ..core.enhancement_profiles import ProfileLoader
from ..core.enhancement_profiles.models import ClientProfile, GlobalConfig


class ProcessingState:
    """
    Track processing state for resumability and detailed progress monitoring
    
    Stages:
    - uploaded: Document uploaded
    - ocr_in_progress: OCR and conversion running
    - ocr_completed: Markdown generated
    - enhancement_in_progress: Enhancement running
    - enhancement_completed: Enhancements generated
    - auto_approval_completed: All enhancements auto-approved
    - synthesis_in_progress: Synthesis running
    - synthesis_completed: Final markdown created
    - vectorization_in_progress: Vectorization running
    - vectorization_completed: Stored in Pinecone
    - ready: Fully processed and ready for chat
    """
    
    STAGES = [
        "uploaded",
        "ocr_in_progress",
        "ocr_completed",
        "enhancement_in_progress",
        "enhancement_completed",
        "auto_approval_completed",
        "synthesis_in_progress",
        "synthesis_completed",
        "vectorization_in_progress",
        "vectorization_completed",
        "ready"
    ]
    
    def __init__(self, doc_id: str, artefacts_dir: Path):
        """Initialize processing state"""
        self.doc_id = doc_id
        self.artefacts_dir = artefacts_dir
        self.state_file = artefacts_dir / doc_id / "processing_state.json"
        
        self.current_stage = "uploaded"
        self.stage_timestamps = {}
        self.stage_durations = {}
        self.errors = []
        self.metadata = {}
        
        # ETA tracking
        self.stage_progress = {}  # Per-stage progress details
        self.estimated_remaining_seconds = None
        self.current_stage_start_time = None
    
    def set_stage(self, stage: str, metadata: Optional[Dict] = None):
        """
        Set current processing stage
        
        Args:
            stage: Stage name (must be in STAGES)
            metadata: Optional metadata for this stage
        """
        if stage not in self.STAGES:
            raise ValueError(f"Invalid stage: {stage}. Must be one of {self.STAGES}")
        
        prev_stage = self.current_stage
        self.current_stage = stage
        self.stage_timestamps[stage] = datetime.now().isoformat()
        self.current_stage_start_time = datetime.now()
        
        # Calculate duration from previous stage
        if prev_stage in self.stage_timestamps:
            prev_time = datetime.fromisoformat(self.stage_timestamps[prev_stage])
            curr_time = datetime.fromisoformat(self.stage_timestamps[stage])
            duration = (curr_time - prev_time).total_seconds()
            self.stage_durations[f"{prev_stage}_to_{stage}"] = duration
        
        if metadata:
            self.metadata[stage] = metadata
        
        # Update ETA
        self._update_eta()
        
        self.save()
        logger.info(f"[{self.doc_id}] Stage: {prev_stage} → {stage}")
    
    def update_stage_progress(self, stage: str, progress_data: Dict[str, Any]):
        """
        Update progress within current stage (for detailed tracking).
        
        Args:
            stage: Stage name
            progress_data: Progress details (e.g., {"current_window": 2, "total_windows": 5, "percentage": 40})
        """
        self.stage_progress[stage] = {
            **progress_data,
            "updated_at": datetime.now().isoformat()
        }
        
        # Update ETA
        self._update_eta()
        
        self.save()
    
    def _update_eta(self):
        """
        Calculate estimated time remaining based on stage progress and historical durations.
        """
        try:
            current_index = self.STAGES.index(self.current_stage)
            total_stages = len(self.STAGES)
            
            # Calculate average time per stage from completed stages
            completed_durations = []
            for key, duration in self.stage_durations.items():
                if "_to_" in key:
                    completed_durations.append(duration)
            
            if completed_durations:
                avg_stage_duration = sum(completed_durations) / len(completed_durations)
            else:
                # Estimate based on typical durations
                avg_stage_duration = 60  # Default 60 seconds per stage
            
            # Remaining stages
            remaining_stages = total_stages - current_index - 1
            
            # Current stage progress (if available)
            current_stage_progress = self.stage_progress.get(self.current_stage, {})
            current_stage_percentage = current_stage_progress.get("percentage", 0)
            
            # Time remaining in current stage
            if self.current_stage_start_time:
                elapsed_in_current = (datetime.now() - self.current_stage_start_time).total_seconds()
                if current_stage_percentage > 0:
                    estimated_current_duration = (elapsed_in_current / current_stage_percentage) * 100
                    remaining_in_current = estimated_current_duration - elapsed_in_current
                else:
                    remaining_in_current = avg_stage_duration
            else:
                remaining_in_current = avg_stage_duration
            
            # Total ETA
            self.estimated_remaining_seconds = int(remaining_in_current + (remaining_stages * avg_stage_duration))
            
        except Exception as e:
            logger.warning(f"Could not calculate ETA: {e}")
            self.estimated_remaining_seconds = None
    
    def add_error(self, stage: str, error: str):
        """Add error for a stage"""
        self.errors.append({
            "stage": stage,
            "error": error,
            "timestamp": datetime.now().isoformat()
        })
        self.save()
    
    def get_progress_percentage(self) -> int:
        """Get progress percentage (0-100)"""
        if self.current_stage not in self.STAGES:
            return 0
        
        current_index = self.STAGES.index(self.current_stage)
        total_stages = len(self.STAGES)
        
        return int((current_index / total_stages) * 100)
    
    def is_complete(self) -> bool:
        """Check if processing is complete"""
        return self.current_stage == "ready"
    
    def save(self):
        """Save state to JSON file"""
        import json
        
        self.state_file.parent.mkdir(parents=True, exist_ok=True)
        
        state_data = {
            "doc_id": self.doc_id,
            "current_stage": self.current_stage,
            "progress_percentage": self.get_progress_percentage(),
            "is_complete": self.is_complete(),
            "stage_timestamps": self.stage_timestamps,
            "stage_durations": self.stage_durations,
            "errors": self.errors,
            "metadata": self.metadata,
            "stage_progress": self.stage_progress,
            "estimated_remaining_seconds": self.estimated_remaining_seconds,
            "last_updated": datetime.now().isoformat()
        }
        
        with open(self.state_file, 'w', encoding='utf-8') as f:
            json.dump(state_data, f, indent=2, ensure_ascii=False)
    
    @classmethod
    def load(cls, doc_id: str, artefacts_dir: Path) -> 'ProcessingState':
        """Load existing state from file"""
        import json
        
        state = cls(doc_id, artefacts_dir)
        
        if state.state_file.exists():
            with open(state.state_file, 'r', encoding='utf-8') as f:
                data = json.load(f)
            
            state.current_stage = data.get("current_stage", "uploaded")
            state.stage_timestamps = data.get("stage_timestamps", {})
            state.stage_durations = data.get("stage_durations", {})
            state.errors = data.get("errors", [])
            state.metadata = data.get("metadata", {})
            state.stage_progress = data.get("stage_progress", {})
            state.estimated_remaining_seconds = data.get("estimated_remaining_seconds")
        
        return state


class DocumentOrchestrator:
    """
    Automated document processing orchestrator
    
    This orchestrator replaces manual UI-driven workflow with fully automated
    background processing using client-specific configuration profiles.
    """
    
    def __init__(
        self,
        doc_id: str,
        namespace: Optional[str] = None,
        artefacts_dir: str = "artefacts"
    ):
        """
        Initialize orchestrator
        
        Args:
            doc_id: Document identifier
            namespace: Namespace ID (if None, uses active namespace)
            artefacts_dir: Artefacts directory path
        """
        self.doc_id = doc_id
        self.artefacts_dir = Path(artefacts_dir)
        
        # Load profiles
        self.profile_loader = ProfileLoader()
        
        # Use active namespace if not specified
        if namespace is None:
            namespace = self.profile_loader.get_active_namespace()
            logger.info(f"Using active namespace: {namespace}")
        
        self.namespace = namespace
        
        # Load client profile and global config
        self.client_profile: ClientProfile = self.profile_loader.get_profile_for_namespace(namespace)
        self.global_config: GlobalConfig = self.profile_loader.load_global_config()
        
        # Initialize state tracker
        self.state = ProcessingState(doc_id, self.artefacts_dir)
        
        logger.info(f"[{doc_id[:8]}...] Init: {self.client_profile.client_name} | NS: {namespace} | {len(self.client_profile.get_enabled_types())} types")
    
    async def run_full_pipeline(self) -> Dict[str, Any]:
        """
        Run complete automated processing pipeline
        
        Returns:
            Dictionary with processing results and statistics
            
        Raises:
            Exception: If any critical error occurs during processing
        """
        try:
            logger.info(f"[{self.doc_id[:8]}...] ======== PIPELINE START ========")
            
            # Stage 1: OCR & Conversion
            await self._step_1_ocr_conversion()
            
            # Stage 2: Enhancement
            await self._step_2_enhancement()
            
            # Stage 3: Auto-approval (immediate, no user interaction)
            await self._step_3_auto_approval()
            
            # Stage 4: Synthesis
            await self._step_4_synthesis()
            
            # Stage 5: Vectorization
            await self._step_5_vectorization()
            
            # Mark as ready
            self.state.set_stage("ready", {
                "completed_at": datetime.now().isoformat()
            })
            
            logger.info(f"[{self.doc_id[:8]}...] ======== COMPLETE ========")
            
            return self._get_processing_summary()
            
        except Exception as e:
            logger.error(f"[{self.doc_id}] Pipeline failed: {e}", exc_info=True)
            self.state.add_error(self.state.current_stage, str(e))
            raise
    
    async def _step_1_ocr_conversion(self):
        """
        Step 1: OCR and Markdown Conversion
        
        Uses Tesseract OCR (local) to extract text and convert to markdown.
        No user interaction needed - runs automatically.
        """
        self.state.set_stage("ocr_in_progress")
        
        try:
            # Import extraction module
            from ..extraction.extractor import extract_pdf_to_markdown
            from ..shared.document_meta import get_original_pdf_filename
            
            # Paths
            pdf_path = self.artefacts_dir / self.doc_id / "source.pdf"
            output_dir = self.artefacts_dir / self.doc_id
            
            if not pdf_path.exists():
                raise FileNotFoundError(f"Source PDF not found: {pdf_path}")
            
            # Get original filename
            original_filename = get_original_pdf_filename(output_dir) or "document.pdf"
            
            # Run extraction with OCR settings from global config
            result = await asyncio.to_thread(
                extract_pdf_to_markdown,
                doc_id=self.doc_id,
                pdf_path=str(pdf_path),
                out_dir=str(self.artefacts_dir),
                original_filename=original_filename,
                ocr_primary_psm=3,  # Use standard layout PSM
                ocr_fallback_psm=[6, 11]
            )
            
            # Get markdown path
            from ..shared.document_meta import get_markdown_path
            markdown_path = get_markdown_path(output_dir, "v1")
            
            # Load page count from metrics
            import json
            total_pages = 0
            try:
                metrics_file = output_dir / "metrics.json"
                if metrics_file.exists():
                    with open(metrics_file, 'r', encoding='utf-8') as f:
                        metrics = json.load(f)
                    total_pages = metrics.get('total_pages', 0)
            except Exception as e:
                logger.warning(f"Could not load metrics: {e}")
            
            self.state.set_stage("ocr_completed", {
                "total_pages": total_pages,
                "markdown_path": str(markdown_path)
            })
            
            logger.info(f"[{self.doc_id[:8]}...] ✓ OCR → {total_pages}p")
            
        except Exception as e:
            logger.error(f"OCR conversion failed: {e}")
            self.state.add_error("ocr_in_progress", str(e))
            raise
    
    async def _step_2_enhancement(self):
        """
        Step 2: Enhancement
        
        Generate enhancements based on client profile configuration.
        Uses enabled enhancement types from client profile.
        """
        self.state.set_stage("enhancement_in_progress")
        
        try:
            from ..enhancement.enhancer import DirectEnhancerV2
            from ..enhancement.config import EnhancementConfig
            import json
            
            # Get markdown path from state
            markdown_path = self.state.metadata.get("ocr_completed", {}).get("markdown_path")
            if not markdown_path:
                raise ValueError("Markdown path not found in state")
            
            # Load units metadata and tables
            doc_dir = self.artefacts_dir / self.doc_id
            
            # Try both locations for units_metadata
            units_meta_path = doc_dir / "units_metadata.json"
            if not units_meta_path.exists():
                units_meta_path = doc_dir / "meta" / "units_metadata.json"
            
            tables_path = doc_dir / "tables.json"
            
            # Load units metadata as list (not dict)
            units_metadata_list = []
            if units_meta_path.exists():
                with open(units_meta_path, 'r', encoding='utf-8') as f:
                    units_metadata_list = json.load(f)
            else:
                logger.warning(f"Units metadata not found at {units_meta_path}")
            
            # Convert list to dict for enhancer (it expects dict format)
            units_metadata = {
                "units": units_metadata_list,
                "total": len(units_metadata_list)
            }
            
            tables_data = []
            if tables_path.exists():
                with open(tables_path, 'r', encoding='utf-8') as f:
                    tables_data = json.load(f)
            
            # Get enabled types from client profile
            enabled_types = self.client_profile.get_enabled_types()
            domain = self.client_profile.get_primary_domain()
            
            # Initialize enhancer
            config = EnhancementConfig()
            enhancer = DirectEnhancerV2(config)
            
            # Run enhancement
            enhancements = await enhancer.enhance_document(
                doc_id=self.doc_id,
                markdown_path=markdown_path,
                units_metadata=units_metadata,
                tables_data=tables_data,
                selected_types=enabled_types,
                domain_hint=domain,
                custom_instructions=None
            )
            
            # Save enhancements
            enhancements_json = [enh.dict() for enh in enhancements]
            enhancements_file = doc_dir / "enhancements.json"
            with open(enhancements_file, 'w', encoding='utf-8') as f:
                json.dump(enhancements_json, f, ensure_ascii=False, indent=2)
            
            # Calculate type distribution for verification
            from collections import Counter
            type_distribution = Counter(enh.enhancement_type for enh in enhancements)
            
            self.state.set_stage("enhancement_completed", {
                "total_enhancements": len(enhancements),
                "enhancements_file": str(enhancements_file),
                "type_distribution": dict(type_distribution)
            })
            
            type_summary = ", ".join([f"{k}:{v}" for k, v in sorted(type_distribution.items())])
            logger.info(f"[{self.doc_id[:8]}...] ✓ Enhancement → {len(enhancements)} items ({type_summary})")
            
        except Exception as e:
            logger.error(f"Enhancement failed: {e}", exc_info=True)
            self.state.add_error("enhancement_in_progress", str(e))
            raise
    
    async def _step_3_auto_approval(self):
        """
        Step 3: Auto-approval
        
        Automatically approve all enhancements without user review.
        This is the default behavior from global config.
        """
        # Auto-approval is immediate - no processing needed
        # All enhancements generated in step 2 are already considered approved
        
        self.state.set_stage("auto_approval_completed", {
            "auto_approve_all": self.global_config.enhancement.auto_approve_all,
            "approval_timestamp": datetime.now().isoformat()
        })
    
    async def _step_4_synthesis(self):
        """
        Step 4: Synthesis
        
        Create final enhanced markdown with footnotes and metadata.
        """
        self.state.set_stage("synthesis_in_progress")
        
        try:
            from ..synthesis.synthesizer import synthesize_final_markdown
            
            # Load approved enhancements
            doc_dir = self.artefacts_dir / self.doc_id
            enhancements_file = doc_dir / "enhancements.json"
            
            curated_suggestions = []
            if enhancements_file.exists():
                import json
                with open(enhancements_file, 'r', encoding='utf-8') as f:
                    enhancements = json.load(f)
                
                # Convert to curated format (all auto-approved)
                curated_suggestions = [
                    {
                        **enh,
                        "status": "approved"
                    }
                    for enh in enhancements
                ]
            
            # Run synthesis
            result_path = await asyncio.to_thread(
                synthesize_final_markdown,
                str(doc_dir),
                curated_suggestions
            )
            
            self.state.set_stage("synthesis_completed", {
                "final_markdown_path": result_path,
                "total_enhancements": len(curated_suggestions)
            })
            
            logger.info(f"[{self.doc_id[:8]}...] ✓ Synthesis")
            
        except Exception as e:
            logger.error(f"Synthesis failed: {e}")
            self.state.add_error("synthesis_in_progress", str(e))
            raise
    
    async def _step_5_vectorization(self):
        """
        Step 5: Vectorization
        
        Store enhanced document in Pinecone vector database.
        Uses namespace and embedding settings from global config.
        """
        self.state.set_stage("vectorization_in_progress")
        
        try:
            from ..vectorization.vectorizer import vectorize_and_store
            from ..shared.document_meta import get_markdown_relative_path
            from pinecone import Pinecone
            from langchain_openai import OpenAIEmbeddings
            import os
            
            # Initialize Pinecone and embeddings
            pc = Pinecone(api_key=os.getenv("PINECONE_API_KEY"))
            pinecone_index = pc.Index(self.global_config.vectorstore.pinecone_index)
            embeddings = OpenAIEmbeddings(model=self.global_config.embedding.model)
            
            doc_dir = self.artefacts_dir / self.doc_id
            
            # Vectorize both v1 and v2 if enabled
            results = {"v1": False, "v2": False}
            
            # Vectorize v1 (original)
            if self.global_config.vectorstore.vector_version_v1_enabled:
                v1_rel = get_markdown_relative_path(doc_dir, "v1")
                results["v1"] = await asyncio.to_thread(
                    vectorize_and_store,
                    str(doc_dir),
                    pinecone_index,
                    v1_rel,
                    "v1",
                    embeddings=embeddings,
                    namespace=self.namespace
                )
            
            # Vectorize v2 (enhanced)
            if self.global_config.vectorstore.vector_version_v2_enabled:
                v2_rel = get_markdown_relative_path(doc_dir, "v2")
                results["v2"] = await asyncio.to_thread(
                    vectorize_and_store,
                    str(doc_dir),
                    pinecone_index,
                    v2_rel,
                    "v2",
                    embeddings=embeddings,
                    namespace=self.namespace
                )
            
            self.state.set_stage("vectorization_completed", {
                "namespace": self.namespace,
                "v1_success": results["v1"],
                "v2_success": results["v2"],
                "embedding_model": self.global_config.embedding.model
            })
            
            logger.info(f"[{self.doc_id[:8]}...] ✓ Vectorization → NS:{self.namespace}")
            
        except Exception as e:
            logger.error(f"Vectorization failed: {e}")
            self.state.add_error("vectorization_in_progress", str(e))
            raise
    
    def _get_processing_summary(self) -> Dict[str, Any]:
        """Get summary of processing results"""
        total_duration = 0
        for duration in self.state.stage_durations.values():
            total_duration += duration
        
        return {
            "doc_id": self.doc_id,
            "namespace": self.namespace,
            "client": self.client_profile.client_name,
            "status": "completed",
            "progress": 100,
            "current_stage": self.state.current_stage,
            "total_duration_seconds": total_duration,
            "stage_timestamps": self.state.stage_timestamps,
            "stage_durations": self.state.stage_durations,
            "metadata": self.state.metadata,
            "errors": self.state.errors
        }
    
    def get_status(self) -> Dict[str, Any]:
        """
        Get current processing status
        
        Returns:
            Dictionary with current status and progress
        """
        return {
            "doc_id": self.doc_id,
            "namespace": self.namespace,
            "client": self.client_profile.client_name,
            "status": "completed" if self.state.is_complete() else "processing",
            "progress": self.state.get_progress_percentage(),
            "current_stage": self.state.current_stage,
            "stage_timestamps": self.state.stage_timestamps,
            "errors": self.state.errors,
            "last_updated": datetime.now().isoformat()
        }
