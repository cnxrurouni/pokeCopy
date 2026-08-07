from __future__ import annotations

import json

from pokebot.enums import Retailer
from pokebot.reseller.capture import CaptureFile, CapturedRequest
from pokebot.reseller.checkout.base import CheckoutContext
from pokebot.reseller.checkout.target_http import TargetHttpCheckout
from pokebot.reseller.models import Account, CheckoutTask, HarvestedToken, TokenKind


class _FakeResp:
    def __init__(self, status_code: int, text: str, headers: dict | None = None) -> None:
        self.status_code = status_code
        self.text = text
        self.headers = headers or {}

    def json(self):
        import json

        return json.loads(self.text)


class _FakeSession:
    """Records requests and returns queued responses — no network."""

    def __init__(self, responses: list[tuple[int, str]]) -> None:
        self._responses = list(responses)
        self.calls: list[tuple[str, str, dict | None]] = []

    def request(self, method: str, url: str, headers=None, data=None) -> _FakeResp:
        self.calls.append((method, url, headers))
        if not self._responses:
            return _FakeResp(503, "empty queue")
        status, text = self._responses.pop(0)
        return _FakeResp(status, text)


_CART_OK = '{"cart_items":[{"tcin":"1001560450","quantity":1}]}'
_CART_EMPTY = '{"cart_items":[]}'


def _checkout(**kwargs) -> TargetHttpCheckout:
    defaults = dict(
        atc_spam_timeout_seconds=2.0,
        checkout_spam_timeout_seconds=2.0,
        atc_retry_delay_ms_min=0,
        atc_retry_delay_ms_max=0,
        spam_delay_ms_min=0,
        spam_delay_ms_max=0,
        warm_cart_checkout=False,
    )
    defaults.update(kwargs)
    return TargetHttpCheckout(**defaults)


def _capture() -> CaptureFile:
    return CaptureFile(
        retailer="target",
        sequence=["add_to_cart", "checkout", "pre_checkout", "place_order"],
        requests=[
            CapturedRequest(
                name="add_to_cart",
                method="POST",
                url="https://x/atc",
                headers={"referer": "{{product_url}}"},
                body='{"tcin":"{{tcin}}","quantity":{{quantity}}}',
                extract={"cart_id": "cart_id"},
                expect_status=201,
            ),
            CapturedRequest(
                name="checkout",
                method="PUT",
                url="https://x/co/{{cart_id}}",
                headers={"referer": "https://www.target.com/cart"},
            ),
            CapturedRequest(
                name="pre_checkout",
                method="POST",
                url="https://x/pre",
                expect_status=201,
            ),
            CapturedRequest(
                name="place_order",
                method="POST",
                url="https://x/po/{{cart_id}}",
                extract={"order_id": "orders.0.order_id", "reference_id": "orders.0.reference_id"},
                success_contains="reference_id",
                commits_order=True,
            ),
        ],
    )


async def test_preflight_stops_before_commit_request():
    checkout = _checkout(preflight=True)
    session = _FakeSession(
        [
            (201, '{"cart_id":"C1","cart_items":[{"tcin":"1001560450"}]}'),
            (200, _CART_OK),
            (200, "{}"),
            (201, "{}"),
            (200, _CART_OK),
        ]
    )
    outcome = await checkout._run_chain(
        session,
        _capture(),
        {
            "tcin": "1001560450",
            "quantity": 1,
            "product_url": "https://www.target.com/p/-/A-1001560450",
        },
        cookies={"accessToken": "x", "_px3": "y" * 30},
    )

    assert outcome.success is True
    assert "PREFLIGHT OK" in (outcome.message or "")
    assert any(c[1] == "https://x/atc" for c in session.calls)
    assert not any("/po/" in c[1] for c in session.calls)


async def test_atc_spam_retries_until_success():
    checkout = _checkout(preflight=True)
    session = _FakeSession(
        [
            (403, "blocked"),
            (503, "busy"),
            (201, '{"cart_id":"C1"}'),
            (200, _CART_OK),
            (200, "{}"),
            (403, "guest"),
            (201, "{}"),
            (200, _CART_OK),
        ]
    )
    outcome = await checkout._run_chain(
        session,
        _capture(),
        {"tcin": "1001560450", "quantity": 2},
        cookies={"accessToken": "x", "login-session": "s", "_px3": "y" * 30},
    )
    assert outcome.success is True
    atc_calls = [c for c in session.calls if c[1] == "https://x/atc"]
    # Failed ATC attempts + one success — must not keep ATCing after 201.
    assert len(atc_calls) == 3


