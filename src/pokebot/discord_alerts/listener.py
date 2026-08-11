from __future__ import annotations

import asyncio
import os
from collections.abc import Awaitable, Callable
from typing import Any

from rich.console import Console

from pokebot.discord_alerts.parse import parse_discord_alert_text
from pokebot.restockr.models import RestockAlert

console = Console()

RestockHandler = Callable[[RestockAlert], Awaitable[None]]

_TOKEN_HINT = (
    "Discord rejected the bot token (401). Use the Bot Token from "
    "Developer Portal → your app → Bot → Reset Token / Copy — not the "
    "OAuth2 Client Secret, not an Application ID, and not a user account token. "
    "Export without quotes: export DISCORD_BOT_TOKEN=..... "
    "Do not prefix with 'Bot '. After reset, old tokens stop working."
)


def normalize_bot_token(raw: str) -> str:
    """Strip quotes / accidental 'Bot ' prefix from env / pasted tokens."""
    token = (raw or "").strip()
    if len(token) >= 2 and token[0] == token[-1] and token[0] in "'\"":
        token = token[1:-1].strip()
    if token.lower().startswith("bot "):
        token = token[4:].strip()
    return token


def _embed_parts(embed: Any) -> tuple[list[str], list[str]]:
    texts: list[str] = []
    urls: list[str] = []
    for attr in ("title", "description"):
        value = getattr(embed, attr, None)
        if value:
            texts.append(str(value))
    url = getattr(embed, "url", None)
    if url:
        urls.append(str(url))
    author = getattr(embed, "author", None)
    if author is not None:
        if getattr(author, "name", None):
            texts.append(str(author.name))
        if getattr(author, "url", None):
            urls.append(str(author.url))
    footer = getattr(embed, "footer", None)
    if footer is not None and getattr(footer, "text", None):
        texts.append(str(footer.text))
    for field in getattr(embed, "fields", None) or ():
        name = getattr(field, "name", None) or ""
        value = getattr(field, "value", None) or ""
        texts.append(f"{name}: {value}".strip(": "))
    return texts, urls


def message_to_alert(message: Any) -> RestockAlert | None:
    embed_texts: list[str] = []
    embed_urls: list[str] = []
    for embed in getattr(message, "embeds", None) or ():
        texts, urls = _embed_parts(embed)
        embed_texts.extend(texts)
        embed_urls.extend(urls)
    return parse_discord_alert_text(
        message_id=str(getattr(message, "id", "") or ""),
        content=str(getattr(message, "content", "") or ""),
        embed_texts=embed_texts,
        embed_urls=embed_urls,
    )


class DiscordAlertListener:
    """discord.py gateway client scoped to one guild channel."""

    def __init__(
        self,
        *,
        token: str,
        guild_id: int,
        channel_id: int,
    ) -> None:
        self.token = token
        self.guild_id = guild_id
        self.channel_id = channel_id
        self._handlers: list[RestockHandler] = []
        self._client: Any = None
        self._closed = asyncio.Event()

    def on_restock(self, handler: RestockHandler) -> None:
        self._handlers.append(handler)

    @classmethod
    def from_env(
        cls,
        *,
        guild_id: str | int,
        channel_id: str | int,
        token_env: str = "DISCORD_BOT_TOKEN",
    ) -> DiscordAlertListener:
        token = normalize_bot_token(os.environ.get(token_env) or "")
        if not token:
            raise RuntimeError(
                f"Set {token_env} to a Discord bot token that can read the alert channel. "
                "Invite the bot with View Channel + Read Message History, and enable "
                "Message Content Intent in the Discord Developer Portal."
            )
        return cls(
            token=token,
            guild_id=int(guild_id),
            channel_id=int(channel_id),
        )

    async def _dispatch(self, alert: RestockAlert) -> None:
        for handler in self._handlers:
            try:
                await handler(alert)
            except Exception as exc:  # noqa: BLE001 — keep listener alive
                console.print(f"[red]Discord handler error:[/red] {exc}")

    async def run(self) -> None:
        try:
            import discord
        except ImportError as exc:  # pragma: no cover
            raise RuntimeError(
                "discord.py is required for Discord alerts. "
                'Install with: pip install -e ".[discord]"'
            ) from exc

        intents = discord.Intents.default()
        intents.message_content = True
        intents.guilds = True
        client = discord.Client(intents=intents)
        self._client = client

        @client.event
        async def on_ready() -> None:
            console.print(
                f"[green]Discord connected[/green] as {client.user} — "
                f"watching channel {self.channel_id}"
            )

        @client.event
        async def on_message(message: discord.Message) -> None:
            if client.user and message.author.id == client.user.id:
                return
            if message.channel.id != self.channel_id:
                return
            guild = message.guild
            if guild is None or guild.id != self.guild_id:
                return
            alert = message_to_alert(message)
            if alert is None:
                console.print(
                    "[dim]Discord message ignored — no Target URL/TCIN "
                    f"(id={message.id})[/dim]"
                )
                return
            console.print(
                f"[cyan]Discord Target alert[/cyan] {alert.product or alert.sku} "
                f"→ {alert.resolve_url()}"
            )
            await self._dispatch(alert)

        try:
            await client.start(self.token)
        except Exception as exc:
            name = type(exc).__name__
            if name in {"LoginFailure", "HTTPException"} or "401" in str(exc):
                raise RuntimeError(_TOKEN_HINT) from exc
            raise
        finally:
            if not client.is_closed():
                await client.close()
            self._closed.set()

    async def close(self) -> None:
        client = self._client
        if client is not None and not client.is_closed():
            await client.close()
        self._closed.set()
