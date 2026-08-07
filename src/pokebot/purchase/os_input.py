from __future__ import annotations

"""OS-level mouse input for real browser windows (no Playwright/CDP).

Walmart join-queue clicks go through the OS so the page never sees CDP Input
or ``navigator.webdriver``. Requires Windows for auto-click; other platforms
fall back to a human prompt.
"""

import random
import re
import sys
import time
from dataclasses import dataclass

from rich.console import Console

console = Console()

# Button names Walmart exposes (UIA Name / accessible name).
JOIN_BUTTON_NAME_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(r"^\s*join\s+queue\s*$", re.I),
    re.compile(r"^\s*get\s+in\s+line\s*$", re.I),
    re.compile(r"^\s*join\s+the\s+queue\s*$", re.I),
    re.compile(r"join\s+queue", re.I),
    re.compile(r"get\s+in\s+line", re.I),
)


@dataclass
class ClickResult:
    ok: bool
    method: str
    detail: str = ""


def name_matches_join_button(name: str | None) -> bool:
    if not name:
        return False
    text = name.strip()
    return any(p.search(text) for p in JOIN_BUTTON_NAME_PATTERNS)


def window_title_looks_like_walmart(title: str | None) -> bool:
    if not title:
        return False
    low = title.lower()
    return "walmart" in low


def window_title_looks_like_target(title: str | None) -> bool:
    if not title:
        return False
    low = title.lower()
    return "target" in low


def warm_browser_window(
    *,
    title_predicate,
    duration_s: float = 4.0,
    label: str = "browser",
) -> bool:
    """Focus a matching window and move/scroll the OS mouse (no CDP).

    Returns True if a window was found and warmed; False if none matched
    (caller can still wait — PX may fire without our mouse input).
    """
    if sys.platform != "win32" or duration_s <= 0:
        time.sleep(max(0.0, duration_s))
        return False
    try:
        from pywinauto import Desktop
    except ImportError:
        time.sleep(duration_s)
        return False

    import ctypes

    user32 = ctypes.windll.user32
    deadline = time.monotonic() + duration_s
    warmed = False
    while time.monotonic() < deadline:
        try:
            wins = [
                w
                for w in Desktop(backend="uia").windows()
                if title_predicate(w.window_text())
            ]
        except Exception:
            wins = []
        if not wins:
            time.sleep(0.35)
            continue
        win = wins[0]
        try:
            win.set_focus()
        except Exception:
            pass
        try:
            rect = win.rectangle()
            cx = int((rect.left + rect.right) / 2 + random.uniform(-80, 80))
            cy = int((rect.top + rect.bottom) / 2 + random.uniform(-60, 60))
            # Reuse the humanized path without clicking — move only.
            class POINT(ctypes.Structure):
                _fields_ = [("x", ctypes.c_long), ("y", ctypes.c_long)]

            pt = POINT()
            user32.GetCursorPos(ctypes.byref(pt))
            sx, sy = int(pt.x), int(pt.y)
            steps = random.randint(10, 18)
            cx_ctrl = (sx + cx) / 2 + random.uniform(-40, 40)
            cy_ctrl = (sy + cy) / 2 + random.uniform(-30, 30)
            for i in range(1, steps + 1):
                t = i / steps
                bx = (1 - t) ** 2 * sx + 2 * (1 - t) * t * cx_ctrl + t**2 * cx
                by = (1 - t) ** 2 * sy + 2 * (1 - t) * t * cy_ctrl + t**2 * cy
                user32.SetCursorPos(int(bx), int(by))
                time.sleep(random.uniform(0.01, 0.03))
            # Wheel scroll nudges (WHEEL_DELTA = 120)
            delta = int(120 * random.choice((-2, -1, 1, 2)))
            user32.mouse_event(0x0800, 0, 0, delta, 0)
            warmed = True
        except Exception:
            pass
        time.sleep(random.uniform(0.25, 0.55))
    if warmed:
        console.print(f"[dim]OS-warmed {label} window (no CDP)[/dim]")
    return warmed


def _humanized_move_and_click(x: int, y: int) -> None:
    """Move cursor along a short curved path, then left-click (Windows)."""
    import ctypes
    from ctypes import wintypes

    user32 = ctypes.windll.user32

    class POINT(ctypes.Structure):
        _fields_ = [("x", wintypes.LONG), ("y", wintypes.LONG)]

    pt = POINT()
    user32.GetCursorPos(ctypes.byref(pt))
    sx, sy = int(pt.x), int(pt.y)

    steps = random.randint(12, 22)
    # Control point for a mild Bézier curve.
    cx = (sx + x) / 2 + random.uniform(-40, 40)
    cy = (sy + y) / 2 + random.uniform(-30, 30)
    for i in range(1, steps + 1):
        t = i / steps
        # Quadratic Bézier
        bx = (1 - t) ** 2 * sx + 2 * (1 - t) * t * cx + t**2 * x
        by = (1 - t) ** 2 * sy + 2 * (1 - t) * t * cy + t**2 * y
        user32.SetCursorPos(int(bx), int(by))
        time.sleep(random.uniform(0.008, 0.025))

    user32.SetCursorPos(int(x), int(y))
    time.sleep(random.uniform(0.05, 0.15))
    # MOUSEEVENTF_LEFTDOWN = 0x0002, LEFTUP = 0x0004
    user32.mouse_event(0x0002, 0, 0, 0, 0)
    time.sleep(random.uniform(0.04, 0.09))
    user32.mouse_event(0x0004, 0, 0, 0, 0)


