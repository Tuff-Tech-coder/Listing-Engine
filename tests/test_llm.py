"""Tests for model-output parsing, retry timing and the template fallback."""

from datetime import UTC, datetime, timedelta
from email.utils import format_datetime

import pytest

from listing_engine.llm import (
    _backoff_seconds,
    _extract_json,
    _generate_template,
)
from listing_engine.models import Product


class FakeResponse:
    def __init__(self, retry_after: str):
        self.headers = {"retry-after": retry_after}


def test_extract_json_returns_first_complete_object_not_greedy_span():
    assert _extract_json('preface {"first": 1} trailing {"second": 2}') == {"first": 1}


def test_extract_json_accepts_markdown_fence():
    assert _extract_json('```json\n{"ok": true}\n```') == {"ok": True}


def test_extract_json_rejects_text_without_object():
    with pytest.raises(ValueError, match="No JSON object"):
        _extract_json("not json")


def test_retry_after_negative_value_is_clamped_to_zero():
    assert _backoff_seconds(FakeResponse("-5"), 0) == 0


def test_retry_after_http_date_is_supported_and_capped():
    future = datetime.now(UTC) + timedelta(minutes=5)
    assert _backoff_seconds(FakeResponse(format_datetime(future)), 0) == 30


def test_template_does_not_pretruncate_adapter_owned_fields():
    product = Product(
        sku="1",
        kind="physical",
        name="Very Long Product Name " * 8,
        keywords_seed=["a keyword phrase that is intentionally longer than twenty characters"],
    )
    generated = _generate_template(product)
    assert len(generated["ebay_title"]) > 80
    assert len(generated["etsy_tags"][0]) > 20


def test_template_escapes_html_and_avoids_empty_copy_segments():
    product = Product(sku="1", kind="physical", name="<script>alert(1)</script>")
    generated = _generate_template(product)
    assert "<script>" not in generated["description_html"]
    assert "&lt;script&gt;" in generated["description_html"]
    assert ". ." not in generated["description_plain"]
    assert generated["bullets"] == []


def test_template_uses_only_source_authored_cover_subtitle():
    product = Product(
        sku="1", kind="book", name="Cover Title", attributes={"subtitle": "Cover Subtitle"}
    )
    assert _generate_template(product)["kdp_subtitle"] == "Cover Subtitle"
