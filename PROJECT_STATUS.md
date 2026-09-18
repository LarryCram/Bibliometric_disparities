# Project Status & Roadmap

This document preserves the context, architectural decisions, and next steps following the migration from `SmallProjects`.

## Completed Milestones
1. **Migration & Rationalization**: Cleanly separated core domain logic (`src/era/core/`), harmonization engine (`src/era/harmonization/`), and external source connectors (`src/era/sources/`).
2. **Packaging**: Standardized as an editable Python package `era-bibliometrics` (`pyproject.toml`), eliminating all `sys.path.insert` hacks.
3. **Harmonization & Test Validation**: 116 tests covering Olensky classification, string hygiene, field rules, and cross-HEP indel/truncation fixes are 100% passing.
4. **Git Repository**: Initial baseline committed and pushed to `git@github.com:LarryCram/Bibliometric_disparities.git`.

## Active Roadmap
1. **Canonical ERA Baseline (Stage 1 Harmonization)**:
   - Run `era_processor.py` to produce deduplicated, canonical ERA research records across all multi-HEP submissions.
2. **External Linkage & Matching (Stage 2)**:
   - Match canonical ERA records against OpenAlex (`src/era/matching/match_openalex.py`).
   - Match canonical ERA records against Scopus (`src/era/matching/match_scopus.py`).
   - Match canonical ERA records against Trove (`src/era/matching/match_trove.py`).
   - Evaluate and classify external mismatches using Olensky's (2015) IAC taxonomy (`src/era/matching/evaluate_disparities.py`).
3. **Metrics & Downstream Analysis (Stage 3)**:
   - Calculate cross-index coverage rates by ERA output type in `analysis/`.
   - Compute overlap Venn/UpSet metrics across ERA, OpenAlex, Scopus, and Trove.
   - Profile the frequency of Olensky inaccuracy codes (Type 1, 2, 3) across each index.
4. **Paper Artifact Generation (Stage 4)**:
   - Generate formatted `.docx` tables in `report/output/tables/` ready for insertion into the Word/LibreOffice manuscript.
   - Generate publication-quality 300-DPI `.png` / vector `.pdf` figures in `report/output/figures/`.
