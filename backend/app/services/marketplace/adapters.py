from __future__ import annotations

import re
from dataclasses import asdict, dataclass
from typing import Any, Iterable
from urllib.parse import quote, urlparse

from app.services.marketplace.query_localization import marketplace_search_term


class VerificationRequired(RuntimeError):
    def __init__(self, platform: str, url: str, context: dict[str, Any] | None = None):
        super().__init__(f"{platform} 需要人工验证")
        self.platform = platform
        self.url = url
        self.context = context or {}


@dataclass(slots=True)
class MarketplaceListing:
    platform: str
    item_id: str
    title: str
    product_url: str
    search_rank: int
    shop_id: str | None = None
    image_url: str | None = None
    price: float | None = None
    original_price: float | None = None
    discount_percent: float | None = None
    sold_count: int | None = None
    rating: float | None = None
    review_count: int | None = None
    seller_name: str | None = None
    seller_location: str | None = None
    is_sponsored: bool | None = None
    data_quality: float = 0.0
    raw_data: dict[str, Any] | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def parse_money(value: Any, require_currency: bool = False) -> float | None:
    if value is None:
        return None
    text = str(value).replace(",", "")
    match = re.search(r"RM\s*([0-9]+(?:\.[0-9]{1,2})?)", text, re.I)
    if match:
        return float(match.group(1))
    if require_currency:
        return None
    match = re.fullmatch(r"\s*([0-9]+(?:\.[0-9]{1,2})?)\s*", text)
    return float(match.group(1)) if match else None


def parse_money_range(value: Any) -> tuple[float, float] | None:
    """Return an explicit marketplace price range, never a crossed-out old price.

    Only range separators in the same textual expression count. Two independent `RM` values on
    different lines are common current/original prices and must not be treated as a range.
    """
    if value is None:
        return None
    text = str(value).replace(",", "")
    match = re.search(
        r"RM[^\S\r\n]*([0-9]+(?:\.[0-9]{1,2})?)[^\S\r\n]*"
        r"(?:-|–|—|~|to)[^\S\r\n]*(?:RM[^\S\r\n]*)?"
        r"([0-9]+(?:\.[0-9]{1,2})?)(?![0-9.]|[^\S\r\n]*%)",
        text,
        re.I,
    )
    if not match:
        return None
    low, high = float(match.group(1)), float(match.group(2))
    if low <= 0 or high <= 0:
        return None
    return (min(low, high), max(low, high))


def parse_compact_count(value: Any) -> int | None:
    if value is None:
        return None
    text = str(value).strip().lower().replace(",", "")
    match = re.search(r"([0-9]+(?:\.[0-9]+)?)\s*([km千萬万亿億]?)", text)
    if not match:
        return None
    number = float(match.group(1))
    multiplier = {
        "": 1,
        "k": 1_000,
        "千": 1_000,
        "萬": 10_000,
        "万": 10_000,
        "m": 1_000_000,
        "亿": 100_000_000,
        "億": 100_000_000,
    }[match.group(2)]
    return int(number * multiplier)


def _first_match(patterns: Iterable[str], text: str, flags: int = re.I) -> str | None:
    for pattern in patterns:
        match = re.search(pattern, text, flags)
        if match:
            return match.group(1)
    return None


def _quality(raw: dict[str, Any]) -> float:
    core = [raw.get("title"), raw.get("href"), raw.get("price")]
    detail = [raw.get("sold"), raw.get("rating"), raw.get("reviews"), raw.get("image")]
    return round((sum(v not in (None, "") for v in core) * 0.2) + (sum(v not in (None, "") for v in detail) * 0.1), 2)


def _safe_rating(value: Any) -> float | None:
    if value in (None, ""):
        return None
    try:
        rating = float(value)
    except (TypeError, ValueError):
        return None
    return rating if 0 <= rating <= 5 else None


