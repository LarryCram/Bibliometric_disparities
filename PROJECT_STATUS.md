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
| **Stage 4: Paper Artifacts** | 12 APA tables (`all_paper_tables.docx`, CSV, MD) & 7 publication figures | High-resolution 300-DPI PNGs and vector PDFs in `report/output/` | **100% Complete** |
| **Stage 5: References & Authors** | GROBID TEI internal citation network (159,841 citations), in-text mention importance & author/institution fidelity | `era_grobid_reference_disparities.parquet` (84,741 papers, 4.05M references, 6.35M mentions); Tables 9 & 10 | **100% Complete** |
| **Stage 6: Assessment vs Index Disparities** | Systematic comparison of ERA submissions vs OpenAlex HEP corpus; temporal mobility gradient | `manuscript/disparities_assessment_vs_index.tex`, compiled 7-page PDF | **100% Complete** |
| **Stage 7: Reference Resolution Diagnostics** | Forward anachronism rates (0.97% refs, 25.3% papers); 4 metadata-only backward error tests | Evaluated 15.9M refs across full corpus; pybliometrics live caching verified (9,998 quota); Table 11 | **100% Complete** |
| **Stage 8: Citation Mutation-Copying (Simkin & Roychowdhury)** | Stochastic citation copying model across GROBID full text (159.8k citations); reading rate $\hat{\rho} = 66.7\%$, copying rate $33.4\%$ | `simkin_roychowdhury_grobid.py`; Table 12; 49.3% inter-HEP diffusion | **100% Complete** |
| **Validation & Test Suite** | Full test suite across normalization, Olensky rules, matching, and S&R copying | 214 unit tests passing | **100% Passing** |

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
   - Expanded and updated `report/generate_tables.py` generating 11 APA-style publication tables in standalone `.docx`, `.csv`, `.md`, and assembled into `all_paper_tables.docx`:
     - Table 1: Submission counts, reconciled canonical works, and duplication gradients (reconciled across all 567,647 submissions).
     - Table 2: Cross-institution reporting disparities across co-submitted works.
     - Table 3: Olensky IAC taxonomy incidence in ERA.
     - Table 4: Distribution of Australian HEP co-submissions across the 83,945 collaborative works.
     - Table 5: Multi-index coverage by output type across OpenAlex (Compact & XPAC), Scopus, Trove, and Union.
     - Table 6: Cross-index disjoint intersections (UpSet overlap matrix).
     - Table 7: Comparative external Olensky inaccuracy profiles across OpenAlex, Scopus, and Trove.
     - Table 8: Dual-perspective coupled lifecycle drift vs isolated reporting errors.
     - Table 9: Bibliographic disparity rates by in-text citation frequency (GROBID full-text parse).
     - Table 10: Author and institutional affiliation disparities across ERA outputs.
     - Table 11: Reference resolution disparity taxonomy across full ERA 2018 corpus (15.9M references).
   - Expanded `report/generate_figures.py` generating 7 high-resolution publication figures (both 300-DPI `.png` and vector `.pdf`) in `report/output/figures/`:
     - Figure 1: Output type volume and duplicate rate gradient.
     - Figure 2: HEP-HEP reporting disparities profile.
     - Figure 3: Olensky IAC taxonomy distribution.
     - Figure 4: Distribution of co-reporting HEPs per output.
     - Figure 5: Multi-index coverage rates by ERA research output type (grouped bar chart).
     - Figure 6: Cross-index intersection sets and composition breakdown (UpSet horizontal bar chart).
     - Figure 7: Comparative Olensky IAC type distribution across external indices (stacked bar chart).
   - Test suite expanded to 205 unit tests (100% passing).