async def test_place_order_is_single_shot_not_spammed():
    checkout = _checkout(preflight=False)
    session = _FakeSession(
        [
            (201, '{"cart_id":"C1","cart_items":[{"tcin":"123"}]}'),
            (200, '{"cart_items":[{"tcin":"123"}]}'),
            (200, "{}"),
            (201, "{}"),
            (200, '{"cart_id":"C1","payment_instructions":[]}'),
            (500, '{"error":"busy"}'),
            (200, '{"order":{"order_id":"O1"}}'),  # must NOT be consumed
        ]
    )
    outcome = await checkout._run_chain(
        session, _capture(), {"tcin": "123", "quantity": 1, "cart_id": "C1"}
    )
    assert outcome.success is False
    assert outcome.retryable is False
    assert "not re-POSTing" in (outcome.message or "") or "failed" in (
        outcome.message or ""
    ).lower()
    po_calls = [c for c in session.calls if "/po/" in c[1]]
    assert len(po_calls) == 1


async def test_preflight_fails_when_cart_never_gets_tcin():
    checkout = _checkout(preflight=True, atc_spam_timeout_seconds=0.15)
    session = _FakeSession(
        [
            (201, '{"cart_id":"C1"}'),
            (200, _CART_EMPTY),
            (200, _CART_EMPTY),
            (200, _CART_EMPTY),
            (200, _CART_EMPTY),
            (200, _CART_EMPTY),
            (200, _CART_EMPTY),
            (200, _CART_EMPTY),
        ]
    )
    outcome = await checkout._run_chain(
        session,
        _capture(),
        {"tcin": "1001560450", "quantity": 1},
        cookies={"accessToken": "x", "_px3": "y" * 30},
    )
    assert outcome.success is False
    assert outcome.retryable is False
    assert "not re-ATCing" in (outcome.message or "") or "cart never showed" in (
        outcome.message or ""
    )
    atc_calls = [c for c in session.calls if c[1] == "https://x/atc"]
    assert len(atc_calls) == 1


async def test_full_chain_places_order_and_extracts_id():
    checkout = _checkout(preflight=False)
    session = _FakeSession(
        [
            (201, '{"cart_id":"C1","cart_items":[{"tcin":"123"}]}'),
            (200, '{"cart_items":[{"tcin":"123"}]}'),
            (200, "{}"),
            (201, "{}"),
            (200, '{"cart_id":"C1","payment_instructions":[]}'),
            (
                200,
                '{"orders":[{"order_id":"CART-UUID","reference_id":"102003652652463",'
                '"cart_state":"COMPLETED"}]}',
            ),
        ]
    )
    outcome = await checkout._run_chain(
        session, _capture(), {"tcin": "123", "quantity": 1, "cart_id": "C1"}
    )

    assert outcome.success is True
    assert outcome.order_id == "102003652652463"


async def test_live_requires_target_cvv_when_card_needs_it(monkeypatch):
    monkeypatch.delenv("TARGET_CVV", raising=False)
    monkeypatch.delenv("POKEBOT_TARGET_CVV", raising=False)
    checkout = _checkout(preflight=False)
    session = _FakeSession(
        [
            (201, '{"cart_id":"C1","cart_items":[{"tcin":"123"}]}'),
            (200, '{"cart_items":[{"tcin":"123"}]}'),
            (200, "{}"),
            (201, "{}"),
            (
                200,
                json.dumps(
                    {
                        "cart_id": "C1",
                        "payment_instructions": [
                            {
                                "payment_instruction_id": "pi-1",
                                "is_cvv_required": True,
                                "card_type": "AMERICANEXPRESS",
                                "card_number": "-------1004",
                            }
                        ],
                    }
                ),
            ),
        ]
    )
    outcome = await checkout._run_chain(
        session, _capture(), {"tcin": "123", "quantity": 1, "cart_id": "C1"}
    )
    assert outcome.success is False
    assert "TARGET_CVV" in (outcome.message or "")
    assert not any("/po/" in c[1] for c in session.calls)


async def test_live_attaches_cvv_before_place_order(monkeypatch):
    monkeypatch.setenv("TARGET_CVV", "1234")
    checkout = _checkout(preflight=False)
    session = _FakeSession(
        [
            (201, '{"cart_id":"C1","cart_items":[{"tcin":"123"}]}'),
            (200, '{"cart_items":[{"tcin":"123"}]}'),
            (200, "{}"),
            (201, "{}"),
            (
                200,
                json.dumps(
                    {
                        "cart_id": "C1",
                        "payment_instructions": [
                            {
                                "payment_instruction_id": "pi-1",
                                "is_cvv_required": True,
                                "card_type": "AMERICANEXPRESS",
                                "card_number": "-------1004",
                            }
                        ],
                    }
                ),
            ),
            (200, "{}"),  # PUT set_cvv
            (200, '{"orders":[{"order_id":"CART-UUID","reference_id":"999888777","cart_state":"COMPLETED"}]}'),
        ]
    )
    outcome = await checkout._run_chain(
        session, _capture(), {"tcin": "123", "quantity": 1, "cart_id": "C1"}
    )
    assert outcome.success is True
    assert outcome.order_id == "999888777"
    assert any("payment_instructions/pi-1" in c[1] and c[0] == "PUT" for c in session.calls)


