"""Multi-Index Synthesis & Coverage Analysis (Stage 3).

Synthesizes canonical ERA outputs against OpenAlex (Oct26), Scopus (post title harvest),
and Trove to evaluate cross-index coverage, intersections, and discipline gradients.
"""

from pathlib import Path
import time
from typing import Any
import duckdb

from era.config import DATA_DIR, PROJECT_ROOT

CANONICAL_PARQUET = DATA_DIR / "era_research_outputs_canonical.parquet"
OA_PAIRED_PARQUET = DATA_DIR / "era_openalex_paired_records.parquet"
OA_MATCHES_PARQUET = DATA_DIR / "era_openalex_matches.parquet"
OA_XPAC_PARQUET = DATA_DIR / "era_openalex_xpac_matches.parquet"
SCOPUS_CANONICAL_PARQUET = DATA_DIR / "era_scopus_canonical_matches.parquet"
TROVE_CANONICAL_PARQUET = DATA_DIR / "era_trove_canonical_matches.parquet"

OA_CANONICAL_MATCHES_PARQUET = DATA_DIR / "era_openalex_canonical_matches.parquet"
SYNTHESIS_PARQUET = DATA_DIR / "era_multi_index_synthesis.parquet"
TABLES_DIR = PROJECT_ROOT / "report" / "output" / "tables"


def build_openalex_canonical_matches(
    con: duckdb.DuckDBPyConnection | None = None,
    output_pq: Path = OA_CANONICAL_MATCHES_PARQUET,
) -> int:
    """Build canonical-level OpenAlex match table (455,741 records)."""
    close_con = False
    if con is None:
        con = duckdb.connect()
        close_con = True

    t0 = time.time()
    print(f"Building canonical OpenAlex matches -> {output_pq}...")

    con.execute(f"""
        COPY (
            WITH all_matches AS (
                SELECT id, openalex_id, match_type, 1 AS src_rank 
                FROM read_parquet('{OA_MATCHES_PARQUET}') 
                WHERE openalex_id IS NOT NULL
                UNION ALL
                SELECT id, openalex_id, match_type, 2 AS src_rank 
                FROM read_parquet('{OA_XPAC_PARQUET}') 
                WHERE openalex_id IS NOT NULL
            ),
            canon_unnested AS (
                SELECT c.canonical_id, UNNEST(c.source_ids) AS sid
                FROM read_parquet('{CANONICAL_PARQUET}') c
            ),
            ranked_matches AS (
                SELECT 
                    u.canonical_id,
                    m.openalex_id,
                    m.match_type,
                    ROW_NUMBER() OVER (
                        PARTITION BY u.canonical_id 
                        ORDER BY 
                            CASE WHEN m.match_type = 'doi' THEN 1 WHEN m.match_type = 'title' THEN 2 ELSE 3 END,
                            m.src_rank ASC,
                            m.openalex_id ASC
                    ) AS rn
                FROM canon_unnested u
                JOIN all_matches m ON u.sid = m.id
            ),
            best_matches AS (
                SELECT canonical_id, openalex_id, match_type AS match_tier
                FROM ranked_matches
                WHERE rn = 1
            ),
            paired_dedup AS (
                SELECT 
                    openalex_id,
                    MIN(oax_title) AS oax_title,
                    MIN(oax_doi) AS oax_doi,
                    MIN(oax_year) AS oax_year,
                    MIN(oax_type) AS oax_type,
                    MAX(oax_cited_by_count) AS oax_cited_by_count
                FROM read_parquet('{OA_PAIRED_PARQUET}')
                GROUP BY openalex_id
            )
            SELECT 
                c.canonical_id,
                c.source_ids,
                c.institutions,
                c.n_submissions,
                c.is_multi_hep,
                c.research_output_type,
                c.title AS era_title,
                c.doi AS era_doi,
                c.reference_year AS era_year,
                (b.openalex_id IS NOT NULL) AS openalex_matched,
                b.match_tier,
                b.openalex_id,
                p.oax_title,
                p.oax_doi,
                p.oax_year,
                p.oax_type,
                p.oax_cited_by_count
            FROM read_parquet('{CANONICAL_PARQUET}') c
            LEFT JOIN best_matches b ON c.canonical_id = b.canonical_id
            LEFT JOIN paired_dedup p ON b.openalex_id = p.openalex_id
        ) TO '{output_pq}' (FORMAT PARQUET, COMPRESSION SNAPPY)
    """)

    n_matched = con.execute(f"SELECT SUM(openalex_matched::INT) FROM read_parquet('{output_pq}')").fetchone()[0]
    print(f"Generated {output_pq.name} in {time.time()-t0:.2f}s ({n_matched:,} matched canonical works)")

    if close_con:
        con.close()
    return n_matched


