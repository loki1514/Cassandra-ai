"""
T33: Rate Limiting

This module provides rate limiting functionality:
- Per-organization rate limits
- 429 responses with Retry-After header
- Multiple rate limit strategies
- Redis-backed storage

Features:
- Sliding window rate limiting
- Burst allowance
- Configurable limits per endpoint
"""

import time
from dataclasses import dataclass
from enum import Enum
from typing import Optional, Dict, Any, Callable
from functools import wraps

import redis.asyncio as redis
import structlog
from fastapi import Request, HTTPException, Response
from fastapi.responses import JSONResponse

from cassandra.config import settings

logger = structlog.get_logger("cassandra.rate_limit")


class RateLimitStrategy(str, Enum):
    """Rate limiting strategies."""
    SLIDING_WINDOW = "sliding_window"
    FIXED_WINDOW = "fixed_window"
    TOKEN_BUCKET = "token_bucket"


@dataclass
class RateLimitConfig:
    """Rate limit configuration."""
    requests: int = 100  # requests per window
    window_seconds: int = 60  # window size
    burst: int = 10  # burst allowance
    strategy: RateLimitStrategy = RateLimitStrategy.SLIDING_WINDOW


@dataclass
class RateLimitStatus:
    """Current rate limit status."""
    allowed: bool
    remaining: int
    reset_time: int
    retry_after: Optional[int] = None


class RateLimiter:
    """
    Redis-backed rate limiter.
    
    Features:
    - Per-organization rate limits
    - Sliding window algorithm
    - Configurable per endpoint
    
    Usage:
        limiter = RateLimiter(redis_client)
        
        # Check rate limit
        status = await limiter.check_limit(
            key="org_123:api_calls",
            config=RateLimitConfig(requests=100, window_seconds=60)
        )
        
        if not status.allowed:
            raise RateLimitExceeded(retry_after=status.retry_after)
    """
    
    def __init__(
        self,
        redis_client: Optional[redis.Redis] = None,
        default_config: Optional[RateLimitConfig] = None
    ):
        """
        Initialize rate limiter.
        
        Args:
            redis_client: Redis client
            default_config: Default rate limit config
        """
        self.redis = redis_client
        self.default_config = default_config or RateLimitConfig()
        
        # In-memory fallback if Redis not available
        self._memory_store: Dict[str, list] = {}
        
        logger.info("rate_limiter_initialized")
    
    def _get_key(self, identifier: str, endpoint: Optional[str] = None) -> str:
        """Generate rate limit key."""
        if endpoint:
            return f"rate_limit:{identifier}:{endpoint}"
        return f"rate_limit:{identifier}"
    
    async def check_limit(
        self,
        identifier: str,
        endpoint: Optional[str] = None,
        config: Optional[RateLimitConfig] = None
    ) -> RateLimitStatus:
        """
        Check if request is within rate limit.
        
        Args:
            identifier: Unique identifier (e.g., org_id, user_id)
            endpoint: Optional endpoint name
            config: Rate limit config
            
        Returns:
            RateLimitStatus
        """
        config = config or self.default_config
        key = self._get_key(identifier, endpoint)
        now = time.time()
        window_start = now - config.window_seconds
        
        if self.redis:
            return await self._check_redis_limit(key, now, window_start, config)
        else:
            return self._check_memory_limit(key, now, window_start, config)
    
    async def _check_redis_limit(
        self,
        key: str,
        now: float,
        window_start: float,
        config: RateLimitConfig
    ) -> RateLimitStatus:
        """Check limit using Redis."""
        pipe = self.redis.pipeline()
        
        # Remove old entries outside window
        pipe.zremrangebyscore(key, 0, window_start)
        
        # Count current entries
        pipe.zcard(key)
        
        # Add current request
        pipe.zadd(key, {str(now): now})
        
        # Set expiry on key
        pipe.expire(key, config.window_seconds + 1)
        
        results = await pipe.execute()
        current_count = results[1]
        
        # Check if within limit (including burst)
        allowed = current_count <= (config.requests + config.burst)
        remaining = max(0, config.requests - current_count)
        reset_time = int(now + config.window_seconds)
        
        retry_after = None
        if not allowed:
            # Calculate retry after based on oldest entry
            oldest = await self.redis.zrange(key, 0, 0, withscores=True)
            if oldest:
                oldest_time = oldest[0][1]
                retry_after = int(oldest_time + config.window_seconds - now)
            else:
                retry_after = config.window_seconds
        
        return RateLimitStatus(
            allowed=allowed,
            remaining=remaining,
            reset_time=reset_time,
            retry_after=retry_after if not allowed else None
        )
    
    def _check_memory_limit(
        self,
        key: str,
        now: float,
        window_start: float,
        config: RateLimitConfig
    ) -> RateLimitStatus:
        """Check limit using in-memory store."""
        # Clean old entries
        if key not in self._memory_store:
            self._memory_store[key] = []
        
        self._memory_store[key] = [
            ts for ts in self._memory_store[key]
            if ts > window_start
        ]
        
        current_count = len(self._memory_store[key])
        
        # Add current request
        self._memory_store[key].append(now)
        
        # Check limit
        allowed = current_count <= (config.requests + config.burst)
        remaining = max(0, config.requests - current_count)
        reset_time = int(now + config.window_seconds)
        
        retry_after = None
        if not allowed and self._memory_store[key]:
            oldest_time = min(self._memory_store[key])
            retry_after = int(oldest_time + config.window_seconds - now)
        
        return RateLimitStatus(
            allowed=allowed,
            remaining=remaining,
            reset_time=reset_time,
            retry_after=retry_after if not allowed else None
        )
    
    async def get_limit_status(
        self,
        identifier: str,
        endpoint: Optional[str] = None
    ) -> Dict[str, Any]:
        """Get current rate limit status without consuming."""
        key = self._get_key(identifier, endpoint)
        
        if self.redis:
            count = await self.redis.zcard(key)
            ttl = await self.redis.ttl(key)
        else:
            count = len(self._memory_store.get(key, []))
            ttl = self.default_config.window_seconds
        
        return {
            "identifier": identifier,
            "endpoint": endpoint,
            "current_requests": count,
            "limit": self.default_config.requests,
            "window_seconds": self.default_config.window_seconds,
            "reset_in_seconds": max(0, ttl)
        }