def test_missing_cvv_response_is_fatal():
    checkout = _checkout(preflight=False)
    req = CapturedRequest(name="place_order", method="POST", url="https://x/po", commits_order=True)
    assert checkout._is_fatal_client_error(
        req,
        400,
        '{"code":"MISSING_CREDIT_CARD_CVV","message":"Missing CVV or PIN"}',
    )


async def test_fatal_400_does_not_spam_forever():
    checkout = _checkout(preflight=False, atc_spam_timeout_seconds=5.0)
    session = _FakeSession([(400, '{"errors":[{"message":"invalid tcin"}]}')])
    outcome = await checkout._run_chain(
        session, _capture(), {"tcin": "123", "quantity": 1}
    )
    assert outcome.success is False
    assert outcome.retryable is False
    assert len(session.calls) == 1


def test_validate_ready_rejects_non_numeric_tcin():
    checkout = _checkout(preflight=True)
    task = CheckoutTask(
        retailer=Retailer.TARGET,
        sku="TEST-SKU",
        product_url="https://www.target.com/",
    )
    account = Account(retailer=Retailer.TARGET, email="a@b.com")
    token = HarvestedToken(
        retailer=Retailer.TARGET,
        kind=TokenKind.PX3,
        value="x" * 40,
        cookies={"_px3": "x" * 40, "accessToken": "tok", "idToken": "id", "login-session": "s"},
        ttl_seconds=60,
        account_id=account.id,
    )
    ctx = CheckoutContext(task=task, account=account, proxy=None, token=token)
    variables = checkout._initial_variables(ctx, _capture())
    err = checkout._validate_ready(ctx, _capture(), variables)
    assert err is not None
    assert "invalid TCIN" in err


def test_validate_ready_rejects_soft_remembered_jwt(monkeypatch):
    import base64

    def make_jwt(**claims) -> str:
        header = base64.urlsafe_b64encode(b'{"alg":"none"}').decode().rstrip("=")
        payload = (
            base64.urlsafe_b64encode(json.dumps(claims).encode()).decode().rstrip("=")
        )
        return f"{header}.{payload}.x"

    monkeypatch.setattr(
        "pokebot.doctor.probe_target_cart_guest_type",
        lambda _c: None,
    )
    soft = make_jwt(sut="R", asl="L", sco="ecom.low,openid")
    checkout = _checkout(preflight=True)
    task = CheckoutTask(
        retailer=Retailer.TARGET,
        sku="1001560450",
        product_url="https://www.target.com/p/-/A-1001560450",
    )
    account = Account(retailer=Retailer.TARGET, email="a@b.com")
    token = HarvestedToken(
        retailer=Retailer.TARGET,
        kind=TokenKind.PX3,
        value="x" * 40,
        cookies={
            "_px3": "x" * 40,
            "accessToken": soft,
            "idToken": "id",
        },
        ttl_seconds=60,
        account_id=account.id,
    )
    ctx = CheckoutContext(task=task, account=account, proxy=None, token=token)
    variables = checkout._initial_variables(ctx, _capture())
    err = checkout._validate_ready(ctx, _capture(), variables)
    assert err is not None
    assert "soft" in err.lower() or "REMEMBERED" in err
    assert "login target" in err


def test_initial_variables_prefer_url_tcin_over_test_sku():
    checkout = _checkout()
    task = CheckoutTask(
        retailer=Retailer.TARGET,
        sku="TEST-SKU",
        product_url="https://www.target.com/p/-/A-1001560450",
        max_quantity=5,
    )
    account = Account(retailer=Retailer.TARGET, email="a@b.com")
    ctx = CheckoutContext(task=task, account=account, proxy=None, token=None)
    variables = checkout._initial_variables(ctx, _capture())
    assert variables["tcin"] == "1001560450"
    assert variables["quantity"] == 5


def test_request_headers_include_client_identity():
    checkout = _checkout(impersonate="chrome146")
    capture = _capture()
    req = capture.requests[0]
    headers = checkout._request_headers(
        req,
        {"tcin": "1001560450", "quantity": 1, "product_url": "https://www.target.com/p/-/A-1"},
        {"accessToken": "tok", "idToken": "id", "_px3": "px", "_px2": "px2"},
    )
    assert "Chrome/146" in headers["user-agent"]
    assert headers["sec-ch-ua"]
    assert headers["sec-ch-ua-mobile"] == "?0"
    assert headers["sec-ch-ua-platform"]
    assert "_px2=px2" in headers["cookie"]


