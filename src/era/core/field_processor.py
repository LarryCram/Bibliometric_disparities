"""FieldProcessor factory: dispatches (field, output_type) to one of a small
number of shared processing modules, rather than growing one bespoke
classify_X() method per field on ERAProcessor.

Two module shapes cover everything (confirmed 2026-09-03, not assumed):
free strings with no external grammar (FreeTextDisparityClassifier - title,
outlet, publisher, ...; well-formedness is idempotence under its own
battery), and structured strings with an external grammar
(FixedFormatTextDisparityClassifier - doi, isbn/issn, the WOS/MEDLINE identifier,
plain numeric fields, reference_year; well-formedness is a regex match).
Each structured field gets its own pattern/canonicalize/battery *instance*
of the one generic class, not its own bespoke class - "own pattern, not own
module." A third, smaller shape (ControlledVocabFieldProcessor) exists for
media, which needs neither a grammar nor a battery, just a leaked-system-
code check; category_type gets its own CategoryTypeRuleProcessor (same
leaked-code check plus a real rule-conformance check against the ERA-SEER
vocabulary).

The factory key is (field, output_type) to allow for fields that aren't
uniform across every type under one shared name, with output_type=None as
a wildcard for fields confirmed uniform. extent used to be the one
exception (page count for some types, duration/dimension prose for
others) but is no longer registered at all - see NOT_CLASSIFIED below.

Every module returns the same DisparityResult/transform_log shape from
olensky.py, so Olensky coding is available uniformly wherever it applies,
not just for title/doi.
"""

import json
import re
import sys
from pathlib import Path
from typing import Protocol

import duckdb
import openpyxl

from era.core.olensky import (
    DisparityClassifier, DisparityResult, FixedFormatTextDisparityClassifier,
    ChecksumTextDisparityClassifier, book_number_checksum_valid, issn_checksum_valid,
    standard_number_checksum_valid, Transform, TransformStep, doi_classifier,
    FreeTextDisparityClassifier,
)


class FieldProcessor(Protocol):
    def process(
        self, value: str, field: str, output_type: str | None, raw_row: dict | None = None
    ) -> DisparityResult: ...


class DisparityClassifierAdapter:
    """Wraps an existing DisparityClassifier (built for a single, context-
    free value) so it fits the factory's (value, field, output_type)
    contract. Ignores field/output_type - correct for every classifier
    registered through it so far, since none of them vary by field or
    type. str()-converts the value defensively since some raw columns
    (reference_year, era_journal_id) are stored as int, not str."""

    def __init__(self, classifier: DisparityClassifier):
        self._classifier = classifier

    def process(self, value, field, output_type, raw_row=None):
        return self._classifier.classify_wellformedness(str(value))


_SYSTEM_URI_RE = re.compile(r"^/[a-z0-9/]+$")


class ControlledVocabFieldProcessor:
    """media: a small, semi-controlled value set with no documented
    vocabulary to check against (unlike category_type - see
    CategoryTypeRuleProcessor below). Flags (does not correct) a confirmed
    real problem: raw internal system taxonomy URIs leaking through (e.g.
    Pure CRIS's
    "/dk/atira/pure/researchoutput/researchoutputtypes/nontextual/performance").
    Case-inconsistency ("Video" vs "video", also confirmed real) isn't
    checkable from one value alone - it needs the field's other values to
    compare against, so it's a cluster/corpus-level check, not a per-value
    one, same distinction as title's HEP-to-HEP comparison work, not
    attempted here.
    """

    def process(self, value, field, output_type, raw_row=None):
        value = value.strip()
        is_leaked_uri = bool(_SYSTEM_URI_RE.match(value))
        log = [TransformStep(
            name="system_uri_check",
            effect="changed" if is_leaked_uri else "no_effect",
            iac_code="D" if is_leaked_uri else None,
        )]
        return DisparityResult(cleaned_value=value, wellformed=not is_leaked_uri, transform_log=log)


