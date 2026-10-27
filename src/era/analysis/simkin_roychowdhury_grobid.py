"""Simkin & Roychowdhury (2003, 2005) Citation Copying Analysis on GROBID Full Text.

Applies the Simkin & Roychowdhury stochastic citation mutation-copying framework to
the 71,436 Australian open-access full-text PDFs parsed via GROBID, focusing on the
134,195 matched internal citations between Australian ERA research outputs.

Theoretical Framework:
    Simkin, M. V., & Roychowdhury, V. P. (2003). Read before you cite!
        Complex Systems, 14(3), 269-274.
    Simkin, M. V., & Roychowdhury, V. P. (2005). Stochastic modeling of citation slips.
        Scientometrics, 62(3), 367-384.

Core Principle:
    When an author reads a paper, they cite it correctly (canonical form) or introduce
    a fresh, independent typographical error with small probability mu, creating a singleton
    error variant (k = 1).
    When an author copies a citation from an existing paper without reading the primary work,
    they copy the existing citation text verbatim, propagating identical misprints (k >= 2).
    Under the preferential attachment / Simon model, the fraction of citing authors who
    actually read the original work is estimated by rho_hat = N_1 / sum(k * N_k).
"""

import csv
import json
import math
import time
from pathlib import Path
from typing import Any

import duckdb

from era.config import DATA_DIR, PROJECT_ROOT

DISPARITIES_PARQUET = DATA_DIR / "era_grobid_reference_disparities.parquet"
OUTPUT_DIR = PROJECT_ROOT / "report" / "output" / "tables"


