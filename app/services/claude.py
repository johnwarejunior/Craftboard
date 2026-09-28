"""Optional Claude API layer.

The pricing engine produces every number. Claude only (1) explains a plan in
plain language and (2) gives a second read on a brief. With no ANTHROPIC_API_KEY
both functions return None and the app falls back to the engine's own text.
"""
from __future__ import annotations

import json
import logging
from typing import Any

from ..config import settings

log = logging.getLogger("craftboard.claude")

PLAN_SYSTEM = (
    "You are the goal-setting agent inside Craftboard, a back-office tool for independent artists "
    "who take commissions. A pricing engine has already computed a plan from the artist's own history. "
    "Explain it in a warm, direct coach's voice: second person, plain text, 3 short paragraphs, under 170 words. "
    "Cover why this price and timeline, one concrete action for this week, and one risk to watch. "
    "Use ONLY numbers present in the data. Never invent, estimate or recompute figures. No markdown."
)

BRIEF_SYSTEM = (
    "You screen commission requests for an independent artist. Read the brief and reply with JSON only, "
    'no prose and no code fences: {"summary": string (one sentence), "questions": string[] '
    '(0-3 short follow-up questions for the client), "risk": "low" | "medium" | "high"}.'
)


def available() -> bool:
    return bool(settings.anthropic_api_key)


def _client():
    import anthropic  # imported lazily so the app runs without the package configured

    return anthropic.Anthropic(api_key=settings.anthropic_api_key)


def _text(message: Any) -> str:
    return "".join(getattr(block, "text", "") for block in message.content).strip()


def plan_facts(plan: dict[str, Any]) -> dict[str, Any]:
    """The only data Claude sees: a flat, human-scaled summary of the engine output."""
    h, rec, base = plan["history"], plan["recommended"], plan["baseline"]
    return {
        "goal_usd": plan["goal_cents"] / 100,
        "weeks_requested": plan["weeks_requested"],
        "plan_weeks": plan["plan_weeks"],
        "current_avg_price_usd": base["avg_price_cents"] / 100,
        "commissions_at_current_price": base["commissions"],
        "recommended_avg_price_usd": rec["avg_price_cents"] / 100,
        "raise_percent": rec["raise_pct"],
        "commissions_needed": rec["commissions"],
        "per_month": plan["per_month"],
        "per_week": plan["per_week"],
        "best_month": h["best_month"],
        "six_month_avg_per_month": h["avg_month"],
        "capacity_per_month": plan["capacity_per_month"],
        "hours_per_week": plan["hours_per_week"],
        "waitlist": plan["demand"]["waitlist"],
        "slots": plan["demand"]["slots"],
        "past_raises_held_bookings": plan["demand"]["raises_held"],
        "burnout_risk_0_100": plan["pace"]["burnout_risk"],
        "engine_insights": [i["text"] for i in plan["insights"]],
    }


def narrate_plan(plan: dict[str, Any]) -> str | None:
    if not available():
        return None
    try:
        msg = _client().messages.create(
            model=settings.claude_model,
            max_tokens=600,
            system=PLAN_SYSTEM,
            messages=[{"role": "user", "content": "Plan data (JSON):\n" + json.dumps(plan_facts(plan))}],
        )
        return _text(msg) or None
    except Exception as exc:  # network, auth, rate limit: never break the plan
        log.warning("Claude narration failed: %s", exc)
        return None


def review_brief(brief: dict[str, Any], type_name: str, price_cents: int, est_hours: float) -> dict[str, Any] | None:
    if not available():
        return None
    prompt = (
        f"Commission type: {type_name} (${price_cents / 100:,.0f}, about {est_hours:g} hours).\n"
        f"Brief (JSON): {json.dumps(brief)}"
    )
    try:
        msg = _client().messages.create(
            model=settings.claude_model,
            max_tokens=400,
            system=BRIEF_SYSTEM,
            messages=[{"role": "user", "content": prompt}],
        )
        raw = _text(msg).replace("```json", "").replace("```", "").strip()
        data = json.loads(raw)
        return {
            "summary": str(data.get("summary", ""))[:400],
            "questions": [str(q)[:200] for q in data.get("questions", [])][:3],
            "risk": data.get("risk") if data.get("risk") in {"low", "medium", "high"} else "medium",
        }
    except Exception as exc:
        log.warning("Claude brief review failed: %s", exc)
        return None
