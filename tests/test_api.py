import io

GOOD_BRIEF = {"description": "Chibi of my OC Mira: silver bob haircut, round glasses, oversized green hoodie with a frog pin, "
                             "holding a steaming mug with a sleepy smile.", "references": ["toyhou.se/mira"]}


def submit(client, creator, handle="mira", type_key="chibi", brief=GOOD_BRIEF):
    return client.post(f"/api/public/{creator['handle']}/requests",
                       json={"client_handle": handle, "type_id": creator["types"][type_key]["id"], "brief": brief})


def accept_first(client, creator):
    reqs = client.get("/api/requests", headers=creator["headers"]).json()["requests"]
    r = client.post(f"/api/requests/{reqs[0]['id']}/accept", headers=creator["headers"])
    assert r.status_code == 200, r.text
    return r.json()


def test_auth_required(client):
    assert client.get("/api/dashboard").status_code == 401


def test_signup_validation(client):
    r = client.post("/api/auth/signup", json={"email": "bad", "password": "short", "name": "x", "handle": "a"})
    assert r.status_code == 422
    r = client.post("/api/auth/signup", json={"email": "a@b.co", "password": "longenough", "name": "x", "handle": "api"})
    assert r.status_code == 409


def test_full_commission_lifecycle(client, creator):
    h = creator["headers"]
    profile = client.get(f"/api/public/{creator['handle']}").json()
    assert profile["intake"]["open"] and len(profile["types"]) == 2

    live = client.post(f"/api/public/{creator['handle']}/screen",
                       json={"type_id": creator["types"]["chibi"]["id"], "brief": {"description": "surprise me"}}).json()
    assert live["verdict"] == "vague"

    assert submit(client, creator).json() == {"status": "received"}
    reqs = client.get("/api/requests", headers=h).json()["requests"]
    assert reqs[0]["screening"]["verdict"] == "ok"

    c = accept_first(client, creator)
    assert c["status"] == "awaiting_deposit" and c["price_cents"] == 6000 and c["deposit_cents"] == 3000
    token = c["portal_token"]

    # brief is locked: portal shows it, client pays deposit (simulated)
    p = client.get(f"/api/portal/{token}").json()
    assert p["brief"]["description"].startswith("Chibi of my OC Mira")
    assert "portal_token" not in p
    r = client.post(f"/api/portal/{token}/pay", json={"kind": "deposit"}).json()
    assert r["mode"] == "simulated" and r["commission"]["status"] == "queued"

    cid = c["id"]
    assert client.post(f"/api/commissions/{cid}/start", headers=h).json()["status"] == "in_progress"
    assert client.post(f"/api/commissions/{cid}/submit-wip", headers=h).json()["status"] == "in_review"

    # minor change → counted revision
    r = client.post(f"/api/portal/{token}/changes", json={"text": "Could the hoodie be a bit darker green?"}).json()
    assert r["result"]["kind"] == "revision" and r["commission"]["revisions_used"] == 1
    client.post(f"/api/commissions/{cid}/submit-wip", headers=h)

    # scope creep → quote, no revision used
    r = client.post(f"/api/portal/{token}/changes", json={"text": "Can you add my boyfriend next to her?"}).json()
    assert r["result"]["kind"] == "out_of_scope"
    q = r["commission"]["open_quote"]
    assert q and q["quote_cents"] == 3500
    blocked = client.post(f"/api/portal/{token}/changes", json={"text": "also darker shading"})
    assert blocked.status_code == 409
    p = client.post(f"/api/portal/{token}/quotes/{q['id']}/accept").json()
    assert p["price_cents"] == 9500 and p["upgrades_cents"] == 3500 and p["revisions_used"] == 1

    client.post(f"/api/commissions/{cid}/submit-wip", headers=h)
    assert client.post(f"/api/commissions/{cid}/deliver", headers=h, files=[("files", ("a.png", b"x", "image/png"))]).status_code == 409
    assert client.post(f"/api/portal/{token}/approve").json()["status"] == "approved"

    d = client.post(f"/api/commissions/{cid}/deliver", headers=h, data={"hours": "2.5"},
                    files=[("files", ("mira_final.png", b"\x89PNG fake", "image/png"))])
    assert d.status_code == 200, d.text
    d = d.json()
    assert d["status"] == "delivered" and d["balance_cents"] == 6500 and d["hours_logged"] == 2.5

    fid = d["files"][0]["id"]
    assert client.get(f"/api/portal/{token}/files/{fid}").status_code == 402
    r = client.post(f"/api/portal/{token}/pay", json={"kind": "balance"}).json()
    assert r["commission"]["status"] == "completed" and r["commission"]["files_unlocked"]
    f = client.get(f"/api/portal/{token}/files/{fid}")
    assert f.status_code == 200 and f.content == b"\x89PNG fake"

    hist = client.get("/api/history", headers=h).json()
    assert hist[0]["price_cents"] == 9500 and hist[0]["had_upgrade"] == 1

    a = client.get("/api/analytics", headers=h).json()
    assert a["delivered_180d"] == 1 and a["effective_hourly_cents"] == 3800


