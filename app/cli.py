"""Admin commands.

    python -m app.cli seed-demo [--reset]     create the demo creator with ~6 months of history
    python -m app.cli set-plan EMAIL PLAN     switch a creator to free | studio | pro
    python -m app.cli list                    list creators
"""
from __future__ import annotations

import argparse
import random
import sys

from .config import settings
from .db import connect, dumps, init_db, now
from .engines.screening import screen_brief
from .security import hash_password, portal_token

DAY = 86400
DEMO_EMAIL = "tamsin@example.com"
DEMO_PASSWORD = "craftboard-demo"

# name, current price ($), est hours, booking weight, hours multiplier (how long it really takes)
TYPES = [
    ("Chibi portrait", 60, 2.0, 4, 1.0, "Full body, cute proportions, flat colour."),
    ("Bust portrait", 95, 3.5, 3, 1.0, "Shoulders up, rendered, simple background."),
    ("Character sheet", 150, 7.0, 1.5, 1.05, "Front and back views plus three expressions."),
    ("Emote pack", 45, 2.6, 3, 1.35, "Three Twitch/Discord emotes at all sizes."),
    ("Reference sheet", 125, 5.0, 1, 1.0, "Turnaround with colour palette and notes."),
    ("Full illustration", 230, 9.0, 1, 1.1, "Full scene with a detailed background."),
]
BUCKETS = [14, 17, 24, 18, 20, 23]          # bookings per 30-day stretch, oldest first
RAISES_DAYS_AGO = [120, 60]                  # two 10% raises, both held
CLIENTS = ["mira", "keo", "juno", "sol", "ash.p", "bramble", "cyd", "dune", "ember.art", "fen", "gale", "harlow",
           "ivo", "jett", "kit.sketch", "lumen", "moss", "nyx", "orrin", "pax", "quill", "rue", "sable", "tavi",
           "umber", "vex", "wren", "yarrow", "zephyr", "noor"]


def _round5(cents: float) -> int:
    return int(round(cents / 500.0)) * 500


