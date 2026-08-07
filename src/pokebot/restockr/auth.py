from __future__ import annotations

import json
from pathlib import Path

from pokebot.config import data_dir


TOKEN_FILE = data_dir() / "restockr_token.json"


def save_token(token: str, username: str | None = None) -> None:
    payload = {"token": token}
    if username:
        payload["username"] = username
    TOKEN_FILE.write_text(json.dumps(payload, indent=2))


def load_token() -> str | None:
    if not TOKEN_FILE.exists():
        return None
    data = json.loads(TOKEN_FILE.read_text())
    return data.get("token")


def load_username() -> str | None:
    if not TOKEN_FILE.exists():
        return None
    data = json.loads(TOKEN_FILE.read_text())
    return data.get("username")


def clear_token() -> None:
    if TOKEN_FILE.exists():
        TOKEN_FILE.unlink()
