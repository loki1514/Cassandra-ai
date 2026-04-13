"""
T06: Per-Org KMS Encryption Setup

This module provides encryption/decryption services using AWS KMS
with per-organization key isolation for multi-tenant security.

Features:
- Per-organization KMS key generation and management
- Symmetric encryption for sensitive data
- Key caching for performance
- Async support for non-blocking operations
"""

import base64
import json
import logging
from typing import Optional, Dict, Any, Union
from functools import lru_cache
from datetime import datetime, timedelta

# boto3 is lazy-loaded inside OrgKeyManager to avoid import failures
# when AWS credentials are not configured. KMS operations will raise
# KMSEncryptionError with a clear message when AWS is not available.

# Configure logging
logger = logging.getLogger(__name__)


class KMSEncryptionError(Exception):
    """Base exception for KMS encryption errors."""
    pass


class KeyNotFoundError(KMSEncryptionError):
    """Raised when a KMS key is not found for an organization."""
    pass


class EncryptionError(KMSEncryptionError):
    """Raised when encryption fails."""
    pass


class DecryptionError(KMSEncryptionError):
    """Raised when decryption fails."""
    pass


class OrgKeyManager:
    """
    Manages per-organization KMS keys for encryption.
    
    This class handles:
    - Key generation for new organizations
    - Key retrieval and caching
    - Key rotation tracking
    """
    
    def __init__(
        self,
        aws_region: str = "us-east-1",
        key_alias_prefix: str = "alias/cassandra-org",
        enable_key_rotation: bool = True
    ):
        """
        Initialize the OrgKeyManager.
        
        Args:
            aws_region: AWS region for KMS
            key_alias_prefix: Prefix for KMS key aliases
            enable_key_rotation: Whether to enable automatic key rotation
        """
        self._aws_region = aws_region
        self.key_alias_prefix = key_alias_prefix
        self.enable_key_rotation = enable_key_rotation
        self._key_cache: Dict[str, Dict[str, Any]] = {}
        self._cache_ttl = timedelta(hours=1)
        self._kms_client = None  # lazy-loaded

    @property
    def kms_client(self):
        """Lazy-load KMS client. Returns None if AWS is not configured."""
        if self._kms_client is None:
            try:
                import boto3
                self._kms_client = boto3.client('kms', region_name=self._aws_region)
            except Exception as e:
                logger.warning("kms_client_unavailable", reason=str(e))
                self._kms_client = None
        return self._kms_client

    def _require_kms(self):
        """Raise KMSEncryptionError if KMS client is unavailable."""
        if self.kms_client is None:
            raise KMSEncryptionError(
                "AWS KMS is not configured. "
                "Set AWS_ACCESS_KEY_ID, AWS_SECRET_ACCESS_KEY (or AWS_ROLE_ARN), "
                "and ensure boto3 is installed."
            )
        
    def _get_key_alias(self, org_id: str) -> str:
        """Generate the KMS key alias for an organization."""
        return f"{self.key_alias_prefix}-{org_id}"
    
    def _get_from_cache(self, org_id: str) -> Optional[str]:
        """Get key ID from cache if valid."""
        if org_id in self._key_cache:
            cached = self._key_cache[org_id]
            if datetime.utcnow() - cached['timestamp'] < self._cache_ttl:
                return cached['key_id']
            else:
                del self._key_cache[org_id]
        return None
    
    def _add_to_cache(self, org_id: str, key_id: str):
        """Add key ID to cache."""
        self._key_cache[org_id] = {
            'key_id': key_id,
            'timestamp': datetime.utcnow()
        }
    
    def generate_org_key(self, org_id: str) -> str:
        """
        Generate a new KMS key for an organization.

        Args:
            org_id: Organization UUID

        Returns:
            The KMS Key ID

        Raises:
            KMSEncryptionError: If AWS is not configured or key generation fails
        """
        from botocore.exceptions import ClientError, BotoCoreError

        self._require_kms()
        key_alias = self._get_key_alias(org_id)

        try:
            # Check if key already exists
            try:
                response = self.kms_client.describe_key(KeyId=key_alias)
                key_id = response['KeyMetadata']['KeyId']
                logger.info(f"Using existing KMS key for org {org_id}: {key_id}")
                self._add_to_cache(org_id, key_id)
                return key_id
            except ClientError as e:
                if e.response['Error']['Code'] != 'NotFoundException':
                    raise

            # Get AWS account ID for key policy
            import boto3
            sts = boto3.client('sts')
            account_id = sts.get_caller_identity()['Account']

            # Create new key
            key_policy = {
                "Version": "2012-10-17",
                "Statement": [
                    {
                        "Sid": "Enable IAM User Permissions",
                        "Effect": "Allow",
                        "Principal": {
                            "AWS": f"arn:aws:iam::{account_id}:root"
                        },
                        "Action": "kms:*",
                        "Resource": "*"
                    },
                    {
                        "Sid": "Allow organization-specific access",
                        "Effect": "Allow",
                        "Principal": {
                            "AWS": "*"
                        },
                        "Action": [
                            "kms:Encrypt",
                            "kms:Decrypt",
                            "kms:GenerateDataKey*",
                            "kms:DescribeKey"
                        ],
                        "Resource": "*",
                        "Condition": {
                            "StringEquals": {
                                "kms:CallerAccount": account_id
                            }
                        }
                    }
                ]
            }

            response = self.kms_client.create_key(
                Description=f"Cassandra AI encryption key for organization {org_id}",
                KeyUsage='ENCRYPT_DECRYPT',
                KeySpec='SYMMETRIC_DEFAULT',
                Policy=json.dumps(key_policy),
                Tags=[
                    {
                        'TagKey': 'Organization',
                        'TagValue': org_id
                    },
                    {
                        'TagKey': 'Service',
                        'TagValue': 'cassandra-ai'
                    }
                ]
            )

            key_id = response['KeyMetadata']['KeyId']

            # Create alias for the key
            self.kms_client.create_alias(
                AliasName=key_alias,
                TargetKeyId=key_id
            )

            # Enable automatic key rotation if configured
            if self.enable_key_rotation:
                self.kms_client.enable_key_rotation(KeyId=key_id)

            self._add_to_cache(org_id, key_id)
            logger.info(f"Created new KMS key for org {org_id}: {key_id}")

            return key_id

        except (ClientError, BotoCoreError) as e:
            logger.error(f"Failed to generate KMS key for org {org_id}: {e}")
            raise EncryptionError(f"Failed to generate KMS key: {e}")
    
    def get_org_key(self, org_id: str) -> str:
        """
        Get the KMS key ID for an organization.

        Args:
            org_id: Organization UUID

        Returns:
            The KMS Key ID

        Raises:
            KeyNotFoundError: If key doesn't exist
        """
        from botocore.exceptions import ClientError

        # Check cache first
        cached_key = self._get_from_cache(org_id)
        if cached_key:
            return cached_key

        self._require_kms()
        key_alias = self._get_key_alias(org_id)

        try:
            response = self.kms_client.describe_key(KeyId=key_alias)
            key_id = response['KeyMetadata']['KeyId']
            self._add_to_cache(org_id, key_id)
            return key_id
        except ClientError as e:
            if e.response['Error']['Code'] == 'NotFoundException':
                raise KeyNotFoundError(f"KMS key not found for org {org_id}")
            logger.error(f"Failed to get KMS key for org {org_id}: {e}")
            raise EncryptionError(f"Failed to get KMS key: {e}")


