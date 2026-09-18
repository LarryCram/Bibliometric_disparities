"""StringProcessor: pre-Olensky structural feature detection, field-agnostic.

Consolidates what used to be five separate module-level functions/fields in
era_processor.py (has_html, has_tex, has_non_latin, has_daterange, and now
spelling-dialect) into one class with one consistent contract. These are
deliberately NOT Olensky disparity classification: they're facts about a
raw string's structure, computed independent of any comparison target, kept
as flags rather than cleaned - see era_processor.py's docstrings for why
html/tex markup isn't stripped yet (holding for evidence of a real match
failure) and why spelling dialect isn't normalized at all (dialect may
carry real information - which country's convention a record follows -
unlike case, which carries none; collapsing to one form would destroy that
signal, not just represent it differently).
"""

import re
import unicodedata
from dataclasses import dataclass, field
from pathlib import Path

import enchant
import ftfy

from era.config import DATA_PERSISTED_DIR
_DIALECT_SUPPLEMENT_DIR = DATA_PERSISTED_DIR / "dialect_supplements"

_HTML_TAG_RE = re.compile(r"</?[a-zA-Z][a-zA-Z0-9]*\s*/?>")
_TEX_COMMAND_RE = re.compile(r"\\[a-zA-Z]+")

_DATERANGE_RE = re.compile(
    r"(1[5-9]\d{2}|20[0-4]\d)[\s]{0,2}[-‐‑‒–—―−_]{0,2}"
    r"[\s]{0,2}(1[5-9]\d{2}|20[0-4]\d)"
)

# Semantic blocklist: pairs that look like British/American spelling variants
# but are actually different words or different senses - never treated as
# the same word regardless of context. Not exhaustive; extend as more are
# found rather than trying to enumerate every English polyseme up front.
_SEMANTIC_BLOCKLIST = frozenset({
    "program", "programme", "check", "cheque", "tire", "tyre",
    "disk", "disc", "curb", "kerb",
})

# Named-entity phrase exceptions: checked as phrases, not bare words - a
# different kind of exception than the blocklist above. "labor" alone is a
# completely ordinary American spelling; "Australian Labor Party" is a
# specific proper noun that keeps that spelling regardless of dialect.
_PROTECTED_PHRASES = (
    "australian labor party",
    "labor party",
)

# Real Hunspell dictionaries (via the system's libhunspell + hunspell-en-us/
# en-gb/en-au packages, wrapped by pyenchant) rather than VarCon's 370-line
# curated-irregulars file: the earlier VarCon-only approach could match
# "colour"/"color" but not inflected forms like "analysed"/"analyzed",
# since VarCon's file lists irregular pairs, not a full affix-aware
# dictionary. Hunspell applies its own affix rules, so both base and
# inflected forms are handled correctly with no separate suffix-regex tier
# needed - confirmed directly (2026-09-03): analyzed/analysed,
# organized/organised, categorized/categorised all resolve correctly.
# en_AU included alongside en_US/en_GB since this is an Australian dataset -
# confirmed AU mostly but not exactly tracks GB (e.g. "programme" is
# GB-only in this dictionary release, AU=False), so it's a genuine third
# axis, not a redundant copy of GB.
#
# Each dictionary is supplemented with a project-maintained personal word
# list (data_persisted/dialect_supplements/) for exactly the gap confirmed
# 2026-09-03: general-purpose dictionaries don't include specialised
# academic/medical compound vocabulary common in paper titles
# ("dichotomization"/"dichotomisation", "multicenter"/"multicentre" were
# both entirely unrecognised by the base dictionaries on either side).
# Grow these files as more such pairs turn up in real ERA titles rather
# than trying to anticipate them.
def _dict_with_supplement(tag):
    pwl_path = _DIALECT_SUPPLEMENT_DIR / f"{tag}.txt"
    return enchant.DictWithPWL(tag, str(pwl_path)) if pwl_path.exists() else enchant.Dict(tag)


_DICT_US = _dict_with_supplement("en_US")
_DICT_GB = _dict_with_supplement("en_GB")
_DICT_AU = _dict_with_supplement("en_AU")
_DIALECT_DICTS = {"has_en_US": _DICT_US, "has_en_GB": _DICT_GB, "has_en_AU": _DICT_AU}

_WORD_RE = re.compile(r"[a-z]+")


