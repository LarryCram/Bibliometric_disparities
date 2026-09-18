"""Olensky-coded disparity classification: DisparityClassifier is a Template
Method - the classification procedure is fixed, only the canonicalize_steps,
transform_battery and well-formedness rule vary per field. The same
classifier instance is meant to be reused at every layer where two
bibliographic value representations need reconciling: self-cleaning a
single raw value, merging two ERA HEPs' values for the same output, and
(later) comparing a canonical ERA value against its linked
OpenAlex/Scopus/Trove/WOS counterpart.

Every operation applied to a raw value is logged, whether or not it has an
Olensky code - nothing is ever applied silently. `codes` (the paper-facing
Olensky vocabulary) is a derived view over `transform_log` (the full,
step-by-step audit trail), not an independently-tracked list, so the two
can never disagree.

IAC codes are Olensky (2015)'s taxonomy, reproduced in full in
docs/era_quality_review.md. Only codes that are actually load-bearing for a
given field's canonicalize_steps/transform_battery are referenced here -
see each concrete classifier's docstring for which codes it uses and why.
"""

import difflib
import re
import unicodedata
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Callable


@dataclass
class Transform:
    code: str | None
    name: str
    func: Callable[[str], str]


@dataclass
class TransformStep:
    name: str
    effect: str  # "not_attempted" | "no_effect" | "changed"
    iac_code: str | None = None


@dataclass
class DisparityResult:
    cleaned_value: str | None
    wellformed: bool
    transform_log: list[TransformStep] = field(default_factory=list)

    @property
    def codes(self) -> list[str]:
        return [s.iac_code for s in self.transform_log if s.effect == "changed" and s.iac_code]


class DisparityClassifier(ABC):
    @property
    @abstractmethod
    def canonicalize_steps(self) -> list[Transform]:
        """Ordered, UNCONDITIONAL hygiene steps - always run, on every
        value, regardless of well-formedness, each diffed individually
        (before vs. after that one step) to build the transform log. A step
        with code=None is pure representation with no Olensky code (e.g.
        lowercasing, NFC composition - zero content difference). A step
        with a code is still unconditional and deterministic, but is
        tagged anyway: determinism and "worth recording as a disparity"
        are orthogonal, as established for K (Space)."""

    @property
    @abstractmethod
    def transform_battery(self) -> list[Transform]:
        """Ordered candidate transforms tried only if canonicalization
        alone isn't well-formed, via leave-one-out ablation to determine
        which are actually necessary."""

    @abstractmethod
    def is_wellformed(self, value: str) -> bool:
        """Structural validity check for this field, applied after
        canonicalisation and after any battery transforms."""

    def _run_canonicalize(self, raw_value):
        value = raw_value
        log = []
        for t in self.canonicalize_steps:
            before = value
            value = t.func(value)
            if value != before:
                log.append(TransformStep(name=t.name, effect="changed", iac_code=t.code))
            else:
                log.append(TransformStep(name=t.name, effect="no_effect", iac_code=None))
        return value, log

    def _apply_all(self, value, exclude=None):
        result = value
        for t in self.transform_battery:
            if t is exclude:
                continue
            result = t.func(result)
        return result

    def classify_wellformedness(self, raw_value: str) -> DisparityResult:
        """Stage B, self-clean: canonicalize (unconditional, logged step by
        step), then check well-formedness. If not well-formed, apply the
        full transform battery, then determine which battery transforms
        were actually necessary via leave-one-out ablation - a transform
        not attempted at all (canonical form was already well-formed) is
        distinguished from one attempted but found unnecessary, and both
        are distinguished from one found necessary (which contributes its
        code to `codes` via the transform log).
        """
        canon, log = self._run_canonicalize(raw_value)

        if self.is_wellformed(canon):
            for t in self.transform_battery:
                log.append(TransformStep(name=t.name, effect="not_attempted"))
            return DisparityResult(cleaned_value=canon, wellformed=True, transform_log=log)

        fully_cleaned = self._apply_all(canon)
        if not self.is_wellformed(fully_cleaned):
            for t in self.transform_battery:
                log.append(TransformStep(name=t.name, effect="no_effect"))
            return DisparityResult(cleaned_value=fully_cleaned, wellformed=False, transform_log=log)

        for t in self.transform_battery:
            without = self._apply_all(canon, exclude=t)
            if not self.is_wellformed(without):
                log.append(TransformStep(name=t.name, effect="changed", iac_code=t.code))
            else:
                log.append(TransformStep(name=t.name, effect="no_effect"))
        return DisparityResult(cleaned_value=fully_cleaned, wellformed=True, transform_log=log)

    def classify_match(self, value_a: str, value_b: str) -> DisparityResult:
        """HEP-merge / external-link: do value_a and value_b denote the same
        thing once canonicalised and (if needed) battery-cleaned? Cleans
        each side independently via classify_wellformedness, then compares.
        transform_log is both sides' logs concatenated, so codes is the
        union of both sides' necessary codes; empty codes with
        wellformed=True and cleaned_value set means the two were already
        identical after canonicalisation alone.
        """
        result_a = self.classify_wellformedness(value_a)
        result_b = self.classify_wellformedness(value_b)
        if result_a.cleaned_value == result_b.cleaned_value:
            return DisparityResult(
                cleaned_value=result_a.cleaned_value, wellformed=True,
                transform_log=result_a.transform_log + result_b.transform_log,
            )
        return DisparityResult(cleaned_value=None, wellformed=False, transform_log=[])


