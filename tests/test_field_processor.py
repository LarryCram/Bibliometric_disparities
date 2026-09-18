"""field_processor.py: the factory dispatching (field, output_type) to a
shared processing module. Rule-processors (JournalTitleRuleProcessor,
CategoryTypeRuleProcessor, ConferenceNameRuleProcessor) are tested with
FRESH instances built on small, fake lookups rather than the real
ERA_JOURNALS/CATEGORY_VOCAB/CONFERENCE_VOCAB singletons - this decouples
the logic tests from the real data_persisted/ reference files (which
could change independently) and lets every branch (matched/unmatched/
no-id/leaked-URI/vocab-mismatch) be constructed directly instead of
hunted for in the real corpus. A handful of separate smoke tests at the
bottom confirm the real singletons load and contain known real entries.
"""

from era.core.olensky import DisparityResult, FreeTextDisparityClassifier, TransformStep
from era.core.field_processor import (
    CategoryTypeRuleProcessor,
    ConferenceNameRuleProcessor,
    ControlledVocabFieldProcessor,
    DisparityClassifierAdapter,
    FIELD_PROCESSORS,
    JournalTitleRuleProcessor,
    NumericDescriptorProcessor,
    get_processor,
    ERA_JOURNALS,
    CATEGORY_VOCAB,
    CONFERENCE_VOCAB,
)


# --- get_processor dispatch ---

class TestGetProcessor:
    def test_type_specific_entry_wins_over_wildcard(self):
        # extent used to be the type-specific example here; it's been
        # fully deregistered (2026-09-04), so use a field that's still
        # only ever registered as a (field, None) wildcard and confirm
        # the wildcard IS what's returned when no specific entry exists.
        assert get_processor("doi", "Journal Article") is get_processor("doi", None)

    def test_unregistered_field_raises_keyerror(self):
        import pytest
        with pytest.raises(KeyError):
            get_processor("not_a_real_field", None)


# --- ControlledVocabFieldProcessor (media) ---

class TestControlledVocabFieldProcessor:
    def setup_method(self):
        self.processor = ControlledVocabFieldProcessor()

    def test_leaked_system_uri_flagged_d(self):
        result = self.processor.process(
            "/dk/atira/pure/researchoutput/researchoutputtypes/nontextual/performance",
            "media", None,
        )
        assert result.wellformed is False
        assert "D" in result.codes

    def test_ordinary_value_wellformed(self):
        result = self.processor.process("Video", "media", None)
        assert result.wellformed is True
        assert result.codes == []


# --- NumericDescriptorProcessor (volume/issue/...) ---

class TestNumericDescriptorProcessor:
    def setup_method(self):
        self.processor = NumericDescriptorProcessor()

    def test_pure_digits_is_numeric(self):
        result = self.processor.process("213", "volume", None)
        assert result.wellformed is True  # is_numeric, not a normative claim
        assert result.codes == []

    def test_combined_volume_is_not_numeric_but_uncoded(self):
        # confirmed real 2026-09-04: "213-214" is a legitimate combined-
        # volume publishing convention, not an error - must NOT get D.
        result = self.processor.process("213-214", "journal_volume", None)
        assert result.wellformed is False
        assert result.codes == []

    def test_short_alternate_format_uncoded(self):
        result = self.processor.process("S2", "volume", None)
        assert result.wellformed is False
        assert result.codes == []

    def test_long_prose_flagged_d(self):
        result = self.processor.process(
            "Annual project report 2015-2016", "volume", None,
        )
        assert result.wellformed is False
        assert "D" in result.codes

    def test_colon_containing_value_flagged_d(self):
        result = self.processor.process("Spec: 30", "volume", None)
        assert "D" in result.codes


# --- JournalTitleRuleProcessor ---

class TestJournalTitleRuleProcessor:
    def setup_method(self):
        self.lookup = {"1": {"title": "Abstract and Applied Analysis"}}
        self.processor = JournalTitleRuleProcessor(self.lookup)

    def test_exact_match_wellformed(self):
        result = self.processor.process(
            "Abstract and Applied Analysis", "journal_title", None,
            raw_row={"era_journal_id": 1},
        )
        assert result.wellformed is True
        assert result.codes == []

    def test_case_and_whitespace_difference_still_wellformed(self):
        result = self.processor.process(
            "  ABSTRACT AND APPLIED ANALYSIS  ", "journal_title", None,
            raw_row={"era_journal_id": 1},
        )
        assert result.wellformed is True

    def test_genuine_mismatch_flagged_uncoded(self):
        result = self.processor.process(
            "Totally Different Journal", "journal_title", None,
            raw_row={"era_journal_id": 1},
        )
        assert result.wellformed is False
        assert result.codes == []

    def test_unmatched_id_is_not_checked(self):
        result = self.processor.process(
            "Some Journal", "journal_title", None,
            raw_row={"era_journal_id": 999999},
        )
        assert result.wellformed is None

    def test_missing_id_is_not_checked(self):
        result = self.processor.process(
            "Some Journal", "journal_title", None,
            raw_row={"era_journal_id": None},
        )
        assert result.wellformed is None

    def test_no_raw_row_is_not_checked(self):
        result = self.processor.process("Some Journal", "journal_title", None)
        assert result.wellformed is None