def _clean_location(value: Any, title: str = "") -> str | None:
    if value in (None, ""):
        return None
    text = " ".join(str(value).split()).strip()
    if not text or text.lower() == " ".join(title.lower().split()):
        return None
    if re.search(r"^(?:sponsored|iklan|ad|advertisement)$", text, re.I):
        return None
    if re.search(r"^RM\s*[0-9]", text, re.I):
        return None
    if re.search(r"(?:sold|terjual|reviews?|ratings|ulasan|penilaian)", text, re.I):
        return None
    if re.search(r"(?:已售(?:出)?|售出|销量|銷量|^售\s*[0-9])", text, re.I):
        return None
    if text in {"找相似", "相似商品", "similar"}:
        return None
    if re.fullmatch(r"[0-5](?:\.[0-9])?", text):
        return None
    return text[:200]


# A singular label such as "4.8 rating" normally describes the score, not a count.
_REVIEW_PATTERNS = (
    r"([0-9,.]+\s*[km]?\s*\+?)\s*(?:reviews?|ratings|ulasan|penilaian)\b",
    r"(?m)^\s*\(([0-9,.]+\s*[km千萬万亿億]?\s*\+?)\)\s*$",
)
_SOLD_PATTERNS = (
    r"([0-9,.]+\s*[km]?\s*\+?)\s*(?:sold|terjual)\b",
    r"(?:已售(?:出)?|售出|销量|銷量|售)\s*([0-9,.]+\s*[km千萬万亿億]?\s*\+?)(?:\s*件)?",
)


class MarketplaceAdapter:
    platform: str
    blocked_hosts: tuple[str, ...] = ()

    def search_url(self, keyword: str, page: int = 1) -> str:
        raise NotImplementedError

    @property
    def extraction_script(self) -> str:
        raise NotImplementedError

    def is_verification_page(self, url: str, body_text: str = "") -> bool:
        parsed = urlparse(url)
        host = parsed.netloc.lower()
        path = parsed.path.lower()
        body = body_text[:2500].lower()
        if any(blocked in host for blocked in self.blocked_hosts):
            return True

        path_signals = (
            "/verify", "/verification", "/captcha", "/punish", "/security-check",
            "/security_check", "/challenge",
        )
        if any(signal in path for signal in path_signals):
            return True

        body_signals = (
            "captcha", "verification required", "please verify", "verify to continue",
            "verify your identity", "robot check", "security check", "unusual traffic",
            "suspicious activity", "drag the slider", "slide to verify", "complete verification",
        )
        return any(signal in body for signal in body_signals)

    def parse_cards(self, cards: list[dict[str, Any]], limit: int) -> list[MarketplaceListing]:
        results: list[MarketplaceListing] = []
        seen: set[str] = set()
        for raw in cards:
            listing = self.parse_card(raw, len(results) + 1)
            if not listing or listing.item_id in seen:
                continue
            seen.add(listing.item_id)
            results.append(listing)
            if len(results) >= limit:
                break
        return results

    def parse_card(self, raw: dict[str, Any], rank: int) -> MarketplaceListing | None:
        raise NotImplementedError


