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

8. **Stage 1 Canonical ERA Baseline & Stage-C Combiner**:
   - Built `StageCCombiner` in `src/era/harmonization/combiner.py` and exposed `ERAProcessor.build_canonical()`.
   - Transitive clustering via connected components (DSU) across valid DOIs, valid WoS/MEDLINE identifiers, and safeguarded normalized titles (preventing spurious mergers of generic titles with conflicting DOIs).
   - Reconciled all 567,647 raw ERA submissions into 455,741 distinct canonical research records in `data/era_research_outputs_canonical.parquet`.
   - 100.0% of submissions mapped losslessly (`sum(len(source_ids)) == 567,647`), identifying 83,945 multi-HEP collaborative works and tracking cross-institution disparity events (Olensky codes E for DOI omissions, D for DOI conflicts, T for year differences, and title/outlet variances).
   - Expanded test suite to 163 unit tests (100% passing).
9. **Stage 2 Scopus Linkage & Olensky Disparity Profiling**:
   - Built `src/era/matching/match_scopus.py` implementing tiered DOI and normalized title fallback matching against 303,618 cached Scopus records (2011–2016).
   - Matched 293,482 canonical ERA works (64.4% overall coverage), spanning 78.3% of Journal Articles, 33.0% of Conference Publications, 17.6% of Book Chapters, and 9.1% of Books.
   - Classified discrepancies via Olensky's taxonomy: 93.9% punctuation/case variants (code R), 3.7% typographical (code A), 2.0% spelling indels (code B), and 0.26% discordant (code D).
   - Generated `data/era_scopus_canonical_matches.parquet` and `data/era_scopus_disparities.parquet`. Unit test suite expanded to 172 tests (100% passing).
10. **Scopus Title Search & Detached Background Harvesting Pipeline**:
   - Created `src/era/sources/scopus/fetch_titles.py` with adaptive exponential backoff and jitter, query sanitization (HTML tag stripping, wildcard suppression), integrated seamlessly with `pybliometrics`' on-disk cache on K_DRIVE.
   - Built `src/era/sources/scopus/build_title_shard.py` to compile cached title searches into `data/scopus_records/titles.parquet`.
   - Established that 20.2% of canonical ERA Journal Articles lacked DOIs, and empirical sampling revealed ~40–50% of these non-DOI works are indexed in Scopus and recoverable via title search without weekly quota limits under institutional tokens.
   - Automated execution via detached runner `run_scopus_harvest.sh` managed as a persistent systemd user service (`scopus-harvest.service`) surviving IDE disconnects.
   - Test suite expanded to 180 unit tests (100% passing).

## Active Roadmap
1. **External Linkage & Matching Expansion (Stage 2)**:
   - Match canonical ERA records against Trove (`src/era/matching/match_trove.py`).
   - Evaluate and classify external mismatches using Olensky's (2015) IAC taxonomy across all indices.
2. **Metrics & Downstream Analysis (Stage 3)**:
   - Calculate cross-index coverage rates by ERA output type in `analysis/`.
   - Compute overlap Venn/UpSet metrics across ERA, OpenAlex, Scopus, and Trove.
   - Profile the frequency of Olensky inaccuracy codes (Type 1, 2, 3) across each index.
3. **Paper Artifact Generation (Stage 4)**:
   - Update `report/generate_tables.py` and `report/generate_figures.py` to reflect Oct26 data, the canonical ERA baseline, Scopus matches, and the dual-perspective / coupled drift profiles.

