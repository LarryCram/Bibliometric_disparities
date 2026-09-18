# Data Quality Characteristics of the ARC Excellence in Research for Australia (ERA) Dataset

Lawrence Cram

## Abstract

This note documents structural and quality characteristics of the Excellence in Research for Australia (ERA) research-output dataset used as an independent reference set in a broader study of bibliometric discrepancies in OpenAlex. The dataset comprises 567,647 records submitted by Australian universities across the 2011-2016 reference-year window, spanning ten output types from Journal Article through to Curated Exhibition Event. We report record counts and apparent duplicate rates by output type, and the distribution of each type across reference years. Duplicate records - detected here as multiple records sharing an identical normalised title within the same output type - are concentrated in Journal Article (19.4%) and are markedly rarer among the small creative and performance-based categories. One category, Portfolio, carries no reference-year value for any of its 690 records. These characteristics inform how the dataset should be sampled and interpreted in subsequent matching work against OpenAlex, Web of Science, Scopus, and Trove.

**Keywords:** Excellence in Research for Australia; ERA; bibliometrics; research output classification; duplicate records; data quality; Australian higher education

## Introduction

*[Placeholder - Introduction to be written.]*

## Framework: Olensky's Taxonomy of Bibliographic Inaccuracies

This study adopts the analytical framework developed by Olensky (2015) in her doctoral thesis *Data Accuracy in Bibliometric Data Sources and its Impact on Citation Matching* (Humboldt-Universität zu Berlin). Olensky's central methodological move is directly analogous to the one adopted here: rather than simply counting whether a citation is matched or unmatched by an index (Web of Science, in her case), she assessed the bibliographic data values themselves against a trusted reference - the original cited article - and classified every discrepancy she found into a structured taxonomy. This reframes "does the source find it" as "what, specifically, is wrong with the source's data, and why does that particular kind of wrongness break matching." The present study applies the same reframing to OpenAlex, using ERA as the trusted reference in place of the original article.

Olensky's taxonomy organises 25 primary inaccuracy codes (IACs) into three main groups and nine subgroups. The grouping is based not on how severe an error looks to a human reader, but on how sophisticated a data-manipulation or matching rule would need to be to recognise the assessed value as (part of) the correct one: **Type 1** codes mark a field that contains a correct value in full; **Type 2** codes mark a field that contains only part of a correct value; **Type 3** codes mark a field that does not contain a correct value at all. Table 1 lists all 25 codes; the code *G (Interchanged fields)* additionally carries seven sub-codes (G1-G7) that record which pair of fields - issue number, starting page, ending page, volume number, last name, first initial, second initial - was swapped, bringing the full codebook to 32 codes in total.

**Table 1: Olensky's (2015) taxonomy of bibliographic inaccuracy codes (IACs)**

