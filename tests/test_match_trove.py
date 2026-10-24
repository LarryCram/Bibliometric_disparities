"""Unit tests for match_trove.py."""

from pathlib import Path
import pyarrow as pa
import pyarrow.parquet as pq

from era.matching.match_trove import (
    normalize_isbn,
    normalize_title,
    classify_title_disparity,
    classify_isbn_disparity,
    match_canonical_to_trove,
)


class TestTroveNormalization:
    def test_normalize_isbn_10_and_13(self):
        assert normalize_isbn("978-0-123456-47-2") == "9780123456472"
        assert normalize_isbn("0-123456-47-X") == "012345647X"
        assert normalize_isbn(" 9780123456472 ") == "9780123456472"
        assert normalize_isbn("012345647x") == "012345647X"

    def test_normalize_isbn_invalid(self):
        assert normalize_isbn(None) is None
        assert normalize_isbn("") is None
        assert normalize_isbn("12345") is None  # Too short
        assert normalize_isbn("invalid-isbn-string") is None

    def test_normalize_title(self):
        assert normalize_title("Australian Literature &amp; History") == "australian literature history"
        assert normalize_title("   A   Title   with   Spaces  ") == "a title with spaces"
        assert normalize_title(None) == ""


class TestTroveTitleDisparityClassification:
    def test_exact(self):
        codes, label = classify_title_disparity("A History of Australia", "A History of Australia")
        assert codes == []
        assert label == "exact"

    def test_punctuation_case(self):
        codes, label = classify_title_disparity("A History of Australia:", "a history of australia")
        assert "R" in codes
        assert label == "punctuation_or_case"

    def test_markup_or_entity(self):
        codes, label = classify_title_disparity("Arts &amp; Culture in NSW", "Arts & Culture in NSW")
        assert "Q" in codes
        assert label == "markup_or_entity"

    def test_cropped_subtitle(self):
        codes, label = classify_title_disparity(
            "Australian Modernism: Ideas and Influences in Design",
            "Australian Modernism",
        )
        assert "F" in codes
        assert label == "cropped_subtitle"

    def test_single_char_indel(self):
        codes, label = classify_title_disparity("Australian Modernism", "Australan Modernism")
        assert "B" in codes
        assert label == "spelling_indel"

    def test_discordant(self):
        codes, label = classify_title_disparity("Australian Modernism", "Quantum Electrochemistry Principles")
        assert "D" in codes
        assert label == "discordant"


class TestTroveIsbnDisparityClassification:
    def test_exact(self):
        codes, label = classify_isbn_disparity("978-0-12-345678-9", "9780123456789")
        assert codes == []
        assert label == "exact"

    def test_omitted(self):
        codes, label = classify_isbn_disparity("9780123456789", None)
        assert "E" in codes
        assert label == "omitted_in_trove"

        codes, label = classify_isbn_disparity(None, "9780123456789")
        assert "E" in codes
        assert label == "omitted_in_era"

    def test_conflict(self):
        codes, label = classify_isbn_disparity("9780123456789", "9780987654321")
        assert "D" in codes
        assert label == "conflict"


