"""External Disparity Evaluator: Classify discrepancies between canonical ERA
records and matched OpenAlex works using Olensky's (2015) IAC taxonomy.

Provides two distinct analytical perspectives:
  1. Submission-Level ($N = 540,353$): Evaluates each university's individual
     submitted record against OpenAlex (capturing university-level reporting hygiene).
  2. Canonical Work-Level ($N = 431,842$): Following Olensky's hand-coding
     methodology, multi-HEP duplicate submissions are consolidated into a single
     canonical ERA record per intellectual output before comparing to OpenAlex.
  3. Institutional Divergence: Measures how often co-submitting universities
     receive conflicting disparity classifications for the exact same paper.

Outputs:
  - data/era_openalex_disparities.parquet (submission-level)
  - data/era_openalex_canonical_disparities.parquet (canonical work-level)
  - docs/openalex_disparity_review.md (comparative report)
"""

import html
import re
import time
from pathlib import Path
from typing import Any

import duckdb
import pyarrow as pa
import pyarrow.parquet as pq

from era.config import DATA_DIR, PROJECT_ROOT
from era.core.olensky import single_char_indel_code
from era.sources.openalex.preprocess import detect_subtitle_truncation

_PUNCT_RE = re.compile(r"[^a-zA-Z0-9\s]")
_SPACE_RE = re.compile(r"\s+")
_DOI_PREFIX_RE = re.compile(r"^(https?://(dx\.)?doi\.org/|doi:\s*)", re.IGNORECASE)

PAIRED_PARQUET = DATA_DIR / "era_openalex_paired_records.parquet"
SUBMISSION_DISPARITIES_PARQUET = DATA_DIR / "era_openalex_disparities.parquet"
CANONICAL_DISPARITIES_PARQUET = DATA_DIR / "era_openalex_canonical_disparities.parquet"


def normalize_doi(doi: str | None) -> str | None:
    if not doi or not str(doi).strip():
        return None
    d = str(doi).strip().lower()
    return _DOI_PREFIX_RE.sub("", d).strip()


def classify_title_disparity(t_era: str | None, t_oax: str | None) -> tuple[list[str], str]:
    """Classify the discrepancy between ERA title and OpenAlex title."""
    if not t_era or not t_oax:
        return ["E"], "omitted"

    if t_era == t_oax:
        return [], "exact"

    # Check for HTML entity unescape resolution
    u_era = html.unescape(t_era)
    u_oax = html.unescape(t_oax)
    if (u_era != t_era or u_oax != t_oax) and (u_era == u_oax or u_era.strip().lower() == u_oax.strip().lower()):
        return ["Q"], "markup_or_entity"

    # Check for subtitle truncation / cropping
    if detect_subtitle_truncation(t_era, t_oax):
        return ["F"], "cropped_subtitle"

    # Normalize punctuation and whitespace
    c_era = _SPACE_RE.sub(" ", _PUNCT_RE.sub("", u_era)).strip().lower()
    c_oax = _SPACE_RE.sub(" ", _PUNCT_RE.sub("", u_oax)).strip().lower()

    if c_era == c_oax:
        return ["R"], "punctuation_or_case"

    # Check for single-character indel
    if single_char_indel_code(c_era, c_oax) is not None:
        return ["B"], "spelling_indel"

    # Check token overlap / Jaccard similarity for discordance
    tokens1 = set(c_era.split())
    tokens2 = set(c_oax.split())
    if tokens1 and tokens2:
        jaccard = len(tokens1.intersection(tokens2)) / len(tokens1.union(tokens2))
        if jaccard < 0.25:
            return ["D"], "discordant"

    return ["A"], "typographical_variant"


def classify_year_disparity(y_era: int | None, y_oax: int | None) -> tuple[list[str], int | None]:
    """Classify publication year disparity between ERA and OpenAlex."""
    if y_era is None or y_oax is None:
        return ["E"], None

    try:
        diff = abs(int(y_era) - int(y_oax))
    except (ValueError, TypeError):
        return ["E"], None

    if diff == 0:
        return [], 0
    return ["T"], diff


