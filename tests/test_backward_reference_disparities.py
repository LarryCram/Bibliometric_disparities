"""Unit tests for metadata-only backward reference resolution diagnostics."""

import pytest
from era.analysis.backward_reference_disparities import (
    ReferenceItem,
    InversionAnomaly,
    detect_alphabetical_inversions,
    compare_scopus_openalex_references,
    detect_obscure_historical_outliers,
    extract_first_surname,
    normalize_title,
)


def test_extract_first_surname():
    assert extract_first_surname("Bell, S.; Quiggin, J.") == "BELL"
    assert extract_first_surname("Bjornlund H.") == "BJORNLUND"
    assert extract_first_surname("Bjornlund, H.") == "BJORNLUND"
    assert extract_first_surname("Kepa Brian Morgan") == "MORGAN"
    assert extract_first_surname("") is None
    assert extract_first_surname(None) is None


def test_normalize_title():
    raw = "The Limits of Markets: The Politics of Water Management in Rural Australia!"
    norm = normalize_title(raw)
    assert norm == "thelimitsofmarketsthepoliticsofwatermanagementinruralaustralia"
    assert normalize_title(None) == ""


def test_detect_alphabetical_inversions_pure_alphabetical():
    refs = [
        ReferenceItem(position=1, source_index="scopus", title="T1", authors="Adams, A.", first_surname="ADAMS", year=2000, doi=None),
        ReferenceItem(position=2, source_index="scopus", title="T2", authors="Bell, B.", first_surname="BELL", year=2002, doi=None),
        ReferenceItem(position=3, source_index="scopus", title="T3", authors="Cook, C.", first_surname="COOK", year=2005, doi=None),
        ReferenceItem(position=4, source_index="scopus", title="T4", authors="Davis, D.", first_surname="DAVIS", year=2008, doi=None),
        ReferenceItem(position=5, source_index="scopus", title="T5", authors="Evans, E.", first_surname="EVANS", year=2010, doi=None),
    ]
    is_alpha, inversions = detect_alphabetical_inversions(refs)
    assert is_alpha is True
    assert len(inversions) == 0


def test_detect_alphabetical_inversions_with_anomaly():
    # An 'Adams' reference mistakenly inserted at position 4 after 'Davis'
    refs = [
        ReferenceItem(position=1, source_index="scopus", title="T1", authors="Bell, B.", first_surname="BELL", year=2002, doi=None),
        ReferenceItem(position=2, source_index="scopus", title="T2", authors="Cook, C.", first_surname="COOK", year=2005, doi=None),
        ReferenceItem(position=3, source_index="scopus", title="T3", authors="Davis, D.", first_surname="DAVIS", year=2008, doi=None),
        ReferenceItem(position=4, source_index="scopus", title="T4", authors="Adams, A.", first_surname="ADAMS", year=2000, doi=None),
        ReferenceItem(position=5, source_index="scopus", title="T5", authors="Evans, E.", first_surname="EVANS", year=2010, doi=None),
    ]
    is_alpha, inversions = detect_alphabetical_inversions(refs)
    assert is_alpha is True
    assert len(inversions) == 1
    assert inversions[0].position == 4
    assert inversions[0].curr_surname == "ADAMS"
    assert inversions[0].prev_surname == "DAVIS"
    assert inversions[0].is_major is True
    assert inversions[0].letter_distance == ord("D") - ord("A")


def test_compare_scopus_openalex_references():
    s_refs = [
        ReferenceItem(position=1, source_index="scopus", title="Common paper on hydrology", authors="Smith, J.", first_surname="SMITH", year=2010, doi="10.1001/a"),
        ReferenceItem(position=2, source_index="scopus", title="Unique scopus study of rivers", authors="Jones, B.", first_surname="JONES", year=2012, doi="10.1001/b"),
    ]
    o_refs = [
        ReferenceItem(position=1, source_index="openalex", title="Common paper on hydrology", authors=None, first_surname=None, year=2010, doi="10.1001/a"),
        ReferenceItem(position=2, source_index="openalex", title="Different openalex target on canals", authors=None, first_surname=None, year=2011, doi="10.1001/c"),
    ]
    comp = compare_scopus_openalex_references(
        doi="10.1016/sample",
        citing_year=2014,
        scopus_refs=s_refs,
        openalex_refs=o_refs,
    )
    assert comp.scopus_count == 2
    assert comp.openalex_count == 2
    assert len(comp.consensus_titles) == 1
    assert len(comp.scopus_only_targets) == 1
    assert len(comp.openalex_only_targets) == 1
    assert comp.jaccard_similarity == 1 / 3


def test_detect_obscure_historical_outliers():
    refs = [
        ReferenceItem(position=1, source_index="scopus", title="Modern study", authors="Smith", first_surname="SMITH", year=2012, doi=None, cited_by_count=150),
        ReferenceItem(position=2, source_index="scopus", title="Obscure ancient note", authors="Old", first_surname="OLD", year=1980, doi=None, cited_by_count=2),
        ReferenceItem(position=3, source_index="scopus", title="Darwin foundational classic", authors="Darwin", first_surname="DARWIN", year=1859, doi=None, cited_by_count=50000),
    ]
    outliers = detect_obscure_historical_outliers(refs, citing_year=2014, min_age_gap=20, max_citations=5)
    assert len(outliers) == 1
    assert outliers[0].title == "Obscure ancient note"
    assert outliers[0].year == 1980


def test_detect_topological_ghost_orphans():
    from era.analysis.backward_reference_disparities import detect_topological_ghost_orphans

    refs = [
        ReferenceItem(position=1, source_index="scopus", title="Water markets in rural Australia", authors="A", first_surname="A", year=2010, doi=None),
        ReferenceItem(position=2, source_index="scopus", title="Irrigation and water management in rural basins", authors="B", first_surname="B", year=2012, doi=None),
        ReferenceItem(position=3, source_index="scopus", title="Policy implications of water rights in Australia", authors="C", first_surname="C", year=2008, doi=None),
        ReferenceItem(position=4, source_index="scopus", title="Catchment basins and hydrological flows", authors="D", first_surname="D", year=2011, doi=None),
        ReferenceItem(position=5, source_index="scopus", title="Locomotive pressure valves in steam boilers", authors="E", first_surname="E", year=1985, doi=None),  # orphan
    ]
    orphans = detect_topological_ghost_orphans(refs, citing_year=2014, min_age_gap=15)
    assert len(orphans) == 1
    assert orphans[0].title == "Locomotive pressure valves in steam boilers"
    assert orphans[0].year == 1985
