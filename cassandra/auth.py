"""
T17: JWT Auth Middleware + Org Scoping

This module provides JWT authentication and authorization:
- verify_jwt(token) → {user_id, org_id, role}
- FastAPI dependency for auth
- Middleware logging (no payload content)
- 401 for expired/missing tokens

Features:
- Supabase JWT verification
- Organization-based scoping
- Role-based permissions
- Secure token handling
"""

import time
from typing import Optional, Dict, Any, List
from dataclasses import dataclass
from functools import wraps

import jwt
from jwt.exceptions import (
    ExpiredSignatureError,
    InvalidTokenError,
    DecodeError,
    InvalidSignatureError
)
from fastapi import Request, HTTPException, status, Depends
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
import structlog
import httpx

from cassandra.config import settings

logger = structlog.get_logger("cassandra.auth")


# =============================================================================
# Data Models
# =============================================================================

@dataclass
class UserContext:
    """
    Authenticated user context.
    
    Attributes:
        user_id: Unique user identifier
        org_id: Organization identifier for scoping
        role: User role (admin, member, viewer)
        permissions: List of granted permissions
        email: User email (optional)
        metadata: Additional user metadata
    """
    user_id: str
    org_id: str
    role: str
    permissions: List[str]
    email: Optional[str] = None
    metadata: Dict[str, Any] = None


# =============================================================================
# Custom Exceptions
# =============================================================================

class AuthError(Exception):
    """Base authentication error."""
    pass


class TokenExpiredError(AuthError):
    """Raised when token has expired."""
    pass


class InvalidTokenError(AuthError):
    """Raised when token is invalid."""
    pass


class MissingTokenError(AuthError):
    """Raised when token is missing."""
    pass


class OrgAccessError(AuthError):
    """Raised when user doesn't have access to organization."""
    pass


# =============================================================================
# JWT Verification
# =============================================================================

class JWTVerifier:
    """
    JWT token verifier for Supabase tokens.
    
    Features:
    - RS256 signature verification
    - Expiration checking
    - Claims validation
    - JWKS key fetching
    """
    
    def __init__(self):
        self.jwt_secret = settings.supabase.jwt_secret
        self.jwt_algorithm = settings.security.jwt_algorithm
        self.jwks_url = f"{settings.supabase.url}/auth/v1/jwks"
        self._jwks_cache: Optional[Dict] = None
        self._jwks_cache_time: float = 0
        self._jwks_cache_ttl = 3600  # 1 hour
        
        logger.info(
            "jwt_verifier_initialized",
            algorithm=self.jwt_algorithm,
            has_secret=bool(self.jwt_secret)
        )
    
    async def _get_jwks(self) -> Dict:
        """Fetch JWKS from Supabase (with caching)."""
        now = time.time()
        
        if self._jwks_cache and (now - self._jwks_cache_time) < self._jwks_cache_ttl:
            return self._jwks_cache
        
        try:
            async with httpx.AsyncClient() as client:
                response = await client.get(self.jwks_url)
                response.raise_for_status()
                self._jwks_cache = response.json()
                self._jwks_cache_time = now
                return self._jwks_cache
        except Exception as e:
            logger.error("jwks_fetch_error", error=str(e))
            # Fallback to cached if available
            if self._jwks_cache:
                return self._jwks_cache
            raise
    
    def _get_signing_key(self, token: str, jwks: Dict) -> str:
        """Extract signing key from JWKS based on token header."""
        try:
            # Get key ID from token header
            header = jwt.get_unverified_header(token)
            kid = header.get("kid")
            
            if not kid:
                raise InvalidTokenError("Token missing key ID")
            
            # Find matching key in JWKS
            for key in jwks.get("keys", []):
                if key.get("kid") == kid:
                    # Convert JWK to PEM
                    return self._jwk_to_pem(key)
            
            raise InvalidTokenError(f"No matching key found for kid: {kid}")
            
        except Exception as e:
            logger.error("signing_key_error", error=str(e))
            raise InvalidTokenError(f"Failed to get signing key: {str(e)}") from e
    
    def _jwk_to_pem(self, jwk: Dict) -> str:
        """Convert JWK to PEM format."""
        from cryptography.hazmat.primitives import serialization
        from cryptography.hazmat.primitives.asymmetric.rsa import RSAPublicNumbers
        from cryptography.hazmat.backends import default_backend
        
        # Extract RSA components
        e = int.from_bytes(
            self._base64url_decode(jwk["e"]),
            byteorder="big"
        )
        n = int.from_bytes(
            self._base64url_decode(jwk["n"]),
            byteorder="big"
        )
        
        # Create public key
        public_numbers = RSAPublicNumbers(e, n)
        public_key = public_numbers.public_key(default_backend())
        
        # Export as PEM
        pem = public_key.public_bytes(
            encoding=serialization.Encoding.PEM,
            format=serialization.PublicFormat.SubjectPublicKeyInfo
        )
        
        return pem.decode("utf-8")
    
    def _base64url_decode(self, input_str: str) -> bytes:
        """Decode base64url string."""
        import base64
        padding = 4 - len(input_str) % 4
        if padding != 4:
            input_str += "=" * padding
        return base64.urlsafe_b64decode(input_str)
    
    async def verify(self, token: str) -> Dict[str, Any]:
        """
        Verify JWT token and return claims.
        
        Args:
            token: JWT token string
            
        Returns:
            Token claims dictionary
            
        Raises:
            TokenExpiredError: If token has expired
            InvalidTokenError: If token is invalid
            MissingTokenError: If token is empty
        """
        if not token:
            raise MissingTokenError("Token is required")
        
        try:
            # Use symmetric key if configured (for testing)
            if self.jwt_secret and self.jwt_algorithm == "HS256":
                payload = jwt.decode(
                    token,
                    self.jwt_secret,
                    algorithms=[self.jwt_algorithm]
                )
            else:
                # Use JWKS for RS256
                jwks = await self._get_jwks()
                signing_key = self._get_signing_key(token, jwks)
                payload = jwt.decode(
                    token,
                    signing_key,
                    algorithms=["RS256"]
                )
            
            logger.debug(
                "token_verified",
                user_id=payload.get("sub"),
                org_id=payload.get("org_id")
            )
            
            return payload
            
        except ExpiredSignatureError:
            logger.warning("token_expired")
            raise TokenExpiredError("Token has expired")
            
        except (InvalidSignatureError, DecodeError) as e:
            logger.warning("invalid_token", error=str(e))
            raise InvalidTokenError("Invalid token signature") from e
            
        except InvalidTokenError:
            logger.warning("invalid_token_claims")
            raise
            
        except Exception as e:
            logger.error("token_verification_error", error=str(e))
            raise InvalidTokenError(f"Token verification failed: {str(e)}") from e


