# Empirical OpenAlex Disparity Analysis & Olensky Classification

Comprehensive evaluation of discrepancies between the Australian Excellence in Research for Australia (ERA) canonical dataset and matched OpenAlex outputs ($N = 540,353$).

## 1. Title Disparities

Only **264,489 (48.9%)** of matched titles are identical between ERA and OpenAlex. The remaining **51.1%** exhibit systematic Olensky inaccuracies:

| Disparity Category | Olensky Code | Count ($N$) | Rate (%) | Description |
| :--- | :---: | ---: | ---: | :--- |
| Exact Title Match | — | 264,489 | 48.9% | Character-for-character identical |
| Punctuation & Casing | **R** | 219,224 | 40.6% | Title case vs sentence case, hyphenation |
| Cropped Subtitle | **F** | 6,740 | 1.2% | Subtitle omitted in OpenAlex after colon/dash |
| Typographical / Variant | **A** | 33,372 | 6.2% | Spelling conventions (British vs American) |
| Spelling Error / Indel | **B** | 10,252 | 1.9% | Single-character insertion/deletion typos |
| Markup / HTML Entities | **Q** | 233 | 0.0% | Unescaped entities (`&amp;`, `&lt;`) or LaTeX |
| Discordant / Mismatch | **D** | 6,014 | 1.1% | Low token similarity (erroneous join) |

## 2. Publication Year Disparities (Olensky Code T)

Publication year divergence between ERA reporting periods and OpenAlex indexing affects **21.9%** of works:

* **Exact Year Agreement**: 421,992 (78.1%)
* **Off by 1 Year ($\pm 1$)**: 103,056 (19.1%) — reflects advance online publication vs print volume dates.
* **Off by 2+ Years ($\ge 2$)**: 15,153 (2.8%) — reflects delayed institutional reporting or repository upload latency.

## 3. Identifier (DOI) Disparities

* **Exact Matching DOI**: 374,413 (69.3%)
* **DOI Omission (Code E)**: 106,721 (19.8%) — output carries DOI in one source but omitted in the other.
* **DOI Conflict (Code D)**: 1,369 (0.3%) — contradictory valid DOIs.