15. **GROBID TEI Reference Disparities, Citation Importance, and Author/Institution Analysis (Stage 5 Completed)**:
   - High-throughput parallel extraction (`extract_grobid.py`) across 71,436 TEI XML outputs parsed from full-text PDFs in `/home/lc/k/era_oa_pdfs_tei` (+1,645 new papers):
     - `grobid_papers.parquet`: 71,436 papers with header author strings and citation tallies.
     - `grobid_references.parquet`: 3,420,060 bibliography entries kept strictly separated from canonical ERA records (+68,254).
     - `grobid_mentions.parquet`: 5,378,654 in-text citation mentions with calling context and section headers (`<head>`) (+111,900).
   - Mapped 134,195 internal citations between Australian ERA outputs (+2,800 internal citations) stored in `data/era_grobid_reference_disparities.parquet` (42,398 exact DOI citations + 91,797 exact unique title matches with year tolerance).
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
16. **Assessment Submissions vs Index-Based Bibliometrics (Stage 6 Completed)**:
   - Built empirical comparison between national assessment submissions (ERA 2018 canonical corpus: 455,741 works) and global address-based index records (OpenAlex Oct26: 744,574 works carrying Australian HEP affiliations, 2011–2016).
   - **Inclusion/Omission Landscape**: 401,261 works in ERA (53.9%), 343,313 works not in ERA (46.1%). Document types include 75k non-traditional/ineligible formats vs 202k standard peer-reviewed formats (169,636 journal articles, 29,284 book chapters, 3,476 books).
   - **Citation Network Integration**: Identified 45,822 unsubmitted Australian HEP works directly cited by ERA papers (109,443 citation links), including landmark tools (*limma*, *Grey Wolf Optimizer*, *PLS-SEM*, *scikit-image*).
   - **Institutional Attribution Discordance & Temporal Mobility Gradient**: 67.64% submitting HEP on paper, 18.60% other AU entity, and 13.76% (49,962 works) with zero Australian affiliation. Demonstrated monotonic temporal mobility gradient from 2016 (74.6% HEP on paper, 8.4% foreign) to 2011 (60.5% HEP on paper, 19.4% foreign).
   - Authored LaTeX manuscript: `manuscript/disparities_assessment_vs_index.tex`, compiled to `manuscript/disparities_assessment_vs_index.pdf` (7 pages).
