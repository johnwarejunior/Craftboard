"""Payments: Stripe Checkout when configured, simulated otherwise.

Craftboard never takes a cut. With Stripe Connect, charges are created directly
on the creator's connected account with no application fee; the creator pays
only Stripe's standard processing.
"""
from __future__ import annotations

import sqlite3
from typing import Any

from ..config import settings
from ..db import now


class PaymentError(Exception):
    pass


def stripe_enabled() -> bool:
    return bool(settings.stripe_secret_key)


def _stripe():
    import stripe

    stripe.api_key = settings.stripe_secret_key
    return stripe


def amount_due(commission: dict[str, Any], kind: str) -> int:
    if kind == "deposit":
        return commission["deposit_cents"]
    return commission["price_cents"] - commission["deposit_cents"]


def start_payment(conn: sqlite3.Connection, commission: dict[str, Any], creator: dict[str, Any], kind: str) -> dict[str, Any]:
    """Create a pending payment and return how the client should complete it."""
    expected_status = {"deposit": "awaiting_deposit", "balance": "delivered"}[kind]
    if commission["status"] != expected_status:
        raise PaymentError(f"No {kind} is due right now.")
    amount = amount_due(commission, kind)
    if amount <= 0:
        mark_paid(conn, _insert(conn, commission["id"], kind, 0, "simulated"), None)
        return {"mode": "none", "paid": True}

    if stripe_enabled():
        payment_id = _insert(conn, commission["id"], kind, amount, "stripe")
        stripe = _stripe()
        portal = f"{settings.base_url}/p/{commission['portal_token']}"
        kwargs: dict[str, Any] = {
            "mode": "payment",
            "line_items": [{
                "quantity": 1,
                "price_data": {
                    "currency": settings.currency,
                    "unit_amount": amount,
                    "product_data": {"name": f"{commission['title']} ({kind}) · @{creator['handle']}"},
                },
            }],
            "success_url": portal + "?paid=" + kind,
            "cancel_url": portal,
            "metadata": {"payment_id": str(payment_id), "commission_id": str(commission["id"]), "kind": kind},
        }
        if creator.get("stripe_account_id"):
            kwargs["stripe_account"] = creator["stripe_account_id"]  # direct charge, no application fee
        session = stripe.checkout.Session.create(**kwargs)
        conn.execute("UPDATE payments SET provider_ref = ? WHERE id = ?", (session.id, payment_id))
        return {"mode": "stripe", "checkout_url": session.url}

    if not settings.allow_simulated_payments:
        raise PaymentError("Payments are not configured. Set STRIPE_SECRET_KEY.")
    payment_id = _insert(conn, commission["id"], kind, amount, "simulated")
    mark_paid(conn, payment_id, "sim_" + str(payment_id))
    return {"mode": "simulated", "paid": True}


def _insert(conn: sqlite3.Connection, commission_id: int, kind: str, amount: int, provider: str) -> int:
    cur = conn.execute(
        "INSERT INTO payments (commission_id, kind, amount_cents, provider, status, created_at) VALUES (?,?,?,?, 'pending', ?)",
        (commission_id, kind, amount, provider, now()),
    )
    return int(cur.lastrowid)


def mark_paid(conn: sqlite3.Connection, payment_id: int, provider_ref: str | None) -> None:
    p = conn.execute("SELECT * FROM payments WHERE id = ?", (payment_id,)).fetchone()
    if p is None or p["status"] == "paid":
        return
    ts = now()
    conn.execute("UPDATE payments SET status='paid', paid_at=?, provider_ref=COALESCE(?, provider_ref) WHERE id=?",
                 (ts, provider_ref, payment_id))
    if p["kind"] == "deposit":
        conn.execute("UPDATE commissions SET status='queued' WHERE id=? AND status='awaiting_deposit'", (p["commission_id"],))
    else:
        conn.execute("UPDATE commissions SET status='completed', completed_at=? WHERE id=? AND status='delivered'",
                     (ts, p["commission_id"]))


def handle_webhook(conn: sqlite3.Connection, payload: bytes, signature: str | None) -> str:
    if not (stripe_enabled() and settings.stripe_webhook_secret):
        raise PaymentError("Stripe webhooks are not configured.")
    stripe = _stripe()
    try:
        event = stripe.Webhook.construct_event(payload, signature or "", settings.stripe_webhook_secret)
    except Exception as exc:
        raise PaymentError(f"Invalid webhook: {exc}") from exc
    if event["type"] == "checkout.session.completed":
        session = event["data"]["object"]
        if hasattr(session, "to_dict"):  # stripe-python 12+ objects aren't dict subclasses
            session = session.to_dict()
        payment_id = int((session.get("metadata") or {}).get("payment_id", 0))
        if payment_id and session.get("payment_status") == "paid":
            mark_paid(conn, payment_id, session.get("payment_intent") or session.get("id"))
    return event["type"]


def connect_onboarding_link(conn: sqlite3.Connection, creator: dict[str, Any]) -> str:
    """Create (once) a Stripe Connect Express account and return an onboarding link."""
    if not stripe_enabled():
        raise PaymentError("Set STRIPE_SECRET_KEY to connect payouts.")
    stripe = _stripe()
    account_id = creator.get("stripe_account_id")
    if not account_id:
        account = stripe.Account.create(type="express", email=creator["email"])
        account_id = account.id
        conn.execute("UPDATE creators SET stripe_account_id=? WHERE id=?", (account_id, creator["id"]))
    link = stripe.AccountLink.create(
        account=account_id,
        refresh_url=f"{settings.base_url}/app#settings",
        return_url=f"{settings.base_url}/app#settings",
        type="account_onboarding",
    )
    return link.url