def test_slots_fill_then_waitlist(client, creator):
    h = creator["headers"]
    client.patch("/api/me", json={"slots": 2}, headers=h)
    for name in ("a1", "a2"):
        submit(client, creator, handle=name)
        accept_first(client, creator)
    state = client.get(f"/api/public/{creator['handle']}").json()["intake"]
    assert state == {"slots": 2, "filled": 2, "open": False, "waitlist": 0, "waitlist_enabled": True}
    r = submit(client, creator, handle="late").json()
    assert r == {"status": "waitlisted", "position": 1}
    wl = client.get("/api/waitlist", headers=h).json()
    inv = client.post(f"/api/waitlist/{wl[0]['id']}/invite", headers=h).json()
    assert "slot just opened" in inv["message"]


def test_free_plan_caps_and_gates(client):
    client.cookies.clear()
    import os
    os.environ["DEFAULT_PLAN"] = "free"
    from app.config import settings
    settings.default_plan = "free"
    try:
        r = client.post("/api/auth/signup", json={"email": "free@example.com", "password": "longenough",
                                                  "name": "Free", "handle": "freebie"})
        h = {"Authorization": "Bearer " + r.json()["token"]}
        client.cookies.clear()
        me = client.patch("/api/me", json={"slots": 10}, headers=h).json()
        assert me["features"]["max_active"] == 3
        t = client.post("/api/types", json={"name": "Sketch", "base_price": 20}, headers=h).json()
        assert client.get(f"/api/public/freebie").json()["intake"]["slots"] == 3
        assert client.get("/api/analytics", headers=h).status_code == 402
        assert client.post("/api/goals", json={"amount": 1000, "weeks": 4}, headers=h).status_code == 402
        assert t["base_price_cents"] == 2000
    finally:
        settings.default_plan = "pro"


def test_price_change_recorded_and_goal(client, creator):
    h = creator["headers"]
    tid = creator["types"]["chibi"]["id"]
    client.patch(f"/api/types/{tid}", json={"base_price": 66}, headers=h)
    csv_data = "client,type,price,hours,accepted_at,completed_at\n" + "\n".join(
        f"c{i % 7},Chibi portrait,85,3.5,2026-0{(i % 5) + 4}-01,2026-0{(i % 5) + 4}-09" for i in range(30))
    r = client.post("/api/import/csv", headers=h, files={"file": ("history.csv", csv_data, "text/csv")}).json()
    assert r["imported"] == 30 and not r["errors"]
    g = client.post("/api/goals", json={"amount": 5000, "weeks": 4, "hours_per_week": 30}, headers=h)
    assert g.status_code == 201, g.text
    g = g.json()
    assert g["plan"]["headline"] and len(g["plan"]["insights"]) == 3
    assert g["narrative"] is None and g["claude_available"] is False
    assert g["checkin"]["week"] == 1
    active = client.get("/api/goals/active", headers=h).json()["goal"]
    assert active["id"] == g["id"]
    assert client.post(f"/api/goals/{g['id']}/narrate", headers=h).status_code == 503


def test_csv_import_reports_bad_rows(client, creator):
    csv_data = "client,type,price,completed_at\nok,Sketch,40,2026-05-01\nbad,Sketch,forty,2026-05-01\n"
    r = client.post("/api/import/csv", headers=creator["headers"], files={"file": ("h.csv", csv_data, "text/csv")}).json()
    assert r["imported"] == 1 and "Row 3" in r["errors"][0]


def test_portal_token_isolation(client, creator):
    assert client.get("/api/portal/not-a-real-token").status_code == 404


def test_logout_revokes_session(client):
    r = client.post("/api/auth/signup", json={"email": "bye@example.com", "password": "correct-horse",
                                              "name": "Bye", "handle": "bye.artist"})
    h = {"Authorization": f"Bearer {r.json()['token']}"}
    client.cookies.clear()
    assert client.get("/api/me", headers=h).status_code == 200
    assert client.post("/api/auth/logout", headers=h).status_code == 200
    assert client.get("/api/me", headers=h).status_code == 401


def test_pages_served(client):
    for path in ("/login", "/app", "/c/anyone", "/p/sometoken", "/static/app.js", "/static/ui.js", "/static/app.css"):
        assert client.get(path).status_code == 200, path


def test_seed_demo_matches_pitch(client):
    from app.cli import DEMO_EMAIL, DEMO_PASSWORD, seed_demo

    seed_demo(reset=True)
    seed_demo(reset=True)  # idempotent rebuild
    r = client.post("/api/auth/login", json={"email": DEMO_EMAIL, "password": DEMO_PASSWORD})
    h = {"Authorization": f"Bearer {r.json()['token']}"}
    client.cookies.clear()
    d = client.get("/api/dashboard", headers=h).json()
    assert d["intake"]["filled"] == 4 and d["intake"]["slots"] == 6 and d["intake"]["waitlist"] == 12
    verdicts = [x["screening"]["verdict"] for x in client.get("/api/requests", headers=h).json()["requests"]]
    assert verdicts == ["ok", "vague", "red"]
    plan = client.post("/api/goals", json={"amount": 5000, "weeks": 4, "narrate": False}, headers=h).json()["plan"]
    assert plan["demand"]["strength"] == "strong" and plan["pace"]["overloaded"]


