"""Extract every OA location (not just best_oa_location) for each
ERA-matched OpenAlex work, as fallback candidates for download_oa_pdfs_direct.py
and download_oa_pdfs_landing.py.

add_oa_urls.py only pulled best_oa_location - OpenAlex's own pick of the
single "best" copy, which is frequently a publisher's "bronze" OA page (free
to read at the publisher's discretion, no license, no repository backup -
see the access-investigation notes in download_oa_pdfs_landing.py's history).
Bronze/publisher pages are exactly the ones most often behind Cloudflare/AWS
WAF bot-walls; an institutional-repository mirror of the same work usually
isn't. This pulls the full `locations` array so the downloader has a
fallback chain instead of one shot per work, including two things this
script used to drop:
  - locations with is_oa=false. OpenAlex's location-level is_oa flag is
    conservative/sometimes wrong - a repository entry marked is_oa=false
    can still have a resolvable landing_page_url worth trying, and
    previously such locations were excluded from the candidate set
    entirely, before a download was ever attempted.
  - loc.source.type ("repository" vs "journal" etc.) and loc.version
    ("publishedVersion"/"acceptedVersion"/"submittedVersion"), so
    download_oa_pdfs_landing.py can prefer a repository-hosted copy (even
    an accepted-manuscript version) over a publisher one, rather than
    trying candidates in OpenAlex's own arbitrary location order.

Same narrow-projection-scan-of-the-raw-676GB-snapshot approach as
add_oa_urls.py: only reads id/locations, not the full ~49-column schema, and
filters down to the openalex_id set that appears in the ERA matches before
writing output.
"""

import sys
from pathlib import Path

import duckdb

sys.path.insert(0, str(Path(__file__).parent.parent))
from config import RAW_OPENALEX_WORKS, DUCKDB_TMP

DATA_DIR = Path(__file__).parent.parent / "data"
MAIN_MATCHES = DATA_DIR / "era_openalex_matches.parquet"
XPAC_MATCHES = DATA_DIR / "era_openalex_xpac_matches.parquet"
OUTPUT_FILE = DATA_DIR / "oa_locations.parquet"

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

    print(f"Scanning raw OpenAlex snapshot at {RAW_WORKS_DIR} for all OA locations "
          f"(narrow projection: id, locations only)...")
    con.execute(f"""
        COPY (
            WITH matched AS (
                SELECT
                    CAST(regexp_replace(id, '[^0-9]', '', 'g') AS BIGINT) AS work_idx,
                    UNNEST(locations) AS loc
                FROM read_parquet('{RAW_WORKS_DIR}/*/*.parquet', hive_partitioning=1)
                WHERE CAST(regexp_replace(id, '[^0-9]', '', 'g') AS BIGINT) IN (SELECT work_idx FROM wanted_ids)
            )
            SELECT
                work_idx AS oax_id,
                loc.is_oa AS is_oa,
                loc.pdf_url AS pdf_url,
                loc.landing_page_url AS landing_page_url,
                loc.source.display_name AS source_name,
                loc.source.type AS source_type,
                loc.version AS version
            FROM matched
            WHERE loc.pdf_url IS NOT NULL OR loc.landing_page_url IS NOT NULL
        ) TO '{OUTPUT_FILE}' (FORMAT PARQUET, COMPRESSION SNAPPY)
    """)

    counts = con.execute(f"""
        SELECT
            COUNT(*) AS n_locations,
            COUNT(DISTINCT oax_id) AS n_works,
            SUM((pdf_url IS NOT NULL)::INT) AS n_with_pdf_url,
            SUM((is_oa)::INT) AS n_is_oa,
            SUM((source_type = 'repository')::INT) AS n_repository
        FROM read_parquet('{OUTPUT_FILE}')
    """).fetchone()
    print(f"\n{counts[0]} OA locations for {counts[1]} works ({n_needed - counts[1]} works have no "
          f"OA location at all), {counts[2]} with a pdf_url")
    print(f"  {counts[3]} locations marked is_oa=true, {counts[0] - counts[3]} marked is_oa=false "
          f"(kept as candidates too - see module docstring)")
    print(f"  {counts[4]} locations are source.type='repository'")

    multi = con.execute(f"""
        SELECT COUNT(*) FROM (
            SELECT oax_id FROM read_parquet('{OUTPUT_FILE}') GROUP BY oax_id HAVING COUNT(*) > 1
        )
    """).fetchone()[0]
    print(f"{multi} works have more than one OA location (fallback candidates available)")
    print(f"Written to {OUTPUT_FILE}")


if __name__ == "__main__":
    main()
