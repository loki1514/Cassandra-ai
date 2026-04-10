"""
Cassandra AI - RAG (Retrieval-Augmented Generation) Module

This module provides memory management, context retrieval, and truth ledger
functionality for the Cassandra AI ticket resolution system.

Exports:
    MemoryManager: Core memory operations with Supermemory integration
    ContextFetcher: Semantic search and context resolution
    generate_idempotency_key: Idempotency key generation for deduplication
    check_idempotency: Check if event has already been processed
    TruthLedger: Ground truth event tracking and confidence scoring
    OrphanReconciler: Daily orphan detection and re-linking
    ConflictResolver: DB1-wins conflict resolution
    DeepHistorian: Async fact extraction from transcripts
    ProvenanceTracker: Source metadata and UI data

Usage:
    from cassandra.rag import MemoryManager, ContextFetcher, TruthLedger
"""

from .memory_manager import MemoryManager, MemoryEntry, MemorySearchResult
from .context_fetcher import (
    ContextFetcher,
    FetchContextInput,
    FetchContextResult,
    resolve_query,
    QueryResolutionError
)
from .idempotency import (
    generate_idempotency_key,
    check_idempotency,
    IdempotencyStore,
    IdempotencyKey
)
from .truth_ledger import (
    TruthLedger,
    TruthEvent,
    EntityType,
    ConfidenceLevel,
    ReviewStatus,
    DeepHistorian,
    DeepHistorianConfig,
    TranscriptSegment,
    ExtractedFact,
    TruthLedgerController
)
from .reconciliation import (
    OrphanReconciler,
    OrphanStatus,
    ReconciliationResult,
    DailyReconciliationJob,
    run_daily_reconciliation
)
from .conflict_resolver import (
    ConflictResolver,
    merge_context,
    ConflictType,
    ResolutionStrategy,
    ConflictResolutionError
)
from .provenance import (
    ProvenanceTracker,
    ProvenanceInfo,
    SourceAttribution,
    SourceType,
    ConfidenceDisplay,
    LedgerVersion,
    build_response_provenance,
    get_confidence_display,
    create_source_attribution
)

__all__ = [
    # Memory Manager
    'MemoryManager',
    'MemoryEntry',
    'MemorySearchResult',
    
    # Context Fetcher (T21)
    'ContextFetcher',
    'FetchContextInput',
    'FetchContextResult',
    'resolve_query',
    'QueryResolutionError',
    
    # Idempotency
    'generate_idempotency_key',
    'check_idempotency',
    'IdempotencyStore',
    'IdempotencyKey',
    
    # Truth Ledger (T38)
    'TruthLedger',
    'TruthEvent',
    'EntityType',
    'ConfidenceLevel',
    'ReviewStatus',
    'DeepHistorian',
    'DeepHistorianConfig',
    'TranscriptSegment',
    'ExtractedFact',
    'TruthLedgerController',
    
    # Reconciliation (T30)
    'OrphanReconciler',
    'OrphanStatus',
    'ReconciliationResult',
    'DailyReconciliationJob',
    'run_daily_reconciliation',
    
    # Conflict Resolver (T24)
    'ConflictResolver',
    'merge_context',
    'ConflictType',
    'ResolutionStrategy',
    'ConflictResolutionError',
    
    # Provenance (T39)
    'ProvenanceTracker',
    'ProvenanceInfo',
    'SourceAttribution',
    'SourceType',
    'ConfidenceDisplay',
    'LedgerVersion',
    'build_response_provenance',
    'get_confidence_display',
    'create_source_attribution',
]

__version__ = '1.0.0'
__author__ = 'Cassandra AI Team'
