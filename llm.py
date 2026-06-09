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
import textwrap
from typing import Any

from models import Product

# The structured object every backend must return.
GENERATION_SCHEMA = {
    "ebay_title": "<= 80 chars, keyword-dense, most important terms first, no fluff",
    "etsy_title": "<= 140 chars, readable; pack the best keywords into the first 40 chars",
    "kdp_title": "the book's main title",
    "kdp_subtitle": "keyword-rich subtitle (KDP's biggest free SEO slot); '' if not a book",
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
    field for that platform's search behavior.

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
    t = text.strip()
    if t.startswith("```"):
        t = t.split("```", 2)[1]
        t = t[4:] if t.lstrip().lower().startswith("json") else t
    start, end = t.find("{"), t.rfind("}")
    if start == -1 or end == -1:
        raise ValueError(f"No JSON object found in model output:\n{text[:500]}")
    return json.loads(t[start : end + 1])


# --- backends ---------------------------------------------------------------

def _generate_anthropic(product: Product) -> dict[str, Any]:
    import requests  # lazy: only needed for this backend

    key = os.environ.get("ANTHROPIC_API_KEY")
    if not key:
        raise RuntimeError("ANTHROPIC_API_KEY is not set")
    model = os.environ.get("LISTING_LLM_MODEL", "claude-sonnet-4-6")
    resp = requests.post(
        "https://api.anthropic.com/v1/messages",
        headers={
            "x-api-key": key,
            "anthropic-version": "2023-06-01",
            "content-type": "application/json",
        },
        json={
            "model": model,
            "max_tokens": 2000,
            "messages": [{"role": "user", "content": _build_prompt(product)}],
        },
        timeout=90,
    )
    resp.raise_for_status()
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
    plain = (
        f"{base}. {feat_line}. "
        + (product.notes or "Quality you can count on.")
    ).strip()
    html = "<p>" + plain.replace(". ", ".</p><p>") + "</p>"
    if product.features:
        html += "<ul>" + "".join(f"<li>{f}</li>" for f in product.features) + "</ul>"

    tags = []
    for s in (seeds + base.lower().split()):
        s = s.strip().lower()
        if s and s not in tags:
            tags.append(s[:20])
    tags = tags[:13]

    kw = [f"{cat} {s}".strip()[:50] for s in (seeds[:7] or [base.lower()])][:7]

    return {
        "ebay_title": long_title[:80],
        "etsy_title": long_title[:140],
        "kdp_title": base if is_book else "",
        "kdp_subtitle": (cat + " " + " ".join(seeds[:4]))[:120] if is_book else "",
        "description_html": html,
        "description_plain": plain,
        "bullets": product.features[:5] or [feat_line],
        "etsy_tags": tags,
        "kdp_keywords": kw,
        "ebay_item_specifics": {
            **({"Brand": product.brand} if product.brand else {}),
            **{k.title(): v for k, v in product.attributes.items()},
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