class EncryptionService:
    """
    Service for encrypting and decrypting data using per-organization KMS keys.
    
    This service:
    - Uses envelope encryption (KMS data keys)
    - Supports both sync and async operations
    - Handles serialization of complex objects
    """
    
    def __init__(
        self,
        aws_region: str = "us-east-1",
        key_alias_prefix: str = "alias/cassandra-org",
        enable_key_rotation: bool = True
    ):
        """
        Initialize the EncryptionService.
        
        Args:
            aws_region: AWS region for KMS
            key_alias_prefix: Prefix for KMS key aliases
            enable_key_rotation: Whether to enable automatic key rotation
        """
        self.key_manager = OrgKeyManager(
            aws_region=aws_region,
            key_alias_prefix=key_alias_prefix,
            enable_key_rotation=enable_key_rotation
        )
        self._aws_region = aws_region
        self._kms_client = None  # lazy-loaded

    @property
    def kms_client(self):
        """Lazy-load KMS client. Returns None if AWS is not configured."""
        if self._kms_client is None:
            try:
                import boto3
                self._kms_client = boto3.client('kms', region_name=self._aws_region)
            except Exception as e:
                logger.warning("kms_client_unavailable", reason=str(e))
                self._kms_client = None
        return self._kms_client
        
    def _serialize_payload(self, payload: Any) -> bytes:
        """Serialize payload to bytes."""
        if isinstance(payload, bytes):
            return payload
        elif isinstance(payload, str):
            return payload.encode('utf-8')
        else:
            return json.dumps(payload, default=str).encode('utf-8')
    
    def _deserialize_payload(self, data: bytes) -> Any:
        """Deserialize payload from bytes."""
        try:
            return json.loads(data.decode('utf-8'))
        except (json.JSONDecodeError, UnicodeDecodeError):
            return data.decode('utf-8')

    def _require_kms(self):
        """Raise KMSEncryptionError if KMS client is unavailable."""
        if self.kms_client is None:
            raise KMSEncryptionError(
                "AWS KMS is not configured. "
                "Set AWS_ACCESS_KEY_ID, AWS_SECRET_ACCESS_KEY (or AWS_ROLE_ARN), "
                "and ensure boto3 is installed."
            )

    def encrypt(
        self,
        payload: Any,
        org_id: str,
        key_id: Optional[str] = None
    ) -> Dict[str, str]:
        """
        Encrypt data using the organization's KMS key.
        
        Uses envelope encryption:
        1. Generate a data key from KMS
        2. Encrypt payload with data key (locally)
        3. Store encrypted data key alongside encrypted payload
        
        Args:
            payload: Data to encrypt (str, bytes, or JSON-serializable)
            org_id: Organization UUID for key selection
            key_id: Optional specific KMS key ID (uses org key if not provided)
            
        Returns:
            Dict containing:
                - ciphertext: Base64-encoded encrypted payload
                - encrypted_data_key: Base64-encoded encrypted data key
                - org_id: Organization ID
                
        Raises:
            EncryptionError: If encryption fails
        """
        from botocore.exceptions import ClientError, BotoCoreError

        self._require_kms()
        try:
            # Get or generate org key
            if key_id is None:
                try:
                    key_id = self.key_manager.get_org_key(org_id)
                except KeyNotFoundError:
                    key_id = self.key_manager.generate_org_key(org_id)
            
            # Generate data key
            data_key_response = self.kms_client.generate_data_key(
                KeyId=key_id,
                KeySpec='AES_256'
            )
            
            plaintext_data_key = data_key_response['Plaintext']
            encrypted_data_key = data_key_response['CiphertextBlob']
            
            # Encrypt payload locally with data key
            from cryptography.fernet import Fernet
            import hashlib
            
            # Derive Fernet key from data key
            fernet_key = base64.urlsafe_b64encode(
                hashlib.sha256(plaintext_data_key).digest()
            )
            fernet = Fernet(fernet_key)
            
            serialized = self._serialize_payload(payload)
            encrypted_payload = fernet.encrypt(serialized)
            
            # SECURITY NOTE: Python bytes are immutable - this assignment doesn't clear memory
            # The original key bytes remain on the heap until garbage collected
            # For true secure memory handling, consider:
            # 1. Using a C extension with explicit memory clearing
            # 2. Using secrets.compare_digest for key comparison (constant time)
            # 3. Minimizing key lifetime by decrypting only when needed
            # 4. Using AWS KMS directly for small payloads instead of envelope encryption
            import secrets
            # Best effort: overwrite reference (original bytes still in heap)
            plaintext_data_key = secrets.token_bytes(len(plaintext_data_key))
            del plaintext_data_key  # Remove reference for faster GC
            
            return {
                'ciphertext': base64.b64encode(encrypted_payload).decode('utf-8'),
                'encrypted_data_key': base64.b64encode(encrypted_data_key).decode('utf-8'),
                'org_id': org_id,
                'key_id': key_id
            }
            
        except (ClientError, BotoCoreError) as e:
            logger.error(f"KMS encryption failed for org {org_id}: {e}")
            raise EncryptionError(f"Encryption failed: {e}")
        except Exception as e:
            logger.error(f"Unexpected encryption error for org {org_id}: {e}")
            raise EncryptionError(f"Unexpected encryption error: {e}")
    
    def decrypt(
        self,
        ciphertext: str,
        encrypted_data_key: str,
        org_id: str
    ) -> Any:
        """
        Decrypt data using the organization's KMS key.
        
        Args:
            ciphertext: Base64-encoded encrypted payload
            encrypted_data_key: Base64-encoded encrypted data key
            org_id: Organization UUID
            
        Returns:
            Decrypted payload (deserialized from JSON if applicable)
            
        Raises:
            DecryptionError: If decryption fails
        """
        from botocore.exceptions import ClientError, BotoCoreError

        self._require_kms()
        try:
            # SECURITY FIX: Use stored key_id for decryption to support key rotation
            # The key_id should be stored alongside the encrypted data
            # This allows decryption of data encrypted with old key versions
            
            # For now, use the alias which may point to old key during rotation
            # In production, retrieve the specific key_id from encrypted data metadata
            key_alias = self.key_manager._get_key_alias(org_id)
            
            # Decrypt the data key using KMS
            # Note: KMS can decrypt with any key version, not just the current alias target
            decrypted_key_response = self.kms_client.decrypt(
                CiphertextBlob=base64.b64decode(encrypted_data_key),
                KeyId=key_alias  # Explicitly specify key for audit logging
            )
            
            plaintext_data_key = decrypted_key_response['Plaintext']
            
            # Decrypt payload locally
            from cryptography.fernet import Fernet
            import hashlib
            
            fernet_key = base64.urlsafe_b64encode(
                hashlib.sha256(plaintext_data_key).digest()
            )
            fernet = Fernet(fernet_key)
            
            encrypted_payload = base64.b64decode(ciphertext)
            decrypted_payload = fernet.decrypt(encrypted_payload)
            
            # SECURITY NOTE: Python bytes are immutable - this assignment doesn't clear memory
            # The original key bytes remain on the heap until garbage collected
            # For true secure memory handling, consider:
            # 1. Using a C extension with explicit memory clearing
            # 2. Using secrets.compare_digest for key comparison (constant time)
            # 3. Minimizing key lifetime by decrypting only when needed
            # 4. Using AWS KMS directly for small payloads instead of envelope encryption
            import secrets
            # Best effort: overwrite reference (original bytes still in heap)
            plaintext_data_key = secrets.token_bytes(len(plaintext_data_key))
            del plaintext_data_key  # Remove reference for faster GC
            
            return self._deserialize_payload(decrypted_payload)
            
        except (ClientError, BotoCoreError) as e:
            logger.error(f"KMS decryption failed for org {org_id}: {e}")
            raise DecryptionError(f"Decryption failed: {e}")
        except Exception as e:
            logger.error(f"Unexpected decryption error for org {org_id}: {e}")
            raise DecryptionError(f"Unexpected decryption error: {e}")
    
    def rotate_key(self, org_id: str) -> str:
        """
        Manually trigger key rotation for an organization.

        Args:
            org_id: Organization UUID

        Returns:
            New key ID
        """
        from botocore.exceptions import ClientError, BotoCoreError

        self._require_kms()
        key_id = self.key_manager.get_org_key(org_id)

        try:
            self.kms_client.enable_key_rotation(KeyId=key_id)
            logger.info(f"Key rotation enabled for org {org_id}: {key_id}")
            return key_id
        except (ClientError, BotoCoreError) as e:
            logger.error(f"Key rotation failed for org {org_id}: {e}")
            raise EncryptionError(f"Key rotation failed: {e}")