def _normalize_category(s):
    return re.sub(r"[^a-z0-9]", "", s.lower())


from era.config import DATA_PERSISTED_DIR
_ERA_SEER_CATEGORY_VOCAB_JSON = DATA_PERSISTED_DIR / "era_seer_category_vocab.json"


def _load_category_vocab(path=_ERA_SEER_CATEGORY_VOCAB_JSON):
    """research_output_type -> set of normalized valid category_type
    labels, from the ERA-SEER 2018 spec's own controlled vocabulary
    (sections 3.3.8.1-3.3.8.5, transcribed - not a data asset the
    pipeline previously loaded, this is what wires it in). Normalizing to
    lowercase-alphanumeric-only handles case (no Olensky code) and
    punctuation variants ERA's own submission system uses interchangeably
    with the spec's exact prose (e.g. "Film/Video" vs the spec's
    "Film, video" - identical once "/" and "," are both stripped).

    Two labels are added beyond the spec text itself, confirmed by direct
    inspection (2026-09-04) against every (research_output_type,
    category_type) pair actually in use in the full dataset - no
    cross-type leakage, and every value denotes exactly one valid spec
    entry, just with ERA's own wording rather than the spec's: Curated
    Exhibition Event's "Web-based exhibition" (spec says "...exhibition
    work"), Recorded Rendered Work's "Websites/web exhibition" (spec says
    singular "Website/...").
    """
    raw = json.loads(path.read_text())
    vocab = {
        output_type: {_normalize_category(label) for label in codes.values()}
        for output_type, codes in raw.items()
        if output_type != "_source"
    }
    vocab["Curated Exhibition Event"].add(_normalize_category("Web-based exhibition"))
    vocab["Recorded Rendered Work"].add(_normalize_category("Websites/web exhibition"))
    return vocab


CATEGORY_VOCAB = _load_category_vocab()


class CategoryTypeRuleProcessor:
    """category_type: checked against the ERA-SEER spec's own controlled
    vocabulary (data_persisted/era_seer_category_vocab.json) for the
    record's own research_output_type - a documented ERA rule, the same
    status as journal_title's check against the ERA 2018 Journal List.
    Also keeps the leaked-system-URI check (D) ControlledVocabFieldProcessor
    uses for media, since that failure mode is independent of vocabulary
    conformance and can't be a false positive if the vocab check ran
    first (a leaked URI never normalizes to a valid vocab entry anyway).

    When research_output_type isn't one of the five non-traditional types
    the spec's category vocabulary covers (Journal Article, Book, etc.
    have no category_type at all), there is no rule to check - wellformed
    is None (not checked), never coerced to True or False.

    Checked directly against every (research_output_type, category_type)
    pair actually in use (2026-09-04): no cross-type leakage, and no
    genuine misclassification found once wording variants are normalized
    (see _load_category_vocab) - this currently confirms conformance
    rather than finding a new disparity.
    """

    def __init__(self, vocab: dict):
        self._vocab = vocab

    def process(self, value, field, output_type, raw_row=None):
        value = value.strip()
        is_leaked_uri = bool(_SYSTEM_URI_RE.match(value))
        if is_leaked_uri:
            log = [TransformStep(name="system_uri_check", effect="changed", iac_code="D")]
            return DisparityResult(cleaned_value=value, wellformed=False, transform_log=log)

        valid = self._vocab.get(output_type)
        if valid is None:
            return DisparityResult(cleaned_value=value, wellformed=None, transform_log=[])

        matches = _normalize_category(value) in valid
        log = []
        if not matches:
            log.append(TransformStep(name="category_vocab_mismatch", effect="changed", iac_code=None))
        return DisparityResult(cleaned_value=value, wellformed=matches, transform_log=log)