def classify_doi_disparity(d_era: str | None, d_oax: str | None) -> list[str]:
    """Classify identifier (DOI) discrepancy between ERA and OpenAlex."""
    n_era = normalize_doi(d_era)
    n_oax = normalize_doi(d_oax)

    if n_era and n_oax:
        return [] if n_era == n_oax else ["D"]
    elif n_era or n_oax:
        return ["E"]
    return []


def classify_disparity_profile(
    title_codes: list[str],
    year_diff: int | None,
    doi_codes: list[str],
) -> tuple[str, bool]:
    """Classify the combined disparity profile and whether it exhibits coupled lifecycle drift.

    Coupled lifecycle drift occurs when both publication year and DOI diverge simultaneously,
    characteristic of multi-version preprint-to-published migration.

    Profiles:
      - 'clean': No title, year, or DOI disparities.
      - 'isolated_title_disp': Title disparity only.
      - 'isolated_year_disp': Year disparity only.
      - 'isolated_doi_disp': DOI disparity only.
      - 'title_and_year_disp': Title and year disparity (DOI clean).
      - 'title_and_doi_disp': Title and DOI disparity (Year clean).
      - 'coupled_lifecycle_drift': Coupled year and DOI disparity with clean title.
      - 'coupled_with_title_disp': Coupled year and DOI disparity with title variation.

    Returns:
      tuple of (disparity_profile: str, is_coupled_lifecycle_drift: bool)
    """
    has_title = bool(title_codes)
    has_year = bool(year_diff is not None and year_diff > 0)
    has_doi = bool(doi_codes)

    is_coupled = has_year and has_doi

    if not has_title and not has_year and not has_doi:
        profile = "clean"
    elif is_coupled and not has_title:
        profile = "coupled_lifecycle_drift"
    elif is_coupled and has_title:
        profile = "coupled_with_title_disp"
    elif has_doi and not has_year and not has_title:
        profile = "isolated_doi_disp"
    elif has_year and not has_doi and not has_title:
        profile = "isolated_year_disp"
    elif has_title and not has_year and not has_doi:
        profile = "isolated_title_disp"
    elif has_title and has_year and not has_doi:
        profile = "title_and_year_disp"
    elif has_title and has_doi and not has_year:
        profile = "title_and_doi_disp"
    else:
        profile = "other_combination"

    return profile, is_coupled


def _process_records(rows, cols):
    processed = []
    stats = {
        "total": len(rows),
        "exact_titles": 0,
        "title_r": 0,
        "title_f": 0,
        "title_b": 0,
        "title_q": 0,
        "title_d": 0,
        "title_a": 0,
        "year_exact": 0,
        "year_off1": 0,
        "year_off2plus": 0,
        "year_omitted": 0,
        "doi_exact": 0,
        "doi_omission": 0,
        "doi_conflict": 0,
        "profile_clean": 0,
        "profile_coupled_lifecycle_drift": 0,
        "profile_coupled_with_title_disp": 0,
        "profile_isolated_doi_disp": 0,
        "profile_isolated_year_disp": 0,
        "profile_isolated_title_disp": 0,
        "profile_title_and_year_disp": 0,
        "profile_title_and_doi_disp": 0,
        "profile_other_combination": 0,
        "coupled_lifecycle_drift_total": 0,
    }

    for r in rows:
        rec = dict(zip(cols, r))
        t_codes, t_label = classify_title_disparity(rec["era_title"], rec["oax_title"])
        y_codes, y_diff = classify_year_disparity(rec["era_year"], rec["oax_year"])
        d_codes = classify_doi_disparity(rec["era_doi"], rec["oax_doi"])

        rec["title_olensky_codes"] = t_codes
        rec["title_disparity_label"] = t_label
        rec["year_olensky_codes"] = y_codes
        rec["year_difference"] = y_diff
        rec["doi_olensky_codes"] = d_codes

        profile, is_coupled = classify_disparity_profile(t_codes, y_diff, d_codes)
        rec["is_coupled_lifecycle_drift"] = is_coupled
        rec["disparity_profile"] = profile

        stats[f"profile_{profile}"] += 1
        if is_coupled:
            stats["coupled_lifecycle_drift_total"] += 1

        if not t_codes:
            stats["exact_titles"] += 1
        elif "R" in t_codes:
            stats["title_r"] += 1
        elif "F" in t_codes:
            stats["title_f"] += 1
        elif "B" in t_codes:
            stats["title_b"] += 1
        elif "Q" in t_codes:
            stats["title_q"] += 1
        elif "D" in t_codes:
            stats["title_d"] += 1
        elif "A" in t_codes:
            stats["title_a"] += 1

        if y_diff == 0:
            stats["year_exact"] += 1
        elif y_diff == 1:
            stats["year_off1"] += 1
        elif y_diff and y_diff >= 2:
            stats["year_off2plus"] += 1
        else:
            stats["year_omitted"] += 1

        if not d_codes and rec["era_doi"] and rec["oax_doi"]:
            stats["doi_exact"] += 1
        elif "E" in d_codes:
            stats["doi_omission"] += 1
        elif "D" in d_codes:
            stats["doi_conflict"] += 1

        processed.append(rec)

    return processed, stats