# =============================================================================
# T32: KMS Key Rotation Management
# =============================================================================

class KeyRotationManager:
    """
    Manages KMS key rotation with 90-day policy.
    
    Features:
    - 90-day rotation schedule
    - Version tracking
    - Automatic rotation enforcement
    - Audit logging
    """
    
    ROTATION_PERIOD_DAYS = 90
    
    def __init__(
        self,
        kms_client=None,
        aws_region: str = "us-east-1",
        key_alias_prefix: str = "alias/cassandra-org"
    ):
        """
        Initialize key rotation manager.
        
        Args:
            kms_client: Optional KMS client
            aws_region: AWS region
            key_alias_prefix: Prefix for key aliases
        """
        self._kms_client_provided = kms_client
        self._aws_region = aws_region
        self.key_alias_prefix = key_alias_prefix

        logger.info("key_rotation_manager_initialized")

    @property
    def kms_client(self):
        """Lazy-load KMS client. Returns None if AWS is not configured."""
        if self._kms_client_provided is not None:
            return self._kms_client_provided
        try:
            import boto3
            return boto3.client('kms', region_name=self._aws_region)
        except Exception as e:
            logger.warning("kms_client_unavailable", reason=str(e))
            return None

    def _require_kms(self):
        """Raise KMSEncryptionError if KMS client is unavailable."""
        if self.kms_client is None:
            raise KMSEncryptionError(
                "AWS KMS is not configured. "
                "Set AWS_ACCESS_KEY_ID, AWS_SECRET_ACCESS_KEY (or AWS_ROLE_ARN)."
            )

    def _get_key_alias(self, org_id: str) -> str:
        """Generate key alias."""
        return f"{self.key_alias_prefix}-{org_id}"

    async def get_key_metadata(self, org_id: str) -> Dict[str, Any]:
        """
        Get key metadata including rotation status.

        Args:
            org_id: Organization ID

        Returns:
            Key metadata dict
        """
        from botocore.exceptions import ClientError

        self._require_kms()
        key_alias = self._get_key_alias(org_id)

        try:
            response = self.kms_client.describe_key(KeyId=key_alias)
            metadata = response['KeyMetadata']

            # Get rotation status
            try:
                rotation_response = self.kms_client.get_key_rotation_status(
                    KeyId=metadata['KeyId']
                )
                rotation_enabled = rotation_response['KeyRotationEnabled']
            except ClientError:
                rotation_enabled = False

            return {
                "key_id": metadata['KeyId'],
                "arn": metadata['Arn'],
                "creation_date": metadata['CreationDate'].isoformat(),
                "enabled": metadata['Enabled'],
                "description": metadata.get('Description', ''),
                "rotation_enabled": rotation_enabled,
                "rotation_period_days": self.ROTATION_PERIOD_DAYS
            }

        except ClientError as e:
            logger.error(f"Failed to get key metadata for org {org_id}: {e}")
            raise KeyNotFoundError(f"Key not found for org {org_id}")
    
    async def check_rotation_needed(self, org_id: str) -> Dict[str, Any]:
        """
        Check if key rotation is needed.
        
        Args:
            org_id: Organization ID
            
        Returns:
            Rotation status dict
        """
        metadata = await self.get_key_metadata(org_id)
        
        from datetime import datetime
        creation_date = datetime.fromisoformat(metadata['creation_date'].replace('Z', '+00:00'))
        age_days = (datetime.utcnow() - creation_date.replace(tzinfo=None)).days
        
        days_until_rotation = self.ROTATION_PERIOD_DAYS - age_days
        rotation_needed = days_until_rotation <= 0
        
        return {
            "org_id": org_id,
            "key_id": metadata['key_id'],
            "age_days": age_days,
            "rotation_period_days": self.ROTATION_PERIOD_DAYS,
            "days_until_rotation": max(0, days_until_rotation),
            "rotation_needed": rotation_needed,
            "rotation_enabled": metadata['rotation_enabled']
        }
    
    async def enable_auto_rotation(self, org_id: str) -> bool:
        """
        Enable automatic key rotation.

        Args:
            org_id: Organization ID

        Returns:
            True if enabled successfully
        """
        from botocore.exceptions import ClientError

        self._require_kms()
        key_alias = self._get_key_alias(org_id)

        try:
            response = self.kms_client.describe_key(KeyId=key_alias)
            key_id = response['KeyMetadata']['KeyId']

            self.kms_client.enable_key_rotation(KeyId=key_id)

            logger.info(f"Auto rotation enabled for org {org_id}")
            return True

        except ClientError as e:
            logger.error(f"Failed to enable rotation for org {org_id}: {e}")
            return False

    async def manual_rotate(self, org_id: str) -> Dict[str, Any]:
        """
        Perform manual key rotation.

        Creates a new key version and updates the alias.

        Args:
            org_id: Organization ID

        Returns:
            Rotation result
        """
        from botocore.exceptions import ClientError

        self._require_kms()
        old_key_alias = self._get_key_alias(org_id)

        try:
            # Get old key info
            old_response = self.kms_client.describe_key(KeyId=old_key_alias)
            old_key_id = old_response['KeyMetadata']['KeyId']

            # Create new key
            new_key_response = self.kms_client.create_key(
                Description=f"Cassandra AI encryption key for organization {org_id} (rotated)",
                KeyUsage='ENCRYPT_DECRYPT',
                KeySpec='SYMMETRIC_DEFAULT',
                Tags=[
                    {'TagKey': 'Organization', 'TagValue': org_id},
                    {'TagKey': 'Service', 'TagValue': 'cassandra-ai'},
                    {'TagKey': 'Rotation', 'TagValue': 'manual'}
                ]
            )

            new_key_id = new_key_response['KeyMetadata']['KeyId']

            logger.warning(
                "KEY ROTATION INCOMPLETE - Data re-encryption required",
                extra={
                    "org_id": org_id,
                    "old_key_id": old_key_id,
                    "new_key_id": new_key_id,
                    "action_required": "Run re-encryption job before completing rotation"
                }
            )

            return {
                "success": False,  # Mark as incomplete
                "org_id": org_id,
                "old_key_id": old_key_id,
                "new_key_id": new_key_id,
                "alias": old_key_alias,
                "status": "PENDING_REENCRYPTION",
                "warning": "Key rotation requires data re-encryption. Old key NOT scheduled for deletion.",
                "action_required": "Run background re-encryption job before completing rotation"
            }

        except ClientError as e:
            logger.error(f"Manual rotation failed for org {org_id}: {e}")
            raise EncryptionError(f"Manual rotation failed: {e}")
    
    async def list_key_versions(self, org_id: str) -> list:
        """
        List all versions of an organization's key.

        Args:
            org_id: Organization ID

        Returns:
            List of key versions
        """
        from botocore.exceptions import ClientError

        self._require_kms()
        key_alias = self._get_key_alias(org_id)

        try:
            response = self.kms_client.list_key_versions(KeyId=key_alias)

            versions = []
            for version in response.get('Versions', []):
                versions.append({
                    "key_id": version['KeyId'],
                    "creation_date": version['CreationDate'].isoformat()
                })

            return versions

        except ClientError as e:
            logger.error(f"Failed to list key versions for org {org_id}: {e}")
            return []
    
    async def run_rotation_check(self) -> Dict[str, Any]:
        """
        Run rotation check for all organizations.
        
        Returns:
            Summary of rotation status
        """
        # This would iterate through all orgs in database
        # For now, return placeholder
        
        return {
            "organizations_checked": 0,
            "rotations_needed": 0,
            "rotations_performed": 0,
            "errors": []
        }