class FixedFormatTextDisparityClassifier(DisparityClassifier):
    """Generic, constructor-parameterized DisparityClassifier for any field
    whose correctness is "matches this external grammar" - a pattern to
    check against, not a per-field bespoke class. DOI, ISBN/ISSN, the
    WOS/MEDLINE identifier field, plain numeric-string fields (volume,
    issue, era_conference_id), and reference_year are all this same shape
    underneath: canonicalize + a small battery tried via leave-one-out
    ablation + a regex the final value must match. Each gets its own
    pattern/canonicalize/battery *instance*, not its own class - "own
    pattern, not own module" (2026-09-03).
    """

    def __init__(self, pattern, canonicalize_steps=None, transform_battery=None):
        self._pattern = pattern
        self._canonicalize_steps = canonicalize_steps or []
        self._transform_battery = transform_battery or []

    @property
    def canonicalize_steps(self):
        return self._canonicalize_steps

    @property
    def transform_battery(self):
        return self._transform_battery

    def is_wellformed(self, value):
        return bool(self._pattern.fullmatch(value))


class ChecksumTextDisparityClassifier(FixedFormatTextDisparityClassifier):
    """FixedFormatTextDisparityClassifier plus a real checksum requirement
    - for ISBN/ISSN/ISMN, character-set-only matching (a-la the original
    isbn pattern [0-9X\\-]+) accepts any digit/hyphen combination
    regardless of length, so it can never actually catch a malformed
    identifier (confirmed 2026-09-04: 104 present isbn/outlet_isbn values
    sit at lengths - 11,12,14,15,16,18 - that don't correspond to any real
    hyphenated-or-bare ISBN-10/13 form, and the old check passed every one
    of them). checksum_validator receives the value AFTER canonicalize +
    battery (so hyphens may or may not be present - it must strip them
    itself) and does the real arithmetic; both the pattern AND the
    checksum must pass.
    """

    def __init__(self, pattern, checksum_validator, canonicalize_steps=None, transform_battery=None):
        super().__init__(pattern, canonicalize_steps, transform_battery)
        self._checksum_validator = checksum_validator

    def is_wellformed(self, value):
        return super().is_wellformed(value) and self._checksum_validator(value)


def issn_checksum_valid(value):
    """ISSN: 7 digits + 1 check character (0-9 or X), mod-11. Weight
    digits 1-7 by 8..2, check = (11 - sum mod 11) mod 11, 'X' if 10.
    Verified directly against the ISSN.org-documented example 2049-3630."""
    digits = value.replace("-", "")
    if len(digits) != 8 or not digits[:7].isdigit() or digits[7].upper() not in "0123456789X":
        return False
    total = sum(int(d) * w for d, w in zip(digits[:7], range(8, 1, -1)))
    expected = (11 - total % 11) % 11
    expected_char = "X" if expected == 10 else str(expected)
    return digits[7].upper() == expected_char


def isbn10_checksum_valid(value):
    """ISBN-10: 9 digits + 1 check character (0-9 or X for 10), weighted
    10..2 on the first 9 digits, (sum + check_value) mod 11 == 0. Verified
    directly against the real published example 0-19-852663-6."""
    digits = value.replace("-", "")
    if len(digits) != 10 or not digits[:9].isdigit() or digits[9].upper() not in "0123456789X":
        return False
    total = sum(int(d) * w for d, w in zip(digits[:9], range(10, 1, -1)))
    check_value = 10 if digits[9].upper() == "X" else int(digits[9])
    return (total + check_value) % 11 == 0


