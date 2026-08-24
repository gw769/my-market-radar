from __future__ import annotations

import json
import math
import re
from datetime import datetime, timezone
from typing import Any, Iterable
from urllib.parse import urlparse

import httpx

from app.core.config import get_settings
from app.core.logging import logger
from app.services.marketplace.query_localization import deterministic_localization


class MarketplaceAIError(RuntimeError):
    pass


_GENERIC_SEARCH_TERMS = {
    "goods", "item", "items", "merchandise", "product", "products",
    "barangan", "produk", "商品", "产品", "產品",
}
_CONTROL_RE = re.compile(r"[\x00-\x1f\x7f]")
_NUMBER_RE = re.compile(r"(?<![\w.])\d+(?:\.\d+)?%?")


def ai_status() -> dict[str, Any]:
    settings = get_settings()
    configured = bool(
        settings.LLM_PROVIDER.strip()
        and settings.LLM_API_KEY.strip()
        and settings.LLM_BASE_URL.strip()
        and settings.LLM_MODEL.strip()
    )
    parsed = urlparse(settings.LLM_BASE_URL.strip()) if settings.LLM_BASE_URL.strip() else None
    return {
        "enabled": configured,
        "provider": settings.LLM_PROVIDER.strip() or None,
        "model": settings.LLM_MODEL.strip() or None,
        "endpoint_host": parsed.hostname if parsed else None,
        "uses_structured_output": True,
        "score_authority": "deterministic_rules",
    }


def _endpoint(path: str) -> str:
    settings = get_settings()
    base = settings.LLM_BASE_URL.strip().rstrip("/")
    parsed = urlparse(base)
    if parsed.scheme != "https" or not parsed.hostname or parsed.username or parsed.password:
        raise MarketplaceAIError("LLM_BASE_URL 必须是无账号信息的 HTTPS 地址")
    suffix = path if path.startswith("/") else f"/{path}"
    if base.endswith("/v1") and suffix.startswith("/v1/"):
        suffix = suffix[3:]
    return f"{base}{suffix}"


def _chat_json(
    *,
    system: str,
    user: str,
    schema_name: str,
    schema: dict[str, Any],
    max_completion_tokens: int,
) -> dict[str, Any]:
    settings = get_settings()
    status = ai_status()
    if not status["enabled"]:
        raise MarketplaceAIError("AI 尚未配置")

    payload = {
        "model": settings.LLM_MODEL.strip(),
        "store": False,
        "reasoning_effort": settings.LLM_REASONING_EFFORT.strip() or "low",
        "max_completion_tokens": max_completion_tokens,
        "messages": [
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ],
        "response_format": {
            "type": "json_schema",
            "json_schema": {
                "name": schema_name,
                "strict": True,
                "schema": schema,
            },
        },
    }
    headers = {
        "Authorization": f"Bearer {settings.LLM_API_KEY.strip()}",
        "Content-Type": "application/json",
    }
    timeout = httpx.Timeout(settings.LLM_TIMEOUT_SECONDS, connect=min(8.0, settings.LLM_TIMEOUT_SECONDS))
    try:
        with httpx.Client(timeout=timeout, follow_redirects=False) as client:
            response = client.post(_endpoint("/v1/chat/completions"), headers=headers, json=payload)
    except httpx.TimeoutException as exc:
        raise MarketplaceAIError("AI 请求超时") from exc
    except httpx.HTTPError as exc:
        raise MarketplaceAIError("AI 服务连接失败") from exc

    if response.status_code != 200:
        logger.warning("AI 服务返回非 200: status=%s", response.status_code)
        raise MarketplaceAIError(f"AI 服务返回 HTTP {response.status_code}")
    try:
        body = response.json()
        choice = (body.get("choices") or [])[0]
        message = choice.get("message") or {}
        if message.get("refusal"):
            raise MarketplaceAIError("AI 拒绝处理本次请求")
        content = message.get("content")
        if not isinstance(content, str) or not content.strip():
            raise MarketplaceAIError("AI 没有返回结构化内容")
        result = json.loads(content)
    except MarketplaceAIError:
        raise
    except (IndexError, TypeError, ValueError, json.JSONDecodeError) as exc:
        raise MarketplaceAIError("AI 返回格式不符合约定") from exc
    if not isinstance(result, dict):
        raise MarketplaceAIError("AI 返回格式不符合约定")
    return result


