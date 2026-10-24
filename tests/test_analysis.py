"""Unit tests for Stage 3 multi-index analysis and comparative Olensky profiling."""

import pytest
import duckdb
from pathlib import Path

from era.analysis.multi_index_coverage import (
    calculate_output_type_coverage,
    calculate_intersection_metrics,
)
from era.analysis.comparative_olensky import (
    calculate_comparative_olensky_taxonomy,
    calculate_olensky_by_output_type,
)


@pytest.fixture
def mock_synthesis_con(tmp_path):
    con = duckdb.connect()
    # Create sample synthesis dataset
    pq_path = tmp_path / "mock_synthesis.parquet"
    con.execute(f"""
        COPY (
            SELECT 
                1 AS canonical_id, 'Journal Article' AS research_output_type,
                TRUE AS in_openalex, TRUE AS in_scopus, FALSE AS in_trove,
                2 AS index_count, TRUE AS in_any_index, 'OpenAlex & Scopus' AS coverage_category
            UNION ALL
            SELECT 
                2 AS canonical_id, 'Journal Article' AS research_output_type,
                TRUE AS in_openalex, TRUE AS in_scopus, TRUE AS in_trove,
                3 AS index_count, TRUE AS in_any_index, 'All Three' AS coverage_category
            UNION ALL
            SELECT 
                3 AS canonical_id, 'Book' AS research_output_type,
                TRUE AS in_openalex, FALSE AS in_scopus, TRUE AS in_trove,
                2 AS index_count, TRUE AS in_any_index, 'OpenAlex & Trove' AS coverage_category
            UNION ALL
            SELECT 
                4 AS canonical_id, 'Book Chapter' AS research_output_type,
                FALSE AS in_openalex, FALSE AS in_scopus, TRUE AS in_trove,
                1 AS index_count, TRUE AS in_any_index, 'Trove Only' AS coverage_category
            UNION ALL
            SELECT 
                5 AS canonical_id, 'Original Creative Work' AS research_output_type,
                FALSE AS in_openalex, FALSE AS in_scopus, FALSE AS in_trove,
                0 AS index_count, FALSE AS in_any_index, 'ERA Only' AS coverage_category
        ) TO '{pq_path}' (FORMAT PARQUET)
    """)
    yield con, pq_path
    con.close()


def test_calculate_output_type_coverage(mock_synthesis_con):
    con, pq_path = mock_synthesis_con
    res = calculate_output_type_coverage(con, synthesis_pq=pq_path, export_csv=False)
    assert len(res) == 4
    
    # Check Journal Articles (2 total, 2 in OA, 2 in Scopus, 1 in Trove, 2 union)
    journal_row = next(r for r in res if r["output_type"] == "Journal Article")
    assert journal_row["total_canonical"] == 2
    assert journal_row["n_openalex"] == 2
    assert journal_row["pct_openalex"] == 100.0
    assert journal_row["n_scopus"] == 2
    assert journal_row["n_trove"] == 1
    assert journal_row["n_union"] == 2
    assert journal_row["pct_union"] == 100.0
    assert journal_row["n_era_only"] == 0

    # Check Creative Work (1 total, 0 matched, 1 ERA only)
    creative_row = next(r for r in res if r["output_type"] == "Original Creative Work")
    assert creative_row["total_canonical"] == 1
    assert creative_row["n_union"] == 0
    assert creative_row["n_era_only"] == 1
    assert creative_row["pct_era_only"] == 100.0


def test_calculate_intersection_metrics(mock_synthesis_con):
    con, pq_path = mock_synthesis_con
    res = calculate_intersection_metrics(con, synthesis_pq=pq_path, export_csv=False)
    categories = {r["coverage_category"]: r["work_count"] for r in res}
    
    assert categories["OpenAlex & Scopus"] == 1
    assert categories["All Three"] == 1
    assert categories["OpenAlex & Trove"] == 1
    assert categories["Trove Only"] == 1
    assert categories["ERA Only"] == 1