class ShopeeMalaysiaAdapter(MarketplaceAdapter):
    platform = "shopee"
    blocked_hosts = ("xiapibuy.com",)

    def search_url(self, keyword: str, page: int = 1) -> str:
        localized = marketplace_search_term(keyword)
        page_index = max(1, int(page)) - 1
        return f"https://shopee.com.my/search?keyword={quote(localized)}&page={page_index}"

    @property
    def extraction_script(self) -> str:
        return r"""() => {
          const cards = Array.from(document.querySelectorAll('[data-sqe="item"]'));
          const pageSize = cards.length;
          return cards.map((card, pageIndex) => {
          const a = card.querySelector('a[href*="-i."]');
          if (!a) return null;
          const text = (card?.innerText || a.innerText || '').trim();
          const lines = text.split('\n').map(x => x.trim()).filter(Boolean);
          const image = card?.querySelector('img');
          const href = a.href;
          const id = href.match(/-i\.(\d+)\.(\d+)/);
          const title = a.getAttribute('aria-label') || a.title || lines[0] || '';
          const ratingNode = card?.querySelector('img[alt*="rating-star" i], [aria-label*="rating" i], [aria-label*="star" i], [class*="rating" i]');
          const ratingText = (ratingNode?.getAttribute('aria-label') || ratingNode?.textContent || '').trim();
          const ratingArea = ratingNode ? (ratingNode.parentElement?.innerText || '') : '';
          const price = text.match(/RM\s*[0-9,.]+/i)?.[0] || null;
          const sold = text.match(/[0-9,.]+\s*[km]?\s*\+?\s*(?:sold|terjual)\b/i)?.[0]
            || text.match(/(?:已售(?:出)?|销量|銷量|售)\s*[0-9,.]+\s*(?:[km千萬万亿億])?\s*\+?\s*(?:件)?/i)?.[0] || null;
          const reviews = ratingArea.match(/\(([0-9,.]+\s*[km]?\s*\+?)\)/)?.[1]
            || text.match(/([0-9,.]+\s*[km]?\s*\+?)\s*(?:reviews?|ratings|ulasan|penilaian)\b/i)?.[1]
            || text.match(/^\s*\(([0-9,.]+\s*[km千萬万亿億]?\s*\+?)\)\s*$/im)?.[1] || null;
          const rating = ratingText.match(/\b[0-5](?:\.[0-9])?\b/)?.[0]
            || ratingArea.match(/(?:^|\n)\s*([0-5](?:\.[0-9])?)\s*(?:\n|$)/)?.[1]
            || text.match(/(?:rating|rated|bintang)\s*[:\-]?\s*([0-5](?:\.[0-9])?)/i)?.[1] || null;
          const location = [...lines].reverse().find(line => line !== title
            && !/^(?:sponsored|iklan|ad|advertisement|广告|廣告)$/i.test(line)
            && !/^RM\s*[0-9]/i.test(line)
            && !/(?:sold|terjual|reviews?|ratings|ulasan|penilaian)/i.test(line)
            && !/(?:已售(?:出)?|销量|銷量|^售\s*[0-9])/i.test(line)
            && !/^(?:找相似|相似商品|similar)$/i.test(line)
            && !/^\([0-9,.]+\s*[km千萬万亿億]?\s*\+?\)$/.test(line)
            && !/^[0-5](?:\.[0-9])?$/.test(line)) || null;
          return {href, text, title,
            image: image?.currentSrc || image?.src || null, price, sold, rating, reviews,
            seller: null, location,
            sponsored: /sponsored|iklan|广告|廣告/i.test(text), shop_id: id?.[1] || null,
            item_id: id?.[2] || null, page_position: pageIndex + 1, page_size: pageSize};
          }).filter(Boolean);
        }"""

    def parse_card(self, raw: dict[str, Any], rank: int) -> MarketplaceListing | None:
        href = str(raw.get("href") or "")
        id_match = re.search(r"-i\.(\d+)\.(\d+)", href)
        item_id = str(raw.get("item_id") or (id_match.group(2) if id_match else ""))
        if not item_id:
            return None
        text = str(raw.get("text") or "")
        title = str(raw.get("title") or "").strip()
        title = re.sub(r"^view product:\s*", "", title, flags=re.I)
        if not title or title.lower().startswith("rm"):
            lines = [line.strip() for line in text.splitlines() if line.strip()]
            title = next((line for line in lines if not re.match(r"^(RM|[0-9.]+\s*(sold|terjual))", line, re.I)), "")
        if not title:
            return None
        sold_text = raw.get("sold") or _first_match(_SOLD_PATTERNS, text)
        review_text = raw.get("reviews") or _first_match(_REVIEW_PATTERNS, text)
        # A visible variant range such as RM 10 - RM 20 has no single comparable unit price.
        # Using only the minimum systematically made these products look cheaper than peers.
        price = None if parse_money_range(text) else (
            parse_money(raw.get("price")) if raw.get("price") else parse_money(text, require_currency=True)
        )
        return MarketplaceListing(
            platform=self.platform,
            item_id=item_id,
            shop_id=str(raw.get("shop_id") or (id_match.group(1) if id_match else "")) or None,
            title=title[:1000],
            product_url=href.split("?")[0],
            image_url=raw.get("image"),
            price=price,
            sold_count=parse_compact_count(sold_text),
            rating=_safe_rating(raw.get("rating")),
            review_count=parse_compact_count(review_text),
            seller_name=raw.get("seller"),
            seller_location=_clean_location(raw.get("location"), title),
            is_sponsored=bool(raw.get("sponsored")) if raw.get("sponsored") is not None else None,
            search_rank=rank,
            data_quality=_quality(raw),
            raw_data=raw,
        )


