"""Content generation layer.

ONE generation call produces a superset of copy sized for every platform
(eBay's 80-char keyword title, Etsy's 13 tags, KDP's 7 keyword phrases, etc.).
Adapters then slice + validate. This keeps cost to one LLM call per product.

Three backends:
  - "anthropic": Claude API (set ANTHROPIC_API_KEY). Best quality.
  - "ollama":    local model at localhost:11434 (free, good for high volume).
  - "template":  no LLM at all; deterministic heuristic so the pipeline runs
                 with zero setup. Output is plausibly-shaped, not good copy.
"""

from __future__ import annotations

import json
import os
import random
import textwrap
import time
from datetime import UTC, datetime
from email.utils import parsedate_to_datetime
from html import escape
from typing import Any

from .models import Product

# The structured object every backend must return.
GENERATION_SCHEMA = {
    "ebay_title": "<= 80 chars, keyword-dense, most important terms first, no fluff",
    "etsy_title": "<= 140 chars, readable; pack the best keywords into the first 40 chars",
    "kdp_title": "the title exactly as printed on the book cover; '' if not a book",
    "kdp_subtitle": "the subtitle exactly as printed on the book cover; otherwise ''",
    "description_html": "rich description, simple HTML (<p>,<ul>,<li>,<b>), no scripts",
    "description_plain": "same description as plain text, no tags",
    "bullets": ["up to 5 short benefit-led bullet points"],
    "etsy_tags": ["up to 13 tags, each <= 20 chars, multi-word long-tail phrases"],
    "kdp_keywords": ["exactly 7 search phrases, each <= 50 chars, NONE repeating title words"],
    "ebay_item_specifics": {"Brand": "...", "Type": "...", "Color": "..."},
    "category_suggestions": {
        "ebay": "best-guess eBay category path",
        "etsy": "best-guess Etsy category path",
        "kdp": "up to 3 BISAC-style categories, comma separated",
    },
}


def _build_prompt(product: Product) -> str:
    schema = json.dumps(GENERATION_SCHEMA, indent=2)
    return textwrap.dedent(f"""
    You are an expert marketplace listing copywriter for eBay, Etsy and Amazon KDP.
    Write compelling, accurate, policy-safe listing content for the product below.
    Do not invent specifications that aren't supported by the input. Optimize each
    field for that platform's search behavior. For KDP, title and subtitle are cover
    metadata: copy them exactly and never invent or keyword-stuff them.

    PRODUCT
    -------
    kind: {product.kind}
    name: {product.name}
    category_hint: {product.category_hint}
    brand: {product.brand}
    price: {product.price}
    features: {product.features}
    attributes: {product.attributes}
    keyword seeds: {product.keywords_seed}
    notes: {product.notes}

    Return ONLY a JSON object with exactly these keys (no markdown, no commentary):
    {schema}
    """).strip()


def _extract_json(text: str) -> dict[str, Any]:
    """Pull the first JSON object out of a model response, tolerating fences."""
    decoder = json.JSONDecoder()
    for start, char in enumerate(text):
        if char != "{":
            continue
        try:
            value, _ = decoder.raw_decode(text[start:])
        except json.JSONDecodeError:
            continue
        if isinstance(value, dict):
            return value
    raise ValueError(f"No JSON object found in model output:\n{text[:500]}")


# --- retry policy -----------------------------------------------------------

# Transient statuses worth retrying. 429 is rate limiting, 529 is Anthropic's
# "overloaded" response, and 5xx are server-side faults. Everything else --
# 400 (bad request), 401 (bad key), 404 -- is a caller problem that retrying
# only delays.
_RETRY_STATUSES = frozenset({429, 500, 502, 503, 504, 529})
_MAX_ATTEMPTS = 4
_BACKOFF_BASE_SECONDS = 1.0
_BACKOFF_CAP_SECONDS = 30.0


def _backoff_seconds(resp: Any, attempt: int) -> float:
    """How long to wait before the next attempt.

    Honours the API's Retry-After header when present; otherwise falls back
    to exponential backoff with jitter so concurrent workers don't retry in
    lockstep.
    """
    if resp is not None:
        header = resp.headers.get("retry-after")
        if header:
            try:
                seconds = float(header)
            except ValueError:
                try:
                    retry_at = parsedate_to_datetime(header)
                    if retry_at.tzinfo is None:
                        retry_at = retry_at.replace(tzinfo=UTC)
                    seconds = (retry_at - datetime.now(UTC)).total_seconds()
                except (TypeError, ValueError, OverflowError):
                    pass  # malformed header -- fall through to backoff
                else:
                    return min(max(seconds, 0.0), _BACKOFF_CAP_SECONDS)
            else:
                return min(max(seconds, 0.0), _BACKOFF_CAP_SECONDS)
    delay = _BACKOFF_BASE_SECONDS * (2**attempt)
    return min(delay, _BACKOFF_CAP_SECONDS) + random.uniform(0, 0.5)