# --- CategoryTypeRuleProcessor ---

class TestCategoryTypeRuleProcessor:
    def setup_method(self):
        self.vocab = {"Curated Exhibition Event": {"exhibitionevent", "festival", "other"}}
        self.processor = CategoryTypeRuleProcessor(self.vocab)

    def test_valid_category_wellformed(self):
        result = self.processor.process(
            "Exhibition/Event", "category_type", "Curated Exhibition Event",
        )
        assert result.wellformed is True

    def test_invalid_category_uncoded_mismatch(self):
        result = self.processor.process(
            "Nonsense Category", "category_type", "Curated Exhibition Event",
        )
        assert result.wellformed is False
        assert result.codes == []

    def test_leaked_uri_flagged_d_even_if_it_could_never_match(self):
        result = self.processor.process(
            "/dk/atira/pure/foo", "category_type", "Curated Exhibition Event",
        )
        assert result.wellformed is False
        assert "D" in result.codes

    def test_output_type_with_no_vocab_entry_is_not_checked(self):
        result = self.processor.process("Something", "category_type", "Journal Article")
        assert result.wellformed is None


# --- ConferenceNameRuleProcessor ---

class TestConferenceNameRuleProcessor:
    def setup_method(self):
        self.classifier = FreeTextDisparityClassifier()
        self.vocab = {"51056": "spie optics and photonics"}
        self.processor = ConferenceNameRuleProcessor(self.classifier, self.vocab)

    def test_matching_conference_wellformed(self):
        result = self.processor.process(
            "SPIE Optics and Photonics", "conference_name", None,
            raw_row={"era_conference_id": "51056"},
        )
        assert result.wellformed is True

    def test_mismatched_conference_uncoded(self):
        result = self.processor.process(
            "Some Totally Different Conference", "conference_name", None,
            raw_row={"era_conference_id": "51056"},
        )
        assert result.wellformed is False

    def test_sentinel_id_not_checked_but_free_text_still_runs(self):
        # "00000" is never in the vocab (excluded at build time) - the
        # consensus check must not apply, but the free-text self-cleaning
        # still runs and still contributes its own codes/verdict.
        result = self.processor.process(
            "a  double  spaced conference", "conference_name", None,
            raw_row={"era_conference_id": "00000"},
        )
        assert result.wellformed is True
        assert "K" in result.codes

    def test_free_text_cleaning_still_applies_alongside_consensus_check(self):
        # confirmed real 2026-09-04: conference_name has genuine K/R/Q/S
        # disparities the free-text battery already catches - the
        # consensus layer must not replace that cleaning.
        result = self.processor.process(
            "SPIE Optics and Photonics.", "conference_name", None,
            raw_row={"era_conference_id": "51056"},
        )
        assert result.wellformed is True
        assert "R" in result.codes  # trailing period stripped
        assert result.cleaned_value == "spie optics and photonics"

    def test_processor_trusts_its_vocab_as_already_cleaned(self):
        # The processor itself does NOT clean the reference side - that
        # is _load_conference_vocab's job (tested directly below). This
        # documents that contract boundary: if a vocab is ever built from
        # uncleaned values again, THIS is where it would surface as a
        # spurious mismatch, exactly as it did in practice 2026-09-04
        # (era_conference_id 60360, raw value "...Consortium." with a
        # trailing period the free-text battery already strips).
        dirty_vocab = {"60360": "intellectbase international consortium."}
        processor = ConferenceNameRuleProcessor(self.classifier, dirty_vocab)
        result = processor.process(
            "Intellectbase International Consortium.", "conference_name", None,
            raw_row={"era_conference_id": "60360"},
        )
        assert result.wellformed is False


class TestLoadConferenceVocab:
    """Regression coverage for the actual function that had the bug -
    _load_conference_vocab must tally votes on the CLEANED value, not
    the raw string, or a formatting quirk in whichever raw row happens
    to be the majority (e.g. one HEP's trailing period) becomes the
    permanent reference every other cleaned candidate is wrongly
    compared against.
    """

    def test_votes_are_tallied_on_cleaned_value(self, tmp_path):
        import pyarrow as pa
        import pyarrow.parquet as pq
        from era.core.field_processor import _load_conference_vocab

        raw_path = tmp_path / "era_research_outputs_raw.parquet"
        table = pa.table({
            "era_conference_id": ["60360", "60360", "60360"],
            "conference_name": [
                "Intellectbase International Consortium.",
                "Intellectbase International Consortium.",
                "Intellectbase International Consortium.",
            ],
        })
        pq.write_table(table, raw_path)

        vocab = _load_conference_vocab(FreeTextDisparityClassifier(), path=raw_path)

        assert vocab["60360"] == "intellectbase international consortium"

    def test_sentinel_id_excluded_from_vocab(self, tmp_path):
        import pyarrow as pa
        import pyarrow.parquet as pq
        from era.core.field_processor import _load_conference_vocab

        raw_path = tmp_path / "era_research_outputs_raw.parquet"
        table = pa.table({
            "era_conference_id": ["00000", "00000", "12345"],
            "conference_name": ["Conference A", "Conference B", "Conference C"],
        })
        pq.write_table(table, raw_path)

        vocab = _load_conference_vocab(FreeTextDisparityClassifier(), path=raw_path)

        assert "00000" not in vocab
        assert vocab["12345"] == "conference c"

    def test_missing_path_returns_empty_dict(self, tmp_path):
        from era.core.field_processor import _load_conference_vocab

        vocab = _load_conference_vocab(FreeTextDisparityClassifier(), path=tmp_path / "nonexistent.parquet")

        assert vocab == {}


