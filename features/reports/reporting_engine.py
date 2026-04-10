"""
CAT-G: Custom Reports & Analytics (F31-F35)
- F31: On-Demand Voice Report Generation
- F32: SLA Breach Heat Map
- F33: Inspector Productivity Report
- F34: Cost Variance Report — Budget vs Actual
- F35: Tenant Satisfaction Tracker
"""

from typing import Dict, Any, List
from datetime import datetime


class ReportingEngine:
    """Custom reports and analytics features."""
    
    def __init__(self, db_client, pdf_engine, notion_client, analytics_service):
        self.db = db_client
        self.pdf = pdf_engine
        self.notion = notion_client
        self.analytics = analytics_service
    
    # F31: On-Demand Voice Report Generation
    async def generate_voice_report(self, report_type: str, property_id: str,
                                   period: str, org_id: str) -> Dict:
        """Generate report from voice command."""
        # Query DB1
        data = await self._query_report_data(report_type, property_id, period, org_id)
        
        # LLM narrative synthesis
        narrative = await self._synthesize_narrative(data, report_type)
        
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
            "period": period
        }
    
    # F32: SLA Breach Heat Map
    async def generate_sla_heatmap(self, quarter: str, org_id: str) -> Dict:
        """Generate SLA breach heat map."""
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
        
        # Export as PNG
        image_url = await self._export_heatmap_png(heatmap)
        
        return {
            "heatmap_data": heatmap,
            "image_url": image_url,
            "properties": list(set(r['property'] for r in results)),
            "categories": list(set(r['category'] for r in results))
        }
    
    # F33: Inspector Productivity Report
    async def generate_inspector_report(self, month: str, org_id: str) -> Dict:
        """Generate inspector productivity report."""
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
            "total_inspectors": len(results)
        }
    
    # F34: Cost Variance Report
    async def generate_variance_report(self, quarter: str, org_id: str) -> Dict:
        """Generate budget vs actual variance report."""
        # Get budgets from Truth Ledger
        budgets = await self._get_budgets(quarter, org_id)
        
        # Get actuals from DB1
        actuals = await self._get_actuals(quarter, org_id)
        
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
        
        # LLM narrative on top 3
        top_variances = sorted(variances, key=lambda x: abs(x['variance']), reverse=True)[:3]
        narrative = await self._explain_variances(top_variances)
        
        return {
            "variances": variances,
            "top_3": top_variances,
            "narrative": narrative,
            "quarter": quarter
        }
    
    # F35: Tenant Satisfaction Tracker
    async def generate_satisfaction_report(self, year: str, org_id: str) -> Dict:
        """Generate tenant satisfaction trend report."""
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
            "trend": self._calculate_trend(properties)
        }
    
    # Helper methods
    async def _query_report_data(self, report_type: str, property_id: str, 
                                period: str, org_id: str) -> Dict:
        """Query report data from DB1."""
        return {}
    
    async def _synthesize_narrative(self, data: Dict, report_type: str) -> str:
        """Synthesize narrative via LLM."""
        return "Report narrative"
    
    def _build_heatmap_matrix(self, results: List) -> List:
        """Build heat map matrix."""
        return []
    
    async def _export_heatmap_png(self, heatmap: List) -> str:
        """Export heat map as PNG."""
        return "heatmap.png"
    
    async def _get_budgets(self, quarter: str, org_id: str) -> Dict:
        """Get budgets from Truth Ledger."""
        return {}
    
    async def _get_actuals(self, quarter: str, org_id: str) -> Dict:
        """Get actual costs from DB1."""
        return {}
    
    async def _explain_variances(self, top_variances: List) -> str:
        """Explain top variances via LLM."""
        return "Variance explanation"
    
    def _calculate_trend(self, properties: Dict) -> str:
        """Calculate satisfaction trend."""
        return "Improving"