def _clean_phrase(value: Any) -> str:
    phrase = " ".join(str(value or "").split()).strip().casefold()
    if not phrase or len(phrase) > 100 or _CONTROL_RE.search(phrase):
        return ""
    parsed = urlparse(phrase)
    if parsed.scheme or parsed.netloc or "/" in phrase or "\\" in phrase:
        return ""
    return phrase


def _unique_texts(values: Iterable[Any], *, limit: int, max_length: int) -> list[str]:
    result: list[str] = []
    for value in values:
        text = " ".join(str(value or "").split()).strip()
        if not text or len(text) > max_length or _CONTROL_RE.search(text) or text in result:
            continue
        result.append(text)
        if len(result) >= limit:
            break
    return result


def translate_keyword(keyword: str) -> dict[str, Any]:
    source = " ".join(str(keyword or "").split()).strip()
    if len(source) < 2 or len(source) > 200 or _CONTROL_RE.search(source):
        raise MarketplaceAIError("关键词长度或字符不合法")

    deterministic = deterministic_localization(source)
    if deterministic:
        return deterministic

    schema = {
        "type": "object",
        "properties": {
            "search_term": {"type": "string"},
            "aliases": {
                "type": "array",
                "items": {"type": "string"},
                "maxItems": 8,
            },
        },
        "required": ["search_term", "aliases"],
        "additionalProperties": False,
    }
    result = _chat_json(
        system=(
            "You are a conservative Malaysia ecommerce taxonomy translator. "
            "Translate the user's product keyword into the narrowest English search noun phrase "
            "for Shopee Malaysia and Lazada Malaysia. Return exact product synonyms only. "
            "Aliases may include English, Bahasa Melayu, and the source language. Never add "
            "attributes, audiences, sizes, bundles, accessories, replacement parts, use cases, "
            "or adjacent product categories. If ambiguous, preserve the literal narrow meaning."
        ),
        user=f"Product keyword: {source}\nMarket: Malaysia\nReturn a search term and exact aliases only.",
        schema_name="marketplace_keyword_localization",
        schema=schema,
        max_completion_tokens=320,
    )
    search_term = _clean_phrase(result.get("search_term"))
    if not search_term or search_term in _GENERIC_SEARCH_TERMS:
        raise MarketplaceAIError("AI 翻译结果过于宽泛")
    aliases = []
    for value in (result.get("aliases") or []):
        phrase = _clean_phrase(value)
        if phrase and phrase not in _GENERIC_SEARCH_TERMS and phrase not in aliases:
            aliases.append(phrase)
    aliases = list(dict.fromkeys((search_term, *aliases)))[:8]
    return {
        "keyword": source.casefold(),
        "search_term": search_term,
        "aliases": aliases,
        "source": "ai",
        "model": get_settings().LLM_MODEL.strip(),
    }


def _compact_number(value: Any) -> float | int | None:
    if not isinstance(value, (int, float)) or isinstance(value, bool) or not math.isfinite(float(value)):
        return None
    return int(value) if float(value).is_integer() else round(float(value), 2)


