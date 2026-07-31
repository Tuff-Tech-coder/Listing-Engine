"""Tests for the platform adapters -- the load-bearing constraint layer."""
import pytest

from listing_engine.engine import generate_listings
from listing_engine.models import GeneratedListing, Product
from listing_engine.platforms import ADAPTERS, EbayAdapter, EtsyAdapter, KdpAdapter


@pytest.fixture
def product():
    return Product(
        sku="SKU-1", kind="physical", name="Wireless Earbuds",
        category_hint="bluetooth earbuds", brand="Acme", price=49.99,
        features=["30h battery", "IPX7 waterproof"],
        attributes={"color": "black"}, keywords_seed=["anc", "workout"],
    )


@pytest.fixture
def book():
    return Product(sku="BK-1", kind="book", name="Dino Coloring Book",
                   category_hint="kids coloring book", price=7.99)


class TestProductModel:
    def test_from_dict_ignores_unknown_keys(self):
        p = Product.from_dict({"sku": "A", "kind": "book", "name": "N", "bogus": 1})
        assert p.sku == "A" and not hasattr(p, "bogus")

    def test_defaults_are_independent_instances(self):
        a, b = Product("1", "physical", "A"), Product("2", "physical", "B")
        a.features.append("x")
        assert b.features == [], "mutable default leaked between instances"


class TestEbayAdapter:
    def test_title_truncated_to_80_with_warning(self, product):
        out = EbayAdapter().render(product, {"ebay_title": "W" * 120})
        assert len(out.title) <= 80
        assert any("80" in w for w in out.warnings)

    def test_warns_when_item_specifics_missing(self, product):
        out = EbayAdapter().render(product, {"ebay_title": "T", "ebay_item_specifics": {}})
        assert any("item specifics" in w.lower() for w in out.warnings)

    def test_payload_matches_sell_inventory_shape(self, product):
        out = EbayAdapter().render(product, {
            "ebay_title": "T", "ebay_item_specifics": {"Brand": "Acme"}})
        payload = out.extra["api_payload"]
        assert payload["inventory_item"]["sku"] == "SKU-1"
        assert payload["inventory_item"]["product"]["aspects"]["Brand"] == ["Acme"]
        assert payload["offer"]["marketplaceId"] == "EBAY_US"
        assert payload["offer"]["pricingSummary"]["price"]["value"] == "49.99"

    def test_push_is_explicitly_unimplemented(self, product):
        out = EbayAdapter().render(product, {"ebay_title": "T"})
        with pytest.raises(NotImplementedError):
            EbayAdapter().push(out)


class TestEtsyAdapter:
    def test_drops_overlong_tags_and_warns(self, product):
        out = EtsyAdapter().render(product, {"etsy_tags": ["ok tag", "x" * 25]})
        assert "x" * 25 not in out.keywords
        assert any("dropped" in w for w in out.warnings)

    def test_caps_at_thirteen_tags(self, product):
        out = EtsyAdapter().render(product, {"etsy_tags": [f"tag{i}" for i in range(20)]})
        assert len(out.keywords) == 13

    def test_deduplicates_tags(self, product):
        out = EtsyAdapter().render(product, {"etsy_tags": ["a", "a", "b"]})
        assert out.keywords == ["a", "b"]

    def test_warns_when_under_thirteen_tags(self, product):
        out = EtsyAdapter().render(product, {"etsy_tags": ["a", "b"]})
        assert any("13" in w for w in out.warnings)

    def test_draft_state_payload(self, product):
        out = EtsyAdapter().render(product, {"etsy_title": "T", "etsy_tags": []})
        assert out.extra["api_payload"]["state"] == "draft"
        assert out.extra["api_payload"]["price"] == 49.99


class TestKdpAdapter:
    def test_keywords_capped_at_seven_slots(self, book):
        out = KdpAdapter().render(book, {"kdp_keywords": [f"kw{i}" for i in range(12)]})
        assert len(out.keywords) == 7

    def test_keyword_truncated_to_fifty_chars(self, book):
        out = KdpAdapter().render(book, {"kdp_keywords": ["z" * 80]})
        assert all(len(k) <= 50 for k in out.keywords)

    def test_warns_on_title_word_repetition(self, book):
        out = KdpAdapter().render(book, {
            "kdp_title": "Dino Coloring Book", "kdp_keywords": ["dino fun"]})
        assert any("repeats title" in w for w in out.warnings)

    def test_warns_when_subtitle_missing(self, book):
        out = KdpAdapter().render(book, {"kdp_subtitle": ""})
        assert any("subtitle" in w.lower() for w in out.warnings)

    def test_paste_sheet_has_all_seven_slots(self, book):
        out = KdpAdapter().render(book, {"kdp_keywords": ["a", "b"]})
        sheet = out.extra["paste_sheet"]
        for i in range(1, 8):
            assert f"  {i}." in sheet


class TestEngine:
    def test_generates_all_platforms_by_default(self, product):
        out = generate_listings(product, backend="template")
        assert set(out) == set(ADAPTERS)
        assert all(isinstance(v, GeneratedListing) for v in out.values())

    def test_platform_subset(self, product):
        assert set(generate_listings(product, ["ebay"], backend="template")) == {"ebay"}

    def test_unknown_platform_raises(self, product):
        with pytest.raises(ValueError, match="Unknown platform"):
            generate_listings(product, ["shopify"], backend="template")

    def test_template_backend_needs_no_credentials(self, product, monkeypatch):
        monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
        out = generate_listings(product, backend="template")
        assert out["ebay"].title
        assert out["etsy"].keywords

    def test_listing_is_json_serializable(self, product):
        import json
        out = generate_listings(product, ["ebay"], backend="template")
        assert json.loads(json.dumps(out["ebay"].to_dict()))["sku"] == "SKU-1"
