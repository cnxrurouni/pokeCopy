#!/usr/bin/env bash
# Try a Target checkout (mobile channel by default).
#
# Usage:
#   ./scripts/try-target-checkout.sh "https://www.target.com/p/-/A-TCIN"
#   ./scripts/try-target-checkout.sh --from-cart
#   ./scripts/try-target-checkout.sh --from-cart --live
#   ./scripts/try-target-checkout.sh "https://www.target.com/p/-/A-TCIN" --live
#   ./scripts/try-target-checkout.sh "https://www.target.com/p/-/A-TCIN" --web
#
# Default is --preflight (no purchase). Pass --live to place_order.
# --from-cart skips ATC and checks out whatever is already in the cart.

set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"

if [[ -f .venv/bin/activate ]]; then
  # shellcheck source=/dev/null
  source .venv/bin/activate
fi

URL=""
CHANNEL="mobile"
PREFLIGHT=1
FROM_CART=0
EXTRA=()

while [[ $# -gt 0 ]]; do
  case "$1" in
    --live)
      PREFLIGHT=0
      shift
      ;;
    --preflight)
      PREFLIGHT=1
      shift
      ;;
    --from-cart)
      FROM_CART=1
      shift
      ;;
    --mobile)
      CHANNEL="mobile"
      shift
      ;;
    --web)
      CHANNEL="web"
      shift
      ;;
    --http-atc)
      EXTRA+=(--http-atc)
      shift
      ;;
    --quantity)
      EXTRA+=(--quantity "$2")
      shift 2
      ;;
    -h|--help)
      sed -n '2,12p' "$0" | sed 's/^# \{0,1\}//'
      exit 0
      ;;
    -*)
      echo "Unknown flag: $1" >&2
      exit 2
      ;;
    *)
      if [[ -z "$URL" ]]; then
        URL="$1"
      else
        echo "Unexpected argument: $1" >&2
        exit 2
      fi
      shift
      ;;
  esac
done

if [[ "$FROM_CART" -eq 0 && -z "$URL" ]]; then
  echo "Usage: $0 <target-product-url|--from-cart> [--mobile|--web] [--preflight|--live]" >&2
  exit 2
fi

ARGS=(reseller target --channel "$CHANNEL")
if [[ "$FROM_CART" -eq 1 ]]; then
  ARGS+=(--from-cart)
  echo "FROM CART ($CHANNEL) — skip ATC"
fi
if [[ -n "$URL" ]]; then
  ARGS+=(--url "$URL")
fi
if [[ "$PREFLIGHT" -eq 1 ]]; then
  ARGS+=(--preflight)
  echo "PREFLIGHT — no purchase"
else
  echo "LIVE — can place a real order"
fi

exec python -m pokebot "${ARGS[@]}" "${EXTRA[@]+"${EXTRA[@]}"}"
