from __future__ import annotations

import atexit
import os
import re
import sys
from datetime import datetime, timezone
from pathlib import Path

from pokebot.config import data_dir

# Strip Rich/ANSI color codes from the file copy (keep them on the live terminal).
_ANSI_RE = re.compile(r"\x1b\[[0-9;]*[A-Za-z]|\x1b\].*?\x07|\x1b\[[\?][0-9;]*[A-Za-z]")

_active: "RunLog | None" = None


def terminal_log_dir() -> Path:
    path = data_dir() / "logs" / "terminal"
    path.mkdir(parents=True, exist_ok=True)
    return path


def latest_log_path() -> Path:
    return terminal_log_dir() / "latest.log"


class _TeeStream:
    """Mirror writes to the real stream and a log file (ANSI-stripped)."""

    def __init__(self, primary, log_fp) -> None:
        self._primary = primary
        self._log = log_fp

    def write(self, data) -> int:
        if not isinstance(data, str):
            data = data.decode("utf-8", errors="replace")
        n = self._primary.write(data)
        try:
            self._log.write(_ANSI_RE.sub("", data))
            self._log.flush()
        except Exception:
            pass
        return n

    def flush(self) -> None:
        try:
            self._primary.flush()
        except Exception:
            pass
        try:
            self._log.flush()
        except Exception:
            pass

    def isatty(self) -> bool:
        try:
            return bool(self._primary.isatty())
        except Exception:
            return False

    def fileno(self) -> int:
        return self._primary.fileno()

    @property
    def encoding(self):
        return getattr(self._primary, "encoding", "utf-8")

    def __getattr__(self, name: str):
        return getattr(self._primary, name)


class RunLog:
    """Tee stdout/stderr into data/logs/terminal/ for post-run review."""

    def __init__(self, path: Path, archive_path: Path) -> None:
        self.path = path
        self.archive_path = archive_path
        self._fp = path.open("w", encoding="utf-8", newline="\n", buffering=1)
        self._orig_out = sys.stdout
        self._orig_err = sys.stderr
        self._closed = False

    def install(self) -> None:
        sys.stdout = _TeeStream(self._orig_out, self._fp)  # type: ignore[assignment]
        sys.stderr = _TeeStream(self._orig_err, self._fp)  # type: ignore[assignment]

    def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        sys.stdout = self._orig_out
        sys.stderr = self._orig_err
        try:
            self._fp.flush()
            self._fp.close()
        except Exception:
            pass
        # Keep a timestamped copy alongside latest.log
        try:
            if self.path.exists() and self.archive_path != self.path:
                self.archive_path.write_bytes(self.path.read_bytes())
        except Exception:
            pass


def start_run_log(*, label: str | None = None) -> Path | None:
    """Begin teeing terminal output. Returns the latest.log path, or None if disabled."""
    global _active
    if os.environ.get("POKEBOT_NO_RUN_LOG", "").strip() in ("1", "true", "yes"):
        return None
    if _active is not None:
        return _active.path

    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    latest = latest_log_path()
    archive = terminal_log_dir() / f"run-{stamp}.log"
    run = RunLog(latest, archive)
    run.install()
    _active = run
    atexit.register(stop_run_log)

    cmd = " ".join(sys.argv)
    header = (
        f"=== PokeBot run log started {stamp} ===\n"
        f"cwd: {os.getcwd()}\n"
        f"argv: {cmd}\n"
        + (f"label: {label}\n" if label else "")
        + f"log: {latest}\n"
        f"archive: {archive}\n"
        f"{'=' * 60}\n"
    )
    # Write header to file only (also appears via tee once printed).
    print(header, end="")
    return latest


def stop_run_log() -> None:
    global _active
    if _active is None:
        return
    try:
        print(
            f"\n=== PokeBot run log closed → {_active.path} "
            f"(archive {_active.archive_path.name}) ===\n",
            end="",
        )
    except Exception:
        pass
    _active.close()
    _active = None
