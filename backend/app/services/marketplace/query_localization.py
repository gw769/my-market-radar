from __future__ import annotations


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
}


def _normalized(value: str) -> str:
    return " ".join(str(value or "").lower().split())


def relevance_phrases(keyword: str) -> tuple[str, ...]:
    normalized = _normalized(keyword)
    aliases = _QUERY_PROFILES.get(normalized, ())
    return tuple(dict.fromkeys((normalized, *aliases))) if normalized else ()


def marketplace_search_term(keyword: str) -> str:
    normalized = _normalized(keyword)
    aliases = _QUERY_PROFILES.get(normalized, ())
    return aliases[0] if aliases else " ".join(str(keyword or "").split())
