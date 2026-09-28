"""The goal-setting agent's pricing engine.

All numbers the creator sees come from here, computed from their own history.
The LLM layer (services/claude.py) only explains the result; it never produces
figures. Every function is pure: pass in plain data, get plain dicts back.
"""
from __future__ import annotations

import math
from dataclasses import dataclass
from statistics import median
from typing import Any, Iterable

DAY = 86_400
MONTH_DAYS = 30
WEEKS_PER_MONTH = 4.345
WINDOW_DAYS = 180
MIN_HISTORY = 5
RAISE_STEPS = [0, 10, 15, 25, 40]


@dataclass(frozen=True)
class Completed:
    price_cents: int
    hours: float
    accepted_at: int
    completed_at: int


@dataclass(frozen=True)
class PriceChange:
    pct: float
    changed_at: int


def round5(cents: float) -> int:
    """Round to the nearest $5, never below $5."""
    return int(max(500, round(cents / 500) * 500))


def history_stats(items: Iterable[Completed], now: int, fallback_price_cents: int = 0,
                  fallback_hours: float = 3.0) -> dict[str, Any]:
    window = [x for x in items if now - x.completed_at <= WINDOW_DAYS * DAY]
    n = len(window)
    buckets = [0] * 6
    revenue = [0] * 6
    for x in window:
        idx = 5 - min(5, (now - x.completed_at) // (MONTH_DAYS * DAY))
        buckets[idx] += 1
        revenue[idx] += x.price_cents

    enough = n >= MIN_HISTORY
    avg_price = sum(x.price_cents for x in window) / n if n else fallback_price_cents
    logged = [x.hours for x in window if x.hours > 0]
    avg_hours = sum(logged) / len(logged) if logged else fallback_hours
    hours_total = sum(logged)
    revenue_total = sum(x.price_cents for x in window)
    revenue_with_hours = sum(x.price_cents for x in window if x.hours > 0)

    def turnaround(x: Completed) -> float:
        return (x.completed_at - x.accepted_at) / DAY

    recent = [turnaround(x) for x in window if now - x.completed_at <= MONTH_DAYS * DAY]
    older = [turnaround(x) for x in window if now - x.completed_at > MONTH_DAYS * DAY]

    return {
        "count": n,
        "enough_history": enough,
        "avg_price_cents": round(avg_price),
        "avg_hours": round(avg_hours, 2),
        "buckets": buckets,
        "revenue_buckets_cents": revenue,
        "best_month": max(buckets) if n else 0,
        "avg_month": round(sum(buckets) / 6, 2),
        "last_30": buckets[5],
        "effective_hourly_cents": round(revenue_with_hours / hours_total) if hours_total else None,
        "turnaround_recent_days": round(median(recent), 1) if recent else None,
        "turnaround_before_days": round(median(older), 1) if older else None,
        "revenue_total_cents": revenue_total,
    }


def demand_signal(accepted_at: list[int], price_changes: list[PriceChange], waitlist: int, slots: int,
                  active: int, now: int) -> dict[str, Any]:
    """Did bookings hold up after past raises, and is anyone waiting?"""
    results = []
    for pc in price_changes:
        if pc.pct <= 0:
            continue
        before = sum(1 for a in accepted_at if pc.changed_at - MONTH_DAYS * DAY <= a < pc.changed_at)
        after = sum(1 for a in accepted_at if pc.changed_at <= a < pc.changed_at + MONTH_DAYS * DAY)
        complete = now >= pc.changed_at + MONTH_DAYS * DAY
        held = True if before == 0 else after / before >= 0.9
        results.append({"pct": pc.pct, "before": before, "after": after, "held": held, "complete": complete})

    judged = [r for r in results if r["complete"]]
    all_held = bool(judged) and all(r["held"] for r in judged)
    if waitlist >= slots and all_held:
        strength, raise_pct = "strong", 15
    elif waitlist > 0 or active >= slots:
        strength, raise_pct = "moderate", 8
    else:
        strength, raise_pct = "weak", 0
    return {
        "price_changes": results,
        "raises_held": all_held,
        "waitlist": waitlist,
        "slots": slots,
        "active": active,
        "strength": strength,
        "suggested_raise_pct": raise_pct,
        "max_safe_raise_pct": {"strong": 25, "moderate": 10, "weak": 0}[strength],
    }


def pace_signal(stats: dict[str, Any], active_hours: float, hours_per_week: float) -> dict[str, Any]:
    ratio = stats["last_30"] / stats["avg_month"] if stats["avg_month"] else 1.0
    tr, tb = stats["turnaround_recent_days"], stats["turnaround_before_days"]
    slowdown = tr / tb if tr and tb else 1.0
    load = active_hours / (hours_per_week * 2) if hours_per_week else 0
    risk = 20 + max(0, ratio - 1) * 120 + max(0, slowdown - 1) * 40 + max(0, load - 0.6) * 40
    risk = max(3, min(97, round(risk)))
    level = "high" if risk > 66 else "rising" if risk > 40 else "low"
    return {
        "pace_ratio": round(ratio, 2),
        "turnaround_slowdown": round(slowdown, 2),
        "active_hours": round(active_hours, 1),
        "burnout_risk": risk,
        "level": level,
        "overloaded": ratio > 1.15 or slowdown > 1.4,
    }


def build_plan(*, goal_cents: int, weeks: int, hours_per_week: float, history: list[Completed],
               price_changes: list[PriceChange], waitlist: int, slots: int, active: int, active_hours: float,
               now: int, catalog_avg_price_cents: int = 0, catalog_avg_hours: float = 3.0) -> dict[str, Any]:
    if goal_cents <= 0 or weeks <= 0 or hours_per_week <= 0:
        raise ValueError("goal, weeks and hours_per_week must be positive")

    stats = history_stats(history, now, catalog_avg_price_cents, catalog_avg_hours)
    if stats["avg_price_cents"] <= 0:
        raise ValueError("Add at least one commission type with a price, or import your history.")
    demand = demand_signal([h.accepted_at for h in history], price_changes, waitlist, slots, active, now)
    pace = pace_signal(stats, active_hours, hours_per_week)

    months = weeks / WEEKS_PER_MONTH
    capacity = hours_per_week * WEEKS_PER_MONTH / max(stats["avg_hours"], 0.25)
    best = stats["best_month"] if stats["enough_history"] else capacity
    safe = max(1.0, min(best, capacity))

    options = []
    for r in RAISE_STEPS:
        price = stats["avg_price_cents"] if r == 0 else round5(stats["avg_price_cents"] * (1 + r / 100))
        count = math.ceil(goal_cents / price)
        per_month = count / months
        options.append({
            "raise_pct": r, "avg_price_cents": price, "commissions": count,
            "per_month": round(per_month, 1), "fits": per_month <= safe,
            "risky": r > demand["max_safe_raise_pct"],
        })

    pick = next((o for o in options if o["fits"] and not o["risky"]), None)
    extended_weeks = None
    if pick is None:
        target = 15 if demand["suggested_raise_pct"] >= 15 else 10 if demand["suggested_raise_pct"] >= 8 else 0
        pick = next(o for o in options if o["raise_pct"] == target)
        extended_weeks = math.ceil(pick["commissions"] / (safe * 0.95) * WEEKS_PER_MONTH)

    plan_weeks = extended_weeks or weeks
    per_month = pick["commissions"] / (plan_weeks / WEEKS_PER_MONTH)
    per_week = pick["commissions"] / plan_weeks
    milestones = [
        {"week": i, "revenue_cents": round(goal_cents * i / plan_weeks),
         "commissions": math.ceil(pick["commissions"] * i / plan_weeks)}
        for i in range(1, plan_weeks + 1)
    ]

    plan = {
        "goal_cents": goal_cents,
        "weeks_requested": weeks,
        "plan_weeks": plan_weeks,
        "extended": extended_weeks is not None,
        "hours_per_week": hours_per_week,
        "history": stats,
        "capacity_per_month": round(capacity, 1),
        "safe_per_month": round(safe, 1),
        "options": options,
        "baseline": options[0],
        "recommended": pick,
        "per_month": round(per_month, 1),
        "per_week": round(per_week, 1),
        "milestones": milestones,
        "demand": demand,
        "pace": pace,
    }
    plan["headline"] = headline(plan)
    plan["insights"] = insights(plan)
    return plan


def _usd(cents: float) -> str:
    return f"${cents / 100:,.0f}"


def headline(p: dict[str, Any]) -> str:
    rec = p["recommended"]
    if p["extended"]:
        return f"Raise to {_usd(rec['avg_price_cents'])} and give it {p['plan_weeks']} weeks."
    if rec["raise_pct"]:
        return f"Raise to {_usd(rec['avg_price_cents'])} and book {p['per_week']} a week."
    return f"Keep your prices and book {p['per_week']} a week."


def insights(p: dict[str, Any]) -> list[dict[str, str]]:
    """The Pricing / Demand / Pace sentences, built only from engine numbers."""
    base, rec, h = p["baseline"], p["recommended"], p["history"]
    weeks = p["weeks_requested"]
    out = []

    pricing = (f"{_usd(p['goal_cents'])} in {weeks} week{'s' if weeks != 1 else ''} at your "
               f"{_usd(base['avg_price_cents'])} average takes {base['commissions']} commissions, "
               f"about {round(base['per_month'])} a month.")
    if h["enough_history"]:
        pricing += f" Your best month so far is {h['best_month']}."
    else:
        pricing += " (Based on your price list; import past commissions for sharper advice.)"
    if base["fits"] and rec["raise_pct"] == 0:
        pricing += f" That fits. Keep your prices and hold a steady {p['per_week']} a week."
    elif p["extended"]:
        pricing += (f" At {_usd(rec['avg_price_cents'])} it takes {rec['commissions']}. Suggest "
                    f"{_usd(rec['avg_price_cents'])} over {p['plan_weeks']} weeks: about {round(p['per_month'])} "
                    f"a month, within what your hours and best month support.")
    else:
        pricing += (f" At {_usd(rec['avg_price_cents'])} it takes {rec['commissions']}, about "
                    f"{round(rec['per_month'])} a month. Suggest {_usd(rec['avg_price_cents'])} for the next {weeks} weeks.")
    out.append({"label": "Pricing", "text": pricing})

    d = p["demand"]
    if d["strength"] == "strong":
        n = len([r for r in d["price_changes"] if r["complete"]])
        demand = (f"Your queue has {d['active']} of {d['slots']} slots filled and {d['waitlist']} clients are waiting. "
                  f"Your last {n} price increase{'s' if n != 1 else ''} didn't slow bookings. "
                  f"A {d['suggested_raise_pct']}% raise is low risk.")
    elif d["strength"] == "moderate":
        demand = (f"You have {d['waitlist']} on the waitlist. Demand is steady but not overflowing, "
                  f"so keep any raise to about {d['suggested_raise_pct']}%.")
    else:
        demand = "Your waitlist is empty. Hold prices and focus on getting booked before raising them."
    out.append({"label": "Demand", "text": demand})

    pc = p["pace"]
    if pc["overloaded"]:
        above = round((pc["pace_ratio"] - 1) * 100)
        where = f"{above}% above" if above > 0 else "at"
        pace = f"You're working {where} your 6-month pace"
        if h["turnaround_recent_days"] and h["turnaround_before_days"]:
            pace += (f" and delivery times went from {round(h['turnaround_before_days'])} "
                     f"to {round(h['turnaround_recent_days'])} days")
        pace += ". Close intake for 2 weeks or turn on a rush fee."
    else:
        pace = (f"Your pace is within your normal range ({h['last_30']} completed in the last 30 days vs. "
                f"a {h['avg_month']} average). No change needed.")
    out.append({"label": "Pace", "text": pace})
    return out


def checkin(plan: dict[str, Any], created_at: int, earned_cents: int, delivered: int, now: int) -> dict[str, Any]:
    """Weekly check-in: where the creator stands against the plan's milestones."""
    elapsed_weeks = max(0.0, (now - created_at) / (7 * DAY))
    week = min(plan["plan_weeks"], max(1, math.ceil(elapsed_weeks) or 1))
    expected = round(plan["goal_cents"] * min(1.0, elapsed_weeks / plan["plan_weeks"]))
    on_track = earned_cents >= expected * 0.85
    remaining_weeks = max(0.0, plan["plan_weeks"] - elapsed_weeks)
    remaining = max(0, plan["goal_cents"] - earned_cents)
    price = plan["recommended"]["avg_price_cents"]
    needed = math.ceil(remaining / price) if price else 0
    return {
        "week": week,
        "of_weeks": plan["plan_weeks"],
        "earned_cents": earned_cents,
        "delivered": delivered,
        "expected_cents": expected,
        "on_track": on_track,
        "gap_cents": max(0, expected - earned_cents),
        "remaining_cents": remaining,
        "commissions_remaining": needed,
        "per_week_needed": round(needed / remaining_weeks, 1) if remaining_weeks >= 1 else needed,
        "percent": round(min(100.0, earned_cents / plan["goal_cents"] * 100), 1),
    }
