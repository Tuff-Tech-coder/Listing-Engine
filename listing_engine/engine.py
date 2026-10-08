"""Orchestration: turn a Product into finished listings for chosen platforms."""

from __future__ import annotations

from . import llm
from .models import GeneratedListing, Product
from .platforms import ADAPTERS

PLATFORMS_BY_KIND = {
    "physical": ("ebay", "etsy"),
    "book": ("kdp",),
}


def generate_listings(
    product: Product,
    platforms: list[str] | None = None,
    backend: str | None = None,
) -> dict[str, GeneratedListing]:
    """One LLM generation, rendered for each requested platform."""
    kind = product.kind.strip().casefold()
    if kind not in PLATFORMS_BY_KIND:
        raise ValueError(
            f"Unknown product kind '{product.kind}'. Choose: {list(PLATFORMS_BY_KIND)}"
        )

    if platforms is None:
        platforms = list(PLATFORMS_BY_KIND[kind])
    elif not platforms:
        raise ValueError("At least one platform must be selected.")

    unknown = [p for p in platforms if p not in ADAPTERS]
    if unknown:
        raise ValueError(f"Unknown platform(s): {unknown}. Have: {list(ADAPTERS)}")

    incompatible = [p for p in platforms if p not in PLATFORMS_BY_KIND[kind]]
    if incompatible:
        raise ValueError(
            f"Platform(s) {incompatible} are incompatible with product kind '{kind}'. "
            f"Choose: {list(PLATFORMS_BY_KIND[kind])}"
        )

    gen = llm.generate(product, backend=backend)   # the single generation call
    return {p: ADAPTERS[p].render(product, gen) for p in platforms}