17. **Reference Resolution Error Diagnostics & Metadata-Only Triangulation (Stage 7 Completed)**:
   - **Full-Corpus Reference Resolution Audit (15,939,773 References across 367,655 ERA Papers)**:
     - Executed full DuckDB scan across all 10 compact reference partitions on M-Drive ([`src/era/analysis/run_backward_reference_corpus.py`](file:///home/lc/Projects_Antigravity/Bibliometric_disparities/src/era/analysis/run_backward_reference_corpus.py)).
     - **Forward Anachronisms**: 154,024 references (0.97%), affecting **92,965 papers (25.29% — 1 in 4 papers)**; 59,631 severe anachronisms ($\ge 5$ years future) affecting **44,441 papers (12.09% — 1 in 8 papers)**.
     - **Backward Obscure Historical Outliers (Mean 3)**:
       - $\ge 20$ years old with $\le 5$ citations: **39,620 references (0.25%)**, affecting **25,184 papers (6.85% — 1 in 15 papers)**.
       - $\ge 30$ years old with $\le 5$ citations: **21,736 references (0.14%)**, affecting **13,931 papers (3.79%)**.
       - $\ge 50$ years old with $\le 5$ citations: **7,887 references (0.05%)**, affecting **5,048 papers (1.37%)**.
       - $\ge 100$ years old with $\le 5$ citations: **2,002 references (0.01%)**, affecting **1,396 papers (0.38%)**.
     - **Failure Mode Discovery**: Identified four non-namesake mechanisms generating forward errors: retrospective publisher/OJS platform migration dates, classic monograph hijacking by modern student study guides, reprint drift, and conference volume collisions. In backward errors, isolated short-title and OCR collisions to pre-1910 bulletins (*The Invalid's Lunch-Box*, 1885 anthropological notes).
   - **Search Space Asymmetry Principle**: Empirically proved that historical candidate pools (1850–2011) account for 2,164,731 historical citations (13.58% of all references), hiding tens of thousands of spurious resolution matches that bypass temporal filters.
   - **Implemented Diagnostics & Table 11**:
     - Built [`src/era/analysis/backward_reference_disparities.py`](file:///home/lc/Projects_Antigravity/Bibliometric_disparities/src/era/analysis/backward_reference_disparities.py) implementing all 4 detection engines (MAI, CCD, OHO, TGO).
     - Generated Publication **Table 11: Reference Resolution Disparity Taxonomy Across ERA 2018 Corpus** in standalone `.docx`, `.csv`, `.md`, and assembled into master [`report/output/tables/all_paper_tables.docx`](file:///home/lc/Projects_Antigravity/Bibliometric_disparities/report/output/tables/all_paper_tables.docx).
     - Expanded unit test suite to 205 tests (100% passing).
18. **Simkin & Roychowdhury Citation Mutation-Copying Dynamics (Stage 8 Completed)**:
   - Applied Simkin & Roychowdhury's (2003, 2005) stochastic citation copying model to identical typographical and severe error variants across the 134,195 matched internal citations between Australian ERA research outputs parsed from 71,436 full-text PDFs ([`src/era/analysis/simkin_roychowdhury_grobid.py`](file:///home/lc/Projects_Antigravity/Bibliometric_disparities/src/era/analysis/simkin_roychowdhury_grobid.py)).
   - **Empirical Reading vs. Copying Rates**:
     - **Type 2 Textual Variations (Typos/Indels)**: 7,780 total variants, 6,714 singletons ($k = 1$), 1,066 replicated variants ($k \ge 2$), spanning 10,073 total citations.
     - S&R Reading Rate: **$\hat{\rho} = 66.65\%$** (reading the primary paper).
     - S&R Copying Rate: **$1 - \hat{\rho} = 33.35\%$** (1 in 3 citations carrying a textual typo was copied verbatim from an existing bibliography without reading the primary source).
     - **Combined Flawed Citations**: 10,182 total variants, 9,071 singletons, 1,111 replicated variants across 12,540 citations ($\hat{\rho} = 72.34\%$, copying rate $27.66\%$).
   - **Cross-Institutional Diffusion**:
     - 50.68% (563 lineages) replicated within the same Australian HEP (lab templates / co-authors).
     - **49.32% (548 lineages) diffused across 2 to 13 distinct Australian universities**, demonstrating that citation copying permeates the broader national scholarly network.
   - **Temporal Persistence**:
     - 70.48% (783 lineages) persisted across subsequent publication years (mean span: 1.32 years, maximum 5 years).
   - **In-Text Placement**:
     - Copied variants are disproportionately placed in the **Introduction** (35.16% vs 29.26% for singletons) and less frequent in the **Discussion** (15.08% vs 19.93%), directly validating Olensky's reading effort hypothesis.
   - Built [`tests/test_simkin_roychowdhury.py`](file:///home/lc/Projects_Antigravity/Bibliometric_disparities/tests/test_simkin_roychowdhury.py) expanding test suite to 207 tests (100% passing).
   - Generated Publication **Table 12: Simkin & Roychowdhury Citation Mutation-Copying Dynamics** in standalone `.docx`, `.csv`, `.md`, and assembled into master [`report/output/tables/all_paper_tables.docx`](file:///home/lc/Projects_Antigravity/Bibliometric_disparities/report/output/tables/all_paper_tables.docx).

19. **Pure Gold Open Access Harvest Workbench & Automated Multi-Folder Sweeper**:
    - Engineered [`src/era/sources/fulltext/generate_browser_queue.py`](file:///home/lc/Projects_Antigravity/Bibliometric_disparities/src/era/sources/fulltext/generate_browser_queue.py) to target the 39,325 verified Gold OA journal papers with direct PDF links, formatting as a compact, ultra-fast 1-line-per-URL HTML workbench with multi-tab opening.
    - Built [`src/era/sources/fulltext/sweep_downloads.py`](file:///home/lc/Projects_Antigravity/Bibliometric_disparities/src/era/sources/fulltext/sweep_downloads.py) and test suite [`tests/test_sweep_downloads.py`](file:///home/lc/Projects_Antigravity/Bibliometric_disparities/tests/test_sweep_downloads.py) (210 tests passing).
    - Multi-folder watcher daemon continuously monitors `~/Downloads` and `~/Dropbox/DOWNLOADS`, matching downloaded PDFs via manifest mapping, DOI slugs, and first-page embedded DOI/title extraction to automatically relabel into `/home/lc/k/era_oa_pdfs/<oax_id>.pdf`.
    - Empirically demonstrated that 81.3% of Gold OA articles download directly to `~/Downloads` without viewer interception, with the remaining 18.7% (MDPI/inline) caught seamlessly via the multi-folder sweeper.

20. **Automated Playwright Publisher Harvester & Rate-Limit Engine**:
    - Engineered [`src/era/sources/fulltext/playwright_harvester.py`](file:///home/lc/Projects_Antigravity/Bibliometric_disparities/src/era/sources/fulltext/playwright_harvester.py) and sequential publisher runner [`src/era/sources/fulltext/harvest_proven_publishers.py`](file:///home/lc/Projects_Antigravity/Bibliometric_disparities/src/era/sources/fulltext/harvest_proven_publishers.py) (test suite 100% passing).
    - Configured persistent Google Chrome profile (`~/.config/era_chrome_profile`) with `plugins.always_open_pdf_externally: True`, bypassing Chrome's internal PDF viewer plugin and eliminating manual click/save workflows.
    - Added automated rate-limit detection (`Blocked`, `Access Denied`, `Validate User`, `Just a moment...`) and a 4-consecutive-failure circuit breaker to safeguard against IP throttling.
    - Added strict PDF validation (`is_valid_pdf_file`) checking magic bytes (`%PDF-`), minimum file sizes, and actively filtering out platform navigation brochures/user guides.
    - Executed publisher-by-publisher sequential harvesting with flawless yields:
      - **MDPI** (*Sensors, Nutrients, Energies, IJMS, Polymers*): **1,488 / 1,488 succeeded (100.0%)**
      - **Cell Press** (*Cell, Neuron, Immunity, Cancer Cell, Current Biology, Structure*): **902 / 910 succeeded (99.1%)**
      - **ASM (American Society for Microbiology)**: **840 / 899 succeeded (93.4%)**
      - **ECU Digital Commons** (*Paramedicine & Teacher Education*): **687 / 691 succeeded (99.4%)**
      - **BMJ Open**: **508 / 508 succeeded (100.0%)**
      - **Journal of Biological Chemistry (JBC)**: **335 / 335 succeeded (100.0%)**
      - **AISeL (Association for Information Systems)**: **284 / 286 succeeded (99.3%)**
      - **Journal of Neuroscience**: **265 / 274 succeeded (96.7%)**
      - **RSC (Royal Society of Chemistry)**: **230 succeeded**
      - **PNAS (Proceedings of the National Academy of Sciences)**: **183 succeeded** across 2 rate-limited sessions
      - **ARVO (Association for Research in Vision and Ophthalmology)**: **157 succeeded (91.3%)**
      - **De Gruyter**: **136 / 139 succeeded (97.8%)**
      - **Journal of Dairy Science**: **122 / 122 succeeded (100.0%)**
      - **PeerJ**: **42 / 42 succeeded (100.0%)**
      - **Paramedicine + Oncotarget + Miscellaneous**: **87 direct downloads**
    - Enabled automatic browser context recycling (every 250 papers) and crash recovery ensuring non-stop background harvesting.

21. **Alternative Repositories & Non-Gated Small Publishers Sweep (Completed)**:
    - Built [`src/era/sources/fulltext/harvest_repositories_and_tiny_publishers.py`](file:///home/lc/Projects_Antigravity/Bibliometric_disparities/src/era/sources/fulltext/harvest_repositories_and_tiny_publishers.py) targeting alternative Green OA repositories for gated journal articles and 654 non-gated small publishers with automated 2-strike domain circuit breakers.
    - Isolated institutional repositories by institution name, recovering significant pools:
      - **UNSWorks (University of New South Wales)**: **92 / 92 (100.0%)** direct downloads
      - **Charles Sturt University (CRO)**: **19 / 22 succeeded**
      - **Minerva Access (University of Melbourne)**: **16 succeeded**
      - **Adelaide Research & Scholarship**: **8 succeeded**
      - **Hull Worktribe**: **5 / 5 (100.0%)**
      - **Deep Blue (University of Michigan)**: **6 succeeded**
      - **Manchester, Chalmers, UTIA, Edinburgh, Dundee**: **18 direct repository downloads**
    - Swept all 654 small/independent publishers with high yields on open societies:
      - **BMJ Journals**: **88 / 90 (97.8%)**
      - **World Journal of Gastroenterology (Baishideng/WJG)**: **28 succeeded**
      - **Bioscientifica, AMS, Biologists, OARSI, AJTMH, EarthDoc, Army War College, etc.**: **3,160 successful downloads** in a single autonomous session.

22. **Full-Text TEI Extraction & Citation Network Expansion (Completed)**:
    - Executed high-throughput GROBID 0.9.0 parallel extraction (`run_grobid.py`, 10 workers) across newly acquired PDFs: **10,587 new TEI XML files** successfully generated, bringing the full-text TEI corpus to **84,741 papers** in `/home/lc/k/era_oa_pdfs_tei`.
    - Extracted verbatim reference lists and in-text citation mentions via `extract_grobid.py` in 30.7s (2,763 files/s):
      - **84,741 papers**
      - **4,050,160 bibliography references** (surpassing 4.05 million)
      - **6,350,007 in-text citation mentions** with section context (surpassing 6.35 million)
    - Re-linked GROBID references against canonical ERA outputs (`reference_disparities.py`): internal citations expanded to **159,841 records** (+25,646 new linked citation pairs).
    - Re-computed Simkin & Roychowdhury citation copying dynamics (`simkin_roychowdhury_grobid.py`) and regenerated all 12 publication APA tables and master compilation `report/output/tables/all_paper_tables.docx`.

23. **DSpace & Institutional Repositories Harvest (Completed)**:
    - Built [`src/era/sources/fulltext/harvest_dspace_repositories.py`](file:///home/lc/Projects_Antigravity/Bibliometric_disparities/src/era/sources/fulltext/harvest_dspace_repositories.py) targeting direct PDF repository locations across 362 institutional repositories.
    - Yielded **1,596 net new full-text PDFs** with exceptional success on open university portals:
      - **Research Online (University of Wollongong)**: **550 full-text PDFs**
      - **Murdoch Research Repository (Murdoch University)**: **322 full-text PDFs** (98.8% yield)
      - **Figshare Institutional Repositories**: **198 full-text PDFs** (99.0% yield)
      - **NOVA (University of Newcastle)**: **197 full-text PDFs** (96.1% yield)
      - **eCite Digital Repository (University of Tasmania)**: **106 full-text PDFs** (99.1% yield)
      - **PubMed Central**: **76 direct downloads** (100.0% yield)
      - **UNSWorks, Adelaide, La Trobe, James Cook, USC, Deep Blue, Pereira, Udine, etc.**: **147 additional PDFs**.
    - Maintained fast 2-strike skipping on bot-shielded domains (Melbourne Minerva, Curtin eSpace), processing all 362 repositories cleanly in a single session.

## Current State & Next Steps
- All analytical stages complete and verified. 214 unit tests passing (100%).
- Full texts on disk in `/home/lc/k/era_oa_pdfs/`: **86,789 PDFs** (**+12,217 net new full-text PDFs** acquired autonomously).
- TEI full texts in `/home/lc/k/era_oa_pdfs_tei/`: **84,861+ papers** (actively expanding via multi-threaded GROBID background extraction).
- Manuscripts: `manuscript/disparities_assessment_vs_index.pdf` and 12 publication tables in `report/output/tables/all_paper_tables.docx`.