def test_csv_import_rejects_negative_and_inverted_rows(client, creator):
    csv_data = ("client,type,price,accepted_at,completed_at\n"
                "@ok,Brand new type,40,2026-04-01,2026-04-05\n"
                "neg,Brand new type,-40,2026-04-01,2026-04-05\n"
                "flip,Brand new type,40,2026-04-09,2026-04-05\n")
    r = client.post("/api/import/csv", headers=creator["headers"], files={"file": ("h.csv", csv_data, "text/csv")})
    assert r.status_code == 200, r.text
    r = r.json()
    assert r["imported"] == 1 and len(r["errors"]) == 2
    assert client.get("/api/history", headers=creator["headers"]).json()[0]["client_handle"] == "ok"


def test_waitlist_position_is_per_client(client, creator):
    client.patch("/api/me", json={"intake_mode": "closed"}, headers=creator["headers"])
    assert submit(client, creator, handle="first").json()["position"] == 1
    assert submit(client, creator, handle="second").json()["position"] == 2
    assert submit(client, creator, handle="first").json()["position"] == 1  # resubmitting keeps your place


def test_rejected_delivery_writes_no_files(client, creator):
    from app.config import settings

    h = creator["headers"]
    submit(client, creator)
    c = accept_first(client, creator)
    token, cid = c["portal_token"], c["id"]
    client.post(f"/api/portal/{token}/pay", json={"kind": "deposit"})
    client.post(f"/api/commissions/{cid}/start", headers=h)
    client.post(f"/api/commissions/{cid}/submit-wip", headers=h)
    client.post(f"/api/portal/{token}/approve")
    old = settings.max_upload_mb
    settings.max_upload_mb = 1
    try:
        r = client.post(f"/api/commissions/{cid}/deliver", headers=h,
                        files=[("files", ("small.png", b"x", "image/png")), ("files", ("big.png", b"x" * (2 << 20), "image/png"))])
    finally:
        settings.max_upload_mb = old
    assert r.status_code == 413
    folder = settings.upload_dir / str(cid)
    assert not folder.exists() or not any(folder.iterdir())


def test_stripe_webhook_handles_stripe_objects(client, creator, monkeypatch):
    import stripe
    from app.config import settings
    from app.services import payments

    submit(client, creator)
    c = accept_first(client, creator)
    conn_payment = {}

    def fake_event(payload, sig, secret):
        return stripe.Event.construct_from({"type": "checkout.session.completed", "data": {"object": {
            "id": "cs_test", "payment_status": "paid", "payment_intent": "pi_test",
            "metadata": {"payment_id": str(conn_payment["id"])}}}}, "sk_test")

    from app.db import connect
    with connect() as conn:
        conn_payment["id"] = payments._insert(conn, c["id"], "deposit", c["deposit_cents"], "stripe")
    monkeypatch.setattr(settings, "stripe_secret_key", "sk_test_x")
    monkeypatch.setattr(settings, "stripe_webhook_secret", "whsec_x")
    monkeypatch.setattr(stripe.Webhook, "construct_event", staticmethod(fake_event))
    r = client.post("/api/stripe/webhook", content=b"{}", headers={"stripe-signature": "t=1,v1=x"})
    assert r.status_code == 200, r.text
    monkeypatch.setattr(settings, "stripe_secret_key", None)
    assert client.get(f"/api/portal/{c['portal_token']}").json()["status"] == "queued"


def test_env_file_inline_comments(tmp_path, monkeypatch):
    from app import config

    env = tmp_path / ".env"
    env.write_text('CB_T1=http://localhost:8000        # used in links\nCB_T2="has # inside"\nCB_T3=plain\n', encoding="utf-8")
    for k in ("CB_T1", "CB_T2", "CB_T3"):
        monkeypatch.delenv(k, raising=False)
    config._load_dotenv(env)
    import os
    assert os.environ["CB_T1"] == "http://localhost:8000"
    assert os.environ["CB_T2"] == "has # inside"
    assert os.environ["CB_T3"] == "plain"


def test_ask_for_details_is_written_for_the_client(client, creator):
    submit(client, creator, brief={"description": "idk surprise me"})
    reqs = client.get("/api/requests", headers=creator["headers"]).json()
    assert reqs["claude_available"] is False
    msg = client.post(f"/api/requests/{reqs['requests'][0]['id']}/ask", headers=creator["headers"], json={}).json()["message"]
    assert "reference images" in msg and "Ask for" not in msg and msg.endswith("?")


def test_static_files_revalidate(client):
    assert client.get("/static/app.js").headers["cache-control"] == "no-cache"
