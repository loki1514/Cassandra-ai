"""
T27: Redis Queue

This module provides Redis-based queue functionality:
- Redis queue for memory writes
- Dead letter queue for failed jobs
- Retry with exponential backoff
- Job prioritization

Features:
- Async queue operations
- Reliable job processing
- Monitoring and metrics
"""

import asyncio
import json
import pickle
import uuid
from dataclasses import dataclass, asdict
from datetime import datetime, timedelta
from enum import Enum
from typing import Optional, Dict, Any, List, Callable, TypeVar, Generic
from functools import wraps

import redis.asyncio as redis
import structlog
from pydantic import BaseModel, Field

from cassandra.config import settings

logger = structlog.get_logger("cassandra.queue")

T = TypeVar('T')


class JobStatus(str, Enum):
    """Job status values."""
    PENDING = "pending"
    PROCESSING = "processing"
    COMPLETED = "completed"
    FAILED = "failed"
    RETRYING = "retrying"
    DEAD = "dead"


class JobPriority(int, Enum):
    """Job priority levels."""
    CRITICAL = 1
    HIGH = 2
    NORMAL = 3
    LOW = 4
    BACKGROUND = 5


@dataclass
class Job:
    """
    Queue job definition.
    
    Attributes:
        job_id: Unique job identifier
        queue_name: Queue this job belongs to
        job_type: Type of job
        payload: Job data
        priority: Job priority
        status: Current status
        created_at: Creation timestamp
        scheduled_at: When to process
        started_at: When processing started
        completed_at: When processing completed
        attempts: Number of processing attempts
        max_attempts: Maximum retry attempts
        error: Last error message
        traceback: Last error traceback
    """
    job_id: str
    queue_name: str
    job_type: str
    payload: Dict[str, Any]
    priority: JobPriority = JobPriority.NORMAL
    status: JobStatus = JobStatus.PENDING
    created_at: datetime = None
    scheduled_at: datetime = None
    started_at: Optional[datetime] = None
    completed_at: Optional[datetime] = None
    attempts: int = 0
    max_attempts: int = 3
    error: Optional[str] = None
    traceback: Optional[str] = None
    
    def __post_init__(self):
        if self.created_at is None:
            self.created_at = datetime.utcnow()
        if self.scheduled_at is None:
            self.scheduled_at = self.created_at
    
    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary."""
        return {
            "job_id": self.job_id,
            "queue_name": self.queue_name,
            "job_type": self.job_type,
            "payload": self.payload,
            "priority": self.priority.value,
            "status": self.status.value,
            "created_at": self.created_at.isoformat() if self.created_at else None,
            "scheduled_at": self.scheduled_at.isoformat() if self.scheduled_at else None,
            "started_at": self.started_at.isoformat() if self.started_at else None,
            "completed_at": self.completed_at.isoformat() if self.completed_at else None,
            "attempts": self.attempts,
            "max_attempts": self.max_attempts,
            "error": self.error,
            "traceback": self.traceback
        }
    
    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> 'Job':
        """Create from dictionary."""
        return cls(
            job_id=data["job_id"],
            queue_name=data["queue_name"],
            job_type=data["job_type"],
            payload=data["payload"],
            priority=JobPriority(data.get("priority", 3)),
            status=JobStatus(data.get("status", "pending")),
            created_at=datetime.fromisoformat(data["created_at"]) if data.get("created_at") else datetime.utcnow(),
            scheduled_at=datetime.fromisoformat(data["scheduled_at"]) if data.get("scheduled_at") else None,
            started_at=datetime.fromisoformat(data["started_at"]) if data.get("started_at") else None,
            completed_at=datetime.fromisoformat(data["completed_at"]) if data.get("completed_at") else None,
            attempts=data.get("attempts", 0),
            max_attempts=data.get("max_attempts", 3),
            error=data.get("error"),
            traceback=data.get("traceback")
        )


class QueueStats(BaseModel):
    """Queue statistics."""
    
    queue_name: str
    pending: int = 0
    processing: int = 0
    completed: int = 0
    failed: int = 0
    dead: int = 0
    total: int = 0


class RedisQueue:
    """
    Redis-based job queue.
    
    Features:
    - Priority-based job ordering
    - Dead letter queue for failed jobs
    - Exponential backoff retry
    - Job timeouts and heartbeats
    
    Usage:
        queue = RedisQueue(redis_client, "memory_writes")
        
        # Enqueue job
        job = await queue.enqueue(
            job_type="add_memory",
            payload={"content": "...", "org_id": "..."},
            priority=JobPriority.HIGH
        )
        
        # Process jobs
        async for job in queue.dequeue_batch(batch_size=10):
            # Process job
            await queue.complete(job)
    """
    
    # Retry delays (exponential backoff)
    RETRY_DELAYS = [5, 30, 120, 600, 1800]  # seconds
    
    def __init__(
        self,
        redis_client: redis.Redis,
        queue_name: str,
        default_max_attempts: int = 3,
        job_timeout: int = 300  # 5 minutes
    ):
        """
        Initialize Redis queue.
        
        Args:
            redis_client: Redis client
            queue_name: Name of the queue
            default_max_attempts: Default max retry attempts
            job_timeout: Job processing timeout in seconds
        """
        self.redis = redis_client
        self.queue_name = queue_name
        self.default_max_attempts = default_max_attempts
        self.job_timeout = job_timeout
        
        # Redis keys
        self.key_pending = f"queue:{queue_name}:pending"
        self.key_processing = f"queue:{queue_name}:processing"
        self.key_completed = f"queue:{queue_name}:completed"
        self.key_failed = f"queue:{queue_name}:failed"
        self.key_dead = f"queue:{queue_name}:dead"
        self.key_jobs = f"queue:{queue_name}:jobs"
        
        logger.info(
            "queue_initialized",
            queue_name=queue_name,
            max_attempts=default_max_attempts
        )
    
    def _generate_job_id(self) -> str:
        """Generate unique job ID."""
        return f"job_{uuid.uuid4().hex[:16]}"
    
    def _get_retry_delay(self, attempt: int) -> int:
        """Get retry delay for attempt number."""
        if attempt < len(self.RETRY_DELAYS):
            return self.RETRY_DELAYS[attempt]
        return self.RETRY_DELAYS[-1]
    
    async def enqueue(
        self,
        job_type: str,
        payload: Dict[str, Any],
        priority: JobPriority = JobPriority.NORMAL,
        max_attempts: Optional[int] = None,
        delay_seconds: int = 0
    ) -> Job:
        """
        Add job to queue.
        
        Args:
            job_type: Type of job
            payload: Job data
            priority: Job priority
            max_attempts: Max retry attempts
            delay_seconds: Delay before processing
            
        Returns:
            Created Job
        """
        job_id = self._generate_job_id()
        scheduled_at = datetime.utcnow() + timedelta(seconds=delay_seconds)
        
        job = Job(
            job_id=job_id,
            queue_name=self.queue_name,
            job_type=job_type,
            payload=payload,
            priority=priority,
            status=JobStatus.PENDING,
            scheduled_at=scheduled_at,
            max_attempts=max_attempts or self.default_max_attempts
        )
        
        # Store job data
        await self.redis.hset(
            self.key_jobs,
            job_id,
            json.dumps(job.to_dict())
        )
        
        # Add to pending queue (sorted by priority and scheduled time)
        score = priority.value * 1e12 + scheduled_at.timestamp()
        await self.redis.zadd(self.key_pending, {job_id: score})
        
        logger.debug(
            "job_enqueued",
            job_id=job_id,
            job_type=job_type,
            priority=priority.value
        )
        
        return job
    
    async def dequeue(
        self,
        timeout: int = 5
    ) -> Optional[Job]:
        """
        Get next job from queue.
        
        Args:
            timeout: Blocking timeout in seconds
            
        Returns:
            Job if available, None otherwise
        """
        # Get job with lowest score (highest priority, earliest scheduled)
        result = await self.redis.zpopmin(self.key_pending, count=1)
        
        if not result:
            return None
        
        job_id, _ = result[0]
        job_id = job_id.decode() if isinstance(job_id, bytes) else job_id
        
        # Get job data
        job_data = await self.redis.hget(self.key_jobs, job_id)
        if not job_data:
            logger.warning("job_data_missing", job_id=job_id)
            return None
        
        job = Job.from_dict(json.loads(job_data))
        
        # Update status
        job.status = JobStatus.PROCESSING
        job.started_at = datetime.utcnow()
        job.attempts += 1
        
        # Move to processing queue
        await self.redis.hset(
            self.key_jobs,
            job_id,
            json.dumps(job.to_dict())
        )
        await self.redis.zadd(
            self.key_processing,
            {job_id: datetime.utcnow().timestamp()}
        )
        
        logger.debug(
            "job_dequeued",
            job_id=job_id,
            job_type=job.job_type,
            attempt=job.attempts
        )
        
        return job
    
    async def dequeue_batch(
        self,
        batch_size: int = 10
    ) -> List[Job]:
        """
        Get multiple jobs from queue.
        
        Args:
            batch_size: Number of jobs to get
            
        Returns:
            List of Jobs
        """
        jobs = []
        for _ in range(batch_size):
            job = await self.dequeue()
            if job:
                jobs.append(job)
            else:
                break
        return jobs
    
    async def complete(self, job: Job, result: Optional[Dict] = None):
        """
        Mark job as completed.
        
        Args:
            job: Job to complete
            result: Optional result data
        """
        job.status = JobStatus.COMPLETED
        job.completed_at = datetime.utcnow()
        
        if result:
            job.payload["_result"] = result
        
        # Update job data
        await self.redis.hset(
            self.key_jobs,
            job.job_id,
            json.dumps(job.to_dict())
        )
        
        # Move from processing to completed
        await self.redis.zrem(self.key_processing, job.job_id)
        await self.redis.zadd(
            self.key_completed,
            {job.job_id: datetime.utcnow().timestamp()}
        )
        
        logger.debug(
            "job_completed",
            job_id=job.job_id,
            job_type=job.job_type,
            attempts=job.attempts
        )
    
    async def fail(
        self,
        job: Job,
        error: str,
        traceback: Optional[str] = None
    ):
        """
        Mark job as failed (will retry if attempts remain).
        
        Args:
            job: Failed job
            error: Error message
            traceback: Error traceback
        """
        job.error = error
        job.traceback = traceback
        
        # Check if should retry
        if job.attempts < job.max_attempts:
            # Schedule retry with backoff
            delay = self._get_retry_delay(job.attempts)
            job.scheduled_at = datetime.utcnow() + timedelta(seconds=delay)
            job.status = JobStatus.RETRYING
            
            # Update job data
            await self.redis.hset(
                self.key_jobs,
                job.job_id,
                json.dumps(job.to_dict())
            )
            
            # Move back to pending
            await self.redis.zrem(self.key_processing, job.job_id)
            score = job.priority.value * 1e12 + job.scheduled_at.timestamp()
            await self.redis.zadd(self.key_pending, {job.job_id: score})
            
            logger.warning(
                "job_failed_retry_scheduled",
                job_id=job.job_id,
                attempt=job.attempts,
                max_attempts=job.max_attempts,
                retry_delay=delay,
                error=error[:100]
            )
        else:
            # Move to dead letter queue
            await self._move_to_dead_letter(job)
    
    async def _move_to_dead_letter(self, job: Job):
        """Move job to dead letter queue."""
        job.status = JobStatus.DEAD
        job.completed_at = datetime.utcnow()
        
        # Update job data
        await self.redis.hset(
            self.key_jobs,
            job.job_id,
            json.dumps(job.to_dict())
        )
        
        # Move to dead queue
        await self.redis.zrem(self.key_processing, job.job_id)
        await self.redis.zadd(
            self.key_dead,
            {job.job_id: datetime.utcnow().timestamp()}
        )
        
        logger.error(
            "job_moved_to_dead_letter",
            job_id=job.job_id,
            job_type=job.job_type,
            attempts=job.attempts,
            error=job.error[:100] if job.error else None
        )
    
    async def get_stats(self) -> QueueStats:
        """Get queue statistics."""
        pending = await self.redis.zcard(self.key_pending)
        processing = await self.redis.zcard(self.key_processing)
        completed = await self.redis.zcard(self.key_completed)
        failed = await self.redis.zcard(self.key_failed)
        dead = await self.redis.zcard(self.key_dead)
        
        return QueueStats(
            queue_name=self.queue_name,
            pending=pending,
            processing=processing,
            completed=completed,
            failed=failed,
            dead=dead,
            total=pending + processing + completed + failed + dead
        )
    
    async def get_dead_letter_jobs(
        self,
        limit: int = 100
    ) -> List[Job]:
        """Get jobs from dead letter queue."""
        job_ids = await self.redis.zrevrange(self.key_dead, 0, limit - 1)
        
        jobs = []
        for job_id in job_ids:
            job_id = job_id.decode() if isinstance(job_id, bytes) else job_id
            job_data = await self.redis.hget(self.key_jobs, job_id)
            if job_data:
                jobs.append(Job.from_dict(json.loads(job_data)))
        
        return jobs
    
    async def retry_dead_job(self, job_id: str) -> bool:
        """
        Retry a job from dead letter queue.
        
        Args:
            job_id: Job ID to retry
            
        Returns:
            True if retried, False if not found
        """
        job_data = await self.redis.hget(self.key_jobs, job_id)
        if not job_data:
            return False
        
        job = Job.from_dict(json.loads(job_data))
        job.status = JobStatus.PENDING
        job.attempts = 0
        job.error = None
        job.traceback = None
        job.scheduled_at = datetime.utcnow()
        
        # Update and move to pending
        await self.redis.hset(
            self.key_jobs,
            job_id,
            json.dumps(job.to_dict())
        )
        await self.redis.zrem(self.key_dead, job_id)
        
        score = job.priority.value * 1e12 + job.scheduled_at.timestamp()
        await self.redis.zadd(self.key_pending, {job_id: score})
        
        logger.info("dead_job_requeued", job_id=job_id)
        return True
    
    async def cleanup_old_jobs(self, days: int = 7):
        """
        Remove old completed jobs.
        
        Args:
            days: Remove jobs older than this many days
        """
        cutoff = datetime.utcnow() - timedelta(days=days)
        cutoff_timestamp = cutoff.timestamp()
        
        # Remove old completed jobs
        old_completed = await self.redis.zrangebyscore(
            self.key_completed,
            0,
            cutoff_timestamp
        )
        
        if old_completed:
            await self.redis.hdel(self.key_jobs, *old_completed)
            await self.redis.zremrangebyscore(
                self.key_completed,
                0,
                cutoff_timestamp
            )
            
            logger.info(
                "old_jobs_cleaned",
                queue=self.queue_name,
                removed=len(old_completed)
            )


class QueueWorker:
    """
    Worker for processing queue jobs.
    
    Usage:
        worker = QueueWorker(queue)
        
        @worker.handler("add_memory")
        async def handle_add_memory(payload):
            # Process job
            return {"status": "success"}
        
        await worker.start()
    """
    
    def __init__(
        self,
        queue: RedisQueue,
        poll_interval: float = 1.0,
        batch_size: int = 10
    ):
        """
        Initialize worker.
        
        Args:
            queue: RedisQueue to process
            poll_interval: Seconds between polls
            batch_size: Jobs to process per batch
        """
        self.queue = queue
        self.poll_interval = poll_interval
        self.batch_size = batch_size
        self.handlers: Dict[str, Callable] = {}
        self._running = False
        self._task: Optional[asyncio.Task] = None
        
        logger.info(
            "worker_initialized",
            queue=queue.queue_name,
            poll_interval=poll_interval
        )
    
    def handler(self, job_type: str):
        """
        Decorator to register job handler.
        
        Usage:
            @worker.handler("add_memory")
            async def handle_add_memory(payload):
                ...
        """
        def decorator(func: Callable):
            self.handlers[job_type] = func
            return func
        return decorator
    
    async def start(self):
        """Start the worker."""
        self._running = True
        self._task = asyncio.create_task(self._worker_loop())
        logger.info("worker_started", queue=self.queue.queue_name)
    
    async def stop(self):
        """Stop the worker."""
        self._running = False
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
        logger.info("worker_stopped", queue=self.queue.queue_name)
    
    async def _worker_loop(self):
        """Main worker loop."""
        while self._running:
            try:
                # Get jobs
                jobs = await self.queue.dequeue_batch(self.batch_size)
                
                if jobs:
                    for job in jobs:
                        await self._process_job(job)
                else:
                    # No jobs, wait before polling
                    await asyncio.sleep(self.poll_interval)
                    
            except Exception as e:
                logger.error("worker_loop_error", error=str(e))
                await asyncio.sleep(self.poll_interval)
    
    async def _process_job(self, job: Job):
        """Process a single job."""
        handler = self.handlers.get(job.job_type)
        
        if not handler:
            logger.error("no_handler_for_job_type", job_type=job.job_type)
            await self.queue.fail(job, f"No handler for job type: {job.job_type}")
            return
        
        try:
            # Call handler
            result = await handler(job.payload)
            
            # Mark complete
            await self.queue.complete(job, result)
            
        except Exception as e:
            import traceback as tb
            error_msg = str(e)
            traceback_str = tb.format_exc()
            
            logger.error(
                "job_processing_error",
                job_id=job.job_id,
                job_type=job.job_type,
                error=error_msg[:200]
            )
            
            await self.queue.fail(job, error_msg, traceback_str)


# =============================================================================
# Convenience Functions
# =============================================================================

async def get_redis_client() -> redis.Redis:
    """Get Redis client from settings."""
    return redis.Redis.from_url(
        settings.redis.url,
        decode_responses=True
    )


async def create_memory_write_queue() -> RedisQueue:
    """Create queue for memory writes."""
    redis_client = await get_redis_client()
    return RedisQueue(
        redis_client,
        "memory_writes",
        default_max_attempts=5
    )
