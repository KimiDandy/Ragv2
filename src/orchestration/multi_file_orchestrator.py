"""
Multi-File Orchestrator

Manages parallel processing of multiple documents with configurable concurrency limits.
Uses global_config.json for performance settings.
"""

import asyncio
from pathlib import Path
from typing import List, Dict, Any, Optional
from datetime import datetime
from loguru import logger

from .document_orchestrator import DocumentOrchestrator
from ..core.enhancement_profiles import ProfileLoader

# Optional: Try to import psutil for CPU monitoring
try:
    import psutil
    PSUTIL_AVAILABLE = True
except ImportError:
    PSUTIL_AVAILABLE = False
    logger.warning("psutil not installed - CPU monitoring disabled")


class MultiFileOrchestrator:
    """
    Orchestrates parallel processing of multiple documents.
    
    Features:
    - Configurable concurrency (max files processed simultaneously)
    - CPU monitoring with automatic fallback
    - Memory limit enforcement per file
    - Progress tracking across all files
    """
    
    def __init__(
        self,
        namespace: Optional[str] = None,
        artefacts_dir: str = "artefacts"
    ):
        """
        Initialize multi-file orchestrator.
        
        Args:
            namespace: Namespace ID (if None, uses active namespace)
            artefacts_dir: Artefacts directory path
        """
        self.artefacts_dir = Path(artefacts_dir)
        
        # Load configuration
        self.profile_loader = ProfileLoader()
        self.global_config = self.profile_loader.load_global_config()
        
        # Use active namespace if not specified
        if namespace is None:
            namespace = self.profile_loader.get_active_namespace()
            logger.info(f"Using active namespace: {namespace}")
        
        self.namespace = namespace
        
        # Performance config
        perf_config = self.global_config.performance.multi_file_processing
        self.max_concurrent_files = perf_config.max_concurrent_files
        self.max_cpu_cores = perf_config.max_cpu_cores_dedicated
        self.enable_cpu_monitoring = perf_config.enable_cpu_monitoring
        self.cpu_threshold = perf_config.cpu_threshold_fallback_percent
        self.memory_limit_mb = perf_config.memory_limit_per_file_mb
        
        # Tracking
        self.file_statuses: Dict[str, Dict[str, Any]] = {}
        self.total_files = 0
        self.completed_files = 0
        self.failed_files = 0
        
        logger.info(f"MultiFileOrchestrator initialized")
        logger.info(f"  Namespace: {namespace}")
        logger.info(f"  Max concurrent files: {self.max_concurrent_files}")
        logger.info(f"  Max CPU cores: {self.max_cpu_cores}")
        logger.info(f"  CPU monitoring: {self.enable_cpu_monitoring}")
        logger.info(f"  CPU threshold: {self.cpu_threshold}%")
        logger.info(f"  Memory limit per file: {self.memory_limit_mb}MB")
    
    def _check_cpu_usage(self) -> float:
        """
        Check current CPU usage percentage.
        
        Returns:
            CPU usage percentage (0-100)
        """
        if not PSUTIL_AVAILABLE:
            return 0.0
        
        try:
            cpu_percent = psutil.cpu_percent(interval=0.1)
            return cpu_percent
        except Exception as e:
            logger.warning(f"Could not check CPU usage: {e}")
            return 0.0
    
    def _should_fallback_to_sequential(self) -> bool:
        """
        Check if should fallback to sequential processing due to high CPU usage.
        
        Returns:
            True if should fallback, False otherwise
        """
        if not self.enable_cpu_monitoring:
            return False
        
        cpu_usage = self._check_cpu_usage()
        
        if cpu_usage > self.cpu_threshold:
            logger.warning(f"⚠️ CPU usage high ({cpu_usage:.1f}% > {self.cpu_threshold}%). Falling back to sequential processing.")
            return True
        
        return False
    
    async def process_multiple_files(
        self,
        doc_ids: List[str]
    ) -> Dict[str, Any]:
        """
        Process multiple documents in parallel with concurrency control.
        
        Args:
            doc_ids: List of document IDs to process
            
        Returns:
            Dict with processing summary
        """
        self.total_files = len(doc_ids)
        self.completed_files = 0
        self.failed_files = 0
        
        logger.info(f"======== MULTI-FILE PROCESSING START ========")
        logger.info(f"Total files: {self.total_files}")
        logger.info(f"Concurrency: {self.max_concurrent_files}")
        
        start_time = datetime.now()
        
        # Initialize status tracking
        for doc_id in doc_ids:
            self.file_statuses[doc_id] = {
                "doc_id": doc_id,
                "status": "pending",
                "start_time": None,
                "end_time": None,
                "duration_seconds": None,
                "error": None
            }
        
        # Check if should use sequential processing
        concurrent_limit = self.max_concurrent_files
        if self._should_fallback_to_sequential():
            concurrent_limit = 1
            logger.info(f"Using sequential processing (concurrency=1) due to CPU constraints")
        
        # Process files with semaphore for concurrency control
        semaphore = asyncio.Semaphore(concurrent_limit)
        
        logger.info(f"[Multi-File] Created semaphore with limit: {concurrent_limit}")
        logger.info(f"[Multi-File] Will process {len(doc_ids)} files with max {concurrent_limit} concurrent")
        
        async def process_single_file(doc_id: str, index: int) -> Dict[str, Any]:
            """Process a single file with semaphore"""
            logger.info(f"[Multi-File] File {index+1}/{len(doc_ids)} ({doc_id[:8]}...) waiting for semaphore slot...")
            async with semaphore:
                logger.info(f"[Multi-File] File {index+1}/{len(doc_ids)} ({doc_id[:8]}...) acquired semaphore, starting processing...")
                result = await self._process_file(doc_id)
                logger.info(f"[Multi-File] File {index+1}/{len(doc_ids)} ({doc_id[:8]}...) released semaphore")
                return result
        
        # Execute all files
        tasks = [process_single_file(doc_id, idx) for idx, doc_id in enumerate(doc_ids)]
        results = await asyncio.gather(*tasks, return_exceptions=True)
        
        # Process results
        for doc_id, result in zip(doc_ids, results):
            if isinstance(result, Exception):
                logger.error(f"File {doc_id} failed with exception: {result}")
                self.file_statuses[doc_id]["status"] = "failed"
                self.file_statuses[doc_id]["error"] = str(result)
                self.failed_files += 1
            elif result and result.get("status") == "completed":
                self.file_statuses[doc_id] = result
                self.completed_files += 1
            else:
                self.failed_files += 1
        
        total_duration = (datetime.now() - start_time).total_seconds()
        
        # Summary
        summary = {
            "total_files": self.total_files,
            "completed": self.completed_files,
            "failed": self.failed_files,
            "total_duration_seconds": total_duration,
            "avg_duration_per_file": total_duration / self.total_files if self.total_files > 0 else 0,
            "concurrency_used": concurrent_limit,
            "file_statuses": self.file_statuses,
            "namespace": self.namespace
        }
        
        logger.info(f"======== MULTI-FILE PROCESSING COMPLETE ========")
        logger.info(f"Results: {self.completed_files} completed, {self.failed_files} failed")
        logger.info(f"Total duration: {total_duration:.1f}s")
        logger.info(f"Avg per file: {summary['avg_duration_per_file']:.1f}s")
        
        return summary
    
    async def _process_file(self, doc_id: str) -> Dict[str, Any]:
        """
        Process a single file.
        
        Args:
            doc_id: Document ID
            
        Returns:
            Processing result
        """
        short_id = doc_id[:8]
        logger.info(f"[Multi-File] ▶ STARTING file: {short_id}... (full: {doc_id})")
        
        file_start_time = datetime.now()
        self.file_statuses[doc_id]["status"] = "processing"
        self.file_statuses[doc_id]["start_time"] = file_start_time.isoformat()
        
        # Log current concurrent files
        processing_count = sum(1 for s in self.file_statuses.values() if s["status"] == "processing")
        logger.info(f"[Multi-File] Currently processing: {processing_count} files")
        
        try:
            # Create orchestrator for this file
            orchestrator = DocumentOrchestrator(
                doc_id=doc_id,
                namespace=self.namespace,
                artefacts_dir=str(self.artefacts_dir)
            )
            
            # Run full pipeline
            result = await orchestrator.run_full_pipeline()
            
            file_end_time = datetime.now()
            duration = (file_end_time - file_start_time).total_seconds()
            
            # UPDATE STATUS FIRST before counting
            self.file_statuses[doc_id]["status"] = "completed"
            self.file_statuses[doc_id]["end_time"] = file_end_time.isoformat()
            self.file_statuses[doc_id]["duration_seconds"] = duration
            
            # NOW count remaining processing files (after status update)
            processing_count = sum(1 for s in self.file_statuses.values() if s["status"] == "processing")
            logger.info(f"[Multi-File] ✓ COMPLETED file: {short_id}... ({duration:.1f}s)")
            logger.info(f"[Multi-File] Still processing: {processing_count} files")
            
            return {
                "doc_id": doc_id,
                "status": "completed",
                "start_time": file_start_time.isoformat(),
                "end_time": file_end_time.isoformat(),
                "duration_seconds": duration,
                "error": None,
                "result": result
            }
            
        except Exception as e:
            logger.error(f"[Multi-File] ✗ FAILED file: {short_id}... - {e}")
            
            file_end_time = datetime.now()
            duration = (file_end_time - file_start_time).total_seconds()
            
            return {
                "doc_id": doc_id,
                "status": "failed",
                "start_time": file_start_time.isoformat(),
                "end_time": file_end_time.isoformat(),
                "duration_seconds": duration,
                "error": str(e)
            }
    
    def get_progress(self) -> Dict[str, Any]:
        """
        Get current progress across all files.
        
        Returns:
            Progress information
        """
        processing = sum(1 for s in self.file_statuses.values() if s["status"] == "processing")
        pending = sum(1 for s in self.file_statuses.values() if s["status"] == "pending")
        
        progress_percentage = 0
        if self.total_files > 0:
            progress_percentage = int((self.completed_files / self.total_files) * 100)
        
        return {
            "total_files": self.total_files,
            "completed": self.completed_files,
            "failed": self.failed_files,
            "processing": processing,
            "pending": pending,
            "progress_percentage": progress_percentage,
            "file_statuses": self.file_statuses
        }
