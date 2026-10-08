"""Platform adapters.

Each adapter takes the canonical generation dict (from llm.generate) and:
  1. renders it into a GeneratedListing using that platform's fields,
  2. validates against the platform's REAL hard limits (truncating + warning),
  3. produces what you actually do with it:
       - eBay / Etsy: an API payload preview (push() is stubbed for creds),
       - KDP:         a paste-ready metadata sheet (no API exists).

The constraints below are the load-bearing part — they encode each platform's
actual limits and SEO conventions so generated copy doesn't get rejected.
"""

from __future__ import annotations

from html import escape
from html.parser import HTMLParser
from typing import Any

from .models import GeneratedListing, Product


def _truncate(text: str, limit: int, warnings: list[str], label: str) -> str:
    if len(text) > limit:
        warnings.append(f"{label} exceeded {limit} chars ({len(text)}) — truncated.")
        shortened = text[:limit].rstrip()
        if not text[limit].isspace() and " " in shortened:
            shortened = shortened.rsplit(" ", 1)[0].rstrip()
        return shortened or text[:limit]
    return text


def _text(value: Any, default: str = "") -> str:
    """Normalise a scalar model field without leaking ``None`` downstream."""
    if value is None:
        return default
    return str(value)


def _string_list(value: Any) -> list[str]:
    """Return a model-provided sequence as strings; reject scalar lookalikes."""
    if not isinstance(value, (list, tuple)):
        return []
    return [str(item) for item in value if item is not None]