def _post_with_retry(url: str, headers: dict[str, str], payload: dict[str, Any],
                     timeout: int) -> Any:
    """POST with bounded exponential backoff on transient failures."""
    import requests  # lazy: only needed for the networked backends

    last_error: Exception | None = None
    for attempt in range(_MAX_ATTEMPTS):
        resp = None
        try:
            resp = requests.post(url, headers=headers, json=payload, timeout=timeout)
        except requests.RequestException as exc:  # DNS, connection reset, timeout
            last_error = exc
        else:
            if resp.status_code not in _RETRY_STATUSES:
                resp.raise_for_status()   # non-transient failures raise here
                return resp
            last_error = requests.HTTPError(
                f"{resp.status_code} from {url}", response=resp
            )

        if attempt < _MAX_ATTEMPTS - 1:
            time.sleep(_backoff_seconds(resp, attempt))

    raise RuntimeError(
        f"{url} still failing after {_MAX_ATTEMPTS} attempts"
    ) from last_error


# --- backends ---------------------------------------------------------------

def _generate_anthropic(product: Product) -> dict[str, Any]:
    key = os.environ.get("ANTHROPIC_API_KEY")
    if not key:
        raise RuntimeError("ANTHROPIC_API_KEY is not set")
    model = os.environ.get("LISTING_LLM_MODEL", "claude-sonnet-5")
    resp = _post_with_retry(
        "https://api.anthropic.com/v1/messages",
        headers={
            "x-api-key": key,
            "anthropic-version": "2023-06-01",
            "content-type": "application/json",
        },
        payload={
            "model": model,
            "max_tokens": 2000,
            "messages": [{"role": "user", "content": _build_prompt(product)}],
        },
        timeout=90,
    )
    parts = [b.get("text", "") for b in resp.json().get("content", []) if b.get("type") == "text"]
    return _extract_json("".join(parts))


def _generate_ollama(product: Product) -> dict[str, Any]:
    import requests  # lazy

    host = os.environ.get("OLLAMA_HOST", "http://localhost:11434")
    model = os.environ.get("LISTING_LLM_MODEL", "llama3.1")
    resp = requests.post(
        f"{host}/api/chat",
        json={
            "model": model,
            "format": "json",
            "stream": False,
            "messages": [{"role": "user", "content": _build_prompt(product)}],
        },
        timeout=180,
    )
    resp.raise_for_status()
    return _extract_json(resp.json()["message"]["content"])


def _generate_template(product: Product) -> dict[str, Any]:
    """Zero-dependency heuristic so the pipeline runs out of the box."""
    base = product.name.strip()
    cat = product.category_hint or product.attributes.get("type", "")
    seeds = product.keywords_seed or [w for w in cat.split() if len(w) > 2]
    is_book = product.kind == "book"

    long_title = " ".join(x for x in [product.brand, base, cat] if x).strip()
    feat_line = "; ".join(product.features) if product.features else cat
    plain_parts = [part.strip().rstrip(".") for part in (base, feat_line) if part.strip()]
    plain_parts.append((product.notes or "Quality you can count on.").strip().rstrip("."))
    plain = ". ".join(plain_parts) + "."
    html = "".join(f"<p>{escape(part)}.</p>" for part in plain_parts)
    if product.features:
        html += "<ul>" + "".join(f"<li>{escape(f)}</li>" for f in product.features) + "</ul>"

    tags = []
    for s in (seeds + base.lower().split()):
        s = s.strip().lower()
        if s and s not in tags:
            tags.append(s)
    tags = tags[:13]

    keyword_candidates = [*seeds, cat, base.lower()]
    kw = []
    for candidate in keyword_candidates:
        candidate = candidate.strip()
        if candidate and candidate.casefold() not in {item.casefold() for item in kw}:
            kw.append(candidate)
    kw = kw[:7]

    return {
        "ebay_title": long_title,
        "etsy_title": long_title,
        "kdp_title": base if is_book else "",
        "kdp_subtitle": product.attributes.get("subtitle", "") if is_book else "",
        "description_html": html,
        "description_plain": plain,
        "bullets": product.features[:5] or ([feat_line] if feat_line else []),
        "etsy_tags": tags,
        "kdp_keywords": kw,
        "ebay_item_specifics": {
            **({"Brand": product.brand} if product.brand else {}),
            **{
                k.title(): v
                for k, v in product.attributes.items()
                if not k.startswith("etsy_") and k != "subtitle"
            },
        },
        "category_suggestions": {"ebay": cat, "etsy": cat, "kdp": cat},
    }


_BACKENDS = {
    "anthropic": _generate_anthropic,
    "ollama": _generate_ollama,
    "template": _generate_template,
}


def generate(product: Product, backend: str | None = None) -> dict[str, Any]:
    backend = backend or os.environ.get("LISTING_BACKEND", "template")
    if backend not in _BACKENDS:
        raise ValueError(f"Unknown backend '{backend}'. Choose: {list(_BACKENDS)}")
    return _BACKENDS[backend](product)