def evaluate_submission_disparities(
    input_pq: Path = PAIRED_PARQUET,
    output_pq: Path = SUBMISSION_DISPARITIES_PARQUET,
) -> dict[str, Any]:
    """Perspective 1: Evaluate each institutional submission individually."""
    t0 = time.time()
    con = duckdb.connect()

    print(f"\n--- Perspective 1: Submission-Level Disparity Evaluation ---")
    print(f"Reading paired records from {input_pq}...")
    rows = con.execute(f"""
        SELECT id, openalex_id, research_output_type, match_type,
               era_title, oax_title, era_year, oax_year, era_doi, oax_doi,
               oax_type, oax_cited_by_count, match_source
        FROM read_parquet('{input_pq}')
    """).fetchall()
    cols = [d[0] for d in con.description]

    print(f"Loaded {len(rows):,} records ({time.time() - t0:.2f}s). Classifying...")
    processed, stats = _process_records(rows, cols)

    table = pa.Table.from_pylist(processed)
    pq.write_table(table, output_pq, compression="snappy")
    print(f"Saved {len(processed):,} submission records to {output_pq} ({time.time() - t0:.2f}s)!")
    return stats


def evaluate_canonical_disparities(
    input_pq: Path = PAIRED_PARQUET,
    output_pq: Path = CANONICAL_DISPARITIES_PARQUET,
) -> dict[str, Any]:
    """Perspective 2: Canonical Work-Level (Olensky Hand-Coding style).
    
    Consolidates multi-HEP duplicate submissions into a single canonical ERA record
    per distinct work before evaluating against OpenAlex.
    """
    t0 = time.time()
    con = duckdb.connect()

    print(f"\n--- Perspective 2: Canonical Work-Level Disparity Evaluation ---")
    print(f"Consolidating multi-HEP records by openalex_id...")
    q = f"""
    SELECT 
        openalex_id,
        first(id) as representative_id,
        count(*) as n_submissions,
        mode(era_title) as era_title,
        coalesce(mode(CASE WHEN era_doi IS NOT NULL AND trim(era_doi) != '' THEN era_doi END), NULL) as era_doi,
        mode(era_year) as era_year,
        first(oax_title) as oax_title,
        first(oax_doi) as oax_doi,
        first(oax_year) as oax_year,
        first(research_output_type) as research_output_type,
        first(oax_type) as oax_type,
        first(oax_cited_by_count) as oax_cited_by_count,
        first(match_type) as match_type
    FROM read_parquet('{input_pq}')
    GROUP BY openalex_id
    """
    rows = con.execute(q).fetchall()
    cols = [d[0] for d in con.description]

    print(f"Consolidated into {len(rows):,} distinct canonical works ({time.time() - t0:.2f}s). Classifying...")
    processed, stats = _process_records(rows, cols)

    table = pa.Table.from_pylist(processed)
    pq.write_table(table, output_pq, compression="snappy")
    print(f"Saved {len(processed):,} canonical work records to {output_pq} ({time.time() - t0:.2f}s)!")
    return stats


