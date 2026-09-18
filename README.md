# Bibliometric Disparities: ERA vs OpenAlex, Scopus & Trove

Research pipeline and analysis comparing how ARC ERA-reported research outputs are indexed and described across OpenAlex, Scopus, Web of Science, and Trove (National Library of Australia). 

The project separates:
1. **Reporter-side disparities**: Variations that arise from how different Australian Higher Education Providers (HEPs) report the same underlying research outputs to ERA (whitespace hygiene, punctuation, truncated strings from institutional CRIS systems, and missing/divergent DOIs).
2. **Indexer-side disparities**: Variations that arise from how external bibliometric databases catalog, link, or fail to match these works.

Discrepancies are systematically evaluated and classified using **Olensky's (2015) Taxonomy of Bibliographic Inaccuracies**.

## Architecture (`src/` Layout)

The codebase is organized as a Python package (`era`) with clear domain boundaries:

```text
Bibliometric_disparities/
├── config.py                      # Global path configuration (resolves K/M drives from .env)
├── pyproject.toml                 # Package definition (editable install via pip install -e .)
├── src/
│   └── era/
│       ├── core/                  # Pure domain logic: Olensky taxonomy, text hygiene, field rules
│       │   ├── olensky.py         # DisparityClassifier, Transform batteries, single-char indel codes
│       │   ├── string_processor.py# Structural feature detection (HTML/TeX, non-ASCII, spelling dialects)
│       │   └── field_processor.py # Field processors, controlled vocabularies, and checksum validators
│       │
│       ├── harmonization/         # ERA data cleaning and multi-HEP record reconciliation
│       │   ├── era_processor.py   # ERAProcessor factory, cluster indel fixes, and truncation repairs
│       │   ├── duplicates.py      # Cross-institution duplicate detection and group analysis
│       │   ├── download_era_outputs.py
│       │   └── convert_to_parquet.py
│       │
│       ├── sources/               # External source connectors and shard builders
│       │   ├── openalex/          # Snapshot matching, URLs, locations, authorships
│       │   ├── scopus/            # Scopus API fetcher and parquet shard compiler
│       │   ├── wos/               # Web of Science API fetcher and normalizer
│       │   ├── trove/             # Trove ISBN matcher, title search, and portfolio item fetchers
│       │   └── fulltext/          # PDF downloaders, landing page scrapers, GROBID/TEI parser
│       │
│       ├── matching/              # Matching canonical ERA outputs to external indexes
│       └── provenance.py          # Pipeline provenance tracking
│
├── analysis/                      # Research metrics & cross-source tables
│   ├── build_era_institution_ror_lookup.py
│   └── era_institution_ror_lookup.csv
│
├── report/                        # Publication artifacts (Word .docx tables & 300-DPI figures)
│   └── output/
│       ├── figures/
│       └── tables/
│
├── data_persisted/                # Curated reference assets (ERA Journal List, SEER vocabularies)
├── docs/                          # Methodology papers and setup guides (era_quality_review.md)
└── tests/                         # Unit tests mirroring src/era/
```

## Setup

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
pip install -e .
```

Copy `.env.example` to `.env` and set your external drive roots and API keys:
```bash
cp .env.example .env
```

## Running Tests

Run the unit test suite across the core classification rules and processors:
```bash
pytest tests/
```
