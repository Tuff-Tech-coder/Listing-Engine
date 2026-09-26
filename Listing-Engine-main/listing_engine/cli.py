"""Command-line runner.

Installed as the `listing-engine` console script (`pip install -e .`).

Examples
--------
# Runs out of the box, no setup (template backend):
listing-engine --file sample_products.json

# Real copy via your local Ollama model:
LISTING_BACKEND=ollama LISTING_LLM_MODEL=llama3.1 listing-engine --file sample_products.json

# Real copy via Claude:
LISTING_BACKEND=anthropic ANTHROPIC_API_KEY=sk-... listing-engine --file sample_products.json
"""

from __future__ import annotations

import argparse
import json
import os
from collections.abc import Sequence
from pathlib import Path

from .engine import generate_listings
from .models import Product


def _print(listing) -> None:
    print(f"\n{'='*70}\n[{listing.platform.upper()}]  sku={listing.sku}\n{'='*70}")
    print(f"TITLE: {listing.title}")
    if listing.platform == "kdp":
        print("\n" + listing.extra["paste_sheet"])
    else:
        print(f"CATEGORY: {listing.category}")
        if listing.keywords:
            print(f"TAGS ({len(listing.keywords)}): {', '.join(listing.keywords)}")
        if listing.attributes:
            print(f"ITEM SPECIFICS: {listing.attributes}")
        print(f"DESCRIPTION:\n{listing.description}")
    if listing.warnings:
        print("\nWARNINGS:")
        for warn in listing.warnings:
            print(f"  ! {warn}")


def main(argv: Sequence[str] | None = None) -> None:
    ap = argparse.ArgumentParser(description="Multi-channel listing generator")
    ap.add_argument("--file", required=True, help="JSON file: a product or list of products")
    ap.add_argument(
        "--platforms",
        help="comma-separated; defaults to eBay/Etsy for physical items and KDP for books",
    )
    ap.add_argument("--backend", default=os.environ.get("LISTING_BACKEND", "template"),
                    choices=["template", "ollama", "anthropic"])
    ap.add_argument("--json-out", help="write all listings to this JSON file")
    args = ap.parse_args(argv)

    try:
        with open(args.file, encoding="utf-8") as fh:
            data = json.load(fh)
    except (OSError, json.JSONDecodeError) as exc:
        ap.error(f"could not read product JSON: {exc}")

    records = data if isinstance(data, list) else [data]
    if not records:
        ap.error("product JSON must contain at least one product")

    products: list[Product] = []
    for index, record in enumerate(records, start=1):
        if not isinstance(record, dict):
            ap.error(f"product {index} must be a JSON object")
        try:
            product = Product.from_dict(record)
        except TypeError as exc:
            ap.error(f"product {index} is invalid: {exc}")
        for field in ("sku", "kind", "name"):
            value = getattr(product, field)
            if not isinstance(value, str) or not value.strip():
                ap.error(f"product {index} field '{field}' must be a non-empty string")
        products.append(product)

    skus = [product.sku for product in products]
    duplicate_skus = sorted({sku for sku in skus if skus.count(sku) > 1})
    if duplicate_skus:
        ap.error(f"duplicate SKU(s) would overwrite JSON output: {duplicate_skus}")

    platforms = None
    if args.platforms is not None:
        platforms = [p.strip().casefold() for p in args.platforms.split(",") if p.strip()]
        if not platforms:
            ap.error("--platforms must name at least one platform")

    out: dict[str, dict] = {}
    for product in products:
        try:
            listings = generate_listings(product, platforms, backend=args.backend)
        except ValueError as exc:
            ap.error(str(exc))
        out[product.sku] = {p: listing.to_dict() for p, listing in listings.items()}
        for listing in listings.values():
            _print(listing)

    if args.json_out:
        destination = Path(args.json_out)
        destination.parent.mkdir(parents=True, exist_ok=True)
        with destination.open("w", encoding="utf-8") as fh:
            json.dump(out, fh, indent=2)
        print(f"\nWrote {destination}")


if __name__ == "__main__":
    main()