def _insight_evidence(analysis: dict[str, Any]) -> dict[str, Any]:
    platforms: dict[str, Any] = {}
    for name, value in (analysis.get("platform_scores") or {}).items():
        if not isinstance(value, dict):
            continue
        platforms[str(name)] = {
            "score": _compact_number(value.get("score")),
            "verdict": value.get("verdict"),
            "confidence": _compact_number(value.get("confidence")),
            "sample_size": _compact_number(value.get("sample_size")),
            "dimensions": {
                key: _compact_number(score)
                for key, score in (value.get("dimensions") or {}).items()
            },
            "metrics": {
                key: _compact_number((value.get("metrics") or {}).get(key))
                for key in (
                    "median_price", "median_sold", "median_reviews", "average_rating",
                    "sponsored_share", "seller_concentration",
                )
            },
        }
    segments = [
        {
            "label": item.get("label"),
            "score": _compact_number(item.get("opportunity_score")),
            "sample_size": _compact_number(item.get("sample_size")),
            "median_price": _compact_number(item.get("median_price")),
        }
        for item in (analysis.get("opportunity_segments") or [])[:3]
        if isinstance(item, dict)
    ]
    evidence = analysis.get("evidence") if isinstance(analysis.get("evidence"), dict) else {}
    return {
        "keyword": analysis.get("keyword"),
        "opportunity_score": _compact_number(analysis.get("opportunity_score")),
        "verdict": analysis.get("verdict"),
        "confidence": _compact_number(analysis.get("confidence")),
        "evidence_grade": evidence.get("grade"),
        "sample_total": _compact_number(evidence.get("sample_total")),
        "platforms": platforms,
        "top_segments": segments,
    }


def _unsupported_numbers(texts: Iterable[str], evidence: dict[str, Any]) -> list[str]:
    source_numbers = set(_NUMBER_RE.findall(json.dumps(evidence, ensure_ascii=False)))
    claimed = set(_NUMBER_RE.findall(" ".join(texts)))
    return sorted(number for number in claimed if number not in source_numbers)


def generate_market_insights(analysis: dict[str, Any]) -> dict[str, Any]:
    evidence = _insight_evidence(analysis)
    schema = {
        "type": "object",
        "properties": {
            "summary": {"type": "string"},
            "findings": {"type": "array", "items": {"type": "string"}, "maxItems": 3},
            "risks": {"type": "array", "items": {"type": "string"}, "maxItems": 3},
            "actions": {"type": "array", "items": {"type": "string"}, "maxItems": 3},
        },
        "required": ["summary", "findings", "risks", "actions"],
        "additionalProperties": False,
    }
    result = _chat_json(
        system=(
            "You are a cautious Malaysia ecommerce analyst. Explain only the supplied public-data "
            "evidence in concise Simplified Chinese. The deterministic score and verdict are final: "
            "never change them. Never invent sales, profit, cost, conversion, demand, market share, "
            "or numeric claims. Do not promise profitability. Give testable actions, and explicitly "
            "say evidence is insufficient when the verdict says so."
        ),
        user=(
            "Interpret this bounded evidence JSON. Every numeric claim must already appear in it.\n"
            + json.dumps(evidence, ensure_ascii=False, separators=(",", ":"))
        ),
        schema_name="marketplace_ai_insight",
        schema=schema,
        max_completion_tokens=900,
    )
    summary = _unique_texts([result.get("summary")], limit=1, max_length=360)
    findings = _unique_texts(result.get("findings") or [], limit=3, max_length=260)
    risks = _unique_texts(result.get("risks") or [], limit=3, max_length=260)
    actions = _unique_texts(result.get("actions") or [], limit=3, max_length=260)
    texts = [*summary, *findings, *risks, *actions]
    if not summary or not actions:
        raise MarketplaceAIError("AI 分析内容不完整")
    unsupported = _unsupported_numbers(texts, evidence)
    if unsupported:
        raise MarketplaceAIError("AI 分析包含原始证据之外的数字")
    return {
        "status": "completed",
        "model": get_settings().LLM_MODEL.strip(),
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "summary": summary[0],
        "findings": findings,
        "risks": risks,
        "actions": actions,
        "score_changed": False,
        "evidence_scope": "aggregated_public_fields_only",
    }
