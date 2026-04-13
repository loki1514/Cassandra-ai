"""
F15: Energy Consumption Anomaly Detection

Detects abnormal energy consumption patterns by comparing readings
against expected baselines, and surfaces verbal reports of odd
equipment behaviour from Supermemory that may explain or pre-date spikes.

Dual-read: Supabase (energy_readings, assets) + Supermemory (verbal context).
"""

from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional

import numpy as np
import structlog

from cassandra.rag.context_fetcher import fetch_full_context, ContextResult

logger = structlog.get_logger("cassandra.facility.energy_anomaly")

# Flag readings more than this many std devs above the mean
ANOMALY_ZSCORE_THRESHOLD = 2.0
# Number of days of history to pull
LOOKBACK_DAYS = 30


@dataclass
class EnergyAnomaly:
    """Single energy anomaly detection result."""
    asset_name: str
    asset_id: str
    anomaly_date: str
    kwh_recorded: float
    kwh_expected: float
    deviation_pct: float
    verbal_reports: List[str]
    likely_cause: str
    zscore: Optional[float] = None


@dataclass
class AnomalyReport:
    """Full energy anomaly detection report."""
    anomalies: List[EnergyAnomaly] = field(default_factory=list)
    org_id: str = ""


class EnergyAnomalyDetector:
    """
    F15: Energy Consumption Anomaly Detection.

    For each asset, computes a rolling 7-day average kWh and flags
    any day where the reading exceeds 2 standard deviations above
    the asset's baseline (expected_kwh_per_day).

    Usage:
        detector = EnergyAnomalyDetector(db_pool)
        report = await detector.detect(org_id="org_abc123")
    """

    def __init__(self, db_pool: Any):
        self.db_pool = db_pool

    async def detect(
        self,
        org_id: str,
        lookback_days: int = LOOKBACK_DAYS,
        zscore_threshold: float = ANOMALY_ZSCORE_THRESHOLD,
        top_k: int = 50,
    ) -> AnomalyReport:
        """
        Detect energy anomalies across all assets.

        Args:
            org_id: Organization ID (required, from JWT)
            lookback_days: Days of history to analyze
            zscore_threshold: Number of std devs to flag as anomaly
            top_k: Maximum readings to analyze per asset

        Returns:
            AnomalyReport with detected anomalies
        """
        result = AnomalyReport(org_id=org_id)

        # Dual-read: Supabase + Supermemory
        context: ContextResult = await fetch_full_context(
            query="energy electricity consumption equipment running hot unusual noise "
                  "HVAC compressor behaving oddly power spike",
            org_id=org_id,
            data_hints=["energy_readings", "assets"],
            top_k=top_k,
        )

        assets = [r for r in context.supabase_rows if r.get("_source_table") == "assets"]
        readings = [r for r in context.supabase_rows if r.get("_source_table") == "energy_readings"]

        # Build asset lookup
        asset_by_id: Dict[str, Dict] = {
            a.get("asset_id"): a for a in assets if a.get("asset_id")
        }

        # Index readings by asset_id and sort by timestamp
        readings_by_asset: Dict[str, List[Dict]] = {}
        for reading in readings:
            aid = reading.get("asset_id")
            if aid:
                readings_by_asset.setdefault(aid, []).append(reading)

        # Verbal reports from Supermemory
        verbal_chunks = [c.get("content", "") for c in context.memory_chunks]

        cutoff = datetime.utcnow() - timedelta(days=lookback_days)

        for asset_id, asset_readings in readings_by_asset.items():
            if not asset_readings:
                continue

            asset = asset_by_id.get(asset_id, {})
            asset_name = asset.get("name", "Unknown Asset")
            expected_kwh = asset.get("expected_kwh_per_day", 0) or 0.0

            # Filter to lookback window
            window_readings = []
            for r in asset_readings:
                ts = r.get("timestamp", "")
                if ts:
                    try:
                        dt = datetime.fromisoformat(ts.replace("Z", "+00:00"))
                        if dt >= cutoff:
                            window_readings.append((dt, r))
                    except (ValueError, TypeError):
                        pass

            if len(window_readings) < 3:
                continue

            # Sort by timestamp
            window_readings.sort(key=lambda x: x[0])

            # Compute rolling 7-day average (each reading vs mean of prior 7)
            kwh_values = [r.get("kwh", 0) or 0 for _, r in window_readings]

            if len(kwh_values) < 3:
                continue

            mean_kwh = np.mean(kwh_values)
            std_kwh = float(np.std(kwh_values)) if len(kwh_values) > 1 else 1.0

            if std_kwh == 0:
                std_kwh = 1.0  # avoid division by zero

            # Flag anomalies
            for dt, reading in window_readings:
                kwh = reading.get("kwh", 0) or 0.0
                if kwh == 0:
                    continue

                zscore = (kwh - mean_kwh) / std_kwh
                if zscore < zscore_threshold:
                    continue

                # Compute deviation from expected
                if expected_kwh > 0:
                    deviation_pct = round(((kwh - expected_kwh) / expected_kwh) * 100, 1)
                else:
                    deviation_pct = round(((kwh - mean_kwh) / mean_kwh) * 100, 1) if mean_kwh > 0 else 0.0

                # Match verbal reports to this asset
                asset_name_lower = asset_name.lower()
                relevant_reports = [
                    c for c in verbal_chunks
                    if asset_name_lower in c.lower() or "energy" in c.lower()
                ]

                # Simple cause inference
                likely_cause = self._infer_cause(kwh, expected_kwh, zscore, relevant_reports)

                result.anomalies.append(EnergyAnomaly(
                    asset_name=asset_name,
                    asset_id=asset_id,
                    anomaly_date=dt.date().isoformat(),
                    kwh_recorded=round(kwh, 2),
                    kwh_expected=round(expected_kwh, 2) if expected_kwh else round(mean_kwh, 2),
                    deviation_pct=deviation_pct,
                    verbal_reports=relevant_reports[:3],
                    likely_cause=likely_cause,
                    zscore=round(zscore, 2),
                ))

        # Sort by deviation_pct descending
        result.anomalies.sort(key=lambda a: a.deviation_pct, reverse=True)

        logger.info(
            "energy_anomaly_detection_completed",
            org_id=org_id,
            anomalies_found=len(result.anomalies),
        )

        return result

    def _infer_cause(
        self,
        kwh: float,
        expected: float,
        zscore: float,
        verbal_reports: List[str],
    ) -> str:
        """Simple heuristic to infer likely cause of anomaly."""
        combined_text = " ".join(verbal_reports).lower()

        if any(w in combined_text for w in ["hot", "overheating", "compressor", "hvac", "ac"]):
            return "HVAC or cooling system malfunction"
        elif any(w in combined_text for w in ["running hot", "noise", "vibration"]):
            return "Equipment running hot or mechanical issue"
        elif any(w in combined_text for w in ["spike", "surge", "power"]):
            return "Power surge or spike"
        elif zscore > 3.5:
            return "Significant unexplained consumption spike"
        elif zscore > 2.5:
            return "Moderate consumption increase, investigate further"
        return "Possible sensor anomaly, verify meter accuracy"