def compute_simkin_roychowdhury_metrics(
    con: duckdb.DuckDBPyConnection | None = None,
    parquet_path: Path | None = None,
) -> dict[str, Any]:
    """Calculate Simkin & Roychowdhury copying metrics across GROBID internal citations."""
    close_con = False
    if con is None:
        con = duckdb.connect()
        close_con = True

    if parquet_path is None:
        parquet_path = DISPARITIES_PARQUET

    if not parquet_path.exists():
        raise FileNotFoundError(f"Reference disparities parquet not found at {parquet_path}")

    # Step 1: Aggregate variants by (cited_canonical_id, ref_title, title_olensky_type)
    query_variants = f"""
        CREATE OR REPLACE TEMP TABLE sr_variants AS
        SELECT 
            cited_canonical_id,
            ref_title,
            title_olensky_type,
            title_disparity_label,
            count(DISTINCT citing_oax_id) as k,
            min(citing_era_year) as t_first,
            max(citing_era_year) as t_last,
            max(citing_era_year) - min(citing_era_year) as span_years,
            count(DISTINCT citing_institutions[1]) as n_distinct_heps,
            list(DISTINCT citing_institutions[1]) as heps_list
        FROM '{parquet_path}'
        WHERE ref_title IS NOT NULL AND length(trim(ref_title)) > 0
        GROUP BY cited_canonical_id, ref_title, title_olensky_type, title_disparity_label
    """
    con.execute(query_variants)

    # Step 2: Extract frequency spectrum N_k for error categories
    # Types: Type 2 (Textual), Type 3 (Severe), Combined (Type 2 + Type 3), All Variants
    categories = {
        "textual_type2": "title_olensky_type = 'Type 2 (Textual Variation)'",
        "severe_type3": "title_olensky_type = 'Type 3 (Severe Inaccuracy)'",
        "combined_errors": "title_olensky_type IN ('Type 2 (Textual Variation)', 'Type 3 (Severe Inaccuracy)')",
        "surface_type1": "title_olensky_type = 'Type 1 (Surface/Punctuation)'",
        "all_variants": "1=1",
    }

    results: dict[str, Any] = {}

    for cat_name, where_clause in categories.items():
        q_spectrum = f"""
            SELECT 
                k,
                count(*) as num_variants,
                k * count(*) as total_citations
            FROM sr_variants
            WHERE {where_clause}
            GROUP BY k
            ORDER BY k ASC
        """
        rows = con.execute(q_spectrum).fetchall()
        spectrum = {int(r[0]): int(r[1]) for r in rows}
        
        total_variants = sum(spectrum.values())
        total_citations = sum(k * count for k, count in spectrum.items())
        n1 = spectrum.get(1, 0)
        n_replicated_variants = sum(count for k, count in spectrum.items() if k >= 2)
        k_replicated_citations = sum(k * count for k, count in spectrum.items() if k >= 2)

        # Simkin & Roychowdhury Estimator: rho_hat = N_1 / K_total
        rho_hat = (n1 / total_citations) if total_citations > 0 else 1.0
        copy_rate = 1.0 - rho_hat

        # Simon model gamma parameter: gamma = rho / (1 - rho)
        gamma = (rho_hat / copy_rate) if copy_rate > 0 else float("inf")
        power_law_alpha = 1.0 + gamma if gamma != float("inf") else float("inf")

        results[cat_name] = {
            "spectrum": spectrum,
            "total_variants": total_variants,
            "total_citations": total_citations,
            "singletons_k1": n1,
            "replicated_variants_k_ge_2": n_replicated_variants,
            "replicated_citations_k_ge_2": k_replicated_citations,
            "rho_reading_estimate": rho_hat,
            "copying_rate_estimate": copy_rate,
            "gamma_parameter": gamma,
            "power_law_alpha": power_law_alpha,
        }

    # Step 3: Diffusion and Persistence for Combined Errors (Type 2 + Type 3)
    q_diffusion = """
        SELECT 
            count(*) as total_multi_variants,
            count(CASE WHEN n_distinct_heps = 1 THEN 1 END) as intra_hep_only,
            count(CASE WHEN n_distinct_heps >= 2 THEN 1 END) as inter_hep_diffusion,
            avg(n_distinct_heps) as mean_heps,
            max(n_distinct_heps) as max_heps,
            count(CASE WHEN span_years = 0 THEN 1 END) as span_0y,
            count(CASE WHEN span_years = 1 THEN 1 END) as span_1y,
            count(CASE WHEN span_years = 2 THEN 1 END) as span_2y,
            count(CASE WHEN span_years >= 3 THEN 1 END) as span_3y_plus,
            avg(span_years) as mean_span_years,
            max(span_years) as max_span_years
        FROM sr_variants
        WHERE title_olensky_type IN ('Type 2 (Textual Variation)', 'Type 3 (Severe Inaccuracy)')
          AND k >= 2
    """
    diff_row = con.execute(q_diffusion).fetchone()
    results["diffusion_combined"] = {
        "total_multi_variants": diff_row[0],
        "intra_hep_only": diff_row[1],
        "intra_hep_pct": (diff_row[1] / diff_row[0] * 100.0) if diff_row[0] else 0.0,
        "inter_hep_diffusion": diff_row[2],
        "inter_hep_pct": (diff_row[2] / diff_row[0] * 100.0) if diff_row[0] else 0.0,
        "mean_heps": diff_row[3],
        "max_heps": diff_row[4],
        "span_0y": diff_row[5],
        "span_1y": diff_row[6],
        "span_2y": diff_row[7],
        "span_3y_plus": diff_row[8],
        "mean_span_years": diff_row[9],
        "max_span_years": diff_row[10],
    }

    # Step 4: In-Text Mention Profile (Singletons vs Copied Errors)
    q_mentions = f"""
        WITH error_citations AS (
            SELECT 
                d.citing_oax_id,
                d.cited_canonical_id,
                d.ref_title,
                d.mention_count,
                d.importance_tier,
                d.primary_section,
                d.in_methods,
                d.in_intro,
                d.in_discuss,
                d.in_results,
                v.k,
                CASE WHEN v.k >= 2 THEN 'Copied Variant (k >= 2)' ELSE 'Singleton Error (k = 1)' END as copy_group
            FROM '{parquet_path}' d
            JOIN sr_variants v ON d.cited_canonical_id = v.cited_canonical_id AND d.ref_title = v.ref_title
            WHERE v.title_olensky_type IN ('Type 2 (Textual Variation)', 'Type 3 (Severe Inaccuracy)')
        )
        SELECT 
            copy_group,
            count(*) as n_citations,
            avg(mention_count) as mean_mentions,
            count(CASE WHEN mention_count = 0 THEN 1 END) as uncited_count,
            round(100.0 * count(CASE WHEN mention_count = 0 THEN 1 END) / count(*), 2) as uncited_pct,
            count(CASE WHEN mention_count = 1 THEN 1 END) as passing_count,
            round(100.0 * count(CASE WHEN mention_count = 1 THEN 1 END) / count(*), 2) as passing_pct,
            count(CASE WHEN mention_count >= 4 THEN 1 END) as substantive_count,
            round(100.0 * count(CASE WHEN mention_count >= 4 THEN 1 END) / count(*), 2) as substantive_pct,
            count(CASE WHEN in_intro THEN 1 END) as in_intro_count,
            round(100.0 * count(CASE WHEN in_intro THEN 1 END) / count(*), 2) as in_intro_pct,
            count(CASE WHEN in_discuss THEN 1 END) as in_discuss_count,
            round(100.0 * count(CASE WHEN in_discuss THEN 1 END) / count(*), 2) as in_discuss_pct
        FROM error_citations
        GROUP BY copy_group
        ORDER BY copy_group DESC
    """
    mentions_rows = con.execute(q_mentions).fetchall()
    results["mention_profiles"] = [
        {
            "copy_group": r[0],
            "n_citations": r[1],
            "mean_mentions": r[2],
            "uncited_count": r[3],
            "uncited_pct": r[4],
            "passing_count": r[5],
            "passing_pct": r[6],
            "substantive_count": r[7],
            "substantive_pct": r[8],
            "in_intro_count": r[9],
            "in_intro_pct": r[10],
            "in_discuss_count": r[11],
            "in_discuss_pct": r[12],
        }
        for r in mentions_rows
    ]

    # Step 5: Top 10 Most Prolific Replicated Error Lineages
    q_top10 = """
        SELECT 
            cited_canonical_id,
            ref_title,
            title_olensky_type,
            title_disparity_label,
            k,
            t_first,
            t_last,
            span_years,
            n_distinct_heps
        FROM sr_variants
        WHERE title_olensky_type IN ('Type 2 (Textual Variation)', 'Type 3 (Severe Inaccuracy)')
        ORDER BY k DESC
        LIMIT 10
    """
    top10_rows = con.execute(q_top10).fetchall()
    results["top10_lineages"] = [
        {
            "cited_canonical_id": r[0],
            "variant_title": r[1],
            "olensky_type": r[2],
            "disparity_label": r[3],
            "k_replications": r[4],
            "first_year": r[5],
            "last_year": r[6],
            "span_years": r[7],
            "n_heps": r[8],
        }
        for r in top10_rows
    ]

    if close_con:
        con.close()

    return results


