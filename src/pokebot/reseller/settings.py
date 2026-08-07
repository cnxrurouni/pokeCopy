from __future__ import annotations

from pathlib import Path

import yaml
from pydantic import BaseModel

from pokebot.config import config_dir


class ResellerSettings(BaseModel):
    dry_run: bool = True
    global_concurrency: int = 10
    per_account_concurrency: int = 1
    px_token_ttl_seconds: float = 300.0
    # Outer retries when the sidecar/session is dead (not in-step ATC spam).
    max_attempts: int = 3
    max_quantity: int | None = None
    atc_spam_timeout_seconds: float = 90.0
    checkout_spam_timeout_seconds: float = 120.0
    # Delay between ATC retries (often needs a few tries). Random ~1–2s avoids 429.
    atc_retry_delay_ms_min: int = 1000
    atc_retry_delay_ms_max: int = 2000
    # Delay between checkout / pre_checkout / place_order retries (same pace).
    spam_delay_ms_min: int = 1000
    spam_delay_ms_max: int = 2000
    # After ATC: open real Chrome on /cart → /checkout so PX can mint before APIs.
    warm_cart_checkout: bool = False
    warm_dwell_seconds: float = 3.0
    # Abort after a few consecutive AUTH_DENIED responses (was 15 — too botty).
    auth_denied_abort_after: int = 3
    # After this many consecutive 429s (each with a cooldown), stop.
    rate_limit_abort_after: int = 3
    # Sleep this long on 429 when Response has no Retry-After header.
    rate_limit_cooldown_seconds: float = 30.0
    accounts_path: str | None = None
    capture_path: str | None = None
    # Required fingerprint knob for curl_cffi (e.g. chrome146).
    curl_impersonate: str | None = "chrome146"
    # Browser click-ATC is disabled — money path is curl_cffi HTTP only.
    browser_atc_enabled: bool = False

    def resolved_accounts_path(self) -> Path:
        if self.accounts_path:
            return Path(self.accounts_path)
        return config_dir() / "reseller.accounts.yaml"

    def resolved_capture_path(self) -> Path:
        if self.capture_path:
            return Path(self.capture_path)
        return config_dir() / "reseller.capture.target.json"


def load_reseller_settings() -> ResellerSettings:
    path = config_dir() / "reseller.yaml"
    if not path.exists():
        return ResellerSettings()
    raw = yaml.safe_load(path.read_text()) or {}
    return ResellerSettings.model_validate(raw.get("reseller", raw))
