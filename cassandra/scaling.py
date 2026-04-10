"""
T41: Horizontal Scaling

This module provides horizontal scaling support:
- Stateless design verification
- Load balancer configuration
- Health checks
- Session management

Features:
- Stateless architecture validation
- Distributed caching
- Load balancer integration
"""

import os
from dataclasses import dataclass
from typing import Dict, Any, List, Optional
from datetime import datetime

import structlog
from pydantic import BaseModel, Field

logger = structlog.get_logger("cassandra.scaling")


class HealthStatus(BaseModel):
    """Health status for a component."""
    
    component: str
    status: str = Field(..., regex="^(healthy|unhealthy|degraded)$")
    latency_ms: float = 0.0
    last_check: datetime = Field(default_factory=datetime.utcnow)
    details: Dict[str, Any] = Field(default_factory=dict)


class ScalingConfig(BaseModel):
    """Configuration for horizontal scaling."""
    
    replica_count: int = Field(default=3)
    min_replicas: int = Field(default=2)
    max_replicas: int = Field(default=10)
    cpu_threshold: float = Field(default=70.0)
    memory_threshold: float = Field(default=80.0)
    
    class Config:
        json_schema_extra = {
            "example": {
                "replica_count": 3,
                "min_replicas": 2,
                "max_replicas": 10,
                "cpu_threshold": 70.0
            }
        }


class StatelessVerifier:
    """
    Verifies stateless design compliance.
    
    Checks:
    - No local state
    - External session storage
    - Shared cache
    - Database-driven configuration
    """
    
    def __init__(self):
        """Initialize verifier."""
        self.checks = []
        
        logger.info("stateless_verifier_initialized")
    
    def verify_stateless_design(self) -> Dict[str, Any]:
        """
        Verify stateless design compliance.
        
        Returns:
            Verification results
        """
        results = {
            "overall_status": "healthy",
            "checks": []
        }
        
        # Check 1: No local file storage
        local_storage_check = self._check_local_storage()
        results["checks"].append(local_storage_check)
        
        # Check 2: Session storage
        session_check = self._check_session_storage()
        results["checks"].append(session_check)
        
        # Check 3: Cache configuration
        cache_check = self._check_cache_configuration()
        results["checks"].append(cache_check)
        
        # Check 4: Database connectivity
        db_check = self._check_database_connectivity()
        results["checks"].append(db_check)
        
        # Check 5: External service dependencies
        external_check = self._check_external_services()
        results["checks"].append(external_check)
        
        # Determine overall status
        failed_checks = [c for c in results["checks"] if c["status"] == "failed"]
        if failed_checks:
            results["overall_status"] = "unhealthy"
        
        return results
    
    def _check_local_storage(self) -> Dict[str, Any]:
        """Check for local file storage usage."""
        # Check environment for local storage config
        local_uploads = os.getenv("LOCAL_UPLOADS_ENABLED", "false").lower() == "true"
        
        return {
            "name": "local_storage",
            "status": "passed" if not local_uploads else "failed",
            "message": "Local file storage should not be used" if local_uploads else "OK",
            "details": {"local_uploads_enabled": local_uploads}
        }
    
    def _check_session_storage(self) -> Dict[str, Any]:
        """Check session storage configuration."""
        session_backend = os.getenv("SESSION_BACKEND", "redis")
        is_distributed = session_backend in ["redis", "database", "memcached"]
        
        return {
            "name": "session_storage",
            "status": "passed" if is_distributed else "failed",
            "message": f"Session backend: {session_backend}",
            "details": {"backend": session_backend, "distributed": is_distributed}
        }
    
    def _check_cache_configuration(self) -> Dict[str, Any]:
        """Check cache configuration."""
        cache_backend = os.getenv("CACHE_BACKEND", "redis")
        is_distributed = cache_backend in ["redis", "memcached"]
        
        return {
            "name": "cache_configuration",
            "status": "passed" if is_distributed else "failed",
            "message": f"Cache backend: {cache_backend}",
            "details": {"backend": cache_backend, "distributed": is_distributed}
        }
    
    def _check_database_connectivity(self) -> Dict[str, Any]:
        """Check database connectivity."""
        db_url = os.getenv("DATABASE_URL")
        has_db_config = bool(db_url)
        
        return {
            "name": "database_connectivity",
            "status": "passed" if has_db_config else "failed",
            "message": "Database configured" if has_db_config else "Database not configured",
            "details": {"configured": has_db_config}
        }
    
    def _check_external_services(self) -> Dict[str, Any]:
        """Check external service configurations."""
        services = {
            "redis": bool(os.getenv("REDIS_URL")),
            "s3": bool(os.getenv("AWS_S3_BUCKET")),
            "kms": bool(os.getenv("AWS_KMS_KEY_ID"))
        }
        
        all_configured = all(services.values())
        
        return {
            "name": "external_services",
            "status": "passed" if all_configured else "warning",
            "message": "External services configured" if all_configured else "Some services not configured",
            "details": services
        }


