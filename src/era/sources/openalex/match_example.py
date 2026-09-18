"""Example: tiered match of ERA research outputs against a local OpenAlex
works snapshot, using DuckDB.

Match priority (per user direction): DOI first, then ISBN/ISSN, then title -
this export has no ISBN/ISSN columns (see below) so it runs DOI then title.
Each ERA record falls through to the next tier only if the earlier tier finds
no match. `reference_year` is never used as a join predicate - ERA's
reference-year can diverge from OpenAlex's publication_year (ERA reporting
period vs. actual publication date) - it's only surfaced as a `year_close`
confidence flag on title-tier matches, for the user to filter on afterward.

Configured for the local OpenAlex export at D:/openalex_feb26/parquet/works
(~1,982 files, ~60GB, one row per work). That export's schema has no
ISSN/ISBN columns - just work_idx, doi, title, publication_year,
source_name, etc. - so the ISBN/ISSN tier is skipped entirely here (falls
straight from DOI to title). `work_idx` is the integer suffix of the OpenAlex
Work ID (e.g. work_idx=2741809807 <-> https://openalex.org/W2741809807), so
it's a stable, globally-meaningful join-back key. If you later work from a
fuller OpenAlex export that does carry ISSN/ISBN, set OPENALEX_ISSN_COL /
OPENALEX_ISBN_COL below and the ISBN/ISSN tier will be included again.

Querying the full 60GB snapshot on a slow HDD will take a while - this
script defaults to a 5-file glob (LIMIT_FILES) for a quick correctness check;
set LIMIT_FILES = None to run against everything.
"""

import sys
from pathlib import Path

import duckdb
import pyarrow as pa
import pyarrow.parquet as pq

sys.path.insert(0, str(Path(__file__).parent.parent))
from config import OPENALEX_COMPACT_WORKS, DUCKDB_TMP

DATA_DIR = Path(__file__).parent.parent / "data"
ERA_PARQUET = DATA_DIR / "era_research_outputs.parquet"
OUTPUT_FILE = DATA_DIR / "era_openalex_matches.parquet"
OUTPUT_FILE_SUBSET = DATA_DIR / "era_openalex_matches_subset.parquet"

OPENALEX_DIR = OPENALEX_COMPACT_WORKS
LIMIT_FILES = None  # None = read every file in OPENALEX_DIR

OPENALEX_ID_COL = "work_idx"
OPENALEX_DOI_COL = "doi"
OPENALEX_TITLE_COL = "title"
OPENALEX_YEAR_COL = "publication_year"
# This export has no ISSN/ISBN columns, so there's no tier 2 to run here -
# matching falls straight from DOI to title. If you later work from a fuller
# OpenAlex export that does carry ISSN/ISBN, add a similarly-structured
# dedup-then-join pass between the DOI and title tiers below.

# Run each tier as its own narrowly-projected scan of the OpenAlex snapshot,
# rather than one shared CTE joined twice. A CTE referenced by more than one
# downstream join can get materialized in full (title + doi + derived columns
# for ~250M rows -- tens of GB) before either join even starts, which is what
# blew out memory/disk on the first attempt. Splitting into separate scans
# also lets each pass select only the 2-3 columns it actually needs.
#
# The title tier additionally pre-deduplicates OpenAlex by title_normalized
# (one row per unique title) before joining. Without this, a generic/common
# title shared by many OpenAlex works would multiply against every ERA row
# with that same normalized title, and that combinatorial blowup - not disk
# speed - was the real cause of the earlier failure.

DOI_MATCH_SQL = f"""
WITH era AS (
    SELECT id, doi_normalized FROM read_parquet(?) WHERE doi_normalized IS NOT NULL
),
openalex_doi AS (
    SELECT
        {OPENALEX_ID_COL} AS openalex_id,
        regexp_replace(
            regexp_replace(lower(trim({OPENALEX_DOI_COL})), '^https?://(dx\\.)?doi\\.org/', ''),
            '^doi:\\s*', ''
        ) AS doi_normalized
    FROM read_parquet(?)
    WHERE {OPENALEX_DOI_COL} IS NOT NULL
)
SELECT era.id AS era_id, openalex_doi.openalex_id
FROM era JOIN openalex_doi ON era.doi_normalized = openalex_doi.doi_normalized
QUALIFY ROW_NUMBER() OVER (PARTITION BY era.id ORDER BY openalex_doi.openalex_id) = 1
"""

