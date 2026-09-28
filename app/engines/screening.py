"""Brief screening and revision scope checks.

Both are deterministic rules so they run instantly on every request and are easy
to test. Claude can add a second read on top (see services/claude.py), but it
never replaces these results.
"""
from __future__ import annotations

import math
import re
from typing import Any

VAGUE_PHRASES = [
    "surprise me", "idk", "whatever you want", "something cool", "up to you",
    "anything", "dunno", "you decide",
]

RED_FLAGS: list[tuple[re.Pattern[str], str]] = [
    (re.compile(r"\bexposure\b", re.I), "Offers “exposure” instead of payment."),
    (re.compile(r"\b(discount|cheaper|cheap|lower (the )?price|tight budget|budget is tight)\b", re.I),
     "Asks for a discount before work starts."),
    (re.compile(r"\b(asap|urgent|by tomorrow|tonight|rush)\b", re.I),
     "Rush timeline. Offer a rush fee or decline."),
    (re.compile(r"\b(nft|ai training|train (an|my) ai|dataset)\b", re.I),
     "Use that many artists exclude in their terms (NFT or AI training)."),
    (re.compile(r"\b(trace|exactly like|copy (the )?style of)\b", re.I),
     "Asks to copy another artist’s work or style."),
    (re.compile(r"\b(pay (you )?later|after (it'?s )?done|pay when)\b", re.I),
     "Wants to pay after delivery."),
]

COMMERCIAL = re.compile(r"\b(merch|brand|logo|business|sell|commercial|print run|mascot)\b", re.I)
DETAIL_WORDS = re.compile(
    r"\b(armor|armour|wings|mech|weapon|sword|lute|instrument|horns|tail|jewelry|necklace|pattern|"
    r"tattoo|hands|holding|dynamic|action|lighting|glow|scales|feathers)\b",
    re.I,
)

FLAG_LABELS = {"vague": "Vague", "red": "Red flag", "info": "Scope", "ok": "Clear"}


def screen_brief(brief: dict[str, Any], ctype: dict[str, Any]) -> dict[str, Any]:
    """Return {flags: [{kind, message}], complexity: 1-5, verdict: ok|vague|red}.

    brief keys: description, characters, background (None|Simple|Detailed),
                references (list[str]), usage (Personal|Commercial), deadline
    ctype keys: name, base_price_cents, est_hours, characters_included, extra_character_pct
    """
    flags: list[dict[str, str]] = []
    desc = (brief.get("description") or "").strip()
    deadline = brief.get("deadline") or ""
    words = len(desc.split()) if desc else 0

    if words < 18:
        flags.append({"kind": "vague", "message": f"Short description ({words} words). Ask for appearance, pose and mood.",
                      "question": "more detail on the character's appearance, pose and mood"})
    low = desc.lower()
    vague_hits = [p for p in VAGUE_PHRASES if re.search(rf"\b{re.escape(p)}\b", low)]
    if vague_hits:
        flags.append({"kind": "vague", "message": f"Open-ended wording (“{vague_hits[0]}”). Ask what they have in mind.",
                      "question": "what you have in mind for the piece, even a rough idea"})
    if not brief.get("references"):
        flags.append({"kind": "vague", "message": "No reference links for the character.",
                      "question": "reference images or links for the character"})

    for pattern, message in RED_FLAGS:
        if pattern.search(desc) or pattern.search(deadline):
            flags.append({"kind": "red", "message": message})

    if COMMERCIAL.search(desc) and (brief.get("usage") or "Personal") == "Personal":
        flags.append({"kind": "red", "message": "Mentions commercial use but chose a personal license. Quote a commercial upgrade."})

    characters = max(1, int(brief.get("characters") or 1))
    included = int(ctype.get("characters_included") or 1)
    if characters > included:
        extra = characters - included
        pct = ctype.get("extra_character_pct")
        pct = 60 if pct is None else int(pct)
        add = extra * pct / 100 * ctype["base_price_cents"]
        flags.append({
            "kind": "info",
            "message": f"{characters} characters on a {ctype['name'].lower()} (includes {included}). "
                       f"Add {pct}% per extra character: +${add / 100:,.0f}.",
        })

    detail = len(DETAIL_WORDS.findall(desc))
    background = brief.get("background") or "None"
    cx = (
        1
        + min(2, characters - 1)
        + (1 if background == "Detailed" else 0.5 if background == "Simple" else 0)
        + min(1.5, detail * 0.4)
        + (0.5 if float(ctype.get("est_hours") or 0) >= 6 else 0)
    )
    complexity = max(1, min(5, int(math.floor(cx + 0.5))))

    if not flags:
        flags.append({"kind": "ok", "message": "Clear brief with references. Nothing to follow up on."})
    kinds = {f["kind"] for f in flags}
    verdict = "red" if "red" in kinds else "vague" if "vague" in kinds else "ok"
    return {"flags": flags, "complexity": complexity, "verdict": verdict}


# ---------------------------------------------------------------------------
# Revision scope check

SCOPE_CREEP: list[tuple[re.Pattern[str], str, float]] = [
    (re.compile(r"\b(another|second|extra|additional|new) (character|person|oc|pet)\b|"
                r"\badd (my|a|an|another) \w*\s?(character|friend|partner|pet|oc|boyfriend|girlfriend|wife|husband)\b", re.I),
     "Adds a character", 0.60),
    (re.compile(r"\b(add|include|put)( a| an)? (background|scene|setting)\b|\bdetailed background\b", re.I),
     "Adds a background", 0.40),
    (re.compile(r"\b(change|different|new) (the )?(pose|composition|angle)\b", re.I),
     "Changes the pose or composition", 0.35),
    (re.compile(r"\b(change|different|new) (the )?(outfit|costume|clothes)\b", re.I),
     "Changes the outfit", 0.30),
    (re.compile(r"\b(start over|redo (it|everything)|completely different)\b", re.I),
     "Restarts the piece", 0.80),
    (re.compile(r"\b(also|extra|another) (version|variant|expression|emote)\b", re.I),
     "Adds an extra version", 0.30),
]

PAID_REVISION_PCT = 0.15


def round_to_5_dollars(cents: float) -> int:
    return int(max(500, round(cents / 500) * 500))


def scope_check(text: str, base_price_cents: int, revisions_included: int, revisions_used: int) -> dict[str, Any]:
    """Classify a change request against the locked brief.

    Returns one of:
      {kind: "out_of_scope", reasons: [...], quote_cents}
      {kind: "revision", revisions_left_after}
      {kind: "paid_revision", quote_cents}
    """
    hits = [(label, pct) for pattern, label, pct in SCOPE_CREEP if pattern.search(text)]
    if hits:
        pct = sum(p for _, p in hits)
        return {
            "kind": "out_of_scope",
            "reasons": [label for label, _ in hits],
            "quote_cents": round_to_5_dollars(base_price_cents * pct),
        }
    left = revisions_included - revisions_used
    if left <= 0:
        return {"kind": "paid_revision", "reasons": [], "quote_cents": round_to_5_dollars(base_price_cents * PAID_REVISION_PCT)}
    return {"kind": "revision", "reasons": [], "quote_cents": 0, "revisions_left_after": left - 1}
