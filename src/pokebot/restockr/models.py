from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field, field_validator

from pokebot.msrp import parse_price


class RestockAlert(BaseModel):
    id: str
    sku: str | None = None
    store: str
    stock_quantity: int | None = Field(default=None, alias="stockQuantity")
    region: str | None = None
    parent_id: str | None = Field(default=None, alias="parentId")
    url: str | None = None
    product_urls: dict[str, str] = Field(default_factory=dict, alias="productUrls")
    restock_url: str | None = Field(default=None, alias="restockUrl")
    product: str | None = None
    alert_type: str | None = Field(default=None, alias="alertType")
    new_sku_category: str | None = Field(default=None, alias="newSkuCategory")
    price: float | None = None

    model_config = {"populate_by_name": True}

    @field_validator("price", mode="before")
    @classmethod
    def coerce_price(cls, value: Any) -> float | None:
        if value is None:
            return None
        price, unknown = parse_price(value)
        return None if unknown else price

    @classmethod
    def from_socket_payload(cls, data: dict[str, Any]) -> RestockAlert:
        return cls.model_validate(data)

    def resolve_url(self, parent_id: str | None = None) -> str | None:
        if self.restock_url:
            return self.restock_url
        if parent_id and parent_id in self.product_urls:
            return self.product_urls[parent_id]
        if self.product_urls.get("default"):
            return self.product_urls["default"]
        return self.url


class UserProfile(BaseModel):
    username: str | None = None
    product_skus: list[str] = Field(default_factory=list, alias="productSkus")
    auto_open_pc_queue: bool = Field(default=False, alias="autoOpenPcQueue")
    minimum_qty: int = Field(default=1, alias="minimumQty")

    model_config = {"populate_by_name": True}


class PurchaseResult(BaseModel):
    success: bool
    retailer: str
    sku: str | None
    url: str
    order_id: str | None = None
    message: str | None = None
    attempted_at: datetime = Field(default_factory=datetime.utcnow)
