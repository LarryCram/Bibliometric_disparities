"""Comparative Olensky Inaccuracy Profiling (Stage 3).

Synthesizes bibliographic discrepancy classifications across OpenAlex, Scopus,
and Trove using Olensky's (2015) Inaccuracy Classification (IAC) taxonomy.
"""

from pathlib import Path
import time
from typing import Any
import duckdb

from era.config import DATA_DIR, PROJECT_ROOT

OA_DISPARITIES_PARQUET = DATA_DIR / "era_openalex_disparities.parquet"
SCOPUS_DISPARITIES_PARQUET = DATA_DIR / "era_scopus_disparities.parquet"
TROVE_DISPARITIES_PARQUET = DATA_DIR / "era_trove_disparities.parquet"

TABLES_DIR = PROJECT_ROOT / "report" / "output" / "tables"


def calculate_comparative_olensky_taxonomy(
    con: duckdb.DuckDBPyConnection | None = None,
    export_csv: bool = True,
) -> list[dict[str, Any]]:
    """Profile Olensky title inaccuracy categories across OpenAlex, Scopus, and Trove."""
    close_con = False
    if con is None:
        con = duckdb.connect()
        close_con = True

    t0 = time.time()
    print("Profiling comparative Olensky inaccuracy distributions...")

    categories = [
        "exact",
        "punctuation_or_case",
        "typographical_variant",
        "spelling_indel",
        "cropped_subtitle",
        "markup_or_entity",
        "discordant",
        "omitted",
    ]

    q = f"""
        WITH oa AS (
            SELECT 
                'OpenAlex' AS source,
                COUNT(*) AS total_matched,
                SUM((title_disparity_label = 'exact')::INT) AS n_exact,
                SUM((title_disparity_label = 'punctuation_or_case')::INT) AS n_punctuation,
                SUM((title_disparity_label = 'typographical_variant')::INT) AS n_typo,
                SUM((title_disparity_label = 'spelling_indel')::INT) AS n_indel,
                SUM((title_disparity_label = 'cropped_subtitle')::INT) AS n_cropped,
                SUM((title_disparity_label = 'markup_or_entity')::INT) AS n_markup,
                SUM((title_disparity_label = 'discordant')::INT) AS n_discordant,
                SUM((title_disparity_label = 'omitted')::INT) AS n_omitted
            FROM read_parquet('{OA_DISPARITIES_PARQUET}')
        ),
        scopus AS (
            SELECT 
                'Scopus' AS source,
                COUNT(*) AS total_matched,
                SUM((title_disparity_label = 'exact')::INT) AS n_exact,
                SUM((title_disparity_label = 'punctuation_or_case')::INT) AS n_punctuation,
                SUM((title_disparity_label = 'typographical_variant')::INT) AS n_typo,
                SUM((title_disparity_label = 'spelling_indel')::INT) AS n_indel,
                SUM((title_disparity_label = 'cropped_subtitle')::INT) AS n_cropped,
                SUM((title_disparity_label = 'markup_or_entity')::INT) AS n_markup,
                SUM((title_disparity_label = 'discordant')::INT) AS n_discordant,
                SUM((title_disparity_label = 'omitted')::INT) AS n_omitted
            FROM read_parquet('{SCOPUS_DISPARITIES_PARQUET}')
        ),
        trove AS (
            SELECT 
                'Trove' AS source,
                COUNT(*) AS total_matched,
                SUM((title_disparity_label = 'exact')::INT) AS n_exact,
                SUM((title_disparity_label = 'punctuation_or_case')::INT) AS n_punctuation,
                SUM((title_disparity_label = 'typographical_variant')::INT) AS n_typo,
                SUM((title_disparity_label = 'spelling_indel')::INT) AS n_indel,
                SUM((title_disparity_label = 'cropped_subtitle')::INT) AS n_cropped,
                SUM((title_disparity_label = 'markup_or_entity')::INT) AS n_markup,
                SUM((title_disparity_label = 'discordant')::INT) AS n_discordant,
                SUM((title_disparity_label = 'omitted')::INT) AS n_omitted
            FROM read_parquet('{TROVE_DISPARITIES_PARQUET}')
        ),
        unioned AS (
            SELECT * FROM oa
            UNION ALL
            SELECT * FROM scopus
            UNION ALL
            SELECT * FROM trove
        )
        SELECT 
            source,
            total_matched,
            n_exact, ROUND(n_exact * 100.0 / total_matched, 2) AS pct_exact,
            n_punctuation, ROUND(n_punctuation * 100.0 / total_matched, 2) AS pct_punctuation,
            n_typo, ROUND(n_typo * 100.0 / total_matched, 2) AS pct_typo,
            n_indel, ROUND(n_indel * 100.0 / total_matched, 2) AS pct_indel,
            n_cropped, ROUND(n_cropped * 100.0 / total_matched, 2) AS pct_cropped,
            n_markup, ROUND(n_markup * 100.0 / total_matched, 2) AS pct_markup,
            n_discordant, ROUND(n_discordant * 100.0 / total_matched, 2) AS pct_discordant,
            n_omitted, ROUND(n_omitted * 100.0 / total_matched, 2) AS pct_omitted,
            -- Olensky IAC Type aggregations:
            -- Type 1 (Surface/Format): Exact, Punctuation/Case, Markup
            ROUND((n_exact + n_punctuation + n_markup) * 100.0 / total_matched, 2) AS pct_type1_surface,
            -- Type 2 (Textual Variation): Typographical, Spelling Indel, Cropped Subtitle
            ROUND((n_typo + n_indel + n_cropped) * 100.0 / total_matched, 2) AS pct_type2_textual,
            -- Type 3 (Severe Inaccuracy): Discordant, Omitted
            ROUND((n_discordant + n_omitted) * 100.0 / total_matched, 2) AS pct_type3_discordant
        FROM unioned
    """

    rows = con.execute(q).fetchall()
    cols = [
        "source", "total_matched",
        "n_exact", "pct_exact",
        "n_punctuation", "pct_punctuation",
        "n_typo", "pct_typo",
        "n_indel", "pct_indel",
        "n_cropped", "pct_cropped",
        "n_markup", "pct_markup",
        "n_discordant", "pct_discordant",
        "n_omitted", "pct_omitted",
        "pct_type1_surface", "pct_type2_textual", "pct_type3_discordant",
    ]
    results = [dict(zip(cols, r)) for r in rows]

    if export_csv:
        TABLES_DIR.mkdir(parents=True, exist_ok=True)
        csv_path = TABLES_DIR / "table_comparative_olensky.csv"
        con.execute(f"COPY ({q}) TO '{csv_path}' (HEADER, DELIMITER ',')")
        print(f"Exported comparative Olensky table to {csv_path} ({time.time()-t0:.2f}s)")

    if close_con:
        con.close()
    return results


