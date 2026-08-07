from __future__ import annotations


def normalize_store(store: str) -> str:
    return store.strip().lower().replace(" ", "")