def ean13_checksum_valid(value):
    """ISBN-13 and ISMN both use the standard EAN-13/GTIN check digit:
    13 digits, alternating weights 1,3 on the first 12, check =
    (10 - sum mod 10) mod 10. Covers ISMN too (its EAN-13 form is
    identical in mechanism, just prefixed 9790 instead of 978/979) -
    confirmed real ISMN-shaped values in ERA's own standard_number field
    (2026-09-04). Verified directly against the ISO-documented example
    978-3-16-148410-0."""
    digits = value.replace("-", "")
    if len(digits) != 13 or not digits.isdigit():
        return False
    total = sum(int(d) * (1 if i % 2 == 0 else 3) for i, d in enumerate(digits[:12]))
    expected = (10 - total % 10) % 10
    return int(digits[12]) == expected


def book_number_checksum_valid(value):
    """isbn/outlet_isbn: either edition standard is legitimate (ISBN-10
    pre-2007, ISBN-13/ISMN since) - confirmed real, not hypothetical:
    351/757 present isbn/outlet_isbn values are bare 10-digit, far too
    many to be typos."""
    return isbn10_checksum_valid(value) or ean13_checksum_valid(value)


def standard_number_checksum_valid(value):
    """standard_number: the ERA-SEER spec itself defines this field
    open-endedly ("reference number... e.g. ISMN"), and the real data
    confirms it holds a mix of ISBN-13, ISMN, ISSN, and bare ISBN-10
    shapes (2026-09-04) - so well-formed here means matching ANY
    recognized standard-number checksum, not committing to one scheme."""
    return book_number_checksum_valid(value) or issn_checksum_valid(value)


_DOI_PREFIX_RE = re.compile(r"^(https?://(dx\.)?doi\.org/|doi:\s*)", re.IGNORECASE)
_DOI_WELLFORMED_RE = re.compile(r"10\.\d{4,}(?:\.\d+)*/\S+")

# DOI transform battery, restricted to the two disparities actually
# observed in ERA's doi field:
#   S (Padded) - a "doi:" or "https://doi.org/"-style prefix wrapped
#     around an otherwise correct value.
#   K (Space) - leading/trailing whitespace.
# Case is handled by canonicalize_steps, not the battery: the DOI syntax
# standard (ISO 26324) defines DOIs as case-insensitive, so a case
# difference is not a bibliographic disparity to classify, just an
# equivalent representation.
# Well-formedness requires the bare DOI form only (a "10." prefix, a
# registrant code of 4+ digits with optional dot-subdivisions, a slash,
# then any non-whitespace suffix - the IDF permits almost any printable
# character there, e.g. SICI-based suffixes like
# "10.1002/(SICI)1097-0258(19980815)17:15<1661::AID-SIM872>3.0.CO;2-9").
# Deliberately does NOT accept a doi.org URL wrapper here even though one
# would be a harmless, common variant - that wrapper is exactly what the
# S transform strips, and it needs to still be present for is_wellformed
# to fail on the canonical value, so the battery actually runs and tags
# the S code rather than the wrapper passing through unclassified.
doi_classifier = FixedFormatTextDisparityClassifier(
    pattern=_DOI_WELLFORMED_RE,
    canonicalize_steps=[
        Transform(None, "lowercase", str.lower),
    ],
    transform_battery=[
        Transform("S", "strip_prefix", lambda v: _DOI_PREFIX_RE.sub("", v)),
        Transform("K", "strip_whitespace", lambda v: v.strip()),
    ],
)
# Residual not-well-formed population (127 records, confirmed 2026-09-04):
# mostly a literal space where the "/" between the registrant code and
# suffix should be (e.g. "10.3850/978-981-08-7920-4 aw-9-0443"), plus a
# handful of one-off corruptions (a dropped digit, a stray "?"). Reviewed
# directly with the user: NOT treated as an insertion to repair via the
# battery - guessing where a missing "/" belongs is exactly the kind of
# presumptive fix this project avoids, unlike S/K above which restore an
# unambiguous, fully-specified original. Left correctly classified as
# malformed with no code, not silently passed or force-fixed.


