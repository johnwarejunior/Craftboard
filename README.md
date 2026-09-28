# Craftboard MVP

**Run your commission business, not your DMs.**

Craftboard is the back office for independent creatives who take commissions: intake, queue, revisions, payments and pricing insight, for one flat monthly price with no cut of the creator's sales. This repository is a working MVP of the product described in the pre-seed pitch deck. It is a small FastAPI + SQLite app with a vanilla-JS frontend, so it runs anywhere Python does with no build step.

## What's in it

Everything below works end to end, and each item maps to a slide in the pitch.

| Pitch promise | What the MVP does |
|---|---|
| Smart intake forms | Public form at `/c/<handle>` with one option per commission type. Briefs are screened live as the client types (vague wording, missing references, red flags such as "exposure", discount asks, rush timelines, pay-later, NFT/AI-training use, commercial use on a personal license) and scored 1–5 for complexity. Extra characters are priced automatically. |
| AI screening (Pro) | Optional "second read from Claude" on any request: a one-line summary, follow-up questions and a risk level. The rule-based screen always runs; Claude only adds to it. |
| Intelligent queue | Slot-based capacity. In `auto` mode intake closes when the last slot fills and new requests join the waitlist on their own (Studio and Pro). Creators invite waitlisted clients when a slot opens. |
| Revision protection | The brief locks on acceptance. The client portal counts included revisions; requests that add characters, backgrounds, poses, outfits, versions or a restart are flagged as scope creep and quoted as a paid upgrade (rounded to $5). Once included revisions are used, further ones are quoted as paid revisions. |
| Payments | Deposit on acceptance (percentage set by the creator), balance on delivery, files unlock only after the balance is paid. Stripe Checkout with Connect direct charges and **no application fee**; simulated mode when Stripe isn't configured; "mark paid" for money received outside Craftboard. |
| Goal-setting agent (Pro) | Set a revenue goal and a deadline. A deterministic pricing engine reads the creator's own history (best month, average price, effective hourly rate, how bookings responded to past raises, waitlist, pace and turnaround) and returns a plan: price options, whether each fits capacity, a recommendation that stretches the timeline if nothing fits, weekly milestones and Pricing / Demand / Pace advice. Claude explains the plan in plain words and never changes the numbers. |
| Business analytics (Studio) | Effective hourly rate, average commission, repeat-client revenue share, revenue by 30-day stretch, burnout risk (pace vs. 6-month average, turnaround slowdown, hours queued), what each type really pays per hour, revision and scope-creep rates, top clients. |
| You own everything | Import past commissions from CSV; export full history as CSV anytime. |
| Free / Studio / Pro | Plan gating mirrors the pricing slide: Free allows 3 active commissions with no waitlist, analytics or AI; Studio adds unlimited slots, waitlist and analytics; Pro adds the goal agent and Claude screening. |

## Quick start

Requires Python 3.10+.

```bash
cd craftboard-mvp/Craftboard
python -m venv .venv && source .venv/bin/activate      # Windows: .venv\Scripts\activate
pip install -r requirements.txt
cp .env.example .env                                     # optional; Windows: copy .env.example .env

python -m app.cli seed-demo                              # demo artist with 6 months of history
uvicorn app.main:app --reload
```

Open http://localhost:8000 and sign in as **tamsin@example.com / craftboard-demo**. The seed command also prints client-portal links for the four commissions in the queue, so you can play the client in another browser window. The demo matches the pitch: 4 of 6 slots filled, 12 people on the waitlist, three incoming requests (one clean, one vague, one full of red flags), and two past price raises that held.

To start fresh instead, create an account at `/login#signup`. New accounts start on `DEFAULT_PLAN` (Free unless you change it). Add commission types in Settings, then share your `/c/<handle>` link.

### Admin commands

```bash
python -m app.cli seed-demo [--reset]         # create (or rebuild) the demo creator
python -m app.cli set-plan EMAIL studio       # free | studio | pro
python -m app.cli list                        # list creators
```

Subscription billing isn't built yet, so plans are switched with `set-plan`.

## A commission, start to finish

