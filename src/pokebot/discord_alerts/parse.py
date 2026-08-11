from __future__ import annotations

import re
from typing import Iterable

from pokebot.reseller.target_ids import resolve_target_product_url, resolve_target_tcin
from pokebot.restockr.models import RestockAlert
from pokebot.url_parser import TARGET_TCIN_RE, canonical_target_product_url

# Target PDP / share links (stop before whitespace or common markdown closers).
_TARGET_URL_RE = re.compile(
    r"https?://(?:www\.)?target\.com/[^\s<>\)\]\"']+",
    re.IGNORECASE,
)
_QTY_RE = re.compile(
    r"(?:qty|quantity|stock(?:\s*qty)?|available)\s*[:=]?\s*(\d+)",
    re.IGNORECASE,
)
_PRODUCT_NAME_RE = re.compile(
    r"(?:product|title|name)\s*[:=]\s*(.+)",
    re.IGNORECASE,
)


def iter_target_urls(*blobs: str | None) -> list[str]:
    """Deduped Target URLs found in free text / embed fields."""
    seen: set[str] = set()
    out: list[str] = []
    for blob in blobs:
        if not blob:
            continue
        for match in _TARGET_URL_RE.finditer(blob):
            raw = match.group(0).rstrip(".,;:!")
            cleaned = canonical_target_product_url(raw) or raw
            key = cleaned.lower()
            if key in seen:
                continue
            seen.add(key)
            out.append(cleaned)
    return out


def _first_qty(*blobs: str | None) -> int | None:
    for blob in blobs:
        if not blob:
            continue
        match = _QTY_RE.search(blob)
        if match:
            try:
                return int(match.group(1))
            except ValueError:
                continue
    return None


def _guess_product_name(*blobs: str | None) -> str | None:
    for blob in blobs:
        if not blob:
            continue
        match = _PRODUCT_NAME_RE.search(blob)
        if match:
            name = match.group(1).strip()
            if name:
                return name[:200]
    # Fall back to first non-URL line that looks like a title.
    for blob in blobs:
        if not blob:
            continue
        for line in blob.splitlines():
            line = line.strip()
            if not line or line.startswith("http") or "target.com" in line.lower():
                continue
            if len(line) >= 8:
                return line[:200]
    return None


def parse_discord_alert_text(
    *,
    message_id: str,
    content: str = "",
    embed_texts: Iterable[str] | None = None,
    embed_urls: Iterable[str] | None = None,
) -> RestockAlert | None:
    """Build a Target RestockAlert from Discord message text/embeds, or None."""
    texts = [content, *(embed_texts or ())]
    urls = list(embed_urls or ())
    found = iter_target_urls(*texts, *urls)
    if not found:
        # Bare TCIN in text (common in compact alert bots).
        for blob in texts:
            if not blob:
                continue
            match = TARGET_TCIN_RE.search(blob)
            if match:
                tcin = match.group(1)
                found = [f"https://www.target.com/p/-/A-{tcin}"]
                break
            bare = resolve_target_tcin(url=None, sku=blob.strip())
            if bare and blob.strip() == bare:
                found = [f"https://www.target.com/p/-/A-{bare}"]
                break
    if not found:
        return None

    url = found[0]
    tcin = resolve_target_tcin(url=url, sku=None)
    if not tcin:
        return None
    product_url = resolve_target_product_url(url, tcin=tcin)
    qty = _first_qty(*texts)
    product = _guess_product_name(*texts)

    return RestockAlert(
        id=f"discord-{message_id}",
        sku=tcin,
        store="target",
        url=product_url,
        restock_url=url,
        stock_quantity=qty,
        product=product,
        alert_type="discord",
    )
