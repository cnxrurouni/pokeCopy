from __future__ import annotations

"""Load Target email/password from env or a gitignored YAML file."""

import os
from dataclasses import dataclass
from pathlib import Path

import yaml

from pokebot.config import config_dir


@dataclass(frozen=True)
class TargetCredentials:
    email: str
    password: str
    source: str  # "env" | "file"


def credentials_path() -> Path:
    return config_dir() / "target.credentials.yaml"


def load_target_credentials() -> TargetCredentials | None:
    """Return Target login credentials, or None if not configured.

    Preference order:
      1. TARGET_EMAIL + TARGET_PASSWORD env vars
      2. config/target.credentials.yaml (gitignored)
    """
    email = (os.environ.get("TARGET_EMAIL") or "").strip()
    password = (os.environ.get("TARGET_PASSWORD") or "").strip()
    if email and password:
        return TargetCredentials(email=email, password=password, source="env")

    path = credentials_path()
    if not path.exists():
        return None
    try:
        raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    except Exception:
        return None
    if not isinstance(raw, dict):
        return None
    email = str(raw.get("email") or "").strip()
    password = str(raw.get("password") or "").strip()
    if email and password:
        return TargetCredentials(email=email, password=password, source="file")
    return None