def _has_mojibake(s):
    """Detection only, no correction - ftfy.fix_encoding() is used purely
    as the test (does it think there's encoding-level corruption here),
    not applied to produce a cleaned value anywhere. Deliberately the
    narrow fix_encoding(), not the broad fix_text(): the latter also
    normalises curly quotes and other things this project already handles
    itself (R), which would conflate two different phenomena in one flag.
    Confirmed 2026-09-03 against the full raw table: 1,027 titles
    (0.18%) flagged, with a long tail of ~306 distinct corruption
    signatures - a few common ones (Ã¢, the â\\x80\\x99-family apostrophe
    corruption, â\\x88\\x9e for infinity) but no small fixed set covers
    most of the variety, which is why this stays a flag and not a fix for
    now."""
    return ftfy.fix_encoding(s) != s


def _daterange_status(s):
    """See era_processor.py's prior _daterange_status docstring (unchanged
    logic, moved here): detects a year-range substring and checks it
    against its self-evident canonical form (dddd-dddd) directly, with no
    comparison to another value needed."""
    m = _DATERANGE_RE.search(s)
    if not m:
        return False, None
    canonical = f"{m.group(1)}-{m.group(2)}"
    return True, ("valid" if m.group(0) == canonical else "adjusted")


def _has_non_latin_script(s):
    """See era_processor.py's prior docstring (unchanged logic, moved
    here): True if s contains an alphabetic character outside the Latin
    script, distinct from a Latin letter carrying a diacritic."""
    return any(ch.isalpha() and not unicodedata.name(ch, "").startswith("LATIN") for ch in s)


def _spelling_dialect_flags(s):
    """Non-exclusive (has_en_US, has_en_GB, has_en_AU) flags: for each
    dictionary, does the raw string contain a word that dictionary
    recognises but that is NOT recognised by BOTH of the other two - i.e.
    not a word shared universally across all three. More than one flag
    can be True for the same word (e.g. "colour" is absent from en_US, so
    both has_en_GB and has_en_AU fire for it - confirmed directly,
    2026-09-03, that en_AU mostly but not exactly tracks en_GB, so this
    is a genuine three-way signal, not two copies of one comparison).
    A word valid in all three regardless of sense (e.g. "check", "tire",
    "program" are all recognised everywhere) is correctly never flagged -
    confirmed directly that this alone keeps the blocklist's originally
    feared false-positive risk (program/programme, check/cheque,
    tire/tyre) from actually firing. The blocklist and phrase-exceptions
    are kept anyway as defense-in-depth for whatever other cases the three
    dictionaries' coverage doesn't naturally resolve.
    """
    lowered = s.lower()
    protected_spans = []
    for phrase in _PROTECTED_PHRASES:
        start = 0
        while True:
            idx = lowered.find(phrase, start)
            if idx == -1:
                break
            protected_spans.append((idx, idx + len(phrase)))
            start = idx + 1

    flags = {name: False for name in _DIALECT_DICTS}
    for m in _WORD_RE.finditer(lowered):
        word = m.group(0)
        if len(word) < 3 or word in _SEMANTIC_BLOCKLIST:
            continue
        if any(a <= m.start() < b for a, b in protected_spans):
            continue
        recognised = {name: d.check(word) for name, d in _DIALECT_DICTS.items()}
        for name, is_recognised in recognised.items():
            if not is_recognised:
                continue
            others_all_recognise = all(v for k, v in recognised.items() if k != name)
            if not others_all_recognise:
                flags[name] = True
    return flags


@dataclass
class StringProcessorResult:
    original: str
    modified: str | None = None
    codes: tuple[str, ...] = field(default_factory=tuple)


class StringProcessor:
    """Runs every pre-Olensky structural detector over one string and
    returns a single consolidated result, rather than each detector living
    as its own free function with its own field on the caller's record.
    Field-agnostic - nothing here is title-specific, so the same instance
    is reusable for outlet/place_of_publication later without change.
    """

    def process(self, s: str) -> StringProcessorResult:
        codes = []
        modified = None

        if _HTML_TAG_RE.search(s):
            codes.append("has_html")
        if _TEX_COMMAND_RE.search(s):
            codes.append("has_tex")
        if _has_non_latin_script(s):
            codes.append("has_non_latin")
        if _has_mojibake(s):
            codes.append("has_mojibake")

        has_daterange, daterange_status = _daterange_status(s)
        if has_daterange:
            codes.append("has_daterange")
            codes.append(f"daterange_{daterange_status}")

        dialect_flags = _spelling_dialect_flags(s)
        for name, fired in dialect_flags.items():
            if fired:
                codes.append(name)

        return StringProcessorResult(original=s, modified=modified, codes=tuple(codes))