# --- ISBN/ISSN/standard_number/identifier via the real registered modules ---

class TestBookNumberFields:
    def test_isbn_accepts_hyphenated_and_bare(self):
        assert get_processor("isbn", None).process("978-3-16-148410-0", "isbn", None).wellformed
        assert get_processor("isbn", None).process("9783161484100", "isbn", None).wellformed

    def test_isbn_rejects_bad_checksum(self):
        result = get_processor("isbn", None).process("9999999999999", "isbn", None)
        assert result.wellformed is False

    def test_isbn_accepts_isbn10(self):
        result = get_processor("isbn", None).process("0-19-852663-6", "isbn", None)
        assert result.wellformed is True


class TestIssnField:
    def test_hyphenated_wellformed_no_extra_code(self):
        result = get_processor("issn", None).process("2049-3630", "issn", None)
        assert result.wellformed is True
        assert "R" not in result.codes

    def test_bare_form_repaired_and_tagged_r(self):
        result = get_processor("issn", None).process("20493630", "issn", None)
        assert result.wellformed is True
        assert result.cleaned_value == "2049-3630"
        assert "R" in result.codes

    def test_bad_checksum_rejected(self):
        result = get_processor("issn", None).process("1234-5678", "issn", None)
        assert result.wellformed is False


class TestStandardNumberField:
    def test_accepts_ismn_shape(self):
        result = get_processor("standard_number", None).process(
            "9790720105048", "standard_number", None,
        )
        assert result.wellformed is True

    def test_rejects_bare_digit_garbage(self):
        result = get_processor("standard_number", None).process("1", "standard_number", None)
        assert result.wellformed is False


class TestIdentifierField:
    def test_wos_clean_no_extra_code(self):
        result = get_processor("identifier", None).process(
            "WOS:000320969100004", "identifier", None,
        )
        assert result.wellformed is True
        assert "S" not in result.codes

    def test_bci_redundant_prefix_stripped_and_tagged_s(self):
        result = get_processor("identifier", None).process(
            "BCI:BCI201200059473", "identifier", None,
        )
        assert result.wellformed is True
        assert result.cleaned_value == "BCI:201200059473"
        assert "S" in result.codes

    def test_zoorec_abbreviated_prefix_stripped(self):
        result = get_processor("identifier", None).process(
            "ZOOREC:ZOOR15103016136", "identifier", None,
        )
        assert result.wellformed is True
        assert result.cleaned_value == "ZOOREC:15103016136"
        assert "S" in result.codes

    def test_medline_clean_no_extra_code(self):
        result = get_processor("identifier", None).process(
            "MEDLINE:12345678", "identifier", None,
        )
        assert result.wellformed is True
        assert "S" not in result.codes

    def test_unrecognised_prefix_malformed(self):
        result = get_processor("identifier", None).process(
            "UNKNOWN:12345", "identifier", None,
        )
        assert result.wellformed is False


# --- Real singletons: smoke tests only, not logic tests ---

class TestRealSingletonsLoad:
    def test_era_journals_loaded_with_known_entry(self):
        assert len(ERA_JOURNALS) > 20000
        assert ERA_JOURNALS["1459"]["title"] == "Journal of Materials Chemistry"

    def test_category_vocab_loaded_with_known_type(self):
        assert "Research Report for External Body" in CATEGORY_VOCAB
        assert len(CATEGORY_VOCAB["Research Report for External Body"]) >= 4

    def test_conference_vocab_loaded_and_excludes_sentinel(self):
        assert len(CONFERENCE_VOCAB) > 3000
        assert "00000" not in CONFERENCE_VOCAB

    def test_field_processors_registered_for_known_fields(self):
        for field in ("doi", "title", "journal_title", "category_type",
                      "conference_name", "identifier", "issn", "standard_number"):
            assert (field, None) in FIELD_PROCESSORS

    def test_extent_is_not_registered(self):
        # confirmed deliberate 2026-09-04 - extent left unflagged like
        # era_round/institution, not classified at all.
        assert not any(f == "extent" for f, _ in FIELD_PROCESSORS)
