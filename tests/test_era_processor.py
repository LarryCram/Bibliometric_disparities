"""era_processor.py: the presence-stage NULL_SENTINELS logic, and the two
cross-record repair passes (_fix_indels_within_clusters,
_repair_truncated_in_records) - both already pure functions over a plain
list of dicts (2026-09-04 refactor, done specifically so they could be
tested without needing a real parquet file on disk), so tested directly
with small synthetic record lists rather than the full real dataset.
"""

from era.harmonization.era_processor import ERAProcessor, NULL_SENTINELS


# --- NULL_SENTINELS presence-stage handling ---

class TestClassifyFieldPresence:
    def test_none_is_field_null(self):
        result = ERAProcessor.classify_field({"outlet": None, "research_output_type": "Journal Article"}, "outlet")
        assert result["presence"] == "FIELD_NULL"

    def test_empty_string_is_field_null(self):
        result = ERAProcessor.classify_field({"outlet": "", "research_output_type": "Journal Article"}, "outlet")
        assert result["presence"] == "FIELD_NULL"

    def test_unknown_sentinel_is_field_null(self):
        # confirmed real 2026-09-03: "Unknown" appears 12,510 times in
        # extent - a disguised missing value, not a value to clean.
        result = ERAProcessor.classify_field(
            {"standard_number": "Unknown", "research_output_type": "Journal Article"}, "standard_number",
        )
        assert result["presence"] == "FIELD_NULL"

    def test_sentinel_check_is_case_insensitive(self):
        result = ERAProcessor.classify_field(
            {"standard_number": "NULL", "research_output_type": "Journal Article"}, "standard_number",
        )
        assert result["presence"] == "FIELD_NULL"

    def test_real_value_is_field_present(self):
        result = ERAProcessor.classify_field(
            {"publisher": "IEEE", "research_output_type": "Journal Article"}, "publisher",
        )
        assert result["presence"] == "FIELD_PRESENT"
        assert result["cleaned_value"] == "ieee"

    def test_every_documented_sentinel_is_recognised(self):
        for sentinel in NULL_SENTINELS:
            result = ERAProcessor.classify_field(
                {"standard_number": sentinel, "research_output_type": "Journal Article"}, "standard_number",
            )
            assert result["presence"] == "FIELD_NULL", f"{sentinel!r} should be FIELD_NULL"


# --- _fix_indels_within_clusters ---

class TestFixIndelsWithinClusters:
    def _record(self, id_, doi, title, doi_wellformed=True):
        return {
            "id": id_, "doi": doi, "doi_wellformed": doi_wellformed,
            "title": title, "title_provenance": [],
        }

    def test_missing_space_fixed_and_coded(self):
        records = [
            self._record(1, "10.1/x", "a titlehere"),
            self._record(2, "10.1/x", "a title here"),
        ]
        n_fixed = ERAProcessor._fix_indels_within_clusters(records, "doi", "doi_wellformed")
        assert n_fixed == 1
        assert records[0]["title"] == "a title here"
        assert records[0]["title_provenance"] == ["K"]
        assert records[1]["title"] == "a title here"  # already the longer form, untouched

    def test_singleton_cluster_untouched(self):
        records = [self._record(1, "10.1/x", "a title here")]
        n_fixed = ERAProcessor._fix_indels_within_clusters(records, "doi", "doi_wellformed")
        assert n_fixed == 0
        assert records[0]["title"] == "a title here"

    def test_not_wellformed_key_excludes_from_clustering(self):
        # two records share the same doi STRING but it's not marked
        # wellformed - must not be trusted as a real join key.
        records = [
            self._record(1, "10.1/x", "a titlehere", doi_wellformed=False),
            self._record(2, "10.1/x", "a title here", doi_wellformed=False),
        ]
        n_fixed = ERAProcessor._fix_indels_within_clusters(records, "doi", "doi_wellformed")
        assert n_fixed == 0

    def test_genuinely_different_titles_untouched(self):
        records = [
            self._record(1, "10.1/x", "completely different title"),
            self._record(2, "10.1/x", "a title here"),
        ]
        n_fixed = ERAProcessor._fix_indels_within_clusters(records, "doi", "doi_wellformed")
        assert n_fixed == 0
        assert records[0]["title"] == "completely different title"

    def test_second_pass_sees_first_passs_fix(self):
        # doi and identifier clusters can overlap - the second pass must
        # operate on whatever the first pass already wrote, not a frozen
        # snapshot.
        records = [
            {
                "id": 1, "doi": "10.1/x", "doi_wellformed": True,
                "identifier": "WOS:1", "identifier_wellformed": True,
                "title": "a titlehere", "title_provenance": [],
            },
            {
                "id": 2, "doi": "10.1/x", "doi_wellformed": True,
                "identifier": "WOS:2", "identifier_wellformed": True,
                "title": "a title here", "title_provenance": [],
            },
        ]
        n1 = ERAProcessor._fix_indels_within_clusters(records, "doi", "doi_wellformed")
        n2 = ERAProcessor._fix_indels_within_clusters(records, "identifier", "identifier_wellformed")
        assert n1 == 1
        assert n2 == 0  # already identical after the first pass, nothing left to fix
        assert records[0]["title"] == "a title here"


# --- _repair_truncated_in_records ---

class TestRepairTruncatedInRecords:
    def _record(self, value):
        return {"publisher": value, "publisher_provenance": []}

    def test_unique_match_repaired_and_tagged_f(self):
        records = [
            self._record("association of researchers for construction man.."),
            self._record("association of researchers for construction management"),
        ]
        n_flagged, n_repaired = ERAProcessor._repair_truncated_in_records(records, "publisher")
        assert n_flagged == 1
        assert n_repaired == 1
        assert records[0]["publisher"] == "association of researchers for construction management"
        assert records[0]["publisher_provenance"] == ["F"]
        # the untruncated model record itself is untouched
        assert records[1]["publisher_provenance"] == []

    def test_no_match_flagged_but_unrepaired(self):
        records = [self._record("ieee - the institute of electrical and electronic engi..")]
        n_flagged, n_repaired = ERAProcessor._repair_truncated_in_records(records, "publisher")
        assert n_flagged == 1
        assert n_repaired == 0
        assert records[0]["publisher"] == "ieee - the institute of electrical and electronic engi.."
        assert records[0]["publisher_provenance"] == ["F"]

    def test_ambiguous_match_flagged_but_unrepaired(self):
        # two DIFFERENT full forms share the same truncated prefix - must
        # not guess which one is right.
        records = [
            self._record("acme corporation for x.."),
            self._record("acme corporation for xylophones"),
            self._record("acme corporation for xrays"),
        ]
        n_flagged, n_repaired = ERAProcessor._repair_truncated_in_records(records, "publisher")
        assert n_flagged == 1
        assert n_repaired == 0
        assert records[0]["publisher"] == "acme corporation for x.."
        assert records[0]["publisher_provenance"] == ["F"]

    def test_untruncated_values_untouched(self):
        records = [self._record("a perfectly normal publisher")]
        n_flagged, n_repaired = ERAProcessor._repair_truncated_in_records(records, "publisher")
        assert n_flagged == 0
        assert n_repaired == 0
        assert records[0]["publisher_provenance"] == []

    def test_null_value_skipped(self):
        records = [{"publisher": None, "publisher_provenance": []}]
        n_flagged, n_repaired = ERAProcessor._repair_truncated_in_records(records, "publisher")
        assert n_flagged == 0
        assert records[0]["publisher_provenance"] == []