def _mapping(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _optional_bool(value: Any) -> bool | None:
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        normalised = value.strip().casefold()
        if normalised in {"true", "yes", "1"}:
            return True
        if normalised in {"false", "no", "0"}:
            return False
    return None


class _SafeListingHtml(HTMLParser):
    """Keep the small formatting subset marketplaces accept and strip scripts."""

    _ALLOWED = frozenset({"p", "ul", "ol", "li", "b", "strong", "em", "br"})
    _SUPPRESSED = frozenset({"script", "style"})

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []
        self.suppressed_depth = 0

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        tag = tag.lower()
        if tag in self._SUPPRESSED:
            self.suppressed_depth += 1
        elif not self.suppressed_depth and tag in self._ALLOWED:
            self.parts.append(f"<{tag}>")

    def handle_startendtag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if not self.suppressed_depth and tag.lower() == "br":
            self.parts.append("<br>")

    def handle_endtag(self, tag: str) -> None:
        tag = tag.lower()
        if tag in self._SUPPRESSED and self.suppressed_depth:
            self.suppressed_depth -= 1
        elif not self.suppressed_depth and tag in self._ALLOWED and tag != "br":
            self.parts.append(f"</{tag}>")

    def handle_data(self, data: str) -> None:
        if not self.suppressed_depth:
            self.parts.append(escape(data))


def _safe_html(value: Any, fallback: Any = "") -> str:
    raw = _text(value) or _text(fallback)
    parser = _SafeListingHtml()
    parser.feed(raw)
    parser.close()
    return "".join(parser.parts)


# --- eBay -------------------------------------------------------------------

class EbayAdapter:
    name = "ebay"
    TITLE_MAX = 80          # hard eBay limit
    MARKETPLACE = "EBAY_US"

    def render(self, product: Product, gen: dict[str, Any]) -> GeneratedListing:
        w: list[str] = []
        title_text = _text(gen.get("ebay_title")) or product.name
        title = _truncate(title_text, self.TITLE_MAX, w, "eBay title")
        specifics = {
            str(key): _text(value)
            for key, value in _mapping(gen.get("ebay_item_specifics")).items()
            if _text(value)
        }
        if not specifics:
            w.append("No item specifics — eBay search heavily weights these; add some.")

        categories = _mapping(gen.get("category_suggestions"))

        listing = GeneratedListing(
            platform=self.name,
            sku=product.sku,
            title=title,
            description=_safe_html(
                gen.get("description_html"), gen.get("description_plain", "")
            ),
            keywords=[],  # eBay has no tags; keywords live in title + specifics
            bullets=_string_list(gen.get("bullets")),
            category=_text(categories.get("ebay")),
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
            "publishOffer. Validate listing.extra['api_payload'] before sending."
        )


# --- Etsy -------------------------------------------------------------------

class EtsyAdapter:
    name = "etsy"
    TITLE_MAX = 140
    TAG_MAX_COUNT = 13
    TAG_MAX_LEN = 20
    RECOMMENDED_TITLE_WORDS = 15

    def render(self, product: Product, gen: dict[str, Any]) -> GeneratedListing:
        w: list[str] = []
        title_text = _text(gen.get("etsy_title")) or product.name
        title = _truncate(title_text, self.TITLE_MAX, w, "Etsy title")
        if len(title.split()) > self.RECOMMENDED_TITLE_WORDS:
            w.append(
                f"Etsy recommends clear titles under {self.RECOMMENDED_TITLE_WORDS} words; "
                f"this title has {len(title.split())}."
            )

        tags: list[str] = []
        seen_tags: set[str] = set()
        for t in _string_list(gen.get("etsy_tags")):
            t = str(t).strip()
            if len(t) > self.TAG_MAX_LEN:
                w.append(f"Tag '{t}' > {self.TAG_MAX_LEN} chars — dropped.")
                continue
            folded = t.casefold()
            if t and folded not in seen_tags:
                tags.append(t)
                seen_tags.add(folded)
        if len(tags) > self.TAG_MAX_COUNT:
            w.append(f"More than {self.TAG_MAX_COUNT} tags — kept first {self.TAG_MAX_COUNT}.")
            tags = tags[: self.TAG_MAX_COUNT]
        if len(tags) < self.TAG_MAX_COUNT:
            w.append(
                f"Only {len(tags)}/{self.TAG_MAX_COUNT} tags used — "
                f"Etsy SEO rewards using all {self.TAG_MAX_COUNT}."
            )

        categories = _mapping(gen.get("category_suggestions"))
        taxonomy_raw = product.attributes.get("etsy_taxonomy_id")
        try:
            taxonomy_id = int(taxonomy_raw) if taxonomy_raw not in (None, "") else None
        except (TypeError, ValueError):
            taxonomy_id = None

        who_made = _text(product.attributes.get("etsy_who_made")).strip() or None
        when_made = _text(product.attributes.get("etsy_when_made")).strip() or None
        is_supply = _optional_bool(product.attributes.get("etsy_is_supply"))
        missing_etsy_fields = [
            name
            for name, value in (
                ("taxonomy_id", taxonomy_id),
                ("who_made", who_made),
                ("when_made", when_made),
                ("is_supply", is_supply),
            )
            if value is None
        ]
        if missing_etsy_fields:
            w.append(
                "Etsy draft preview is not submit-ready; provide explicit "
                + ", ".join(missing_etsy_fields)
                + " metadata and verify the item meets Etsy's Creativity Standards."
            )

        listing = GeneratedListing(
            platform=self.name,
            sku=product.sku,
            title=title,
            description=_text(gen.get("description_plain")),
            keywords=tags,
            bullets=_string_list(gen.get("bullets")),
            category=_text(categories.get("etsy")),
            warnings=w,
        )
        listing.extra["api_payload"] = {
            # Preview of Etsy API v3 createDraftListing. Null policy fields are
            # deliberate blockers until the seller supplies truthful metadata.
            "quantity": 1,
            "title": title,
            "description": listing.description,
            "price": product.price or 0,
            # These policy-sensitive fields must be supplied by the seller. Do
            # not infer handmade/production claims from a product description.
            "who_made": who_made,
            "when_made": when_made,
            "taxonomy_id": taxonomy_id,
            "is_supply": is_supply,
            "tags": tags,
            "state": "draft",
        }
        return listing

    def push(self, listing: GeneratedListing, creds: dict[str, str] | None = None):
        raise NotImplementedError(
            "Wire Etsy API v3 OAuth here: createDraftListing -> uploadListingImage -> "
            "updateListing(state='active'). Complete and validate the payload first."
        )


# --- Amazon KDP (no API) ----------------------------------------------------

class KdpAdapter:
    name = "kdp"
    KEYWORD_SLOTS = 7
    KEYWORD_MAX_LEN = 50
    CATEGORY_SLOTS = 3   # you pick 3; the 2026 algorithm assigns the rest by metadata

    def render(self, product: Product, gen: dict[str, Any]) -> GeneratedListing:
        w: list[str] = []
        # KDP requires title and subtitle to match the book cover exactly. Model
        # suggestions are deliberately ignored rather than silently publishing
        # keyword-stuffed metadata that is absent from the cover.
        title = product.name.strip()
        suggested_title = _text(gen.get("kdp_title")).strip()
        if suggested_title and suggested_title != title:
            w.append("Generated KDP title ignored — KDP metadata must match the cover.")

        subtitle = _text(product.attributes.get("subtitle")).strip()
        suggested_subtitle = _text(gen.get("kdp_subtitle")).strip()
        if suggested_subtitle and suggested_subtitle != subtitle:
            w.append("Generated KDP subtitle ignored — KDP metadata must match the cover.")
        if len(title) + len(subtitle) >= 200:
            raise ValueError("KDP title and subtitle must total fewer than 200 characters.")

        title_words = {x.lower() for x in (title + " " + subtitle).split()}

        # Normalise and cap to the slot count BEFORE validating. Validating
        # first meant warning about keywords that the cap then discarded —
        # noise about copy that never reaches the listing.
        kws: list[str] = []
        seen_keywords: set[str] = set()
        for raw in _string_list(gen.get("kdp_keywords")):
            original = raw.strip()
            k = _truncate(original, self.KEYWORD_MAX_LEN, w, "KDP keyword")
            folded = k.casefold()
            if k and folded not in seen_keywords:
                kws.append(k)
                seen_keywords.add(folded)
        kws = kws[: self.KEYWORD_SLOTS]

        for k in kws:
            if any(word in title_words for word in k.lower().split()):
                w.append(f"Keyword '{k}' repeats title/subtitle words — wastes a slot.")
        if len(kws) < self.KEYWORD_SLOTS:
            w.append(f"Only {len(kws)}/{self.KEYWORD_SLOTS} keyword slots filled.")

        cats = _text(_mapping(gen.get("category_suggestions")).get("kdp"))

        listing = GeneratedListing(
            platform=self.name,
            sku=product.sku,
            title=title,
            description=_safe_html(
                gen.get("description_html"), gen.get("description_plain", "")
            ),
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