class TestMatchCanonicalToTrovePipeline:
    def test_end_to_end_matching(self, tmp_path: Path):
        # 1. Create synthetic canonical parquet
        era_records = [
            {
                "canonical_id": 1,
                "source_ids": [101, 102],
                "institutions": ["Univ A", "Univ B"],
                "n_submissions": 2,
                "is_multi_hep": True,
                "research_output_type": "Book",
                "title": "A History of Australian Architecture",
                "doi": None,
                "isbn": "9780123456789",
                "outlet_isbn": None,
                "reference_year": 2014,
                "outlet": None,
                "publisher": "Melbourne University Press",
            },
            {
                "canonical_id": 2,
                "source_ids": [201],
                "institutions": ["Univ C"],
                "n_submissions": 1,
                "is_multi_hep": False,
                "research_output_type": "Book Chapter",
                "title": "Indigenous Land Rights and Sovereignty",
                "doi": None,
                "isbn": None,
                "outlet_isbn": "9789876543210",
                "reference_year": 2015,
                "outlet": "Essays on Australian Legal History",
                "publisher": "Federation Press",
            },
            {
                "canonical_id": 3,
                "source_ids": [301],
                "institutions": ["Univ D"],
                "n_submissions": 1,
                "is_multi_hep": False,
                "research_output_type": "Journal Article",
                "title": "Unmatched Output Without Any Trove Record",
                "doi": "10.1001/missing.doi",
                "isbn": None,
                "outlet_isbn": None,
                "reference_year": 2013,
                "outlet": "Obscure Journal",
                "publisher": "Unknown",
            },
        ]
        canon_pq = tmp_path / "canonical.parquet"
        pq.write_table(pa.Table.from_pylist(era_records), canon_pq)

        # 2. Create synthetic Trove ISBN record (for canonical_id 1)
        trove_isbn_records = [
            {
                "isbn_normalized": "9780123456789",
                "status": "ok",
                "trove_work_id": "work-1111",
                "trove_url": "https://trove.nla.gov.au/work/1111",
                "title": "A History of Australian Architecture / by J. Smith",
                "title_source": "work",
            }
        ]
        trove_isbn_pq = tmp_path / "trove_isbn_2014.parquet"
        pq.write_table(pa.Table.from_pylist(trove_isbn_records), trove_isbn_pq)

        # 3. Create synthetic Trove Title candidate record (for canonical_id 2 via source_id 201)
        trove_title_records = [
            {
                "era_id": 201,
                "era_title": "Indigenous Land Rights and Sovereignty",
                "era_type": "book_chapter",
                "era_year": 2015,
                "status": "ok",
                "category": "research",
                "rank": 0,
                "trove_work_id": "work-2222",
                "trove_url": "https://trove.nla.gov.au/work/2222",
                "work_title": "Indigenous Land Rights and Sovereignty",
                "work_contributor": ["Doe, J."],
                "work_issued": "2015",
                "work_type": ["Article"],
                "edition_creators": ["Doe, J."],
                "edition_issued": ["2015"],
                "edition_subject": ["Law"],
                "edition_publisher": ["Federation Press"],
                "metadata_source": "ANU:IR",
            }
        ]
        shard_dir = tmp_path / "title_records" / "book_chapter"
        shard_dir.mkdir(parents=True)
        trove_title_pq = shard_dir / "2015.parquet"
        pq.write_table(pa.Table.from_pylist(trove_title_records), trove_title_pq)

        out_matches_pq = tmp_path / "out_trove_matches.parquet"
        out_disp_pq = tmp_path / "out_trove_disparities.parquet"

        stats = match_canonical_to_trove(
            canonical_pq=canon_pq,
            trove_isbn_glob=tmp_path / "trove_isbn_*.parquet",
            trove_title_glob=tmp_path / "title_records" / "*" / "*.parquet",
            output_matches_pq=out_matches_pq,
            output_disparities_pq=out_disp_pq,
        )

        assert stats["canonical_records"] == 3
        assert stats["matched_records"] == 2
        assert stats["tier1_isbn_matches"] == 1
        assert stats["tier2_title_matches"] == 1

        # Check matched table
        table_matches = pq.read_table(out_matches_pq).to_pylist()
        m_map = {r["canonical_id"]: r for r in table_matches}

        assert m_map[1]["trove_matched"] is True
        assert m_map[1]["match_tier"] == "isbn"
        assert m_map[1]["trove_work_id"] == "work-1111"
        assert m_map[1]["trove_url"] == "https://trove.nla.gov.au/work/1111"

        assert m_map[2]["trove_matched"] is True
        assert m_map[2]["match_tier"] == "title_candidate"
        assert m_map[2]["trove_work_id"] == "work-2222"
        assert m_map[2]["trove_category"] == "research"
        assert m_map[2]["trove_metadata_source"] == "ANU:IR"

        assert m_map[3]["trove_matched"] is False
        assert m_map[3]["match_tier"] is None

        # Check disparities table
        table_disp = pq.read_table(out_disp_pq).to_pylist()
        d_map = {r["canonical_id"]: r for r in table_disp}
        assert 1 in d_map
        assert 2 in d_map
        assert 3 not in d_map
        # Chapter 2 title was exact match
        assert d_map[2]["title_disparity_label"] == "exact"
        assert d_map[2]["title_olensky_codes"] == []
