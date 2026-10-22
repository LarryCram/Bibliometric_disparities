# Project Status & Roadmap

This document preserves the context, architectural decisions, and next steps following the migration from `SmallProjects`.

## Completed Milestones
1. **Migration & Rationalization**: Cleanly separated core domain logic (`src/era/core/`), harmonization engine (`src/era/harmonization/`), and external source connectors (`src/era/sources/`).
2. **Packaging**: Standardized as an editable Python package `era-bibliometrics` (`pyproject.toml`), eliminating all `sys.path.insert` hacks.
3. **Harmonization & Test Validation**: 116 tests covering Olensky classification, string hygiene, field rules, and cross-HEP indel/truncation fixes are 100% passing.
4. **Git Repository**: Initial baseline committed and pushed to `git@github.com:LarryCram/Bibliometric_disparities.git`.
5. **Stage 4 Paper Artifact Generation (Tables & Figures)**: Formatted APA-style `.docx`, `.md`, and `.csv` tables generated via `report/generate_tables.py`, plus 300-DPI `.png` and vector `.pdf` publication figures generated via `report/generate_figures.py` (profiling ERA duplication gradient, HEP–HEP reporting disparities, Olensky IAC breakdown, and multi-institution collaboration distribution).
6. **Stage 2 Disparity Profiling & Coupled Lifecycle Drift**: Implemented `classify_disparity_profile` in `src/era/matching/evaluate_disparities.py` isolating preprint/lifecycle version drift from isolated reporting errors. Unit test suite expanded to 153 tests (100% passing).
7. **OpenAlex Snapshot Migration (Jul26 -> Oct26) & Pipeline Execution**:
   - Safely archived all previous July 2026 dataset artifacts to `data/jul26/` and comparative review to `docs/openalex_disparity_review_jul26.md`.
   - Updated global path configuration (`config.py`, `.env`, `.env.example`) to dynamically support `OPENALEX_SNAPSHOT`, defaulting to `openalex_oct26` on `M_DRIVE`.
   - Re-executed the end-to-end matching and hydration pipeline: `match_example.py` (530,978 compact matches), `match_xpac.py` (39,152 fallback matches), `urls.py` (452k works scanned for OA URLs across 1,898 partitions), and `match_openalex.py` (536,707 paired records).
   - Re-evaluated external disparities: exact canonical title agreement rose to 50.2%, discordant title errors dropped by >60% (from 1.1% in Jul26 down to 0.5% in Oct26), and coupled lifecycle drift remained consistent at 4.4%–4.9%.

## Active Roadmap
1. **Canonical ERA Baseline (Stage 1 Harmonization)**:
   - Run `era_processor.py` to produce deduplicated, canonical ERA research records across all multi-HEP submissions.
2. **External Linkage & Matching Expansion (Stage 2)**:
   - Match canonical ERA records against Scopus (`src/era/matching/match_scopus.py`).
   - Match canonical ERA records against Trove (`src/era/matching/match_trove.py`).
   - Evaluate and classify external mismatches using Olensky's (2015) IAC taxonomy across all indices.
3. **Metrics & Downstream Analysis (Stage 3)**:
   - Calculate cross-index coverage rates by ERA output type in `analysis/`.
   - Compute overlap Venn/UpSet metrics across ERA, OpenAlex, Scopus, and Trove.
   - Profile the frequency of Olensky inaccuracy codes (Type 1, 2, 3) across each index.
4. **Paper Artifact Generation (Stage 4)**:
   - Update `report/generate_tables.py` and `report/generate_figures.py` to reflect Oct26 data and the dual-perspective / coupled drift profiles.

