"""Full-Corpus Backward Reference Resolution Disparity Analysis.

Evaluates all 15,939,773 resolved references across the 367,655 Australian ERA papers
in OpenAlex (compact schema on M-Drive).

Quantifies:
  1. Temporal Disparity Landscape:
     - Normal Recent References (0 <= Delta_T < 20y)
     - Forward Anachronisms (Delta_T < 0)
     - Historical References (Delta_T >= 20y)
  2. Backward Obscure Historical Outliers (OHO):
     - Severe Age Outliers (>= 20y, >= 30y, >= 50y, >= 100y) with negligible citations (<= 5).
  3. Cumulative Paper-Level Disparity Rates:
     - Papers with >= 1 Forward Anachronism (25.3%)
     - Papers with >= 1 Severe Forward Anachronism (12.1%)
     - Papers with >= 1 Obscure Historical Outlier (6.9%)
  4. Generates Publication Table 11 (CSV & Markdown).
"""

from __future__ import annotations

import argparse
import time
from pathlib import Path

import duckdb

from era.config import DATA_DIR, M_DRIVE, PROJECT_ROOT

SYNTHESIS_PARQUET = DATA_DIR / "era_multi_index_synthesis.parquet"
OUTPUT_TABLES_DIR = PROJECT_ROOT / "report" / "output" / "tables"


