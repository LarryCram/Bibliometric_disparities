# Bibliometric Disparities: ERA vs OpenAlex, Scopus & Trove

Research pipeline and analysis comparing how ARC ERA-reported research outputs are indexed and described across OpenAlex, Scopus, Web of Science, and Trove (National Library of Australia).

The project separates:
1. **Reporter-side disparities**: Variations that arise from how different Australian Higher Education Providers (HEPs) report the same underlying research outputs to ERA (whitespace hygiene, punctuation, truncated strings from institutional CRIS systems, and missing/divergent DOIs).
2. **Indexer-side disparities**: Variations that arise from how external bibliometric databases catalog, link, or fail to match these works.

Discrepancies are systematically evaluated and classified using **Olensky's (2015) Taxonomy of Bibliographic Inaccuracies**.

---

## Architecture (`src/` Layout)

The codebase is organized as an editable Python package (`era`):

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
│       │   ├── combiner.py        # Stage-C multi-HEP deduplication & canonical record combiner
│       │   ├── duplicates.py      # Cross-institution duplicate detection and group analysis
│       │   ├── download_era_outputs.py
│       │   └── convert_to_parquet.py
│       │
│       ├── sources/               # External source connectors and shard builders
│       │   ├── openalex/          # Snapshot matching, URLs, locations, authorships
│       │   ├── scopus/            # Scopus DOI/title fetchers, backoff engine, and shard compilers
│       │   ├── wos/               # Web of Science API fetcher and normalizer
│       │   ├── trove/             # Trove ISBN matcher, title search, and portfolio item fetchers
│       │   └── fulltext/          # PDF downloaders, landing page scrapers, GROBID/TEI parser
│       │
│       ├── matching/              # Matching canonical ERA outputs to external indexes
│       │   ├── match_openalex.py  # OpenAlex work hydration & paired record generation
│       │   ├── match_scopus.py    # Scopus tiered DOI & title matching with Olensky evaluation
│       │   ├── match_trove.py     # Trove tiered ISBN & candidate title matching with Olensky profiling
│       │   └── evaluate_disparities.py # Olensky disparity evaluator & coupled drift profiler
│       │
│       └── analysis/              # Multi-index synthesis & downstream coverage metrics
│           ├── multi_index_coverage.py   # Canonical OpenAlex rollup & synthesis dataset builder
│           ├── comparative_olensky.py    # Cross-index Olensky IAC taxonomy synthesis
│           └── run_stage3.py             # Stage 3 orchestration runner
│
├── report/                        # Publication artifacts (8 APA Word/CSV/MD tables & 7 vector figures)
│   ├── generate_tables.py         # Word docx, CSV, and markdown table generator
│   ├── generate_figures.py        # 300-DPI PNG and vector PDF figure generator
│   └── output/
│       ├── figures/               # 7 publication figures (PDF + PNG)
│       └── tables/                # 8 APA publication tables (DOCX + CSV + MD)
│
├── data_persisted/                # Curated reference assets (ERA Journal List, SEER vocabularies)
├── docs/                          # Methodology reports and analysis summaries
└── tests/                         # 195 unit tests (100% passing)
```

---

## End-to-End Pipeline Milestones

1. **Stage 1: Canonical ERA Baseline Reconcilation (`combiner.py`)**
   - Transitive connected component clustering over DOIs, WoS/MEDLINE IDs, and safeguarded titles.
   - Reconciled 567,647 raw ERA submissions into **455,741 distinct canonical research records** (`era_research_outputs_canonical.parquet`).
   - Identified 83,945 multi-HEP collaborative works and tracked cross-institution reporting disparities.

2. **Stage 2: External Index Linkage & Disparity Profiling**
   - **OpenAlex (Oct26)**: Matched 427,882 canonical works (93.9%). Recovered 5,200 non-journal works via XPAC.
   - **Scopus**: Executed detached title-harvest for non-DOI works (68,774 queries, 73.8% hit rate). Total matched canonical works surged from 293,482 (64.4%) to **342,478 (75.1%)**, with Journal Article coverage reaching **92.6%**.
   - **Trove**: Matched 36,614 works across books and NTROs (**87.8%** of Books, **61.0%** of Chapters, **74.3%** of Exhibitions, **73.4%** of Reports).
   - **Multi-DOI Work Locations**: Demonstrated that cross-HEP conflicting DOIs (Olensky code D) resolve into the identical OpenAlex work via `work_locations`.

3. **Stage 3: Multi-Index Downstream Synthesis (`era.analysis`)**
   - Combined all four sources into `data/era_multi_index_synthesis.parquet`.
   - **Multi-Index Union**: Indexes **95.73%** (436,273 works) of all Australian academic outputs:
     - Journal Articles: **98.5%**
     - Books: **97.3%**
     - Conference Publications: **90.5%**
     - Book Chapters: **87.5%**
     - Research Reports: **82.1%**
     - Curated Exhibitions: **79.4%**
   - Profiled comparative Olensky inaccuracies across indices: OpenAlex (49.6% exact, 0.44% discordant), Scopus (94.8% punctuation/case, 0.22% discordant), Trove (79.5% punctuation/case, 11.4% typo, 4.9% cropped subtitles, 3.89% discordant).

4. **Stage 4: Paper Artifact Generation (`report/`)**
   - Generated 8 APA publication tables (`report/generate_tables.py`).
   - Generated 7 publication figures (`report/generate_figures.py`).

---

## Setup & Execution

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
pip install -e .
```

### Running Tests
```bash
pytest
```

### Running Stage 3 Synthesis
```bash
python -m era.analysis.run_stage3
```

### Generating Paper Tables and Figures
```bash
python report/generate_tables.py
python report/generate_figures.py
```
