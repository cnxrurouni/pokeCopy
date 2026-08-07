from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass

from pokebot.enums import Retailer
from pokebot.reseller.models import Account, CheckoutTask, HarvestedToken, ProxyEndpoint


@dataclass
class CheckoutContext:
    task: CheckoutTask
    account: Account
    proxy: ProxyEndpoint | None
    token: HarvestedToken | None


@dataclass
class CheckoutOutcome:
    success: bool
    order_id: str | None = None
    message: str | None = None
    retryable: bool = False


class CheckoutClient(ABC):
    retailer: Retailer

    @abstractmethod
    async def place_order(self, ctx: CheckoutContext) -> CheckoutOutcome:
        """Run cart -> checkout -> place-order over HTTP with the given token."""
