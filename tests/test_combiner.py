"""Unit tests for StageCCombiner (Stage-C multi-HEP reconciliation)."""

from era.harmonization.combiner import StageCCombiner, DisjointSetUnion


class TestDisjointSetUnion:
    def test_basic_union_find(self):
        dsu = DisjointSetUnion(5)
        assert dsu.find(0) == 0
        assert dsu.find(1) == 1
        dsu.union(0, 1)
        assert dsu.find(0) == dsu.find(1)
        dsu.union(1, 2)
        assert dsu.find(0) == dsu.find(2)
        assert dsu.find(3) != dsu.find(0)


class TestStageCCombinerClustering:
    def _make_rec(
        self,
        id_,
        title,
        doi=None,
        doi_wellformed=True,
        identifier=None,
        identifier_wellformed=True,
        output_type="Journal Article",
        institution="University of Melbourne",
        year=2015,
    ):
        return {
            "id": id_,
            "title": title,
            "title_wellformed": True,
            "title_provenance": [],
            "doi": doi,
            "doi_wellformed": doi_wellformed if doi else None,
            "doi_provenance": [],
            "identifier": identifier,
            "identifier_wellformed": identifier_wellformed if identifier else None,
            "identifier_provenance": [],
            "research_output_type": output_type,
            "institution": institution,
            "reference_year": year,
            "outlet": "Journal of Physics",
            "outlet_provenance": [],
            "publisher": "Institute of Physics",
            "publisher_provenance": [],
        }

    def test_singletons_remain_separate(self):
        r1 = self._make_rec(1, "Unique Paper One", doi="10.1001/a")
        r2 = self._make_rec(2, "Unique Paper Two", doi="10.1001/b")
        clusters = StageCCombiner.cluster_records([r1, r2])
        assert len(clusters) == 2
        assert [0] in clusters
        assert [1] in clusters

    def test_cluster_by_doi(self):
        r1 = self._make_rec(1, "A great discovery in physics", doi="10.1001/shared", institution="Univ A")
        r2 = self._make_rec(2, "A Great Discovery in Physics", doi="10.1001/shared", institution="Univ B")
        clusters = StageCCombiner.cluster_records([r1, r2])
        assert len(clusters) == 1
        assert sorted(clusters[0]) == [0, 1]

    def test_cluster_by_identifier(self):
        r1 = self._make_rec(1, "A study on biology", identifier="WOS:0001", institution="Univ A")
        r2 = self._make_rec(2, "A study on biology", identifier="WOS:0001", institution="Univ B")
        clusters = StageCCombiner.cluster_records([r1, r2])
        assert len(clusters) == 1
        assert sorted(clusters[0]) == [0, 1]

    def test_cluster_by_title_without_conflict(self):
        # Univ A reports DOI, Univ B omits DOI (Code E case)
        r1 = self._make_rec(1, "Collaborative study across institutions", doi="10.1001/collab", institution="Univ A")
        r2 = self._make_rec(2, "Collaborative study across institutions", doi=None, institution="Univ B")
        clusters = StageCCombiner.cluster_records([r1, r2])
        assert len(clusters) == 1
        assert sorted(clusters[0]) == [0, 1]

    def test_generic_title_with_conflicting_dois_not_merged(self):
        # Two book chapters titled "Conclusions" in different books with different DOIs
        r1 = self._make_rec(1, "Conclusions and future outlook", doi="10.1001/book1", output_type="Book Chapter")
        r2 = self._make_rec(2, "Conclusions and future outlook", doi="10.1001/book2", output_type="Book Chapter")
        clusters = StageCCombiner.cluster_records([r1, r2])
        # Must NOT merge into 1 cluster because they have conflicting DOIs
        assert len(clusters) == 2


