from __future__ import annotations

import os
from pathlib import Path

import yaml
from pydantic import BaseModel, Field


def project_root() -> Path:
    return Path(__file__).resolve().parents[2]


def config_dir() -> Path:
    env = os.environ.get("POKEBOT_CONFIG_DIR")
    if env:
        return Path(env)
    return project_root() / "config"


def data_dir() -> Path:
    root = project_root()
    path = root / "data"
    path.mkdir(exist_ok=True)
    return path


class RestockRSettings(BaseModel):
    api_base: str = "https://emerald-alerts-development.onrender.com/api"
    socket_url: str = "https://emerald-alerts-development.onrender.com"


class AutobuySettings(BaseModel):
    """Filters shared by open-alerts and reseller RestockR listeners."""

    enabled: bool = True
    watchlist_only: bool = True
    max_price: float | None = None
    max_quantity: int | None = None
    target_min_quantity: int = 1
    cooldown_seconds: int = 300
    dedup_window_seconds: int = 60
    retailers: list[str] = Field(default_factory=lambda: ["target"])


class DiscordSettings(BaseModel):
    """Discord channel → reseller checkout (bot token via env)."""

    guild_id: str = ""
    channel_id: str = ""
    token_env: str = "DISCORD_BOT_TOKEN"
    # Discord alerts are intentional channel signals — do not require RestockR watchlist.
    watchlist_only: bool = False
    open_in_chrome: bool = True


class Settings(BaseModel):
    restockr: RestockRSettings = Field(default_factory=RestockRSettings)
    autobuy: AutobuySettings = Field(default_factory=AutobuySettings)
    discord: DiscordSettings = Field(default_factory=DiscordSettings)


def _load_yaml(path: Path) -> dict:
    if not path.exists():
        return {}
    with path.open() as f:
        return yaml.safe_load(f) or {}


def load_settings() -> Settings:
    return Settings.model_validate(_load_yaml(config_dir() / "settings.yaml"))


def session_dir(retailer: str) -> Path:
    """On-disk Chrome profile directory used only for manual Target login export."""
    path = data_dir() / "sessions" / retailer
    path.mkdir(parents=True, exist_ok=True)
    return path
