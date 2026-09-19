"""Unit tests for OpenAlex-only preprocessing, internal Olensky classification,
lifecycle version drift, and ORCID temporal provenance checks.
"""

import pytest
from era.sources.openalex.preprocess import (
    classify_openalex_title,
    detect_subtitle_truncation,
    is_pre_orcid_publication,
    evaluate_lifecycle_version_drift,
    evaluate_authorship_drift,
)


class TestClassifyOpenAlexTitle:
    def test_clean_title_has_no_codes(self):
        res = classify_openalex_title("Machine Learning in High Energy Physics")
        assert res["cleaned_title"] == "Machine Learning in High Energy Physics"
        assert res["codes"] == []

    def test_html_entity_detected_code_q(self):
        res = classify_openalex_title("Quantum transport in graphene &amp; carbon nanotubes")
        assert "Q" in res["codes"]
        assert "&amp;" not in res["cleaned_title"]
        assert "&" in res["cleaned_title"]

    def test_latex_formula_detected_code_q(self):
        res = classify_openalex_title("Measurement of $CP$ violation in $B^0 \\to K_S^0 \\pi^0$ decays")
        assert "Q" in res["codes"]
        assert res["has_tex"] is True

    def test_whitespace_run_detected_code_k(self):
        res = classify_openalex_title("Deep   Neural   Networks for  Astronomy")
        assert "K" in res["codes"]
        assert res["cleaned_title"] == "Deep Neural Networks for Astronomy"

    def test_cropped_ellipsis_detected_code_f(self):
        res = classify_openalex_title("Studies in Australian higher education policy..")
        assert "F" in res["codes"]
        assert res["is_cropped"] is True


class TestDetectSubtitleTruncation:
    def test_colon_subtitle_truncation(self):
        full = "Bibliometric Disparities: A Study of Australian Research Outputs"
        short = "Bibliometric Disparities"
        assert detect_subtitle_truncation(short, full) is True
        assert detect_subtitle_truncation(full, short) is True

    def test_hyphen_subtitle_truncation(self):
        full = "Australian Higher Education - Trends and Challenges"
        short = "Australian Higher Education"
        assert detect_subtitle_truncation(short, full) is True

    def test_unrelated_titles_not_truncated(self):
        t1 = "Machine Learning for Healthcare"
        t2 = "Machine Learning for Autonomous Vehicles"
        assert detect_subtitle_truncation(t1, t2) is False


class TestTemporalOrcidCheck:
    def test_pre_launch_years_are_flagged(self):
        assert is_pre_orcid_publication(2009) is True
        assert is_pre_orcid_publication(2010) is True
        assert is_pre_orcid_publication(2011) is True

    def test_post_launch_years_are_not_flagged(self):
        assert is_pre_orcid_publication(2013) is False
        assert is_pre_orcid_publication(2015) is False
        assert is_pre_orcid_publication(None) is False


class TestLifecycleVersionDrift:
    def test_single_version_has_no_divergence(self):
        locs = [
            {"version": "publishedVersion", "source_name": "Physical Review D", "is_oa": True}
        ]
        res = evaluate_lifecycle_version_drift(locs)
        assert res["has_multiple_versions"] is False
        assert res["has_host_divergence"] is False

    def test_preprint_and_published_version_divergence(self):
        locs = [
            {"version": "submittedVersion", "source_name": "arXiv", "is_oa": True},
            {"version": "publishedVersion", "source_name": "Physical Review Letters", "is_oa": False},
        ]
        res = evaluate_lifecycle_version_drift(locs)
        assert res["has_multiple_versions"] is True
        assert res["has_host_divergence"] is True
        assert "submittedVersion" in res["versions"]
        assert "publishedVersion" in res["versions"]


class TestAuthorshipDrift:
    def test_identical_authorship_has_no_drift(self):
        auth1 = [
            {"author_id": "A1", "author_position": "first", "author_rank": 1},
            {"author_id": "A2", "author_position": "last", "author_rank": 2},
        ]
        auth2 = [
            {"author_id": "A1", "author_position": "first", "author_rank": 1},
            {"author_id": "A2", "author_position": "last", "author_rank": 2},
        ]
        res = evaluate_authorship_drift(auth1, auth2)
        assert res["author_count_diff"] == 0
        assert res["has_reordering"] is False

    def test_author_count_and_reordering_drift(self):
        # Preprint had 2 authors, published version added a 3rd and swapped 1 & 2
        auth_preprint = [
            {"author_id": "A1", "author_position": "first", "author_rank": 1},
            {"author_id": "A2", "author_position": "last", "author_rank": 2},
        ]
        auth_published = [
            {"author_id": "A2", "author_position": "first", "author_rank": 1},
            {"author_id": "A1", "author_position": "middle", "author_rank": 2},
            {"author_id": "A3", "author_position": "last", "author_rank": 3},
        ]
        res = evaluate_authorship_drift(auth_preprint, auth_published)
        assert res["author_count_diff"] == 1
        assert res["has_reordering"] is True
