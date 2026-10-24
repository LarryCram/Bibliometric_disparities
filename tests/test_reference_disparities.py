"""Unit tests for GROBID reference disparities and mention importance analysis."""

import duckdb
import pytest
from pathlib import Path

from era.analysis.reference_disparities import (
    build_grobid_reference_disparities,
    OUTPUT_DISPARITIES_PARQUET,
)
from era.matching.evaluate_disparities import (
    classify_title_disparity,
    classify_year_disparity,
)


def test_classify_title_disparity_in_references():
    # Exact match
    codes, label = classify_title_disparity("Quantum Computing Basics", "Quantum Computing Basics")
    assert label == "exact"
    assert codes == []

    # Punctuation / case
    codes, label = classify_title_disparity("Quantum Computing Basics", "quantum computing basics.")
    assert label == "punctuation_or_case"
    assert "R" in codes

    # Typographical variant
    codes, label = classify_title_disparity("Quantum Computing Basics", "Quantum Computng Basics")
    assert label == "spelling_indel"
    assert "B" in codes

    # Cropped subtitle
    codes, label = classify_title_disparity("Quantum Computing: A Modern Guide", "Quantum Computing")
    assert label == "cropped_subtitle"
    assert "F" in codes

    # Discordant
    codes, label = classify_title_disparity("Quantum Computing Basics", "Deep Learning in Neural Networks")
    assert label == "discordant"
    assert "D" in codes


def test_classify_year_disparity_in_references():
    codes, diff = classify_year_disparity(2015, 2015)
    assert diff == 0
    assert codes == []

    codes, diff = classify_year_disparity(2015, 2014)
    assert diff == 1
    assert codes == ["T"]

    codes, diff = classify_year_disparity(2015, None)
    assert diff is None
    assert codes == ["E"]


def test_reference_disparities_dataset_exists_and_valid():
    assert OUTPUT_DISPARITIES_PARQUET.exists()
    con = duckdb.connect()
    count = con.execute(f"SELECT count(*) FROM '{OUTPUT_DISPARITIES_PARQUET}'").fetchone()[0]
    assert count > 100_000

    # Verify key columns
    cols = [desc[0] for desc in con.execute(f"DESCRIBE SELECT * FROM '{OUTPUT_DISPARITIES_PARQUET}'").fetchall()]
    assert "citing_oax_id" in cols
    assert "cited_canonical_id" in cols
    assert "mention_count" in cols
    assert "importance_tier" in cols
    assert "section_group" in cols
    assert "title_disparity_label" in cols
    assert "title_olensky_type" in cols
    con.close()