# Ported from arc_grants/src/utils/name_diacritic_variants.py
# (canonicalize_name_punctuation(), 2026-09-02) per direct instruction: the
# problems are different enough (person-name entity resolution vs.
# bibliographic-field disparity classification) that a shared/relocatable
# package isn't right, but the underlying text-hygiene mechanics are
# general-purpose - nothing in them is name-specific - so they're
# re-extracted here rather than reimplemented from scratch. What's
# genuinely new here, not present in the source: splitting NFKC into its
# space-category and other-compatibility-character effects so each can
# carry its own Olensky code, and tagging every step with a code (or None)
# at all - the source project normalizes silently, since its goal is
# reaching a correct match; this project counts and characterizes
# disparities, so nothing here is silent.
_ZERO_WIDTH_STRIP_RE = re.compile(
    "[­​‌‍﻿]"
)  # soft hyphen, zero-width space/non-joiner/joiner, BOM/zero-width no-break space
_QUOTE_VARIANTS_RE = re.compile(
    "[‘’ʼ`´ʹ′]"
)  # curly quotes, modifier-letter apostrophe, grave/acute accent, primes
_EXOTIC_HYPHENS_RE = re.compile(
    "[‐‑‒–—―−－]"
)  # hyphen, non-breaking hyphen, figure/en/em dash, horizontal bar, minus, fullwidth hyphen


def _nfkc_fold_space(s):
    """NFKC-fold only Unicode space-separator (category Zs) characters,
    leaving every other character untouched - isolates the specific effect
    confirmed empirically (2026-09-02) to fold NBSP/thin-space/four-per-em-
    space/narrow-no-break-space down to a plain ASCII space, i.e. exactly
    the non-ASCII-space-glyph cases already folded into K."""
    return "".join(
        unicodedata.normalize("NFKC", ch) if unicodedata.category(ch) == "Zs" else ch
        for ch in s
    )


def _nfkc_fold_other(s):
    """NFKC-fold everything except Zs space characters (already handled by
    _nfkc_fold_space) - ligatures (ﬁ -> fi), Roman numerals, sub/superscripts.
    Safe to run after an NFC pass has already resolved cross-character
    composition, since the compatibility characters this targets are single,
    already-precomposed code points, not combining sequences."""
    return "".join(
        ch if unicodedata.category(ch) == "Zs" else unicodedata.normalize("NFKC", ch)
        for ch in s
    )