def export_table12(results: dict[str, Any], output_dir: Path | None = None) -> tuple[Path, Path]:
    """Export Table 12: Simkin & Roychowdhury Citation Mutation-Copying Dynamics."""
    if output_dir is None:
        output_dir = OUTPUT_DIR
    output_dir.mkdir(parents=True, exist_ok=True)

    csv_path = output_dir / "table12_simkin_roychowdhury_propagation.csv"
    md_path = output_dir / "table12_simkin_roychowdhury_propagation.md"

    comb = results["combined_errors"]
    t2 = results["textual_type2"]
    t3 = results["severe_type3"]
    diff = results["diffusion_combined"]
    m_single = results["mention_profiles"][0] if results["mention_profiles"] else {}
    m_copied = results["mention_profiles"][1] if len(results["mention_profiles"]) > 1 else {}

    # Format Markdown
    md_text = f"""# Table 12: Simkin & Roychowdhury Citation Mutation-Copying Dynamics Across GROBID Full-Text

Evaluated across **134,195** matched internal citations between Australian ERA research outputs parsed from 71,436 full-text PDFs.
Applying the Simkin & Roychowdhury (2003, 2005) stochastic citation copying model to identical typographical and severe error variants.

### Part A: Error Variant Spectrum and Reading vs. Copying Rates

| Inaccuracy Category | Total Variants ($M$) | Singletons ($k = 1$) | Replicated Variants ($k \\ge 2$) | Total Citations ($K$) | Replicated Citations | S&R Reading Rate ($\\hat{{\\rho}}$) | S&R Copying Rate ($1 - \\hat{{\\rho}}$) |
| :--- | :---: | :---: | :---: | :---: | :---: | :---: | :---: |
| **Type 2: Textual (Typos/Indels)** | {t2['total_variants']:,} | {t2['singletons_k1']:,} | {t2['replicated_variants_k_ge_2']:,} | {t2['total_citations']:,} | {t2['replicated_citations_k_ge_2']:,} | {t2['rho_reading_estimate']*100:.2f}% | {t2['copying_rate_estimate']*100:.2f}% |
| **Type 3: Severe Inaccuracies** | {t3['total_variants']:,} | {t3['singletons_k1']:,} | {t3['replicated_variants_k_ge_2']:,} | {t3['total_citations']:,} | {t3['replicated_citations_k_ge_2']:,} | {t3['rho_reading_estimate']*100:.2f}% | {t3['copying_rate_estimate']*100:.2f}% |
| **Combined Flawed Citations** | **{comb['total_variants']:,}** | **{comb['singletons_k1']:,}** | **{comb['replicated_variants_k_ge_2']:,}** | **{comb['total_citations']:,}** | **{comb['replicated_citations_k_ge_2']:,}** | **{comb['rho_reading_estimate']*100:.2f}%** | **{comb['copying_rate_estimate']*100:.2f}%** |

### Part B: Institutional Diffusion & Temporal Persistence ($k \\ge 2$ Error Lineages, $N = {diff['total_multi_variants']:,}$)

| Diffusion / Persistence Metric | Count | Rate (%) | Context / Definition |
| :--- | :---: | :---: | :--- |
| **Intra-HEP Replication** | {diff['intra_hep_only']:,} | {diff['intra_hep_pct']:.2f}% | Confined to the same Australian Higher Education Provider |
| **Inter-HEP Diffusion** | {diff['inter_hep_diffusion']:,} | {diff['inter_hep_pct']:.2f}% | Propagated across 2 to {diff['max_heps']} distinct Australian universities |
| **Synchronous Replication (0y span)** | {diff['span_0y']:,} | {diff['span_0y']/diff['total_multi_variants']*100:.2f}% | Replicated within the same publication year |
| **Multi-Year Propagation (1 to 5y span)** | {diff['span_1y'] + diff['span_2y'] + diff['span_3y_plus']:,} | {(diff['span_1y'] + diff['span_2y'] + diff['span_3y_plus'])/diff['total_multi_variants']*100:.2f}% | Persisted across subsequent publication years (mean: {diff['mean_span_years']:.2f}y) |

### Part C: In-Text Citation Importance Comparison

| Copy Group | Citations ($N$) | Mean Mentions | Uncited in Body (%) | Passing Mentions (1x) (%) | Substantive (4x+) (%) | In Introduction (%) | In Discussion (%) |
| :--- | :---: | :---: | :---: | :---: | :---: | :---: | :---: |
| **Singleton Errors ($k = 1$)** | {m_single.get('n_citations', 0):,} | {m_single.get('mean_mentions', 0):.2f} | {m_single.get('uncited_pct', 0):.2f}% | {m_single.get('passing_pct', 0):.2f}% | {m_single.get('substantive_pct', 0):.2f}% | {m_single.get('in_intro_pct', 0):.2f}% | {m_single.get('in_discuss_pct', 0):.2f}% |
| **Copied Variants ($k \\ge 2$)** | {m_copied.get('n_citations', 0):,} | {m_copied.get('mean_mentions', 0):.2f} | {m_copied.get('uncited_pct', 0):.2f}% | {m_copied.get('passing_pct', 0):.2f}% | {m_copied.get('substantive_pct', 0):.2f}% | {m_copied.get('in_intro_pct', 0):.2f}% | {m_copied.get('in_discuss_pct', 0):.2f}% |
"""
    md_path.write_text(md_text, encoding="utf-8")

    # Format CSV
    with open(csv_path, "w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow(["Metric Category", "Sub-Dimension", "Count", "Rate (%)", "Reading Rate (rho)", "Copying Rate (1-rho)"])
        writer.writerow(["Error Spectrum", "Type 2 Textual Variations", t2["total_citations"], "100.00", f"{t2['rho_reading_estimate']*100:.2f}%", f"{t2['copying_rate_estimate']*100:.2f}%"])
        writer.writerow(["Error Spectrum", "Type 3 Severe Inaccuracies", t3["total_citations"], "100.00", f"{t3['rho_reading_estimate']*100:.2f}%", f"{t3['copying_rate_estimate']*100:.2f}%"])
        writer.writerow(["Error Spectrum", "Combined Flawed Citations", comb["total_citations"], "100.00", f"{comb['rho_reading_estimate']*100:.2f}%", f"{comb['copying_rate_estimate']*100:.2f}%"])
        writer.writerow(["Diffusion", "Intra-HEP Only", diff["intra_hep_only"], f"{diff['intra_hep_pct']:.2f}", "", ""])
        writer.writerow(["Diffusion", "Inter-HEP Diffusion", diff["inter_hep_diffusion"], f"{diff['inter_hep_pct']:.2f}", "", ""])
        writer.writerow(["Persistence", "Synchronous (0y)", diff["span_0y"], f"{diff['span_0y']/diff['total_multi_variants']*100:.2f}", "", ""])
        span_multi = diff["span_1y"] + diff["span_2y"] + diff["span_3y_plus"]
        span_multi_pct = span_multi / diff["total_multi_variants"] * 100.0 if diff["total_multi_variants"] else 0.0
        writer.writerow(["Persistence", "Multi-Year Span (1-5y)", span_multi, f"{span_multi_pct:.2f}", "", ""])

    return csv_path, md_path


def main() -> None:
    t0 = time.time()
    print("Executing Simkin & Roychowdhury Citation Mutation-Copying Analysis...")
    results = compute_simkin_roychowdhury_metrics()
    csv_file, md_file = export_table12(results)
    print(f"Completed in {time.time()-t0:.2f}s.")
    print(f"Saved CSV: {csv_file}")
    print(f"Saved MD:  {md_file}")


if __name__ == "__main__":
    main()
