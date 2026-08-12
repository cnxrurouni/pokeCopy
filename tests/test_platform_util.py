from __future__ import annotations

import sys
from pathlib import Path

from pokebot.platform_util import (
    browser_ua_platform,
    is_macos,
    is_windows,
    quit_bot_browser_hint,
)


def test_browser_ua_matches_host() -> None:
    _os, platform = browser_ua_platform()
    assert platform in {'"Windows"', '"macOS"', '"Linux"'}
    if sys.platform == "darwin":
        assert is_macos()
        assert "Macintosh" in _os
    elif sys.platform == "win32":
        assert is_windows()
        assert "Windows" in _os


def test_quit_hint_non_empty() -> None:
    assert quit_bot_browser_hint()


def test_kill_browsers_using_profile_noop_on_missing() -> None:
    from pokebot.platform_util import kill_browsers_using_profile

    # Must not raise when nothing matches.
    kill_browsers_using_profile(Path("/tmp/pokebot-no-such-profile-dir"))
