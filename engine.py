"""Orchestration: turn a Product into finished listings for chosen platforms."""

from __future__ import annotations

from models import GeneratedListing, Product
from platforms import ADAPTERS
import llm


def generate_listings(
    product: Product,
    platforms: list[str] | None = None,
    backend: str | None = None,
) -> dict[str, GeneratedListing]:
    """One LLM generation, rendered for each requested platform."""
    platforms = platforms or list(ADAPTERS)
    unknown = [p for p in platforms if p not in ADAPTERS]
    if unknown:
        raise ValueError(f"Unknown platform(s): {unknown}. Have: {list(ADAPTERS)}")

    gen = llm.generate(product, backend=backend)   # the single generation call
    return {p: ADAPTERS[p].render(product, gen) for p in platforms}
