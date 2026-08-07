from __future__ import annotations

from pathlib import Path

import yaml

from pokebot.enums import Retailer
from pokebot.reseller.models import Account, FingerprintProfile, ProxyEndpoint
_DEFAULT_USER_AGENTS = (
    # Kept for backward-compat callers; FingerprintFactory prefers ClientIdentity.
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/146.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/146.0.0.0 Safari/537.36",
)


class ProxyManager:
    """Holds the proxy pool and enforces one static proxy per account."""

    def __init__(self, proxies: list[ProxyEndpoint] | None = None) -> None:
        self._by_label: dict[str, ProxyEndpoint] = {p.label: p for p in (proxies or [])}

    @classmethod
    def from_yaml(cls, path: str | Path) -> ProxyManager:
        p = Path(path)
        if not p.exists():
            return cls([])
        raw = yaml.safe_load(p.read_text()) or {}
        proxies = [ProxyEndpoint.model_validate(x) for x in raw.get("proxies", [])]
        return cls(proxies)

    def add(self, proxy: ProxyEndpoint) -> None:
        self._by_label[proxy.label] = proxy

    def get(self, label: str | None) -> ProxyEndpoint | None:
        if label is None:
            return None
        return self._by_label.get(label)

    def for_account(self, account: Account) -> ProxyEndpoint | None:
        return self.get(account.proxy_label)

    def __len__(self) -> int:
        return len(self._by_label)


class FingerprintFactory:
    """Generates deterministic per-account fingerprints, ideally geo-matched."""

    def __init__(self, user_agents: tuple[str, ...] = _DEFAULT_USER_AGENTS) -> None:
        self._user_agents = user_agents

    def build(self, account: Account, *, geo_timezone: str | None = None) -> FingerprintProfile:
        from pokebot.reseller.fingerprint_contract import resolve_client_identity

        seed = abs(hash(account.id)) % (2**31)
        ident = resolve_client_identity("chrome")
        return FingerprintProfile(
            user_agent=ident.user_agent,
            locale="en-US",
            timezone_id=geo_timezone or "America/Los_Angeles",
            canvas_seed=seed,
        )


class AccountStore:
    """Loads accounts and enforces pinned resources per account.

    Accounts carry secrets, so the backing file must be gitignored. In dry-run
    mode a synthetic account can be created without a file.
    """

    def __init__(self, accounts: list[Account] | None = None) -> None:
        self._accounts: list[Account] = list(accounts or [])
        self._in_use: set[str] = set()

    @classmethod
    def from_yaml(cls, path: str | Path) -> AccountStore:
        p = Path(path)
        if not p.exists():
            return cls([])
        raw = yaml.safe_load(p.read_text()) or {}
        accounts = [Account.model_validate(a) for a in raw.get("accounts", [])]
        return cls(accounts)

    def add(self, account: Account) -> None:
        self._accounts.append(account)

    def all(self, retailer: Retailer) -> list[Account]:
        return [a for a in self._accounts if a.retailer == retailer and a.enabled]

    def acquire(self, retailer: Retailer) -> Account | None:
        """Return an enabled account not currently running a checkout."""
        for account in self.all(retailer):
            if account.id not in self._in_use:
                self._in_use.add(account.id)
                return account
        return None

    def release(self, account: Account) -> None:
        self._in_use.discard(account.id)

    @staticmethod
    def synthetic(retailer: Retailer, *, email: str = "dryrun@example.com") -> Account:
        return Account(
            retailer=retailer,
            email=email,
            aged=True,
            proxy_label=None,
            payment_label="dry-run-card",
        )

    @staticmethod
    def default_session_account(retailer: Retailer) -> Account:
        """Account bound to the saved ``pokebot login <retailer>`` browser session.

        For a solo reseller who logged in once (Chrome export →
        ``data/sessions/<retailer>-auth.json``) but has no reseller accounts YAML,
        this gives the pipeline a usable account so the token bank and checkout can
        run against that session. The id is stable so a sidecar token stays matched
        to the same account across runs.
        """
        return Account(
            id=f"session-{retailer.value}",
            retailer=retailer,
            email=f"{retailer.value}-browser-session",
            aged=True,
            proxy_label=None,
        )
