"""GROBID Reference Disparities and Citation Importance Analysis.

Evaluates reference list citations between ERA works extracted from GROBID TEI full text.
Keeps GROBID references and in-text mentions strictly separate in dedicated datasets.
Quantifies bibliographic inaccuracies (Olensky 2015 IAC taxonomy) as a function of:
  1. Citation Mention Frequency (Passing vs Moderate vs Substantive vs Foundational)
  2. In-text Section Placement (Methods/Data, Results, Discussion, Introduction)
  3. Cited Research Output Type (Journal, Book Chapter, Conference, Book)
"""

import html
import re
import time
from pathlib import Path
from typing import Any

import duckdb
import pyarrow as pa
import pyarrow.parquet as pq

from era.config import DATA_DIR, PROJECT_ROOT
from era.matching.evaluate_disparities import (
    classify_title_disparity,
    classify_year_disparity,
    normalize_doi,
)

GROBID_REFS_PARQUET = DATA_DIR / "grobid_references.parquet"
GROBID_MENTIONS_PARQUET = DATA_DIR / "grobid_mentions.parquet"
GROBID_PAPERS_PARQUET = DATA_DIR / "grobid_papers.parquet"
ERA_CANONICAL_PARQUET = DATA_DIR / "era_research_outputs_canonical.parquet"
ERA_SYNTHESIS_PARQUET = DATA_DIR / "era_multi_index_synthesis.parquet"

OUTPUT_DISPARITIES_PARQUET = DATA_DIR / "era_grobid_reference_disparities.parquet"
TABLES_DIR = PROJECT_ROOT / "report" / "output" / "tables"