| Main group | Subgroup | Code | Name | Type | Description |
|---|---|---|---|---|---|
| Simple | Added data values | K | Space | 1 | Extra or missing space character |
| Simple | Added data values | L | Informational letter | 1 | Disambiguating letter added to a numeric field (e.g. "2003a") |
| Simple | Added data values | N | Additional information | 1 | Correct but non-essential information not part of the correct value (e.g. "in press", a translation) |
| Simple | Added data values | R | Punctuation | 1 | Differing punctuation |
| Simple | Added data values | S | Padded | 1 | Correct value plus extraneous added characters or digits |
| Simple | Added data values | U | Full first name | 1 | Full first name given where only an initial was expected |
| Simple | Disarranged data values | G (+G1-G7) | Interchanged fields | 1 | Values from two fields swapped; sub-codes track which fields |
| Simple | Disarranged data values | H | Jumbled value | 1 | Correct components present but internally reordered |
| Simple | Disarranged data values | O | Incorrect order of authors | 1 | Authors listed in the wrong sequence |
| Moderate | Incorrect interpretation of data values | M | Incorrect interpretation of author names | 2 | Part of a name misread into the wrong field |
| Moderate | Incorrect interpretation of data values | V | Incorrect interpretation of additional information | 2 | Non-name information (e.g. an affiliation) misread as part of a field |
| Moderate | Spelling variations | A | Typographical variation | 2 | Spelling-convention variant (British/American, transliteration) |
| Moderate | Spelling variations | B | Spelling error | 2 | Misspelling within a 2-character-edit threshold |
| Moderate | Spelling variations | Q | Special character | 2 | Diacritics, non-Latin characters, Roman numerals |
| Moderate | Spelling variations | Y | Word stem | 2 | Same word stem, different ending, exceeding the 2-edit threshold |
| Moderate | Abbreviated data values | F | Cropped | 2 | Value incomplete at the start or end (missing subtitle, "et al.") |
| Moderate | Abbreviated data values | I | Abbreviation | 2 | Abbreviated form given where the full form (or vice versa) was expected |
| Moderate | Other variations | J | Partially incorrect | 2 | Value recognisable but garbled mid-string, beyond a spelling error |
| Moderate | Other variations | T | Plus/Minus | 2 | Value off by +-1 or 2 via a simple arithmetic operation |
| Moderate | Other variations | X | Stop word | 2 | Omission, addition, or misspelling of a stop word |
| Complex | Not assessable | C | Different language | 3 | Value is in a different language from the target |
| Complex | Not assessable | Z | Not available | 3 | Target value itself is missing, so the source value is unassessable |
| Complex | Missing data values | E | Omitted | 3 | Value completely missing from the source |
| Complex | Missing data values | P | No author name | 3 | Author name replaced by a citation-style placeholder (e.g. "ibid.", "idem.") |
| Complex | Completely incorrect | D | Completely incorrect | 3 | Value bears no recoverable relationship to the correct one |

Capitalization has no corresponding code, but the reason is narrower than "case doesn't matter": her automatic accuracy assessment used a hand-built Levenshtein distance function ("the Levenshtein distance function is not a standard MySQL functionality, it was programmed and added manually"), and that specific implementation had a technical inability to detect case differences, as well as 22 of 91 tested Latin1-Supplement special characters (e.g. é, è, ñ) - a limitation of her 2012 tool, not a stated position that capitalization is bibliographically insignificant (Olensky, 2015, p.58). A worked example in her thesis (Table 27, IAC Y) is consistent with this: two article titles differing in case throughout are compared without the case difference being flagged, only an unrelated word-stem difference is coded.

This points to a broader methodological fact worth being explicit about: her codebook (chapter 6) only ever assesses discrepancies that survive an automated Levenshtein-based screening pass (Part A of her process) - "only inaccuracies that the Levenshtein distance function can detect on a technical level were considered." Since that same automated pass replaced all punctuation with spaces and collapsed repeated spaces before comparison, a value pair differing *only* by punctuation or *only* by whitespace run-length would look identical to her detector and never be flagged for the human coding in Part B - her own worked example of a combination code, "A K" (a spelling variation co-occurring with a space discrepancy), is consistent with K only ever appearing alongside some other already-detected difference, not on its own. The present study does not replicate that two-stage screen-then-code pipeline: every raw field value here is classified directly and exhaustively, without an automated pre-filter gating what gets assessed. Olensky's code *definitions* and Type 1/2/3 structure are adopted as the classification vocabulary; her specific detection methodology, and therefore her published code frequencies, are not being reproduced, and are not directly comparable to the frequencies reported in this study.

This three-tier structure - simple/moderate/complex, defined by matching difficulty rather than perceived severity - is a useful lens for the present project's own findings. Several of the disparities already characterised in this study's exploratory work map cleanly onto Olensky's categories even though they arise from institutional-repository harvesting and free-text search rather than citation parsing: Trove's non-tolerance of a single missing letter mirrors the *Spelling variations* threshold problem; the multi-institution duplicate titles reported in the ERA Data section below are a *Disarranged/Added*-adjacent phenomenon at the record level rather than the field level; and OpenAlex's outright non-matches for a subset of ERA Book Chapters are candidates for the *Complex* group once their underlying cause is established. Adopting Olensky's vocabulary, rather than inventing a parallel one, is intended to let this study's OpenAlex-disparity findings be read alongside the existing citation-matching-accuracy literature.

## ERA Data

### Overview

