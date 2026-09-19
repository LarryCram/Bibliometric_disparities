"""Match OpenAlex: Hydrate ERA-OpenAlex matches with native OpenAlex metadata.

Joins the 540,353 ERA-OpenAlex matched links with the local OpenAlex works snapshots
(OPENALEX_COMPACT_WORKS and OPENALEX_XPAC_WORKS) to extract:
  - oax_title: OpenAlex native title
  - oax_doi: OpenAlex normalized DOI
  - oax_year: OpenAlex publication year
  - oax_type: OpenAlex work type classification (article, book-chapter, dataset, etc.)
  - oax_cited_by_count: citation counts

Output: data/era_openalex_paired_records.parquet
"""

import time
from pathlib import Path
import duckdb

from era.config import DATA_DIR, OPENALEX_COMPACT_WORKS, OPENALEX_XPAC_WORKS, DUCKDB_TMP

MATCHES_PQ = DATA_DIR / "era_openalex_matches_with_oa.parquet"
ERA_PQ = DATA_DIR / "era_research_outputs.parquet"
OUTPUT_PQ = DATA_DIR / "era_openalex_paired_records.parquet"


def build_paired_records(con: duckdb.DuckDBPyConnection) -> None:
    t0 = time.time()
    DUCKDB_TMP.mkdir(exist_ok=True)
    con.execute(f"PRAGMA temp_directory='{DUCKDB_TMP.as_posix()}'")
    con.execute("PRAGMA memory_limit='24GB'")
    con.execute("PRAGMA preserve_insertion_order=false")

    print(f"Reading ERA matches from {MATCHES_PQ}...")
    con.execute(f"""
        CREATE TEMP TABLE era_base AS
        SELECT m.id, m.openalex_id, m.match_type, m.year_close, m.source as match_source,
               m.oa_url, m.best_oa_pdf_url, m.best_oa_landing_page_url,
               e.research_output_type, e.title as era_title, e.doi as era_doi, e.reference_year as era_year
        FROM read_parquet('{MATCHES_PQ}') m
        JOIN read_parquet('{ERA_PQ}') e USING (id)
    """)
    n_base = con.execute("SELECT count(*) FROM era_base").fetchone()[0]
    print(f"Base table ready: {n_base:,} rows ({time.time() - t0:.2f}s)")

    print(f"Extracting native works from compact export ({OPENALEX_COMPACT_WORKS})...")
    con.execute(f"""
        CREATE TEMP TABLE oax_compact AS
        SELECT o.work_idx as openalex_id, o.title as oax_title, o.doi as oax_doi,
               o.publication_year as oax_year, o.type as oax_type, o.cited_by_count as oax_cited_by_count
        FROM read_parquet('{OPENALEX_COMPACT_WORKS}/*.parquet') o
        WHERE o.work_idx IN (SELECT openalex_id FROM era_base)
    """)
    n_compact = con.execute("SELECT count(*) FROM oax_compact").fetchone()[0]
    print(f"Found {n_compact:,} works in compact export ({time.time() - t0:.2f}s)")

    print(f"Extracting remaining works from xpac export ({OPENALEX_XPAC_WORKS})...")
    con.execute(f"""
        CREATE TEMP TABLE oax_xpac AS
        SELECT o.work_idx as openalex_id, o.title as oax_title, o.doi as oax_doi,
               o.publication_year as oax_year, o.type as oax_type, o.cited_by_count as oax_cited_by_count
        FROM read_parquet('{OPENALEX_XPAC_WORKS}/*.parquet') o
        WHERE o.work_idx IN (SELECT openalex_id FROM era_base)
          AND o.work_idx NOT IN (SELECT openalex_id FROM oax_compact)
    """)
    n_xpac = con.execute("SELECT count(*) FROM oax_xpac").fetchone()[0]
    print(f"Found {n_xpac:,} works in xpac export ({time.time() - t0:.2f}s)")

    print(f"Writing joined paired dataset to {OUTPUT_PQ}...")
    con.execute(f"""
        COPY (
            WITH oax_all AS (
                SELECT * FROM oax_compact
                UNION ALL
                SELECT * FROM oax_xpac
            )
            SELECT b.*, o.oax_title, o.oax_doi, o.oax_year, o.oax_type, o.oax_cited_by_count
            FROM era_base b
            LEFT JOIN oax_all o USING (openalex_id)
        ) TO '{OUTPUT_PQ}' (FORMAT PARQUET, COMPRESSION SNAPPY)
    """)
    n_out = con.execute(f"SELECT count(*) FROM read_parquet('{OUTPUT_PQ}')").fetchone()[0]
    print(f"Successfully created {OUTPUT_PQ} with {n_out:,} paired records in {time.time() - t0:.2f}s!")


def main():
    con = duckdb.connect()
    build_paired_records(con)


if __name__ == "__main__":
    main()
