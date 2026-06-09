# Multi-Channel Listing Engine

Generate marketplace listings for **eBay**, **Etsy**, and **Amazon KDP** from a
single product description. Describe a product once; the engine produces
platform-optimized copy and pushes it (eBay/Etsy) or hands you a paste-ready
metadata sheet (KDP, which has no public API).

## Why it's built this way

- **One source of truth.** A `Product` (see `models.py`) is described once.
- **One generation call.** The LLM produces a *superset* of copy sized for every
  platform at once — cheaper and consistent across channels.
- **Adapters render + validate.** Each platform slices that output into its own
  fields and enforces the platform's *real* limits (eBay 80-char titles, Etsy's
  13 tags ≤ 20 chars, KDP's 7 keyword slots ≤ 50 chars), truncating and warning
  rather than letting a listing get rejected.
- **KDP is treated as no-API on purpose.** It produces a paste sheet. Browser
  automation for KDP exists but violates KDP's terms and risks your account, so
  it's deliberately not here.

## Quickstart (zero setup)

```bash
python cli.py --file sample_products.json --platforms ebay,etsy,kdp
```

This uses the `template` backend — deterministic, no LLM, no install. The copy is
crude on purpose; its only job is to prove the pipeline. Plug in a real model for
real copy:

```bash
# Local model (free, good for volume):
pip install -r requirements.txt
LISTING_BACKEND=ollama LISTING_LLM_MODEL=llama3.1 python cli.py --file sample_products.json

# Claude (best quality):
LISTING_BACKEND=anthropic ANTHROPIC_API_KEY=sk-... python cli.py --file sample_products.json
```

## Files

| File | Role |
|------|------|
| `models.py` | `Product` (input) and `GeneratedListing` (output) |
| `llm.py` | generation + the prompt + three backends |
| `platforms.py` | per-platform constraints, validation, rendering, API payloads |
| `engine.py` | orchestration (product → one generation → per-platform listings) |
| `cli.py` | command-line runner |
| `sample_products.json` | one physical good, one KDP book |