class TestStageCCombinerReconciliation:
    def _make_rec(
        self,
        id_,
        title,
        doi=None,
        doi_wellformed=True,
        identifier=None,
        identifier_wellformed=True,
        output_type="Journal Article",
        institution="University of Sydney",
        year=2015,
        title_prov=None,
        doi_prov=None,
    ):
        rec = {
            "id": id_,
            "title": title,
            "title_wellformed": True,
            "title_provenance": title_prov or [],
            "title_has_html": False,
            "title_has_tex": False,
            "title_has_non_latin": False,
            "title_has_mojibake": False,
            "title_has_daterange": False,
            "title_daterange_status": None,
            "title_has_en_us": False,
            "title_has_en_gb": False,
            "title_has_en_au": False,
            "doi": doi,
            "doi_wellformed": doi_wellformed if doi else None,
            "doi_provenance": doi_prov or [],
            "identifier": identifier,
            "identifier_wellformed": identifier_wellformed if identifier else None,
            "identifier_provenance": [],
            "research_output_type": output_type,
            "institution": institution,
            "reference_year": year,
            "outlet": "Nature Communications",
            "outlet_provenance": [],
            "publisher": "Springer Nature",
            "publisher_provenance": [],
        }
        for f in [
            "place_of_publication", "category_type", "conference_name", "era_conference_id",
            "era_journal_id", "isbn", "issn", "issue", "journal_issue", "journal_title",
            "journal_volume", "media", "notes", "outlet_editor", "outlet_isbn",
            "outlet_issue", "outlet_venue", "outlet_volume", "standard_number", "volume",
        ]:
            rec[f] = None
            rec[f"{f}_provenance"] = []
        return rec

    def test_reconcile_singleton(self):
        r = self._make_rec(100, "Quantum entanglement in semiconductors", doi="10.1038/s41467-015-1")
        canon = StageCCombiner.reconcile_cluster([r])
        assert canon["canonical_id"] == 100
        assert canon["source_ids"] == [100]
        assert canon["institutions"] == ["University of Sydney"]
        assert canon["n_submissions"] == 1
        assert canon["is_multi_hep"] is False
        assert canon["title"] == "Quantum entanglement in semiconductors"
        assert canon["doi"] == "10.1038/s41467-015-1"
        assert canon["has_title_disparity"] is False
        assert canon["has_doi_disparity"] is False
        assert canon["has_year_disparity"] is False

    def test_reconcile_multi_hep_doi_omission_code_e(self):
        r1 = self._make_rec(1, "Advanced machine learning in oncology", doi="10.1016/j.onc.2014.01", institution="Univ A")
        r2 = self._make_rec(2, "Advanced machine learning in oncology", doi=None, institution="Univ B")
        canon = StageCCombiner.reconcile_cluster([r1, r2])

        assert canon["source_ids"] == [1, 2]
        assert set(canon["institutions"]) == {"Univ A", "Univ B"}
        assert canon["n_submissions"] == 2
        assert canon["is_multi_hep"] is True
        assert canon["doi"] == "10.1016/j.onc.2014.01"
        assert "E" in canon["doi_provenance"]
        assert canon["has_doi_disparity"] is True

    def test_reconcile_multi_hep_year_disparity_code_t(self):
        r1 = self._make_rec(1, "Study on astrophysics", doi="10.1001/astro", institution="Univ A", year=2014)
        r2 = self._make_rec(2, "Study on astrophysics", doi="10.1001/astro", institution="Univ B", year=2015)
        canon = StageCCombiner.reconcile_cluster([r1, r2])

        assert canon["n_submissions"] == 2
        assert "T" in canon["reference_year_provenance"]
        assert canon["has_year_disparity"] is True

    def test_reconcile_multi_hep_title_punctuation_disparity(self):
        r1 = self._make_rec(1, "Health and wellbeing: a national survey", doi="10.1001/health", institution="Univ A")
        r2 = self._make_rec(2, "Health and wellbeing a national survey", doi="10.1001/health", institution="Univ B")
        r3 = self._make_rec(3, "Health and wellbeing: a national survey", doi="10.1001/health", institution="Univ C")
        canon = StageCCombiner.reconcile_cluster([r1, r2, r3])

        assert canon["n_submissions"] == 3
        # Mode title is chosen (with colon)
        assert canon["title"] == "Health and wellbeing: a national survey"
        assert canon["has_title_disparity"] is True
        # Punctuation disparity classified as Olensky code R
        assert "R" in canon["title_provenance"]