class NumericDescriptorProcessor:
    """volume, issue, journal_volume, journal_issue, outlet_volume,
    outlet_issue: NOT a DisparityClassifier, deliberately - checked
    directly against the ERA-SEER spec (2026-09-03), its only text for
    these fields is "the volume number of the [journal/non-traditional
    research output]", no format constraint at all, and the data itself
    falsifies a digits-only assumption (slash notation, decimals, report
    codes among real values). So `wellformed` is repurposed here as a
    purely DESCRIPTIVE is_numeric fact ("not_numeric" rather than
    "not_wf" - there is no standard being violated, just an observation),
    not a normative correctness judgment - `is_wf`/`not_wf` language
    should never be used for this processor's output.

    The one exception that IS a genuine, defensible judgment, independent
    of numeric-format questions entirely: a value that's clearly prose -
    a full conference session/track title ("Genetic Improvement Programs:
    Selection using molecular information (Posters)"), a date, a chapter
    reference - stuffed into a field meant to hold a short identifier.
    That's a content-type mismatch regardless of what counts as a
    legitimate alternate numeric format, confirmed real for outlet_issue
    (43.3% of its values) and present at much lower volume in `volume`
    itself. Tagged D (Completely incorrect) only for that confirmed shape
    (long, or contains a colon) - never for merely non-numeric values.
    """

    def process(self, value, field, output_type, raw_row=None):
        value = value.strip()
        is_numeric = bool(re.fullmatch(r"\d+", value))
        codes = []
        if not is_numeric and self._looks_like_prose(value):
            codes.append("D")
        log = [TransformStep(name=c, effect="changed", iac_code=c) for c in codes]
        return DisparityResult(cleaned_value=value, wellformed=is_numeric, transform_log=log)

    @staticmethod
    def _looks_like_prose(value):
        return len(value) > 15 or ":" in value


_ERA_JOURNALS_XLSX = DATA_PERSISTED_DIR / "ERA 2018 Journal List.xlsx"


def _load_era_journals(path=_ERA_JOURNALS_XLSX):
    """The ARC's own ERA 2018 Journal List (25,017 journals, released
    August 2017 to support ERA 2018 submissions) - era_journal_id ->
    canonical title/foreign title/FoR codes/ISSN(s). Same standing as the
    ERA-SEER spec text: a documented rule the ERA dataset itself is
    supposed to follow, keyed by ERA's own era_journal_id rather than
    transcribed prose.

    NOT the era_journals.xlsx file also sitting in data_persisted/ - that
    one is a 2023-vintage list (has an OpenAlex source_id column this file
    lacks), the wrong round for this dataset. Confirmed 2026-09-04: this
    file resolves every era_journal_id actually used in the ERA data (0
    unmatched), the 2023 file left 224 distinct ids/2,393 records
    unmatched, including real, unambiguous journals like "Journal of
    Materials Chemistry" (era_journal_id 1459) that the 2023 list had
    apparently renumbered or dropped. No OpenAlex linkage is available
    from this file - that idea is deferred until a separate ISSN-based
    join against OpenAlex is built, if still wanted.

    Loaded once at import time (read_only) rather than pre-converted to
    JSON, so the xlsx stays the single source of truth with nothing to
    fall out of sync.
    """
    wb = openpyxl.load_workbook(path, read_only=True)
    rows = wb["ERA 2018 Journal List"].iter_rows(values_only=True)
    next(rows)  # header: ERA Journal Id, Title, Foreign Title, FoR 1, FoR 1 Name,
    # FoR 2, FoR 2 Name, FoR 3, FoR 3 Name, ISSN 1..7
    lookup = {}
    for row in rows:
        era_journal_id = row[0]
        if era_journal_id is None:
            continue
        title, foreign_title = row[1], row[2]
        for_codes = [c for c in (row[3], row[5], row[7]) if c]
        issns = [i for i in row[9:16] if i]
        lookup[str(era_journal_id)] = {
            "title": title,
            "foreign_title": foreign_title or None,
            "for_codes": for_codes,
            "issns": issns,
        }
    return lookup


ERA_JOURNALS = _load_era_journals()