TITLE_MATCH_SQL = f"""
WITH era AS (
    SELECT id, title_normalized, reference_year FROM read_parquet(?)
    WHERE title_normalized IS NOT NULL AND length(title_normalized) >= 15 AND id NOT IN (
        SELECT UNNEST(?::BIGINT[])
    )
),
title_openalex AS (
    SELECT title_normalized, MIN(openalex_id) AS openalex_id, MIN(publication_year) AS publication_year
    FROM (
        SELECT
            {OPENALEX_ID_COL} AS openalex_id,
            {OPENALEX_YEAR_COL} AS publication_year,
            trim(regexp_replace(regexp_replace(lower({OPENALEX_TITLE_COL}), '[^a-z0-9\\s]', ' ', 'g'), '\\s+', ' ', 'g')) AS title_normalized
        FROM read_parquet(?)
    )
    WHERE title_normalized IS NOT NULL AND length(title_normalized) >= 15
    GROUP BY title_normalized
)
SELECT
    era.id AS era_id,
    t.openalex_id,
    (ABS(era.reference_year - t.publication_year) <= 1) AS year_close
FROM era JOIN title_openalex t ON era.title_normalized = t.title_normalized
"""


def configure_duckdb(con):
    # This machine has ~62GB RAM and 282GB free on /. Point the spill
    # directory at a local scratch dir and cap memory to leave headroom
    # for the OS.
    DUCKDB_TMP.mkdir(exist_ok=True)
    con.execute(f"PRAGMA temp_directory='{DUCKDB_TMP.as_posix()}'")
    con.execute("PRAGMA memory_limit='30GB'")
    con.execute("PRAGMA preserve_insertion_order=false")


def main():
    if not OPENALEX_DIR.exists():
        print(f"OpenAlex directory not found at {OPENALEX_DIR} - nothing to match against.")
        return

    files = sorted(OPENALEX_DIR.glob("*.parquet"))
    if LIMIT_FILES is not None:
        files = files[-LIMIT_FILES:]
    if not files:
        print(f"No parquet files found in {OPENALEX_DIR}.")
        return

    glob_arg = [str(f) for f in files] if LIMIT_FILES is not None else str(OPENALEX_DIR / "*.parquet")
    output_file = OUTPUT_FILE_SUBSET if LIMIT_FILES is not None else OUTPUT_FILE
    print(f"Matching against {len(files)} OpenAlex file(s)" + (" (subset)" if LIMIT_FILES else " (full snapshot)"))

    con = duckdb.connect()
    configure_duckdb(con)

    print("Tier 1: DOI...")
    doi_matches = con.execute(DOI_MATCH_SQL, [str(ERA_PARQUET), glob_arg]).fetchall()
    doi_matched_ids = [row[0] for row in doi_matches]
    doi_lookup = {row[0]: row[1] for row in doi_matches}
    print(f"  {len(doi_matches)} DOI matches")

    print("Tier 2: title (excluding DOI matches)...")
    title_matches = con.execute(TITLE_MATCH_SQL, [str(ERA_PARQUET), doi_matched_ids, glob_arg]).fetchall()
    title_lookup = {row[0]: (row[1], row[2]) for row in title_matches}
    print(f"  {len(title_matches)} title matches")

    era_table = con.execute(
        "SELECT id, title, doi, reference_year FROM read_parquet(?)", [str(ERA_PARQUET)]
    ).fetchall()

    ids, titles, dois, years, openalex_ids, match_types, year_closes = [], [], [], [], [], [], []
    for era_id, title, doi, reference_year in era_table:
        ids.append(era_id)
        titles.append(title)
        dois.append(doi)
        years.append(reference_year)
        if era_id in doi_lookup:
            openalex_ids.append(doi_lookup[era_id])
            match_types.append("doi")
            year_closes.append(None)
        elif era_id in title_lookup:
            openalex_id, year_close = title_lookup[era_id]
            openalex_ids.append(openalex_id)
            match_types.append("title")
            year_closes.append(year_close)
        else:
            openalex_ids.append(None)
            match_types.append("none")
            year_closes.append(None)

    table = pa.table({
        "id": pa.array(ids, type=pa.int64()),
        "title": titles,
        "doi": dois,
        "reference_year": pa.array(years, type=pa.int32()),
        "openalex_id": pa.array(openalex_ids, type=pa.int64()),
        "match_type": match_types,
        "year_close": year_closes,
    })
    pq.write_table(table, output_file, compression="snappy")

    counts = {}
    for match_type in match_types:
        counts[match_type] = counts.get(match_type, 0) + 1
    print(f"Matched {table.num_rows} ERA records. Breakdown by tier: {counts}")
    print(f"Written to {output_file}")


if __name__ == "__main__":
    main()
