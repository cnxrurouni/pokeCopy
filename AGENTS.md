# AGENTS.md

Context for AI agents working in this repository.

## What this project is

PokeBot listens to [RestockR](https://www.restockr.app) for Target restocks and buys via **HTTP capture-replay** (`curl_cffi` TLS impersonation). Cookies (registered auth + PerimeterX `_px3`) come from a **real Chrome** login export — not Playwright.

There is **no Playwright** in this codebase. Browser click-checkout was removed because automation browsers are easy for PerimeterX/HUMAN to flag.

## Evidence before claims

Do not claim login/checkout state without a concrete check (`doctor`, logs under `data/logs/reseller-http/`, or a successful preflight). Profile dirs alone do not prove registered Target auth — use `data/sessions/target-auth.json` with **hard** registered auth: JWT `sut=R` **and not** soft/`asl=L`/`ecom.low`, PerimeterX `_px3`, and cart `guest_type=REGISTERED` (doctor probes the cart). Soft/"Keep me signed in" sessions still have `sut=R` but cart is `REMEMBERED` and `pre_checkout` returns `403 INVALID_GUEST_STATUS`. `login-session` is optional when Target omits it.

## Layout

- `src/pokebot/__main__.py` — CLI
- `src/pokebot/cli/restockr_cli.py` — `login`, `open-alerts`, `status`, `doctor`
- `src/pokebot/cli/reseller_cli.py` — `reseller run` / `reseller target`
- `src/pokebot/restockr/` — RestockR auth (curl_cffi), Socket.IO listener
- `src/pokebot/chrome_login.py` — real Chrome + CDP cookie export → sidecar
- `src/pokebot/session_auth.py` — `target-auth.json` (auth + PX)
- `src/pokebot/reseller/checkout/target_http.py` — ATC/checkout over curl_cffi
- `src/pokebot/reseller/fingerprint_contract.py` — UA / sec-ch-ua* pinned to curl_impersonate
- `src/pokebot/reseller/http_telemetry.py` — NDJSON request/TLS dumps
- `src/pokebot/reseller/pipeline.py` / `orchestrator.py` — RestockR → HTTP buy
- `src/pokebot/alert_open.py` — optional: open product URL in everyday Chrome

## How to run

```bash
pip install -e ".[dev]"
export RESTOCKR_USERNAME=... RESTOCKR_PASSWORD=...
python -m pokebot login restockr
python -m pokebot login target          # real Chrome; exports auth+_px3
python -m pokebot doctor
python -m pokebot reseller target --url "https://www.target.com/p/..." --preflight
python -m pokebot reseller run          # LIVE if config/reseller.yaml dry_run: false
python -m pokebot open-alerts           # listen + open URLs in normal Chrome only
```

Telemetry: `data/logs/reseller-http/*.ndjson` (headers redacted, impersonate target, IPs, elapsed, body snips).

Doctor checks curl_cffi impersonate availability and ClientIdentity (UA + Client Hints)
alignment with `reseller.curl_impersonate`, then hard-registered sidecar auth.

## Config

- `config/settings.yaml` — RestockR + watchlist/retailer filters
- `config/reseller.yaml` — `dry_run`, `curl_impersonate`, ATC abort thresholds
- `config/reseller.capture.target.json` — captured Target API chain

## Gotchas

- Reseller checkout places **real orders** when `dry_run: false` / `--live`.
- Cookies from `login target` are cached in `data/sessions/target-auth.json` and reused for HTTP. Optional Chrome warm (`warm_cart_checkout`) is off by default — enabling it can briefly make Go-Proxy reject the same token (MI6 issuer 401).
- `_px3` expires; re-run `login target` when ATC returns sustained 401 AUTH_DENIED.
- Live `place_order` needs CVV when Target sets `is_cvv_required` on the saved card: `export TARGET_CVV=…` (Amex is 4 digits). Never commit CVV to config.
- Automated purchasing may violate retailer Terms of Service.
