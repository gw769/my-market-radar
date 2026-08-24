from __future__ import annotations

from typing import Any, Iterable


# Deterministic Malaysia-market search profiles. Keep these narrow: a profile may bridge
# languages, but it must not silently broaden the product family (for example, nail stickers
# are not the same product as fake nails or generic manicure tools).
_QUERY_PROFILES: dict[str, tuple[str, ...]] = {
    "指甲贴": (
        "nail sticker",
        "nail stickers",
        "nail decal",
        "nail decals",
        "toenail sticker",
        "toenail stickers",
        "pelekat kuku",
        "美甲贴纸",
    ),
    "美甲贴纸": (
        "nail sticker",
        "nail stickers",
        "nail decal",
        "nail decals",
        "toenail sticker",
        "toenail stickers",
        "pelekat kuku",
        "指甲贴",
    ),
    "美甲贴": (
        "nail sticker",
        "nail stickers",
        "nail decal",
        "nail decals",
        "toenail sticker",
        "toenail stickers",
        "pelekat kuku",
        "指甲贴",
        "美甲贴纸",
    ),
    "毛巾": (
        "towel",
        "towels",
        "tuala",
    ),
}


def _normalized(value: str) -> str:
    return " ".join(str(value or "").lower().split())


def _clean_phrases(values: Iterable[Any]) -> tuple[str, ...]:
    cleaned: list[str] = []
    for value in values:
        phrase = _normalized(str(value or ""))
        if not phrase or len(phrase) > 100 or phrase in cleaned:
            continue
        cleaned.append(phrase)
    return tuple(cleaned)


def deterministic_localization(keyword: str) -> dict[str, Any] | None:
    normalized = _normalized(keyword)
    aliases = _QUERY_PROFILES.get(normalized, ())
    if not normalized or not aliases:
        return None
    return {
        "keyword": normalized,
        "search_term": aliases[0],
        "aliases": list(_clean_phrases(aliases)),
        "source": "deterministic",
        "model": None,
    }


def valid_cached_localization(keyword: str, value: Any) -> dict[str, Any] | None:
    if not isinstance(value, dict):
        return None
    normalized = _normalized(keyword)
    if _normalized(str(value.get("keyword") or "")) != normalized:
        return None
    search_term = _normalized(str(value.get("search_term") or ""))
    aliases = _clean_phrases(value.get("aliases") or ())
    if not search_term or len(search_term) > 100:
        return None
    return {
        "keyword": normalized,
        "search_term": search_term,
        "aliases": list(_clean_phrases((search_term, *aliases))),
        "source": str(value.get("source") or "cached")[:30],
        "model": str(value.get("model") or "")[:100] or None,
    }


def effective_localization(keyword: str, cached: Any = None) -> dict[str, Any] | None:
    return deterministic_localization(keyword) or valid_cached_localization(keyword, cached)


def relevance_phrases(
    keyword: str,
    extra_phrases: Iterable[Any] | None = None,
) -> tuple[str, ...]:
    normalized = _normalized(keyword)
    aliases = _QUERY_PROFILES.get(normalized, ())
    extras = tuple(extra_phrases or ())
    return _clean_phrases((normalized, *aliases, *extras)) if normalized else ()


def marketplace_search_term(keyword: str, localization: Any = None) -> str:
    normalized = _normalized(keyword)
    effective = effective_localization(keyword, localization)
    if effective:
        return str(effective["search_term"])
    aliases = _QUERY_PROFILES.get(normalized, ())
    return aliases[0] if aliases else " ".join(str(keyword or "").split())
