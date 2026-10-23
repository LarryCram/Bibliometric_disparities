"""Match Scopus: Match canonical ERA records against Scopus records and evaluate disparities.

Matches the 455,741 canonical ERA research outputs against Scopus snapshot shards
in data/scopus_records/*.parquet using:
  1. Tier 1 (DOI): Match normalized canonical ERA DOI with Scopus doi_normalized.
     Where multiple Scopus items share a book/proceedings DOI, selects the chapter
     whose title best matches the ERA title.
  2. Tier 2 (Title): Fallback for unmatched/no-DOI records with length >= 15 chars,
     disambiguated by unique normalized title.

Classifies bibliographic discrepancies using Olensky's (2015) IAC taxonomy:
  - Title disparities: exact, punctuation/case (R), markup/entity (Q),
    cropped subtitle (F), spelling indel (B), discordant (D), typographical (A).
  - DOI disparities: exact, omission (E), conflict (D).

Outputs:
  - data/era_scopus_canonical_matches.parquet (all 455,741 canonical works with match status)
  - data/era_scopus_disparities.parquet (matched subset with Olensky disparity evaluations)
"""

import html
from pathlib import Path
import re
import time
from typing import Any

import duckdb
import pyarrow as pa
import pyarrow.parquet as pq

from era.config import DATA_DIR
from era.core.olensky import single_char_indel_code
from era.sources.openalex.preprocess import detect_subtitle_truncation

_PUNCT_RE = re.compile(r"[^a-zA-Z0-9\s]")
_SPACE_RE = re.compile(r"\s+")
_DOI_PREFIX_RE = re.compile(r"^(https?://(dx\.)?doi\.org/|doi:\s*)", re.IGNORECASE)

CANONICAL_PQ = DATA_DIR / "era_research_outputs_canonical.parquet"
SCOPUS_GLOB = DATA_DIR / "scopus_records" / "*.parquet"
SCOPUS_CANONICAL_MATCHES_PQ = DATA_DIR / "era_scopus_canonical_matches.parquet"
SCOPUS_DISPARITIES_PQ = DATA_DIR / "era_scopus_disparities.parquet"


def normalize_doi(doi: str | None) -> str | None:
    if not doi or not str(doi).strip():
        return None
    d = str(doi).strip().lower()
    return _DOI_PREFIX_RE.sub("", d).strip()


def normalize_title(title: str | None) -> str:
    if not title:
        return ""
    u = html.unescape(str(title))
    p = _PUNCT_RE.sub(" ", u)
    return _SPACE_RE.sub(" ", p).strip().lower()


def classify_title_disparity(t_era: str | None, t_scopus: str | None) -> tuple[list[str], str]:
    """Classify the discrepancy between ERA title and Scopus title using Olensky's taxonomy."""
    if not t_era or not t_scopus:
        return ["E"], "omitted"

    if t_era == t_scopus:
        return [], "exact"

    # HTML entity / unescape check
    u_era = html.unescape(t_era)
    u_scopus = html.unescape(t_scopus)
    if (u_era != t_era or u_scopus != t_scopus) and (u_era == u_scopus or u_era.strip().lower() == u_scopus.strip().lower()):
        return ["Q"], "markup_or_entity"

    # Normalized comparison (punctuation and space differences)
    c_era = normalize_title(u_era)
    c_scopus = normalize_title(u_scopus)
    if c_era == c_scopus:
        return ["R"], "punctuation_or_case"

    # Subtitle truncation / cropping check (where one title contains an extra subtitle)
    if detect_subtitle_truncation(t_era, t_scopus):
        return ["F"], "cropped_subtitle"

    # Single-character indel (misspelling / dropped char)
    if single_char_indel_code(c_era, c_scopus) is not None:
        return ["B"], "spelling_indel"

    # Token overlap / Jaccard similarity for discordance
    tok1 = set(c_era.split())
    tok2 = set(c_scopus.split())
    if tok1 and tok2:
        jaccard = len(tok1.intersection(tok2)) / len(tok1.union(tok2))
        if jaccard < 0.25:
            return ["D"], "discordant"

    return ["A"], "typographical_variant"