def run_full_reference_corpus_analysis(output_dir: Path | None = None) -> dict[str, Any]:
    """Execute full-corpus reference resolution audit in DuckDB."""
    if output_dir is None:
        output_dir = OUTPUT_TABLES_DIR
    output_dir.mkdir(parents=True, exist_ok=True)

    t0 = time.time()
    print("=" * 80)
    print("FULL-CORPUS REFERENCE RESOLUTION DISPARITY ANALYSIS (OpenAlex M-Drive)")
    print("=" * 80)

    con = duckdb.connect()

    # Step 1: Filter to Australian ERA citers in OpenAlex
    print("Step 1: Identifying ERA OpenAlex citing outputs (2011-2016)...")
    con.execute(f"""
        CREATE TEMP TABLE era_citers AS
        SELECT openalex_id as citer_idx, era_year, canonical_id, research_output_type
        FROM '{SYNTHESIS_PARQUET}'
        WHERE in_openalex = true AND openalex_id IS NOT NULL AND era_year BETWEEN 2011 AND 2016
    """)
    n_citers = con.execute("SELECT count(*) FROM era_citers").fetchone()[0]
    print(f"  Loaded {n_citers:,} ERA citing works in {time.time() - t0:.2f}s.\n")

    # Step 2: Full-corpus reference link analysis
    print("Step 2: Scanning all 10 compact reference partitions (15.9M references)...")
    t1 = time.time()
    ref_query = f"""
        WITH matched AS (
            SELECT 
                s.citer_idx,
                c.era_year as citer_year,
                w.publication_year as cited_year,
                w.cited_by_count,
                c.era_year - w.publication_year as age_gap
            FROM '{M_DRIVE}/openalex_oct26/parquet_converted/compact/references/part_*.parquet' s
            JOIN era_citers c ON s.citer_idx = c.citer_idx
            JOIN '{M_DRIVE}/openalex_oct26/parquet_converted/compact/works/*.parquet' w ON s.cited_idx = w.work_idx
            WHERE w.publication_year IS NOT NULL
        )
        SELECT 
            count(*) as total_refs,
            count(*) filter (WHERE age_gap < 0) as forward_anachronisms,
            count(*) filter (WHERE age_gap <= -5) as severe_forward_anachronisms,
            count(*) filter (WHERE age_gap >= 0 AND age_gap < 20) as recent_refs,
            count(*) filter (WHERE age_gap >= 20) as historical_refs,
            count(*) filter (WHERE age_gap >= 20 AND cited_by_count <= 5) as obscure_20y_outliers,
            count(*) filter (WHERE age_gap >= 30 AND cited_by_count <= 5) as obscure_30y_outliers,
            count(*) filter (WHERE age_gap >= 50 AND cited_by_count <= 5) as obscure_50y_outliers,
            count(*) filter (WHERE age_gap >= 100 AND cited_by_count <= 5) as obscure_100y_outliers
        FROM matched
    """
    cursor_refs = con.execute(ref_query)
    ref_cols = [d[0] for d in cursor_refs.description]
    ref_row = cursor_refs.fetchone()
    ref_stats = dict(zip(ref_cols, ref_row))
    print(f"  Reference-level analysis completed in {time.time() - t1:.2f}s.\n")

    # Step 3: Paper-level aggregation
    print("Step 3: Calculating cumulative paper-level impact across all citing papers...")
    t2 = time.time()
    paper_query = f"""
        WITH matched AS (
            SELECT 
                s.citer_idx,
                c.era_year as citer_year,
                w.publication_year as cited_year,
                w.cited_by_count,
                c.era_year - w.publication_year as age_gap
            FROM '{M_DRIVE}/openalex_oct26/parquet_converted/compact/references/part_*.parquet' s
            JOIN era_citers c ON s.citer_idx = c.citer_idx
            JOIN '{M_DRIVE}/openalex_oct26/parquet_converted/compact/works/*.parquet' w ON s.cited_idx = w.work_idx
            WHERE w.publication_year IS NOT NULL
        ),
        paper_agg AS (
            SELECT 
                citer_idx,
                count(*) as total_refs,
                count(*) filter (WHERE age_gap < 0) as n_anachronisms,
                count(*) filter (WHERE age_gap <= -5) as n_severe_anachronisms,
                count(*) filter (WHERE age_gap >= 20 AND cited_by_count <= 5) as n_obscure_20y,
                count(*) filter (WHERE age_gap >= 30 AND cited_by_count <= 5) as n_obscure_30y,
                count(*) filter (WHERE age_gap >= 50 AND cited_by_count <= 5) as n_obscure_50y,
                count(*) filter (WHERE age_gap >= 100 AND cited_by_count <= 5) as n_obscure_100y
            FROM matched
            GROUP BY citer_idx
        )
        SELECT 
            count(*) as total_citing_papers,
            count(*) filter (WHERE n_anachronisms > 0) as papers_with_anachronism,
            count(*) filter (WHERE n_severe_anachronisms > 0) as papers_with_severe_anachronism,
            count(*) filter (WHERE n_obscure_20y > 0) as papers_with_obscure_20y,
            count(*) filter (WHERE n_obscure_30y > 0) as papers_with_obscure_30y,
            count(*) filter (WHERE n_obscure_50y > 0) as papers_with_obscure_50y,
            count(*) filter (WHERE n_obscure_100y > 0) as papers_with_obscure_100y
        FROM paper_agg
    """
    cursor_papers = con.execute(paper_query)
    paper_cols = [d[0] for d in cursor_papers.description]
    paper_row = cursor_papers.fetchone()
    paper_stats = dict(zip(paper_cols, paper_row))
    print(f"  Paper-level analysis completed in {time.time() - t2:.2f}s.\n")

    # Step 4: Display Summary Table
    total_refs = ref_stats["total_refs"]
    total_papers = paper_stats["total_citing_papers"]

    print("=" * 80)
    print("TABLE 11: FULL-CORPUS REFERENCE RESOLUTION DISPARITY TAXONOMY")
    print("=" * 80)
    print(f"{'Metric Category':<45} | {'Count':<12} | {'Rate (%)':<10}")
    print("-" * 75)
    print(f"{'Total Resolved References':<45} | {total_refs:12,d} | {'100.00%':<10}")
    print(f"{'Normal Recent References (0 <= Delta_T < 20y)':<45} | {ref_stats['recent_refs']:12,d} | {ref_stats['recent_refs']/total_refs*100:6.2f}%")
    print(f"{'Total Historical References (Delta_T >= 20y)':<45} | {ref_stats['historical_refs']:12,d} | {ref_stats['historical_refs']/total_refs*100:6.2f}%")
    print("-" * 75)
    print(f"{'Forward Anachronisms (T_target > T_source)':<45} | {ref_stats['forward_anachronisms']:12,d} | {ref_stats['forward_anachronisms']/total_refs*100:6.2f}%")
    print(f"{'Severe Forward Anachronisms (Delta_T <= -5y)':<45} | {ref_stats['severe_forward_anachronisms']:12,d} | {ref_stats['severe_forward_anachronisms']/total_refs*100:6.2f}%")
    print("-" * 75)
    print(f"{'Backward Obscure Outliers (>= 20y, <= 5 cites)':<45} | {ref_stats['obscure_20y_outliers']:12,d} | {ref_stats['obscure_20y_outliers']/total_refs*100:6.2f}%")
    print(f"{'Backward Obscure Outliers (>= 30y, <= 5 cites)':<45} | {ref_stats['obscure_30y_outliers']:12,d} | {ref_stats['obscure_30y_outliers']/total_refs*100:6.2f}%")
    print(f"{'Backward Obscure Outliers (>= 50y, <= 5 cites)':<45} | {ref_stats['obscure_50y_outliers']:12,d} | {ref_stats['obscure_50y_outliers']/total_refs*100:6.2f}%")
    print(f"{'Backward Obscure Outliers (>= 100y, <= 5 cites)':<45} | {ref_stats['obscure_100y_outliers']:12,d} | {ref_stats['obscure_100y_outliers']/total_refs*100:6.2f}%")
    print("=" * 80)
    print(f"{'Total Citing Papers Audited':<45} | {total_papers:12,d} | {'100.00%':<10}")
    print(f"{'Papers with >= 1 Forward Anachronism':<45} | {paper_stats['papers_with_anachronism']:12,d} | {paper_stats['papers_with_anachronism']/total_papers*100:6.2f}%")
    print(f"{'Papers with >= 1 Severe Forward Anachronism':<45} | {paper_stats['papers_with_severe_anachronism']:12,d} | {paper_stats['papers_with_severe_anachronism']/total_papers*100:6.2f}%")
    print(f"{'Papers with >= 1 Obscure 20y+ Outlier':<45} | {paper_stats['papers_with_obscure_20y']:12,d} | {paper_stats['papers_with_obscure_20y']/total_papers*100:6.2f}%")
    print(f"{'Papers with >= 1 Obscure 30y+ Outlier':<45} | {paper_stats['papers_with_obscure_30y']:12,d} | {paper_stats['papers_with_obscure_30y']/total_papers*100:6.2f}%")
    print(f"{'Papers with >= 1 Obscure 50y+ Outlier':<45} | {paper_stats['papers_with_obscure_50y']:12,d} | {paper_stats['papers_with_obscure_50y']/total_papers*100:6.2f}%")
    print(f"{'Papers with >= 1 Obscure 100y+ Outlier':<45} | {paper_stats['papers_with_obscure_100y']:12,d} | {paper_stats['papers_with_obscure_100y']/total_papers*100:6.2f}%")
    print("=" * 80)

    # Step 5: Save Table 11 to CSV and Markdown
    csv_file = output_dir / "table11_reference_resolution_disparities.csv"
    md_file = output_dir / "table11_reference_resolution_disparities.md"

    md_content = f"""# Table 11: Reference Resolution Disparity Taxonomy Across ERA 2018 Corpus

Evaluated across **{total_refs:,}** resolved reference links from **{total_papers:,}** Australian university outputs in OpenAlex (2011–2016).

| Disparity Category | Reference Count | % of References | Paper Count | % of Papers |
| :--- | :---: | :---: | :---: | :---: |
| **Total Evaluated** | {total_refs:,} | 100.00% | {total_papers:,} | 100.00% |
| **Normal Recent Citations** ($0 \\le \\Delta T < 20\\text{{y}}$) | {ref_stats['recent_refs']:,} | {ref_stats['recent_refs']/total_refs*100:.2f}% | — | — |
| **Total Historical Citations** ($\\Delta T \\ge 20\\text{{y}}$) | {ref_stats['historical_refs']:,} | {ref_stats['historical_refs']/total_refs*100:.2f}% | — | — |
| **Forward Anachronisms** ($T_{{\\text{{target}}}} > T_{{\\text{{source}}}}$) | {ref_stats['forward_anachronisms']:,} | {ref_stats['forward_anachronisms']/total_refs*100:.2f}% | {paper_stats['papers_with_anachronism']:,} | {paper_stats['papers_with_anachronism']/total_papers*100:.2f}% |
| **Severe Forward Anachronisms** ($\\ge 5$ years future) | {ref_stats['severe_forward_anachronisms']:,} | {ref_stats['severe_forward_anachronisms']/total_refs*100:.2f}% | {paper_stats['papers_with_severe_anachronism']:,} | {paper_stats['papers_with_severe_anachronism']/total_papers*100:.2f}% |
| **Backward Obscure Outliers** ($\\ge 20\\text{{y}}$ old, $\\le 5$ cites) | {ref_stats['obscure_20y_outliers']:,} | {ref_stats['obscure_20y_outliers']/total_refs*100:.2f}% | {paper_stats['papers_with_obscure_20y']:,} | {paper_stats['papers_with_obscure_20y']/total_papers*100:.2f}% |
| **Backward Obscure Outliers** ($\\ge 30\\text{{y}}$ old, $\\le 5$ cites) | {ref_stats['obscure_30y_outliers']:,} | {ref_stats['obscure_30y_outliers']/total_refs*100:.2f}% | {paper_stats['papers_with_obscure_30y']:,} | {paper_stats['papers_with_obscure_30y']/total_papers*100:.2f}% |
| **Backward Obscure Outliers** ($\\ge 50\\text{{y}}$ old, $\\le 5$ cites) | {ref_stats['obscure_50y_outliers']:,} | {ref_stats['obscure_50y_outliers']/total_refs*100:.2f}% | {paper_stats['papers_with_obscure_50y']:,} | {paper_stats['papers_with_obscure_50y']/total_papers*100:.2f}% |
| **Backward Obscure Outliers** ($\\ge 100\\text{{y}}$ old, $\\le 5$ cites) | {ref_stats['obscure_100y_outliers']:,} | {ref_stats['obscure_100y_outliers']/total_refs*100:.2f}% | {paper_stats['papers_with_obscure_100y']:,} | {paper_stats['papers_with_obscure_100y']/total_papers*100:.2f}% |
"""
    md_file.write_text(md_content)

    csv_content = (
        "category,ref_count,ref_pct,paper_count,paper_pct\n"
        f"total,{total_refs},100.00,{total_papers},100.00\n"
        f"recent_0_to_19y,{ref_stats['recent_refs']},{ref_stats['recent_refs']/total_refs*100:.2f},,\n"
        f"historical_ge_20y,{ref_stats['historical_refs']},{ref_stats['historical_refs']/total_refs*100:.2f},,\n"
        f"forward_anachronisms,{ref_stats['forward_anachronisms']},{ref_stats['forward_anachronisms']/total_refs*100:.2f},{paper_stats['papers_with_anachronism']},{paper_stats['papers_with_anachronism']/total_papers*100:.2f}\n"
        f"severe_forward_ge_5y,{ref_stats['severe_forward_anachronisms']},{ref_stats['severe_forward_anachronisms']/total_refs*100:.2f},{paper_stats['papers_with_severe_anachronism']},{paper_stats['papers_with_severe_anachronism']/total_papers*100:.2f}\n"
        f"backward_obscure_ge_20y,{ref_stats['obscure_20y_outliers']},{ref_stats['obscure_20y_outliers']/total_refs*100:.2f},{paper_stats['papers_with_obscure_20y']},{paper_stats['papers_with_obscure_20y']/total_papers*100:.2f}\n"
        f"backward_obscure_ge_30y,{ref_stats['obscure_30y_outliers']},{ref_stats['obscure_30y_outliers']/total_refs*100:.2f},{paper_stats['papers_with_obscure_30y']},{paper_stats['papers_with_obscure_30y']/total_papers*100:.2f}\n"
        f"backward_obscure_ge_50y,{ref_stats['obscure_50y_outliers']},{ref_stats['obscure_50y_outliers']/total_refs*100:.2f},{paper_stats['papers_with_obscure_50y']},{paper_stats['papers_with_obscure_50y']/total_papers*100:.2f}\n"
        f"backward_obscure_ge_100y,{ref_stats['obscure_100y_outliers']},{ref_stats['obscure_100y_outliers']/total_refs*100:.2f},{paper_stats['papers_with_obscure_100y']},{paper_stats['papers_with_obscure_100y']/total_papers*100:.2f}\n"
    )
    csv_file.write_text(csv_content)
    print(f"Exported Table 11 to:\n  - {csv_file}\n  - {md_file}")

    return {"ref_stats": ref_stats, "paper_stats": paper_stats}


def main():
    parser = argparse.ArgumentParser(description="Full-Corpus Backward Reference Resolution Disparity Analysis")
    parser.parse_args()
    run_full_reference_corpus_analysis()


if __name__ == "__main__":
    main()