The ERA dataset comprises 567,647 research-output records, each carrying an output type, a reporting institution, a title, an outlet, and a reference year (with one exception noted below). Ten output types are represented, ranging from 443,879 Journal Article records (78.2% of the dataset) down to 546 Curated Exhibition Event records (0.1%). The two largest categories, Journal Article and Conference Publication, together account for 89.2% of all records; the remaining eight types - Book Chapter, Book, Original Creative Work, Research Report for External Body, Recorded Rendered Work, Live Performance, Portfolio, and Curated Exhibition Event - make up the remaining 10.8% and are collectively of particular interest because they are structurally under-covered by journal-indexing services such as Web of Science and Scopus.

### Output Type Counts and Duplicate Records

Table 1 reports, for each output type, the total record count, the count of distinct normalised titles (lower-cased, whitespace-trimmed) within that type, and the resulting duplicate count and rate. A record is counted as a duplicate if its normalised title matches that of at least one other record of the same output type. This is a conservative, title-only measure: it will under-count duplicates that differ by minor punctuation or formatting, and it cannot distinguish a genuine duplicate from two distinct works that happen to share a title. It is, however, well suited to detecting the dominant known source of duplication in ERA - the same research output being independently submitted by two or more collaborating institutions under an identical title.

| Output type | Records | Distinct titles | Duplicates | Duplicate rate |
|---|---:|---:|---:|---:|
| Journal Article | 443,879 | 357,642 | 86,237 | 19.4% |
| Conference Publication | 62,371 | 56,972 | 5,399 | 8.7% |
| Book Chapter | 45,910 | 43,046 | 2,864 | 6.2% |
| Book | 5,427 | 5,128 | 299 | 5.5% |
| Original Creative Work | 3,900 | 3,850 | 50 | 1.3% |
| Research Report for External Body | 3,260 | 3,212 | 48 | 1.5% |
| Recorded Rendered Work | 835 | 828 | 7 | 0.8% |
| Live Performance | 829 | 826 | 3 | 0.4% |
| Portfolio | 690 | 689 | 1 | 0.1% |
| Curated Exhibition Event | 546 | 544 | 2 | 0.4% |
| **Total** | **567,647** | - | **94,910** | **16.7%** |

The duplicate rate falls off sharply with output-type size, from 19.4% for Journal Article to under 1.5% for every category smaller than Book. This gradient is consistent with duplication arising primarily from multi-institutional co-authorship: journal articles are both the most numerous and, typically, the most collaboratively authored output type, giving the greatest opportunity for the same title to be submitted by several institutions. The creative and performance-based categories, by contrast, are predominantly single-creator, single-institution outputs, and their near-zero duplicate rates are consistent with that.

### Output Type by Reference Year

Table 2 gives the count of each output type by reference year. All types except Portfolio carry a reference year in the range 2011-2016.

| Output type | 2011 | 2012 | 2013 | 2014 | 2015 | 2016 |
|---|---:|---:|---:|---:|---:|---:|
| Journal Article | 61,798 | 67,595 | 73,626 | 79,259 | 80,782 | 80,819 |
| Conference Publication | 11,040 | 11,378 | 11,150 | 10,323 | 9,979 | 8,501 |
| Book Chapter | 6,803 | 7,501 | 7,990 | 8,033 | 8,141 | 7,442 |
| Book | 810 | 889 | 998 | 890 | 930 | 910 |
| Original Creative Work | 581 | 647 | 771 | 591 | 656 | 654 |
| Research Report for External Body | 519 | 534 | 635 | 554 | 527 | 491 |
| Recorded Rendered Work | 100 | 116 | 177 | 150 | 159 | 133 |
| Live Performance | 130 | 153 | 150 | 134 | 132 | 130 |
| Curated Exhibition Event | 84 | 100 | 102 | 85 | 99 | 76 |
| Portfolio | - | - | - | - | - | - |

Journal Article shows a steady year-on-year increase across the whole window (61,798 to 80,819, a 30.8% rise), while Conference Publication shows the opposite trend, declining from 11,040 to 8,501 (-23.0%). The remaining types fluctuate within a comparatively narrow band from year to year without a clear directional trend. Portfolio's complete absence of a reference year across all 690 of its records is a data-quality anomaly distinct from the duplication patterns above: it is not a sparse or partially-populated field but a field that is entirely unpopulated for this one output type, and any year-based analysis of Portfolio records will need to source a reference year elsewhere or exclude the category.
