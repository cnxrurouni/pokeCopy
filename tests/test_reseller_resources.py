from __future__ import annotations

import textwrap

from pokebot.enums import Retailer
from pokebot.reseller.models import Account, ProxyEndpoint
from pokebot.reseller.resources import AccountStore, ProxyManager


def test_proxy_curl_url_embeds_credentials():
    proxy = ProxyEndpoint(
        label="p1", server="http://host:7000", username="u", password="p"
    )
    assert proxy.as_curl_proxy() == "http://u:p@host:7000"


def test_proxy_manager_and_accounts_from_yaml(tmp_path):
    path = tmp_path / "accts.yaml"
    path.write_text(
        textwrap.dedent(
            """
            proxies:
              - label: resi-01
                server: "http://host:7000"
                username: u
                password: p
            accounts:
              - id: acct_1
                retailer: target
                email: a@example.com
                proxy_label: resi-01
            """
        )
    )
    proxies = ProxyManager.from_yaml(path)
    accounts = AccountStore.from_yaml(path)

    account = accounts.all(Retailer.TARGET)[0]
    pinned = proxies.for_account(account)
    assert pinned is not None
    assert pinned.label == "resi-01"


def test_account_acquire_release_prevents_double_use():
    store = AccountStore([Account(id="a1", retailer=Retailer.TARGET, email="x@example.com")])
    first = store.acquire(Retailer.TARGET)
    assert first is not None
    assert store.acquire(Retailer.TARGET) is None
    store.release(first)
    assert store.acquire(Retailer.TARGET) is not None
