"""End-to-end command-line tests."""

import json

import pytest

from listing_engine.cli import main


def _write_json(path, value):
    path.write_text(json.dumps(value), encoding="utf-8")


def test_cli_uses_kind_aware_defaults_and_creates_output_parents(tmp_path):
    source = tmp_path / "products.json"
    destination = tmp_path / "nested" / "results.json"
    _write_json(source, [
        {"sku": "P-1", "kind": "physical", "name": "Widget"},
        {"sku": "B-1", "kind": "book", "name": "Book"},
    ])

    main(["--file", str(source), "--json-out", str(destination)])

    output = json.loads(destination.read_text(encoding="utf-8"))
    assert set(output["P-1"]) == {"ebay", "etsy"}
    assert set(output["B-1"]) == {"kdp"}


def test_cli_rejects_duplicate_skus_instead_of_overwriting(tmp_path, capsys):
    source = tmp_path / "products.json"
    _write_json(source, [
        {"sku": "SAME", "kind": "physical", "name": "One"},
        {"sku": "SAME", "kind": "physical", "name": "Two"},
    ])

    with pytest.raises(SystemExit, match="2"):
        main(["--file", str(source)])
    assert "duplicate SKU" in capsys.readouterr().err


def test_cli_rejects_empty_platform_argument(tmp_path, capsys):
    source = tmp_path / "product.json"
    _write_json(source, {"sku": "P-1", "kind": "physical", "name": "Widget"})

    with pytest.raises(SystemExit, match="2"):
        main(["--file", str(source), "--platforms", " , "])
    assert "must name at least one" in capsys.readouterr().err


def test_cli_reports_non_object_products_cleanly(tmp_path, capsys):
    source = tmp_path / "product.json"
    _write_json(source, ["not an object"])

    with pytest.raises(SystemExit, match="2"):
        main(["--file", str(source)])
    assert "must be a JSON object" in capsys.readouterr().err