def build_multi_index_synthesis(
    con: duckdb.DuckDBPyConnection | None = None,
    output_pq: Path = SYNTHESIS_PARQUET,
) -> int:
    """Build unified multi-index synthesis table linking ERA, OpenAlex, Scopus, and Trove."""
    close_con = False
    if con is None:
        con = duckdb.connect()
        close_con = True

    if not OA_CANONICAL_MATCHES_PARQUET.exists():
        build_openalex_canonical_matches(con)

    t0 = time.time()
    print(f"Building multi-index synthesis table -> {output_pq}...")

    con.execute(f"""
        COPY (
            SELECT 
                c.canonical_id,
                c.research_output_type,
                c.era_title,
                c.era_doi,
                c.era_year,
                c.n_submissions,
                c.is_multi_hep,
                c.institutions,
                -- OpenAlex linkage
                COALESCE(oa.openalex_matched, FALSE) AS in_openalex,
                oa.match_tier AS oa_match_tier,
                oa.openalex_id,
                oa.oax_title,
                oa.oax_doi,
                oa.oax_year,
                oa.oax_type,
                oa.oax_cited_by_count,
                -- Scopus linkage
                COALESCE(sc.scopus_matched, FALSE) AS in_scopus,
                sc.match_tier AS scopus_match_tier,
                sc.scopus_eid,
                sc.scopus_title,
                sc.scopus_doi,
                sc.scopus_n_authors,
                sc.scopus_citedby_count,
                -- Trove linkage
                COALESCE(tr.trove_matched, FALSE) AS in_trove,
                tr.match_tier AS trove_match_tier,
                tr.trove_work_id,
                tr.trove_title,
                tr.trove_category,
                -- Multi-index synthesis flags
                (COALESCE(oa.openalex_matched, FALSE)::INT + 
                 COALESCE(sc.scopus_matched, FALSE)::INT + 
                 COALESCE(tr.trove_matched, FALSE)::INT) AS index_count,
                (COALESCE(oa.openalex_matched, FALSE) OR 
                 COALESCE(sc.scopus_matched, FALSE) OR 
                 COALESCE(tr.trove_matched, FALSE)) AS in_any_index,
                CASE 
                    WHEN oa.openalex_matched AND sc.scopus_matched AND tr.trove_matched THEN 'All Three'
                    WHEN oa.openalex_matched AND sc.scopus_matched AND NOT tr.trove_matched THEN 'OpenAlex & Scopus'
                    WHEN oa.openalex_matched AND NOT sc.scopus_matched AND tr.trove_matched THEN 'OpenAlex & Trove'
                    WHEN NOT oa.openalex_matched AND sc.scopus_matched AND tr.trove_matched THEN 'Scopus & Trove'
                    WHEN oa.openalex_matched AND NOT sc.scopus_matched AND NOT tr.trove_matched THEN 'OpenAlex Only'
                    WHEN NOT oa.openalex_matched AND sc.scopus_matched AND NOT tr.trove_matched THEN 'Scopus Only'
                    WHEN NOT oa.openalex_matched AND NOT sc.scopus_matched AND tr.trove_matched THEN 'Trove Only'
                    ELSE 'ERA Only'
                END AS coverage_category
            FROM read_parquet('{OA_CANONICAL_MATCHES_PARQUET}') c
            LEFT JOIN read_parquet('{OA_CANONICAL_MATCHES_PARQUET}') oa USING (canonical_id)
            LEFT JOIN read_parquet('{SCOPUS_CANONICAL_PARQUET}') sc USING (canonical_id)
            LEFT JOIN read_parquet('{TROVE_CANONICAL_PARQUET}') tr USING (canonical_id)
        ) TO '{output_pq}' (FORMAT PARQUET, COMPRESSION SNAPPY)
    """)

    n_rows = con.execute(f"SELECT COUNT(*) FROM read_parquet('{output_pq}')").fetchone()[0]
    print(f"Generated {output_pq.name} in {time.time()-t0:.2f}s ({n_rows:,} canonical records)")

    if close_con:
        con.close()
    return n_rows


