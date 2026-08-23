from __future__ import annotations

import json
from typing import Any
from urllib.parse import urlsplit, urlunsplit


def _clean_text(value: Any) -> str:
    return " ".join(str(value or "").split()).strip()


def _canonical_href(value: Any) -> str:
    href = _clean_text(value)
    if not href:
        return ""
    try:
        parts = urlsplit(href)
        return urlunsplit((parts.scheme.lower(), parts.netloc.lower(), parts.path, "", ""))
    except ValueError:
        return href.split("?", 1)[0]


def raw_card_key(card: dict[str, Any]) -> str:
    """Build a stable raw-card identity across scroll rounds."""
    item_id = _clean_text(card.get("item_id"))
    shop_id = _clean_text(card.get("shop_id"))
    if item_id:
        return f"item:{shop_id}:{item_id}" if shop_id else f"item:{item_id}"

    href = _canonical_href(card.get("href"))
    if href:
        return f"href:{href}"

    title = _clean_text(card.get("title")).lower()
    price = _clean_text(card.get("price")).lower()
    if title:
        return f"title:{title}|price:{price}"

    text = _clean_text(card.get("text")).lower()
    if text:
        return f"text:{text[:300]}"

    return "json:" + json.dumps(card, ensure_ascii=False, sort_keys=True, default=str)


def _merge_card(existing: dict[str, Any], incoming: dict[str, Any]) -> dict[str, Any]:
    merged = dict(existing)
    for key, value in incoming.items():
        if value in (None, "", [], {}):
            continue
        if key in {"text", "title"}:
            old_text = _clean_text(merged.get(key))
            new_text = _clean_text(value)
            if old_text and len(new_text) < len(old_text):
                continue
        merged[key] = value
    return merged


class RawCardAccumulator:
    """Preserve first-seen order while enriching cards rediscovered after scrolling."""

    def __init__(self, max_cards: int = 200):
        self.max_cards = max(1, int(max_cards))
        self._cards: dict[str, dict[str, Any]] = {}

    def add(self, cards: list[dict[str, Any]] | None) -> int:
        new_items = 0
        for card in cards or []:
            if not isinstance(card, dict):
                continue
            key = raw_card_key(card)
            if key in self._cards:
                self._cards[key] = _merge_card(self._cards[key], card)
                continue
            if len(self._cards) >= self.max_cards:
                continue
            self._cards[key] = dict(card)
            new_items += 1
        return new_items

    def cards(self) -> list[dict[str, Any]]:
        return list(self._cards.values())

    def __len__(self) -> int:
        return len(self._cards)
