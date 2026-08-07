from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone
from enum import StrEnum

from pydantic import BaseModel, Field

from pokebot.enums import Retailer


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _as_aware_utc(value: datetime) -> datetime:
    """Normalize naive/aware datetimes so freshness math never mixes kinds."""
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def _new_id(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex[:12]}"


class TokenKind(StrEnum):
    """Anti-bot token varieties the pipeline mints and spends.

    Both Target and Walmart run PerimeterX/HUMAN (confirmed live 2026-08 — Target
    serves _px3/_pxvid/pxcts, not Akamai _abck), so PX3 is the primary token. The
    Akamai kind is kept for retailers that use Shape.
    """

    PX3 = "px3"
    AKAMAI = "akamai"


class TaskStatus(StrEnum):
    QUEUED = "queued"
    ASSIGNED = "assigned"
    TOKEN_WAIT = "token_wait"
    HARVESTING = "harvesting"
    CHECKING_OUT = "checking_out"
    PLACED = "placed"
    RETRY = "retry"
    FAILED = "failed"
    VERIFIED = "verified"
    CANCELLED = "cancelled"


TERMINAL_STATUSES = frozenset({TaskStatus.VERIFIED, TaskStatus.CANCELLED, TaskStatus.FAILED})


class ProxyEndpoint(BaseModel):
    """A single upstream proxy, pinned 1:1 to an account in production."""

    label: str
    server: str  # e.g. "http://host:port" or "socks5://host:port"
    username: str | None = None
    password: str | None = None
    geo: str | None = None  # ZIP/region used for geo matching
    sticky: bool = True

    def as_dict_proxy(self) -> dict[str, str]:
        proxy: dict[str, str] = {"server": self.server}
        if self.username:
            proxy["username"] = self.username
        if self.password:
            proxy["password"] = self.password
        return proxy

    def as_curl_proxy(self) -> str | None:
        """Return a proxy URL with embedded credentials for curl_cffi/requests."""
        if not self.username:
            return self.server
        scheme, _, rest = self.server.partition("://")
        if not rest:
            return self.server
        creds = self.username if not self.password else f"{self.username}:{self.password}"
        return f"{scheme}://{creds}@{rest}"


class FingerprintProfile(BaseModel):
    """Per-account browser fingerprint. Locale/timezone should match proxy geo."""

    user_agent: str
    locale: str = "en-US"
    timezone_id: str = "America/Los_Angeles"
    canvas_seed: int = 0


class Account(BaseModel):
    """A retailer account with its pinned resources.

    Secrets live here (password, saved session cookies); the store file must be
    gitignored. One account is pinned to one proxy and one fingerprint.
    """

    id: str = Field(default_factory=lambda: _new_id("acct"))
    retailer: Retailer
    email: str
    password: str | None = None
    aged: bool = False
    proxy_label: str | None = None
    fingerprint: FingerprintProfile | None = None
    payment_label: str | None = None
    session_cookies: dict[str, str] = Field(default_factory=dict)
    enabled: bool = True


class HarvestedToken(BaseModel):
    """An anti-bot token minted by a harvester, valid until it expires."""

    kind: TokenKind
    retailer: Retailer
    value: str = ""
    cookies: dict[str, str] = Field(default_factory=dict)
    ttl_seconds: float = 300.0
    account_id: str | None = None
    created_at: datetime = Field(default_factory=_utcnow)

    @property
    def expires_at(self) -> datetime:
        return self.created_at + timedelta(seconds=self.ttl_seconds)

    def is_fresh(self, *, at: datetime | None = None, margin_seconds: float = 5.0) -> bool:
        now = _as_aware_utc(at or _utcnow())
        created = _as_aware_utc(self.created_at)
        age = (now - created).total_seconds()
        return age < (self.ttl_seconds - margin_seconds)


class CheckoutTask(BaseModel):
    id: str = Field(default_factory=lambda: _new_id("task"))
    retailer: Retailer
    sku: str
    product_url: str
    # Alert/shortlink to open in the harvest browser (may redirect to product_url).
    navigate_url: str | None = None
    max_price: float | None = None
    max_quantity: int | None = None
    status: TaskStatus = TaskStatus.QUEUED
    account_id: str | None = None
    attempts: int = 0
    message: str | None = None
    created_at: datetime = Field(default_factory=_utcnow)


class TaskResult(BaseModel):
    task_id: str
    retailer: Retailer
    sku: str
    success: bool
    status: TaskStatus
    order_id: str | None = None
    message: str | None = None
    account_id: str | None = None
    attempts: int = 0
    finished_at: datetime = Field(default_factory=_utcnow)
