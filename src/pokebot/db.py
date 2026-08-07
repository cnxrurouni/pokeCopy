from __future__ import annotations

import aiosqlite
from datetime import datetime

from pokebot.config import data_dir
from pokebot.enums import Retailer
from pokebot.models import PollLogEntry, StockStatus


class Database:
    def __init__(self, path: str | None = None) -> None:
        self.path = path or str(data_dir() / "pokebot.db")

    async def init(self) -> None:
        async with aiosqlite.connect(self.path) as db:
            await db.execute(
                """
                CREATE TABLE IF NOT EXISTS poll_log (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    product_id TEXT NOT NULL,
                    retailer TEXT NOT NULL,
                    price REAL,
                    in_stock INTEGER NOT NULL,
                    msrp_match INTEGER NOT NULL,
                    checked_at TEXT NOT NULL
                )
                """
            )
            await db.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_poll_log_product
                ON poll_log (retailer, product_id, checked_at DESC)
                """
            )
            await db.commit()

    async def log_poll(self, status: StockStatus) -> None:
        async with aiosqlite.connect(self.path) as db:
            await db.execute(
                """
                INSERT INTO poll_log (product_id, retailer, price, in_stock, msrp_match, checked_at)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    status.product.product_id,
                    status.product.retailer.value,
                    status.price,
                    int(status.in_stock),
                    int(status.msrp_match),
                    status.checked_at.isoformat(),
                ),
            )
            await db.commit()

    async def latest_polls(self) -> list[PollLogEntry]:
        async with aiosqlite.connect(self.path) as db:
            db.row_factory = aiosqlite.Row
            cursor = await db.execute(
                """
                SELECT product_id, retailer, price, in_stock, msrp_match, checked_at
                FROM poll_log p1
                WHERE id = (
                    SELECT MAX(id) FROM poll_log p2
                    WHERE p2.retailer = p1.retailer AND p2.product_id = p1.product_id
                )
                ORDER BY checked_at DESC
                """
            )
            rows = await cursor.fetchall()
        return [
            PollLogEntry(
                product_id=row["product_id"],
                retailer=Retailer(row["retailer"]),
                price=row["price"],
                in_stock=bool(row["in_stock"]),
                msrp_match=bool(row["msrp_match"]),
                checked_at=datetime.fromisoformat(row["checked_at"]),
            )
            for row in rows
        ]
