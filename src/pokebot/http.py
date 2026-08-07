from __future__ import annotations

import httpx

from pokebot.platform_util import browser_ua_platform

_ua_os, _ua_platform = browser_ua_platform()

DEFAULT_HEADERS = {
    "User-Agent": (
        f"Mozilla/5.0 ({_ua_os}) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/122.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
    "sec-ch-ua": '"Chromium";v="122", "Not(A:Brand";v="24", "Google Chrome";v="122"',
    "sec-ch-ua-mobile": "?0",
    "sec-ch-ua-platform": _ua_platform,
    "sec-fetch-dest": "document",
    "sec-fetch-mode": "navigate",
    "sec-fetch-site": "none",
    "sec-fetch-user": "?1",
    "upgrade-insecure-requests": "1",
}


def create_client(timeout: float = 30.0) -> httpx.AsyncClient:
    return httpx.AsyncClient(headers=DEFAULT_HEADERS, timeout=timeout, follow_redirects=True)