def evaluate_institutional_divergence(disparities_pq: Path = SUBMISSION_DISPARITIES_PARQUET) -> dict[str, Any]:
    """Analyze how often co-submitting Australian universities receive conflicting
    disparity classifications for the exact same intellectual work.
    """
    con = duckdb.connect()
    q = f"""
    WITH work_disp AS (
        SELECT 
            openalex_id,
            count(*) as n_submissions,
            count(DISTINCT title_disparity_label) as n_distinct_title_labels,
            count(DISTINCT year_difference) as n_distinct_year_diffs,
            count(DISTINCT CAST(doi_olensky_codes AS VARCHAR)) as n_distinct_doi_codes
        FROM read_parquet('{disparities_pq}')
        GROUP BY openalex_id
        HAVING count(*) > 1
    )
    SELECT 
        count(*) as multi_sub_works,
        sum(case when n_distinct_title_labels > 1 then 1 else 0 end) as divergent_title_works,
        round(100.0 * sum(case when n_distinct_title_labels > 1 then 1 else 0 end) / count(*), 1) as divergent_title_pct,
        sum(case when n_distinct_year_diffs > 1 then 1 else 0 end) as divergent_year_works,
        round(100.0 * sum(case when n_distinct_year_diffs > 1 then 1 else 0 end) / count(*), 1) as divergent_year_pct,
        sum(case when n_distinct_doi_codes > 1 then 1 else 0 end) as divergent_doi_works,
        round(100.0 * sum(case when n_distinct_doi_codes > 1 then 1 else 0 end) / count(*), 1) as divergent_doi_pct
    FROM work_disp
    """
    r = con.execute(q).fetchone()
    return {
        "multi_sub_works": r[0],
        "divergent_title_works": r[1],
        "divergent_title_pct": r[2],
        "divergent_year_works": r[3],
        "divergent_year_pct": r[4],
        "divergent_doi_works": r[5],
        "divergent_doi_pct": r[6],
    }


