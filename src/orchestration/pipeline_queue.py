"""
Pipeline Queue for Overlapping Enhancement and Vectorization

Allows vectorization to start on completed enhancement batches
while next batches are still being enhanced.
"""

import asyncio
from typing import Dict, Any, List, Optional
from pathlib import Path
from datetime import datetime
from loguru import logger


class PipelineQueue:
    """
    Queue for managing overlap between enhancement and vectorization stages.
    
    Flow:
    - Enhancement produces batches → Queue
    - Vectorization consumes from queue in parallel
    - Both stages run concurrently
    """
    
    def __init__(self, doc_id: str, max_queue_size: int = 3):
        """
        Initialize pipeline queue.
        
        Args:
            doc_id: Document ID
            max_queue_size: Maximum batches in queue
        """
        self.doc_id = doc_id
        self.max_queue_size = max_queue_size
        self.queue: asyncio.Queue = asyncio.Queue(maxsize=max_queue_size)
        
        self.enhancement_complete = False
        self.vectorization_complete = False
        
        self.stats = {
            "batches_produced": 0,
            "batches_consumed": 0,
            "total_enhancements": 0,
            "total_chunks_vectorized": 0
        }
        
        logger.info(f"[Pipeline Queue] Initialized for doc: {doc_id} (max queue size: {max_queue_size})")
    
    async def produce_batch(self, batch_data: Dict[str, Any]):
        """
        Producer: Enhancement puts completed batch into queue.
        
        Args:
            batch_data: Batch information with enhancements
        """
        batch_num = batch_data.get("batch_num", self.stats["batches_produced"] + 1)
        num_enhancements = len(batch_data.get("enhancements", []))
        
        logger.info(f"[Pipeline Queue] Producing batch {batch_num} ({num_enhancements} enhancements)")
        
        await self.queue.put(batch_data)
        
        self.stats["batches_produced"] += 1
        self.stats["total_enhancements"] += num_enhancements
        
        logger.debug(f"[Pipeline Queue] Queue size: {self.queue.qsize()}/{self.max_queue_size}")
    
    async def consume_batch(self) -> Optional[Dict[str, Any]]:
        """
        Consumer: Vectorization gets batch from queue.
        
        Returns:
            Batch data or None if enhancement is complete and queue is empty
        """
        try:
            # Try to get batch with timeout
            batch_data = await asyncio.wait_for(self.queue.get(), timeout=2.0)
            
            batch_num = batch_data.get("batch_num", "unknown")
            logger.info(f"[Pipeline Queue] Consuming batch {batch_num}")
            
            self.stats["batches_consumed"] += 1
            
            return batch_data
            
        except asyncio.TimeoutError:
            # If enhancement is complete and queue is empty, we're done
            if self.enhancement_complete and self.queue.empty():
                logger.info(f"[Pipeline Queue] All batches consumed (enhancement complete, queue empty)")
                return None
            
            # Otherwise, keep waiting
            return await self.consume_batch()
    
    def mark_enhancement_complete(self):
        """Mark that enhancement stage is complete"""
        self.enhancement_complete = True
        logger.info(f"[Pipeline Queue] Enhancement marked complete ({self.stats['batches_produced']} batches produced)")
    
    def mark_vectorization_complete(self):
        """Mark that vectorization stage is complete"""
        self.vectorization_complete = True
        logger.info(f"[Pipeline Queue] Vectorization marked complete ({self.stats['batches_consumed']} batches consumed)")
    
    def get_stats(self) -> Dict[str, Any]:
        """Get pipeline statistics"""
        return {
            **self.stats,
            "enhancement_complete": self.enhancement_complete,
            "vectorization_complete": self.vectorization_complete,
            "queue_size": self.queue.qsize(),
            "is_complete": self.enhancement_complete and self.vectorization_complete
        }


async def run_pipeline_with_overlap(
    enhancement_task: callable,
    vectorization_task: callable,
    pipeline_queue: PipelineQueue
) -> Dict[str, Any]:
    """
    Run enhancement and vectorization with pipeline overlap.
    
    Args:
        enhancement_task: Async function that produces batches
        vectorization_task: Async function that consumes batches
        pipeline_queue: Pipeline queue instance
        
    Returns:
        Results from both stages
    """
    logger.info(f"[Pipeline Overlap] Starting overlapped pipeline")
    start_time = datetime.now()
    
    # Run both tasks concurrently
    enhancement_result, vectorization_result = await asyncio.gather(
        enhancement_task(pipeline_queue),
        vectorization_task(pipeline_queue),
        return_exceptions=True
    )
    
    duration = (datetime.now() - start_time).total_seconds()
    
    # Check for exceptions
    if isinstance(enhancement_result, Exception):
        logger.error(f"[Pipeline Overlap] Enhancement failed: {enhancement_result}")
        raise enhancement_result
    
    if isinstance(vectorization_result, Exception):
        logger.error(f"[Pipeline Overlap] Vectorization failed: {vectorization_result}")
        raise vectorization_result
    
    stats = pipeline_queue.get_stats()
    
    logger.info(f"[Pipeline Overlap] Complete in {duration:.1f}s")
    logger.info(f"[Pipeline Overlap] Batches processed: {stats['batches_consumed']}")
    logger.info(f"[Pipeline Overlap] Enhancements: {stats['total_enhancements']}")
    
    return {
        "duration_seconds": duration,
        "enhancement_result": enhancement_result,
        "vectorization_result": vectorization_result,
        "pipeline_stats": stats
    }