def _beep() -> None:
    if sys.platform == "win32":
        try:
            import winsound

            winsound.MessageBeep(winsound.MB_ICONEXCLAMATION)
            return
        except Exception:
            pass
    print("\a", end="", flush=True)


def prompt_join_fallback(*, reason: str = "") -> ClickResult:
    """Bring attention to the browser; human must click Join."""
    _beep()
    msg = (
        "[bold yellow]Click Join queue / Get in line[/bold yellow] in the Edge window now."
    )
    if reason:
        msg += f"\n[dim]({reason})[/dim]"
    console.print(msg)
    return ClickResult(ok=False, method="human_prompt", detail=reason or "prompted")


def _click_via_pywinauto(timeout_s: float) -> ClickResult:
    try:
        from pywinauto import Desktop
    except ImportError:
        return ClickResult(
            ok=False,
            method="pywinauto",
            detail="pywinauto not installed (pip install pywinauto)",
        )

    deadline = time.monotonic() + timeout_s
    last_err = ""
    while time.monotonic() < deadline:
        try:
            desktop = Desktop(backend="uia")
            windows = desktop.windows()
            walmart_wins = [
                w
                for w in windows
                if window_title_looks_like_walmart(w.window_text())
            ]
            if not walmart_wins:
                time.sleep(0.4)
                continue

            for win in walmart_wins:
                try:
                    win.set_focus()
                except Exception:
                    pass
                # Descend for buttons / hyperlinks with join text.
                try:
                    descendants = win.descendants()
                except Exception as exc:
                    last_err = str(exc)
                    continue
                for ctrl in descendants:
                    try:
                        name = ctrl.window_text() or ctrl.element_info.name
                    except Exception:
                        continue
                    if not name_matches_join_button(name):
                        continue
                    try:
                        rect = ctrl.rectangle()
                        cx = int((rect.left + rect.right) / 2)
                        cy = int((rect.top + rect.bottom) / 2)
                    except Exception:
                        # Fall back to control click if geometry fails.
                        try:
                            ctrl.click_input()
                            return ClickResult(
                                ok=True,
                                method="pywinauto_click_input",
                                detail=name,
                            )
                        except Exception as exc:
                            last_err = str(exc)
                            continue
                    _humanized_move_and_click(cx, cy)
                    return ClickResult(
                        ok=True,
                        method="pywinauto_uia_coords",
                        detail=f"{name!r} @ ({cx},{cy})",
                    )
        except Exception as exc:
            last_err = str(exc)
        time.sleep(0.5)

    return ClickResult(
        ok=False,
        method="pywinauto",
        detail=last_err or "Join button not found in UIA tree",
    )


def try_click_join_queue(*, timeout_s: float = 45.0) -> ClickResult:
    """Find Walmart Join queue / Get in line and OS-click it.

    On non-Windows or if UIA fails, prompts the human (never falls back to Playwright).
    """
    if sys.platform != "win32":
        return prompt_join_fallback(reason="OS auto-click is Windows-only")

    console.print(
        f"[dim]Looking for Join queue / Get in line via UI Automation "
        f"(up to {timeout_s:.0f}s)…[/dim]"
    )
    result = _click_via_pywinauto(timeout_s)
    if result.ok:
        console.print(
            f"[green]OS-clicked join[/green] via {result.method}: {result.detail}"
        )
        return result

    return prompt_join_fallback(reason=result.detail)


def find_walmart_window_titles() -> list[str]:
    """Return titles of top-level windows that look like Walmart (Windows)."""
    if sys.platform != "win32":
        return []
    try:
        from pywinauto import Desktop

        return [
            w.window_text()
            for w in Desktop(backend="uia").windows()
            if window_title_looks_like_walmart(w.window_text())
        ]
    except Exception:
        return []


def title_suggests_queue_ready(title: str) -> bool:
    low = title.lower()
    needles = (
        "your turn",
        "time to shop",
        "ready to shop",
        "add to cart",
        "checkout",
    )
    return any(n in low for n in needles)


def title_suggests_queue_waiting(title: str) -> bool:
    low = title.lower()
    needles = ("queue", "in line", "waiting", "place in line")
    return any(n in low for n in needles)