class FreeTextDisparityClassifier(DisparityClassifier):
    r"""Title canonicalize_steps and transform_battery, built up
    incrementally and empirically, one Olensky type at a time, against the
    full raw title field (567,647 rows) before being added.

    canonicalize_steps (unconditional, always run, each logged whether or
    not it changes anything):
      nfc - Unicode canonical composition. No code: same character, whether
        precomposed (NFC) or base+combining-mark (NFD) - zero content
        difference, the direct analogue of byte-order/endianness one layer
        up. Pending: full byte/encoding-level discrepancy handling is being
        built in a related project and will extend this step later.
      zero_width_strip (S) - invisible contamination (soft hyphen,
        zero-width space/joiner/non-joiner, BOM) that OCR/scraping/multi-
        source assembly can leak into text with no visible trace. S fits
        Olensky's own definition ("extraneous added characters... of no
        additional value to the reader... origin not reproducible") well,
        even though it's not one of her named examples.
      nfkc_space_fold (K) - see _nfkc_fold_space(). Confirmed empirically:
        of the 53,592 titles (9.44%) that change under the full K
        treatment, 808 occurrences are exactly the four space-glyph
        variants this step folds (NBSP, thin space, four-per-em space,
        narrow no-break space); the rest (double ASCII space,
        leading/trailing space, embedded \n/\r/tab/NEL/LINE SEPARATOR) are
        NOT touched by NFKC and still need the collapse_whitespace battery
        item below.
      nfkc_other_fold (Q) - see _nfkc_fold_other(). Q's own definition
        names "Roman numerals" explicitly; ligatures are the same
        compatibility-character phenomenon.
      quote_substitution, hyphen_substitution (R) - curly quotes/exotic
        hyphens/dashes to their plain ASCII equivalents. "Differing
        punctuation" is Olensky's own definition of R.
      lowercase - no code. Olensky explicitly excludes capitalization from
        her assessable inaccuracies (see this classifier's git history /
        docs/era_quality_review.md for the primary-source citation).

    transform_battery (conditional - only attempted if canonicalization
    alone isn't already well-formed):
      collapse_whitespace (K) - collapse any run of whitespace to a single
        space, strip ends. Covers what nfkc_space_fold does not: genuine
        ASCII double-spacing/edge-whitespace and embedded control
        characters (\n, \r, tab, NEL, LINE SEPARATOR - 684 occurrences,
        none of them Zs-category so NFKC never touches them). Title's
        word-spacing convention makes the correct spacing unambiguous
        regardless of cause, unlike DOI's suffix.
      strip_trailing_period (R) - a title ending in "." Found via
        DOI-cluster inspection (2026-09-02): the same real title recorded
        with and without a trailing full stop by different HEPs, e.g.
        "...dermal tissue" vs "...dermal tissue." - titles don't
        conventionally end in a full stop, so a trailing one is treated as
        differing punctuation (R) applied unconditionally, not gated on
        cluster membership. One known residual risk, accepted rather than
        solved: a title genuinely ending in an abbreviation ("...Ph.D.")
        would have its real final period stripped too - not detected as
        distinguishable from the extraneous case without more context.

    Not yet built: A (Typographical variation / transliteration), Y (Word
    stem), H (Jumbled value), N (Additional information - e.g. a trailing
    footnote marker). Also not yet built here: the general single-
    character-indel detector discussed for cluster-pair comparison (K for
    a missing space, R for a missing/extra punctuation mark, B for a
    missing/extra letter or digit) - that's a cross-record comparison, not
    a single-value classification, so it lives in ERAProcessor's cluster
    pass rather than in this classifier (see single_char_indel_code()
    below).

    is_wellformed is idempotence under the battery (does cleaning change
    the value at all), not a grammar check - title has no formal grammar
    the way DOI does, so there's nothing external to validate against.
    """

    canonicalize_steps = [
        Transform(None, "nfc", lambda v: unicodedata.normalize("NFC", v)),
        Transform("S", "zero_width_strip", lambda v: _ZERO_WIDTH_STRIP_RE.sub("", v)),
        Transform("K", "nfkc_space_fold", _nfkc_fold_space),
        Transform("Q", "nfkc_other_fold", _nfkc_fold_other),
        Transform("R", "quote_substitution", lambda v: _QUOTE_VARIANTS_RE.sub("'", v)),
        Transform("R", "hyphen_substitution", lambda v: _EXOTIC_HYPHENS_RE.sub("-", v)),
        Transform(None, "lowercase", str.lower),
    ]

    transform_battery = [
        Transform("K", "collapse_whitespace", lambda v: re.sub(r"\s+", " ", v).strip()),
        Transform("R", "strip_trailing_period", lambda v: re.sub(r"\.$", "", v)),
    ]

    def is_wellformed(self, value):
        return value == self._apply_all(value)


def single_char_indel_code(a, b):
    """Cross-record comparison, not single-value classification - lives
    here as a shared utility rather than on DisparityClassifier since it
    takes two already-cleaned values, typically DOI-cluster siblings that
    still differ after stage B. If a and b differ by exactly one
    character inserted/deleted at a single position (everything else
    identical), returns the Olensky code for that character's type -
    K for whitespace, B for a letter/digit (fits her 2-character-edit
    threshold for spelling errors), R for anything else (punctuation).
    Returns None if the difference isn't a single-character indel (more
    than one edit, a substitution, or a multi-character gap).

    The safety argument is deliberately not "does removing the character
    break a real word" (tried and rejected - it works for a merged word
    like "ofstressed" but not for something like "russells", which reads
    as a plausible word on its own even missing its apostrophe). What
    actually justifies treating this as a resolvable disparity is that
    the rest of the two strings match character-for-character AND they
    belong to the same DOI cluster - near-total identity plus shared DOI
    is strong enough evidence on its own, independent of what the
    differing character happens to be.
    """
    sm = difflib.SequenceMatcher(None, a, b, autojunk=False)
    edits = [op for op in sm.get_opcodes() if op[0] != "equal"]
    if len(edits) != 1:
        return None
    tag, i1, i2, j1, j2 = edits[0]
    if tag == "insert":
        segment = b[j1:j2]
    elif tag == "delete":
        segment = a[i1:i2]
    else:
        return None
    if len(segment) != 1:
        return None
    ch = segment
    if ch.isspace():
        return "K"
    if ch.isalnum():
        return "B"
    return "R"