def match_canonical_to_scopus(
    canonical_pq: Path = CANONICAL_PQ,
    scopus_glob: Path = SCOPUS_GLOB,
    output_matches_pq: Path = SCOPUS_CANONICAL_MATCHES_PQ,
    output_disparities_pq: Path = SCOPUS_DISPARITIES_PQ,
) -> dict[str, Any]:
    """Run tiered matching of canonical ERA records against Scopus."""
    t0 = time.time()
    con = duckdb.connect()

    print(f"Loading canonical ERA records from {canonical_pq}...")
    con.execute(f"""
        CREATE TEMP TABLE era_canonical AS
        SELECT 
            canonical_id,
            source_ids,
            institutions,
            n_submissions,
            is_multi_hep,
            research_output_type,
            title as era_title,
            doi as era_doi,
            doi_wellformed,
            reference_year as era_year,
            outlet as era_outlet,
            publisher as era_publisher,
            CASE 
                WHEN doi IS NOT NULL AND doi_wellformed = true AND trim(doi) != ''
                THEN lower(trim(regexp_replace(regexp_replace(doi, '^https?://(dx\\.)?doi\\.org/', ''), '^doi:\\s*', '')))
                ELSE NULL 
            END as era_doi_normalized,
            trim(regexp_replace(regexp_replace(lower(title), '[^a-z0-9\\s]', ' ', 'g'), '\\s+', ' ', 'g')) as era_title_normalized
        FROM read_parquet('{canonical_pq}')
    """)
    n_canonical = con.execute("SELECT count(*) FROM era_canonical").fetchone()[0]
    print(f"Loaded {n_canonical:,} canonical ERA records ({time.time() - t0:.2f}s).")

    print(f"Loading and deduplicating Scopus records from {scopus_glob}...")
    con.execute(f"""
        CREATE TEMP TABLE scopus_all AS
        SELECT 
            lower(trim(doi_normalized)) as scopus_doi_normalized,
            title as scopus_title,
            eid as scopus_eid,
            n_authors as scopus_n_authors,
            author_names as scopus_author_names,
            citedby_count as scopus_citedby_count,
            openaccess_flag as scopus_openaccess_flag,
            affiliation_name as scopus_affiliations,
            affiliation_country as scopus_affiliation_countries,
            trim(regexp_replace(regexp_replace(lower(title), '[^a-z0-9\\s]', ' ', 'g'), '\\s+', ' ', 'g')) as scopus_title_normalized
        FROM read_parquet('{scopus_glob}')
        WHERE status = 'ok'
    """)
    n_scopus = con.execute("SELECT count(*) FROM scopus_all").fetchone()[0]
    print(f"Loaded {n_scopus:,} valid Scopus entries ({time.time() - t0:.2f}s).")

    # Tier 1: DOI Match (handling 1-to-many book DOIs via title agreement)
    print("Executing Tier 1: DOI matching...")
    con.execute("""
        CREATE TEMP TABLE tier1_matches AS
        WITH candidates AS (
            SELECT 
                e.canonical_id,
                'doi' as match_tier,
                s.*,
                CASE 
                    WHEN e.era_title_normalized = s.scopus_title_normalized THEN 1
                    WHEN s.scopus_title_normalized IS NOT NULL AND length(s.scopus_title_normalized) > 0 
                         AND (e.era_title_normalized LIKE '%' || s.scopus_title_normalized || '%' 
                              OR s.scopus_title_normalized LIKE '%' || e.era_title_normalized || '%') THEN 2
                    ELSE 3
                END as title_rank
            FROM era_canonical e
            JOIN scopus_all s ON e.era_doi_normalized = s.scopus_doi_normalized
            WHERE e.era_doi_normalized IS NOT NULL
        )
        SELECT * EXCLUDE (title_rank)
        FROM candidates
        QUALIFY ROW_NUMBER() OVER (PARTITION BY canonical_id ORDER BY title_rank, scopus_eid) = 1
    """)
    n_tier1 = con.execute("SELECT count(*) FROM tier1_matches").fetchone()[0]
    print(f"Tier 1 DOI matches: {n_tier1:,} works ({time.time() - t0:.2f}s).")

    # Tier 2: Title Fallback Match (for unmatched records, length >= 15, unique in Scopus)
    print("Executing Tier 2: Title fallback matching...")
    con.execute("""
        CREATE TEMP TABLE scopus_unique_titles AS
        SELECT 
            scopus_title_normalized,
            min(scopus_eid) as scopus_eid,
            first(scopus_doi_normalized) as scopus_doi_normalized,
            first(scopus_title) as scopus_title,
            first(scopus_n_authors) as scopus_n_authors,
            first(scopus_author_names) as scopus_author_names,
            first(scopus_citedby_count) as scopus_citedby_count,
            first(scopus_openaccess_flag) as scopus_openaccess_flag,
            first(scopus_affiliations) as scopus_affiliations,
            first(scopus_affiliation_countries) as scopus_affiliation_countries
        FROM scopus_all
        WHERE scopus_title_normalized IS NOT NULL AND length(scopus_title_normalized) >= 15
        GROUP BY scopus_title_normalized
        HAVING count(DISTINCT scopus_eid) = 1
    """)

    con.execute("""
        CREATE TEMP TABLE tier2_matches AS
        SELECT 
            e.canonical_id,
            'title' as match_tier,
            s.scopus_doi_normalized,
            s.scopus_title,
            s.scopus_eid,
            s.scopus_n_authors,
            s.scopus_author_names,
            s.scopus_citedby_count,
            s.scopus_openaccess_flag,
            s.scopus_affiliations,
            s.scopus_affiliation_countries,
            s.scopus_title_normalized
        FROM era_canonical e
        JOIN scopus_unique_titles s ON e.era_title_normalized = s.scopus_title_normalized
        WHERE e.canonical_id NOT IN (SELECT canonical_id FROM tier1_matches)
          AND e.era_title_normalized IS NOT NULL 
          AND length(e.era_title_normalized) >= 15
    """)
    n_tier2 = con.execute("SELECT count(*) FROM tier2_matches").fetchone()[0]
    print(f"Tier 2 Title matches: {n_tier2:,} works ({time.time() - t0:.2f}s).")

    # Combine Matches
    con.execute("""
        CREATE TEMP TABLE all_matches AS
        SELECT * FROM tier1_matches
        UNION ALL
        SELECT * FROM tier2_matches
    """)
    n_total_matched = con.execute("SELECT count(*) FROM all_matches").fetchone()[0]
    print(f"Total Matched: {n_total_matched:,} / {n_canonical:,} ({100.0 * n_total_matched / n_canonical:.1f}%).")

    # Build canonical matches dataset (all canonical works, with match flags)
    print(f"Exporting canonical match table to {output_matches_pq}...")
    con.execute(f"""
        COPY (
            SELECT 
                e.canonical_id,
                e.source_ids,
                e.institutions,
                e.n_submissions,
                e.is_multi_hep,
                e.research_output_type,
                e.era_title,
                e.era_doi,
                e.era_year,
                e.era_outlet,
                e.era_publisher,
                (m.canonical_id IS NOT NULL) as scopus_matched,
                m.match_tier,
                m.scopus_eid,
                m.scopus_doi_normalized as scopus_doi,
                m.scopus_title,
                m.scopus_n_authors,
                m.scopus_author_names,
                m.scopus_citedby_count,
                m.scopus_openaccess_flag,
                m.scopus_affiliations,
                m.scopus_affiliation_countries
            FROM era_canonical e
            LEFT JOIN all_matches m USING (canonical_id)
            ORDER BY e.canonical_id
        ) TO '{output_matches_pq}' (FORMAT PARQUET, COMPRESSION SNAPPY)
    """)

    # Classify Disparities for Matched Records
    print(f"Evaluating Olensky disparities on matched records...")
    rows = con.execute(f"""
        SELECT 
            canonical_id,
            research_output_type,
            match_tier,
            era_title,
            scopus_title,
            era_doi,
            scopus_doi,
            scopus_citedby_count,
            scopus_n_authors
        FROM read_parquet('{output_matches_pq}')
        WHERE scopus_matched = true
    """).fetchall()
    cols = [d[0] for d in con.description]

    disparities = []
    t_labels_count = {}
    doi_labels_count = {}

    for r in rows:
        row_dict = dict(zip(cols, r))
        t_era = row_dict["era_title"]
        t_scp = row_dict["scopus_title"]
        d_era = normalize_doi(row_dict["era_doi"])
        d_scp = normalize_doi(row_dict["scopus_doi"])

        t_codes, t_label = classify_title_disparity(t_era, t_scp)
        t_labels_count[t_label] = t_labels_count.get(t_label, 0) + 1

        if not d_era and not d_scp:
            doi_codes, doi_label = [], "both_missing"
        elif not d_era or not d_scp:
            doi_codes, doi_label = ["E"], "omitted"
        elif d_era == d_scp:
            doi_codes, doi_label = [], "exact"
        else:
            doi_codes, doi_label = ["D"], "discordant"
        doi_labels_count[doi_label] = doi_labels_count.get(doi_label, 0) + 1

        disparities.append({
            "canonical_id": row_dict["canonical_id"],
            "research_output_type": row_dict["research_output_type"],
            "match_tier": row_dict["match_tier"],
            "era_title": t_era,
            "scopus_title": t_scp,
            "title_disparity_label": t_label,
            "title_olensky_codes": t_codes,
            "era_doi": d_era,
            "scopus_doi": d_scp,
            "doi_disparity_label": doi_label,
            "doi_olensky_codes": doi_codes,
            "scopus_citedby_count": row_dict["scopus_citedby_count"],
            "scopus_n_authors": row_dict["scopus_n_authors"],
        })

    print(f"Writing disparities dataset to {output_disparities_pq}...")
    table = pa.Table.from_pylist(disparities)
    pq.write_table(table, output_disparities_pq, compression="snappy")
    print(f"Successfully generated {output_disparities_pq} ({len(disparities):,} records) in {time.time() - t0:.2f}s!")

    stats = {
        "canonical_records": n_canonical,
        "matched_records": n_total_matched,
        "match_rate": round(100.0 * n_total_matched / n_canonical, 2),
        "tier1_doi_matches": n_tier1,
        "tier2_title_matches": n_tier2,
        "title_disparity_breakdown": t_labels_count,
        "doi_disparity_breakdown": doi_labels_count,
    }
    return stats


def main():
    match_canonical_to_scopus()


if __name__ == "__main__":
    main()
