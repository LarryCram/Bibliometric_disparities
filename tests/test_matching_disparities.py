"""Unit tests for ERA vs. OpenAlex external disparity evaluation (Stage 2).
"""

import pytest
from era.matching.evaluate_disparities import (
    classify_title_disparity,
    classify_year_disparity,
    classify_doi_disparity,
)


class TestClassifyTitleDisparity:
    def test_exact_match(self):
        codes, label = classify_title_disparity(
            "Deep Learning in Astronomy", "Deep Learning in Astronomy"
        )
        assert codes == []
        assert label == "exact"

    def test_case_and_punctuation_code_r(self):
        codes, label = classify_title_disparity(
            "Deep Learning in Astronomy: A Survey",
            "Deep learning in astronomy: a survey",
        )
        assert "R" in codes
        assert label == "punctuation_or_case"

    def test_subtitle_truncation_code_f(self):
        codes, label = classify_title_disparity(
            "Bibliometric Disparities: An Australian Study",
            "Bibliometric Disparities",
        )
        assert "F" in codes
        assert label == "cropped_subtitle"

    def test_single_char_indel_code_b(self):
        codes, label = classify_title_disparity(
            "Cosmological Parameters from Planck",
            "Cosmological Parameter from Planck",
        )
        assert "B" in codes
        assert label == "spelling_indel"

    def test_html_and_markup_code_q(self):
        codes, label = classify_title_disparity(
            "Quantum Transport in Graphene & Carbon Nanotubes",
            "Quantum Transport in Graphene &amp; Carbon Nanotubes",
        )
        assert "Q" in codes
        assert label == "markup_or_entity"

    def test_discordant_code_d(self):
        codes, label = classify_title_disparity(
            "General Relativity and Gravitation",
            "Clinical Outcomes in Cardiac Surgery",
        )
        assert "D" in codes
        assert label == "discordant"


class TestClassifyYearDisparity:
    def test_same_year(self):
        codes, delta = classify_year_disparity(2014, 2014)
        assert codes == []
        assert delta == 0

    def test_off_by_one_year_code_t(self):
        codes, delta = classify_year_disparity(2014, 2015)
        assert "T" in codes
        assert delta == 1

    def test_off_by_multiple_years_code_t(self):
        codes, delta = classify_year_disparity(2011, 2014)
        assert "T" in codes
        assert delta == 3

    def test_missing_year(self):
        codes, delta = classify_year_disparity(2014, None)
        assert codes == ["E"]
        assert delta is None


class TestClassifyDoiDisparity:
    def test_identical_dois(self):
        codes = classify_doi_disparity("10.1007/s10509-012-1000-0", "10.1007/s10509-012-1000-0")
        assert codes == []

    def test_doi_omission_code_e(self):
        codes1 = classify_doi_disparity("10.1007/s10509-012-1000-0", None)
        assert codes1 == ["E"]

        codes2 = classify_doi_disparity(None, "10.1007/s10509-012-1000-0")
        assert codes2 == ["E"]

    def test_doi_conflict_code_d(self):
        codes = classify_doi_disparity("10.1007/s10509-012-1000-0", "10.1016/j.jneumeth.2012.01.001")
        assert codes == ["D"]
