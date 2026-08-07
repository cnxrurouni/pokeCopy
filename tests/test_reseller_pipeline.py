from __future__ import annotations

from pokebot.enums import Retailer
from pokebot.reseller.pipeline import TargetPipeline, token_from_sidecar
from pokebot.reseller.settings import ResellerSettings
from pokebot.restockr.models import RestockAlert


def test_task_from_alert_resolves_tcin_from_url():
    pipeline = TargetPipeline.build(ResellerSettings())
    alert = RestockAlert(
        id="x",
        sku="TEST-SKU",
        store="target",
        url="https://www.target.com/p/-/A-1001560450",
        restock_url="https://short.example/abc",
        stock_quantity=4,
    )
    task = pipeline.task_from_alert(alert)
    assert task is not None
    assert task.sku == "1001560450"
    assert "1001560450" in task.product_url
    assert task.navigate_url == "https://short.example/abc"
    # No config cap → RestockR stock_quantity flows through.
    assert task.max_quantity == 4


def test_ensure_default_account_falls_back_to_session():
    pipeline = TargetPipeline.build(ResellerSettings())
    injected = pipeline.ensure_default_account()
    assert injected is True
    accounts = pipeline.accounts.all(Retailer.TARGET)
    assert [a.id for a in accounts] == ["session-target"]
    assert pipeline.ensure_default_account() is False
    assert len(pipeline.accounts.all(Retailer.TARGET)) == 1


def test_token_from_sidecar_requires_px(tmp_path, monkeypatch):
    from pokebot import session_auth

    monkeypatch.setattr(session_auth, "data_dir", lambda: tmp_path)
    assert token_from_sidecar("a1", ttl_seconds=60) is None
    session_auth.save_session_auth(
        "target",
        {
            "accessToken": "x",
            "idToken": "y",
            "login-session": "z",
            "_px3": "px" * 20,
        },
    )
    # Guest JWT would still load into token; checkout validates sut separately.
    token = token_from_sidecar("a1", ttl_seconds=60)
    assert token is not None
    assert token.value.startswith("px")
    assert token.cookies["_px3"]