def test_essential_cookies_include_px2():
    checkout = _checkout()
    sent = checkout._cookies_for_request(
        {"accessToken": "a", "idToken": "i", "_px3": "3", "_px2": "2", "noise": "x"}
    )
    assert "_px2" in sent
    assert "noise" not in sent


def test_parse_retry_after_seconds():
    assert TargetHttpCheckout._parse_retry_after_seconds({"Retry-After": "45"}) == 45.0
    assert TargetHttpCheckout._parse_retry_after_seconds({"retry-after": "30"}) == 30.0
    assert TargetHttpCheckout._parse_retry_after_seconds({"Retry-After": "Wed, 01 Jan"}) is None
    assert TargetHttpCheckout._parse_retry_after_seconds({}) is None


def test_rate_limit_cooldown_uses_default_and_retry_after(monkeypatch):
    checkout = _checkout(rate_limit_cooldown_seconds=30.0)
    slept: list[float] = []
    monkeypatch.setattr(
        "pokebot.reseller.checkout.target_http.time.sleep",
        lambda s: slept.append(s),
    )
    assert checkout._rate_limit_cooldown(label="atc") == 30.0
    assert slept == [30.0]
    slept.clear()
    assert (
        checkout._rate_limit_cooldown(
            response_headers={"Retry-After": "12"}, label="atc"
        )
        == 12.0
    )
    assert slept == [12.0]


def test_atc_stock_failure_detects_max_purchase_and_oos():
    req = CapturedRequest(name="add_to_cart", method="POST", url="https://x/atc")
    body = (
        '{"message":"Items cannot be added to cart as max purchase limit exceeded",'
        '"code":"MAX_PURCHASE_LIMIT_EXCEEDED"}'
    )
    parsed = json.loads(body)
    reason = TargetHttpCheckout._atc_stock_failure(req, 400, body, parsed)
    assert reason == "MAX_PURCHASE_LIMIT_EXCEEDED"

    oos = '{"message":"Item is out of stock","code":"INVENTORY_UNAVAILABLE"}'
    assert (
        TargetHttpCheckout._atc_stock_failure(req, 400, oos, json.loads(oos))
        == "INVENTORY_UNAVAILABLE"
    )

    checkout_req = CapturedRequest(name="checkout", method="PUT", url="https://x/co")
    assert (
        TargetHttpCheckout._atc_stock_failure(checkout_req, 400, body, parsed) is None
    )


async def test_atc_stops_on_max_purchase_limit():
    checkout = _checkout(atc_spam_timeout_seconds=30.0)
    body = (
        '{"message":"Items cannot be added to cart as max purchase limit exceeded",'
        '"code":"MAX_PURCHASE_LIMIT_EXCEEDED"}'
    )
    session = _FakeSession([(400, body), (201, '{"cart_id":"c1"}')])
    capture = _capture()
    variables = {
        "tcin": "1001560450",
        "quantity": 1,
        "product_url": "https://www.target.com/p/-/A-1001560450",
    }
    cookies = {"accessToken": "a", "idToken": "i", "_px3": "p" * 40}
    outcome = checkout._spam_until_ok(
        session,
        capture.requests[0],
        variables,
        cookies,
        timeout_s=30.0,
        require_cart_tcin="1001560450",
        label="add_to_cart",
    )
    assert outcome is not None
    assert outcome.success is False
    assert outcome.retryable is False
    assert "MAX_PURCHASE_LIMIT_EXCEEDED" in (outcome.message or "")
    assert len(session.calls) == 1



def test_resolve_placed_order_id_prefers_reference():
    assert (
        TargetHttpCheckout._resolve_placed_order_id(
            {"order_id": "CART-UUID", "reference_id": "102003652652463"}
        )
        == "102003652652463"
    )
    assert TargetHttpCheckout._resolve_placed_order_id({"order_id": "CART-UUID"}) == "CART-UUID"
    assert TargetHttpCheckout._resolve_placed_order_id({}) is None


def test_apply_extract_place_order_fallback_orders_array():
    checkout = _checkout()
    req = CapturedRequest(
        name="place_order",
        method="POST",
        url="https://x/po",
        extract={},  # live capture had empty extract — fallback must fill ids
        commits_order=True,
    )
    variables: dict = {}
    parsed = {
        "orders": [
            {
                "order_id": "6be8c5d1-9159-11f1-b3f1-e7e8dc713f8f",
                "reference_id": "102003652652463",
                "cart_state": "COMPLETED",
            }
        ]
    }
    checkout._apply_extract(req, parsed, variables)
    assert variables["reference_id"] == "102003652652463"
    assert variables["order_id"] == "6be8c5d1-9159-11f1-b3f1-e7e8dc713f8f"
    assert TargetHttpCheckout._resolve_placed_order_id(variables) == "102003652652463"
