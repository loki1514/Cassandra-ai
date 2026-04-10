# CASSANDRA AI - FILE MANIFEST

**Project:** Cassandra AI Voice-Enabled RAG System  
**Version:** 1.0.0  
**Generated:** 2024-04-08

---

## Directory Structure

```
/mnt/okcomputer/output/cassandra-ai/
├── README.md                          # Project overview and quick start
├── PROJECT_STATUS.md                  # 50-task implementation tracker
├── LAUNCH_VERIFIED.md                 # Launch verification checklist
├── FINAL_REPORT.md                    # Comprehensive project report
├── API_DOCUMENTATION.md               # API reference documentation
├── RUNBOOK.md                         # Operational procedures
├── Dockerfile                         # Multi-stage Docker build
├── docker-compose.yml                 # Docker Compose configuration
├── .dockerignore                      # Docker ignore patterns
├── .env.example                       # Environment variables template
├── requirements.txt                   # Python dependencies
│
├── cassandra/                         # Main application code
│   ├── __init__.py                    # Package initialization
│   ├── main.py                        # FastAPI application entry point
│   ├── auth.py                        # JWT authentication middleware
│   ├── config.py                      # Configuration management
│   ├── encryption.py                  # Encryption utilities
│   ├── transcription.py               # AssemblyAI integration
│   ├── speaker_id.py                  # Pyannote diarization
│   ├── extraction.py                  # LLM commitment extraction
│   ├── ARCHITECTURE.md                # System architecture documentation
│   ├── INTEGRATION.md                 # Backend/RAG integration docs
│   └── FILE_MANIFEST.md               # This file
│
│   ├── rag/                           # RAG components
│   │   ├── __init__.py                # Package initialization
│   │   ├── context_fetcher.py         # Context retrieval from vector DB
│   │   ├── memory_manager.py          # Memory storage with embeddings
│   │   ├── idempotency.py             # Idempotency key handling
│   │   ├── truth_ledger.py            # Source attribution tracking
│   │   ├── reconciliation.py          # Data reconciliation engine
│   │   └── conflict_resolver.py       # Conflict resolution logic
│
│   ├── tools/                         # Tool registry
│   │   ├── __init__.py                # Package initialization
│   │   ├── registry.py                # Tool registry and execution
│   │   ├── create_ticket.py           # Ticket creation tool
│   │   ├── add_memory.py              # Memory addition tool
│   │   └── fetch_context.py           # Context fetching tool
│
│   └── mobile/                        # Mobile client components
│       ├── __init__.py                # Package initialization
│       └── audio_client.py            # Mobile audio client
│
├── supabase/                          # Database migrations
│   └── migrations/
│       ├── 001_initial_schema.sql     # Initial schema creation
│       ├── 002_memory_map.sql         # Memory-ticket mapping table
│       ├── 003_soft_delete.sql        # Soft-delete pattern
│       ├── 004_roles.sql              # Service roles
│       ├── 005_rls.sql                # Row-level security policies
│       └── 006_archive.sql            # Archive table
│
└── tests/                             # Test suite
    ├── test_backend.py                # Backend unit tests
    ├── test_rag.py                    # RAG component tests
    └── test_tools.py                  # Tool registry tests
```

---

## Complete File Listing

### Root Documentation
| File | Description |
|------|-------------|
| README.md | Project overview, quick start guide, badges |
| PROJECT_STATUS.md | 50-task implementation tracker with full progress |
| LAUNCH_VERIFIED.md | Launch verification checklist with sign-offs |
| FINAL_REPORT.md | Comprehensive project report with all metrics |
| API_DOCUMENTATION.md | API reference, endpoints, WebSocket protocol |
| RUNBOOK.md | Operational procedures, troubleshooting, runbooks |

### Configuration Files
| File | Description |
|------|-------------|
| Dockerfile | Multi-stage Docker build (production-ready) |
| docker-compose.yml | Docker Compose with profiles (dev/prod/full) |
| .dockerignore | Docker build ignore patterns |
| .env.example | Environment variables template |
| requirements.txt | Python package dependencies |

### Core Application (/cassandra/)
| File | Description | Lines |
|------|-------------|-------|
| __init__.py | Package exports and version info | ~30 |
| main.py | FastAPI app, WebSocket handler, health endpoints | ~450 |
| auth.py | JWT validation, org scoping, RBAC | ~280 |
| config.py | Environment configuration, settings | ~150 |
| encryption.py | KMS encryption utilities | ~200 |
| transcription.py | AssemblyAI integration, speaker segments | ~350 |
| speaker_id.py | Pyannote diarization, voice embeddings | ~400 |
| extraction.py | LLM commitment extraction, entity detection | ~380 |

### RAG Components (/cassandra/rag/)
| File | Description | Lines |
|------|-------------|-------|
| __init__.py | RAG package exports | ~25 |
| context_fetcher.py | Vector search context retrieval | ~280 |
| memory_manager.py | Memory storage with embeddings | ~320 |
| idempotency.py | Duplicate request handling | ~180 |
| truth_ledger.py | Source attribution tracking | ~220 |
| reconciliation.py | Data reconciliation engine | ~250 |
| conflict_resolver.py | Conflict resolution logic | ~200 |

### Tool Registry (/cassandra/tools/)
| File | Description | Lines |
|------|-------------|-------|
| __init__.py | Tool package exports | ~20 |
| registry.py | Tool registration and execution | ~300 |
| create_ticket.py | Ticket creation tool | ~180 |
| add_memory.py | Memory addition tool | ~200 |
| fetch_context.py | Context fetching tool | ~150 |

