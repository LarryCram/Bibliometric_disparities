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
10. **Scopus Title Search & Detached Background Harvesting Pipeline (Completed)**:
   - Created `src/era/sources/scopus/fetch_titles.py` with adaptive exponential backoff and jitter, query sanitization (HTML tag stripping, wildcard suppression), integrated seamlessly with `pybliometrics`' on-disk cache on K_DRIVE.
   - Automated execution via detached runner `run_scopus_harvest.sh` managed as a persistent systemd user service (`scopus-harvest.service`).
   - Completed end-to-end execution across all 68,774 candidate non-DOI titles (50,780 hits, 73.8% hit rate; 0 errors).
   - Compiled `data/scopus_records/titles.parquet` containing 176,002 unique Scopus documents extracted from pybliometrics cache.
   - Re-executed Scopus matching pipeline: total matched canonical works surged from 293,482 (64.4%) to **342,478 (75.1%)**, with canonical Journal Article coverage climbing from 78.3% to **92.6%** (315,579 matched out of 340,779).
   - Re-generated `data/era_scopus_canonical_matches.parquet` and `data/era_scopus_disparities.parquet` (342,478 records).
11. **Stage 2 Trove Linkage & Book/NTRO Disparity Profiling**:
   - Built `src/era/matching/match_trove.py` implementing two-tier matching: Tier 1 normalized ISBN matching against 4,827 Trove book records, and Tier 2 candidate title search matching via unnested submission IDs (`source_ids`) across 78,233 candidate records.
   - Matched 36,614 distinct canonical works, dramatically bridging the coverage gap for non-journal outputs:
     - **Books**: **87.8% matched** (4,494 of 5,117 canonical books, compared to only 9.1% in Scopus).
     - **Book Chapters**: **61.0% matched** (26,324 of 43,150 chapters, compared to only 17.6% in Scopus).
     - **Research Reports**: **73.4% matched** (2,355 of 3,208).
     - **Curated Exhibitions**: **74.3% matched** (405 of 545).
     - **Recorded Rendered Works**: **62.4% matched** (518 of 830).
     - **Original Creative Works**: **53.7% matched** (2,081 of 3,877).
     - **Live Performances**: **50.0% matched** (413 of 826).
   - Classified bibliographic discrepancies via Olensky's IAC taxonomy: 79.5% punctuation/case (code R), 11.4% typographical (code A), 4.9% cropped subtitles (code F), and 3.9% discordant (code D).
   - Generated `data/era_trove_canonical_matches.parquet` and `data/era_trove_disparities.parquet`. Unit test suite expanded to 193 tests (100% passing).
12. **Multi-DOI Work Locations & Cross-Institution Disparity Resolution**:
   - Analyzed the 1,898 partitions of OpenAlex `work_locations` (`parquet_converted/work_locations`) against ERA submissions.
   - Recovered 265 previously unmatched ERA DOIs that did not appear in OpenAlex's primary `compact/works.doi`.
   - Empirically demonstrated that cross-institution DOI disagreements (Olensky code D) among co-authoring Australian HEPs resolve into the same OpenAlex `work_idx` via `work_locations`.
   - Isolated four primary mechanisms driving cross-HEP DOI divergence: preprint/working-paper lifecycle drift (e.g. bioRxiv/SSRN vs journal), publisher discussion forum pairs (e.g. Copernicus *Discussions* vs *Journal*), dual platform/aggregator registrations (e.g. Project MUSE vs JSTOR), and edited monograph vs chapter DOIs.

13. **Multi-Index Synthesis & Downstream Analysis (Stage 3 Completed)**:
   - Built canonical OpenAlex rollup in `src/era/analysis/multi_index_coverage.py`, generating `data/era_openalex_canonical_matches.parquet` (427,882 matched canonical works, 93.89%).
   - Created unified synthesis table `data/era_multi_index_synthesis.parquet` linking ERA Canonical (455,741 records) against OpenAlex, Scopus, and Trove.
   - Profiled multi-index output type coverage: union of any index covers **95.73%** of all Australian research outputs (436,273 works), including 98.5% of Journal Articles, 97.3% of Books, 87.5% of Book Chapters, 82.1% of Research Reports, and 79.4% of Curated Exhibitions.
   - Evaluated cross-index UpSet intersections: 73.5% (334,911 works) in OpenAlex ∩ Scopus; Trove uniquely contributed 5,400 works not present in either OpenAlex or Scopus, and captured 26,566 non-journal works shared only with OpenAlex.
   - Profiled comparative Olensky inaccuracy distributions in `src/era/analysis/comparative_olensky.py`: OpenAlex (49.6% exact, 0.44% discordant), Scopus (94.8% punctuation/case, 0.22% discordant), Trove (79.5% punctuation/case, 11.4% typo, 4.9% cropped subtitles, 3.89% discordant).
   - Unit test suite expanded to 195 tests (100% passing). Exported CSV and markdown summaries to `report/output/`.

## Active Roadmap
1. **Paper Artifact Generation (Stage 4)**:
   - Update `report/generate_tables.py` and `report/generate_figures.py` to reflect Oct26 data, the canonical ERA baseline, Scopus title harvest, Trove coverage, multi-index synthesis, and dual-perspective / coupled drift profiles.