def calculate_olensky_by_output_type(
    con: duckdb.DuckDBPyConnection | None = None,
    export_csv: bool = True,
) -> list[dict[str, Any]]:
    """Compute Olensky inaccuracy distribution broken down by ERA output type for each index."""
    close_con = False
    if con is None:
        con = duckdb.connect()
        close_con = True

    t0 = time.time()
    q = f"""
        WITH combined AS (
            SELECT 
                'OpenAlex' AS source,
                COALESCE(research_output_type, 'Unknown') AS output_type,
                title_disparity_label
            FROM read_parquet('{OA_DISPARITIES_PARQUET}')
            UNION ALL
            SELECT 
                'Scopus' AS source,
                COALESCE(research_output_type, 'Unknown') AS output_type,
                title_disparity_label
            FROM read_parquet('{SCOPUS_DISPARITIES_PARQUET}')
            UNION ALL
            SELECT 
                'Trove' AS source,
                COALESCE(research_output_type, 'Unknown') AS output_type,
                title_disparity_label
            FROM read_parquet('{TROVE_DISPARITIES_PARQUET}')
        )
        SELECT 
            source,
            output_type,
            COUNT(*) AS total_matched,
            SUM((title_disparity_label = 'exact')::INT) AS n_exact,
            ROUND(SUM((title_disparity_label = 'exact')::INT) * 100.0 / COUNT(*), 1) AS pct_exact,
            SUM((title_disparity_label = 'punctuation_or_case')::INT) AS n_punctuation,
            ROUND(SUM((title_disparity_label = 'punctuation_or_case')::INT) * 100.0 / COUNT(*), 1) AS pct_punctuation,
            SUM((title_disparity_label IN ('typographical_variant', 'spelling_indel', 'cropped_subtitle'))::INT) AS n_type2_textual,
            ROUND(SUM((title_disparity_label IN ('typographical_variant', 'spelling_indel', 'cropped_subtitle'))::INT) * 100.0 / COUNT(*), 1) AS pct_type2_textual,
            SUM((title_disparity_label IN ('discordant', 'omitted'))::INT) AS n_discordant,
            ROUND(SUM((title_disparity_label IN ('discordant', 'omitted'))::INT) * 100.0 / COUNT(*), 1) AS pct_discordant
        FROM combined
        GROUP BY 1, 2
        HAVING COUNT(*) >= 50
        ORDER BY source, total_matched DESC
    """

    rows = con.execute(q).fetchall()
    cols = [
        "source", "output_type", "total_matched",
        "n_exact", "pct_exact",
        "n_punctuation", "pct_punctuation",
        "n_type2_textual", "pct_type2_textual",
        "n_discordant", "pct_discordant",
    ]
    results = [dict(zip(cols, r)) for r in rows]

    if export_csv:
        TABLES_DIR.mkdir(parents=True, exist_ok=True)
        csv_path = TABLES_DIR / "table_olensky_by_output_type.csv"
        con.execute(f"COPY ({q}) TO '{csv_path}' (HEADER, DELIMITER ',')")
        print(f"Exported Olensky by output type table to {csv_path} ({time.time()-t0:.2f}s)")

    if close_con:
        con.close()
    return results