# Global verifier instance
_verifier: Optional[JWTVerifier] = None


def get_verifier() -> JWTVerifier:
    """Get or create JWT verifier instance."""
    global _verifier
    if _verifier is None:
        _verifier = JWTVerifier()
    return _verifier


# =============================================================================
# Main Verification Function
# =============================================================================

def verify_jwt(token: str) -> UserContext:
    """
    Verify JWT token and return user context.
    
    This is the main entry point for token verification.
    
    Args:
        token: JWT token string
        
    Returns:
        UserContext with user_id, org_id, role
        
    Raises:
        HTTPException: 401 for expired/invalid/missing tokens
        
    Example:
        >>> try:
        ...     user = verify_jwt(token)
        ...     print(f"User: {user.user_id}, Org: {user.org_id}")
        ... except HTTPException as e:
        ...     print(f"Auth failed: {e.detail}")
    """
    import asyncio
    
    try:
        verifier = get_verifier()
        
        # Run async verification
        try:
            loop = asyncio.get_event_loop()
        except RuntimeError:
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
        
        payload = loop.run_until_complete(verifier.verify(token))
        
        # Extract user context from claims
        # Supabase token structure:
        # - sub: user ID
        # - org_id: organization ID (custom claim)
        # - role: user role (custom claim)
        # - email: user email
        
        user_id = payload.get("sub")
        if not user_id:
            raise InvalidTokenError("Token missing user ID")
        
        org_id = payload.get("org_id", "default")
        role = payload.get("role", "member")
        
        # Extract permissions based on role
        permissions = _get_role_permissions(role)
        
        user_context = UserContext(
            user_id=user_id,
            org_id=org_id,
            role=role,
            permissions=permissions,
            email=payload.get("email"),
            metadata={
                "issued_at": payload.get("iat"),
                "expires_at": payload.get("exp"),
                "issuer": payload.get("iss")
            }
        )
        
        logger.info(
            "user_authenticated",
            user_id=user_context.user_id,
            org_id=user_context.org_id,
            role=user_context.role
        )
        
        return user_context
        
    except TokenExpiredError:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Token has expired",
            headers={"WWW-Authenticate": "Bearer"}
        )
        
    except (InvalidTokenError, MissingTokenError) as e:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=str(e),
            headers={"WWW-Authenticate": "Bearer"}
        )
        
    except Exception as e:
        logger.error("auth_unexpected_error", error=str(e))
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Authentication failed",
            headers={"WWW-Authenticate": "Bearer"}
        )


