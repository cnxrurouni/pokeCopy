from __future__ import annotations

# Preferred curl_cffi impersonation targets, newest first. Both Chrome and
# (modern) Edge are Chromium, so their TLS/JA3/HTTP2 fingerprints match the
# equivalent Chrome build — we impersonate the latest available Chrome target for
# either channel. curl_cffi only ships ancient edge99/edge101 whose old TLS is a
# WORSE match for current Edge than a recent Chrome target. Pair the chosen
# target with ClientIdentity UA / sec-ch-ua* from fingerprint_contract.py.
_PREFERRED_CHROMIUM = (
    "chrome146",
    "chrome145",
    "chrome142",
    "chrome136",
    "chrome133a",
    "chrome131",
    "chrome124",
    "chrome120",
    "chrome116",
)

# Non-Chromium channels map to their own engine family (kept for completeness;
# the pipeline currently only uses Chromium channels for Target).
_ENGINE_FAMILY = {
    "firefox": ("firefox147", "firefox144", "firefox135", "firefox133"),
    "webkit": ("safari184", "safari180", "safari170"),
}


def _available_targets() -> set[str]:
    try:
        from curl_cffi.requests import BrowserType

        return {t.value for t in BrowserType}
    except Exception:
        return set()


def _first_available(candidates: tuple[str, ...], available: set[str]) -> str | None:
    for c in candidates:
        if not available or c in available:
            return c
    return None


def curl_impersonate_for_channel(channel: str | None, *, override: str | None = None) -> str:
    """Resolve the curl_cffi impersonation target.

    Prefer ``reseller.curl_impersonate`` (``override``). ``channel`` is legacy and
    ignored except as a last-resort family hint when override is unset.
    """
    available = _available_targets()
    if override:
        return override

    ch = (channel or "").lower()
    if ch in ("firefox",):
        target = _first_available(_ENGINE_FAMILY["firefox"], available)
    elif ch in ("webkit", "safari"):
        target = _first_available(_ENGINE_FAMILY["webkit"], available)
    else:
        target = _first_available(_PREFERRED_CHROMIUM, available)

    return target or "chrome"


def available_curl_impersonate_targets() -> set[str]:
    """Public wrapper for doctor / diagnostics."""
    return _available_targets()


def check_curl_impersonate_ready(target: str = "chrome146") -> tuple[bool, str]:
    """Verify curl_cffi can impersonate ``target`` (or report empty catalog)."""
    available = _available_targets()
    if not available:
        return (
            True,
            f"curl_cffi BrowserType catalog empty/unavailable — assuming {target} ok",
        )
    if target in available:
        return True, f"curl_cffi impersonate={target} available"
    sample = ", ".join(sorted(available)[:8])
    return (
        False,
        f"curl_cffi missing impersonate={target!r} (have: {sample}…); "
        "upgrade curl_cffi or set reseller.curl_impersonate to an available target",
    )