def calculate_output_type_coverage(
    con: duckdb.DuckDBPyConnection | None = None,
    synthesis_pq: Path = SYNTHESIS_PARQUET,
    export_csv: bool = True,
) -> list[dict[str, Any]]:
    """Calculate coverage rates across all 4 sources grouped by ERA research output type."""
    close_con = False
    if con is None:
        con = duckdb.connect()
        close_con = True

    if not synthesis_pq.exists():
        build_multi_index_synthesis(con, synthesis_pq)

    q = f"""
        SELECT 
            COALESCE(research_output_type, 'Unknown') AS output_type,
            COUNT(*) AS total_canonical,
            SUM(in_openalex::INT) AS n_openalex,
            ROUND(SUM(in_openalex::INT) * 100.0 / COUNT(*), 1) AS pct_openalex,
            SUM(in_scopus::INT) AS n_scopus,
            ROUND(SUM(in_scopus::INT) * 100.0 / COUNT(*), 1) AS pct_scopus,
            SUM(in_trove::INT) AS n_trove,
            ROUND(SUM(in_trove::INT) * 100.0 / COUNT(*), 1) AS pct_trove,
            SUM(in_any_index::INT) AS n_union,
            ROUND(SUM(in_any_index::INT) * 100.0 / COUNT(*), 1) AS pct_union,
            SUM((coverage_category = 'ERA Only')::INT) AS n_era_only,
            ROUND(SUM((coverage_category = 'ERA Only')::INT) * 100.0 / COUNT(*), 1) AS pct_era_only
        FROM read_parquet('{synthesis_pq}')
        GROUP BY 1
        ORDER BY total_canonical DESC
    """
    rows = con.execute(q).fetchall()
    cols = [
        "output_type", "total_canonical", "n_openalex", "pct_openalex",
        "n_scopus", "pct_scopus", "n_trove", "pct_trove",
        "n_union", "pct_union", "n_era_only", "pct_era_only"
    ]
    results = [dict(zip(cols, r)) for r in rows]

    if export_csv:
        TABLES_DIR.mkdir(parents=True, exist_ok=True)
        csv_path = TABLES_DIR / "table_multi_index_coverage.csv"
        con.execute(f"COPY ({q}) TO '{csv_path}' (HEADER, DELIMITER ',')")
        print(f"Exported coverage table to {csv_path}")

    if close_con:
        con.close()
    return results


def calculate_intersection_metrics(
    con: duckdb.DuckDBPyConnection | None = None,
    synthesis_pq: Path = SYNTHESIS_PARQUET,
    export_csv: bool = True,
) -> list[dict[str, Any]]:
    """Compute UpSet / Venn intersection metrics across ERA, OpenAlex, Scopus, and Trove."""
    close_con = False
    if con is None:
        con = duckdb.connect()
        close_con = True

    if not synthesis_pq.exists():
        build_multi_index_synthesis(con, synthesis_pq)

    q = f"""
        SELECT 
            coverage_category,
            COUNT(*) AS work_count,
            ROUND(COUNT(*) * 100.0 / (SELECT COUNT(*) FROM read_parquet('{synthesis_pq}')), 2) AS pct_total,
            SUM((research_output_type = 'Journal Article')::INT) AS n_journal_articles,
            SUM((research_output_type = 'Conference Publication')::INT) AS n_conferences,
            SUM((research_output_type IN ('Book', 'Book Chapter'))::INT) AS n_books_chapters,
            SUM((research_output_type NOT IN ('Journal Article', 'Conference Publication', 'Book', 'Book Chapter'))::INT) AS n_ntro
        FROM read_parquet('{synthesis_pq}')
        GROUP BY 1
        ORDER BY work_count DESC
    """
    rows = con.execute(q).fetchall()
    cols = ["coverage_category", "work_count", "pct_total", "n_journal_articles", "n_conferences", "n_books_chapters", "n_ntro"]
    results = [dict(zip(cols, r)) for r in rows]

    if export_csv:
        TABLES_DIR.mkdir(parents=True, exist_ok=True)
        csv_path = TABLES_DIR / "table_cross_index_intersections.csv"
        con.execute(f"COPY ({q}) TO '{csv_path}' (HEADER, DELIMITER ',')")
        print(f"Exported intersection metrics to {csv_path}")

    if close_con:
        con.close()
    return results
