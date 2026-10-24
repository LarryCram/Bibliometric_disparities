"""Orchestration script for Stage 3: Multi-Index Downstream Metrics & Analysis."""

from pathlib import Path
import time
from typing import Any
import duckdb

from era.config import DATA_DIR, PROJECT_ROOT
from era.analysis.multi_index_coverage import (
    build_openalex_canonical_matches,
    build_multi_index_synthesis,
    calculate_output_type_coverage,
    calculate_intersection_metrics,
    SYNTHESIS_PARQUET,
)
from era.analysis.comparative_olensky import (
    calculate_comparative_olensky_taxonomy,
    calculate_olensky_by_output_type,
)

REPORT_OUTPUT_DIR = PROJECT_ROOT / "report" / "output"


def run_stage3():
    print("=" * 80)
    print("STARTING STAGE 3: MULTI-INDEX SYNTHESIS & DOWNSTREAM METRICS")
    print("=" * 80)
    t_start = time.time()

    con = duckdb.connect()
    con.execute("PRAGMA memory_limit='24GB'")

    # Step 1: Build OpenAlex canonical matches
    print("\n--- Step 1: OpenAlex Canonical Rollup ---")
    n_oa_canon = build_openalex_canonical_matches(con)

    # Step 2: Build unified multi-index synthesis table
    print("\n--- Step 2: Multi-Index Synthesis Dataset ---")
    n_synthesis = build_multi_index_synthesis(con)

    # Step 3: Multi-Index Coverage by ERA Output Type
    print("\n--- Step 3: Output Type Coverage Breakdown ---")
    coverage_rows = calculate_output_type_coverage(con, export_csv=True)
    hdr1 = (
        "Output Type".ljust(34)
        + "Canonical".rjust(10)
        + "OpenAlex".rjust(16)
        + "Scopus".rjust(16)
        + "Trove".rjust(16)
        + "Union (Any)".rjust(16)
        + "ERA Only".rjust(14)
    )
    print(hdr1)
    print("-" * len(hdr1))
    for r in coverage_rows:
        oa_s = f"{r['n_openalex']:,} ({r['pct_openalex']}%)"
        sc_s = f"{r['n_scopus']:,} ({r['pct_scopus']}%)"
        tr_s = f"{r['n_trove']:,} ({r['pct_trove']}%)"
        un_s = f"{r['n_union']:,} ({r['pct_union']}%)"
        eo_s = f"{r['n_era_only']:,} ({r['pct_era_only']}%)"
        line = (
            r['output_type'].ljust(34)
            + f"{r['total_canonical']:,}".rjust(10)
            + oa_s.rjust(16)
            + sc_s.rjust(16)
            + tr_s.rjust(16)
            + un_s.rjust(16)
            + eo_s.rjust(14)
        )
        print(line)

    # Step 4: UpSet / Cross-Index Intersections
    print("\n--- Step 4: Cross-Index Intersection Breakdown (UpSet) ---")
    intersect_rows = calculate_intersection_metrics(con, export_csv=True)
    hdr2 = (
        "Coverage Category".ljust(26)
        + "Count".rjust(10)
        + "Pct (%)".rjust(9)
        + "Journals".rjust(12)
        + "Conferences".rjust(13)
        + "Books/Chap".rjust(12)
        + "NTRO".rjust(10)
    )
    print(hdr2)
    print("-" * len(hdr2))
    for r in intersect_rows:
        line = (
            r['coverage_category'].ljust(26)
            + f"{r['work_count']:,}".rjust(10)
            + f"{r['pct_total']:.2f}%".rjust(9)
            + f"{r['n_journal_articles']:,}".rjust(12)
            + f"{r['n_conferences']:,}".rjust(13)
            + f"{r['n_books_chapters']:,}".rjust(12)
            + f"{r['n_ntro']:,}".rjust(10)
        )
        print(line)

    # Step 5: Comparative Olensky Inaccuracy Distributions
    print("\n--- Step 5: Comparative Olensky Inaccuracy Distributions ---")
    olensky_rows = calculate_comparative_olensky_taxonomy(con, export_csv=True)
    hdr3 = (
        "Index Source".ljust(12)
        + "Total Matched".rjust(14)
        + "Exact (%)".rjust(12)
        + "Punct/Case(%)".rjust(14)
        + "Typo (%)".rjust(11)
        + "Indel (%)".rjust(11)
        + "Cropped (%)".rjust(13)
        + "Discord (%)".rjust(13)
        + "Type 1(%)".rjust(11)
        + "Type 2(%)".rjust(11)
        + "Type 3(%)".rjust(11)
    )
    print(hdr3)
    print("-" * len(hdr3))
    for r in olensky_rows:
        line = (
            r['source'].ljust(12)
            + f"{r['total_matched']:,}".rjust(14)
            + f"{r['pct_exact']:.2f}%".rjust(12)
            + f"{r['pct_punctuation']:.2f}%".rjust(14)
            + f"{r['pct_typo']:.2f}%".rjust(11)
            + f"{r['pct_indel']:.2f}%".rjust(11)
            + f"{r['pct_cropped']:.2f}%".rjust(13)
            + f"{r['pct_discordant']:.2f}%".rjust(13)
            + f"{r['pct_type1_surface']:.2f}%".rjust(11)
            + f"{r['pct_type2_textual']:.2f}%".rjust(11)
            + f"{r['pct_type3_discordant']:.2f}%".rjust(11)
        )
        print(line)

    # Step 6: Olensky by Output Type
    print("\n--- Step 6: Olensky Distributions by Output Type ---")
    calculate_olensky_by_output_type(con, export_csv=True)

    # Step 7: Write Markdown Summary Report
    REPORT_OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    report_file = REPORT_OUTPUT_DIR / "stage3_synthesis_summary.md"
    write_markdown_summary(report_file, coverage_rows, intersect_rows, olensky_rows)
    print(f"\nWritten Stage 3 comprehensive report to: {report_file}")

    con.close()
    print("\n" + "=" * 80)
    print(f"STAGE 3 COMPLETED SUCCESSFULLY IN {time.time()-t_start:.2f}s!")
    print("=" * 80)