class JournalTitleRuleProcessor:
    """journal_title: checked against the ERA 2018 Journal List's canonical
    title for the record's own era_journal_id - a documented ERA rule the
    dataset is supposed to follow, the same status as the ERA-SEER spec
    check for category_type, NOT an internal-consistency or cross-source
    comparison (contrast with e.g. DOI-derived title vs ERA title, where
    neither side is a rule and disagreement is a disparity to describe,
    not a violation to flag).

    Canonicalization (lowercase, whitespace-collapse) absorbs case and
    whitespace differences before comparing: Olensky (2015) has no code
    for capitalization at all (docs/era_quality_review.md - a limitation
    of her detection tool, not a stated position that case is
    insignificant), and whitespace is K everywhere else in this codebase -
    so neither counts as a genuine content disagreement here.

    When era_journal_id is missing, blank, or not found in the lookup,
    there is no rule to check against - wellformed is None (not checked),
    never coerced to True or False. Confirmed 2026-09-04 against the
    correct-vintage ERA 2018 Journal List: every era_journal_id actually
    used in the ERA data resolves (0 unmatched) - the earlier 2023-vintage
    era_journals.xlsx had wrongly left 224 distinct ids/2,393 records in
    this bucket.

    A remaining mismatch is flagged wellformed=False but deliberately left
    uncoded (no specific IAC) for now - spelling variation, abbreviation,
    subtitle drop, and a genuinely wrong journal entirely all look
    identical from here, and sub-coding them requires examining the
    mismatch population directly, not yet done.
    """

    def __init__(self, lookup: dict):
        self._lookup = lookup

    def process(self, value, field, output_type, raw_row=None):
        value = value.strip()
        era_journal_id = raw_row.get("era_journal_id") if raw_row else None
        entry = self._lookup.get(str(era_journal_id)) if era_journal_id is not None else None
        if entry is None or not entry["title"]:
            return DisparityResult(cleaned_value=value, wellformed=None, transform_log=[])

        matches = self._norm(value) == self._norm(entry["title"])
        log = []
        if not matches:
            log.append(TransformStep(name="journal_title_rule_mismatch", effect="changed", iac_code=None))
        return DisparityResult(cleaned_value=value, wellformed=matches, transform_log=log)

    @staticmethod
    def _norm(s):
        return re.sub(r"\s+", " ", s).strip().lower()


from era.config import DATA_DIR
_ERA_RAW_PARQUET = DATA_DIR / "era_research_outputs_raw.parquet"


def _load_conference_vocab(classifier, path=_ERA_RAW_PARQUET):
    """era_conference_id -> consensus conference_name, derived from the
    data itself rather than an external published list - the ARC never
    published an ERA conference list the way it did for journals; it was
    applied only internally at ARC's own submission-validation time
    (confirmed by the user, 2026-09-04). Checked directly whether the
    corpus supports building one anyway: of 3,780 distinct
    era_conference_id values, 3,779 are UNANIMOUS once cleaned - every
    record sharing that id already uses the exact same conference_name,
    dict-of-sets checked directly rather than assumed. The lone
    exception, "00000" (13,924 records, 9,264 distinct names), is a
    sentinel/catch-all id used when a conference isn't individually
    registered, not a real shared conference at all - excluded here the
    same way NULL_SENTINELS excludes disguised-missing values elsewhere
    in this project.

    Tallies votes on classifier.classify_wellformedness(name).cleaned_value,
    not the raw string - confirmed necessary, not just tidy, 2026-09-04:
    building the vocab from raw values first produced 3 spurious mismatches,
    all era_conference_id 60360, whose raw value
    "Intellectbase International Consortium." (trailing period) was stored
    as the reference, while the field being compared against it had
    already had that same period stripped by this classifier's own
    battery - comparing a cleaned value against an uncleaned reference,
    not a real disparity.

    Reads era_research_outputs_raw.parquet directly (not a
    data_persisted/ asset like ERA_JOURNALS/CATEGORY_VOCAB, since this
    one is genuinely derived from the pipeline's own raw data, not an
    external reference) - returns {} if it doesn't exist yet rather than
    failing import, since build_raw() must already have run before this
    module is useful anyway (same implicit ordering build_processed()
    already requires).
    """
    if not path.exists():
        return {}
    con = duckdb.connect()
    rows = con.execute(f"""
        SELECT era_conference_id, conference_name
        FROM read_parquet('{path}')
        WHERE era_conference_id IS NOT NULL AND era_conference_id != '00000'
              AND conference_name IS NOT NULL AND trim(conference_name) != ''
    """).fetchall()
    counts = {}
    for cid, name in rows:
        cleaned = classifier.classify_wellformedness(name).cleaned_value
        tally = counts.setdefault(cid, {})
        tally[cleaned] = tally.get(cleaned, 0) + 1
    return {cid: max(tally.items(), key=lambda kv: kv[1])[0] for cid, tally in counts.items()}