def build_grobid_reference_disparities(
    con: duckdb.DuckDBPyConnection | None = None,
    output_parquet: Path | None = None,
    export_tables: bool = True,
) -> Path:
    """Build the linked reference disparities dataset and compute Olensky classifications."""
    close_con = False
    if con is None:
        con = duckdb.connect()
        close_con = True

    if output_parquet is None:
        output_parquet = OUTPUT_DISPARITIES_PARQUET

    t0 = time.time()
    print("Building GROBID reference disparities dataset...")

    # Step 1: Aggregate mentions by (citing_oax_id, bibl_id)
    print("  Aggregating in-text citation mentions and section locations...")
    con.execute(f"""
        CREATE OR REPLACE TEMP TABLE mention_agg AS
        SELECT 
            citing_oax_id,
            bibl_id,
            count(*) as mention_count,
            bool_or(lower(section) LIKE '%method%' OR lower(section) LIKE '%data%' OR lower(section) LIKE '%material%' OR lower(section) LIKE '%procedur%') as in_methods,
            bool_or(lower(section) LIKE '%intro%' OR lower(section) LIKE '%background%' OR lower(section) LIKE '%literatur%' OR lower(section) LIKE '%related work%') as in_intro,
            bool_or(lower(section) LIKE '%discuss%' OR lower(section) LIKE '%conclus%' OR lower(section) LIKE '%implication%') as in_discuss,
            bool_or(lower(section) LIKE '%result%' OR lower(section) LIKE '%finding%') as in_results,
            mode(COALESCE(section, 'UNSPECIFIED')) as primary_section
        FROM '{GROBID_MENTIONS_PARQUET}'
        GROUP BY citing_oax_id, bibl_id
    """)

    # Step 2: Extract normalized references and link with ERA canonical outputs
    print("  Matching GROBID references against ERA canonical outputs...")
    query_matches = f"""
        WITH norm_refs AS (
            SELECT 
                r.citing_oax_id,
                r.bibl_id,
                r.ref_title,
                r.ref_authors,
                TRY_CAST(regexp_extract(r.ref_year, '[0-9]{4}') AS INT) as ref_year_num,
                r.ref_journal,
                r.ref_doi,
                lower(trim(regexp_replace(r.ref_doi, '^(https?://(dx\\.)?doi\\.org/|doi:\\s*)', '', 'i'))) as norm_ref_doi,
                lower(trim(regexp_replace(r.ref_title, '[^a-zA-Z0-9]', '', 'g'))) as clean_ref_title
            FROM '{GROBID_REFS_PARQUET}' r
        ),
        norm_era AS (
            SELECT 
                canonical_id,
                title as era_title,
                reference_year as era_year,
                research_output_type,
                doi as era_doi,
                lower(trim(regexp_replace(doi, '^(https?://(dx\\.)?doi\\.org/|doi:\\s*)', '', 'i'))) as norm_era_doi,
                lower(trim(regexp_replace(title, '[^a-zA-Z0-9]', '', 'g'))) as clean_era_title
            FROM '{ERA_CANONICAL_PARQUET}'
        ),
        doi_matches AS (
            SELECT 
                r.citing_oax_id,
                r.bibl_id,
                r.ref_title,
                r.ref_authors,
                r.ref_year_num,
                r.ref_journal,
                r.ref_doi,
                e.canonical_id as cited_canonical_id,
                e.era_title as cited_era_title,
                e.era_year as cited_era_year,
                e.era_doi as cited_era_doi,
                e.research_output_type as cited_research_output_type,
                'doi_exact' as match_tier
            FROM norm_refs r
            JOIN norm_era e ON r.norm_ref_doi = e.norm_era_doi
            WHERE r.norm_ref_doi IS NOT NULL AND length(r.norm_ref_doi) > 5
        ),
        unique_era_titles AS (
            SELECT 
                clean_era_title,
                any_value(canonical_id) as canonical_id,
                any_value(era_title) as era_title,
                any_value(era_year) as era_year,
                any_value(era_doi) as era_doi,
                any_value(research_output_type) as research_output_type
            FROM norm_era
            WHERE length(clean_era_title) >= 20
            GROUP BY clean_era_title
            HAVING count(*) = 1
        ),
        title_matches AS (
            SELECT 
                r.citing_oax_id,
                r.bibl_id,
                r.ref_title,
                r.ref_authors,
                r.ref_year_num,
                r.ref_journal,
                r.ref_doi,
                e.canonical_id as cited_canonical_id,
                e.era_title as cited_era_title,
                e.era_year as cited_era_year,
                e.era_doi as cited_era_doi,
                e.research_output_type as cited_research_output_type,
                'title_exact' as match_tier
            FROM norm_refs r
            JOIN unique_era_titles e ON r.clean_ref_title = e.clean_era_title
            LEFT JOIN doi_matches dm ON r.citing_oax_id = dm.citing_oax_id AND r.bibl_id = dm.bibl_id
            WHERE dm.citing_oax_id IS NULL
              AND (r.ref_doi IS NULL OR length(trim(r.ref_doi)) <= 5)
              AND length(r.clean_ref_title) >= 20
              AND (r.ref_year_num IS NULL OR e.era_year IS NULL OR abs(r.ref_year_num - e.era_year) <= 1)
        ),
        all_matches AS (
            SELECT * FROM doi_matches
            UNION ALL
            SELECT * FROM title_matches
        ),
        citing_meta AS (
            SELECT 
                openalex_id,
                canonical_id as citing_canonical_id,
                era_title as citing_era_title,
                era_year as citing_era_year,
                research_output_type as citing_output_type,
                institutions as citing_institutions
            FROM '{ERA_SYNTHESIS_PARQUET}'
            WHERE openalex_id IS NOT NULL
        )
        SELECT 
            m.citing_oax_id,
            cm.citing_canonical_id,
            cm.citing_era_title,
            cm.citing_era_year,
            cm.citing_output_type,
            cm.citing_institutions,
            m.bibl_id,
            m.match_tier,
            m.ref_title,
            m.ref_authors,
            m.ref_year_num,
            m.ref_journal,
            m.ref_doi,
            m.cited_canonical_id,
            m.cited_era_title,
            m.cited_era_year,
            m.cited_era_doi,
            m.cited_research_output_type,
            COALESCE(ma.mention_count, 0) as mention_count,
            COALESCE(ma.in_methods, FALSE) as in_methods,
            COALESCE(ma.in_intro, FALSE) as in_intro,
            COALESCE(ma.in_discuss, FALSE) as in_discuss,
            COALESCE(ma.in_results, FALSE) as in_results,
            COALESCE(ma.primary_section, 'UNCITED_IN_BODY') as primary_section
        FROM all_matches m
        LEFT JOIN mention_agg ma ON m.citing_oax_id = ma.citing_oax_id AND m.bibl_id = ma.bibl_id
        LEFT JOIN citing_meta cm ON m.citing_oax_id = cm.openalex_id
    """

    con.execute(f"CREATE OR REPLACE TEMP TABLE matched_citations AS {query_matches}")
    n_total = con.execute("SELECT count(*) FROM matched_citations").fetchone()[0]
    print(f"  Matched {n_total:,} internal citations across ERA works ({time.time()-t0:.2f}s).")

    # Step 3: Run Olensky Classifications in Python
    print("  Classifying bibliographic discrepancies with Olensky (2015) IAC taxonomy...")
    cols = [desc[0] for desc in con.execute("DESCRIBE matched_citations").fetchall()]
    rows = con.execute("SELECT * FROM matched_citations").fetchall()

    processed: list[dict[str, Any]] = []
    for r in rows:
        rec = dict(zip(cols, r))
        
        # Olensky Title classification
        t_codes, t_label = classify_title_disparity(rec["cited_era_title"], rec["ref_title"])
        rec["title_olensky_codes"] = t_codes
        rec["title_disparity_label"] = t_label
        
        # Olensky IAC Title Type:
        # Type 1: Surface (Exact, Punctuation/Case, Markup)
        # Type 2: Textual (Typo, Spelling Indel, Cropped Subtitle)
        # Type 3: Severe (Discordant, Omitted)
        if t_label in ("exact", "punctuation_or_case", "markup_or_entity"):
            rec["title_olensky_type"] = "Type 1 (Surface/Punctuation)"
        elif t_label in ("typographical_variant", "spelling_indel", "cropped_subtitle"):
            rec["title_olensky_type"] = "Type 2 (Textual Variation)"
        else:
            rec["title_olensky_type"] = "Type 3 (Severe Inaccuracy)"

        # Olensky Year classification
        y_codes, y_diff = classify_year_disparity(rec["cited_era_year"], rec["ref_year_num"])
        rec["year_olensky_codes"] = y_codes
        rec["year_difference"] = y_diff
        rec["has_year_disparity"] = bool(y_diff is not None and y_diff > 0)

        # Mention Importance Tiers
        mc = rec["mention_count"]
        if mc == 0:
            rec["importance_tier"] = "0 (Uncited in body)"
        elif mc == 1:
            rec["importance_tier"] = "1 (Passing citation)"
        elif 2 <= mc <= 3:
            rec["importance_tier"] = "2-3 (Moderate)"
        elif 4 <= mc <= 6:
            rec["importance_tier"] = "4-6 (Substantive)"
        else:
            rec["importance_tier"] = "7+ (Foundational)"

        # Section Placement Classification
        if rec["in_methods"]:
            rec["section_group"] = "Methods/Data/Materials"
        elif rec["in_results"]:
            rec["section_group"] = "Results/Findings"
        elif rec["in_discuss"]:
            rec["section_group"] = "Discussion/Conclusions"
        elif rec["in_intro"]:
            rec["section_group"] = "Introduction/Background"
        else:
            rec["section_group"] = "Other/Uncited in body"

        processed.append(rec)

    # Step 4: Write to dedicated Parquet file
    print(f"  Saving to {output_parquet}...")
    output_parquet.parent.mkdir(parents=True, exist_ok=True)
    table = pa.Table.from_pylist(processed)
    pq.write_table(table, output_parquet, compression="zstd")
    print(f"Saved {len(processed):,} records to {output_parquet} ({time.time()-t0:.2f}s)")

    if export_tables:
        export_summary_tables(con, output_parquet)

    if close_con:
        con.close()

    return output_parquet


