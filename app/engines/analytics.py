"""Business analytics from completed commissions. Pure functions over plain dicts."""
from __future__ import annotations

from collections import defaultdict
from typing import Any

from .pricing import DAY, Completed, history_stats, pace_signal


def compute(completed: list[dict[str, Any]], active_hours: float, hours_per_week: float, now: int) -> dict[str, Any]:
    """completed rows need: client_handle, type_name, price_cents, hours_logged, revisions_used,
    had_upgrade (0/1), accepted_at, completed_at."""
    window = [c for c in completed if c["completed_at"] and now - c["completed_at"] <= 180 * DAY]
    items = [Completed(c["price_cents"], c["hours_logged"] or 0, c["accepted_at"], c["completed_at"]) for c in window]
    stats = history_stats(items, now)

    by_type: dict[str, dict[str, float]] = defaultdict(lambda: {"n": 0, "revenue": 0, "hours": 0, "hours_revenue": 0, "revisions": 0, "upgrades": 0})
    by_client: dict[str, dict[str, float]] = defaultdict(lambda: {"n": 0, "revenue": 0})
    for c in window:
        t = by_type[c["type_name"] or "Other"]
        t["n"] += 1
        t["revenue"] += c["price_cents"]
        t["hours"] += c["hours_logged"] or 0
        t["hours_revenue"] += c["price_cents"] if (c["hours_logged"] or 0) > 0 else 0
        t["revisions"] += c["revisions_used"]
        t["upgrades"] += 1 if c["had_upgrade"] else 0
        cl = by_client[c["client_handle"]]
        cl["n"] += 1
        cl["revenue"] += c["price_cents"]

    types = sorted(
        (
            {
                "type": name,
                "count": int(v["n"]),
                "revenue_cents": int(v["revenue"]),
                "effective_hourly_cents": round(v["hours_revenue"] / v["hours"]) if v["hours"] else None,
                "avg_revisions": round(v["revisions"] / v["n"], 2),
                "scope_creep_rate": round(v["upgrades"] / v["n"], 2),
            }
            for name, v in by_type.items()
        ),
        key=lambda x: -(x["effective_hourly_cents"] or 0),
    )
    total = stats["revenue_total_cents"] or 1
    repeat_revenue = sum(v["revenue"] for v in by_client.values() if v["n"] > 1)
    top_clients = sorted(
        ({"client": k, "commissions": int(v["n"]), "revenue_cents": int(v["revenue"])} for k, v in by_client.items()),
        key=lambda x: -x["revenue_cents"],
    )[:10]

    return {
        "delivered_180d": stats["count"],
        "revenue_180d_cents": stats["revenue_total_cents"],
        "avg_price_cents": stats["avg_price_cents"] if stats["count"] else None,
        "effective_hourly_cents": stats["effective_hourly_cents"],
        "repeat_revenue_share": round(repeat_revenue / total, 3) if stats["count"] else 0,
        "revenue_by_30d_cents": stats["revenue_buckets_cents"],
        "count_by_30d": stats["buckets"],
        "by_type": types,
        "top_clients": top_clients,
        "turnaround_recent_days": stats["turnaround_recent_days"],
        "turnaround_before_days": stats["turnaround_before_days"],
        "pace": pace_signal(stats, active_hours, hours_per_week),
    }