_conference_name_classifier = FreeTextDisparityClassifier()
CONFERENCE_VOCAB = _load_conference_vocab(_conference_name_classifier)


class ConferenceNameRuleProcessor:
    """conference_name: two independent checks layered together, not one
    replacing the other. (1) The same free-text self-cleaning
    FreeTextDisparityClassifier already does for every other prose field
    (NFC, quote/hyphen substitution, whitespace, trailing period) -
    confirmed real, non-trivial disparities here (K=1,075, R=92, Q=3,
    S=1), unlike journal_title, so this field can't simply swap that
    battery out for a bare rule-check the way journal_title did. (2) A
    corpus-derived consensus check against the record's own
    era_conference_id (see _load_conference_vocab) - NOT an external
    authoritative source, just the data's own near-unanimous agreement,
    layered on top of whatever the free-text pass already cleaned.

    wellformed is the AND of both checks when the consensus check
    applies (era_conference_id present, resolvable, and not the "00000"
    sentinel); when it doesn't apply, only the free-text verdict counts -
    same "wellformed=None means not checked" discipline used elsewhere
    would be wrong here, since the free-text check DID run and its
    verdict is real regardless of whether the consensus check could.
    """

    def __init__(self, classifier, vocab):
        self._classifier = classifier
        self._vocab = vocab

    def process(self, value, field, output_type, raw_row=None):
        result = self._classifier.classify_wellformedness(value)
        era_conference_id = raw_row.get("era_conference_id") if raw_row else None
        canonical = self._vocab.get(era_conference_id) if era_conference_id else None
        if canonical is None:
            return result

        matches = self._norm(result.cleaned_value) == self._norm(canonical)
        log = list(result.transform_log)
        if not matches:
            log.append(TransformStep(name="conference_name_consensus_mismatch", effect="changed", iac_code=None))
        return DisparityResult(
            cleaned_value=result.cleaned_value,
            wellformed=result.wellformed and matches,
            transform_log=log,
        )

    @staticmethod
    def _norm(s):
        return re.sub(r"\s+", " ", s).strip().lower()


# --- Pattern-classifier instances: one generic class, many configurations ---

_isbn_classifier = ChecksumTextDisparityClassifier(
    pattern=re.compile(r"[0-9X\-]+"),
    checksum_validator=book_number_checksum_valid,
    canonicalize_steps=[
        Transform("K", "strip_whitespace", lambda v: v.strip()),
        Transform(None, "uppercase", str.upper),
    ],
    transform_battery=[
        Transform("R", "strip_trailing_period", lambda v: re.sub(r"\.$", "", v)),
    ],
)
# isbn, outlet_isbn - accepts ISBN-10 (pre-2007, confirmed real: 351/757
# present bare-10-digit values, far too many to be typos) or ISBN-13/ISMN,
# bare or hyphenated. Hyphen presence/position is NOT coded either way
# here (unlike issn below) - correct ISBN-13 group boundaries depend on
# registrant range-allocation tables this project has no access to, so
# there's no way to verify "correct" placement, only whether the checksum
# validates once hyphens are stripped. Replaced the old character-class-
# only check (2026-09-04, at the user's request to implement the real
# standards): confirmed it silently passed 104 present values sitting at
# lengths (11,12,14,15,16,18) that correspond to no real ISBN-10/13 form
# at all.

