"""Platform adapters.

Each adapter takes the canonical generation dict (from llm.generate) and:
  1. renders it into a GeneratedListing using that platform's fields,
  2. validates against the platform's REAL hard limits (truncating + warning),
  3. produces what you actually do with it:
       - eBay / Etsy: a ready-to-send API payload (push() is stubbed for creds),
       - KDP:         a paste-ready metadata sheet (no API exists).

The constraints below are the load-bearing part — they encode each platform's
actual limits and SEO conventions so generated copy doesn't get rejected.
"""

from __future__ import annotations

from typing import Any

from .models import GeneratedListing, Product


def _truncate(text: str, limit: int, warnings: list[str], label: str) -> str:
    if len(text) > limit:
        warnings.append(f"{label} exceeded {limit} chars ({len(text)}) — truncated.")
        return text[:limit].rstrip()
    return text


# --- eBay -------------------------------------------------------------------

class EbayAdapter:
    name = "ebay"
    TITLE_MAX = 80          # hard eBay limit
    MARKETPLACE = "EBAY_US"

    def render(self, product: Product, gen: dict[str, Any]) -> GeneratedListing:
        w: list[str] = []
        title = _truncate(gen.get("ebay_title", product.name), self.TITLE_MAX, w, "eBay title")
        specifics = gen.get("ebay_item_specifics", {}) or {}
        if not specifics:
            w.append("No item specifics — eBay search heavily weights these; add some.")

        listing = GeneratedListing(
            platform=self.name,
            sku=product.sku,
            title=title,
            description=gen.get("description_html", gen.get("description_plain", "")),
            keywords=[],  # eBay has no tags; keywords live in title + specifics
            bullets=gen.get("bullets", []),
            category=gen.get("category_suggestions", {}).get("ebay", ""),
            attributes=specifics,
            warnings=w,
        )
        listing.extra["api_payload"] = self._payload(product, listing)
        return listing

    def _payload(self, product: Product, listing: GeneratedListing) -> dict[str, Any]:
        # Shape mirrors the eBay Sell Inventory API (createOrReplaceInventoryItem
        # + createOffer). Drop in OAuth + publishOffer to go live.
        return {
            "inventory_item": {
                "sku": product.sku,
                "product": {
                    "title": listing.title,
                    "description": listing.description,
                    "aspects": {k: [v] for k, v in listing.attributes.items()},
                },
                "availability": {
                    "shipToLocationAvailability": {"quantity": 1}
                },
            },
            "offer": {
                "sku": product.sku,
                "marketplaceId": self.MARKETPLACE,
                "format": "FIXED_PRICE",
                "categoryHint": listing.category,
                "pricingSummary": {
                    "price": {"value": str(product.price or 0), "currency": "USD"}
                },
            },
        }

    def push(self, listing: GeneratedListing, creds: dict[str, str] | None = None):
        raise NotImplementedError(
            "Wire eBay OAuth here: createOrReplaceInventoryItem -> createOffer -> "
            "publishOffer. listing.extra['api_payload'] is ready to send."
        )


# --- Etsy -------------------------------------------------------------------

