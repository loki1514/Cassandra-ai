# CASSANDRA AI - OPERATIONAL RUNBOOK

## Document Information
- **System:** Cassandra AI Voice-Enabled RAG System
- **Version:** 1.0.0
- **Last Updated:** 2024-01-15
- **Owner:** DevOps Team
- **On-Call Escalation:** See [Escalation Matrix](#escalation-matrix)

---

## Table of Contents
1. [System Overview](#system-overview)
2. [Escalation Matrix](#escalation-matrix)
3. [Secret Rotation Procedures](#secret-rotation-procedures)
4. [Backup & Restore Procedures](#backup--restore-procedures)
5. [Incident Response Guidelines](#incident-response-guidelines)
6. [Common Issues & Resolution](#common-issues--resolution)
7. [Health Check Commands](#health-check-commands)
8. [Maintenance Windows](#maintenance-windows)

---

## System Overview

### Architecture Components
```
┌─────────────────────────────────────────────────────────────┐
│                        CLIENTS                               │
│              (Web App, Mobile, Phone)                        │
└──────────────────────┬──────────────────────────────────────┘
                       │ WebSocket
                       ▼
┌─────────────────────────────────────────────────────────────┐
│                    KUBERNETES CLUSTER                        │
│  ┌─────────────┐  ┌─────────────┐  ┌─────────────────────┐  │
│  │  FastAPI    │  │   FastAPI   │  │      FastAPI        │  │
│  │  Pod 1      │  │   Pod 2     │  │      Pod N          │  │
│  └──────┬──────┘  └──────┬──────┘  └──────────┬──────────┘  │
└─────────┼────────────────┼────────────────────┼─────────────┘
          │                │                    │
          └────────────────┴────────────────────┘
                           │
           ┌───────────────┼───────────────┐
           ▼               ▼               ▼
    ┌────────────┐  ┌────────────┐  ┌────────────┐
    │  Supabase  │  │   Redis    │  │  Assembly  │
    │ PostgreSQL │  │   Cache    │  │     AI     │
    └────────────┘  └────────────┘  └────────────┘
```

### Critical Dependencies
| Service | Purpose | Failure Impact | SLA |
|---------|---------|----------------|-----|
| Supabase PostgreSQL | Primary data store | Complete outage | 99.9% |
| Redis | Session/cache | Degraded performance | 99.9% |
| AssemblyAI | Transcription | No voice processing | 99.5% |
| Pyannote | Diarization | No speaker ID | 99.5% |
| OpenAI/LLM | Extraction/NLG | Limited functionality | 99.5% |

---

## Escalation Matrix

### Severity Levels

#### SEV 1 - Critical (System Down)
- Complete service outage
- Data loss or corruption
- Security breach
- **Response Time:** 15 minutes
- **Resolution Target:** 1 hour

#### SEV 2 - High (Major Impact)
- Significant functionality impaired
- Performance severely degraded
- Multiple customers affected
- **Response Time:** 30 minutes
- **Resolution Target:** 4 hours

#### SEV 3 - Medium (Partial Impact)
- Single component failure
- Minor functionality issues
- Workarounds available
- **Response Time:** 2 hours
- **Resolution Target:** 24 hours

#### SEV 4 - Low (Minimal Impact)
- Cosmetic issues
- Feature requests
- Documentation updates
- **Response Time:** Next business day
- **Resolution Target:** 1 week

### Contact Information

| Role | Primary | Secondary | Contact Method |
|------|---------|-----------|----------------|
| On-Call Engineer | TBD | TBD | PagerDuty |
| Backend Architect | TBD | TBD | Slack/Phone |
| RAG Specialist | TBD | TBD | Slack/Phone |
| Project Manager | TBD | TBD | Slack/Phone |
| Security Lead | TBD | TBD | Slack/Phone |

---

## Secret Rotation Procedures

### Overview
All secrets must be rotated every 90 days or immediately if compromised.

### Secret Inventory

| Secret Name | Location | Rotation Frequency | Last Rotated |
|-------------|----------|-------------------|--------------|
| SUPABASE_SERVICE_ROLE_KEY | Vault | 90 days | Never |
| SUPABASE_ANON_KEY | Vault | 90 days | Never |
| ASSEMBLYAI_API_KEY | Vault | 90 days | Never |
| OPENAI_API_KEY | Vault | 90 days | Never |
| JWT_SECRET | Vault | 90 days | Never |
| REDIS_PASSWORD | Vault | 90 days | Never |
| KMS_KEY_ID | AWS IAM | 365 days | Never |

### Rotation Procedure: Database Credentials

#### Step 1: Pre-Rotation Checklist
```bash
# Verify backup is current
kubectl exec -it cassandra-ai-pod -- pg_dump $DATABASE_URL > backup_$(date +%Y%m%d).sql

# Check active connections
kubectl exec -it cassandra-ai-pod -- psql $DATABASE_URL -c "SELECT count(*) FROM pg_stat_activity;"
```

#### Step 2: Generate New Credentials
```bash
# In Supabase Dashboard:
# 1. Go to Project Settings > Database
# 2. Click "Reset Database Password"
# 3. Copy new password
```

#### Step 3: Update Vault
```bash
# Update secret in Vault
vault kv put secret/cassandra-ai/supabase \
  password="NEW_PASSWORD" \
  rotation_date="$(date -Iseconds)"
```

#### Step 4: Rolling Restart
```bash
# Rolling restart to pick up new credentials
kubectl rollout restart deployment/cassandra-ai

# Verify pods are healthy
kubectl rollout status deployment/cassandra-ai
kubectl get pods -l app=cassandra-ai
```

#### Step 5: Verify
```bash
# Test database connectivity
kubectl exec -it deployment/cassandra-ai -- python -c "
import psycopg2
conn = psycopg2.connect(os.environ['DATABASE_URL'])
cursor = conn.cursor()
cursor.execute('SELECT 1')
print('Connection successful')
"
```

#### Step 6: Update Documentation
- Update secret inventory with new rotation date
- Document any issues encountered

### Rotation Procedure: API Keys

#### AssemblyAI API Key Rotation
```bash
# 1. Generate new key in AssemblyAI Dashboard
# 2. Update Vault
vault kv put secret/cassandra-ai/assemblyai \
  api_key="NEW_API_KEY" \
  rotation_date="$(date -Iseconds)"

# 3. Rolling restart
kubectl rollout restart deployment/cassandra-ai

# 4. Test transcription
curl -X POST https://api.assemblyai.com/v2/transcript \
  -H "authorization: NEW_API_KEY" \
  -H "content-type: application/json" \
  -d '{"audio_url": "TEST_URL"}'

# 5. Revoke old key in AssemblyAI Dashboard
```

### Emergency Rotation (Compromised Secret)

If a secret is compromised:

1. **Immediate (0-5 minutes):**
   ```bash
   # Scale down to prevent further usage
   kubectl scale deployment/cassandra-ai --replicas=0
   ```

2. **Short-term (5-30 minutes):**
   - Rotate the compromised secret
   - Update all dependent systems
   - Review audit logs for unauthorized access

3. **Medium-term (30 minutes - 4 hours):**
   - Scale back up
   - Monitor for anomalies
   - Notify stakeholders

4. **Long-term (4+ hours):**
   - Post-incident review
   - Update procedures
   - Security assessment

---

## Backup & Restore Procedures

### Backup Strategy

#### Automated Backups
| Type | Frequency | Retention | Storage |
|------|-----------|-----------|---------|
| Full Database | Daily | 30 days | S3 (encrypted) |
| Incremental | Hourly | 7 days | S3 (encrypted) |
| WAL Archiving | Continuous | 7 days | S3 (encrypted) |

#### Manual Backup Commands
```bash
# Full database backup
pg_dump $DATABASE_URL > cassandra_backup_$(date +%Y%m%d_%H%M%S).sql

# Specific table backup
pg_dump $DATABASE_URL --table=memory_ticket_map > memory_backup_$(date +%Y%m%d).sql

# Compressed backup
pg_dump $DATABASE_URL | gzip > cassandra_backup_$(date +%Y%m%d).sql.gz
```

### Restore Procedures

#### Scenario 1: Point-in-Time Recovery (Full Restore)
```bash
# 1. Identify restore point
RESTORE_DATE="2024-01-15 10:00:00"

# 2. Stop application
kubectl scale deployment/cassandra-ai --replicas=0

# 3. Download backup from S3
aws s3 cp s3://cassandra-backups/full/20240115_000000.sql.gz ./
gunzip 20240115_000000.sql.gz

# 4. Restore database
psql $DATABASE_URL < 20240115_000000.sql

# 5. Apply WAL logs for point-in-time recovery
# (If using continuous archiving)

# 6. Verify restore
psql $DATABASE_URL -c "SELECT count(*) FROM memory_ticket_map;"

# 7. Start application
kubectl scale deployment/cassandra-ai --replicas=3
```

#### Scenario 2: Single Table Restore
```bash
# 1. Backup current table (just in case)
pg_dump $DATABASE_URL --table=memory_ticket_map > memory_current_$(date +%Y%m%d).sql

# 2. Drop and recreate table
psql $DATABASE_URL -c "DROP TABLE memory_ticket_map;"

# 3. Restore from backup
psql $DATABASE_URL < memory_backup_20240115.sql

# 4. Verify
psql $DATABASE_URL -c "SELECT count(*) FROM memory_ticket_map;"
```

#### Scenario 3: Corrupted Data Recovery
```bash
# 1. Identify corrupted records
psql $DATABASE_URL -c "
SELECT id, org_id, created_at 
FROM memory_ticket_map 
WHERE metadata IS NULL 
   OR org_id IS NULL;
"

# 2. Restore from archive if available
# (Using soft-delete pattern - no data is truly lost)

# 3. If needed, restore from backup to staging
# and merge valid records
```

### Backup Verification
```bash
# Weekly backup verification (automated)
#!/bin/bash
# backup_verify.sh

BACKUP_FILE=$1

# Download and restore to staging
psql $STAGING_DATABASE_URL < $BACKUP_FILE

# Run verification queries
psql $STAGING_DATABASE_URL -c "
SELECT 
  'memory_ticket_map' as table_name,
  count(*) as row_count,
  max(created_at) as latest_record
FROM memory_ticket_map;
"

# Compare with production counts
# Alert if significant difference
```

---

## Incident Response Guidelines

### Incident Response Process

```
┌─────────────┐    ┌─────────────┐    ┌─────────────┐    ┌─────────────┐
│   DETECT    │───▶│   RESPOND   │───▶│   RESOLVE   │───▶│   REVIEW    │
└─────────────┘    └─────────────┘    └─────────────┘    └─────────────┘
      │                  │                  │                  │
      ▼                  ▼                  ▼                  ▼
 Alert fired       Assess impact       Fix issue         Post-mortem
 On-call paged     Communicate         Verify fix          Document
                   Mitigate            Monitor             Update runbook
```

### Common Incident Types

#### INC-001: Database Connection Pool Exhaustion
**Symptoms:**
- Error: "FATAL: sorry, too many clients already"
- High latency
- Connection timeouts

**Immediate Response:**
```bash
# Check current connections
psql $DATABASE_URL -c "
SELECT state, count(*) 
FROM pg_stat_activity 
GROUP BY state;
"

# Kill idle connections if necessary
psql $DATABASE_URL -c "
SELECT pg_terminate_backend(pid) 
FROM pg_stat_activity 
WHERE state = 'idle' 
  AND state_change < NOW() - INTERVAL '10 minutes';
"
```

**Resolution:**
- Increase connection pool size in PgBouncer
- Review connection leaks in application code
- Scale database if needed

---

#### INC-002: Redis Cache Failure
**Symptoms:**
- High database load
- Increased latency
- Cache miss rate spike

**Immediate Response:**
```bash
# Check Redis health
redis-cli -h $REDIS_HOST ping

# Check memory usage
redis-cli -h $REDIS_HOST info memory

# If Redis is down, restart
kubectl rollout restart deployment/redis
```

**Resolution:**
- Restart Redis pod
- Verify persistence
- Check for memory pressure

---

#### INC-003: AssemblyAI API Degradation
**Symptoms:**
- Transcription failures
- High latency on voice calls
- Error rate spike

**Immediate Response:**
```bash
# Check AssemblyAI status
curl https://status.assemblyai.com/api/v2/status.json

# Enable circuit breaker (if not automatic)
# Fallback to local transcription model if available
```

**Resolution:**
- Enable circuit breaker
- Queue jobs for retry
- Communicate with users

---

#### INC-004: Security Incident - Suspicious Activity
**Symptoms:**
- Unusual API traffic patterns
- Failed authentication attempts
- Unexpected data access

**Immediate Response:**
```bash
# Check audit logs
psql $DATABASE_URL -c "
SELECT * FROM audit_log 
WHERE created_at > NOW() - INTERVAL '1 hour'
ORDER BY created_at DESC;
"

# Block suspicious IPs if identified
# Rotate credentials if compromise suspected
# Scale down if severe
```

**Resolution:**
- Follow security incident procedure
- Notify security team
- Document all actions
- Post-incident review

---

### Incident Communication Template

#### Internal Slack Message
```
:alert: INCIDENT ALERT :alert:
Severity: [SEV 1/2/3/4]
Service: [Service Name]
Impact: [Description of impact]
Started: [Timestamp]
On-Call: [@engineer]
Status: [Investigating/Identified/Monitoring/Resolved]

Updates in #incidents-[incident-id]
```

#### Customer Communication (if needed)
```
Subject: Service Degradation - Cassandra AI

We are currently experiencing [issue] affecting [impact].
Our team is actively working on resolution.

Status Page: [link]
Estimated Resolution: [time]

We apologize for any inconvenience.
```

---

## Common Issues & Resolution

### Issue: High Latency on Voice Processing

**Diagnostic Steps:**
```bash
# Check component latencies
kubectl logs deployment/cassandra-ai | grep "latency"

# Check database performance
psql $DATABASE_URL -c "
SELECT query, mean_exec_time 
FROM pg_stat_statements 
ORDER BY mean_exec_time DESC 
LIMIT 10;
"

# Check Redis hit rate
redis-cli -h $REDIS_HOST info stats | grep keyspace
```

**Common Causes:**
1. Database query optimization needed
2. Cache miss rate high
3. External API latency
4. Insufficient resources

**Resolution:**
- Add database indexes
- Tune cache TTL
- Enable circuit breakers
- Scale horizontally

---

### Issue: Speaker Identification Accuracy Low

**Diagnostic Steps:**
```bash
# Check embedding quality
# Review Pyannote logs
kubectl logs deployment/cassandra-ai | grep "diarization"

# Verify voice profile enrollment
psql $DATABASE_URL -c "
SELECT org_id, count(*) as profile_count 
FROM voice_profiles 
GROUP BY org_id;
"
```

**Common Causes:**
1. Insufficient voice profile data
2. Background noise
3. Multiple speakers overlapping

**Resolution:**
- Improve audio preprocessing
- Retrain on customer data
- Manual speaker correction workflow

---

## Health Check Commands

### Application Health
```bash
# Health endpoint
curl https://api.cassandra.ai/health

# Readiness probe
curl https://api.cassandra.ai/ready

# Metrics endpoint
curl https://api.cassandra.ai/metrics
```

### Database Health
```bash
# Connection test
psql $DATABASE_URL -c "SELECT 1;"

# Table statistics
psql $DATABASE_URL -c "
SELECT 
  schemaname,
  tablename,
  n_tup_ins,
  n_tup_upd,
  n_tup_del
FROM pg_stat_user_tables
ORDER BY n_tup_ins DESC;
"

# Lock monitoring
psql $DATABASE_URL -c "
SELECT 
  blocked_locks.pid AS blocked_pid,
  blocked_activity.usename AS blocked_user,
  blocking_locks.pid AS blocking_pid
FROM pg_catalog.pg_locks blocked_locks
JOIN pg_catalog.pg_stat_activity blocked_activity ON blocked_activity.pid = blocked_locks.pid
JOIN pg_catalog.pg_locks blocking_locks ON blocking_locks.locktype = blocked_locks.locktype
WHERE NOT blocked_locks.granted;
"
```

### Kubernetes Health
```bash
# Pod status
kubectl get pods -l app=cassandra-ai

# Resource usage
kubectl top pods -l app=cassandra-ai

# Events
kubectl get events --sort-by='.lastTimestamp' | tail -20

# Logs
kubectl logs -f deployment/cassandra-ai --tail=100
```

---

## Maintenance Windows

### Scheduled Maintenance

| Window | Frequency | Activities |
|--------|-----------|------------|
| Sunday 02:00-04:00 UTC | Weekly | Database maintenance, index rebuilds |
| First Sunday 02:00-06:00 UTC | Monthly | Major updates, schema migrations |
| Quarterly | As needed | Security patches, major version upgrades |

### Maintenance Procedure

1. **Pre-Maintenance (24 hours before):**
   - Notify stakeholders
   - Verify backups
   - Prepare rollback plan

2. **During Maintenance:**
   - Set maintenance mode
   - Execute changes
   - Verify functionality
   - Monitor metrics

3. **Post-Maintenance:**
   - Disable maintenance mode
   - Run smoke tests
   - Monitor for 2 hours
   - Update documentation

### Maintenance Mode
```bash
# Enable maintenance mode
kubectl set env deployment/cassandra-ai MAINTENANCE_MODE=true

# Show maintenance page
kubectl apply -f k8s/maintenance-ingress.yaml

# Disable maintenance mode
kubectl set env deployment/cassandra-ai MAINTENANCE_MODE=false
kubectl apply -f k8s/production-ingress.yaml
```

---

## Document Control

| Version | Date | Author | Changes |
|---------|------|--------|---------|
| 1.0 | 2024-01-15 | DevOps Team | Initial creation |

---

## Appendix

### Quick Reference Card

```
EMERGENCY CONTACTS
==================
On-Call: [TBD]
Backend Architect: [TBD]
RAG Specialist: [TBD]
Security: [TBD]

USEFUL COMMANDS
===============
Logs: kubectl logs -f deployment/cassandra-ai
Health: curl https://api.cassandra.ai/health
Restart: kubectl rollout restart deployment/cassandra-ai
Scale: kubectl scale deployment/cassandra-ai --replicas=N

CRITICAL URLs
=============
Production: https://api.cassandra.ai
Grafana: https://grafana.cassandra.ai
Status Page: https://status.cassandra.ai
```

---

*This runbook is a living document. Update it after every incident.*