_issn_classifier = ChecksumTextDisparityClassifier(
    pattern=re.compile(r"\d{4}-\d{3}[0-9X]"),
    checksum_validator=issn_checksum_valid,
    canonicalize_steps=[
        Transform("K", "strip_whitespace", lambda v: v.strip()),
        Transform(None, "uppercase", str.upper),
    ],
    transform_battery=[
        Transform("R", "strip_trailing_period", lambda v: re.sub(r"\.$", "", v)),
        Transform(
            "R", "insert_issn_hyphen",
            lambda v: f"{v[:4]}-{v[4:]}" if re.fullmatch(r"\d{7}[0-9X]", v) else v,
        ),
    ],
)
# issn - unlike isbn, the correct hyphen position is fixed and universal
# (always after digit 4, no registry lookup needed), so the bare 8-char
# form (confirmed real and a genuine minority, not a rare fluke: 1,284 of
# 6,425 present values) is coded R when found - the same status as any
# other punctuation difference in this codebase. No specific citation
# found in Olensky (2015) addressing ISSN's hyphen convention directly;
# R is her general "differing punctuation" code, and absent a documented
# exception the way capitalization has one (a specific, citable tool
# limitation - see FreeTextDisparityClassifier), this project defaults to
# coding the difference rather than assuming an exemption without
# evidence.

_standard_number_classifier = ChecksumTextDisparityClassifier(
    pattern=re.compile(r"[0-9X\-]+"),
    checksum_validator=standard_number_checksum_valid,
    canonicalize_steps=[
        Transform("K", "strip_whitespace", lambda v: v.strip()),
        Transform(None, "uppercase", str.upper),
    ],
    transform_battery=[
        Transform("R", "strip_trailing_period", lambda v: re.sub(r"\.$", "", v)),
    ],
)
# standard_number - the ERA-SEER spec defines this field open-endedly
# ("reference number for the non-traditional research output, e.g. the
# ISMN"), and the real data confirms a genuine mix of ISBN-13-shaped,
# ISMN-shaped (979-0 prefix), ISSN-shaped, and bare-ISBN-10-shaped values
# (2026-09-04) - so wellformedness here means matching ANY recognized
# standard-number checksum, not committing to one scheme the way isbn/issn
# do individually.

_wos_identifier_classifier = FixedFormatTextDisparityClassifier(
    pattern=re.compile(r"(WOS|MEDLINE|BCI|ZOOREC):\d+", re.IGNORECASE),
    canonicalize_steps=[
        Transform("K", "strip_whitespace", lambda v: v.strip()),
    ],
    transform_battery=[
        Transform("K", "collapse_colon_whitespace", lambda v: re.sub(r"\s*:\s*", ":", v)),
        Transform(
            "S", "strip_padded_accession_prefix",
            lambda v: re.sub(r"^([A-Za-z]+):[A-Za-z]*(?=\d)", r"\1:", v),
        ),
    ],
)
# identifier - PREFIX:digits, four confirmed-legitimate source databases
# (2026-09-03/04): WOS (98.7%), MEDLINE (1.3%), and two more found in the
# full-review pass, BCI (Biosis Citation Index) and ZOOREC (Zoological
# Record) - accounting for every present value (395,815, confirmed no
# residual prefix outside these four). Unlike WOS/MEDLINE, every single
# BCI/ZOOREC accession redundantly repeats its own prefix right after the
# colon - "BCI:BCI201200059473", "ZOOREC:ZOOR15103016136" (abbreviated to
# 4 letters, not an exact repeat) - a real disparity distinct from the
# choice of source database itself: Olensky's S (Padded), the same code
# already used for DOI's "doi:"-wrapper case - a correct value (the
# digits) plus extraneous added characters. strip_padded_accession_prefix
# strips any leading letters in the accession portion generically (not
# hardcoded to "BCI"/"ZOOR" specifically) so it's a no-op, and untagged,
# for WOS/MEDLINE's already-clean digit-only accessions.

