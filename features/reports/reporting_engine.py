"""
CAT-G: Custom Reports & Analytics (F31-F35)
- F31: On-Demand Voice Report Generation
- F32: SLA Breach Heat Map
- F33: Inspector Productivity Report
- F34: Cost Variance Report — Budget vs Actual
- F35: Tenant Satisfaction Tracker
"""

import asyncio
from typing import Dict, Any, List, Optional
from datetime import datetime

from cassandra.rag.context_fetcher import fetch_full_context, ContextResult
from cassandra.supabase import get_supabase_client


class ReportingEngine:
    """Custom reports and analytics features."""

    def __init__(self, db_client, pdf_engine, notion_client, analytics_service, llm_client=None):
        self.db = db_client
        self.pdf = pdf_engine
        self.notion = notion_client
        self.analytics = analytics_service
        self.llm = llm_client

    # F31: On-Demand Voice Report Generation
    async def generate_voice_report(self, report_type: str, property_id: str,
                                   period: str, org_id: str) -> Dict:
        """Generate report from voice command."""
        # F31: Enrich with dual-read context from Supabase + Supermemory
        context = await fetch_full_context(
            query=f"{report_type} report for property {property_id} during {period}",
            org_id=org_id,
            data_hints=["tickets", "vendors", "checklists"],
            top_k=5,
        )

        # Query DB1
        data = await self._query_report_data(report_type, property_id, period, org_id)

        # LLM narrative synthesis — pass both structured rows and conversational memory
        narrative = await self._synthesize_narrative(
            data,
            report_type,
            supabase_rows=context.supabase_rows,
            memory_chunks=context.memory_chunks,
        )

        # Generate PDF
        pdf_url = await self.pdf.generate(
            template=report_type,
            data=data,
            narrative=narrative
        )

        # Push to Notion
        notion_url = await self.notion.create_report_page(
            title=f"{report_type} - {period}",
            content=narrative,
            attachments=[pdf_url]
        )

        return {
            "pdf_url": pdf_url,
            "notion_url": notion_url,
            "report_type": report_type,
            "period": period,
            "context_sources": context.sources_queried,
            "context_latency_ms": context.latency_ms,
        }

    # F32: SLA Breach Heat Map
    async def generate_sla_heatmap(self, quarter: str, org_id: str) -> Dict:
        """Generate SLA breach heat map."""
        # F32: Add dual-read context for verbal SLA commentary
        context = await fetch_full_context(
            query=f"SLA breaches and response time issues in quarter {quarter}",
            org_id=org_id,
            data_hints=["tickets"],
            top_k=5,
        )

        # Query breach data
        query = """
            SELECT
                p.name as property,
                t.category,
                COUNT(*) as total_tickets,
                COUNT(CASE WHEN t.completed_at > t.deadline THEN 1 END) as breaches,
                COUNT(CASE WHEN t.completed_at > t.deadline THEN 1 END)::float /
                    NULLIF(COUNT(*), 0) as breach_rate
            FROM tickets t
            JOIN properties p ON p.id = t.property_id
            WHERE t.org_id = $1
            AND t.created_at >= DATE_TRUNC('quarter', $2::date)
            AND t.created_at < DATE_TRUNC('quarter', $2::date) + INTERVAL '3 months'
            GROUP BY p.name, t.category
        """
        results = await self.db.fetch(query, org_id, quarter)

        # Build heat map matrix
        heatmap = self._build_heatmap_matrix(results)

        # Annotate heatmap with verbal context from memory
        if context.memory_chunks:
            memory_notes = "\n".join(
                f"- {c.get('content', '')}" for c in context.memory_chunks
            )
            narrative_blurb = (
                f"Additional context from conversational memory:\n{memory_notes}\n"
            )
        else:
            narrative_blurb = ""

        # Export as PNG
        image_url = await self._export_heatmap_png(heatmap, narrative_blurb)

        return {
            "heatmap_data": heatmap,
            "image_url": image_url,
            "properties": list(set(r['property'] for r in results)),
            "categories": list(set(r['category'] for r in results)),
            "memory_context": [c.get("content") for c in context.memory_chunks],
            "context_sources": context.sources_queried,
        }

    # F33: Inspector Productivity Report
    async def generate_inspector_report(self, month: str, org_id: str) -> Dict:
        """Generate inspector productivity report."""
        # F33: Add dual-read context for checklist and asset verbal notes
        context = await fetch_full_context(
            query=f"inspector performance, checklist completions, and asset notes in {month}",
            org_id=org_id,
            data_hints=["checklists", "assets"],
            top_k=5,
        )

        query = """
            SELECT
                u.name as inspector,
                COUNT(DISTINCT ci.id) as checklists_completed,
                COUNT(DISTINCT ce.photo_url) as photos_captured,
                COUNT(DISTINCT t.id) as tickets_raised,
                AVG(EXTRACT(EPOCH FROM (ci.completed_at - c.created_at))/3600) as avg_completion_hours
            FROM users u
            LEFT JOIN checklist_items ci ON ci.completed_by = u.id
                AND ci.completed_at >= DATE_TRUNC('month', $1::date)
            LEFT JOIN checklist_evidence ce ON ce.checklist_item_id = ci.id
            LEFT JOIN tickets t ON t.created_by = u.id
                AND t.created_at >= DATE_TRUNC('month', $1::date)
            WHERE u.org_id = $2
            AND u.role = 'inspector'
            GROUP BY u.id, u.name
            ORDER BY checklists_completed DESC
        """
        results = await self.db.fetch(query, month, org_id)

        return {
            "inspectors": [dict(r) for r in results],
            "month": month,
            "total_inspectors": len(results),
            "memory_context": [c.get("content") for c in context.memory_chunks],
            "context_sources": context.sources_queried,
        }

    # F34: Cost Variance Report
    async def generate_variance_report(self, quarter: str, org_id: str) -> Dict:
        """Generate budget vs actual variance report."""
        # Get budgets from Truth Ledger (Supabase)
        budgets = await self._get_budgets(quarter, org_id)

        # Get actuals from DB1 — ticket costs + checklist costs (Supabase)
        actuals = await self._get_actuals(quarter, org_id)

        # F34: Enrich with dual-read context about verbal budget discussions
        context = await fetch_full_context(
            query=f"budget variance, cost overruns, and financial discussions for quarter {quarter}",
            org_id=org_id,
            data_hints=["budgets", "tickets", "assets"],
            top_k=5,
        )

        # Calculate variance
        variances = []
        for property_id in set(list(budgets.keys()) + list(actuals.keys())):
            budget = budgets.get(property_id, 0)
            actual = actuals.get(property_id, 0)
            variance = actual - budget
            variance_pct = (variance / budget * 100) if budget > 0 else 0

            variances.append({
                "property_id": property_id,
                "budget": budget,
                "actual": actual,
                "variance": variance,
                "variance_percent": variance_pct,
                "flagged": abs(variance_pct) > 15
            })

        # LLM narrative on top 3 — pass both sources
        top_variances = sorted(variances, key=lambda x: abs(x['variance']), reverse=True)[:3]
        narrative = await self._explain_variances(
            top_variances,
            supabase_rows=context.supabase_rows,
            memory_chunks=context.memory_chunks,
        )

        return {
            "variances": variances,
            "top_3": top_variances,
            "narrative": narrative,
            "quarter": quarter,
            "context_sources": context.sources_queried,
            "context_latency_ms": context.latency_ms,
        }

    # F35: Tenant Satisfaction Tracker
    async def generate_satisfaction_report(self, year: str, org_id: str) -> Dict:
        """Generate tenant satisfaction trend report."""
        # F35: Add dual-read context for verbal tenant sentiment
        context = await fetch_full_context(
            query=f"tenant complaints, satisfaction feedback, and service quality in year {year}",
            org_id=org_id,
            data_hints=["tickets"],
            top_k=5,
        )

        # Aggregate complaint data
        query = """
            SELECT
                p.name as property,
                DATE_TRUNC('quarter', t.created_at) as quarter,
                COUNT(*) as complaint_count,
                AVG(EXTRACT(EPOCH FROM (t.completed_at - t.created_at))/3600) as avg_response_hours,
                COUNT(CASE WHEN t.reopened THEN 1 END) as reopen_count
            FROM tickets t
            JOIN properties p ON p.id = t.property_id
            WHERE t.org_id = $1
            AND t.category = 'tenant_complaint'
            AND t.created_at >= DATE_TRUNC('year', $2::date)
            GROUP BY p.name, DATE_TRUNC('quarter', t.created_at)
            ORDER BY p.name, quarter
        """
        results = await self.db.fetch(query, org_id, year)

        # Calculate satisfaction score (0-100)
        properties = {}
        for r in results:
            prop = r['property']
            if prop not in properties:
                properties[prop] = []

            # Score formula
            score = 100
            score -= r['complaint_count'] * 2
            score -= r['avg_response_hours'] * 0.5 if r['avg_response_hours'] else 0
            score -= r['reopen_count'] * 5

            properties[prop].append({
                "quarter": r['quarter'],
                "score": max(score, 0),
                "complaints": r['complaint_count'],
                "response_hours": r['avg_response_hours']
            })

        return {
            "properties": properties,
            "year": year,
            "trend": self._calculate_trend(properties),
            "memory_sentiment": [c.get("content") for c in context.memory_chunks],
            "context_sources": context.sources_queried,
        }

    # Helper methods
    async def _query_report_data(self, report_type: str, property_id: str,
                                period: str, org_id: str) -> Dict:
        """Query report data from Supabase based on report type."""
        client = get_supabase_client("service")

        # Parse period (e.g., "2024-Q1", "2024-01", "2024")
        from datetime import datetime, timedelta

        # Determine date range
        if "Q" in period:  # Quarterly
            year, quarter = period.split("-Q")
            q_starts = {"1": "01-01", "2": "04-01", "3": "07-01", "4": "10-01"}
            q_ends = {"1": "03-31", "2": "06-30", "3": "09-30", "4": "12-31"}
            start_date = f"{year}-{q_starts.get(quarter, '01-01')}"
            end_date = f"{year}-{q_ends.get(quarter, '12-31')}"
        elif len(period) == 7:  # Monthly (YYYY-MM)
            year, month = period.split("-")
            start_date = f"{period}-01"
            # Last day of month
            if month in ["01", "03", "05", "07", "08", "10", "12"]:
                end_date = f"{period}-31"
            elif month in ["04", "06", "09", "11"]:
                end_date = f"{period}-30"
            else:  # February
                end_date = f"{period}-28"
        else:  # Yearly
            start_date = f"{period}-01-01"
            end_date = f"{period}-12-31"

        data = {}

        # Query based on report type
        if report_type in ["maintenance", "facility_health", "operations"]:
            # Get tickets for property in period
            tickets_result = (
                client.table("tickets")
                .select("id, title, status, priority, created_at, resolved_at, category")
                .eq("org_id", org_id)
                .eq("property_id", property_id)
                .gte("created_at", start_date)
                .lte("created_at", end_date)
                .execute()
            )
            data["tickets"] = tickets_result.data if tickets_result.data else []
            data["total_tickets"] = len(data["tickets"])
            data["resolved_tickets"] = sum(1 for t in data["tickets"] if t.get("status") == "resolved")

        if report_type in ["inspection", "compliance"]:
            # Get checklists for property in period
            checklist_result = (
                client.table("checklists")
                .select("id, name, status, total_items, completed_items, created_at")
                .eq("org_id", org_id)
                .eq("property_id", property_id)
                .gte("created_at", start_date)
                .lte("created_at", end_date)
                .execute()
            )
            data["checklists"] = checklist_result.data if checklist_result.data else []

        if report_type in ["financial", "budget"]:
            # Get budget data
            budget_result = (
                client.table("budgets")
                .select("amount, category, period_start, period_end")
                .eq("org_id", org_id)
                .eq("property_id", property_id)
                .gte("period_start", start_date)
                .lte("period_end", end_date)
                .execute()
            )
            data["budgets"] = budget_result.data if budget_result.data else []

        # Get property details
        property_result = (
            client.table("properties")
            .select("id, name, sqft, type, occupancy_rate, monthly_revenue")
            .eq("id", property_id)
            .eq("org_id", org_id)
            .execute()
        )
        if property_result.data:
            data["property"] = property_result.data[0]

        data["period"] = period
        data["start_date"] = start_date
        data["end_date"] = end_date

        return data

    async def _synthesize_narrative(
        self,
        data: Dict,
        report_type: str,
        supabase_rows: Optional[List[Dict]] = None,
        memory_chunks: Optional[List[Dict]] = None,
    ) -> str:
        """Synthesize narrative via LLM, grounding in dual-read context."""
        supabase_rows = supabase_rows or []
        memory_chunks = memory_chunks or []

        # Build comprehensive context from both sources
        context_parts = []

        # Add structured data summary
        if data:
            context_parts.append(f"Structured data for {report_type}:\n{data}")

        # Add Supabase rows
        if supabase_rows:
            context_parts.append(
                f"Related records from database:\n"
                + "\n".join(str(row) for row in supabase_rows[:5])
            )

        # Add conversational memory chunks
        if memory_chunks:
            context_parts.append(
                "Relevant discussions and verbal context:\n"
                + "\n".join(c.get("content", "") for c in memory_chunks[:5])
            )

        # Build LLM prompt
        prompt = f"""You are an expert facility management analyst. Generate a comprehensive narrative report for a {report_type}.

Context and Data:
{chr(10).join(context_parts)}

Please provide:
1. Executive summary (2-3 sentences)
2. Key findings and metrics
3. Notable trends or patterns
4. Actionable recommendations (3-5 items)

Format the output in clear, professional language suitable for facility management stakeholders."""

        try:
            # Call LLM to generate narrative
            if hasattr(self, 'llm') and self.llm:
                response = await self.llm.generate(prompt)
                if isinstance(response, dict):
                    narrative = response.get("content", str(response))
                else:
                    narrative = str(response)
            else:
                # Fallback if LLM not available
                narrative = self._generate_fallback_narrative(
                    report_type, data, supabase_rows, memory_chunks
                )
        except Exception as e:
            # Fallback on error
            narrative = self._generate_fallback_narrative(
                report_type, data, supabase_rows, memory_chunks
            )

        return narrative

    def _generate_fallback_narrative(
        self,
        report_type: str,
        data: Dict,
        supabase_rows: List[Dict],
        memory_chunks: List[Dict],
    ) -> str:
        """Generate basic narrative when LLM is unavailable."""
        parts = [f"# {report_type.replace('_', ' ').title()} Report\n"]

        # Executive summary
        parts.append("## Executive Summary")
        parts.append(f"This report covers {report_type} with {len(supabase_rows)} database records and {len(memory_chunks)} contextual references.\n")

        # Key metrics from data
        if data:
            parts.append("## Key Metrics")
            for key, value in list(data.items())[:5]:
                parts.append(f"- {key}: {value}")
            parts.append("")

        # Database context
        if supabase_rows:
            parts.append(f"## Database Records ({len(supabase_rows)} records)")
            parts.append("Data extracted from Supabase tables.")
            parts.append("")

        # Conversational context
        if memory_chunks:
            parts.append(f"## Conversational Context ({len(memory_chunks)} references)")
            for i, chunk in enumerate(memory_chunks[:3], 1):
                content = chunk.get("content", "")
                if content:
                    preview = content[:100] + "..." if len(content) > 100 else content
                    parts.append(f"{i}. {preview}")
            parts.append("")

        # Recommendations
        parts.append("## Recommendations")
        parts.append("1. Review detailed data for specific insights")
        parts.append("2. Monitor key metrics for trends")
        parts.append("3. Follow up on flagged items")

        return "\n".join(parts)

    def _build_heatmap_matrix(self, results: List) -> List:
        """Build heat map matrix from SLA breach query results."""
        if not results:
            return []

        # Extract unique properties and categories
        properties = sorted(list(set(r['property'] for r in results)))
        categories = sorted(list(set(r['category'] for r in results)))

        # Build matrix as list of lists
        # Each row represents a property, each column a category
        matrix = []

        for prop in properties:
            row = {"property": prop, "breach_rates": {}}
            for cat in categories:
                # Find matching result
                matching = [r for r in results if r['property'] == prop and r['category'] == cat]
                if matching:
                    breach_rate = matching[0].get('breach_rate', 0)
                else:
                    breach_rate = 0
                row["breach_rates"][cat] = round(breach_rate * 100, 1)  # Convert to percentage
            matrix.append(row)

        return matrix

    async def _export_heatmap_png(self, heatmap: List, narrative_blurb: str = "") -> str:
        """Export heat map as PNG using matplotlib/seaborn."""
        if not heatmap:
            return ""

        try:
            import matplotlib.pyplot as plt
            import seaborn as sns
            import pandas as pd
            import io
            import base64
            from datetime import datetime

            # Convert heatmap to DataFrame
            properties = [row["property"] for row in heatmap]
            if not heatmap[0]["breach_rates"]:
                return ""

            categories = list(heatmap[0]["breach_rates"].keys())
            data = []
            for row in heatmap:
                data.append([row["breach_rates"].get(cat, 0) for cat in categories])

            df = pd.DataFrame(data, index=properties, columns=categories)

            # Create heatmap
            plt.figure(figsize=(12, 8))
            sns.heatmap(
                df,
                annot=True,
                fmt=".1f",
                cmap="RdYlGn_r",  # Red-Yellow-Green reversed (red = high breach rate)
                center=15,  # Center the color scale at 15%
                vmin=0,
                vmax=50,
                cbar_kws={'label': 'SLA Breach Rate (%)'}
            )

            plt.title("SLA Breach Heat Map: Properties × Categories", fontsize=14, fontweight='bold')
            plt.xlabel("Ticket Category", fontsize=12)
            plt.ylabel("Property", fontsize=12)
            plt.tight_layout()

            # Add narrative blurb if provided
            if narrative_blurb:
                plt.figtext(
                    0.5, 0.02, narrative_blurb[:200],
                    ha="center", fontsize=8, wrap=True
                )

            # Save to bytes
            buf = io.BytesIO()
            plt.savefig(buf, format='png', dpi=150, bbox_inches='tight')
            buf.seek(0)
            plt.close()

            # For now, return a data URL (in production, upload to S3/storage)
            img_base64 = base64.b64encode(buf.read()).decode('utf-8')
            data_url = f"data:image/png;base64,{img_base64}"

            # In production, you would upload this to cloud storage:
            # filename = f"heatmap_{datetime.now().strftime('%Y%m%d_%H%M%S')}.png"
            # s3_url = await upload_to_s3(buf, filename)
            # return s3_url

            return data_url[:100]  # Return truncated for display (full data URL can be very long)

        except ImportError:
            # If matplotlib/seaborn not available, return placeholder
            return "heatmap_placeholder.png"
        except Exception as e:
            return f"error_generating_heatmap: {str(e)}"

    # F34 — implemented with Supabase
    async def _get_budgets(self, quarter: str, org_id: str) -> Dict[str, float]:
        """
        Get budgeted amounts per property from the budgets table.

        SECURITY: Every query uses .eq("org_id", org_id) — no exceptions.
        """
        client = get_supabase_client("service")

        # Map quarter string (e.g. "2024-Q1") to date range
        year, q_num = quarter.split("-Q")
        q_start_map = {
            "1": f"{year}-01-01",
            "2": f"{year}-04-01",
            "3": f"{year}-07-01",
            "4": f"{year}-10-01",
        }
        q_start = q_start_map.get(q_num, f"{year}-01-01")

        response = (
            client.table("budgets")
            .select("property_id, amount, category")
            .eq("org_id", org_id)
            .gte("period_start", q_start)
            .execute()
        )

        budgets: Dict[str, float] = {}
        for row in response.data:
            prop_id = row.get("property_id")
            if prop_id:
                budgets[prop_id] = budgets.get(prop_id, 0) + float(row.get("amount", 0))

        return budgets

    # F34 — implemented with Supabase
    async def _get_actuals(self, quarter: str, org_id: str) -> Dict[str, float]:
        """
        Calculate actual costs per property by summing ticket costs and checklist costs.

        SECURITY: Every query uses .eq("org_id", org_id) — no exceptions.
        """
        client = get_supabase_client("service")

        # Map quarter string to date range
        year, q_num = quarter.split("-Q")
        q_start_map = {
            "1": f"{year}-01-01",
            "2": f"{year}-04-01",
            "3": f"{year}-07-01",
            "4": f"{year}-10-01",
        }
        q_start = q_start_map.get(q_num, f"{year}-01-01")
        # End of quarter
        q_end_map = {
            "1": f"{year}-03-31",
            "2": f"{year}-06-30",
            "3": f"{year}-09-30",
            "4": f"{year}-12-31",
        }
        q_end = q_end_map.get(q_num, f"{year}-12-31")

        actuals: Dict[str, float] = {}

        # Actual costs from tickets
        ticket_response = (
            client.table("tickets")
            .select("property_id, estimated_cost, actual_cost")
            .eq("org_id", org_id)
            .gte("created_at", q_start)
            .lte("created_at", q_end)
            .execute()
        )
        for row in ticket_response.data:
            prop_id = row.get("property_id")
            cost = float(row.get("actual_cost") or row.get("estimated_cost") or 0)
            if prop_id:
                actuals[prop_id] = actuals.get(prop_id, 0) + cost

        # Actual costs from checklist completions
        checklist_response = (
            client.table("checklist_items")
            .select("property_id, cost")
            .eq("org_id", org_id)
            .not_.is_("completed_at", "null")
            .gte("completed_at", q_start)
            .lte("completed_at", q_end)
            .execute()
        )
        for row in checklist_response.data:
            prop_id = row.get("property_id")
            cost = float(row.get("cost") or 0)
            if prop_id:
                actuals[prop_id] = actuals.get(prop_id, 0) + cost

        return actuals

    async def _explain_variances(
        self,
        top_variances: List,
        supabase_rows: Optional[List[Dict]] = None,
        memory_chunks: Optional[List[Dict]] = None,
    ) -> str:
        """Explain top variances via LLM, grounded in dual-read context."""
        supabase_rows = supabase_rows or []
        memory_chunks = memory_chunks or []

        if not top_variances:
            return "No significant budget variances to report."

        # Build context from both sources
        context_parts = []

        # Add variance data
        variance_summary = []
        for v in top_variances:
            variance_summary.append(
                f"Property {v.get('property_id')}: "
                f"Budget ${v.get('budget', 0):,.2f}, "
                f"Actual ${v.get('actual', 0):,.2f}, "
                f"Variance ${v.get('variance', 0):,.2f} ({v.get('variance_percent', 0):.1f}%)"
            )
        context_parts.append("Top Budget Variances:\n" + "\n".join(variance_summary))

        # Add database context
        if supabase_rows:
            context_parts.append(
                f"\nRelated database records ({len(supabase_rows)} records):\n"
                + "\n".join(str(row)[:200] for row in supabase_rows[:3])
            )

        # Add conversational memory
        if memory_chunks:
            context_parts.append(
                f"\nRelated discussions and context ({len(memory_chunks)} references):\n"
                + "\n".join(c.get("content", "")[:200] for c in memory_chunks[:3])
            )

        # Build LLM prompt
        prompt = f"""You are a financial analyst specializing in facility management budgets.

Analyze the following budget variances:

{chr(10).join(context_parts)}

Please provide:
1. Root cause analysis for each major variance
2. Whether each variance is concerning or expected
3. Specific recommendations for budget management
4. Any patterns or trends across properties

Format your response in clear, professional language for facility management stakeholders."""

        try:
            # Call LLM to generate explanation
            if self.llm:
                response = await self.llm.generate(prompt)
                if isinstance(response, dict):
                    explanation = response.get("content", str(response))
                else:
                    explanation = str(response)
            else:
                # Fallback if LLM not available
                explanation = self._generate_fallback_variance_explanation(
                    top_variances, supabase_rows, memory_chunks
                )
        except Exception as e:
            # Fallback on error
            explanation = self._generate_fallback_variance_explanation(
                top_variances, supabase_rows, memory_chunks
            )

        return explanation

    def _generate_fallback_variance_explanation(
        self,
        top_variances: List,
        supabase_rows: List[Dict],
        memory_chunks: List[Dict],
    ) -> str:
        """Generate basic variance explanation when LLM is unavailable."""
        parts = ["# Budget Variance Analysis\n"]

        parts.append("## Summary")
        parts.append(f"Analyzing {len(top_variances)} properties with significant budget variances.\n")

        parts.append("## Top Variances")
        for i, v in enumerate(top_variances, 1):
            property_id = v.get('property_id', 'Unknown')
            budget = v.get('budget', 0)
            actual = v.get('actual', 0)
            variance = v.get('variance', 0)
            variance_pct = v.get('variance_percent', 0)
            flagged = v.get('flagged', False)

            status = "⚠️ FLAGGED" if flagged else "✓ Normal"
            over_under = "over budget" if variance > 0 else "under budget"

            parts.append(f"\n### {i}. Property {property_id} - {status}")
            parts.append(f"- Budgeted: ${budget:,.2f}")
            parts.append(f"- Actual: ${actual:,.2f}")
            parts.append(f"- Variance: ${abs(variance):,.2f} {over_under} ({abs(variance_pct):.1f}%)")

        # Add context
        if supabase_rows:
            parts.append(f"\n## Database Context")
            parts.append(f"Analysis includes {len(supabase_rows)} related database records.")

        if memory_chunks:
            parts.append(f"\n## Additional Context")
            parts.append(f"Found {len(memory_chunks)} relevant discussions about budget and costs.")

        # Recommendations
        parts.append("\n## Recommendations")
        parts.append("1. Review flagged properties with variances exceeding 15%")
        parts.append("2. Identify recurring cost patterns driving overages")
        parts.append("3. Update budget forecasts based on actual spending trends")
        parts.append("4. Implement cost controls for properties consistently over budget")

        return "\n".join(parts)

    def _calculate_trend(self, properties: Dict) -> str:
        """Calculate satisfaction trend across quarters."""
        if not properties:
            return "No Data"

        # Calculate trend by comparing quarter-over-quarter scores
        trends = []

        for prop_name, quarters in properties.items():
            if len(quarters) < 2:
                continue  # Need at least 2 quarters to determine trend

            # Sort quarters chronologically
            sorted_quarters = sorted(quarters, key=lambda x: x.get("quarter", ""))

            # Calculate average score change
            score_changes = []
            for i in range(1, len(sorted_quarters)):
                prev_score = sorted_quarters[i-1].get("score", 0)
                curr_score = sorted_quarters[i].get("score", 0)
                change = curr_score - prev_score
                score_changes.append(change)

            if score_changes:
                avg_change = sum(score_changes) / len(score_changes)
                trends.append(avg_change)

        if not trends:
            return "Stable"

        # Calculate overall trend
        overall_change = sum(trends) / len(trends)

        # Classify trend
        if overall_change > 5:
            return "Significantly Improving"
        elif overall_change > 1:
            return "Improving"
        elif overall_change > -1:
            return "Stable"
        elif overall_change > -5:
            return "Declining"
        else:
            return "Significantly Declining"