1. The client opens `/c/<handle>`, picks a type, writes a brief and sees live screening and an estimated price.
2. The request lands in **Requests** with its flags and complexity. The creator accepts (optionally overriding the price), asks for details, declines, or asks Claude for a second read. Accepting locks the brief, fills a slot and copies the client's portal link.
3. The client pays the deposit in their portal at `/p/<token>`.
4. The creator starts work, logs hours and sends a WIP. The client approves or asks for changes. Small changes use up an included revision; scope creep gets an upgrade quote the client can accept or decline.
5. The creator uploads the final files. The client pays the balance, the files unlock, and the commission feeds analytics and the goal agent.

Portal links are unguessable tokens, so clients never need an account.

## Configuration

All settings are environment variables; a `.env` file in the project root is read automatically. See `.env.example`.

| Variable | Default | Purpose |
|---|---|---|
| `CRAFTBOARD_DB` | `data/craftboard.db` | SQLite database path |
| `CRAFTBOARD_UPLOADS` | `data/uploads` | Delivered files |
| `BASE_URL` | `http://localhost:8000` | Used in links and Stripe redirects. An `https://` URL turns on secure cookies. |
| `DEFAULT_PLAN` | `free` | Plan for new signups |
| `SESSION_DAYS` | `30` | Session lifetime |
| `MAX_UPLOAD_MB` | `50` | Per-file upload limit |
| `ANTHROPIC_API_KEY` | – | Enables Claude plan explanations and brief reviews |
| `CLAUDE_MODEL` | `claude-sonnet-5` | Model used for both |
| `STRIPE_SECRET_KEY` | – | Enables Stripe Checkout and Connect onboarding |
| `STRIPE_WEBHOOK_SECRET` | – | Verifies webhook signatures |
| `CURRENCY` | `usd` | Checkout currency |
| `ALLOW_SIMULATED_PAYMENTS` | true without a Stripe key | "Pay" marks payments paid instantly. Keep it off in production. |

### Stripe

1. Use test-mode keys first. Set `STRIPE_SECRET_KEY` and enable Connect in the Stripe dashboard.
2. Each creator clicks **Connect Stripe** in Settings, which creates an Express account and sends them through Stripe's onboarding.
3. Client payments are Checkout sessions created **directly on the creator's connected account with no application fee**, so money goes straight to the creator and Craftboard takes nothing. Stripe's standard processing applies, as the pitch says.
4. Add a webhook endpoint at `<BASE_URL>/api/stripe/webhook` for `checkout.session.completed`. Because charges live on connected accounts, create it as a **Connect** webhook ("events on connected accounts"). For local testing: `stripe listen --forward-connect-to localhost:8000/api/stripe/webhook`, then put the printed secret in `STRIPE_WEBHOOK_SECRET`.

If a creator hasn't connected Stripe yet, Checkout runs on the platform account, which is fine for testing but not how you'd run production.

### Claude

The design rule from the pitch holds in code: **the engine does the math, Claude explains it.** `app/engines/pricing.py` computes every number. `app/services/claude.py` sends Claude those facts with instructions to use only the numbers provided, and it never feeds anything back into the plan. Brief reviews return structured JSON (summary, questions, risk) that is shown beside the rule-based screen, not instead of it. Without an API key, the app uses the engine's own sentences and hides the Claude buttons.

## Architecture

```
app/
  main.py            FastAPI app, page routes, static files
  config.py          settings from env / .env
  db.py, schema.sql  SQLite (money in integer cents, timestamps in Unix seconds)
  security.py        PBKDF2 password hashing, hashed session tokens, portal tokens
  deps.py            current creator (cookie or Bearer token), plan gating (HTTP 402)
  repo.py            shared queries, plan table, intake state, commission view
  models.py          pydantic request bodies
  engines/           pure, tested business logic
    screening.py     brief screening and revision scope checks
    pricing.py       history stats, demand and pace signals, goal plans, weekly check-ins
    analytics.py     insights dashboard
  services/
    claude.py        optional Claude layer
    payments.py      Stripe Checkout / Connect, webhook, simulated mode
  routes/            auth, creator, agent (goals), public (intake + portal + webhook)
  static/            login, app (creator), intake, portal pages; shared ui.js and app.css
  cli.py             seed-demo, set-plan, list
tests/               engine unit tests and API lifecycle tests
samples/             past_commissions.csv for the importer
```