_numeric_classifier = FixedFormatTextDisparityClassifier(
    pattern=re.compile(r"\d+"),
    canonicalize_steps=[
        Transform("K", "strip_whitespace", lambda v: v.strip()),
    ],
    transform_battery=[
        Transform("R", "strip_trailing_period", lambda v: re.sub(r"\.$", "", v)),
    ],
)
# Pure-numeric-string fields: page-count-shaped extent (for the types where
# extent really is a page count), volume, issue, journal_volume,
# journal_issue, outlet_volume, outlet_issue, era_conference_id,
# era_journal_id. A non-numeric value (a supplement marker like "S2", a
# range like "2-3", a session title stuffed into outlet_issue) is correctly
# left unresolved rather than forced - these are legitimate alternate
# formats or genuine misuse, not something a battery should guess how to
# fix.

_year_classifier = FixedFormatTextDisparityClassifier(
    pattern=re.compile(r"(18|19|20)\d{2}"),
    canonicalize_steps=[
        Transform("K", "strip_whitespace", lambda v: v.strip()),
    ],
    transform_battery=[
        Transform("R", "strip_trailing_period", lambda v: re.sub(r"\.$", "", v)),
    ],
)
# reference_year is the same shape as volume/issue (own pattern, not own
# module) - just a stricter 4-digit-year pattern instead of "any digits".

# extent is NOT registered at all (2026-09-04, reversing the 2026-09-03
# page-count/N-suffix work) - reviewed directly with the user against the
# full uncoded residual (202,590 records, 45% of all present extent
# values) and found FOUR more shapes beyond what the N-suffix battery
# covered: "8pp"/"9 pp" (same N pattern, different abbreviation), page
# RANGES ("1-10", "pages 1 - 10"), counts in a different unit entirely
# ("1 Paper", "1 Chapter"), and "pages  -" (724 occurrences, a template
# artifact with the numbers missing - arguably a NULL_SENTINEL, not a
# number to fix). Chasing every one of these is disproportionate to what
# extent is: like volume/issue, ERA documents no format standard for it,
# and unlike volume/issue it mixes multiple genuinely different units
# across types (pages vs duration vs dimensions vs paper/chapter counts).
# Decided to treat it exactly like era_round/institution instead - free
# text, presence tracked nowhere, no wellformedness claim attempted at
# all - rather than keep extending a battery that will always be one
# pattern behind the data.

# --- Adapters ---

_string_module = DisparityClassifierAdapter(FreeTextDisparityClassifier())
_doi_module = DisparityClassifierAdapter(doi_classifier)
_isbn_module = DisparityClassifierAdapter(_isbn_classifier)
_issn_module = DisparityClassifierAdapter(_issn_classifier)
_standard_number_module = DisparityClassifierAdapter(_standard_number_classifier)
_wos_identifier_module = DisparityClassifierAdapter(_wos_identifier_classifier)
_numeric_module = DisparityClassifierAdapter(_numeric_classifier)
_year_module = DisparityClassifierAdapter(_year_classifier)
_vocab_module = ControlledVocabFieldProcessor()
_numeric_descriptor_module = NumericDescriptorProcessor()
_journal_title_module = JournalTitleRuleProcessor(ERA_JOURNALS)
_conference_name_module = ConferenceNameRuleProcessor(_conference_name_classifier, CONFERENCE_VOCAB)
_category_type_module = CategoryTypeRuleProcessor(CATEGORY_VOCAB)

# --- Factory construction: small number of modules, many keys built from
# lists rather than one dict-literal line per field. ---

