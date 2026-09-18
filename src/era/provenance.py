"""Build a single provenance table chaining the whole ERA -> OpenAlex ->
PDF -> GROBID pipeline together: ERA record -> OpenAlex work -> PDF URL ->
downloaded? -> TEI extracted?

era_id and openalex_id ("oax_id") are a many-to-one relationship (several
ERA records - e.g. the same paper reported by multiple universities, see
the duplicate-DOI analysis earlier in this project - can point at the same
OpenAlex work), so this is a normalized bridge table with one row per
(era_id, openalex_id) pair rather than folding era_id into tei_papers.parquet
(which is one row per openalex_id/PDF, deduplicated). Join on era_id back to
era_research_outputs.parquet, or on openalex_id forward to
tei_papers/tei_references/tei_mentions.parquet.
"""

import sys
from pathlib import Path

import duckdb

sys.path.insert(0, str(Path(__file__).parent.parent))
from config import PDF_DIR, TEI_DIR

DATA_DIR = Path(__file__).parent.parent / "data"
OA_MATCHES = DATA_DIR / "era_openalex_matches_with_oa.parquet"
OUTPUT_FILE = DATA_DIR / "pipeline_provenance.parquet"


def main():
    con = duckdb.connect()

    downloaded_ids = {int(p.stem) for p in PDF_DIR.glob("*.pdf")}
    tei_ids = {int(p.stem.replace(".tei", "")) for p in TEI_DIR.glob("*.tei.xml")}
    print(f"{len(downloaded_ids)} PDFs downloaded, {len(tei_ids)} TEI files extracted")

    con.execute("CREATE TEMP TABLE downloaded (openalex_id BIGINT)")
    con.executemany("INSERT INTO downloaded VALUES (?)", [(i,) for i in downloaded_ids])
    con.execute("CREATE TEMP TABLE extracted (openalex_id BIGINT)")
    con.executemany("INSERT INTO extracted VALUES (?)", [(i,) for i in tei_ids])

    con.execute(f"""
        COPY (
            SELECT
                m.id AS era_id,
                m.openalex_id AS oax_id,
                m.match_type,
                m.source AS oax_match_source,
                m.best_oa_pdf_url AS pdf_url,
                (d.openalex_id IS NOT NULL) AS pdf_downloaded,
                (e.openalex_id IS NOT NULL) AS tei_extracted
            FROM read_parquet('{OA_MATCHES}') m
            LEFT JOIN downloaded d USING (openalex_id)
            LEFT JOIN extracted e USING (openalex_id)
        ) TO '{OUTPUT_FILE}' (FORMAT PARQUET, COMPRESSION SNAPPY)
    """)

    counts = con.execute(f"""
        SELECT
            COUNT(*) AS n_era_records,
            COUNT(DISTINCT oax_id) AS n_distinct_oax_works,
            SUM(pdf_downloaded::INT) AS n_era_records_with_pdf,
            SUM(tei_extracted::INT) AS n_era_records_with_tei
        FROM read_parquet('{OUTPUT_FILE}')
    """).fetchone()
    print(f"\nProvenance table: {counts[0]} ERA records, {counts[1]} distinct OpenAlex works, "
          f"{counts[2]} ERA records covered by a downloaded PDF, "
          f"{counts[3]} covered by a GROBID TEI extraction")
    print(f"Written to {OUTPUT_FILE}")


if __name__ == "__main__":
    main()
