# PokeBot

Listen to [RestockR](https://www.restockr.app) for Target restocks and checkout via
**HTTP capture-replay** (`curl_cffi` TLS impersonation). Auth + PerimeterX cookies
come from a **real Chrome** login export — not Playwright.

## Requirements

- Python 3.11+
- Google Chrome (for `login target`)
- RestockR account credentials

## Setup

```bash
python3 -m venv .venv
source .venv/bin/activate   # Windows: .venv\Scripts\Activate.ps1
pip install -e ".[dev]"
export RESTOCKR_USERNAME=... RESTOCKR_PASSWORD=...
```

See [`.env.example`](.env.example). Persist exports in your shell profile as needed.

## Usage

```bash
python -m pokebot login restockr
python -m pokebot login target          # real Chrome; exports auth+_px3
python -m pokebot doctor                # arch + RestockR + fingerprint + sidecar
python -m pokebot reseller target --url "https://www.target.com/p/..." --preflight
python -m pokebot reseller run          # LIVE RestockR → HTTP checkout
python -m pokebot open-alerts           # listen + open URLs in everyday Chrome only
python -m pokebot status
```

Preflight runs live ATC → checkout → pre_checkout and **stops before place_order**.
Live place_order may need `export TARGET_CVV=…` when Target requires CVV.

## Configuration

| File | Role |
|------|------|
| [`config/settings.yaml`](config/settings.yaml) | RestockR + watchlist/retailer filters |
| [`config/reseller.yaml`](config/reseller.yaml) | `curl_impersonate`, retry/abort thresholds |
| [`config/reseller.capture.target.json`](config/reseller.capture.target.json) | Captured Target API chain |

HTTP fingerprint: UA + Client Hints are pinned to match `curl_impersonate` (auto-picks
newest Chrome this `curl_cffi` supports) via `fingerprint_contract.py`. Telemetry:
`data/logs/reseller-http/`.

## How it works

```
RestockR (curl_cffi) → alert → TargetHttpCheckout (curl_cffi)
Real Chrome login → data/sessions/target-auth.json ─┘
```

See [AGENTS.md](AGENTS.md) and [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md).

## Notes

- Automated purchasing may violate retailer Terms of Service.
- Soft/"Keep me signed in" Target sessions (`asl=L`) fail pre_checkout — doctor
  requires cart `guest_type=REGISTERED`.
- `_px3` expires; re-run `login target` on sustained `AUTH_DENIED`.