class EtsyAdapter:
    name = "etsy"
    TITLE_MAX = 140
    TAG_MAX_COUNT = 13
    TAG_MAX_LEN = 20

    def render(self, product: Product, gen: dict[str, Any]) -> GeneratedListing:
        w: list[str] = []
        title = _truncate(gen.get("etsy_title", product.name), self.TITLE_MAX, w, "Etsy title")

        tags: list[str] = []
        for t in gen.get("etsy_tags", []):
            t = str(t).strip()
            if len(t) > self.TAG_MAX_LEN:
                w.append(f"Tag '{t}' > {self.TAG_MAX_LEN} chars — dropped.")
                continue
            if t and t not in tags:
                tags.append(t)
        if len(tags) > self.TAG_MAX_COUNT:
            w.append(f"More than {self.TAG_MAX_COUNT} tags — kept first {self.TAG_MAX_COUNT}.")
            tags = tags[: self.TAG_MAX_COUNT]
        if len(tags) < self.TAG_MAX_COUNT:
            w.append(
                f"Only {len(tags)}/{self.TAG_MAX_COUNT} tags used — "
                f"Etsy SEO rewards using all {self.TAG_MAX_COUNT}."
            )

        listing = GeneratedListing(
            platform=self.name,
            sku=product.sku,
            title=title,
            description=gen.get("description_plain", ""),
            keywords=tags,
            bullets=gen.get("bullets", []),
            category=gen.get("category_suggestions", {}).get("etsy", ""),
            warnings=w,
        )
        listing.extra["api_payload"] = {
            # Mirrors Etsy API v3 createDraftListing (then uploadListingImage + publish).
            "quantity": 1,
            "title": title,
            "description": listing.description,
            "price": product.price or 0,
            "who_made": "i_did",
            "when_made": "made_to_order",
            "taxonomy_id": None,  # map listing.category -> Etsy taxonomy id
            "tags": tags,
            "state": "draft",
        }
        return listing

    def push(self, listing: GeneratedListing, creds: dict[str, str] | None = None):
        raise NotImplementedError(
            "Wire Etsy API v3 OAuth here: createDraftListing -> uploadListingImage -> "
            "updateListing(state='active'). listing.extra['api_payload'] is ready."
        )


# --- Amazon KDP (no API) ----------------------------------------------------

class KdpAdapter:
    name = "kdp"
    KEYWORD_SLOTS = 7
    KEYWORD_MAX_LEN = 50
    CATEGORY_SLOTS = 3   # you pick 3; the 2026 algorithm assigns the rest by metadata

    def render(self, product: Product, gen: dict[str, Any]) -> GeneratedListing:
        w: list[str] = []
        title = gen.get("kdp_title") or product.name
        subtitle = gen.get("kdp_subtitle", "")
        if not subtitle:
            w.append("No subtitle — KDP's subtitle is your single biggest keyword slot.")

        title_words = {x.lower() for x in (title + " " + subtitle).split()}

        # Normalise and cap to the slot count BEFORE validating. Validating
        # first meant warning about keywords that the cap then discarded —
        # noise about copy that never reaches the listing.
        kws: list[str] = []
        for raw in gen.get("kdp_keywords", []):
            k = str(raw).strip()[: self.KEYWORD_MAX_LEN]
            if k:
                kws.append(k)
        kws = kws[: self.KEYWORD_SLOTS]

        for k in kws:
            if any(word in title_words for word in k.lower().split()):
                w.append(f"Keyword '{k}' repeats title/subtitle words — wastes a slot.")
        if len(kws) < self.KEYWORD_SLOTS:
            w.append(f"Only {len(kws)}/{self.KEYWORD_SLOTS} keyword slots filled.")

        cats = gen.get("category_suggestions", {}).get("kdp", "")

        listing = GeneratedListing(
            platform=self.name,
            sku=product.sku,
            title=title,
            description=gen.get("description_html", gen.get("description_plain", "")),
            keywords=kws,
            category=cats,
            warnings=w,
        )
        listing.extra["subtitle"] = subtitle
        listing.extra["paste_sheet"] = self._sheet(title, subtitle, listing, cats)
        return listing

    def _sheet(self, title, subtitle, listing: GeneratedListing, cats) -> str:
        lines = [
            "=== KDP METADATA (paste into the KDP form) ===",
            f"Title:     {title}",
            f"Subtitle:  {subtitle}",
            "",
            "Description (paste into the description box):",
            listing.description,
            "",
            "7 Keywords (one per box):",
        ]
        for i in range(self.KEYWORD_SLOTS):
            lines.append(f"  {i+1}. {listing.keywords[i] if i < len(listing.keywords) else ''}")
        lines += ["", f"Categories (pick up to {self.CATEGORY_SLOTS}): {cats}"]
        return "\n".join(lines)

    def push(self, listing: GeneratedListing, creds: dict[str, str] | None = None):
        raise NotImplementedError(
            "KDP has no public API. Use listing.extra['paste_sheet'] to fill the form "
            "by hand. (Browser automation exists but violates KDP terms — account risk.)"
        )


ADAPTERS = {a.name: a for a in (EbayAdapter(), EtsyAdapter(), KdpAdapter())}