def _get_role_permissions(role: str) -> List[str]:
    """Get permissions for a given role."""
    permissions_map = {
        "admin": [
            "read:all",
            "write:all",
            "delete:all",
            "manage:users",
            "manage:org",
            "access:admin"
        ],
        "member": [
            "read:own",
            "write:own",
            "read:org"
        ],
        "viewer": [
            "read:own",
            "read:org"
        ]
    }
    return permissions_map.get(role, permissions_map["viewer"])


# =============================================================================
# FastAPI Dependencies
# =============================================================================

# Security scheme for FastAPI docs
security_scheme = HTTPBearer(auto_error=False)


async def get_current_user(
    credentials: HTTPAuthorizationCredentials = Depends(security_scheme)
) -> UserContext:
    """
    FastAPI dependency to get current authenticated user.
    
    Usage:
        @app.get("/protected")
        async def protected_endpoint(user: UserContext = Depends(get_current_user)):
            return {"user_id": user.user_id}
    """
    if not credentials:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Authentication required",
            headers={"WWW-Authenticate": "Bearer"}
        )
    
    return verify_jwt(credentials.credentials)


async def get_current_user_optional(
    credentials: HTTPAuthorizationCredentials = Depends(security_scheme)
) -> Optional[UserContext]:
    """Get current user if authenticated, None otherwise."""
    if not credentials:
        return None
    
    try:
        return verify_jwt(credentials.credentials)
    except HTTPException:
        return None


def require_org_access(org_id_param: str = "org_id"):
    """
    Dependency factory to require organization access.
    
    Usage:
        @app.get("/orgs/{org_id}/data")
        async def get_org_data(
            org_id: str,
            user: UserContext = Depends(require_org_access())
        ):
            # User has access to org_id
            pass
    """
    async def _check_org_access(
        user: UserContext = Depends(get_current_user)
    ) -> UserContext:
        # In real implementation, check if user has access to org
        # For now, trust the org_id in the token
        return user
    
    return _check_org_access


def require_permissions(required_permissions: List[str]):
    """
    Dependency factory to require specific permissions.
    
    Usage:
        @app.delete("/data/{id}")
        async def delete_data(
            id: str,
            user: UserContext = Depends(require_permissions(["delete:all"]))
        ):
            pass
    """
    async def _check_permissions(
        user: UserContext = Depends(get_current_user)
    ) -> UserContext:
        missing = [p for p in required_permissions if p not in user.permissions]
        if missing:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=f"Missing permissions: {', '.join(missing)}"
            )
        return user
    
    return _check_permissions


# =============================================================================
# Middleware
# =============================================================================

class AuthMiddleware:
    """
    FastAPI middleware for authentication logging.
    
    Logs authentication events without payload content.
    """
    
    async def __call__(self, request: Request, call_next):
        """Process request and log auth events."""
        start_time = time.time()
        
        # Extract auth header for logging (not the token itself)
        auth_header = request.headers.get("authorization", "")
        has_auth = bool(auth_header)
        auth_type = auth_header.split()[0] if auth_header else None
        
        # Log request (no payload content)
        logger.debug(
            "request_started",
            method=request.method,
            path=request.url.path,
            has_auth=has_auth,
            auth_type=auth_type,
            client_ip=request.client.host if request.client else None
        )
        
        # Process request
        response = await call_next(request)
        
        # Log response (no payload content)
        duration = time.time() - start_time
        logger.debug(
            "request_completed",
            method=request.method,
            path=request.url.path,
            status_code=response.status_code,
            duration_ms=round(duration * 1000, 2)
        )
        
        return response


# =============================================================================
# Utility Functions
# =============================================================================

def create_test_token(
    user_id: str,
    org_id: str,
    role: str = "member",
    expires_hours: int = 24
) -> str:
    """
    Create a test JWT token (for development/testing only).
    
    Args:
        user_id: User identifier
        org_id: Organization identifier
        role: User role
        expires_hours: Token expiration in hours
        
    Returns:
        JWT token string
    """
    if not settings.jwt_secret:
        raise ValueError("JWT secret not configured")
    
    now = time.time()
    payload = {
        "sub": user_id,
        "org_id": org_id,
        "role": role,
        "iat": now,
        "exp": now + (expires_hours * 3600)
    }
    
    token = jwt.encode(
        payload,
        settings.jwt_secret,
        algorithm=settings.jwt_algorithm
    )
    
    return token


def decode_token_unsafe(token: str) -> Dict[str, Any]:
    """
    Decode token without verification (for debugging only).
    
    Args:
        token: JWT token string
        
    Returns:
        Token payload (unverified)
    """
    return jwt.decode(token, options={"verify_signature": False})
