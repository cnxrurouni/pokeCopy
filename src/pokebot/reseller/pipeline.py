from __future__ import annotations

import asyncio

from rich.console import Console

from pokebot.enums import Retailer
from pokebot.quantity import resolve_quantity_cap
from pokebot.reseller.checkout.base import CheckoutClient, CheckoutContext
from pokebot.reseller.checkout.target_http import TargetHttpCheckout
from pokebot.reseller.impersonation import curl_impersonate_for_channel
from pokebot.reseller.fingerprint_contract import resolve_client_identity
from pokebot.reseller.models import (
    Account,
    CheckoutTask,
    HarvestedToken,
    TaskResult,
    TaskStatus,
    TokenKind,
    _utcnow,
)
from pokebot.reseller.resources import AccountStore, FingerprintFactory, ProxyManager
from pokebot.reseller.scheduler import TaskScheduler
from pokebot.reseller.settings import ResellerSettings, load_reseller_settings
from pokebot.reseller.token_bank import TokenBank
from pokebot.restockr.models import RestockAlert
from pokebot.session_auth import load_session_auth, missing_sidecar_cookies

console = Console()


def token_from_sidecar(account_id: str, *, ttl_seconds: float) -> HarvestedToken | None:
    """Build a checkout token jar from the Chrome-exported sidecar (no harvest)."""
    cookies = load_session_auth("target")
    missing = missing_sidecar_cookies(cookies)
    if missing:
        return None
    return HarvestedToken(
        kind=TokenKind.PX3,
        retailer=Retailer.TARGET,
        value=cookies.get("_px3") or "",
        cookies=dict(cookies),
        ttl_seconds=ttl_seconds,
        account_id=account_id,
        created_at=_utcnow(),
    )


