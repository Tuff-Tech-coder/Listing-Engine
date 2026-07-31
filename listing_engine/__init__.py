"""Listing Engine — one product description, validated listings for every channel.

Describe a `Product` once; `generate_listings` makes a single LLM generation
call and renders it through per-platform adapters that enforce each
marketplace's real limits.

    from listing_engine import Product, generate_listings

    product = Product(sku="EB-1", kind="physical", name="Wireless Earbuds")
    listings = generate_listings(product, ["ebay", "etsy"])
"""

from .engine import generate_listings
from .models import GeneratedListing, Product
from .platforms import ADAPTERS, EbayAdapter, EtsyAdapter, KdpAdapter

__version__ = "0.1.0"

__all__ = [
    "ADAPTERS",
    "EbayAdapter",
    "EtsyAdapter",
    "GeneratedListing",
    "KdpAdapter",
    "Product",
    "generate_listings",
]
