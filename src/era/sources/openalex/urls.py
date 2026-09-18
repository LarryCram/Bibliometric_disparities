"""Attach open-access URLs to the ERA/OpenAlex matches.

The flattened works exports used for matching (compact/ and xpac/) don't
carry OA fields, but the raw OpenAlex snapshot at ~/k/openalex_jul26/parquet/works
does, nested under `open_access` and `best_oa_location`. That raw export has
no work_idx column - only `id` (e.g. "https://openalex.org/W2741809807") - so
work_idx is derived here by stripping the non-digit prefix, matching the
convention the flattened exports already use.

Only scans the four columns needed (id, open_access, best_oa_location plus
implicit row cost) rather than the full ~49-column nested schema, and filters
down to just the work_idx values that appear in either ERA match file before
writing output - the raw snapshot is 676GB/2446 files, so pulling everything
is not an option.
"""

import sys
from pathlib import Path

import duckdb

sys.path.insert(0, str(Path(__file__).parent.parent))
from config import RAW_OPENALEX_WORKS, DUCKDB_TMP

DATA_DIR = Path(__file__).parent.parent / "data"
MAIN_MATCHES = DATA_DIR / "era_openalex_matches.parquet"
XPAC_MATCHES = DATA_DIR / "era_openalex_xpac_matches.parquet"
OUTPUT_FILE = DATA_DIR / "era_openalex_matches_with_oa.parquet"

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

    print(f"Scanning raw OpenAlex snapshot at {RAW_WORKS_DIR} for OA URLs (this reads all "
          f"{len(list(RAW_WORKS_DIR.glob('updated_date=*/*.parquet')))} partitioned files, narrow projection only)...")
    con.execute(f"""
        CREATE TEMP TABLE oa_urls AS
        SELECT
            CAST(regexp_replace(id, '[^0-9]', '', 'g') AS BIGINT) AS work_idx,
            open_access.oa_url AS oa_url,
            best_oa_location.pdf_url AS best_oa_pdf_url,
            best_oa_location.landing_page_url AS best_oa_landing_page_url
        FROM read_parquet('{RAW_WORKS_DIR}/*/*.parquet', hive_partitioning=1)
        WHERE CAST(regexp_replace(id, '[^0-9]', '', 'g') AS BIGINT) IN (SELECT work_idx FROM wanted_ids)
    """)
    n_found = con.execute("SELECT COUNT(*) FROM oa_urls").fetchone()[0]
    print(f"  found OA info for {n_found} of {n_needed} matched works")

    print("Joining OA URLs onto ERA matches (main export first, xpac as fallback)...")
    con.execute(f"""
        COPY (
            WITH combined AS (
                SELECT id, title, doi, reference_year, openalex_id, match_type, year_close, 'main' AS source
                FROM read_parquet('{MAIN_MATCHES}')
                WHERE match_type != 'none'
                UNION ALL
                SELECT id, title, doi, reference_year, openalex_id, match_type, year_close, 'xpac' AS source
                FROM read_parquet('{XPAC_MATCHES}')
                WHERE match_type != 'none'
                  AND id NOT IN (SELECT id FROM read_parquet('{MAIN_MATCHES}') WHERE match_type != 'none')
            )
            SELECT
                c.id, c.title, c.doi, c.reference_year, c.openalex_id, c.match_type, c.year_close, c.source,
                oa.oa_url, oa.best_oa_pdf_url, oa.best_oa_landing_page_url
            FROM combined c
            LEFT JOIN oa_urls oa ON oa.work_idx = c.openalex_id
        ) TO '{OUTPUT_FILE}' (FORMAT PARQUET, COMPRESSION SNAPPY)
    """)

    counts = con.execute(f"""
        SELECT
            COUNT(*) AS n_matched_era_records,
            SUM(CASE WHEN oa_url IS NOT NULL THEN 1 ELSE 0 END) AS n_with_oa_url,
            SUM(CASE WHEN best_oa_pdf_url IS NOT NULL THEN 1 ELSE 0 END) AS n_with_pdf_url
        FROM read_parquet('{OUTPUT_FILE}')
    """).fetchone()
    print(f"Matched ERA records: {counts[0]}, with oa_url: {counts[1]}, with best_oa_pdf_url: {counts[2]}")
    print(f"Written to {OUTPUT_FILE}")


if __name__ == "__main__":
    main()
