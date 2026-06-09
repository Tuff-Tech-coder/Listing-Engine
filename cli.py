"""Command-line runner.

Examples
--------
# Runs out of the box, no setup (template backend):
python cli.py --file sample_products.json --platforms ebay,etsy,kdp

# Real copy via your local Ollama model:
LISTING_BACKEND=ollama LISTING_LLM_MODEL=llama3.1 python cli.py --file sample_products.json

# Real copy via Claude:
LISTING_BACKEND=anthropic ANTHROPIC_API_KEY=sk-... python cli.py --file sample_products.json
"""

from __future__ import annotations

import argparse
import json
import os

from engine import generate_listings
from models import Product


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


def main() -> None:
    ap = argparse.ArgumentParser(description="Multi-channel listing generator")
    ap.add_argument("--file", required=True, help="JSON file: a product or list of products")
    ap.add_argument("--platforms", default="ebay,etsy,kdp", help="comma-separated")
    ap.add_argument("--backend", default=os.environ.get("LISTING_BACKEND", "template"),
                    choices=["template", "ollama", "anthropic"])
    ap.add_argument("--json-out", help="write all listings to this JSON file")
    args = ap.parse_args()

    data = json.load(open(args.file))
    products = [Product.from_dict(d) for d in (data if isinstance(data, list) else [data])]
    platforms = [p.strip() for p in args.platforms.split(",") if p.strip()]

    out: dict[str, dict] = {}
    for product in products:
        listings = generate_listings(product, platforms, backend=args.backend)
        out[product.sku] = {p: l.to_dict() for p, l in listings.items()}
        for listing in listings.values():
            _print(listing)

    if args.json_out:
        json.dump(out, open(args.json_out, "w"), indent=2)
        print(f"\nWrote {args.json_out}")


if __name__ == "__main__":
    main()