class RateLimitExceeded(HTTPException):
    """Exception for rate limit exceeded."""
    
    def __init__(self, retry_after: int):
        super().__init__(
            status_code=429,
            detail="Rate limit exceeded. Please try again later."
        )
        self.retry_after = retry_after


# =============================================================================
# FastAPI Middleware
# =============================================================================

class RateLimitMiddleware:
    """
    FastAPI middleware for rate limiting.
    
    Usage:
        app.add_middleware(RateLimitMiddleware, limiter=rate_limiter)
    """
    
    def __init__(
        self,
        app,
        limiter: RateLimiter,
        skip_paths: Optional[list] = None
    ):
        self.app = app
        self.limiter = limiter
        self.skip_paths = skip_paths or ["/health", "/docs", "/openapi.json"]
    
    async def __call__(self, scope, receive, send):
        """Process request with rate limiting."""
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        
        request = Request(scope, receive)
        
        # Skip certain paths
        if any(request.url.path.startswith(path) for path in self.skip_paths):
            await self.app(scope, receive, send)
            return
        
        # Get identifier (org_id from header or IP)
        identifier = request.headers.get("X-Organization-ID") or request.client.host
        
        # Check rate limit
        status = await self.limiter.check_limit(identifier)
        
        if not status.allowed:
            response = JSONResponse(
                status_code=429,
                content={
                    "error": "Rate limit exceeded",
                    "retry_after": status.retry_after
                },
                headers={"Retry-After": str(status.retry_after)}
            )
            await response(scope, receive, send)
            return
        
        # Add rate limit headers
        async def send_with_headers(message):
            if message["type"] == "http.response.start":
                headers = message.get("headers", [])
                headers.append((b"X-RateLimit-Limit", str(self.limiter.default_config.requests).encode()))
                headers.append((b"X-RateLimit-Remaining", str(status.remaining).encode()))
                headers.append((b"X-RateLimit-Reset", str(status.reset_time).encode()))
                message["headers"] = headers
            await send(message)
        
        await self.app(scope, receive, send_with_headers)


# =============================================================================
# Decorator for endpoint rate limiting
# =============================================================================

def rate_limit(
    requests: int = 100,
    window_seconds: int = 60,
    burst: int = 10,
    key_func: Optional[Callable[[Request], str]] = None
):
    """
    Decorator for endpoint-specific rate limiting.
    
    Usage:
        @app.post("/api/action")
        @rate_limit(requests=10, window_seconds=60)
        async def action(request: Request):
            ...
    """
    config = RateLimitConfig(
        requests=requests,
        window_seconds=window_seconds,
        burst=burst
    )
    
    def decorator(func):
        @wraps(func)
        async def wrapper(*args, **kwargs):
            # Get request from args/kwargs
            request = kwargs.get('request')
            if not request and args:
                for arg in args:
                    if isinstance(arg, Request):
                        request = arg
                        break
            
            if not request:
                raise ValueError("Request object not found")
            
            # Get identifier
            if key_func:
                identifier = key_func(request)
            else:
                identifier = request.headers.get("X-Organization-ID") or request.client.host
            
            # Check rate limit
            limiter = kwargs.get('limiter') or getattr(request.app.state, 'rate_limiter', None)
            
            if limiter:
                status = await limiter.check_limit(identifier, config=config)
                
                if not status.allowed:
                    raise RateLimitExceeded(retry_after=status.retry_after)
            
            return await func(*args, **kwargs)
        
        return wrapper
    return decorator


# =============================================================================
# Default rate limit configurations
# =============================================================================

DEFAULT_LIMITS = {
    "api_default": RateLimitConfig(requests=1000, window_seconds=60),
    "voice_transcribe": RateLimitConfig(requests=60, window_seconds=60),
    "ticket_create": RateLimitConfig(requests=100, window_seconds=60),
    "memory_search": RateLimitConfig(requests=300, window_seconds=60),
    "websocket_connect": RateLimitConfig(requests=10, window_seconds=60),
}


async def get_rate_limiter() -> RateLimiter:
    """Get or create rate limiter instance."""
    try:
        redis_client = redis.Redis.from_url(
            settings.redis.url,
            decode_responses=True
        )
        return RateLimiter(redis_client)
    except Exception as e:
        logger.warning(f"Redis not available for rate limiting: {e}")
        return RateLimiter()  # In-memory fallback
