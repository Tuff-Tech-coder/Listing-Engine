"""Canonical data models.

The whole system has ONE source of truth: a `Product`. You describe a product
once (loosely is fine), the engine generates rich listing content via an LLM,
and each platform adapter renders that content into its own constrained format.
"""

from __future__ import annotations

from dataclasses import dataclass, field, asdict
from typing import Any


@dataclass
class Product:
    """A single thing you sell, described once, channel-agnostic."""

    sku: str                      # your internal id (also used as eBay SKU)
    kind: str                     # "physical" or "book"
    name: str                     # rough working title / what it is
    category_hint: str = ""       # free text, e.g. "wireless earbuds" or "kids coloring book"
    brand: str = ""               # blank for handmade / books
    price: float | None = None
    features: list[str] = field(default_factory=list)   # specs / selling points / book traits
    attributes: dict[str, str] = field(default_factory=dict)  # color, size, genre, trim_size...
    keywords_seed: list[str] = field(default_factory=list)    # optional terms to bias toward
    notes: str = ""               # anything else the LLM should know

    @staticmethod
    def from_dict(d: dict[str, Any]) -> "Product":
        known = {f for f in Product.__dataclass_fields__}  # type: ignore[attr-defined]
        return Product(**{k: v for k, v in d.items() if k in known})


@dataclass
class GeneratedListing:
    """A finished, platform-specific listing ready to push or paste."""

    platform: str
    sku: str
    title: str
    description: str
    keywords: list[str] = field(default_factory=list)     # tags / backend keywords
    bullets: list[str] = field(default_factory=list)
    category: str = ""
    attributes: dict[str, str] = field(default_factory=dict)
    extra: dict[str, Any] = field(default_factory=dict)   # subtitle, html desc, payload preview
    warnings: list[str] = field(default_factory=list)     # validation flags

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)