def export_summary_tables(con: duckdb.DuckDBPyConnection | None, parquet_path: Path) -> None:
    """Generate analytical summary tables comparing citation importance to bibliographic accuracy."""
    close_con = False
    if con is None:
        con = duckdb.connect()
        close_con = True

    TABLES_DIR.mkdir(parents=True, exist_ok=True)

    # Table 1: Inaccuracy by Mention Frequency (Importance) on DOI-matched citations
    q_importance = f"""
        SELECT 
            importance_tier,
            COUNT(*) AS total_citations,
            ROUND(AVG((title_disparity_label = 'exact')::INT) * 100.0, 2) AS pct_exact_title,
            ROUND(AVG((title_disparity_label = 'punctuation_or_case')::INT) * 100.0, 2) AS pct_punctuation_case,
            ROUND(AVG((title_olensky_type = 'Type 2 (Textual Variation)')::INT) * 100.0, 2) AS pct_type2_textual,
            ROUND(AVG((title_olensky_type = 'Type 3 (Severe Inaccuracy)')::INT) * 100.0, 2) AS pct_type3_discordant,
            ROUND(AVG(has_year_disparity::INT) * 100.0, 2) AS pct_year_disparity,
            ROUND(AVG(mention_count), 2) AS mean_mentions
        FROM read_parquet('{parquet_path}')
        WHERE match_tier = 'doi_exact'
        GROUP BY importance_tier
        ORDER BY importance_tier
    """
    csv_imp = TABLES_DIR / "table_reference_disparities_importance.csv"
    con.execute(f"COPY ({q_importance}) TO '{csv_imp}' (HEADER, DELIMITER ',')")
    print(f"  Exported importance summary to {csv_imp}")

    # Table 2: Inaccuracy by Section Placement on DOI-matched citations
    q_section = f"""
        SELECT 
            section_group,
            COUNT(*) AS total_citations,
            ROUND(AVG((title_disparity_label = 'exact')::INT) * 100.0, 2) AS pct_exact_title,
            ROUND(AVG((title_disparity_label = 'punctuation_or_case')::INT) * 100.0, 2) AS pct_punctuation_case,
            ROUND(AVG((title_olensky_type = 'Type 2 (Textual Variation)')::INT) * 100.0, 2) AS pct_type2_textual,
            ROUND(AVG((title_olensky_type = 'Type 3 (Severe Inaccuracy)')::INT) * 100.0, 2) AS pct_type3_discordant,
            ROUND(AVG(has_year_disparity::INT) * 100.0, 2) AS pct_year_disparity,
            ROUND(AVG(mention_count), 2) AS mean_mentions
        FROM read_parquet('{parquet_path}')
        WHERE match_tier = 'doi_exact'
        GROUP BY section_group
        ORDER BY total_citations DESC
    """
    csv_sec = TABLES_DIR / "table_reference_disparities_sections.csv"
    con.execute(f"COPY ({q_section}) TO '{csv_sec}' (HEADER, DELIMITER ',')")
    print(f"  Exported section placement summary to {csv_sec}")

    # Table 3: Inaccuracy by Cited Research Output Type
    q_type = f"""
        SELECT 
            COALESCE(cited_research_output_type, 'Unknown') AS output_type,
            match_tier,
            COUNT(*) AS total_citations,
            ROUND(AVG((title_olensky_type = 'Type 1 (Surface/Punctuation)')::INT) * 100.0, 2) AS pct_type1_surface,
            ROUND(AVG((title_olensky_type = 'Type 2 (Textual Variation)')::INT) * 100.0, 2) AS pct_type2_textual,
            ROUND(AVG((title_olensky_type = 'Type 3 (Severe Inaccuracy)')::INT) * 100.0, 2) AS pct_type3_discordant,
            ROUND(AVG(has_year_disparity::INT) * 100.0, 2) AS pct_year_disparity
        FROM read_parquet('{parquet_path}')
        GROUP BY 1, 2
        HAVING COUNT(*) >= 50
        ORDER BY match_tier, total_citations DESC
    """
    csv_type = TABLES_DIR / "table_reference_disparities_types.csv"
    con.execute(f"COPY ({q_type}) TO '{csv_type}' (HEADER, DELIMITER ',')")
    print(f"  Exported output type summary to {csv_type}")

    # Table 4: Author Surname Fidelity in References by Importance Tier
    q_auth_fid = f"""
        WITH cited_oa_authors AS (
            SELECT 
                s.canonical_id,
                lower(trim(any_value(CASE WHEN a.author_rank = 1 THEN a.author_display_name END))) as canonical_first_author
            FROM '{ERA_SYNTHESIS_PARQUET}' s
            JOIN '{DATA_DIR / "oa_authorships.parquet"}' a ON s.openalex_id = a.oax_id
            WHERE s.openalex_id IS NOT NULL
            GROUP BY s.canonical_id
        ),
        ref_auth AS (
            SELECT 
                r.cited_canonical_id,
                r.importance_tier,
                lower(trim(split_part(r.ref_authors, ';', 1))) as ref_first_author
            FROM read_parquet('{parquet_path}') r
            WHERE r.ref_authors IS NOT NULL AND length(trim(r.ref_authors)) > 0
              AND r.match_tier = 'doi_exact'
        )
        SELECT 
            ra.importance_tier,
            COUNT(*) AS total_citations,
            ROUND(AVG((split_part(ra.ref_first_author, ' ', -1) = split_part(coa.canonical_first_author, ' ', -1))::INT) * 100.0, 2) AS pct_author_match,
            ROUND(100.0 - AVG((split_part(ra.ref_first_author, ' ', -1) = split_part(coa.canonical_first_author, ' ', -1))::INT) * 100.0, 2) AS pct_author_discrepancy
        FROM ref_auth ra
        JOIN cited_oa_authors coa ON ra.cited_canonical_id = coa.canonical_id
        GROUP BY ra.importance_tier
        ORDER BY ra.importance_tier
    """
    csv_auth = TABLES_DIR / "table_reference_author_fidelity.csv"
    con.execute(f"COPY ({q_auth_fid}) TO '{csv_auth}' (HEADER, DELIMITER ',')")
    print(f"  Exported reference author fidelity to {csv_auth}")

    # Table 5: Author and Institutional Disparities (Olensky Perspective)
    calculate_author_institution_disparities(con)

    if close_con:
        con.close()


