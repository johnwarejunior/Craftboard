from app.engines.pricing import DAY, Completed, PriceChange, build_plan, checkin, demand_signal
from app.engines.screening import scope_check, screen_brief

TYPE = {"name": "Bust portrait", "base_price_cents": 9500, "est_hours": 3.5, "characters_included": 1, "extra_character_pct": 60}
NOW = 1_800_000_000


def brief(**kw):
    b = {"description": "", "characters": 1, "background": "None", "references": [], "usage": "Personal", "deadline": ""}
    b.update(kw)
    return b


def test_clear_brief_passes():
    r = screen_brief(brief(description="Bust portrait of my tiefling bard Vesper: purple skin, curled horns with gold rings, "
                                       "smirking, holding a lute over her shoulder, warm tavern lighting.",
                           references=["toyhou.se/vesper"]), TYPE)
    assert r["verdict"] == "ok"
    assert 1 <= r["complexity"] <= 5


def test_vague_brief_flagged():
    r = screen_brief(brief(description="something cool idk, surprise me"), TYPE)
    assert r["verdict"] == "vague"
    kinds = [f["kind"] for f in r["flags"]]
    assert kinds.count("vague") == 3


def test_red_flags():
    r = screen_brief(brief(description="Need a mascot for our brand ASAP. Can you do a discount? Great exposure for you.",
                           references=["x"]), TYPE)
    assert r["verdict"] == "red"
    msgs = " ".join(f["message"] for f in r["flags"])
    assert "exposure" in msgs and "discount" in msgs and "Rush" in msgs and "commercial" in msgs


def test_extra_characters_priced():
    r = screen_brief(brief(description="x " * 30, characters=3, references=["a"]), TYPE)
    info = [f for f in r["flags"] if f["kind"] == "info"]
    assert info and "+$114" in info[0]["message"]  # 2 extra × 60% × $95


def test_scope_minor_revision_counts():
    r = scope_check("Could the hoodie be a bit darker green?", 6000, 2, 0)
    assert r["kind"] == "revision" and r["revisions_left_after"] == 1


def test_scope_creep_quoted():
    r = scope_check("Love it! Could you add my boyfriend next to her?", 6000, 2, 0)
    assert r["kind"] == "out_of_scope"
    assert r["reasons"] == ["Adds a character"]
    assert r["quote_cents"] == 3500  # 60% of $60, rounded to $5


def test_paid_revision_after_limit():
    r = scope_check("slightly warmer colors please", 10000, 2, 2)
    assert r["kind"] == "paid_revision" and r["quote_cents"] == 1500


def _history(avg_cents=8500, months=(14, 17, 24, 18, 20, 23), hours=3.5):
    items = []
    for i, n in enumerate(months):
        start = NOW - (180 - i * 30) * DAY
        for k in range(n):
            done = start + int((k + 0.5) / n * 30 * DAY)
            items.append(Completed(avg_cents, hours, done - 8 * DAY, done))
    return items


def test_plan_matches_pitch_example():
    """$5,000 at an $85 average takes 59 commissions; best month is 24."""
    plan = build_plan(goal_cents=500_000, weeks=4, hours_per_week=30, history=_history(), price_changes=[],
                      waitlist=12, slots=6, active=4, active_hours=14, now=NOW)
    assert plan["baseline"]["avg_price_cents"] == 8500
    assert plan["baseline"]["commissions"] == 59
    assert plan["history"]["best_month"] == 24
    assert plan["extended"]  # 59 in a month is more than twice the best month
    assert plan["recommended"]["per_month"] > plan["safe_per_month"]  # needed the extension
    assert plan["per_month"] <= plan["safe_per_month"]


def test_plan_fits_without_raise():
    plan = build_plan(goal_cents=150_000, weeks=4, hours_per_week=30, history=_history(), price_changes=[],
                      waitlist=0, slots=6, active=2, active_hours=6, now=NOW)
    assert plan["recommended"]["raise_pct"] == 0
    assert not plan["extended"]
    assert plan["headline"].startswith("Keep your prices")


def test_demand_strong_when_raises_held():
    accepted = [NOW - d * DAY for d in range(1, 170, 2)]  # steady bookings
    d = demand_signal(accepted, [PriceChange(10, NOW - 120 * DAY), PriceChange(10, NOW - 60 * DAY)],
                      waitlist=12, slots=6, active=4, now=NOW)
    assert d["raises_held"] and d["strength"] == "strong" and d["suggested_raise_pct"] == 15


def test_plan_without_history_uses_catalog():
    plan = build_plan(goal_cents=100_000, weeks=4, hours_per_week=20, history=[], price_changes=[], waitlist=0, slots=3,
                      active=0, active_hours=0, now=NOW, catalog_avg_price_cents=10_000, catalog_avg_hours=4)
    assert plan["baseline"]["commissions"] == 10
    assert "price list" in plan["insights"][0]["text"]


def test_checkin_pro_rates():
    plan = build_plan(goal_cents=400_000, weeks=4, hours_per_week=30, history=_history(), price_changes=[],
                      waitlist=0, slots=6, active=0, active_hours=0, now=NOW)
    day0 = checkin(plan, NOW, 0, 0, NOW)
    assert day0["on_track"] and day0["week"] == 1
    later = checkin(plan, NOW - 14 * DAY, 50_000, 5, NOW)
    assert not later["on_track"] and later["gap_cents"] > 0


def test_zero_extra_character_pct_is_respected():
    r = screen_brief(brief(description="x", characters=3), {**TYPE, "extra_character_pct": 0})
    scope = next(f for f in r["flags"] if f["kind"] == "info")
    assert "Add 0% per extra character: +$0" in scope["message"]


def test_vague_phrases_match_whole_words_only():
    r = screen_brief(brief(description="My sidkick character, a lanky rogue", references=["x"]), TYPE)
    assert not any("Open-ended" in f["message"] for f in r["flags"])


def test_effective_hourly_ignores_commissions_without_hours():
    from app.engines.pricing import history_stats

    items = [Completed(10000, 4, NOW - 10 * DAY, NOW - 5 * DAY), Completed(10000, 0, NOW - 10 * DAY, NOW - 5 * DAY)]
    assert history_stats(items, NOW)["effective_hourly_cents"] == 2500