class TargetPipeline:
    """Target acquisition: RestockR alert -> sidecar cookies -> HTTP checkout."""

    retailer = Retailer.TARGET

    def __init__(
        self,
        *,
        settings: ResellerSettings,
        accounts: AccountStore,
        proxies: ProxyManager,
        token_bank: TokenBank,
        checkout: CheckoutClient,
        scheduler: TaskScheduler,
        fingerprints: FingerprintFactory | None = None,
    ) -> None:
        self.settings = settings
        self.accounts = accounts
        self.proxies = proxies
        self.token_bank = token_bank
        self.checkout = checkout
        self.scheduler = scheduler
        self.fingerprints = fingerprints or FingerprintFactory()

    @classmethod
    def build(cls, settings: ResellerSettings | None = None) -> TargetPipeline:
        settings = settings or load_reseller_settings()
        accounts = AccountStore.from_yaml(settings.resolved_accounts_path())
        proxies = ProxyManager.from_yaml(settings.resolved_accounts_path())
        token_bank = TokenBank()
        impersonate = curl_impersonate_for_channel(
            "chrome", override=settings.curl_impersonate or "chrome146"
        )
        identity = resolve_client_identity(
            "chrome", curl_impersonate_override=impersonate
        )
        checkout = TargetHttpCheckout(
            dry_run=settings.dry_run,
            impersonate=identity.curl_impersonate,
            identity=identity,
            capture_path=settings.resolved_capture_path(),
            atc_spam_timeout_seconds=settings.atc_spam_timeout_seconds,
            checkout_spam_timeout_seconds=settings.checkout_spam_timeout_seconds,
            atc_retry_delay_ms_min=settings.atc_retry_delay_ms_min,
            atc_retry_delay_ms_max=settings.atc_retry_delay_ms_max,
            spam_delay_ms_min=settings.spam_delay_ms_min,
            spam_delay_ms_max=settings.spam_delay_ms_max,
            auth_denied_abort_after=settings.auth_denied_abort_after,
            rate_limit_abort_after=settings.rate_limit_abort_after,
            rate_limit_cooldown_seconds=settings.rate_limit_cooldown_seconds,
            warm_cart_checkout=settings.warm_cart_checkout,
            warm_dwell_seconds=settings.warm_dwell_seconds,
        )
        scheduler = TaskScheduler(
            per_account_concurrency=settings.per_account_concurrency,
            global_concurrency=settings.global_concurrency,
        )
        return cls(
            settings=settings,
            accounts=accounts,
            proxies=proxies,
            token_bank=token_bank,
            checkout=checkout,
            scheduler=scheduler,
        )

    def ensure_default_account(self) -> bool:
        if self.accounts.all(self.retailer):
            return False
        self.accounts.add(AccountStore.default_session_account(self.retailer))
        return True

    def task_from_alert(
        self, alert: RestockAlert, *, parent_id: str | None = None
    ) -> CheckoutTask | None:
        from pokebot.reseller.target_ids import (
            resolve_target_product_url,
            resolve_target_tcin,
        )

        navigate_url = alert.resolve_url(parent_id)
        if navigate_url is None and not alert.url:
            return None
        navigate_url = navigate_url or alert.url
        tcin = resolve_target_tcin(url=navigate_url, sku=alert.sku or alert.id)
        if not tcin and alert.url and alert.url != navigate_url:
            tcin = resolve_target_tcin(url=alert.url, sku=alert.sku or alert.id)
        product_url = resolve_target_product_url(alert.url or navigate_url, tcin=tcin)
        sku = tcin or alert.sku or alert.id
        return CheckoutTask(
            retailer=self.retailer,
            sku=sku,
            product_url=product_url,
            navigate_url=navigate_url,
            max_price=alert.price,
            max_quantity=resolve_quantity_cap(
                stock_quantity=alert.stock_quantity,
                config_cap=self.settings.max_quantity,
            ),
        )

    async def handle_alert(
        self, alert: RestockAlert, *, parent_id: str | None = None
    ) -> TaskResult | None:
        task = self.task_from_alert(alert, parent_id=parent_id)
        if task is None:
            return None

        account = self.accounts.acquire(self.retailer)
        if account is None:
            console.print("[yellow]No available Target account for task[/yellow]")
            return TaskResult(
                task_id=task.id,
                retailer=self.retailer,
                sku=task.sku,
                success=False,
                status=TaskStatus.FAILED,
                message="no available account",
            )

        task.account_id = account.id
        task.status = TaskStatus.ASSIGNED
        try:
            return await self.scheduler.run(
                account.id, lambda: self._execute_task(task, account)
            )
        finally:
            self.accounts.release(account)

    async def _execute_task(self, task: CheckoutTask, account: Account) -> TaskResult:
        from pokebot.reseller.target_ids import is_plausible_tcin

        if not self.settings.dry_run and not is_plausible_tcin(task.sku):
            return TaskResult(
                task_id=task.id,
                retailer=self.retailer,
                sku=task.sku,
                success=False,
                status=TaskStatus.FAILED,
                message=(
                    f"invalid Target TCIN {task.sku!r} — refuse checkout "
                    "(need numeric TCIN from product URL)"
                ),
                account_id=account.id,
                attempts=0,
            )

        proxy = self.proxies.for_account(account)
        last_error: str | None = None
        for attempt in range(1, self.settings.max_attempts + 1):
            task.attempts = attempt
            task.status = TaskStatus.TOKEN_WAIT

            if self.settings.dry_run:
                token = HarvestedToken(
                    kind=TokenKind.PX3,
                    retailer=self.retailer,
                    value="dry-run",
                    cookies={"_px3": "dry-run", "accessToken": "dry-run"},
                    ttl_seconds=60,
                    account_id=account.id,
                )
            else:
                token = await self.token_bank.acquire(
                    self.retailer, TokenKind.PX3, account_id=account.id
                )
                if token is None:
                    token = token_from_sidecar(
                        account.id, ttl_seconds=self.settings.px_token_ttl_seconds
                    )
                    if token is not None:
                        await self.token_bank.deposit(token)
                        token = await self.token_bank.acquire(
                            self.retailer, TokenKind.PX3, account_id=account.id
                        )

                if token is None:
                    missing = missing_sidecar_cookies(load_session_auth("target"))
                    last_error = (
                        "no usable auth/_px3 sidecar "
                        f"(missing {missing or 'jar'}). Run: python -m pokebot login target"
                    )
                    console.print(f"[yellow]{last_error}[/yellow]")
                    task.status = TaskStatus.FAILED
                    return TaskResult(
                        task_id=task.id,
                        retailer=self.retailer,
                        sku=task.sku,
                        success=False,
                        status=TaskStatus.FAILED,
                        message=last_error,
                        account_id=account.id,
                        attempts=attempt,
                    )

            task.status = TaskStatus.CHECKING_OUT
            if account.fingerprint is None:
                account.fingerprint = self.fingerprints.build(account)
            console.print(
                f"[cyan]HTTP checkout[/cyan] — add_to_cart for {task.sku} "
                f"(curl_cffi; cookies from Chrome sidecar)"
            )
            outcome = await self.checkout.place_order(
                CheckoutContext(
                    task=task, account=account, proxy=proxy, token=token
                )
            )
            if outcome.success:
                task.status = TaskStatus.PLACED
                label = (
                    "Preflight ok"
                    if getattr(self.checkout, "preflight", False)
                    else "Order placed"
                )
                console.print(
                    f"[bold green]{label}[/bold green] — {task.sku} "
                    f"({outcome.order_id or 'no order id'})"
                )
                return TaskResult(
                    task_id=task.id,
                    retailer=self.retailer,
                    sku=task.sku,
                    success=True,
                    status=TaskStatus.PLACED,
                    order_id=outcome.order_id,
                    message=outcome.message,
                    account_id=account.id,
                    attempts=attempt,
                )
            last_error = outcome.message or "checkout failed"
            console.print(f"[red]Attempt {attempt} failed:[/red] {last_error}")
            if not outcome.retryable:
                task.status = TaskStatus.FAILED
                return TaskResult(
                    task_id=task.id,
                    retailer=self.retailer,
                    sku=task.sku,
                    success=False,
                    status=TaskStatus.FAILED,
                    message=outcome.message,
                    account_id=account.id,
                    attempts=attempt,
                )
            task.status = TaskStatus.RETRY

        return TaskResult(
            task_id=task.id,
            retailer=self.retailer,
            sku=task.sku,
            success=False,
            status=TaskStatus.FAILED,
            message=(
                f"exhausted {self.settings.max_attempts} attempts"
                + (f": {last_error}" if last_error else "")
            ),
            account_id=account.id,
            attempts=self.settings.max_attempts,
        )


async def run_dry_run(product_url: str, *, sku: str = "TEST-SKU") -> TaskResult | None:
    """Exercise the Target pipeline offline with a synthetic account+alert."""
    settings = load_reseller_settings()
    settings.dry_run = True
    pipeline = TargetPipeline.build(settings)
    if not pipeline.accounts.all(Retailer.TARGET):
        pipeline.accounts.add(AccountStore.synthetic(Retailer.TARGET))

    alert = RestockAlert(id=sku, sku=sku, store="target", url=product_url)
    return await pipeline.handle_alert(alert)
