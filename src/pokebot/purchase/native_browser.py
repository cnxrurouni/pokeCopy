from __future__ import annotations

"""Launch installed Edge/Chrome with a persistent profile — no Playwright/CDP.

Used for Walmart queue join (and similar) where CDP control risks PerimeterX
pre-scoring. Same launch style as ``login --manual-chrome``.
"""

import os
import shutil
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path


@dataclass
class NativeBrowserSession:
    """A subprocess browser using ``--user-data-dir`` (no remote debugging)."""

    proc: subprocess.Popen | None
    exe: Path | None
    profile: Path
    start_url: str
    command: str

    @property
    def pid(self) -> int | None:
        if self.proc is None:
            return None
        return self.proc.pid

    def is_running(self) -> bool:
        return self.proc is not None and self.proc.poll() is None

    def terminate_gently(self, *, wait_s: float = 10.0) -> None:
        if self.proc is None:
            return
        if self.proc.poll() is not None:
            return
        try:
            self.proc.terminate()
        except Exception:
            pass
        try:
            self.proc.wait(timeout=wait_s)
        except Exception:
            try:
                self.proc.kill()
            except Exception:
                pass


def resolve_browser_exe(channel: str | None = "msedge") -> Path | None:
    """Locate installed Edge or Chrome. Prefers Edge when channel is msedge/edge."""
    ch = (channel or "msedge").lower()
    candidates: list[Path] = []
    if sys.platform == "win32":
        local = Path(os.environ.get("LOCALAPPDATA", ""))
        pf = Path(os.environ.get("PROGRAMFILES", r"C:\Program Files"))
        pf86 = Path(os.environ.get("PROGRAMFILES(X86)", r"C:\Program Files (x86)"))
        if ch in ("msedge", "edge", ""):
            candidates += [
                pf / "Microsoft/Edge/Application/msedge.exe",
                pf86 / "Microsoft/Edge/Application/msedge.exe",
            ]
        if ch in ("chrome", "chrome-beta", ""):
            candidates += [
                pf / "Google/Chrome/Application/chrome.exe",
                pf86 / "Google/Chrome/Application/chrome.exe",
                local / "Google/Chrome/Application/chrome.exe",
            ]
        # If preferred channel missing, try the other.
        if ch in ("msedge", "edge") and not any(p.exists() for p in candidates):
            candidates += [
                pf / "Google/Chrome/Application/chrome.exe",
                local / "Google/Chrome/Application/chrome.exe",
            ]
        if ch in ("chrome", "chrome-beta") and not any(p.exists() for p in candidates):
            candidates += [
                pf / "Microsoft/Edge/Application/msedge.exe",
                pf86 / "Microsoft/Edge/Application/msedge.exe",
            ]
    elif sys.platform == "darwin":
        if ch in ("msedge", "edge", ""):
            candidates.append(
                Path("/Applications/Microsoft Edge.app/Contents/MacOS/Microsoft Edge")
            )
        candidates.append(
            Path("/Applications/Google Chrome.app/Contents/MacOS/Google Chrome")
        )
    else:
        for name in ("microsoft-edge", "google-chrome", "chromium"):
            found = shutil.which(name)
            if found:
                candidates.append(Path(found))

    return next((p for p in candidates if p and p.exists()), None)


def launch_cmd_hint(profile: Path, start_url: str, *, channel: str | None = "msedge") -> str:
    if sys.platform == "win32":
        exe = "msedge.exe" if (channel or "msedge").lower() in ("msedge", "edge", "") else "chrome.exe"
        return f'{exe} --user-data-dir="{profile}" "{start_url}"'
    return (
        f'open -na "Google Chrome" --args --user-data-dir="{profile}" "{start_url}"'
    )


def launch_native_browser(
    *,
    profile: Path,
    start_url: str,
    channel: str | None = "msedge",
) -> NativeBrowserSession:
    """Start real Edge/Chrome on ``profile`` at ``start_url`` (no CDP flags)."""
    profile = profile.resolve()
    profile.mkdir(parents=True, exist_ok=True)
    exe = resolve_browser_exe(channel)
    if exe is None:
        hint = launch_cmd_hint(profile, start_url, channel=channel)
        return NativeBrowserSession(
            proc=None,
            exe=None,
            profile=profile,
            start_url=start_url,
            command=hint,
        )

    # Intentionally omit --remote-debugging-port — that flag is a bot signal.
    cmd = [
        str(exe),
        f"--user-data-dir={profile}",
        "--no-first-run",
        "--no-default-browser-check",
        start_url,
    ]
    proc = subprocess.Popen(
        cmd,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    return NativeBrowserSession(
        proc=proc,
        exe=exe,
        profile=profile,
        start_url=start_url,
        command=" ".join(cmd),
    )