STRING_FIELDS = [
    "title", "outlet",
    "outlet_venue", "publisher", "outlet_editor", "notes",
    "place_of_publication",
]
# journal_title is NOT here - it has an actual documented rule to check
# against (the ERA 2018 Journal List, keyed by era_journal_id), so it
# gets JournalTitleRuleProcessor below instead of the no-external-grammar
# free-text module.
# conference_name is NOT here either (2026-09-04) - it gets
# ConferenceNameRuleProcessor, which layers a corpus-derived consensus
# check on TOP of this same free-text module rather than replacing it
# (unlike journal_title, conference_name has real free-text noise worth
# keeping the cleaning for).
# edition/outlet_edition are NOT registered anywhere (2026-09-04, found
# during the full-review pass): a real, uncoded disparity population
# exists (the same ordinal given as "1"/"1st"/"First"/"2nd ed."/"Second
# Edition" - 2,003 and 13,194 present respectively), but reviewed
# directly with the user and left deliberately unflagged, same treatment
# as era_round/institution - free text, no wellformedness claim, nothing
# persisted beyond the raw value.
BOOK_NUMBER_FIELDS = ["isbn", "outlet_isbn"]
# issn and standard_number are NOT here - each needs its own checksum
# validator (issn_checksum_valid / standard_number_checksum_valid), not
# the ISBN-or-ISBN13 one book_number_checksum_valid uses, so they're
# registered individually below rather than sharing one module.
NUMERIC_FIELDS = ["era_conference_id", "era_journal_id"]
# volume/issue/journal_volume/journal_issue/outlet_volume/outlet_issue are
# NOT here - checked directly against the ERA-SEER spec (2026-09-03), its
# only text for these is "the volume number of the [journal/non-
# traditional research output]", no format constraint at all, and a
# \d+-only check was falsified directly by the data itself (slash
# notation, decimals, report codes among real values). They route through
# NUMERIC_DESCRIPTOR_FIELDS instead - see NumericDescriptorProcessor for
# why that's a purely descriptive is_numeric fact, not a wellformedness
# judgment, plus the one still-defensible finding (prose/session-title
# content) that holds regardless of numeric-format questions.
NUMERIC_DESCRIPTOR_FIELDS = [
    "volume", "issue", "journal_volume", "journal_issue",
    "outlet_volume", "outlet_issue",
]
VOCAB_FIELDS = ["media"]
# category_type is NOT here - it has an actual documented rule to check
# against (the ERA-SEER spec's own controlled vocabulary), so it gets
# CategoryTypeRuleProcessor instead of the no-vocabulary-reference
# leaked-URI-only module.

FIELD_PROCESSORS: dict[tuple[str, str | None], FieldProcessor] = {}
FIELD_PROCESSORS.update({(f, None): _string_module for f in STRING_FIELDS})
FIELD_PROCESSORS.update({(f, None): _isbn_module for f in BOOK_NUMBER_FIELDS})
FIELD_PROCESSORS.update({(f, None): _numeric_module for f in NUMERIC_FIELDS})
FIELD_PROCESSORS.update({(f, None): _numeric_descriptor_module for f in NUMERIC_DESCRIPTOR_FIELDS})
FIELD_PROCESSORS.update({(f, None): _vocab_module for f in VOCAB_FIELDS})
FIELD_PROCESSORS[("doi", None)] = _doi_module
FIELD_PROCESSORS[("issn", None)] = _issn_module
FIELD_PROCESSORS[("standard_number", None)] = _standard_number_module
FIELD_PROCESSORS[("journal_title", None)] = _journal_title_module
FIELD_PROCESSORS[("conference_name", None)] = _conference_name_module
FIELD_PROCESSORS[("category_type", None)] = _category_type_module
FIELD_PROCESSORS[("identifier", None)] = _wos_identifier_module
FIELD_PROCESSORS[("reference_year", None)] = _year_module


def get_processor(field: str, output_type: str | None) -> FieldProcessor:
    """Type-specific entry first, then the (field, None) wildcard. Raises
    KeyError if truly nothing is registered - a missing mapping should be
    visible, not silently skipped."""
    return FIELD_PROCESSORS.get((field, output_type)) or FIELD_PROCESSORS[(field, None)]