class LoadBalancerConfig:
    """
    Load balancer configuration for horizontal scaling.
    
    Provides:
    - Health check endpoints
    - Routing rules
    - Sticky session configuration
    """
    
    def __init__(self):
        """Initialize load balancer config."""
        logger.info("load_balancer_config_initialized")
    
    def get_nginx_config(self) -> str:
        """
        Generate Nginx load balancer configuration.
        
        Returns:
            Nginx configuration string
        """
        return """
upstream cassandra_backend {
    least_conn;
    server cassandra-1:8000;
    server cassandra-2:8000;
    server cassandra-3:8000;
    
    keepalive 32;
}

server {
    listen 80;
    server_name api.cassandra.ai;
    
    location / {
        proxy_pass http://cassandra_backend;
        proxy_http_version 1.1;
        
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
        
        # WebSocket support
        proxy_set_header Upgrade $http_upgrade;
        proxy_set_header Connection "upgrade";
        
        # Timeouts
        proxy_connect_timeout 60s;
        proxy_send_timeout 60s;
        proxy_read_timeout 60s;
    }
    
    location /health {
        access_log off;
        proxy_pass http://cassandra_backend/health;
    }
}
"""
    
    def get_kubernetes_config(self) -> Dict[str, Any]:
        """
        Generate Kubernetes deployment configuration.
        
        Returns:
            Kubernetes config dict
        """
        return {
            "apiVersion": "apps/v1",
            "kind": "Deployment",
            "metadata": {
                "name": "cassandra-api",
                "labels": {"app": "cassandra-api"}
            },
            "spec": {
                "replicas": 3,
                "selector": {
                    "matchLabels": {"app": "cassandra-api"}
                },
                "template": {
                    "metadata": {
                        "labels": {"app": "cassandra-api"}
                    },
                    "spec": {
                        "containers": [{
                            "name": "api",
                            "image": "cassandra-ai/api:latest",
                            "ports": [{"containerPort": 8000}],
                            "envFrom": [{"configMapRef": {"name": "cassandra-config"}}],
                            "resources": {
                                "requests": {
                                    "cpu": "250m",
                                    "memory": "512Mi"
                                },
                                "limits": {
                                    "cpu": "1000m",
                                    "memory": "1Gi"
                                }
                            },
                            "livenessProbe": {
                                "httpGet": {
                                    "path": "/health/live",
                                    "port": 8000
                                },
                                "initialDelaySeconds": 30,
                                "periodSeconds": 10
                            },
                            "readinessProbe": {
                                "httpGet": {
                                    "path": "/health/ready",
                                    "port": 8000
                                },
                                "initialDelaySeconds": 5,
                                "periodSeconds": 5
                            }
                        }]
                    }
                }
            }
        }
    
    def get_hpa_config(self) -> Dict[str, Any]:
        """
        Generate Horizontal Pod Autoscaler configuration.
        
        Returns:
            HPA config dict
        """
        return {
            "apiVersion": "autoscaling/v2",
            "kind": "HorizontalPodAutoscaler",
            "metadata": {
                "name": "cassandra-api-hpa"
            },
            "spec": {
                "scaleTargetRef": {
                    "apiVersion": "apps/v1",
                    "kind": "Deployment",
                    "name": "cassandra-api"
                },
                "minReplicas": 2,
                "maxReplicas": 10,
                "metrics": [
                    {
                        "type": "Resource",
                        "resource": {
                            "name": "cpu",
                            "target": {
                                "type": "Utilization",
                                "averageUtilization": 70
                            }
                        }
                    },
                    {
                        "type": "Resource",
                        "resource": {
                            "name": "memory",
                            "target": {
                                "type": "Utilization",
                                "averageUtilization": 80
                            }
                        }
                    }
                ],
                "behavior": {
                    "scaleUp": {
                        "stabilizationWindowSeconds": 60,
                        "policies": [
                            {
                                "type": "Percent",
                                "value": 100,
                                "periodSeconds": 15
                            }
                        ]
                    },
                    "scaleDown": {
                        "stabilizationWindowSeconds": 300,
                        "policies": [
                            {
                                "type": "Percent",
                                "value": 10,
                                "periodSeconds": 60
                            }
                        ]
                    }
                }
            }
        }


