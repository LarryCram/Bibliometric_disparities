"""string_processor.py: pre-Olensky structural feature detection - facts
about a raw string computed independent of any comparison target, not
disparity classification. Field-agnostic, so tested directly with plain
strings rather than through any specific field.
"""

from era.core.string_processor import StringProcessor

processor = StringProcessor()


class TestHtmlTexDetection:
    def test_html_tag_detected(self):
        result = processor.process("a title with <i>emphasis</i>")
        assert "has_html" in result.codes

    def test_tex_command_detected(self):
        result = processor.process(r"a title with \textbf{bold}")
        assert "has_tex" in result.codes

    def test_clean_text_flags_neither(self):
        result = processor.process("a perfectly ordinary title")
        assert "has_html" not in result.codes
        assert "has_tex" not in result.codes


class TestNonLatinScript:
    def test_non_latin_detected(self):
        result = processor.process("a title with 中文 characters")
        assert "has_non_latin" in result.codes

    def test_latin_with_diacritic_not_flagged(self):
        # a Latin letter carrying a diacritic is still Latin script -
        # distinct from a non-Latin alphabet entirely.
        result = processor.process("café culture")
        assert "has_non_latin" not in result.codes


class TestMojibake:
    def test_corrupted_encoding_detected(self):
        # "café" mis-decoded as Latin-1-into-UTF-8 mojibake.
        result = processor.process("cafÃ© culture")
        assert "has_mojibake" in result.codes

    def test_clean_text_not_flagged(self):
        result = processor.process("cafe culture")
        assert "has_mojibake" not in result.codes


class TestDaterange:
    def test_valid_canonical_daterange(self):
        result = processor.process("history of the war, 1914-1918")
        assert "has_daterange" in result.codes
        assert "daterange_valid" in result.codes

    def test_adjusted_daterange_with_odd_separator(self):
        result = processor.process("the years 1914 to 1918")
        # "to" isn't one of the recognised separator characters at all,
        # so this should NOT match a daterange - only dash-family
        # separators do (confirmed via the regex definition itself).
        assert "has_daterange" not in result.codes

    def test_en_dash_daterange_is_adjusted(self):
        result = processor.process("the war of 1914–1918")  # en dash
        assert "has_daterange" in result.codes
        # en dash isn't the canonical ASCII hyphen, so this counts as
        # "adjusted" rather than already-canonical.
        assert "daterange_adjusted" in result.codes

    def test_no_daterange_in_plain_title(self):
        result = processor.process("a title with no years in it at all")
        assert "has_daterange" not in result.codes


class TestSpellingDialect:
    def test_british_only_word_flags_gb_and_au(self):
        # confirmed real 2026-09-03: en_AU mostly but not exactly tracks
        # en_GB, and "colour" is absent from en_US - both GB and AU fire.
        result = processor.process("the colour of the sky")
        assert "has_en_GB" in result.codes
        assert "has_en_AU" in result.codes
        assert "has_en_US" not in result.codes

    def test_universal_word_not_flagged(self):
        result = processor.process("a study of the program")
        assert "has_en_US" not in result.codes
        assert "has_en_GB" not in result.codes
        assert "has_en_AU" not in result.codes

    def test_blocklisted_semantic_pair_not_flagged(self):
        # "programme"/"program" is on _SEMANTIC_BLOCKLIST (a pair that
        # looks like a spelling variant but risks a false positive) -
        # suppressed entirely regardless of what the dictionaries alone
        # would say, unlike "colour" above which has no such override.
        result = processor.process("the television programme")
        assert "has_en_GB" not in result.codes
        assert "has_en_US" not in result.codes

    def test_protected_phrase_not_flagged(self):
        result = processor.process("a history of the Australian Labor Party")
        # "labor" alone would flag as US-only, but the protected phrase
        # exempts it.
        assert "has_en_US" not in result.codes

    def test_bare_labor_outside_phrase_is_flagged(self):
        result = processor.process("workplace labor conditions")
        assert "has_en_US" in result.codes