def generate_openalex_disparity_report(sub_stats, canon_stats, div_stats) -> None:
    """Generate comparative markdown summary contrasting Submission vs Canonical levels."""
    doc_path = PROJECT_ROOT / "docs" / "openalex_disparity_review.md"
    n_sub = sub_stats["total"]
    n_can = canon_stats["total"]

    md = f"""# Empirical OpenAlex Disparity Analysis: Submission vs. Canonical Work Level

Comparative evaluation of discrepancies between the Australian Excellence in Research for Australia (ERA) dataset and matched OpenAlex records across two analytical perspectives:
  * **Perspective 1: Submission-Level** ($N = {n_sub:,}$ institutional submissions): Evaluates each university's submitted metadata individually against OpenAlex.
  * **Perspective 2: Canonical Work-Level** ($N = {n_can:,}$ distinct intellectual works): Consolidates multi-HEP duplicate submissions into a single canonical ERA record before comparison (Olensky's hand-coding methodology).

---

## 1. Title Disparities Comparison

| Disparity Category | Olensky Code | Submission Level ($N={n_sub:,}$) | Sub. Rate (%) | Canonical Work Level ($N={n_can:,}$) | Canon. Rate (%) |
| :--- | :---: | ---: | ---: | ---: | ---: |
| **Exact Title Match** | — | {sub_stats['exact_titles']:,} | {sub_stats['exact_titles']*100.0/n_sub:.1f}% | {canon_stats['exact_titles']:,} | {canon_stats['exact_titles']*100.0/n_can:.1f}% |
| **Punctuation & Casing** | **R** | {sub_stats['title_r']:,} | {sub_stats['title_r']*100.0/n_sub:.1f}% | {canon_stats['title_r']:,} | {canon_stats['title_r']*100.0/n_can:.1f}% |
| **Cropped Subtitle** | **F** | {sub_stats['title_f']:,} | {sub_stats['title_f']*100.0/n_sub:.1f}% | {canon_stats['title_f']:,} | {canon_stats['title_f']*100.0/n_can:.1f}% |
| **Typographical / Variant** | **A** | {sub_stats['title_a']:,} | {sub_stats['title_a']*100.0/n_sub:.1f}% | {canon_stats['title_a']:,} | {canon_stats['title_a']*100.0/n_can:.1f}% |
| **Spelling Error / Indel** | **B** | {sub_stats['title_b']:,} | {sub_stats['title_b']*100.0/n_sub:.1f}% | {canon_stats['title_b']:,} | {canon_stats['title_b']*100.0/n_can:.1f}% |
| **Markup / HTML Entities** | **Q** | {sub_stats['title_q']:,} | {sub_stats['title_q']*100.0/n_sub:.1f}% | {canon_stats['title_q']:,} | {canon_stats['title_q']*100.0/n_can:.1f}% |
| **Discordant / Mismatch** | **D** | {sub_stats['title_d']:,} | {sub_stats['title_d']*100.0/n_sub:.1f}% | {canon_stats['title_d']:,} | {canon_stats['title_d']*100.0/n_can:.1f}% |

---

## 2. Publication Year Disparities (Olensky Code T)

| Year Alignment | Submission Level ($N={n_sub:,}$) | Sub. Rate (%) | Canonical Work Level ($N={n_can:,}$) | Canon. Rate (%) |
| :--- | ---: | ---: | ---: | ---: |
| **Exact Year Agreement** | {sub_stats['year_exact']:,} | {sub_stats['year_exact']*100.0/n_sub:.1f}% | {canon_stats['year_exact']:,} | {canon_stats['year_exact']*100.0/n_can:.1f}% |
| **Off by 1 Year ($\\pm 1$)** | {sub_stats['year_off1']:,} | {sub_stats['year_off1']*100.0/n_sub:.1f}% | {canon_stats['year_off1']:,} | {canon_stats['year_off1']*100.0/n_can:.1f}% |
| **Off by 2+ Years ($\\ge 2$)** | {sub_stats['year_off2plus']:,} | {sub_stats['year_off2plus']*100.0/n_sub:.1f}% | {canon_stats['year_off2plus']:,} | {canon_stats['year_off2plus']*100.0/n_can:.1f}% |

---

## 3. Identifier (DOI) Disparities

| DOI Status | Submission Level ($N={n_sub:,}$) | Sub. Rate (%) | Canonical Work Level ($N={n_can:,}$) | Canon. Rate (%) |
| :--- | ---: | ---: | ---: | ---: |
| **Exact Matching DOI** | {sub_stats['doi_exact']:,} | {sub_stats['doi_exact']*100.0/n_sub:.1f}% | {canon_stats['doi_exact']:,} | {canon_stats['doi_exact']*100.0/n_can:.1f}% |
| **DOI Omission (Code E)** | {sub_stats['doi_omission']:,} | {sub_stats['doi_omission']*100.0/n_sub:.1f}% | {canon_stats['doi_omission']:,} | {canon_stats['doi_omission']*100.0/n_can:.1f}% |
| **DOI Conflict (Code D)** | {sub_stats['doi_conflict']:,} | {sub_stats['doi_conflict']*100.0/n_sub:.1f}% | {canon_stats['doi_conflict']:,} | {canon_stats['doi_conflict']*100.0/n_can:.1f}% |

---

## 4. Institutional Disparity Divergence across Co-Submitting HEPs

Evaluated across the **{div_stats['multi_sub_works']:,} multi-HEP works** co-submitted by two or more Australian universities:

* **Divergent Title Classifications**: **{div_stats['divergent_title_works']:,} works ({div_stats['divergent_title_pct']:.1f}%)** receive conflicting disparity ratings against OpenAlex depending on which university's submission is evaluated (e.g. University A matches exactly, while University B is flagged with Code R or Code F).
* **Divergent DOI Classifications**: **{div_stats['divergent_doi_works']:,} works ({div_stats['divergent_doi_pct']:.1f}%)** have conflicting DOI presence across submitting universities (one university provides the DOI while another omits it).
* **Divergent Year Classifications**: **{div_stats['divergent_year_works']:,} works ({div_stats['divergent_year_pct']:.1f}%)** have differing reference years reported across universities for the same publication.

---

## 5. Disparity Profiles & Coupled Lifecycle Drift Analysis

| Disparity Profile | Description | Submission Level ($N={n_sub:,}$) | Sub. Rate (%) | Canonical Work Level ($N={n_can:,}$) | Canon. Rate (%) |
| :--- | :--- | ---: | ---: | ---: | ---: |
| **Clean (No Disparities)** | Exact title, year, and DOI agreement | {sub_stats['profile_clean']:,} | {sub_stats['profile_clean']*100.0/n_sub:.1f}% | {canon_stats['profile_clean']:,} | {canon_stats['profile_clean']*100.0/n_can:.1f}% |
| **Isolated Title Disparity** | Title varies, year and DOI match | {sub_stats['profile_isolated_title_disp']:,} | {sub_stats['profile_isolated_title_disp']*100.0/n_sub:.1f}% | {canon_stats['profile_isolated_title_disp']:,} | {canon_stats['profile_isolated_title_disp']*100.0/n_can:.1f}% |
| **Isolated Year Disparity** | Publication year off, title and DOI match | {sub_stats['profile_isolated_year_disp']:,} | {sub_stats['profile_isolated_year_disp']*100.0/n_sub:.1f}% | {canon_stats['profile_isolated_year_disp']:,} | {canon_stats['profile_isolated_year_disp']*100.0/n_can:.1f}% |
| **Isolated DOI Disparity** | DOI omitted/conflicted, title and year match | {sub_stats['profile_isolated_doi_disp']:,} | {sub_stats['profile_isolated_doi_disp']*100.0/n_sub:.1f}% | {canon_stats['profile_isolated_doi_disp']:,} | {canon_stats['profile_isolated_doi_disp']*100.0/n_can:.1f}% |
| **Coupled Lifecycle Drift** | Year and DOI diverge, title matches exactly | {sub_stats['profile_coupled_lifecycle_drift']:,} | {sub_stats['profile_coupled_lifecycle_drift']*100.0/n_sub:.1f}% | {canon_stats['profile_coupled_lifecycle_drift']:,} | {canon_stats['profile_coupled_lifecycle_drift']*100.0/n_can:.1f}% |
| **Coupled Drift with Title Var.** | Year, DOI, and title all diverge | {sub_stats['profile_coupled_with_title_disp']:,} | {sub_stats['profile_coupled_with_title_disp']*100.0/n_sub:.1f}% | {canon_stats['profile_coupled_with_title_disp']:,} | {canon_stats['profile_coupled_with_title_disp']*100.0/n_can:.1f}% |
| **Title & Year Disparity** | Title and year diverge, DOI matches | {sub_stats['profile_title_and_year_disp']:,} | {sub_stats['profile_title_and_year_disp']*100.0/n_sub:.1f}% | {canon_stats['profile_title_and_year_disp']:,} | {canon_stats['profile_title_and_year_disp']*100.0/n_can:.1f}% |
| **Title & DOI Disparity** | Title and DOI diverge, year matches | {sub_stats['profile_title_and_doi_disp']:,} | {sub_stats['profile_title_and_doi_disp']*100.0/n_sub:.1f}% | {canon_stats['profile_title_and_doi_disp']:,} | {canon_stats['profile_title_and_doi_disp']*100.0/n_can:.1f}% |
| **Total Coupled Lifecycle Drift** | All records where both year and DOI diverge ($Year > 0 \\land DOI \\ne Match$) | {sub_stats['coupled_lifecycle_drift_total']:,} | {sub_stats['coupled_lifecycle_drift_total']*100.0/n_sub:.1f}% | {canon_stats['coupled_lifecycle_drift_total']:,} | {canon_stats['coupled_lifecycle_drift_total']*100.0/n_can:.1f}% |
"""

    with open(doc_path, "w", encoding="utf-8") as f:
        f.write(md)
    print(f"\nComparative summary report written to {doc_path}")


def main():
    sub_stats = evaluate_submission_disparities()
    canon_stats = evaluate_canonical_disparities()
    div_stats = evaluate_institutional_divergence()
    generate_openalex_disparity_report(sub_stats, canon_stats, div_stats)


if __name__ == "__main__":
    main()