class DistributedSessionManager:
    """
    Manages sessions in a distributed environment.
    
    Uses Redis for session storage to enable
    stateless application servers.
    """
    
    def __init__(self, redis_client=None):
        """
        Initialize session manager.
        
        Args:
            redis_client: Redis client for session storage
        """
        self.redis = redis_client
        self.session_ttl = 3600  # 1 hour
        
        logger.info("distributed_session_manager_initialized")
    
    async def create_session(
        self,
        user_id: str,
        org_id: str,
        data: Dict[str, Any]
    ) -> str:
        """
        Create new session.
        
        Args:
            user_id: User ID
            org_id: Organization ID
            data: Session data
            
        Returns:
            Session ID
        """
        import secrets
        session_id = secrets.token_urlsafe(32)
        
        session_data = {
            "user_id": user_id,
            "org_id": org_id,
            "data": data,
            "created_at": datetime.utcnow().isoformat()
        }
        
        if self.redis:
            await self.redis.setex(
                f"session:{session_id}",
                self.session_ttl,
                json.dumps(session_data)
            )
        
        return session_id
    
    async def get_session(self, session_id: str) -> Optional[Dict[str, Any]]:
        """
        Get session data.
        
        Args:
            session_id: Session ID
            
        Returns:
            Session data or None
        """
        if not self.redis:
            return None
        
        data = await self.redis.get(f"session:{session_id}")
        
        if data:
            return json.loads(data)
        
        return None
    
    async def delete_session(self, session_id: str):
        """
        Delete session.
        
        Args:
            session_id: Session ID
        """
        if self.redis:
            await self.redis.delete(f"session:{session_id}")


# =============================================================================
# FastAPI Integration
# =============================================================================

from fastapi import APIRouter, HTTPException

router = APIRouter(prefix="/scaling", tags=["Scaling"])

@router.get("/health/stateless")
async def check_stateless_design():
    """Verify stateless design compliance."""
    verifier = StatelessVerifier()
    results = verifier.verify_stateless_design()
    return results


@router.get("/config/nginx")
async def get_nginx_config():
    """Get Nginx load balancer configuration."""
    config = LoadBalancerConfig()
    return {"config": config.get_nginx_config()}


@router.get("/config/kubernetes")
async def get_kubernetes_config():
    """Get Kubernetes deployment configuration."""
    config = LoadBalancerConfig()
    return config.get_kubernetes_config()


@router.get("/config/hpa")
async def get_hpa_config():
    """Get Horizontal Pod Autoscaler configuration."""
    config = LoadBalancerConfig()
    return config.get_hpa_config()