def seed_demo(reset: bool) -> None:
    init_db()
    rng = random.Random(7)
    t = now()
    with connect() as conn:
        existing = conn.execute("SELECT id FROM creators WHERE email=?", (DEMO_EMAIL,)).fetchone()
        if existing and not reset:
            print(f"Demo creator already exists. Sign in as {DEMO_EMAIL} / {DEMO_PASSWORD} (use --reset to rebuild).")
            return
        if existing:
            cid = existing["id"]
            for table in ("sessions", "goals", "waitlist", "requests", "price_changes"):
                conn.execute(f"DELETE FROM {table} WHERE creator_id=?", (cid,))
            conn.execute("DELETE FROM commissions WHERE creator_id=?", (cid,))
            conn.execute("DELETE FROM clients WHERE creator_id=?", (cid,))
            conn.execute("DELETE FROM commission_types WHERE creator_id=?", (cid,))
            conn.execute("DELETE FROM creators WHERE id=?", (cid,))

        cid = conn.execute(
            """INSERT INTO creators (email, password_hash, name, handle, plan, slots, revisions_included, deposit_pct,
               hours_per_week, intake_message, created_at) VALUES (?,?,?,?,?,?,?,?,?,?,?)""",
            (DEMO_EMAIL, hash_password(DEMO_PASSWORD), "Tamsin Vale", "tamsin.draws", "pro", 6, 2, 50, 30,
             "Hi! I draw cozy fantasy characters. Please include references if you have them. "
             "No NSFW, NFTs or AI training.", t - 200 * DAY)).lastrowid

        types = {}
        for i, (name, price, hours, weight, mult, desc) in enumerate(TYPES):
            tid = conn.execute(
                """INSERT INTO commission_types (creator_id, name, description, base_price_cents, est_hours, sort_order,
                   created_at) VALUES (?,?,?,?,?,?,?)""", (cid, name, desc, price * 100, hours, i, t - 200 * DAY)).lastrowid
            types[name] = {"id": tid, "name": name, "price": price * 100, "hours": hours, "weight": weight, "mult": mult,
                           "base_price_cents": price * 100, "est_hours": hours, "characters_included": 1,
                           "extra_character_pct": 60}

        for days in RAISES_DAYS_AGO:  # one studio-wide raise across all types
            conn.execute("INSERT INTO price_changes (creator_id, type_id, pct, changed_at) VALUES (?,NULL,?,?)",
                         (cid, 10.0, t - days * DAY))

        clients = {}

        def client(handle: str) -> int:
            if handle not in clients:
                clients[handle] = conn.execute("INSERT INTO clients (creator_id, handle, email, created_at) VALUES (?,?,?,?)",
                                               (cid, handle, f"{handle.replace('.', '')}@example.com", t - 190 * DAY)).lastrowid
            return clients[handle]

        def price_at(ty: dict, ts: int) -> int:
            raises = sum(1 for d in RAISES_DAYS_AGO if ts < t - d * DAY)
            return _round5(ty["price"] / (1.1 ** raises))

        # -------------------------------------------------------- completed history
        names, weights = list(types), [types[n]["weight"] for n in types]
        one_offs = [f"{a}{b}" for a in ("star", "moon", "pixel", "fern", "cloud", "ink", "honey", "frost", "rune", "velvet",
                                          "maple", "coral") for b in ("fox", "bun", "moth", "crow", "koi", "wolf", "doe", "owl")]
        rng.shuffle(one_offs)

        def one_off() -> str:
            return one_offs.pop() if one_offs else rng.choice(CLIENTS)
        repeaters = CLIENTS[:8]
        for b, count in enumerate(BUCKETS):
            start = t - (6 - b) * 30 * DAY
            for _ in range(count):
                ty = types[rng.choices(names, weights)[0]]
                turnaround = rng.uniform(8, 12) if b == 5 else rng.uniform(5, 8)
                accepted = start + rng.randint(0, 30 * DAY - 1)       # buckets count bookings
                if accepted + turnaround * DAY > t - 2 * 3600:          # not finished yet: book it earlier instead
                    accepted = int(t - turnaround * DAY - rng.randint(2, 20 * 24) * 3600)
                completed = int(accepted + turnaround * DAY)
                price = price_at(ty, accepted)
                handle = rng.choice(repeaters) if rng.random() < 0.3 else one_off()
                revisions = rng.choices([0, 1, 2], [4, 4, 2])[0]
                hours = round(ty["hours"] * ty["mult"] * rng.uniform(0.9, 1.2) + 0.4 * revisions, 1)
                upgrade = _round5(price * 0.3) if rng.random() < 0.12 else 0
                com = conn.execute(
                    """INSERT INTO commissions (creator_id, client_id, type_id, title, brief_json, brief_locked_at, price_cents,
                       deposit_cents, revisions_included, revisions_used, hours_logged, status, portal_token, source,
                       accepted_at, started_at, delivered_at, completed_at)
                       VALUES (?,?,?,?,?,?,?,?,2,?,?,'completed',?,'manual',?,?,?,?)""",
                    (cid, client(handle), ty["id"], f"{ty['name']} for @{handle}",
                     dumps({"description": f"{ty['name']} of my character.", "characters": 1, "background": "None",
                            "references": [], "usage": "Personal", "deadline": ""}),
                     accepted, price + upgrade, price // 2, revisions, hours, portal_token(),
                     accepted, accepted + DAY, completed, completed)).lastrowid
                if upgrade:
                    conn.execute("""INSERT INTO revision_requests (commission_id, text, kind, reasons_json, quote_cents, status,
                                    created_at, resolved_at) VALUES (?,?, 'out_of_scope', ?, ?, 'accepted', ?, ?)""",
                                 (com, "Could you add a detailed background?", dumps(["New background"]), upgrade,
                                  completed - DAY, completed - DAY))
                for kind, amount, ts in (("deposit", price // 2, accepted), ("balance", price + upgrade - price // 2, completed)):
                    conn.execute("""INSERT INTO payments (commission_id, kind, amount_cents, provider, status, created_at, paid_at)
                                    VALUES (?,?,?, 'simulated', 'paid', ?, ?)""", (com, kind, amount, ts, ts))

        # -------------------------------------------------------- active queue (4 of 6 slots, as in the pitch)
        active = [
            ("mira", "Chibi portrait", "in_progress", 0, "My elf ranger with a green cloak and a small fox companion, happy expression."),
            ("keo", "Character sheet", "in_progress", 1, "Front/back sheet of my knight OC: silver armor, blue cape, scar over left eye."),
            ("juno", "Emote pack", "in_review", 0, "Three emotes of my cat character: love, sleepy, and laughing."),
            ("sol", "Reference sheet", "queued", 0, "Reference sheet for my sun spirit OC, gold and orange palette, flowing hair."),
        ]
        portal_links = []
        for i, (handle, tname, status, revs, desc) in enumerate(active):
            ty = types[tname]
            accepted = t - (12 - i * 3) * DAY
            brief = {"description": desc, "characters": 1, "background": "Simple",
                     "references": [f"https://example.com/refs/{handle}"], "usage": "Personal", "deadline": ""}
            token = portal_token()
            com = conn.execute(
                """INSERT INTO commissions (creator_id, client_id, type_id, title, brief_json, brief_locked_at, price_cents,
                   deposit_cents, revisions_included, revisions_used, hours_logged, status, portal_token, accepted_at, started_at)
                   VALUES (?,?,?,?,?,?,?,?,2,?,?,?,?,?,?)""",
                (cid, client(handle), ty["id"], f"{tname} for @{handle}", dumps(brief), accepted, ty["price"], ty["price"] // 2,
                 revs, round(ty["hours"] * 0.5, 1) if status != "queued" else 0, status, token, accepted,
                 accepted + DAY if status != "queued" else None)).lastrowid
            conn.execute("""INSERT INTO payments (commission_id, kind, amount_cents, provider, status, created_at, paid_at)
                            VALUES (?, 'deposit', ?, 'simulated', 'paid', ?, ?)""", (com, ty["price"] // 2, accepted, accepted))
            if revs:
                conn.execute("""INSERT INTO revision_requests (commission_id, text, kind, status, created_at, resolved_at)
                                VALUES (?, 'Could the cape be a darker blue?', 'revision', 'applied', ?, ?)""",
                             (com, t - 2 * DAY, t - 2 * DAY))
            portal_links.append((handle, status, token))

        # -------------------------------------------------------- waitlist (12, as in the pitch)
        for i, handle in enumerate(CLIENTS[10:22]):
            ty = types[names[i % len(names)]]
            conn.execute("INSERT INTO waitlist (creator_id, client_id, type_id, note, created_at) VALUES (?,?,?,?,?)",
                         (cid, client(handle), ty["id"], f"Would love a {ty['name'].lower()} when you have room!",
                          t - (20 - i) * DAY))

        # -------------------------------------------------------- incoming requests: clean, vague, red flags
        incoming = [
            ("wren", "Bust portrait", {"description": "Bust of my bard OC Wren: warm smile, holding a lute, long auburn braid, freckles, "
                                                      "green tunic with a gold trim. Soft evening lighting, cozy mood.", "characters": 1, "background": "Simple",
                                       "references": ["https://example.com/wren-ref-1", "https://example.com/wren-ref-2"],
                                       "usage": "Personal", "deadline": ""}),
            ("pax", "Full illustration", {"description": "idk something cool with my guy, surprise me", "characters": 1,
                                          "background": "Detailed", "references": [], "usage": "Personal", "deadline": ""}),
            ("vex", "Emote pack", {"description": "Emotes for my stream brand, need them ASAP. Budget is tight but it's great "
                                              "exposure, I have 20k followers. Can pay after it's done.", "characters": 1,
                                   "background": "None", "references": [], "usage": "Commercial", "deadline": "tomorrow"}),
        ]
        for i, (handle, tname, brief) in enumerate(incoming):
            conn.execute("""INSERT INTO requests (creator_id, client_id, type_id, brief_json, screening_json, created_at)
                            VALUES (?,?,?,?,?,?)""",
                         (cid, client(handle), types[tname]["id"], dumps(brief), dumps(screen_brief(brief, types[tname])),
                          t - (5 - i) * 3600))

    print("Seeded demo creator Tamsin Vale (@tamsin.draws), Pro plan.")
    print(f"  Sign in:      {settings.base_url}/login  ->  {DEMO_EMAIL} / {DEMO_PASSWORD}")
    print(f"  Intake form:  {settings.base_url}/c/tamsin.draws")
    for handle, status, token in portal_links:
        print(f"  Portal @{handle:<5} ({status}): {settings.base_url}/p/{token}")


def set_plan(email: str, plan: str) -> None:
    if plan not in {"free", "studio", "pro"}:
        sys.exit("Plan must be free, studio or pro.")
    init_db()
    with connect() as conn:
        cur = conn.execute("UPDATE creators SET plan=? WHERE email=?", (plan, email))
        if cur.rowcount == 0:
            sys.exit(f"No creator with email {email}.")
    print(f"{email} is now on the {plan} plan.")


def list_creators() -> None:
    init_db()
    with connect() as conn:
        for r in conn.execute("SELECT email, handle, plan, created_at FROM creators ORDER BY id"):
            print(f"{r['email']:<32} @{r['handle']:<20} {r['plan']}")


def main(argv: list[str] | None = None) -> None:
    ap = argparse.ArgumentParser(prog="python -m app.cli", description="Craftboard admin commands")
    sub = ap.add_subparsers(dest="cmd", required=True)
    s = sub.add_parser("seed-demo", help="create the demo creator with history, queue, waitlist and requests")
    s.add_argument("--reset", action="store_true", help="delete and rebuild the demo creator")
    p = sub.add_parser("set-plan", help="switch a creator's plan")
    p.add_argument("email")
    p.add_argument("plan", choices=["free", "studio", "pro"])
    sub.add_parser("list", help="list creators")
    args = ap.parse_args(argv)
    if args.cmd == "seed-demo":
        seed_demo(args.reset)
    elif args.cmd == "set-plan":
        set_plan(args.email, args.plan)
    else:
        list_creators()


if __name__ == "__main__":
    main()
