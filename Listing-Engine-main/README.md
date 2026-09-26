# Listing Engine

![Python](https://img.shields.io/badge/Python-3.11+-3776AB?logo=python&logoColor=white)
![Tests](https://img.shields.io/badge/tests-44%20passing-brightgreen)
![License](https://img.shields.io/badge/License-MIT-green)
![Zero dependencies](https://img.shields.io/badge/core-zero%20dependencies-lightgrey)

**Describe a product once. Get validated, platform-optimized listings for eBay, Etsy and Amazon KDP.**

Writing the same product three times for three marketplaces is duplicated effort that drifts out of sync. Doing it with three separate LLM calls costs three times as much and produces three inconsistent voices. Listing Engine makes **one** generation call that returns a superset of copy, then lets per-platform adapters slice and validate it.

```bash
pip install -e .
listing-engine --file sample_products.json
```

That runs with **no API key and no dependencies** — the default `template` backend is a deterministic fallback so you can see the whole pipeline immediately. Physical products route to eBay and Etsy; books route to KDP. Explicit incompatible selections fail fast instead of generating nonsense listings.

---

## The core idea

```
                    ┌──────────────────────┐
   Product ───────► │  ONE generation call │ ─────► superset of copy
   (models.py)      │      (llm.py)        │        (titles, tags, keywords,
                    └──────────────────────┘         descriptions, categories)
                                                              │
                          ┌───────────────────────────────────┤
                          ▼                 ▼                 ▼
                   ┌─────────────┐   ┌─────────────┐   ┌─────────────┐
                   │ EbayAdapter │   │ EtsyAdapter │   │ KdpAdapter  │
                   │ 80-char cap │   │ 13 tags×20  │   │ 7 kw × 50   │
                   └─────────────┘   └─────────────┘   └─────────────┘
                          │                 │                 │
                   payload preview   payload preview    paste-ready sheet
```

**One source of truth.** A `Product` dataclass describes the item once, loosely. Everything downstream derives from it.

**Adapters render *and* validate.** This is the load-bearing part. Each adapter enforces that platform's *real* limits and returns warnings instead of letting a listing get rejected at publish time:

| Platform | Constraints enforced |
|---|---|
| **eBay** | 80-char title cap; word-safe truncation; item-specific warnings; Sell Inventory API payload preview |
| **Etsy** | 140-char hard cap; clear-title guidance; max 13 tags of ≤20 chars; case-insensitive deduplication; flags missing taxonomy and seller-authored policy fields |
| **KDP** | title/subtitle must match the cover and total <200 chars; exactly 7 keyword slots ≤50 chars; flags repeated title words |

---

## Backends

| Backend | Setup | Use for |
|---|---|---|
| `template` | none — **default** | Instant evaluation, CI, high-volume smoke tests. Deterministic heuristic; plausibly-shaped output, not good copy. |
| `ollama` | local model on `:11434` | Free, private, good for bulk catalogs. |
| `anthropic` | `ANTHROPIC_API_KEY` | Best copy quality. |

```bash
# Local model
LISTING_BACKEND=ollama LISTING_LLM_MODEL=llama3.1 listing-engine --file sample_products.json

# Claude
LISTING_BACKEND=anthropic ANTHROPIC_API_KEY=sk-... listing-engine --file sample_products.json
```

Adding a backend means writing one function that returns the generation dict and registering it in `_BACKENDS`.

The `anthropic` backend retries transient failures — HTTP 429 (rate limited), 529 (overloaded) and 5xx — up to four times, honouring the API's `Retry-After` header when present and otherwise backing off exponentially with jitter. Non-transient failures such as a bad API key raise immediately, since retrying them only delays the error.

See [`.env.example`](.env.example) for the environment variables each backend reads. The project reads process environment variables directly; it does not auto-load a `.env` file.

---

## Example

Real output from the shipped `sample_products.json` using the `etsy` adapter on the default `template` backend:

<details>
<summary><code>listing-engine --file sample_products.json</code> (Etsy excerpt)</summary>

```
======================================================================
[ETSY]  sku=TT-EARBUDS-01
======================================================================
TITLE: AcmeSound Wireless Bluetooth Earbuds with Charging Case wireless earbuds bluetooth headphones
CATEGORY: wireless earbuds bluetooth headphones
TAGS (9): noise isolating, running earbuds, usb-c, wireless, bluetooth, earbuds, with, charging, case
DESCRIPTION:
Wireless Bluetooth Earbuds with Charging Case. Bluetooth 5.3 with low-latency mode; 32-hour total battery with USB-C charging case; IPX5 water resistant for workouts; Touch controls and built-in mic. Quality you can count on.

WARNINGS:
  ! Only 9/13 tags used — Etsy SEO rewards using all 13.
  ! Etsy draft preview is not submit-ready; provide explicit taxonomy_id, who_made,
    when_made, is_supply metadata and verify the item meets Etsy's Creativity Standards.
```

</details>

Those warnings are the point. The adapter reports unused SEO slots and refuses to invent policy-sensitive seller claims, rather than presenting an unsafe payload as publishable.

---

## Design decisions

**One generation call instead of three.** Asking for eBay copy, then Etsy copy, then KDP copy costs 3× and lets the three descriptions drift into different voices. A single structured-JSON call returns every field any platform could need — `ebay_title`, `etsy_tags`, `kdp_keywords`, descriptions in both HTML and plain text — and the adapters slice what they need. Cost and consistency both improve, and adding a fourth marketplace costs one adapter rather than one more LLM call per product.

**Validation lives in the adapter, not the prompt.** Asking a model to "keep it under 80 characters" is a request, not a guarantee. The adapters enforce each limit in code and attach warnings, so a bad generation gets truncated and flagged here rather than rejected at publish time.

**KDP has no `push()`, deliberately.** Amazon KDP has no public listing API. Browser automation against the KDP dashboard exists, but it violates KDP's terms and risks account termination — so it is **not implemented here**. `KdpAdapter` produces a paste-ready metadata sheet instead. eBay and Etsy output payload previews; Etsy deliberately leaves policy-sensitive fields unset until the seller supplies truthful taxonomy, production, and supply metadata. Both `push()` methods remain explicit stubs.

Treating "this integration should not be automated" as a design decision rather than a missing feature is intentional.

---

## Project layout

```
listing_engine/
├── __init__.py     Public API re-exports
├── models.py       Product, GeneratedListing  (dataclasses, no deps)
├── llm.py          Generation layer + 3 pluggable backends + retry policy
├── platforms.py    Adapters — limits, validation, API payload previews
├── engine.py       Orchestration: one generation → N renders
└── cli.py          Command-line runner
tests/
├── test_platforms.py   Marketplace rules and compatibility
├── test_llm.py         Parsing, fallback generation and retry behavior
└── test_cli.py         End-to-end input/output safeguards
```

## Development

```bash
pip install -e ".[dev]"
pytest -q          # 44 tests
ruff check .
```

## Roadmap

- [ ] Live `push()` for eBay and Etsy behind an OAuth credential store
- [ ] Etsy taxonomy-ID lookup (currently a `None` placeholder)
- [ ] Image handling for `uploadListingImage`
- [ ] Cost/token accounting per generation

## License

MIT — see [LICENSE](LICENSE).