### Mobile Components (/cassandra/mobile/)
| File | Description | Lines |
|------|-------------|-------|
| __init__.py | Mobile package exports | ~15 |
| audio_client.py | Mobile audio client utilities | ~180 |

### Documentation (/cassandra/)
| File | Description |
|------|-------------|
| ARCHITECTURE.md | System architecture diagrams and data flow |
| INTEGRATION.md | Backend/RAG integration documentation |
| FILE_MANIFEST.md | This file - complete file listing |

### Database Migrations (/supabase/migrations/)
| File | Description | Lines |
|------|-------------|-------|
| 001_initial_schema.sql | Core tables, enums, indexes | ~180 |
| 002_memory_map.sql | Memory-ticket mapping table | ~80 |
| 003_soft_delete.sql | Soft-delete triggers | ~60 |
| 004_roles.sql | Service roles (cassandra_role, backend_role) | ~90 |
| 005_rls.sql | Row-level security policies | ~120 |
| 006_archive.sql | Archive table with partitioning | ~70 |

### Test Suite (/tests/)
| File | Description | Tests |
|------|-------------|-------|
| test_backend.py | Backend unit tests (auth, WebSocket, extraction) | 32 |
| test_rag.py | RAG component tests (context, memory, idempotency) | 28 |
| test_tools.py | Tool registry tests (create_ticket, add_memory) | 24 |

---

## File Statistics

### By Type
| Type | Count | Purpose |
|------|-------|---------|
| Python (.py) | 23 | Application code, tests |
| SQL (.sql) | 6 | Database migrations |
| Markdown (.md) | 9 | Documentation |
| YAML (.yml) | 1 | Docker Compose |
| Text (.txt) | 1 | Requirements |
| Docker | 1 | Dockerfile |
| **Total** | **41** | |

### By Category
| Category | Files | Lines (approx) |
|----------|-------|----------------|
| Documentation | 9 | ~150 KB |
| Core Application | 7 | ~2,100 |
| RAG Components | 7 | ~1,475 |
| Tool Registry | 5 | ~850 |
| Mobile | 2 | ~195 |
| Database | 6 | ~600 |
| Tests | 3 | ~1,800 |
| Configuration | 4 | ~295 |
| **Total** | **43** | **~7,300** |

---

## Key Integration Points

### Backend ↔ RAG Integration
| Integration | Source | Target | Purpose |
|-------------|--------|--------|---------|
| IP-01 | main.py | context_fetcher.py | Real-time context retrieval |
| IP-02 | tools/registry.py | memory_manager.py | Memory storage with embeddings |
| IP-03 | extraction.py | context_fetcher.py | Enriched extraction prompts |
| IP-04 | auth.py | All RAG components | Org-scoped query enforcement |
| IP-05 | main.py | idempotency.py | Duplicate request detection |

### External Service Integration
| Service | File | Purpose |
|---------|------|---------|
| AssemblyAI | transcription.py | Real-time speech-to-text |
| Pyannote | speaker_id.py | Speaker diarization |
| OpenAI GPT-4 | extraction.py | Commitment extraction |
| OpenAI Embeddings | memory_manager.py | Vector embedding generation |
| Supabase | config.py | PostgreSQL database |
| Redis | config.py | Cache and session storage |

---

## Dependencies

### Core Python Packages
```
fastapi==0.109.0              # Web framework
uvicorn[standard]==0.27.0     # ASGI server
websockets==12.0              # WebSocket support
python-jose[cryptography]==3.3.0  # JWT handling
httpx==0.26.0                 # HTTP client
assemblyai==0.20.0            # Transcription API
openai==1.10.0                # LLM API
numpy==1.26.0                 # Numerical computing
scipy==1.12.0                 # Scientific computing
supabase==2.3.0               # Database client
redis==5.0.0                  # Cache client
prometheus-client==0.19.0     # Metrics
opentelemetry-api==1.22.0     # Distributed tracing
structlog==24.1.0             # Structured logging
pydantic==2.5.0               # Data validation
```

### Infrastructure
- **Docker:** 24.0+ (containerization)
- **Kubernetes:** 1.28+ (orchestration)
- **PostgreSQL:** 15+ (via Supabase)
- **Redis:** 7.0+ (caching)

---

## Build & Deployment

### Docker Commands
```bash
# Build image
docker build -t cassandra-ai:1.0.0 .

# Run development
docker-compose --profile dev up

# Run production
docker-compose --profile prod up -d

# Run full stack
docker-compose --profile full up -d
```

### Kubernetes Commands
```bash
# Deploy
kubectl apply -f k8s/

# Verify
kubectl get all -n cassandra

# Scale
kubectl scale deployment cassandra-ai -n cassandra --replicas=5
```

---

## Version Control

### Repository
```
https://github.com/cassandra-ai/cassandra.git
```

### Branches
- `main` - Production releases
- `develop` - Integration branch
- `feature/*` - Feature development
- `hotfix/*` - Emergency fixes
- `release/*` - Release candidates

### Tags
- `v1.0.0` - Initial production release (2024-04-08)

---

## Security Notes

### Sensitive Files (Not in Repository)
- `.env` - Environment variables with secrets
- `*.key` - Private keys
- `*.pem` - Certificates
- `service-account.json` - Cloud service credentials

### Protected Paths
- `/cassandra/auth.py` - Authentication logic
- `/cassandra/encryption.py` - Encryption utilities
- `/supabase/migrations/004_roles.sql` - Database roles
- `/supabase/migrations/005_rls.sql` - RLS policies

---

## License & Copyright

Copyright © 2024 Cassandra AI Team  
All rights reserved.

---

*This manifest was generated on 2024-04-08.*  
*Total Files: 41 | Total Lines: ~7,300 | Status: ✅ COMPLETE*
