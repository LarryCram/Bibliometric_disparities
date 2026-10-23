"""Unit tests for match_scopus.py."""

from pathlib import Path
import pyarrow as pa
import pyarrow.parquet as pq

from era.matching.match_scopus import (
    normalize_doi,
    normalize_title,
    classify_title_disparity,
    match_canonical_to_scopus,
)


class TestScopusNormalization:
    def test_normalize_doi(self):
        assert normalize_doi("https://doi.org/10.1001/jama.2015") == "10.1001/jama.2015"
        assert normalize_doi("http://dx.doi.org/10.1001/jama.2015") == "10.1001/jama.2015"
        assert normalize_doi("doi: 10.1001/JAMA.2015 ") == "10.1001/jama.2015"
        assert normalize_doi(None) is None
        assert normalize_doi("   ") is None

    def test_normalize_title(self):
        assert normalize_title("Hello, World! &amp; Friends") == "hello world friends"
        assert normalize_title("   A   Title   with   Spaces  ") == "a title with spaces"
        assert normalize_title(None) == ""


class TestScopusTitleDisparityClassification:
    def test_exact(self):
        codes, label = classify_title_disparity("Quantum Field Theory", "Quantum Field Theory")
        assert codes == []
        assert label == "exact"

    def test_punctuation_case(self):
        codes, label = classify_title_disparity("quantum field theory:", "Quantum Field Theory")
        assert "R" in codes
        assert label == "punctuation_or_case"

    def test_markup_or_entity(self):
        codes, label = classify_title_disparity("A &amp; B in Science", "A & B in Science")
        assert "Q" in codes
        assert label == "markup_or_entity"

    def test_cropped_subtitle(self):
        codes, label = classify_title_disparity(
            "Advances in Immunology: A Systematic Clinical Review",
            "Advances in Immunology",
        )
        assert "F" in codes
        assert label == "cropped_subtitle"

    def test_single_char_indel(self):
        codes, label = classify_title_disparity("Quantum Field Theor", "Quantum Field Theory")
        assert "B" in codes
        assert label == "spelling_indel"

    def test_discordant(self):
        codes, label = classify_title_disparity("Quantum Field Theory", "Economic Policy in Medieval Europe")
        assert "D" in codes
        assert label == "discordant"


class TestMatchCanonicalToScopusPipeline:
    def test_end_to_end_matching(self, tmp_path: Path):
        # 1. Create synthetic canonical parquet
        era_records = [
            {
                "canonical_id": 1,
                "source_ids": [101, 102],
                "institutions": ["Univ A", "Univ B"],
                "n_submissions": 2,
                "is_multi_hep": True,
                "research_output_type": "Journal Article",
                "title": "Quantum Computing Architecture: Principles and Applications",
                "doi": "10.1016/j.qc.2015.01",
                "doi_wellformed": True,
                "reference_year": 2015,
                "outlet": "Journal of Quantum Computing",
                "publisher": "Elsevier",
            },
            {
                "canonical_id": 2,
                "source_ids": [103],
                "institutions": ["Univ C"],
                "n_submissions": 1,
                "is_multi_hep": False,
                "research_output_type": "Book Chapter",
                "title": "History of Modern Philosophy in Australia and the Pacific",
                "doi": None,
                "doi_wellformed": None,
                "reference_year": 2014,
                "outlet": "Pacific Studies",
                "publisher": "ANU Press",
            },
            {
                "canonical_id": 3,
                "source_ids": [104],
                "institutions": ["Univ D"],
                "n_submissions": 1,
                "is_multi_hep": False,
                "research_output_type": "Journal Article",
                "title": "Unmatched Paper without Scopus Record Here",
                "doi": "10.1001/missing.doi",
                "doi_wellformed": True,
                "reference_year": 2013,
                "outlet": "Unindexed Journal",
                "publisher": "Unknown",
            },
        ]
        canon_pq = tmp_path / "canonical.parquet"
        pq.write_table(pa.Table.from_pylist(era_records), canon_pq)

        # 2. Create synthetic Scopus shard
        scopus_records = [
            {
                "doi_normalized": "10.1016/j.qc.2015.01",
                "status": "ok",
                "title": "Quantum Computing Architecture: Principles and Applications",
                "author_names": ["Smith, J.", "Doe, A."],
                "author_auids": ["123", "456"],
                "n_authors": 2,
                "citedby_count": 42,
                "openaccess_flag": True,
                "eid": "2-s2.0-1111",
                "affiliation_name": "University of Sydney",
                "affiliation_city": "Sydney",
                "affiliation_country": "Australia",
            },
            {
                "doi_normalized": None,
                "status": "ok",
                "title": "History of Modern Philosophy in Australia and the Pacific",
                "author_names": ["Taylor, B."],
                "author_auids": ["789"],
                "n_authors": 1,
                "citedby_count": 5,
                "openaccess_flag": False,
                "eid": "2-s2.0-2222",
                "affiliation_name": "Australian National University",
                "affiliation_city": "Canberra",
                "affiliation_country": "Australia",
            },
        ]
        scopus_pq = tmp_path / "scopus_2015.parquet"
        pq.write_table(pa.Table.from_pylist(scopus_records), scopus_pq)

        out_matches_pq = tmp_path / "out_matches.parquet"
        out_disp_pq = tmp_path / "out_disparities.parquet"

        stats = match_canonical_to_scopus(
            canonical_pq=canon_pq,
            scopus_glob=tmp_path / "scopus_*.parquet",
            output_matches_pq=out_matches_pq,
            output_disparities_pq=out_disp_pq,
        )

        assert stats["canonical_records"] == 3
        assert stats["matched_records"] == 2
        assert stats["tier1_doi_matches"] == 1
        assert stats["tier2_title_matches"] == 1

        # Check matched table
        table_matches = pq.read_table(out_matches_pq).to_pylist()
        m_map = {r["canonical_id"]: r for r in table_matches}
        assert m_map[1]["scopus_matched"] is True
        assert m_map[1]["match_tier"] == "doi"
        assert m_map[1]["scopus_eid"] == "2-s2.0-1111"
        assert m_map[1]["scopus_citedby_count"] == 42

        assert m_map[2]["scopus_matched"] is True
        assert m_map[2]["match_tier"] == "title"
        assert m_map[2]["scopus_eid"] == "2-s2.0-2222"

        assert m_map[3]["scopus_matched"] is False
        assert m_map[3]["match_tier"] is None
