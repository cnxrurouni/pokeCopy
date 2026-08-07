from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field

_VAR_PATTERN = re.compile(r"\{\{\s*([a-zA-Z0-9_.]+)\s*\}\}")


def substitute(text: str, variables: dict[str, Any]) -> str:
    """Replace ``{{var}}`` placeholders with values from ``variables``.

    Unknown placeholders are left intact so a partially-filled template is
    obvious rather than silently blanked.
    """

    def repl(match: re.Match[str]) -> str:
        key = match.group(1)
        if key in variables:
            return str(variables[key])
        return match.group(0)

    return _VAR_PATTERN.sub(repl, text)


def dot_get(data: Any, path: str) -> Any:
    """Fetch a nested value by dot path, supporting numeric list indices."""
    current = data
    for part in path.split("."):
        if current is None:
            return None
        if isinstance(current, list):
            try:
                current = current[int(part)]
            except (ValueError, IndexError):
                return None
        elif isinstance(current, dict):
            current = current.get(part)
        else:
            return None
    return current


class CapturedRequest(BaseModel):
    """One recorded HTTP request to replay, with templating hooks.

    ``url``, ``headers`` values, and ``body`` may contain ``{{var}}`` placeholders.
    After the response returns, ``extract`` pulls values (by dot path into the JSON
    body) into the variable bag for later requests in the sequence.
    """

    name: str
    method: str = "GET"
    url: str
    headers: dict[str, str] = Field(default_factory=dict)
    body: str | None = None
    extract: dict[str, str] = Field(default_factory=dict)
    success_contains: str | None = None
    expect_status: int | None = None
    # The request that actually commits the purchase (place_order). Preflight
    # validation runs the chain up to — but not including — this request, so the
    # token/cart/checkout can be proven without spending money.
    commits_order: bool = False


class CaptureFile(BaseModel):
    """A recorded sequence of retailer API requests that reproduce a checkout."""

    retailer: str = "target"
    cookies: dict[str, str] = Field(default_factory=dict)
    variables: dict[str, str] = Field(default_factory=dict)
    sequence: list[str] = Field(default_factory=list)
    requests: list[CapturedRequest] = Field(default_factory=list)

    def by_name(self) -> dict[str, CapturedRequest]:
        return {r.name: r for r in self.requests}

    def ordered(self) -> list[CapturedRequest]:
        index = self.by_name()
        names = self.sequence or [r.name for r in self.requests]
        return [index[n] for n in names if n in index]

    @classmethod
    def load(cls, path: str | Path) -> CaptureFile:
        return cls.model_validate_json(Path(path).read_text())

    def save(self, path: str | Path) -> None:
        p = Path(path)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(json.dumps(self.model_dump(), indent=2))
