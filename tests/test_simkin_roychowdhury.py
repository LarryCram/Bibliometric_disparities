"""Unit tests for Simkin & Roychowdhury citation mutation-copying analysis."""

import duckdb
import pytest
from pathlib import Path
from era.analysis.simkin_roychowdhury_grobid import (
    compute_simkin_roychowdhury_metrics,
    export_table12,
)


@pytest.fixture
def mock_disparities_table(tmp_path: Path):
    """Creates a temporary Parquet file simulating GROBID reference disparities."""
    parquet_file = tmp_path / "mock_disparities.parquet"
    con = duckdb.connect()

    # Create synthetic dataset with:
    # 1 canonical target with 1 canonical cite, 1 singleton typo (k=1), 1 copied typo (k=3 across 2 HEPs and 2 years)
    # 1 canonical target with 2 singletons
    con.execute("""
        CREATE TABLE mock_refs AS
        SELECT 
            101 as citing_oax_id,
            1 as cited_canonical_id,
            'Paper on Quantum Gravity' as ref_title,
            'Type 1 (Surface/Punctuation)' as title_olensky_type,
            'exact' as title_disparity_label,
            2012 as citing_era_year,
            ['University of Sydney'] as citing_institutions,
            1 as mention_count,
            '1 (Passing citation)' as importance_tier,
            'Introduction/Background' as primary_section,
            false as in_methods,
            true as in_intro,
            false as in_discuss,
            false as in_results
        UNION ALL
        SELECT 
            102, 1, 'Paper on Quatum Gravity', 'Type 2 (Textual Variation)', 'spelling_indel', 2013, ['University of Sydney'], 1, '1 (Passing citation)', 'Introduction/Background', false, true, false, false
        UNION ALL
        SELECT 
            103, 1, 'Paper on Quatum Gravity', 'Type 2 (Textual Variation)', 'spelling_indel', 2014, ['University of Melbourne'], 2, '2-3 (Moderate)', 'Introduction/Background', false, true, false, false
        UNION ALL
        SELECT 
            104, 1, 'Paper on Quatum Gravity', 'Type 2 (Textual Variation)', 'spelling_indel', 2015, ['Monash University'], 1, '1 (Passing citation)', 'Introduction/Background', false, true, false, false
        UNION ALL
        SELECT 
            105, 1, 'Paper on Quantm Gravty', 'Type 2 (Textual Variation)', 'spelling_indel', 2013, ['UNSW Sydney'], 0, '0 (Uncited in body)', 'Other/Uncited in body', false, false, false, false
    """)
    con.execute(f"COPY mock_refs TO '{parquet_file}' (FORMAT PARQUET)")
    con.close()
    return parquet_file


def test_compute_simkin_roychowdhury_metrics(mock_disparities_table: Path):
    metrics = compute_simkin_roychowdhury_metrics(parquet_path=mock_disparities_table)

    assert "textual_type2" in metrics
    assert "combined_errors" in metrics
    assert "diffusion_combined" in metrics
    assert "mention_profiles" in metrics

    t2 = metrics["textual_type2"]
    # We have two Type 2 variants:
    # 1. 'Paper on Quatum Gravity' -> k = 3
    # 2. 'Paper on Quantm Gravty'  -> k = 1
    # Total variants = 2. Total citations = 4. Singletons = 1.
    assert t2["total_variants"] == 2
    assert t2["total_citations"] == 4
    assert t2["singletons_k1"] == 1
    assert t2["replicated_variants_k_ge_2"] == 1
    assert t2["replicated_citations_k_ge_2"] == 3

    # Simkin & Roychowdhury rho = N_1 / K_total = 1 / 4 = 0.25 (25% reading, 75% copying)
    assert pytest.approx(t2["rho_reading_estimate"], 0.01) == 0.25
    assert pytest.approx(t2["copying_rate_estimate"], 0.01) == 0.75

    # Diffusion: 'Paper on Quatum Gravity' appeared across 3 distinct universities
    diff = metrics["diffusion_combined"]
    assert diff["total_multi_variants"] == 1
    assert diff["inter_hep_diffusion"] == 1
    assert diff["intra_hep_only"] == 0
    assert diff["max_heps"] == 3
    # Year span: 2015 - 2013 = 2 years
    assert diff["span_2y"] == 1
    assert diff["mean_span_years"] == 2.0


def test_export_table12(tmp_path: Path, mock_disparities_table: Path):
    metrics = compute_simkin_roychowdhury_metrics(parquet_path=mock_disparities_table)
    out_dir = tmp_path / "output_tables"
    csv_file, md_file = export_table12(metrics, output_dir=out_dir)

    assert csv_file.exists()
    assert md_file.exists()

    md_content = md_file.read_text(encoding="utf-8")
    assert "Table 12: Simkin & Roychowdhury Citation Mutation-Copying Dynamics" in md_content
    assert "Type 2: Textual" in md_content
    assert "Inter-HEP Diffusion" in md_content
