"""ERAProcessor: the factory that turns raw ERA download rows into canonical
ERAResearchRecord objects.

Two disparity layers, both classified with Olensky's taxonomy via the same
reusable DisparityClassifier (see olensky.py):
  1. ERA-internal: a raw field may need self-cleaning even with no duplicate
     (e.g. a "doi:" prefix), and where multiple HEPs report the same real
     output, their raw values must be reconciled into one canonical value -
     both are Olensky-coded disparity events, accumulated as this record's
     provenance.
  2. ERA-vs-external: once a canonical ERAResearchRecord exists, it is
     compared against its matched OpenAlex/Scopus/Trove/WOS profile using
     the same classifier - not yet implemented here.

Every raw row must end up contributing to exactly one ERAResearchRecord,
whether as part of a resolved duplicate group or as an unresolved
singleton - nothing is silently dropped.
"""

import json
import re
import sys
from dataclasses import dataclass, field
from pathlib import Path

import duckdb
import pyarrow as pa
import pyarrow.parquet as pq

from era.core.olensky import doi_classifier, FreeTextDisparityClassifier, single_char_indel_code
from era.core.string_processor import StringProcessor
from era.core.field_processor import get_processor, FIELD_PROCESSORS, NUMERIC_DESCRIPTOR_FIELDS

from era.config import DATA_DIR

# Pre-Olensky: field presence has no target to compare against yet, so it
# isn't one of Olensky's relational codes (E/Z) - it's the precondition
# stage A checks before stage B's classification can even run.
FIELD_PRESENT = "FIELD_PRESENT"
FIELD_NULL = "FIELD_NULL"

# Literal placeholder strings standing in for a genuinely missing value,
# rather than real data - confirmed real, not hypothetical: "Unknown"
# appears 12,510 times in extent (Journal Article), and "Null" appears
# once in standard_number. Checked case-insensitively at the presence
# stage, before any field-specific classifier ever runs - these aren't
# malformed values to clean, they're missing values wearing a disguise.
NULL_SENTINELS = {"unknown", "null", "n/a", "na", "none", "-"}

# outlet/publisher values cut off mid-word by an apparent fixed-length
# source-system truncation, confirmed 2026-09-04: "ieee - the institute
# of electrical and electronic engi..", "impc 2012 - international
# mineral processing cong.." - the text right before the dots is a word
# FRAGMENT, not a real word. title ALSO has 30 values ending in the same
# "\.\.+$" shape, but sampling them showed complete, coherent titles
# using ellipsis as genuine stylistic punctuation ("does practice make
# perfect? it all depends..", "...earth tones.."} - real words, not
# fragments. title is deliberately excluded from TRUNCATED_FIELDS for
# this reason - applying this fix there would mis-tag legitimate
# authorial punctuation as cropped data.
_TRUNCATION_RE = re.compile(r"\s*\.\.+$")
TRUNCATED_FIELDS = ["outlet", "publisher"]

# Every field registered in the factory except doi/title, which keep their
# own dedicated, richer treatment (title has structural features beyond
# presence/wellformed/codes; both are already persisted), and
# reference_year, which was confirmed always well-formed - tracking
# presence/wellformed for it was dropped as pure overhead, not an
# oversight. Built from the factory's own registration, not a hand-typed
# list, so a newly-registered field is picked up automatically.
GENERIC_FIELDS = sorted({f for f, _ in FIELD_PROCESSORS} - {"doi", "title", "reference_year"})


@dataclass
class ERAResearchRecord:
    source_ids: list[int] = field(default_factory=list)
    doi_presence: str | None = None
    doi: str | None = None
    doi_wellformed: bool | None = None
    doi_provenance: list[str] = field(default_factory=list)
    title_presence: str | None = None
    title: str | None = None
    title_wellformed: bool | None = None
    title_provenance: list[str] = field(default_factory=list)
    title_has_html: bool | None = None
    title_has_tex: bool | None = None
    title_has_non_latin: bool | None = None
    title_has_mojibake: bool | None = None
    title_has_daterange: bool | None = None
    title_daterange_status: str | None = None
    title_has_en_us: bool | None = None
    title_has_en_gb: bool | None = None
    title_has_en_au: bool | None = None
    reference_year: int | None = None