def write_markdown_summary(
    report_path: Path,
    coverage_rows: list[dict[str, Any]],
    intersect_rows: list[dict[str, Any]],
    olensky_rows: list[dict[str, Any]],
):
    with open(report_path, "w", encoding="utf-8") as f:
        f.write("# Stage 3 Multi-Index Synthesis & Coverage Report\n\n")
        f.write("Synthesizing ERA 2018 Canonical Baseline (455,741 records) against **OpenAlex (Oct26)**, **Scopus (post title harvest)**, and **Trove**.\n\n")

        f.write("## 1. Multi-Index Coverage by ERA Research Output Type\n\n")
        f.write("| Research Output Type | Canonical Works | OpenAlex Matched | Scopus Matched | Trove Matched | Union (Any Index) | ERA Only (Unmatched) |\n")
        f.write("| :--- | :--- | :--- | :--- | :--- | :--- | :--- |\n")
        for r in coverage_rows:
            f.write(
                f"| **{r['output_type']}** | {r['total_canonical']:,} | "
                f"{r['n_openalex']:,} ({r['pct_openalex']}%) | "
                f"{r['n_scopus']:,} ({r['pct_scopus']}%) | "
                f"{r['n_trove']:,} ({r['pct_trove']}%) | "
                f"**{r['n_union']:,} ({r['pct_union']}%)** | "
                f"{r['n_era_only']:,} ({r['pct_era_only']}%) |\n"
            )

        f.write("\n## 2. Cross-Index Intersection Breakdown (UpSet Matrix)\n\n")
        f.write("| Coverage Category | Total Works | Share (%) | Journal Articles | Conference Pubs | Books & Chapters | NTROs |\n")
        f.write("| :--- | :--- | :--- | :--- | :--- | :--- | :--- |\n")
        for r in intersect_rows:
            f.write(
                f"| **{r['coverage_category']}** | {r['work_count']:,} | {r['pct_total']:.2f}% | "
                f"{r['n_journal_articles']:,} | {r['n_conferences']:,} | {r['n_books_chapters']:,} | {r['n_ntro']:,} |\n"
            )

        f.write("\n## 3. Comparative Olensky Inaccuracy Taxonomy Across Indices\n\n")
        f.write("| Index Source | Total Matched | Exact Match (%) | Punct / Case (%) | Typo Variant (%) | Spelling Indel (%) | Cropped Subtitle (%) | Discordant (%) | Type 1 (Surface) (%) | Type 2 (Textual) (%) | Type 3 (Severe) (%) |\n")
        f.write("| :--- | :--- | :--- | :--- | :--- | :--- | :--- | :--- | :--- | :--- | :--- |\n")
        for r in olensky_rows:
            f.write(
                f"| **{r['source']}** | {r['total_matched']:,} | {r['pct_exact']:.2f}% | {r['pct_punctuation']:.2f}% | "
                f"{r['pct_typo']:.2f}% | {r['pct_indel']:.2f}% | {r['pct_cropped']:.2f}% | {r['pct_discordant']:.2f}% | "
                f"{r['pct_type1_surface']:.2f}% | {r['pct_type2_textual']:.2f}% | {r['pct_type3_discordant']:.2f}% |\n"
            )


if __name__ == "__main__":
    run_stage3()
