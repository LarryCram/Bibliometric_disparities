# Multi-Index Bibliometric Synthesis: ERA vs. OpenAlex, Scopus & Trove

Empirical synthesis of all **455,741 canonical Australian research outputs** from the ERA 2011–2016 collection linked against:
1. **OpenAlex (Oct 2026 Snapshot)**: Hydrated from compact and extended XPAC exports.
2. **Scopus (post title-harvest)**: 479,620 deduplicated Scopus records incorporating DOI and non-DOI title searches.
3. **Trove (National Library of Australia)**: Targeted ISBN matching and candidate title queries for books and non-traditional research outputs (NTROs).

---

## 1. Overall Multi-Index Coverage by Output Type

The multi-index union indexes **95.73%** (436,273 of 455,741) of all Australian academic outputs:

| Research Output Type | Canonical Works | OpenAlex Matched | Scopus Matched | Trove Matched | Union (Any Index) | ERA Only (Unmatched) |
| :--- | ---: | ---: | ---: | ---: | ---: | ---: |
| **Journal Article** | 340,779 | 332,767 (97.6%) | 315,579 (92.6%) | 8 (0.0%) | **335,566 (98.5%)** | 5,213 (1.5%) |
| **Conference Publication** | 56,720 | 51,323 (90.5%) | 18,756 (33.1%) | 16 (0.0%) | **51,350 (90.5%)** | 5,370 (9.5%) |
| **Book Chapter** | 43,150 | 34,544 (80.1%) | 7,624 (17.7%) | 26,324 (61.0%) | **37,748 (87.5%)** | 5,402 (12.5%) |
| **Book** | 5,117 | 4,413 (86.2%) | 482 (9.4%) | 4,494 (87.8%) | **4,981 (97.3%)** | 136 (2.7%) |
| **Original Creative Work** | 3,877 | 1,541 (39.7%) | 13 (0.3%) | 2,081 (53.7%) | **2,506 (64.6%)** | 1,371 (35.4%) |
| **Research Report** | 3,208 | 2,332 (72.7%) | 22 (0.7%) | 2,355 (73.4%) | **2,633 (82.1%)** | 575 (17.9%) |
| **Recorded Rendered Work** | 830 | 348 (41.9%) | 1 (0.1%) | 518 (62.4%) | **556 (67.0%)** | 274 (33.0%) |
| **Live Performance** | 826 | 262 (31.7%) | 0 (0.0%) | 413 (50.0%) | **437 (52.9%)** | 389 (47.1%) |
| **Portfolio** | 689 | 63 (9.1%) | 1 (0.1%) | 0 (0.0%) | **63 (9.1%)** | 626 (90.9%) |
| **Curated Exhibition Event** | 545 | 289 (53.0%) | 0 (0.0%) | 405 (74.3%) | **433 (79.4%)** | 112 (20.6%) |
| **Total** | **455,741** | **427,882 (93.9%)** | **342,478 (75.1%)** | **36,614 (8.0%)** | **436,273 (95.7%)** | **19,468 (4.3%)** |

---

## 2. Cross-Index Intersection Breakdown (UpSet Overlap)

| Coverage Category | Distinct Works | Share (%) | Journals | Conferences | Books & Chapters | NTROs |
| :--- | ---: | ---: | ---: | ---: | ---: | ---: |
| **OpenAlex & Scopus** | 334,911 | 73.49% | 312,772 | 18,717 | 3,412 | 10 |
| **OpenAlex Only** | 61,829 | 13.57% | 19,987 | 32,590 | 8,406 | 846 |
| **OpenAlex & Trove** | 26,566 | 5.83% | 0 | 4 | 22,609 | 3,953 |
| **ERA Only (Unmatched)** | 19,468 | 4.27% | 5,213 | 5,370 | 5,538 | 3,347 |
| **Trove Only** | 5,400 | 1.18% | 0 | 0 | 3,608 | 1,792 |
| **All Three Indices** | 4,576 | 1.00% | 8 | 12 | 4,530 | 26 |
| **Scopus Only** | 2,919 | 0.64% | 2,799 | 27 | 93 | 0 |
| **Scopus & Trove** | 72 | 0.02% | 0 | 0 | 71 | 1 |

---

## 3. Disaggregating OpenAlex: Compact vs. XPAC Contributions

Across the 455,741 canonical ERA works:
- **Compact Matches**: 422,682 (92.75%)
- **XPAC Matches**: 30,790 (6.76%)
- **Incremental XPAC Gain**: **5,200 canonical works (+1.14%)**

XPAC provides critical recovery for NTROs and non-journal outputs:
- Curated Exhibition Events: **+19.6%**
- Original Creative Works: **+15.0%**
- Recorded Rendered Works: **+12.8%**
- Live Performances: **+5.8%**
- Research Reports: **+5.7%**
- Books & Book Chapters: **+3.2% to +3.3%**

---

## 4. Multi-DOI Work Locations & Disparity Resolution

Analysis of the 1,898 partitions of OpenAlex `work_locations`:
1. **Match Recovery**: 265 previously unmatched ERA DOIs were recovered via secondary/alternative location records.
2. **Disparity Resolution**: When co-authoring Australian universities submitted conflicting DOIs (Olensky code D), OpenAlex unifies them under the identical `work_idx`.
3. **Underlying Mechanisms**:
   - Preprint vs. final publisher DOIs (e.g. bioRxiv/SSRN vs journal).
   - Publisher discussion forum pairs (e.g. Copernicus *Discussions* vs *Journal*).
   - Dual platform aggregator co-registrations (e.g. Project MUSE vs JSTOR).
   - Book monograph vs edited chapter DOIs.

---

## 5. Comparative External Olensky Inaccuracy Classification (IAC)

| Index Source | Matched Works | Exact Match (%) | Punct / Case (%) [R] | Typo Variant (%) [A] | Spelling Indel (%) [B] | Cropped Subtitle (%) [F] | Discordant (%) [D] | Type 1 (Surface) | Type 2 (Textual) | Type 3 (Severe) |
| :--- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| **OpenAlex** | 536,707 | 49.55% | 42.09% | 3.69% | 2.93% | 1.29% | 0.44% | **91.66%** | **7.90%** | **0.44%** |
| **Scopus** | 342,478 | 0.02% | 94.78% | 3.13% | 1.69% | 0.11% | 0.22% | **94.84%** | **4.94%** | **0.22%** |
| **Trove** | 36,614 | 0.14% | 79.47% | 11.40% | 0.16% | 4.94% | 3.89% | **79.61%** | **16.49%** | **3.89%** |
