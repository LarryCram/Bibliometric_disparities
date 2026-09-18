"""Extract OpenAlex author names, ORCIDs, and institution/country data for
every ERA-matched work - a fourth narrow-projection scan of the raw
~/k/openalex_jul26/parquet/works snapshot, same pattern as add_oa_urls.py
and add_oa_locations.py.

Kept as its own script rather than folded into add_oa_urls.py: authorship
data is a heavier nested structure (many authors x many institutions per
work) than a couple of URL strings, and a PDF-download bug fix shouldn't
force an unrelated, expensive authorship re-scan.

Each work's `authorships` array has one entry per author, with a nested
`institutions` array per author (an author can list more than one
institution) and a flattened `countries` array. `institutions` is
frequently EMPTY even when `countries` is populated - OpenAlex had a raw
affiliation string it couldn't resolve to a ROR'd institution. Downstream
analysis must fall back to `countries` rather than reading an empty
`institutions` as "no country data".

One scan, three output tables (avoids a later, separate expensive scan
just for citation/reference counts, which live on the same raw record):
  - oa_work_stats.parquet: one row per oax_id (publication_year,
    cited_by_count, referenced_works_count)
  - oa_authorships.parquet: one row per (oax_id, author_rank) - author_rank
    is the 1-based position in the authorships array (the grain key -
    author_position is just the "first"/"middle"/"last" label, not unique
    when there's more than one middle author)
  - oa_authorship_institutions.parquet: one row per (oax_id, author_rank,
    institution) - an author can have more than one listed institution
"""

import sys
from pathlib import Path

import duckdb

sys.path.insert(0, str(Path(__file__).parent.parent))
from config import RAW_OPENALEX_WORKS, DUCKDB_TMP

DATA_DIR = Path(__file__).parent.parent / "data"
MAIN_MATCHES = DATA_DIR / "era_openalex_matches.parquet"
XPAC_MATCHES = DATA_DIR / "era_openalex_xpac_matches.parquet"

WORK_STATS_FILE = DATA_DIR / "oa_work_stats.parquet"
AUTHORSHIPS_FILE = DATA_DIR / "oa_authorships.parquet"
AUTHORSHIP_INSTITUTIONS_FILE = DATA_DIR / "oa_authorship_institutions.parquet"

RAW_WORKS_DIR = RAW_OPENALEX_WORKS


def configure_duckdb(con):
    DUCKDB_TMP.mkdir(exist_ok=True)
    con.execute(f"PRAGMA temp_directory='{DUCKDB_TMP.as_posix()}'")
    con.execute("PRAGMA memory_limit='30GB'")
    con.execute("PRAGMA preserve_insertion_order=false")


def main():
    con = duckdb.connect()
    configure_duckdb(con)

    print("Collecting matched OpenAlex work_idx values from both ERA match files...")
    con.execute(f"""
        CREATE TEMP TABLE wanted_ids AS
        SELECT DISTINCT openalex_id AS work_idx FROM read_parquet('{MAIN_MATCHES}') WHERE openalex_id IS NOT NULL
        UNION
        SELECT DISTINCT openalex_id FROM read_parquet('{XPAC_MATCHES}') WHERE openalex_id IS NOT NULL
    """)
    n_needed = con.execute("SELECT COUNT(*) FROM wanted_ids").fetchone()[0]
    print(f"  {n_needed} distinct matched works")

    print(f"Scanning raw OpenAlex snapshot at {RAW_WORKS_DIR} for authorships "
          f"(narrow projection: id, authorships, publication_year, cited_by_count, "
          f"referenced_works_count only)...")
    con.execute(f"""
        CREATE TEMP TABLE matched_raw AS
        SELECT
            CAST(regexp_replace(id, '[^0-9]', '', 'g') AS BIGINT) AS oax_id,
            authorships,
            publication_year,
            cited_by_count,
            referenced_works_count
        FROM read_parquet('{RAW_WORKS_DIR}/*/*.parquet', hive_partitioning=1)
        WHERE CAST(regexp_replace(id, '[^0-9]', '', 'g') AS BIGINT) IN (SELECT work_idx FROM wanted_ids)
    """)
    n_found = con.execute("SELECT COUNT(*) FROM matched_raw").fetchone()[0]
    print(f"  found {n_found} of {n_needed} matched works in the raw snapshot")

    print("Writing oa_work_stats.parquet...")
    con.execute(f"""
        COPY (
            SELECT oax_id, publication_year, cited_by_count, referenced_works_count
            FROM matched_raw
        ) TO '{WORK_STATS_FILE}' (FORMAT PARQUET, COMPRESSION SNAPPY)
    """)

    print("Writing oa_authorships.parquet...")
    con.execute(f"""
        COPY (
            SELECT
                oax_id,
                auth_rank AS author_rank,
                auth.author_position AS author_position,
                auth.author.id AS author_id,
                auth.author.display_name AS author_display_name,
                auth.author.orcid AS author_orcid,
                auth.is_corresponding AS is_corresponding,
                len(auth.institutions) AS n_institutions,
                auth.countries AS country_codes,
                auth.raw_affiliation_strings AS raw_affiliation_strings
            FROM matched_raw, UNNEST(authorships) WITH ORDINALITY AS t(auth, auth_rank)
        ) TO '{AUTHORSHIPS_FILE}' (FORMAT PARQUET, COMPRESSION SNAPPY)
    """)

    print("Writing oa_authorship_institutions.parquet...")
    con.execute(f"""
        COPY (
            SELECT
                oax_id,
                auth_rank AS author_rank,
                inst.id AS institution_id,
                inst.display_name AS institution_display_name,
                inst.country_code AS country_code,
                inst.ror AS ror,
                inst.lineage AS lineage,
                inst.type AS institution_type
            FROM matched_raw, UNNEST(authorships) WITH ORDINALITY AS t(auth, auth_rank),
                 UNNEST(auth.institutions) AS t2(inst)
        ) TO '{AUTHORSHIP_INSTITUTIONS_FILE}' (FORMAT PARQUET, COMPRESSION SNAPPY)
    """)

    n_authors = con.execute(f"SELECT COUNT(*) FROM read_parquet('{AUTHORSHIPS_FILE}')").fetchone()[0]
    n_author_institutions = con.execute(
        f"SELECT COUNT(*) FROM read_parquet('{AUTHORSHIP_INSTITUTIONS_FILE}')"
    ).fetchone()[0]
    n_empty_institutions = con.execute(
        f"SELECT COUNT(*) FROM read_parquet('{AUTHORSHIPS_FILE}') WHERE n_institutions = 0"
    ).fetchone()[0]
    print(f"\n{n_found} works, {n_authors} author rows ({n_empty_institutions} with no linked "
          f"institution - has country_codes as fallback), {n_author_institutions} author-institution rows")
    print(f"Written to {WORK_STATS_FILE}, {AUTHORSHIPS_FILE}, {AUTHORSHIP_INSTITUTIONS_FILE}")


if __name__ == "__main__":
    main()
