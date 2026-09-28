"""Configuration, read from environment variables (and an optional .env file)."""
from __future__ import annotations

import os
import re
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent


def _load_dotenv(path: Path) -> None:
    """Tiny .env loader so there is no extra dependency. Real env vars win."""
    if not path.exists():
        return
    for line in path.read_text(encoding="utf-8-sig").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        value = value.strip()
        if value[:1] in {'"', "'"} and value.count(value[0]) >= 2:
            value = value[1:value.index(value[0], 1)]      # quoted: keep everything inside the quotes
        else:
            value = re.split(r"\s+#", value, maxsplit=1)[0].strip()  # drop inline "  # comment"
        os.environ.setdefault(key.strip(), value)


_load_dotenv(BASE_DIR / ".env")


def _bool(value: str | None, default: bool) -> bool:
    if value is None or value == "":
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


class Settings:
    def __init__(self) -> None:
        self.database_path = Path(os.getenv("CRAFTBOARD_DB", BASE_DIR / "data" / "craftboard.db"))
        self.upload_dir = Path(os.getenv("CRAFTBOARD_UPLOADS", BASE_DIR / "data" / "uploads"))
        self.base_url = os.getenv("BASE_URL", "http://localhost:8000").rstrip("/")
        self.default_plan = os.getenv("DEFAULT_PLAN", "free")
        self.session_days = int(os.getenv("SESSION_DAYS", "30"))
        self.max_upload_mb = int(os.getenv("MAX_UPLOAD_MB", "50"))

        # Claude API (optional). Without a key, the goal agent uses template text.
        self.anthropic_api_key = os.getenv("ANTHROPIC_API_KEY") or None
        self.claude_model = os.getenv("CLAUDE_MODEL", "claude-sonnet-5")

        # Stripe (optional). Without a key, payments run in simulated mode.
        self.stripe_secret_key = os.getenv("STRIPE_SECRET_KEY") or None
        self.stripe_webhook_secret = os.getenv("STRIPE_WEBHOOK_SECRET") or None
        self.currency = os.getenv("CURRENCY", "usd")
        self.allow_simulated_payments = _bool(
            os.getenv("ALLOW_SIMULATED_PAYMENTS"), default=self.stripe_secret_key is None
        )

    @property
    def secure_cookies(self) -> bool:
        return self.base_url.startswith("https://")


settings = Settings()
