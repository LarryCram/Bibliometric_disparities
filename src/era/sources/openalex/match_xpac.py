"""Tiered match of ERA research outputs against the OpenAlex "xpac" export
(D:/.../openalex_jul26/parquet_converted/xpac/works), which - unlike the
main compact works export used in match_openalex_example.py - covers a much
broader mix of non-journal-article types (dataset, other, dissertation,
report, preprint, standard, software, book, book-chapter, ...). Intended to
catch ERA outputs (creative works, reports, portfolios, etc.) that the main
export mostly missed.

Same DOI-then-title tiering as match_openalex_example.py. See that file for
the reasoning behind per-tier narrow scans and title pre-dedup.
"""

import sys
from pathlib import Path

import duckdb
import pyarrow as pa
import pyarrow.parquet as pq

sys.path.insert(0, str(Path(__file__).parent.parent))
from config import OPENALEX_XPAC_WORKS, DUCKDB_TMP

DATA_DIR = Path(__file__).parent.parent / "data"
ERA_PARQUET = DATA_DIR / "era_research_outputs.parquet"
PRIOR_MATCHES = DATA_DIR / "era_openalex_matches.parquet"
OUTPUT_FILE = DATA_DIR / "era_openalex_xpac_matches.parquet"

OPENALEX_DIR = OPENALEX_XPAC_WORKS

OPENALEX_ID_COL = "work_idx"
OPENALEX_DOI_COL = "doi"
OPENALEX_TITLE_COL = "title"
OPENALEX_YEAR_COL = "publication_year"

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
    DUCKDB_TMP.mkdir(exist_ok=True)
    con.execute(f"PRAGMA temp_directory='{DUCKDB_TMP.as_posix()}'")
    con.execute("PRAGMA memory_limit='30GB'")
    con.execute("PRAGMA preserve_insertion_order=false")


def main():
    if not OPENALEX_DIR.exists():
        print(f"OpenAlex directory not found at {OPENALEX_DIR} - nothing to match against.")
        return

    files = sorted(OPENALEX_DIR.glob("*.parquet"))
    if not files:
        print(f"No parquet files found in {OPENALEX_DIR}.")
        return
    glob_arg = str(OPENALEX_DIR / "*.parquet")
    print(f"Matching against {len(files)} xpac file(s)")

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
    pq.write_table(table, OUTPUT_FILE, compression="snappy")

    counts = {}
    for match_type in match_types:
        counts[match_type] = counts.get(match_type, 0) + 1
    print(f"Matched {table.num_rows} ERA records. Breakdown by tier: {counts}")
    print(f"Written to {OUTPUT_FILE}")


if __name__ == "__main__":
    main()
