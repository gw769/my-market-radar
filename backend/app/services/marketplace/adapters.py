from __future__ import annotations

import re
from dataclasses import asdict, dataclass
from typing import Any, Iterable
from urllib.parse import quote, urlparse


class VerificationRequired(RuntimeError):
    def __init__(self, platform: str, url: str):
        super().__init__(f"{platform} 需要人工验证")
        self.platform = platform
        self.url = url


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


def parse_money(value: Any) -> float | None:
    if value is None:
        return None
    text = str(value).replace(",", "")
    match = re.search(r"RM\s*([0-9]+(?:\.[0-9]{1,2})?)", text, re.I)
    if not match:
        match = re.search(r"([0-9]+(?:\.[0-9]{1,2})?)", text)
    return float(match.group(1)) if match else None


def parse_compact_count(value: Any) -> int | None:
    if value is None:
        return None
    text = str(value).strip().lower().replace(",", "")
    match = re.search(r"([0-9]+(?:\.[0-9]+)?)\s*([km]?)", text)
    if not match:
        return None
    number = float(match.group(1))
    multiplier = {"": 1, "k": 1_000, "m": 1_000_000}[match.group(2)]
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


class MarketplaceAdapter:
    platform: str
    blocked_hosts: tuple[str, ...] = ()

    def search_url(self, keyword: str) -> str:
        raise NotImplementedError

    @property
    def extraction_script(self) -> str:
        raise NotImplementedError

    def is_verification_page(self, url: str, body_text: str = "") -> bool:
        parsed = urlparse(url)
        haystack = f"{parsed.netloc} {parsed.path} {body_text[:1200]}".lower()
        signals = (
            "captcha", "verify", "verification", "punish", "robot check",
            "security check", "unusual traffic", "suspicious activity",
        )
        return any(host in parsed.netloc.lower() for host in self.blocked_hosts) or any(s in haystack for s in signals)

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

    def search_url(self, keyword: str) -> str:
        return f"https://shopee.com.my/search?keyword={quote(keyword)}"

    @property
    def extraction_script(self) -> str:
        return r"""() => Array.from(document.querySelectorAll('a[href*="-i."]')).map(a => {
          const card = a.closest('[data-sqe="item"]') || a.closest('li') || a.parentElement?.parentElement;
          const text = (card?.innerText || a.innerText || '').trim();
          const image = card?.querySelector('img');
          const href = a.href;
          const id = href.match(/-i\.(\d+)\.(\d+)/);
          const price = text.match(/RM\s*[0-9,.]+/i)?.[0] || null;
          const sold = text.match(/[0-9,.]+\s*[km]?\s*(?:sold|terjual)/i)?.[0] || null;
          const rating = text.match(/\b[0-5]\.[0-9]\b/)?.[0] || null;
          return {href, text, title: a.getAttribute('aria-label') || a.title || text.split('\n')[0],
            image: image?.currentSrc || image?.src || null, price, sold, rating,
            reviews: null, seller: null, location: text.split('\n').slice(-1)[0] || null,
            sponsored: /sponsored|iklan/i.test(text), shop_id: id?.[1] || null, item_id: id?.[2] || null};
        })"""

    def parse_card(self, raw: dict[str, Any], rank: int) -> MarketplaceListing | None:
        href = str(raw.get("href") or "")
        id_match = re.search(r"-i\.(\d+)\.(\d+)", href)
        item_id = str(raw.get("item_id") or (id_match.group(2) if id_match else ""))
        if not item_id:
            return None
        text = str(raw.get("text") or "")
        title = str(raw.get("title") or "").strip()
        if not title or title.lower().startswith("rm"):
            lines = [line.strip() for line in text.splitlines() if line.strip()]
            title = next((line for line in lines if not re.match(r"^(RM|[0-9.]+\s*(sold|terjual))", line, re.I)), "")
        if not title:
            return None
        sold_text = raw.get("sold") or _first_match((r"([0-9,.]+\s*[km]?)\s*(?:sold|terjual)",), text)
        rating_text = raw.get("rating") or _first_match((r"\b([0-5]\.[0-9])\b",), text)
        return MarketplaceListing(
            platform=self.platform,
            item_id=item_id,
            shop_id=str(raw.get("shop_id") or (id_match.group(1) if id_match else "")) or None,
            title=title[:1000],
            product_url=href.split("?")[0],
            image_url=raw.get("image"),
            price=parse_money(raw.get("price") or text),
            sold_count=parse_compact_count(sold_text),
            rating=float(rating_text) if rating_text else None,
            review_count=parse_compact_count(raw.get("reviews")),
            seller_name=raw.get("seller"),
            seller_location=raw.get("location"),
            is_sponsored=bool(raw.get("sponsored")) if raw.get("sponsored") is not None else None,
            search_rank=rank,
            data_quality=_quality(raw),
            raw_data=raw,
        )


class LazadaMalaysiaAdapter(MarketplaceAdapter):
    platform = "lazada"
    blocked_hosts = ("acs-m.lazada.com.my",)

    def search_url(self, keyword: str) -> str:
        return f"https://www.lazada.com.my/catalog/?q={quote(keyword)}"

    @property
    def extraction_script(self) -> str:
        return r"""() => Array.from(document.querySelectorAll('a[href*="/products/"]')).map(a => {
          const card = a.closest('[data-item-id]') || a.closest('[class*="Bm3ON"]') || a.closest('div[data-qa-locator="product-item"]') || a.parentElement?.parentElement;
          const text = (card?.innerText || a.innerText || '').trim();
          const image = card?.querySelector('img');
          const href = a.href;
          const itemId = card?.getAttribute('data-item-id') || href.match(/-i(\d+)-/i)?.[1] || href.match(/\/products\/[^/]*-(\d+)\.html/i)?.[1];
          return {href, text, title: a.title || a.getAttribute('aria-label') || text.split('\n')[0],
            image: image?.currentSrc || image?.src || null,
            price: text.match(/RM\s*[0-9,.]+/i)?.[0] || null,
            original_price: Array.from(text.matchAll(/RM\s*[0-9,.]+/ig))[1]?.[0] || null,
            sold: text.match(/[0-9,.]+\s*[km]?\s*(?:sold|terjual)/i)?.[0] || null,
            rating: text.match(/\b[0-5]\.[0-9]\b/)?.[0] || null,
            reviews: text.match(/\(([0-9,.]+\s*[km]?)\)/)?.[1] || null,
            seller: null, location: text.split('\n').slice(-1)[0] || null,
            sponsored: /sponsored|iklan/i.test(text), item_id: itemId || null, shop_id: null};
        })"""

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
        price = parse_money(raw.get("price") or text)
        original = parse_money(raw.get("original_price"))
        discount = round((original - price) / original * 100, 1) if original and price and original > price else None
        sold_text = raw.get("sold") or _first_match((r"([0-9,.]+\s*[km]?)\s*(?:sold|terjual)",), text)
        rating_text = raw.get("rating") or _first_match((r"\b([0-5]\.[0-9])\b",), text)
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
            rating=float(rating_text) if rating_text else None,
            review_count=parse_compact_count(raw.get("reviews")),
            seller_name=raw.get("seller"),
            seller_location=raw.get("location"),
            is_sponsored=bool(raw.get("sponsored")) if raw.get("sponsored") is not None else None,
            search_rank=rank,
            data_quality=_quality(raw),
            raw_data=raw,
        )


ADAPTERS: dict[str, MarketplaceAdapter] = {
    "shopee": ShopeeMalaysiaAdapter(),
    "lazada": LazadaMalaysiaAdapter(),
}
