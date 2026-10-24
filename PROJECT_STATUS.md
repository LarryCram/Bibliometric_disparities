# Project Status & Roadmap

*Last Updated: 24 October 2026*

This document tracks pipeline milestones, architectural decisions, and artifact outputs for the ERA 2018 bibliometric disparities project.

## Executive Summary

| Analytical Stage | Description | Coverage / Scope | Status |
| :--- | :--- | :--- | :--- |
| **Stage 1: ERA Baseline & Deduplication** | Transitive DSU clustering of 567,647 submissions into canonical works | 455,741 canonical works; 83,945 multi-HEP outputs; tracks cross-institution reporting disparities | **100% Complete** |
| **Stage 2A: OpenAlex Linkage** | Snapshot Oct26 matching (Compact + XPAC fallback) & location DOIs | 427,882 canonical works (93.9%); 536,707 paired submissions | **100% Complete** |
| **Stage 2B: Scopus Linkage & Title Harvest** | Persistent title query harvest (50,780 hits) + pybliometrics cache | 342,478 canonical works (75.1%); 92.6% of journal articles | **100% Complete** |
| **Stage 2C: Trove Linkage** | 2-tier ISBN and title query matching across National Library catalog | 36,614 works; 87.8% of books, 61.0% of book chapters, 50–74% NTROs | **100% Complete** |
| **Stage 3: Multi-Index Synthesis** | Unified matrix (`era_multi_index_synthesis.parquet`) & UpSet intersections | **95.73% overall coverage** (436,273 works); comparative Olensky taxonomy | **100% Complete** |
| **Stage 4: Paper Artifacts** | 10 APA tables (`all_paper_tables.docx`, CSV, MD) & 7 publication figures | High-resolution 300-DPI PNGs and vector PDFs in `report/output/` | **100% Complete** |
| **Stage 5: References & Authors** | GROBID TEI internal citation network (131k citations), in-text mention importance & author/institution fidelity | `era_grobid_reference_disparities.parquet`; Tables 9 & 10 | **100% Complete** |
| **Validation & Test Suite** | Full test suite across normalization, Olensky rules, and matching | 198 unit tests passing | **100% Passing** |

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

14. **Paper Artifact Generation (Stage 4 Completed)**:
   - Expanded and updated `report/generate_tables.py` generating 8 APA-style publication tables in standalone `.docx`, `.csv`, `.md`, and assembled into `all_paper_tables.docx`:
     - Table 1: Submission counts, reconciled canonical works, and duplication gradients (reconciled across all 567,647 submissions).
     - Table 2: Cross-institution reporting disparities across co-submitted works.
     - Table 3: Olensky IAC taxonomy incidence in ERA.
     - Table 4: Distribution of Australian HEP co-submissions across the 83,945 collaborative works.
     - Table 5: Multi-index coverage by output type across OpenAlex (Compact & XPAC), Scopus, Trove, and Union.
     - Table 6: Cross-index disjoint intersections (UpSet overlap matrix).
     - Table 7: Comparative external Olensky inaccuracy profiles across OpenAlex, Scopus, and Trove.
     - Table 8: Dual-perspective coupled lifecycle drift vs isolated reporting errors.
   - Expanded `report/generate_figures.py` generating 7 high-resolution publication figures (both 300-DPI `.png` and vector `.pdf`) in `report/output/figures/`:
     - Figure 1: Output type volume and duplicate rate gradient.
     - Figure 2: HEP-HEP reporting disparities profile.
     - Figure 3: Olensky IAC taxonomy distribution.
     - Figure 4: Distribution of co-reporting HEPs per output.
     - Figure 5: Multi-index coverage rates by ERA research output type (grouped bar chart).
     - Figure 6: Cross-index intersection sets and composition breakdown (UpSet horizontal bar chart).
     - Figure 7: Comparative Olensky IAC type distribution across external indices (stacked bar chart).
   - Test suite expanded to 198 unit tests (100% passing).
15. **GROBID TEI Reference Disparities, Citation Importance, and Author/Institution Analysis (Stage 5 Completed)**:
   - High-throughput parallel extraction (`extract_grobid.py`) across 69,791 TEI XML outputs parsed from full-text PDFs in `/home/lc/k/era_oa_pdfs_tei`:
     - `grobid_papers.parquet`: 69,791 papers with header author strings and citation tallies.
     - `grobid_references.parquet`: 3,351,806 bibliography entries kept strictly separated from canonical ERA records.
     - `grobid_mentions.parquet`: 5,266,754 in-text citation mentions with calling context and section headers (`<head>`).
   - Mapped 131,395 internal citations between Australian ERA outputs (41,500 exact DOI citations + 89,895 exact unique title matches with year tolerance) stored in `data/era_grobid_reference_disparities.parquet`.
   - **Olensky Citation Importance Hypothesis Verified**:
     - Bibliographic inaccuracies follow a strict monotonic gradient with in-text citation frequency: Type 3 severe title discordance falls from 25.72% for uncited references and 18.82% for single passing citations down to 14.69% for foundational citations ($7+$ mentions).
     - Surface fidelity (casing/punctuation preservation) increases from 59.83% (uncited) to 71.30% ($7+$ mentions).
     - In-text section placement analysis reveals highest title fidelity in Discussion/Conclusions (79.73% surface, only 7.38% discordant), compared to Introduction (18.65% discordant) and Results (26.67% discordant).
   - **Author & Institutional Disparities (Olensky Perspective)**:
     - Author count concordance between PDF full-text extraction and OpenAlex metadata is 72.00% exact; 9.56% of PDFs omit/truncate authors (et al.), and 18.44% contain extra tokens (editorial boards or institutional department names parsed into author blocks).
     - Reference author surname fidelity improves monotonically with citation importance, rising from 84.27% (uncited) and 88.07% (passing) to 93.93% ($7+$ mentions).
     - Institutional attribution divergence between ERA submissions (submitting Australian HEP) and OpenAlex author affiliations is 32.36% (67.64% normalized concordance), reflecting multi-institution mobility, hospital/institute affiliations, and honorary appointments.
   - Generated Publication Tables 9 & 10 in `.docx`, `.csv`, `.md`, and assembled into master `all_paper_tables.docx`.
   - Unit test suite expanded to 198 tests (100% passing).

## Current State & Next Steps
- All 5 stages of the bibliometric disparities project (Harmonization, Linkage & Profiling, Multi-Index Synthesis, Paper Artifact Generation, and GROBID Reference/Author Network) are 100% complete and verified.
- Available for optional background queueing: 65,845 new direct PDF URLs identified in `work_locations` to expand the full-text corpus if desired.


