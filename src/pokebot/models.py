from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field, HttpUrl

from pokebot.enums import Fulfillment, Retailer


class TargetFilters(BaseModel):
    query: str
    max_price: float | None = None
    in_stock: bool = False
    sold_by_target: bool = False
    category: str | None = None
    store_id: str | None = None
    sort: str = "bestselling"
    limit: int = 24


class WalmartFilters(BaseModel):
    query: str
    max_price: float | None = None
    in_stock: bool = False
    sold_by_walmart: bool = True
    category: str | None = None
    sort: str = "best_match"
    limit: int = 24


class SearchResult(BaseModel):
    retailer: Retailer
    product_id: str
    name: str
    brand: str | None = None
    price: float | None = None
    price_display: str | None = None
    price_unknown: bool = False
    comparison_price: float | None = None
    in_stock: bool | None = None
    url: str
    image_url: str | None = None
    rating: float | None = None
    rating_count: int | None = None
    seller: str | None = None
    msrp_match: bool = False


class WatchProduct(BaseModel):
    name: str
    retailer: Retailer
    product_id: str
    url: str
    max_price: float
    fulfillment: Fulfillment = Fulfillment.SHIPPING
    enabled: bool = True


class StockStatus(BaseModel):
    product: WatchProduct
    price: float | None = None
    price_display: str | None = None
    price_unknown: bool = False
    in_stock: bool = False
    fulfillment_detail: str | None = None
    msrp_match: bool = False
    checked_at: datetime = Field(default_factory=datetime.utcnow)
    error: str | None = None


class PollLogEntry(BaseModel):
    product_id: str
    retailer: Retailer
    price: float | None
    in_stock: bool
    msrp_match: bool
    checked_at: datetime
