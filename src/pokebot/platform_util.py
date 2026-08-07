from __future__ import annotations

import contextlib
import subprocess
import sys
from pathlib import Path


def is_macos() -> bool:
    return sys.platform == "darwin"


def is_windows() -> bool:
    return sys.platform == "win32"


def quit_bot_browser_hint() -> str:
    """How the bot quits the manual-login browser on this OS."""
    if is_macos():
        return "the bot will quit that Chrome for you when you press Enter"
    if is_windows():
        return "the bot will quit that Edge/Chrome for you when you press Enter"
    return "the bot will quit that browser when you press Enter"


def profile_still_locked_hint() -> str:
    if is_macos():
        return (
            "Quit the bot browser with Cmd+Q, or run:\n"
            "  pkill -f 'user-data-dir=.*/data/sessions/target'"
        )
    if is_windows():
        return (
            "Fully quit the bot Chrome/Edge window that used the PokeBot profile "
            "(Task Manager → end any leftover msedge/chrome still on that profile)."
        )
    return "Fully quit the bot browser using the PokeBot profile directory."


def browser_ua_platform() -> tuple[str, str]:
    """Return (user-agent OS token, sec-ch-ua-platform value) for this host."""
    if is_windows():
        return (
            "Windows NT 10.0; Win64; x64",
            '"Windows"',
        )
    if is_macos():
        return (
            "Macintosh; Intel Mac OS X 10_15_7",
            '"macOS"',
        )
    return (
        "X11; Linux x86_64",
        '"Linux"',
    )


def profile_singleton_paths(profile: Path) -> tuple[Path, ...]:
    """Chrome/Edge singleton marker paths under a user-data-dir."""
    return tuple(profile / name for name in (
        "SingletonLock",
        "SingletonSocket",
        "SingletonCookie",
    ))


def profile_singleton_present(profile: Path) -> bool:
    """True if Chrome left singleton markers (including broken symlinks).

    On macOS, ``SingletonLock`` is often a symlink to ``Host-pid`` that does
    **not** resolve as a real file, so ``Path.exists()`` is False even while
    Chrome still owns the profile. Use ``is_symlink()`` as well.
    """
    for path in profile_singleton_paths(profile):
        if path.is_symlink() or path.exists():
            return True
    return False


def clear_profile_singleton(profile: Path) -> None:
    """Remove stale singleton lock/socket/cookie markers (incl. broken symlinks)."""
    for path in profile_singleton_paths(profile):
        if path.is_symlink() or path.exists():
            with contextlib.suppress(OSError):
                path.unlink(missing_ok=True)


def kill_browsers_using_profile(profile: Path) -> None:
    """Kill only browser processes whose command line uses this user-data-dir.

    Safe for daily Chrome/Edge: those use a different profile path.
    """
    marker = str(profile.resolve())
    if is_macos() or sys.platform.startswith("linux"):
        subprocess.run(
            ["pkill", "-f", f"user-data-dir={marker}"],
            check=False,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        return
    if is_windows():
        # Match the bot profile only; do not kill the user's daily browser.
        ps = (
            "$m = '"
            + marker.replace("'", "''")
            + "'; "
            + "Get-CimInstance Win32_Process | "
            + "Where-Object { $_.CommandLine -and $_.CommandLine -like ('*' + $m + '*') } | "
            + "ForEach-Object { Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue }"
        )
        subprocess.run(
            ["powershell", "-NoProfile", "-Command", ps],
            check=False,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )


def open_url_in_system_chrome(url: str) -> None:
    """Open a URL in the user's everyday Chrome (default profile — not PokeBot)."""
    import os
    import shutil

    if is_macos():
        chrome = Path("/Applications/Google Chrome.app")
        if chrome.exists():
            subprocess.Popen(
                ["open", "-a", "Google Chrome", url],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
            return
        # Fall back to whatever `open` chooses for http(s).
        subprocess.Popen(
            ["open", url],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        return

    if is_windows():
        local = Path(os.environ.get("LOCALAPPDATA", ""))
        pf = Path(os.environ.get("PROGRAMFILES", r"C:\Program Files"))
        pf86 = Path(os.environ.get("PROGRAMFILES(X86)", r"C:\Program Files (x86)"))
        candidates = [
            pf / "Google/Chrome/Application/chrome.exe",
            pf86 / "Google/Chrome/Application/chrome.exe",
            local / "Google/Chrome/Application/chrome.exe",
        ]
        exe = next((p for p in candidates if p.exists()), None)
        if exe is not None:
            subprocess.Popen(
                [str(exe), url],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
            return
        os.startfile(url)  # type: ignore[attr-defined]
        return

    chrome = shutil.which("google-chrome") or shutil.which("chromium") or shutil.which(
        "chromium-browser"
    )
    if chrome:
        subprocess.Popen(
            [chrome, url],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        return
    import webbrowser

    webbrowser.open(url)
