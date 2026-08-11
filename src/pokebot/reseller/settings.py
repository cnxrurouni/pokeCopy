from __future__ import annotations

from pathlib import Path

import yaml
from pydantic import BaseModel

from pokebot.config import config_dir


class ResellerSettings(BaseModel):
    global_concurrency: int = 10
    per_account_concurrency: int = 1
    px_token_ttl_seconds: float = 300.0
    # Outer retries when the sidecar/session is dead (not in-step ATC spam).
    max_attempts: int = 3
    max_quantity: int | None = None
    atc_spam_timeout_seconds: float = 300.0
    checkout_spam_timeout_seconds: float = 1200.0
    # Delay between ATC retries (incl. after 429 — no long Retry-After sleep).
    atc_retry_delay_ms_min: int = 500
    atc_retry_delay_ms_max: int = 1000
    # Delay between checkout / pre_checkout / place_order retries.
    spam_delay_ms_min: int = 1500
    spam_delay_ms_max: int = 3000
    # After ATC: open real Chrome on /cart → /checkout so PX can mint before APIs.
    warm_cart_checkout: bool = False
    warm_dwell_seconds: float = 3.0
    # Prefer everyday-Chrome ATC (AppleScript click); skip HTTP ATC when true.
    # Ignored when checkout_channel=mobile (mobile never browser-assists).
    browser_assist_atc: bool = True
    browser_assist_timeout_seconds: float = 120.0
    # HTTP ATC: 0 = never abort on AUTH_DENIED (keep until timeout / stock-limit).
    auth_denied_abort_after: int = 0
    # 0 = never hard-stop the step on 429 streak (cooldown + continue until timeout).
    rate_limit_abort_after: int = 0
    # Sleep this long on 429 when Response has no Retry-After header.
    rate_limit_cooldown_seconds: float = 60.0
    accounts_path: str | None = None
    capture_path: str | None = None
    # Preferred curl_cffi target; falls back to newest available if missing.
    curl_impersonate: str | None = None
    # Separate infra: "web" (desktop Chrome capture) vs "mobile" (iOS app capture).
    checkout_channel: str = "web"

    def resolved_accounts_path(self) -> Path:
        if self.accounts_path:
            return Path(self.accounts_path)
        return config_dir() / "reseller.accounts.yaml"

    def resolved_capture_path(self) -> Path:
        if self.capture_path:
            return Path(self.capture_path)
        if (self.checkout_channel or "web").lower() in ("mobile", "ios", "ios_app", "app"):
            return config_dir() / "reseller.capture.target.mobile.json"
        return config_dir() / "reseller.capture.target.json"

    @property
    def is_mobile_channel(self) -> bool:
        return (self.checkout_channel or "web").lower() in (
            "mobile",
            "ios",
            "ios_app",
            "app",
        )


def load_reseller_settings() -> ResellerSettings:
    path = config_dir() / "reseller.yaml"
    if not path.exists():
        return ResellerSettings()
    raw = yaml.safe_load(path.read_text()) or {}
    return ResellerSettings.model_validate(raw.get("reseller", raw))