def calculate_author_institution_disparities(con: duckdb.DuckDBPyConnection) -> None:
    """Calculate author count fidelity and institutional attribution divergence."""
    # 1. Author count discrepancies
    q_auth_count = f"""
        WITH oa_auth_counts AS (
            SELECT oax_id, count(*) as oa_author_count
            FROM '{DATA_DIR / "oa_authorships.parquet"}'
            GROUP BY oax_id
        ),
        grobid_auth AS (
            SELECT 
                oax_id, 
                CASE 
                    WHEN authors IS NULL OR length(trim(authors)) = 0 THEN 0
                    ELSE length(authors) - length(replace(authors, ';', '')) + 1
                END as grobid_author_count
            FROM '{GROBID_PAPERS_PARQUET}'
        )
        SELECT 
            count(*) as total_papers,
            round(avg((g.grobid_author_count = oa.oa_author_count)::INT) * 100, 2) as pct_exact_count,
            round(avg((g.grobid_author_count < oa.oa_author_count)::INT) * 100, 2) as pct_grobid_fewer,
            round(avg((g.grobid_author_count > oa.oa_author_count)::INT) * 100, 2) as pct_grobid_more
        FROM grobid_auth g
        JOIN oa_auth_counts oa ON g.oax_id = oa.oax_id
    """
    row_auth = con.execute(q_auth_count).fetchone()

    # 2. Institutional attribution divergence
    q_inst = f"""
        WITH era_inst AS (
            SELECT 
                openalex_id,
                canonical_id,
                institutions as era_raw_inst,
                list_transform(institutions, x -> lower(trim(regexp_replace(x, '^the\\s+', '', 'i')))) as era_clean_inst
            FROM '{ERA_SYNTHESIS_PARQUET}'
            WHERE openalex_id IS NOT NULL AND institutions IS NOT NULL
        ),
        oa_inst AS (
            SELECT 
                oax_id,
                list(DISTINCT lower(institution_display_name)) as oa_raw_inst,
                list(DISTINCT lower(trim(regexp_replace(institution_display_name, '^the\\s+', '', 'i')))) as oa_clean_inst
            FROM '{DATA_DIR / "oa_authorship_institutions.parquet"}'
            WHERE institution_display_name IS NOT NULL
            GROUP BY oax_id
        )
        SELECT 
            count(*) as total_evaluated,
            round(avg((list_has_any(oa.oa_raw_inst, list_transform(e.era_raw_inst, x -> lower(x))))::INT) * 100, 2) as pct_raw_match,
            round(avg((list_has_any(oa.oa_clean_inst, e.era_clean_inst))::INT) * 100, 2) as pct_normalized_match,
            round(100.0 - avg((list_has_any(oa.oa_clean_inst, e.era_clean_inst))::INT) * 100, 2) as pct_institutional_divergence
        FROM era_inst e
        JOIN oa_inst oa ON e.openalex_id = oa.oax_id
    """
    row_inst = con.execute(q_inst).fetchone()

    csv_inst = TABLES_DIR / "table_author_institution_disparities.csv"
    with open(csv_inst, "w", encoding="utf-8") as f:
        f.write("metric_category,evaluated_papers,concordant_share,minor_drift_share,divergent_share\n")
        f.write(f"Author Count Concordance (PDF vs OpenAlex),{row_auth[0]},{row_auth[1]},{row_auth[2]},{row_auth[3]}\n")
        f.write(f"Institutional Attribution (ERA Submissions vs OpenAlex),{row_inst[0]},{row_inst[2]},{round(row_inst[2]-row_inst[1], 2)},{row_inst[3]}\n")
    print(f"  Exported author and institutional disparities to {csv_inst}")


if __name__ == "__main__":
    build_grobid_reference_disparities()