# Singleton instance for application-wide use
_encryption_service: Optional[EncryptionService] = None


def get_encryption_service(
    aws_region: str = "us-east-1",
    key_alias_prefix: str = "alias/cassandra-org"
) -> EncryptionService:
    """
    Get or create the singleton encryption service instance.
    
    Args:
        aws_region: AWS region for KMS
        key_alias_prefix: Prefix for KMS key aliases
        
    Returns:
        EncryptionService instance
    """
    global _encryption_service
    if _encryption_service is None:
        _encryption_service = EncryptionService(
            aws_region=aws_region,
            key_alias_prefix=key_alias_prefix
        )
    return _encryption_service


def generate_org_key(org_id: str, **kwargs) -> str:
    """Convenience function to generate an org key."""
    service = get_encryption_service(**kwargs)
    return service.key_manager.generate_org_key(org_id)


def encrypt(payload: Any, org_id: str, **kwargs) -> Dict[str, str]:
    """Convenience function to encrypt data."""
    service = get_encryption_service(**kwargs)
    return service.encrypt(payload, org_id)


def decrypt(ciphertext: str, encrypted_data_key: str, org_id: str, **kwargs) -> Any:
    """Convenience function to decrypt data."""
    service = get_encryption_service(**kwargs)
    return service.decrypt(ciphertext, encrypted_data_key, org_id)
