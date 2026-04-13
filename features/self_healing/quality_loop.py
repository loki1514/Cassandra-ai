"""
CAT-F: Self-Healing Intelligence Loop (F26-F30)
- F26: Answer Quality Logger — Notion Integration
- F27: Weekly Failure Pattern Analysis
- F28: Synonym & Alias Learning
- F29: Confidence Calibration Tracker
- F30: Prompt A/B Testing Framework
"""

from typing import Dict, Any, List
from datetime import datetime, timedelta

from cassandra.rag.context_fetcher import fetch_full_context, ContextResult
from cassandra.supabase import get_supabase_client


class SelfHealingService:
    """Self-healing intelligence loop features."""
    
    def __init__(self, notion_client, db_client, memory_manager, llm_client):
        self.notion = notion_client
        self.db = db_client
        self.memory = memory_manager
        self.llm = llm_client
    
    # F26: Answer Quality Logger
    async def log_quality_issue(self, query: str, cassandra_answer: str, 
                                correct_answer: str, failure_category: str,
                                org_id: str) -> Dict:
        """Log quality issue to Notion failure log."""
        # Create Notion entry
        notion_data = {
            "Query": query,
            "Answer Given": cassandra_answer,
            "Correct Answer": correct_answer,
            "Failure Category": failure_category,
            "Timestamp": datetime.now().isoformat(),
            "Org ID": org_id
        }
        
        entry_id = await self.notion.create_page(
            database_id="failure_log",
            properties=notion_data
        )
        
        return {"success": True, "notion_entry_id": entry_id}
    
    # F27: Weekly Failure Pattern Analysis
    async def analyze_weekly_failures(self, org_id: str) -> Dict:
        """Analyze failures and propose prompt improvements using dual-read."""
        # Dual-read: get any verbal discussions about failures
        context = await fetch_full_context(
            query="failure analysis quality improvement",
            org_id=org_id,
            data_hints=["tickets"],
            top_k=5,
        )

        # Supabase: query tickets table for failed/closed tickets in last 7 days
        client = get_supabase_client("service")
        cutoff = (datetime.now() - timedelta(days=7)).isoformat()
        result = (
            client.table("tickets")
            .select(
                "id, title, description, status, category, priority, "
                "response_time, reopened, property_id, created_at"
            )
            .eq("org_id", org_id)
            .in_("status", ["failed", "closed"])
            .gte("created_at", cutoff)
            .execute()
        )
        failures = result.data if result.data else []

        # Group failures by category
        category_map: Dict[str, List[Dict]] = {}
        for f in failures:
            cat = f.get("category") or f.get("title", "uncategorized")
            category_map.setdefault(cat, []).append(f)

        # Compute failure rate per category
        total_tickets_result = (
            client.table("tickets")
            .select("id", count="exact")
            .eq("org_id", org_id)
            .gte("created_at", cutoff)
            .execute()
        )
        total_tickets = total_tickets_result.count or 1

        category_stats = []
        for cat, items in category_map.items():
            failure_rate = len(items) / total_tickets
            avg_response = (
                sum(t.get("response_time") or 0 for t in items) / len(items)
                if items
                else 0
            )
            reopen_count = sum(1 for t in items if t.get("reopened"))
            category_stats.append({
                "category": cat,
                "failure_count": len(items),
                "failure_rate": failure_rate,
                "avg_response_time": avg_response,
                "reopen_count": reopen_count,
            })

        # Sort by failure rate descending
        category_stats.sort(key=lambda x: x["failure_rate"], reverse=True)

        # LLM clustering — pass both DB rows and memory chunks
        clusters = await self._cluster_failures(category_stats, context)

        # Generate proposals for each cluster
        proposals = []
        for cluster in clusters:
            proposal = await self._generate_proposal(cluster)
            proposals.append(proposal)

        # Post to Notion (keep existing Notion integration)
        await self.notion.create_page(
            database_id="improvement_proposals",
            properties={
                "Title": f"Weekly Improvement Report - {datetime.now().strftime('%Y-%m-%d')}",
                "Clusters": len(clusters),
                "Proposals": proposals,
                "Date": datetime.now().isoformat(),
            },
        )

        return {
            "clusters": clusters,
            "proposals": proposals,
            "category_stats": category_stats,
            "supabase_rows": failures,
            "memory_chunks": context.memory_chunks,
            "total_failures": len(failures),
            "failure_rate": len(failures) / total_tickets,
        }
    
    # F28: Synonym & Alias Learning
    async def learn_synonym(self, canonical_term: str, alias: str,
                           org_id: str) -> Dict:
        """Learn entity synonym from user correction using dual-read."""
        # Dual-read: check existing synonyms before inserting
        context = await fetch_full_context(
            query=f"synonym {canonical_term} {alias}",
            org_id=org_id,
            data_hints=["synonyms"],
            top_k=5,
        )

        # Also check Supabase for existing synonyms
        client = get_supabase_client("service")
        existing = (
            client.table("entity_synonyms")
            .select("id, canonical_term, alias")
            .eq("org_id", org_id)
            .or_(f"canonical_term.eq.{canonical_term},alias.eq.{alias}")
            .execute()
        )
        existing_data = existing.data if existing.data else []

        # If already exists (exact match), skip insert
        already_exists = any(
            e.get("canonical_term") == canonical_term and e.get("alias") == alias
            for e in existing_data
        )
        if already_exists:
            return {
                "success": True,
                "canonical": canonical_term,
                "alias": alias,
                "action": "already_exists",
                "existing_rows": existing_data,
                "memory_chunks": context.memory_chunks,
            }

        # Insert new synonym via Supabase
        client.table("entity_synonyms").insert({
            "canonical_term": canonical_term,
            "alias": alias,
            "org_id": org_id,
            "learned_at": datetime.now().isoformat(),
        }).execute()

        # Update extraction prompt dynamically
        await self._update_extraction_prompt(org_id)

        return {
            "success": True,
            "canonical": canonical_term,
            "alias": alias,
            "action": "inserted",
            "existing_synonyms": existing_data,
            "supabase_rows": context.supabase_rows,
            "memory_chunks": context.memory_chunks,
        }
    
    # F29: Confidence Calibration Tracker
    async def track_calibration(self, predicted_confidence: float,
                                outcome: str, org_id: str) -> Dict:
        """Track predicted confidence vs actual accuracy using dual-read."""
        # Dual-read: get verbal mentions of calibration issues
        context = await fetch_full_context(
            query="calibration confidence accuracy",
            org_id=org_id,
            data_hints=["answer_logs", "tickets"],
            top_k=5,
        )

        # Log prediction to Supabase
        client = get_supabase_client("service")
        client.table("confidence_calibration").insert({
            "predicted_confidence": predicted_confidence,
            "outcome": outcome,
            "org_id": org_id,
            "timestamp": datetime.now().isoformat(),
        }).execute()

        # Compute accuracy rate per query type from answer_logs (last 30 days)
        cutoff = (datetime.now() - timedelta(days=30)).isoformat()
        logs_result = (
            client.table("answer_logs")
            .select("id, query_type, outcome, predicted_confidence, org_id")
            .eq("org_id", org_id)
            .gte("timestamp", cutoff)
            .execute()
        )
        logs = logs_result.data if logs_result.data else []

        # Group by query_type and compute accuracy
        query_type_map: Dict[str, List[Dict]] = {}
        for log in logs:
            qt = log.get("query_type") or "unknown"
            query_type_map.setdefault(qt, []).append(log)

        calibration_stats = []
        for qt, items in query_type_map.items():
            correct = sum(
                1 for item in items
                if item.get("outcome") in ("correct", "accurate", "helpful")
            )
            accuracy = correct / len(items) if items else 0
            avg_predicted = (
                sum(item.get("predicted_confidence") or 0 for item in items)
                / len(items)
            )
            calibration_stats.append({
                "query_type": qt,
                "total_queries": len(items),
                "accuracy": accuracy,
                "avg_predicted_confidence": avg_predicted,
                "calibration_error": abs(avg_predicted - accuracy),
            })

        # Sort by calibration error descending (most miscalibrated first)
        calibration_stats.sort(key=lambda x: x["calibration_error"], reverse=True)

        # LLM-driven weekly summary
        weekly = await self._calculate_calibration(
            org_id, calibration_stats, context
        )

        return {
            "logged": True,
            "calibration_stats": calibration_stats,
            "weekly_calibration": weekly,
            "supabase_rows": logs,
            "memory_chunks": context.memory_chunks,
        }

    # F30: Prompt A/B Testing Framework
    async def start_ab_test(self, prompt_variant: str, control_prompt: str,
                           test_name: str) -> Dict:
        """Start A/B test for prompt variant."""
        # Store in Redis
        await self._set_ab_flag(test_name, {
            "control": control_prompt,
            "variant": prompt_variant,
            "started_at": datetime.now().isoformat(),
            "queries_routed": 0
        })
        
        return {"test_name": test_name, "status": "running"}
    
    async def route_query_for_ab(self, query: str, test_name: str) -> Dict:
        """Route query to A or B variant."""
        flag = await self._get_ab_flag(test_name)
        
        # 50/50 split
        import random
        use_variant = random.random() > 0.5
        
        prompt = flag["variant"] if use_variant else flag["control"]
        variant = "B" if use_variant else "A"
        
        # Track
        flag["queries_routed"] += 1
        await self._set_ab_flag(test_name, flag)
        
        return {"prompt": prompt, "variant": variant}
    
    async def evaluate_ab_test(self, test_name: str, org_id: str = None) -> Dict:
        """Evaluate A/B test results using dual-read context for variant analysis."""
        # Dual-read context for variant selection rationale
        context = None
        if org_id:
            context = await fetch_full_context(
                query=f"AB test {test_name} variant performance",
                org_id=org_id,
                data_hints=["answer_logs"],
                top_k=5,
            )

        # Get accuracy for each variant
        results = await self._get_ab_results(test_name)

        control_accuracy = results.get("A", {}).get("accuracy", 0)
        variant_accuracy = results.get("B", {}).get("accuracy", 0)

        # Auto-promote if variant wins
        if variant_accuracy > control_accuracy + 0.03:
            await self._promote_prompt(test_name)
            return {
                "winner": "variant",
                "control_accuracy": control_accuracy,
                "variant_accuracy": variant_accuracy,
                "auto_promoted": True,
                "supabase_rows": context.supabase_rows if context else [],
                "memory_chunks": context.memory_chunks if context else [],
            }

        return {
            "winner": "control" if control_accuracy >= variant_accuracy else "variant",
            "control_accuracy": control_accuracy,
            "variant_accuracy": variant_accuracy,
            "auto_promoted": False,
            "supabase_rows": context.supabase_rows if context else [],
            "memory_chunks": context.memory_chunks if context else [],
        }
    
    # Helper methods
    async def _cluster_failures(
        self, category_stats: List[Dict], context: ContextResult
    ) -> List:
        """Cluster failures by pattern using LLM + dual-read context."""
        if not category_stats:
            return []

        # Build context for LLM
        context_parts = [f"Categories: {category_stats}"]
        if context.memory_chunks:
            context_parts.append(
                "Related discussions:\n"
                + "\n".join(c.get("content", "") for c in context.memory_chunks[:3])
            )

        prompt = (
            "You are a quality analyst. Given the following failure category statistics "
            "from a self-healing AI system:\n"
            + "\n".join(context_parts)
            + "\n\nGroup these into 3-5 meaningful clusters based on root cause similarity. "
            "Return a JSON list of clusters, each with 'name', 'root_cause', and 'affected_categories'."
        )

        try:
            response = await self.llm.generate(prompt)
            content = response.get("content", response) if isinstance(response, dict) else str(response)
            # Try simple extraction
            clusters = []
            lines = content.split("\n")
            current_cluster = None
            for line in lines:
                if ":" in line and not line.startswith("-"):
                    name = line.split(":")[0].strip().strip('"')
                    if name:
                        current_cluster = {"name": name, "root_cause": "", "affected_categories": []}
                        clusters.append(current_cluster)
                elif current_cluster and ("affected" in line or "categories" in line or "categories" in line):
                    cats = [c.strip().strip(",-") for c in line.split() if c.strip()]
                    current_cluster["affected_categories"] = cats
        except Exception:
            # Fallback: cluster by failure rate bands
            clusters = [
                {
                    "name": "High Failure Rate",
                    "root_cause": "Systematic quality issue",
                    "affected_categories": [
                        c["category"] for c in category_stats if c["failure_rate"] > 0.3
                    ],
                },
                {
                    "name": "Medium Failure Rate",
                    "root_cause": "Edge case handling needed",
                    "affected_categories": [
                        c["category"]
                        for c in category_stats
                        if 0.1 <= c["failure_rate"] <= 0.3
                    ],
                },
                {
                    "name": "Low Failure Rate",
                    "root_cause": "Minor anomalies",
                    "affected_categories": [
                        c["category"] for c in category_stats if c["failure_rate"] < 0.1
                    ],
                },
            ]

        return clusters
    
    async def _generate_proposal(self, cluster: Dict) -> Dict:
        """Generate improvement proposal using LLM."""
        prompt = (
            f"Given this failure cluster: {cluster.get('name', '')}\n"
            f"Root cause: {cluster.get('root_cause', '')}\n"
            f"Affected categories: {cluster.get('affected_categories', [])}\n\n"
            f"Propose 2-3 specific, actionable improvements to the prompt/retrieval system. "
            f"Format as: 'action: <description>', 'expected_impact: <description>'."
        )
        try:
            response = await self.llm.generate(prompt)
            content = response.get("content", response) if isinstance(response, dict) else str(response)
            return {
                "cluster": cluster.get("name", ""),
                "proposal_text": content,
                "root_cause": cluster.get("root_cause", ""),
            }
        except Exception:
            return {
                "cluster": cluster.get("name", ""),
                "proposal_text": "Review prompt templates for affected categories.",
                "root_cause": cluster.get("root_cause", ""),
            }
    
    async def _update_extraction_prompt(self, org_id: str):
        """Update extraction prompt with learned synonyms from Supabase."""
        client = get_supabase_client("service")

        # Fetch all synonyms for this org
        result = (
            client.table("entity_synonyms")
            .select("canonical_term, alias, learned_at")
            .eq("org_id", org_id)
            .order("learned_at", desc=True)
            .execute()
        )

        synonyms = result.data if result.data else []

        if not synonyms:
            return

        # Build synonym mapping for prompt injection
        synonym_map = {}
        for row in synonyms:
            canonical = row.get("canonical_term", "")
            alias = row.get("alias", "")
            if canonical and alias:
                synonym_map.setdefault(canonical, []).append(alias)

        # Store updated prompt template in org_settings or similar
        # For now, we'll just update a settings record
        prompt_addition = "\n\nKnown entity synonyms:\n"
        for canonical, aliases in synonym_map.items():
            prompt_addition += f"- {canonical}: {', '.join(aliases)}\n"

        # Update org settings with new extraction prompt
        client.table("org_settings").upsert({
            "org_id": org_id,
            "setting_key": "extraction_prompt_synonyms",
            "setting_value": prompt_addition,
            "updated_at": datetime.now().isoformat()
        }).execute()
    
    async def _calculate_calibration(
        self,
        org_id: str,
        calibration_stats: List[Dict] = None,
        context: ContextResult = None,
    ) -> Dict:
        """Calculate weekly calibration summary using LLM + dual-read context."""
        stats = calibration_stats or []
        if not stats:
            return {"summary": "No calibration data available."}

        # Build LLM prompt with stats and memory
        context_parts = [f"Query type calibrations: {stats}"]
        if context and context.memory_chunks:
            context_parts.append(
                "Related discussions:\n"
                + "\n".join(c.get("content", "") for c in context.memory_chunks[:3])
            )

        prompt = (
            "You are a calibration analyst. Given the following per-query-type "
            "calibration statistics over the last 30 days:\n"
            + "\n".join(context_parts)
            + "\n\nProvide a concise weekly calibration summary: "
            "overall accuracy, most miscalibrated query types, "
            "and 1-2 recommended actions to improve calibration."
        )

        try:
            response = await self.llm.generate(prompt)
            content = response.get("content", response) if isinstance(response, dict) else str(response)
        except Exception:
            content = "Calibration summary unavailable."

        return {
            "summary": content,
            "most_miscalibrated": [s["query_type"] for s in stats[:3]],
            "overall_accuracy": (
                sum(s["accuracy"] for s in stats) / len(stats) if stats else 0
            ),
        }
    
    async def _set_ab_flag(self, test_name: str, data: Dict):
        """Set A/B test flag in Supabase ab_tests table."""
        client = get_supabase_client("service")

        # Upsert test configuration
        client.table("ab_tests").upsert({
            "test_name": test_name,
            "control_prompt": data.get("control"),
            "variant_prompt": data.get("variant"),
            "started_at": data.get("started_at"),
            "queries_routed": data.get("queries_routed", 0),
            "status": "running",
            "updated_at": datetime.now().isoformat()
        }).execute()

    async def _get_ab_flag(self, test_name: str) -> Dict:
        """Get A/B test flag from Supabase."""
        client = get_supabase_client("service")

        result = (
            client.table("ab_tests")
            .select("*")
            .eq("test_name", test_name)
            .eq("status", "running")
            .execute()
        )

        if result.data:
            return result.data[0]

        return {
            "control": "",
            "variant": "",
            "started_at": None,
            "queries_routed": 0
        }

    async def _get_ab_results(self, test_name: str) -> Dict:
        """Get A/B test results from Supabase answer_logs table."""
        client = get_supabase_client("service")

        # Query answer logs for this test
        result = (
            client.table("answer_logs")
            .select("variant, outcome, confidence")
            .eq("ab_test_name", test_name)
            .execute()
        )

        logs = result.data if result.data else []

        # Calculate accuracy per variant
        variant_stats = {"A": {"total": 0, "correct": 0}, "B": {"total": 0, "correct": 0}}

        for log in logs:
            variant = log.get("variant", "A")
            outcome = log.get("outcome", "")

            if variant in variant_stats:
                variant_stats[variant]["total"] += 1
                if outcome in ("correct", "accurate", "helpful"):
                    variant_stats[variant]["correct"] += 1

        # Calculate accuracy
        results = {}
        for variant, stats in variant_stats.items():
            total = stats["total"]
            correct = stats["correct"]
            accuracy = (correct / total) if total > 0 else 0
            results[variant] = {
                "total_queries": total,
                "correct_count": correct,
                "accuracy": accuracy
            }

        return results

    async def _promote_prompt(self, test_name: str):
        """Promote winning prompt by updating system prompts table."""
        client = get_supabase_client("service")

        # Get the test details
        test_result = (
            client.table("ab_tests")
            .select("variant_prompt, control_prompt")
            .eq("test_name", test_name)
            .execute()
        )

        if not test_result.data:
            return

        test = test_result.data[0]
        winning_prompt = test.get("variant_prompt", "")

        # Update the production prompt
        client.table("system_prompts").upsert({
            "prompt_name": test_name,
            "prompt_text": winning_prompt,
            "promoted_at": datetime.now().isoformat(),
            "source": "ab_test_winner",
            "status": "active"
        }).execute()

        # Mark test as completed
        client.table("ab_tests").update({
            "status": "completed",
            "completed_at": datetime.now().isoformat()
        }).eq("test_name", test_name).execute()