class LazadaMalaysiaAdapter(MarketplaceAdapter):
    platform = "lazada"
    blocked_hosts = ("acs-m.lazada.com.my",)

    def search_url(self, keyword: str, page: int = 1) -> str:
        localized = marketplace_search_term(keyword)
        suffix = f"&page={max(1, int(page))}" if page > 1 else ""
        return f"https://www.lazada.com.my/catalog/?q={quote(localized)}{suffix}"

    @property
    def extraction_script(self) -> str:
        return r"""() => {
          const seenCards = new Set();
          const entries = [];
          for (const a of Array.from(document.querySelectorAll('a[href*="/products/"]'))) {
            const card = a.closest('[data-item-id]') || a.closest('[class*="Bm3ON"]') || a.closest('div[data-qa-locator="product-item"]') || a.parentElement?.parentElement;
            if (!card || seenCards.has(card)) continue;
            seenCards.add(card);
            entries.push({a, card});
          }
          const listNumbers = entries.map(({card}) => {
            const raw = card.getAttribute('data-listno') || card.querySelector('[data-listno]')?.getAttribute('data-listno');
            return raw == null || raw === '' ? null : Number(raw);
          }).filter(value => Number.isFinite(value));
          const pageSize = listNumbers.length ? Math.max(...listNumbers) + 1 : entries.length;
          return entries.map(({a, card}, fallbackIndex) => {
          const text = (card?.innerText || a.innerText || '').trim();
          const lines = text.split('\n').map(x => x.trim()).filter(Boolean);
          const image = card?.querySelector('img');
          const href = a.href;
          const itemId = card?.getAttribute('data-item-id') || href.match(/-i(\d+)-/i)?.[1] || href.match(/\/products\/[^/]*-(\d+)\.html/i)?.[1];
          const title = a.title || a.getAttribute('aria-label') || lines[0] || '';
          const ratingNode = card?.querySelector('[aria-label*="rating" i], [aria-label*="star" i], [class*="rating" i]');
          const ratingText = (ratingNode?.getAttribute('aria-label') || ratingNode?.textContent || '').trim();
          const ratingArea = ratingNode ? (ratingNode.parentElement?.innerText || '') : '';
          const prices = Array.from(text.matchAll(/RM\s*[0-9,.]+/ig));
          const listNoRaw = card.getAttribute('data-listno') || card.querySelector('[data-listno]')?.getAttribute('data-listno');
          const listNo = listNoRaw == null || listNoRaw === '' ? null : Number(listNoRaw);
          const pagePosition = Number.isFinite(listNo) ? listNo + 1 : fallbackIndex + 1;
          const location = [...lines].reverse().find(line => line !== title
            && !/^(?:sponsored|iklan|ad|advertisement|广告|廣告)$/i.test(line)
            && !/^RM\s*[0-9]/i.test(line)
            && !/(?:sold|terjual|reviews?|ratings|ulasan|penilaian)/i.test(line)
            && !/(?:已售(?:出)?|销量|銷量|^售\s*[0-9])/i.test(line)
            && !/^(?:找相似|相似商品|similar)$/i.test(line)
            && !/^\([0-9,.]+\s*[km千萬万亿億]?\s*\+?\)$/.test(line)
            && !/^[0-5](?:\.[0-9])?$/.test(line)) || null;
          return {href, text, title,
            image: image?.currentSrc || image?.src || null,
            price: prices[0]?.[0] || null,
            original_price: prices[1]?.[0] || null,
            sold: text.match(/[0-9,.]+\s*[km]?\s*\+?\s*(?:sold|terjual)\b/i)?.[0]
              || text.match(/(?:已售(?:出)?|销量|銷量|售)\s*[0-9,.]+\s*(?:[km千萬万亿億])?\s*\+?\s*(?:件)?/i)?.[0] || null,
            rating: ratingText.match(/\b[0-5](?:\.[0-9])?\b/)?.[0]
              || text.match(/(?:rating|rated|bintang)\s*[:\-]?\s*([0-5](?:\.[0-9])?)/i)?.[1] || null,
            reviews: ratingArea.match(/\(([0-9,.]+\s*[km]?\s*\+?)\)/)?.[1]
              || text.match(/([0-9,.]+\s*[km]?\s*\+?)\s*(?:reviews?|ratings|ulasan|penilaian)\b/i)?.[1]
              || text.match(/^\s*\(([0-9,.]+\s*[km千萬万亿億]?\s*\+?)\)\s*$/im)?.[1] || null,
            seller: null, location,
            sponsored: /sponsored|iklan|广告|廣告/i.test(text), item_id: itemId || null,
            shop_id: null, page_position: pagePosition, page_size: pageSize};
          });
        }"""

    def parse_card(self, raw: dict[str, Any], rank: int) -> MarketplaceListing | None:
        href = str(raw.get("href") or "")
        item_id = str(raw.get("item_id") or "")
        if not item_id:
            match = re.search(r"-i(\d+)-|-(\d+)\.html", href, re.I)
            item_id = next((g for g in match.groups() if g), "") if match else ""
        if not item_id:
            return None
        text = str(raw.get("text") or "")
        title = str(raw.get("title") or "").strip()
        if not title:
            lines = [line.strip() for line in text.splitlines() if line.strip()]
            title = next((line for line in lines if not line.lower().startswith("rm")), "")
        if not title:
            return None
        # A Lazada range is a variant price range; separate current/original prices are usually
        # on independent lines and therefore are not matched by parse_money_range().
        price = None if parse_money_range(text) else (
            parse_money(raw.get("price")) if raw.get("price") else parse_money(text, require_currency=True)
        )
        original = parse_money(raw.get("original_price"))
        if original is not None and price is not None and original <= price:
            original = None
        discount = round((original - price) / original * 100, 1) if original and price and original > price else None
        sold_text = raw.get("sold") or _first_match(_SOLD_PATTERNS, text)
        review_text = raw.get("reviews") or _first_match(_REVIEW_PATTERNS, text)
        return MarketplaceListing(
            platform=self.platform,
            item_id=item_id,
            shop_id=str(raw.get("shop_id") or "") or None,
            title=title[:1000],
            product_url=href.split("?")[0],
            image_url=raw.get("image"),
            price=price,
            original_price=original,
            discount_percent=discount,
            sold_count=parse_compact_count(sold_text),
            rating=_safe_rating(raw.get("rating")),
            review_count=parse_compact_count(review_text),
            seller_name=raw.get("seller"),
            seller_location=_clean_location(raw.get("location"), title),
            is_sponsored=bool(raw.get("sponsored")) if raw.get("sponsored") is not None else None,
            search_rank=rank,
            data_quality=_quality(raw),
            raw_data=raw,
        )


ADAPTERS: dict[str, MarketplaceAdapter] = {
    "shopee": ShopeeMalaysiaAdapter(),
    "lazada": LazadaMalaysiaAdapter(),
}