class ERAProcessor:
    """Factory for ERAResearchRecord. Owns every stage of turning ERA's raw
    download into canonical, provenance-tracked bibliometric records.
    """

    RAW_JSONL = DATA_DIR / "era_research_outputs.jsonl"
    RAW_PARQUET = DATA_DIR / "era_research_outputs_raw.parquet"
    PROGRESS_FILE = DATA_DIR / "era_download_progress.json"

    # Every field ERA's own download actually provides, flattened to its
    # own column under ERA's own name (kebab-case -> snake_case only - a
    # mechanical, lossless transliteration, not a new name: a literal
    # hyphen would parse as subtraction in unquoted SQL). Confirmed by a
    # full-dataset type scan (2026-09-03) before writing this schema, not
    # assumed from a handful of examples - every field is internally
    # type-consistent (e.g. era-journal-id is always int, era-conference-id
    # is always str; they don't share a type, but neither one varies
    # within itself). portfolio-items is the one exception kept as a JSON
    # string rather than flattened further: it's a genuinely nested list
    # of sub-objects (a portfolio can hold multiple items, each with its
    # own title/type/extent/etc.), which doesn't fit a scalar column
    # without violating one-row-per-source-record.
    RAW_SCHEMA = pa.schema([
        ("id", pa.int64()),
        ("era_round", pa.string()),
        ("institution", pa.string()),
        ("research_output_type", pa.string()),
        ("title", pa.string()),
        ("outlet", pa.string()),
        ("reference_year", pa.int32()),
        ("doi", pa.string()),
        ("place_of_publication", pa.string()),
        ("category_type", pa.string()),
        ("conference_name", pa.string()),
        ("edition", pa.string()),
        ("era_conference_id", pa.string()),
        ("era_journal_id", pa.int64()),
        ("extent", pa.string()),
        ("identifier", pa.string()),
        ("isbn", pa.string()),
        ("issn", pa.string()),
        ("issue", pa.string()),
        ("journal_issue", pa.string()),
        ("journal_title", pa.string()),
        ("journal_volume", pa.string()),
        ("media", pa.string()),
        ("notes", pa.string()),
        ("outlet_edition", pa.string()),
        ("outlet_editor", pa.string()),
        ("outlet_isbn", pa.string()),
        ("outlet_issue", pa.string()),
        ("outlet_venue", pa.string()),
        ("outlet_volume", pa.string()),
        ("portfolio_items", pa.string()),
        ("portfolio_number", pa.string()),
        ("publisher", pa.string()),
        ("standard_number", pa.string()),
        ("volume", pa.string()),
    ])

    RAW_BATCH_SIZE = 50_000

    PROCESSED_PARQUET = DATA_DIR / "era_research_outputs_processed.parquet"

    PROCESSED_SCHEMA = pa.schema([
        ("id", pa.int64()),
        ("doi_presence", pa.string()),
        ("doi", pa.string()),
        ("doi_wellformed", pa.bool_()),
        ("doi_provenance", pa.list_(pa.string())),
        ("title_presence", pa.string()),
        ("title", pa.string()),
        ("title_wellformed", pa.bool_()),
        ("title_provenance", pa.list_(pa.string())),
        ("title_has_html", pa.bool_()),
        ("title_has_tex", pa.bool_()),
        ("title_has_non_latin", pa.bool_()),
        ("title_has_mojibake", pa.bool_()),
        ("title_has_daterange", pa.bool_()),
        ("title_daterange_status", pa.string()),
        ("title_has_en_us", pa.bool_()),
        ("title_has_en_gb", pa.bool_()),
        ("title_has_en_au", pa.bool_()),
        ("reference_year", pa.int32()),
    ] + [
        col
        for f in GENERIC_FIELDS
        for col in (
            (f"{f}_presence", pa.string()),
            (f, pa.string()),
            (f"{f}_is_numeric" if f in NUMERIC_DESCRIPTOR_FIELDS else f"{f}_wellformed", pa.bool_()),
            (f"{f}_provenance", pa.list_(pa.string())),
        )
    ])

    _doi_classifier = doi_classifier
    _title_classifier = FreeTextDisparityClassifier()
    _string_processor = StringProcessor()

    @staticmethod
    def _to_raw_row(record):
        attrs = record["attributes"]
        other = attrs.get("other-details") or {}
        portfolio_items = other.get("portfolio-items")
        return {
            "id": int(record["id"]),
            "era_round": attrs.get("era-round"),
            "institution": attrs.get("institution"),
            "research_output_type": attrs.get("research-output-type"),
            "title": attrs.get("title"),
            "outlet": attrs.get("outlet"),
            "reference_year": attrs.get("reference-year"),
            "doi": attrs.get("doi"),
            "place_of_publication": attrs.get("place-of-publication"),
            "category_type": other.get("category-type"),
            "conference_name": other.get("conference-name"),
            "edition": other.get("edition"),
            "era_conference_id": other.get("era-conference-id"),
            "era_journal_id": other.get("era-journal-id"),
            "extent": other.get("extent"),
            "identifier": other.get("identifier"),
            "isbn": other.get("isbn"),
            "issn": other.get("issn"),
            "issue": other.get("issue"),
            "journal_issue": other.get("journal-issue"),
            "journal_title": other.get("journal-title"),
            "journal_volume": other.get("journal-volume"),
            "media": other.get("media"),
            "notes": other.get("notes"),
            "outlet_edition": other.get("outlet-edition"),
            "outlet_editor": other.get("outlet-editor"),
            "outlet_isbn": other.get("outlet-isbn"),
            "outlet_issue": other.get("outlet-issue"),
            "outlet_venue": other.get("outlet-venue"),
            "outlet_volume": other.get("outlet-volume"),
            "portfolio_items": json.dumps(portfolio_items) if portfolio_items else None,
            "portfolio_number": other.get("portfolio-number"),
            "publisher": other.get("publisher"),
            "standard_number": other.get("standard-number"),
            "volume": other.get("volume"),
        }

    @classmethod
    def _check_download_complete(cls):
        if not cls.PROGRESS_FILE.exists():
            print(f"Warning: {cls.PROGRESS_FILE} not found, cannot verify download is complete.")
            return
        progress = json.loads(cls.PROGRESS_FILE.read_text())
        if progress.get("last_completed_page") != progress.get("total_pages"):
            print(
                f"Warning: download appears incomplete "
                f"(page {progress.get('last_completed_page')}/{progress.get('total_pages')}). "
                f"Converting available data anyway."
            )

    @classmethod
    def build_raw(cls):
        """Convert era_research_outputs.jsonl into era_research_outputs_raw.parquet -
        a precise, untransformed copy of the download, kept only for query
        speed. No normalization, no derived fields: other-details is kept as
        one raw JSON string rather than decomposed into isbn/issn/etc, since
        that decomposition already involves choices that belong in
        classification, not here.
        """
        cls._check_download_complete()

        writer = pq.ParquetWriter(cls.RAW_PARQUET, cls.RAW_SCHEMA, compression="snappy")
        rows = []
        total_rows = 0

        with cls.RAW_JSONL.open(encoding="utf-8") as f:
            for line in f:
                rows.append(cls._to_raw_row(json.loads(line)))
                if len(rows) >= cls.RAW_BATCH_SIZE:
                    writer.write_table(pa.Table.from_pylist(rows, schema=cls.RAW_SCHEMA))
                    total_rows += len(rows)
                    print(f"Wrote {total_rows} rows...")
                    rows = []

        if rows:
            writer.write_table(pa.Table.from_pylist(rows, schema=cls.RAW_SCHEMA))
            total_rows += len(rows)

        writer.close()
        print(f"Done. {total_rows} rows written to {cls.RAW_PARQUET}")

    @classmethod
    def sample_raw(cls, n):
        """A random sample of n raw rows, for developing/debugging
        classification logic before running it against the full table."""
        con = duckdb.connect()
        rows = con.execute(f"""
            SELECT * FROM read_parquet('{cls.RAW_PARQUET}')
            ORDER BY random() LIMIT {int(n)}
        """).fetchall()
        cols = [d[0] for d in con.description]
        return [dict(zip(cols, r)) for r in rows]

    @classmethod
    def classify_doi(cls, raw_row) -> ERAResearchRecord:
        """Stage A (presence) then stage B (self-clean) for one raw row's
        doi field. No separate duplicate-detection stage is built on top of
        this for doi specifically: measured against the full raw table,
        99.9% of present DOIs are already well-formed with zero cleaning
        needed, so a plain equality check on the cleaned `doi` value (once
        doi_wellformed is True) already serves as a reliable join/lookup
        key on its own - e.g. for finding a record's matched OpenAlex/Scopus
        profile - without needing Olensky-coded battery work to get there.
        Cross-institution duplicate detection more broadly is handled by
        title matching (see analysis/find_cross_institution_duplicates.py),
        which covers the ~34% of records with no DOI at all too.
        """
        record = ERAResearchRecord(source_ids=[raw_row["id"]])
        doi = raw_row.get("doi")
        if doi is None or doi.strip() == "":
            record.doi_presence = FIELD_NULL
            return record

        record.doi_presence = FIELD_PRESENT
        result = cls._doi_classifier.classify_wellformedness(doi)
        record.doi = result.cleaned_value
        record.doi_wellformed = result.wellformed
        record.doi_provenance = result.codes
        return record

    @classmethod
    def classify_title(cls, raw_row, record=None) -> ERAResearchRecord:
        """Stage A (presence) then stage B (self-clean) for one raw row's
        title field, plus pre-Olensky structural features (has_html,
        has_tex, has_non_latin, has_daterange/daterange_status,
        has_spelling_british/us - see string_processor.py) computed on the
        raw value. Pass an existing record (e.g. from classify_doi) to add
        title fields onto it rather than creating a new one.
        """
        if record is None:
            record = ERAResearchRecord(source_ids=[raw_row["id"]])
        title = raw_row.get("title")
        if title is None or title.strip() == "":
            record.title_presence = FIELD_NULL
            return record

        record.title_presence = FIELD_PRESENT
        features = cls._string_processor.process(title)
        record.title_has_html = "has_html" in features.codes
        record.title_has_tex = "has_tex" in features.codes
        record.title_has_non_latin = "has_non_latin" in features.codes
        record.title_has_mojibake = "has_mojibake" in features.codes
        record.title_has_daterange = "has_daterange" in features.codes
        record.title_daterange_status = (
            "adjusted" if "daterange_adjusted" in features.codes
            else "valid" if "daterange_valid" in features.codes
            else None
        )
        record.title_has_en_us = "has_en_US" in features.codes
        record.title_has_en_gb = "has_en_GB" in features.codes
        record.title_has_en_au = "has_en_AU" in features.codes
        result = cls._title_classifier.classify_wellformedness(title)
        record.title = result.cleaned_value
        record.title_wellformed = result.wellformed
        record.title_provenance = result.codes
        return record

    @classmethod
    def classify_field(cls, raw_row, field):
        """Generic stage A (presence) + stage B (well-formedness) for any
        field registered in field_processor.FIELD_PROCESSORS, dispatched
        by (field, research_output_type). Returns a plain dict
        (presence/cleaned_value/wellformed/codes) rather than mutating an
        ERAResearchRecord - unlike doi/title, most of these ~25 fields
        aren't being added as permanent named columns yet (that's a
        separate schema decision), this is for the diagnostic sweep to
        measure real per-field disparity rates before deciding what's
        worth persisting.
        """
        value = raw_row.get(field)
        if (
            value is None
            or (isinstance(value, str) and value.strip() == "")
            or (isinstance(value, str) and value.strip().lower() in NULL_SENTINELS)
        ):
            return {"presence": FIELD_NULL, "cleaned_value": None, "wellformed": None, "codes": []}
        processor = get_processor(field, raw_row.get("research_output_type"))
        result = processor.process(value, field, raw_row.get("research_output_type"), raw_row=raw_row)
        return {
            "presence": FIELD_PRESENT,
            "cleaned_value": result.cleaned_value,
            "wellformed": result.wellformed,
            "codes": result.codes,
        }

    @classmethod
    def _to_processed_row(cls, raw_row):
        record = cls.classify_doi(raw_row)
        cls.classify_title(raw_row, record=record)
        record.reference_year = raw_row.get("reference_year")
        row = {
            "id": record.source_ids[0],
            "doi_presence": record.doi_presence,
            "doi": record.doi,
            "doi_wellformed": record.doi_wellformed,
            "doi_provenance": record.doi_provenance,
            "title_presence": record.title_presence,
            "title": record.title,
            "title_wellformed": record.title_wellformed,
            "title_provenance": record.title_provenance,
            "title_has_html": record.title_has_html,
            "title_has_tex": record.title_has_tex,
            "title_has_non_latin": record.title_has_non_latin,
            "title_has_mojibake": record.title_has_mojibake,
            "title_has_daterange": record.title_has_daterange,
            "title_daterange_status": record.title_daterange_status,
            "title_has_en_us": record.title_has_en_us,
            "title_has_en_gb": record.title_has_en_gb,
            "title_has_en_au": record.title_has_en_au,
            "reference_year": record.reference_year,
        }
        for f in GENERIC_FIELDS:
            result = cls.classify_field(raw_row, f)
            row[f"{f}_presence"] = result["presence"]
            row[f] = result["cleaned_value"]
            wellformed_key = f"{f}_is_numeric" if f in NUMERIC_DESCRIPTOR_FIELDS else f"{f}_wellformed"
            row[wellformed_key] = result["wellformed"]
            row[f"{f}_provenance"] = result["codes"]
        return row

    @classmethod
    def build_processed(cls):
        """Run DOI+title stage A/B classification (plus title's structural
        features) over every row in era_research_outputs_raw.parquet,
        writing era_research_outputs_processed.parquet - one row per raw
        row, no HEP-deduplication/merging yet (that's the stage-C combiner,
        not yet designed). No sampling - the full ~567,647 rows.
        """
        con = duckdb.connect()
        rows = con.execute(f"""
            SELECT * FROM read_parquet('{cls.RAW_PARQUET}')
        """).fetchall()
        cols = [d[0] for d in con.description]

        writer = pq.ParquetWriter(cls.PROCESSED_PARQUET, cls.PROCESSED_SCHEMA, compression="snappy")
        batch = []
        total_rows = 0
        for r in rows:
            raw_row = dict(zip(cols, r))
            batch.append(cls._to_processed_row(raw_row))
            if len(batch) >= cls.RAW_BATCH_SIZE:
                writer.write_table(pa.Table.from_pylist(batch, schema=cls.PROCESSED_SCHEMA))
                total_rows += len(batch)
                print(f"Processed {total_rows} rows...")
                batch = []

        if batch:
            writer.write_table(pa.Table.from_pylist(batch, schema=cls.PROCESSED_SCHEMA))
            total_rows += len(batch)

        writer.close()
        print(f"Done. {total_rows} rows written to {cls.PROCESSED_PARQUET}")

    @staticmethod
    def _fix_indels_within_clusters(records, cluster_key, wellformed_key):
        """One clustering pass: group records by cluster_key (only where
        wellformed_key is True - the shared value must itself be reliable
        before it's trusted as evidence two rows denote the same output),
        then within each 2+-row cluster, check every pair of distinct
        titles for a single-character indel. Returns the number of rows
        fixed. Runs against whatever records[i]["title"] currently holds,
        so calling this again with a different cluster_key after an
        earlier pass has already run sees and builds on those fixes.
        """
        by_key = {}
        for i, r in enumerate(records):
            if r[wellformed_key]:
                by_key.setdefault(r[cluster_key], []).append(i)

        n_fixed = 0
        for _, idxs in by_key.items():
            if len(idxs) < 2:
                continue
            distinct_titles = {}
            for i in idxs:
                t = records[i]["title"]
                if t is not None:
                    distinct_titles.setdefault(t, []).append(i)
            if len(distinct_titles) < 2:
                continue

            titles = list(distinct_titles.keys())
            for a in range(len(titles)):
                for b in range(a + 1, len(titles)):
                    t_a, t_b = titles[a], titles[b]
                    code = single_char_indel_code(t_a, t_b)
                    if code is None:
                        continue
                    shorter, longer = (t_a, t_b) if len(t_a) < len(t_b) else (t_b, t_a)
                    for i in distinct_titles[shorter]:
                        records[i]["title"] = longer
                        records[i]["title_provenance"] = list(records[i]["title_provenance"]) + [code]
                        n_fixed += 1
        return n_fixed

    @classmethod
    def apply_cluster_indel_fixes(cls):
        """Cluster-level pass, run after build_processed(): for clusters
        of 2+ ERA records sharing a reliable join value whose cleaned
        titles still differ, check every pair of distinct title values for
        a single-character indel (olensky.single_char_indel_code). Where
        found, the shorter form is brought up to match the longer one and
        the detected code is appended to its title_provenance. Rewrites
        era_research_outputs_processed.parquet in place with the fixes
        applied - this is a cross-record comparison, so it can't live in
        classify_title() itself, which only ever sees one row at a time.

        Runs over BOTH doi and identifier clusters (2026-09-04, added
        identifier after a direct comparison showed it finds more:
        69,944 identifier clusters covering 165,251 records vs DOI's
        57,625 clusters/131,977 records, with 29,864 of those records
        having no DOI at all to cluster on in the first place - a real,
        confirmed-larger population of same-output multi-HEP submissions
        that DOI clustering structurally cannot see). doi runs first
        since it was already established; identifier runs second and
        sees whatever doi's pass already fixed, so the two compose rather
        than duplicate work.
        """
        con = duckdb.connect()
        rows = con.execute(f"SELECT * FROM read_parquet('{cls.PROCESSED_PARQUET}')").fetchall()
        cols = [d[0] for d in con.description]
        records = [dict(zip(cols, r)) for r in rows]

        n_fixed = cls._fix_indels_within_clusters(records, "doi", "doi_wellformed")
        n_fixed += cls._fix_indels_within_clusters(records, "identifier", "identifier_wellformed")

        writer = pq.ParquetWriter(cls.PROCESSED_PARQUET, cls.PROCESSED_SCHEMA, compression="snappy")
        writer.write_table(pa.Table.from_pylist(records, schema=cls.PROCESSED_SCHEMA))
        writer.close()
        print(f"Applied {n_fixed} cluster-level single-character-indel fixes.")
        print(f"Rewrote {cls.PROCESSED_PARQUET}")

    @staticmethod
    def _repair_truncated_in_records(records, field):
        """One field's worth of the truncation repair: tags every value
        ending in the fixed-length-truncation shape (_TRUNCATION_RE) with
        F (Cropped - Olensky's own definition, "value incomplete at the
        start or end"), then attempts a repair by matching its prefix
        against every OTHER value in this field across the WHOLE
        records list - not scoped to any DOI/identifier cluster, since
        the same publisher/outlet name legitimately recurs across many
        unrelated records, so any other untruncated occurrence is a
        valid model regardless of which output it belongs to. Only
        repairs when exactly one distinct untruncated value shares the
        prefix; an absent or ambiguous match is left flagged but
        unrepaired rather than guessed - confirmed 2026-09-04: of 5
        distinct truncated publisher values, 3 had a unique match
        ("...construction man.." -> "...construction management") and 2
        did not ("...electrical and electronic engi.." - no untruncated
        form exists anywhere in the dataset). Returns (n_flagged,
        n_repaired); mutates records in place.
        """
        clean_values = {
            r[field] for r in records
            if r[field] and not _TRUNCATION_RE.search(r[field])
        }
        n_flagged = n_repaired = 0
        for r in records:
            v = r[field]
            if not v or not _TRUNCATION_RE.search(v):
                continue
            n_flagged += 1
            prefix = _TRUNCATION_RE.sub("", v)
            matches = {c for c in clean_values if c.startswith(prefix)}
            if len(matches) == 1:
                r[field] = next(iter(matches))
                n_repaired += 1
            r[f"{field}_provenance"] = list(r[f"{field}_provenance"]) + ["F"]
        return n_flagged, n_repaired

    @classmethod
    def repair_truncated_fields(cls):
        """Third pass, run after apply_cluster_indel_fixes(): applies
        _repair_truncated_in_records to every field in TRUNCATED_FIELDS
        (outlet, publisher) and rewrites era_research_outputs_processed.parquet
        in place - a cross-record comparison, so it can't live in
        classify_field() itself.
        """
        con = duckdb.connect()
        rows = con.execute(f"SELECT * FROM read_parquet('{cls.PROCESSED_PARQUET}')").fetchall()
        cols = [d[0] for d in con.description]
        records = [dict(zip(cols, r)) for r in rows]

        n_flagged = n_repaired = 0
        for f in TRUNCATED_FIELDS:
            flagged, repaired = cls._repair_truncated_in_records(records, f)
            n_flagged += flagged
            n_repaired += repaired

        writer = pq.ParquetWriter(cls.PROCESSED_PARQUET, cls.PROCESSED_SCHEMA, compression="snappy")
        writer.write_table(pa.Table.from_pylist(records, schema=cls.PROCESSED_SCHEMA))
        writer.close()
        print(f"Flagged {n_flagged} truncated values (F), repaired {n_repaired} via unique-prefix match.")
        print(f"Rewrote {cls.PROCESSED_PARQUET}")


def main():
    if len(sys.argv) != 2 or sys.argv[1] not in ("build-raw", "build-processed"):
        print("Usage: era_processor.py build-raw|build-processed")
        sys.exit(1)
    if sys.argv[1] == "build-raw":
        ERAProcessor.build_raw()
    else:
        ERAProcessor.build_processed()


if __name__ == "__main__":
    main()