**Commission lifecycle:** `awaiting_deposit → queued → in_progress ⇄ in_review → approved → delivered → completed`, and `cancelled` from any open state. With a 0% deposit, accepted commissions go straight to `queued`; with nothing left to pay on delivery, they go straight to `completed`.

**Data model:** `creators`, `sessions`, `commission_types`, `price_changes` (every price edit, so the agent can see how demand reacted), `clients`, `requests` (brief + screening before acceptance), `waitlist`, `commissions` (locked brief, price, deposit, revisions, hours, portal token), `revision_requests` (revision / out_of_scope / paid_revision with quote and status), `payments`, `deliverables`, `goals`.

## API

Interactive docs are at `/docs` while the server runs. Creator endpoints need the `cb_session` cookie or `Authorization: Bearer <token>` (signup and login return a token).

| Method & path | Purpose |
|---|---|
| `POST /api/auth/signup`, `/login`, `/logout` | Accounts and sessions |
| `GET, PATCH /api/me` | Profile and studio settings (slots, intake mode, revisions, deposit %, hours/week, intake message) |
| `GET, POST /api/types`, `PATCH /api/types/{id}` | Commission types; price edits are recorded |
| `GET /api/dashboard` | Intake state, open queue, headline stats |
| `GET /api/requests`, `POST /api/requests/{id}/accept · decline · ask · rescreen · claude-review` | Incoming requests |
| `GET /api/waitlist`, `POST /api/waitlist/{id}/invite · remove` | Waitlist (Studio) |
| `GET /api/commissions/{id}`, `POST …/start · submit-wip · hours · deliver · cancel · mark-paid` | Queue work (deliver is multipart with files) |
| `GET /api/history`, `GET /api/analytics` | History and insights (analytics needs Studio) |
| `POST /api/import/csv` | Import past commissions |
| `POST /api/goals`, `GET /api/goals/active`, `POST /api/goals/{id}/narrate`, `DELETE /api/goals/{id}` | Goal agent (Pro) |
| `POST /api/stripe/connect`, `POST /api/stripe/webhook` | Stripe onboarding and webhook |
| `GET /api/public/{handle}`, `POST …/screen`, `POST …/requests` | Public intake |
| `GET /api/portal/{token}`, `POST …/pay · approve · changes · quotes/{id}/accept · quotes/{id}/decline`, `GET …/files/{id}` | Client portal |

Plan-gated endpoints return **402** with the plan needed.

### CSV import

Header row required. Columns `client, type, price, completed_at` plus optional `hours, accepted_at, revisions`. Dates are `YYYY-MM-DD` (US and EU slash formats also work). Unknown types are created as inactive types so history stays grouped. Bad rows are skipped and reported by row number. See `samples/past_commissions.csv`.

## Tests

```bash
pytest -q
```

33 tests cover the screening and scope rules, the pitch's goal example ($5,000 at an $85 average takes 59 commissions against a best month of 24), demand and pace signals, the full commission lifecycle through the API including upgrades and file unlock, waitlisting when slots fill, Free-plan limits and gating, price-change recording, CSV import errors, portal token isolation, logout, page serving, the demo seed, Stripe webhook parsing, `.env` inline comments, waitlist positions, rejected uploads, and client-facing follow-up questions.

## Known limitations and next steps

These are deliberate MVP cuts, roughly in the order the roadmap would take them:

- **Notifications.** Nothing emails the client or creator yet. Portal links, follow-up questions and waitlist invites are copied to the clipboard for the creator to send. Transactional email is the next thing to add.
- **Subscription billing** for Studio and Pro (Stripe Billing), replacing `set-plan`.
- **WIP previews in the portal.** Creators share WIP images outside Craftboard today; only final files are uploaded.
- **Abuse protection** on the public intake and screening endpoints (rate limiting, CAPTCHA).
- **Google Forms import** alongside CSV, and a "Made with Craftboard" referral link on intake pages.
- **Opt-in benchmark data** and market reports, which need many creators first.
- **Scale-out.** SQLite with one connection per request suits a single server. Move to Postgres and object storage for files before running multiple instances.

The interactive pitch demo (a separate single-page build with simulated data) is published alongside this MVP.
