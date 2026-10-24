"""Match Trove: Match canonical ERA records against Trove records and evaluate disparities.

Matches the 455,741 canonical ERA research outputs against Trove National Library of Australia
records:
  1. Tier 1 (ISBN): Direct match on normalized 10- and 13-digit ISBNs for Book and outlet_isbn
     from `data/trove_records/*.parquet`.
  2. Tier 2 (Title Candidate): Match against institutional repository and Trove catalogue title
     searches in `data/trove_title_records/*/*.parquet` via unnested ERA submission IDs (`source_ids`).
     Disambiguates multiple candidates per canonical work prioritizing higher Olensky title similarity,
     category reliability ('research' and 'book'), and Trove rank.

Classifies bibliographic discrepancies using Olensky's (2015) IAC taxonomy:
  - Title disparities: exact, punctuation/case (R), markup/entity (Q),
    cropped subtitle (F), spelling indel (B), discordant (D), typographical (A).
  - ISBN disparities: exact, omission (E), conflict (D).

Outputs:
  - data/era_trove_canonical_matches.parquet (all 455,741 canonical works with match status)
  - data/era_trove_disparities.parquet (matched subset with Olensky disparity evaluations)
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
_NON_ALPHANUM_RE = re.compile(r"[^0-9xX]")

CANONICAL_PQ = DATA_DIR / "era_research_outputs_canonical.parquet"
TROVE_ISBN_GLOB = DATA_DIR / "trove_records" / "*.parquet"
TROVE_TITLE_GLOB = DATA_DIR / "trove_title_records" / "*" / "*.parquet"
TROVE_CANONICAL_MATCHES_PQ = DATA_DIR / "era_trove_canonical_matches.parquet"
TROVE_DISPARITIES_PQ = DATA_DIR / "era_trove_disparities.parquet"


def normalize_isbn(isbn: str | None) -> str | None:
    """Normalize ISBN-10 or ISBN-13 by removing dashes and whitespace."""
    if not isbn or not str(isbn).strip():
        return None
    cleaned = _NON_ALPHANUM_RE.sub("", str(isbn).strip()).upper()
    return cleaned if len(cleaned) in (10, 13) else None


def normalize_title(title: str | None) -> str:
    """Standardize title for comparison: unescape HTML, strip punctuation, lowercase."""
    if not title:
        return ""
    u = html.unescape(str(title))
    p = _PUNCT_RE.sub(" ", u)
    return _SPACE_RE.sub(" ", p).strip().lower()


def classify_title_disparity(t_era: str | None, t_trove: str | None) -> tuple[list[str], str]:
    """Classify the discrepancy between ERA title and Trove title using Olensky's taxonomy."""
    if not t_era or not t_trove:
        return ["E"], "omitted"

    if t_era == t_trove:
        return [], "exact"

    u_era = html.unescape(t_era)
    u_trove = html.unescape(t_trove)
    if (u_era != t_era or u_trove != t_trove) and (u_era == u_trove or u_era.strip().lower() == u_trove.strip().lower()):
        return ["Q"], "markup_or_entity"

    c_era = normalize_title(u_era)
    c_trove = normalize_title(u_trove)
    if c_era == c_trove:
        return ["R"], "punctuation_or_case"

    if detect_subtitle_truncation(t_era, t_trove):
        return ["F"], "cropped_subtitle"

    if single_char_indel_code(c_era, c_trove) is not None:
        return ["B"], "spelling_indel"

    tok1 = set(c_era.split())
    tok2 = set(c_trove.split())
    if tok1 and tok2:
        jaccard = len(tok1.intersection(tok2)) / len(tok1.union(tok2))
        if jaccard < 0.25:
            return ["D"], "discordant"

    return ["A"], "typographical_variant"


def classify_isbn_disparity(i_era: str | None, i_trove: str | None) -> tuple[list[str], str]:
    """Classify discrepancies between ERA ISBN and Trove ISBN."""
    norm_era = normalize_isbn(i_era)
    norm_trove = normalize_isbn(i_trove)

    if norm_era and not norm_trove:
        return ["E"], "omitted_in_trove"
    if not norm_era and norm_trove:
        return ["E"], "omitted_in_era"
    if not norm_era and not norm_trove:
        return ["E"], "both_omitted"

    if norm_era == norm_trove:
        return [], "exact"
    return ["D"], "conflict"


def match_canonical_to_trove(
    canonical_pq: Path = CANONICAL_PQ,
    trove_isbn_glob: Path = TROVE_ISBN_GLOB,
    trove_title_glob: Path = TROVE_TITLE_GLOB,
    output_matches_pq: Path = TROVE_CANONICAL_MATCHES_PQ,
    output_disparities_pq: Path = TROVE_DISPARITIES_PQ,
) -> dict[str, Any]:
    """Execute two-tier matching of canonical ERA records against Trove records."""
    t0 = time.time()
    con = duckdb.connect()

    print(f"Loading canonical ERA records from {canonical_pq}...")
    con.execute(f"""
        CREATE OR REPLACE TABLE canonical_era AS
        SELECT
            canonical_id,
            source_ids,
            institutions,
            n_submissions,
            is_multi_hep,
            research_output_type,
            title AS era_title,
            doi AS era_doi,
            isbn AS era_isbn,
            outlet_isbn AS era_outlet_isbn,
            coalesce(
                regexp_replace(regexp_replace(isbn, '[- ]', '', 'g'), '[^0-9xX]', '', 'g'),
                regexp_replace(regexp_replace(outlet_isbn, '[- ]', '', 'g'), '[^0-9xX]', '', 'g')
            ) AS era_isbn_norm,
            reference_year AS era_year,
            outlet AS era_outlet,
            publisher AS era_publisher
        FROM read_parquet('{canonical_pq}')
    """)
    n_canonical = con.execute("SELECT count(*) FROM canonical_era").fetchone()[0]
    print(f"Loaded {n_canonical:,} canonical ERA works.")

    # 1. Tier 1: ISBN Matching (from data/trove_records/*.parquet)
    print(f"Scanning Trove ISBN records from {trove_isbn_glob}...")
    try:
        con.execute(f"""
            CREATE OR REPLACE TABLE trove_isbn_candidates AS
            SELECT
                isbn_normalized,
                trove_work_id,
                trove_url,
                title AS trove_title,
                status,
                'isbn' AS match_tier
            FROM read_parquet('{trove_isbn_glob}')
            WHERE status IN ('ok', 'edition_unresolved') AND trove_work_id IS NOT NULL
            QUALIFY row_number() OVER (
                PARTITION BY isbn_normalized 
                ORDER BY CASE WHEN status = 'ok' THEN 1 ELSE 2 END
            ) = 1
        """)
        n_isbn_pool = con.execute("SELECT count(*) FROM trove_isbn_candidates").fetchone()[0]
        print(f"Loaded {n_isbn_pool:,} distinct Trove ISBN works.")
    except Exception as e:
        print(f"Warning: Could not load Trove ISBN glob ({e}). Creating empty table.")
        con.execute("""
            CREATE OR REPLACE TABLE trove_isbn_candidates (
                isbn_normalized VARCHAR,
                trove_work_id VARCHAR,
                trove_url VARCHAR,
                trove_title VARCHAR,
                status VARCHAR,
                match_tier VARCHAR
            )
        """)

    con.execute("""
        CREATE OR REPLACE TABLE matches_tier1 AS
        SELECT
            c.canonical_id,
            'isbn' AS match_tier,
            t.trove_work_id,
            t.trove_url,
            t.trove_title,
            'book' AS trove_category,
            0 AS trove_rank,
            NULL AS trove_metadata_source,
            t.isbn_normalized AS trove_isbn
        FROM canonical_era c
        JOIN trove_isbn_candidates t ON c.era_isbn_norm = t.isbn_normalized
        WHERE c.era_isbn_norm IS NOT NULL
        QUALIFY row_number() OVER (PARTITION BY c.canonical_id ORDER BY c.canonical_id) = 1
    """)
    n_t1 = con.execute("SELECT count(*) FROM matches_tier1").fetchone()[0]
    print(f"Tier 1 (ISBN) matches: {n_t1:,}")

    # 2. Tier 2: Title Candidate Matching (from data/trove_title_records/*/*.parquet)
    print(f"Scanning Trove Title search records from {trove_title_glob}...")
    try:
        con.execute(f"""
            CREATE OR REPLACE TABLE trove_title_candidates AS
            SELECT
                era_id,
                trove_work_id,
                trove_url,
                work_title AS trove_title,
                category AS trove_category,
                rank AS trove_rank,
                metadata_source AS trove_metadata_source
            FROM read_parquet('{trove_title_glob}', union_by_name=True)
            WHERE status = 'ok' AND trove_work_id IS NOT NULL
        """)
        n_title_cands = con.execute("SELECT count(*) FROM trove_title_candidates").fetchone()[0]
        print(f"Loaded {n_title_cands:,} candidate title search rows.")
    except Exception as e:
        print(f"Warning: Could not load Trove Title glob ({e}). Creating empty table.")
        con.execute("""
            CREATE OR REPLACE TABLE trove_title_candidates (
                era_id BIGINT,
                trove_work_id VARCHAR,
                trove_url VARCHAR,
                trove_title VARCHAR,
                trove_category VARCHAR,
                trove_rank BIGINT,
                trove_metadata_source VARCHAR
            )
        """)

    con.execute("""
        CREATE OR REPLACE TABLE unnested_era AS
        SELECT canonical_id, era_title, unnest(source_ids) AS era_id
        FROM canonical_era
        WHERE canonical_id NOT IN (SELECT canonical_id FROM matches_tier1)
    """)

    # Select the top candidate per canonical_id prioritizing category (research/book) and rank
    con.execute("""
        CREATE OR REPLACE TABLE matches_tier2 AS
        WITH joined_cands AS (
            SELECT
                u.canonical_id,
                u.era_title,
                t.trove_work_id,
                t.trove_url,
                t.trove_title,
                t.trove_category,
                t.trove_rank,
                t.trove_metadata_source,
                -- Scoring logic: prefer exact/near title matches, research/book category, and lower rank
                (
                    CASE 
                        WHEN lower(trim(u.era_title)) = lower(trim(t.trove_title)) THEN 100
                        ELSE 50
                    END +
                    CASE 
                        WHEN t.trove_category IN ('research', 'book') THEN 20
                        ELSE 0
                    END -
                    coalesce(t.trove_rank, 0) * 5
                ) AS cand_score
            FROM unnested_era u
            JOIN trove_title_candidates t ON u.era_id = t.era_id
        )
        SELECT
            canonical_id,
            'title_candidate' AS match_tier,
            trove_work_id,
            trove_url,
            trove_title,
            trove_category,
            trove_rank,
            trove_metadata_source,
            NULL AS trove_isbn
        FROM joined_cands
        QUALIFY row_number() OVER (
            PARTITION BY canonical_id 
            ORDER BY cand_score DESC, trove_rank ASC
        ) = 1
    """)
    n_t2 = con.execute("SELECT count(*) FROM matches_tier2").fetchone()[0]
    print(f"Tier 2 (Title candidate) matches: {n_t2:,}")

    # Combine Tier 1 and Tier 2
    con.execute("""
        CREATE OR REPLACE TABLE all_trove_matches AS
        SELECT * FROM matches_tier1
        UNION ALL
        SELECT * FROM matches_tier2
    """)
    n_matched = con.execute("SELECT count(*) FROM all_trove_matches").fetchone()[0]

    # Full Canonical Trove Matches Table
    con.execute("""
        CREATE OR REPLACE TABLE final_matches AS
        SELECT
            c.canonical_id,
            c.source_ids,
            c.institutions,
            c.n_submissions,
            c.is_multi_hep,
            c.research_output_type,
            c.era_title,
            c.era_doi,
            coalesce(c.era_isbn, c.era_outlet_isbn) AS era_isbn,
            c.era_year,
            c.era_outlet,
            c.era_publisher,
            (m.trove_work_id IS NOT NULL) AS trove_matched,
            m.match_tier,
            m.trove_work_id,
            m.trove_url,
            m.trove_title,
            m.trove_category,
            m.trove_rank,
            m.trove_metadata_source,
            m.trove_isbn
        FROM canonical_era c
        LEFT JOIN all_trove_matches m ON c.canonical_id = m.canonical_id
        ORDER BY c.canonical_id
    """)

    print(f"Writing matches to {output_matches_pq}...")
    output_matches_pq.parent.mkdir(parents=True, exist_ok=True)
    con.execute(f"COPY final_matches TO '{output_matches_pq}' (FORMAT PARQUET, COMPRESSION 'ZSTD')")

    # Evaluate Disparities for Matched Records
    print("Evaluating Olensky disparities on matched records...")
    matched_records = con.execute("""
        SELECT
            canonical_id,
            research_output_type,
            match_tier,
            era_title,
            trove_title,
            era_isbn,
            trove_isbn,
            trove_work_id,
            trove_category,
            trove_metadata_source
        FROM final_matches
        WHERE trove_matched = true
    """).fetchall()

    disp_rows = []
    for (
        cid,
        rtype,
        tier,
        t_era,
        t_trove,
        i_era,
        i_trove,
        work_id,
        category,
        meta_source,
    ) in matched_records:
        t_codes, t_label = classify_title_disparity(t_era, t_trove)
        i_codes, i_label = classify_isbn_disparity(i_era, i_trove)
        disp_rows.append({
            "canonical_id": cid,
            "research_output_type": rtype,
            "match_tier": tier,
            "era_title": t_era,
            "trove_title": t_trove,
            "title_disparity_label": t_label,
            "title_olensky_codes": t_codes,
            "era_isbn": i_era,
            "trove_isbn": i_trove,
            "isbn_disparity_label": i_label,
            "isbn_olensky_codes": i_codes,
            "trove_work_id": work_id,
            "trove_category": category,
            "trove_metadata_source": meta_source,
        })

    print(f"Writing disparities to {output_disparities_pq}...")
    output_disparities_pq.parent.mkdir(parents=True, exist_ok=True)
    table_disp = pa.Table.from_pylist(disp_rows)
    pq.write_table(table_disp, output_disparities_pq, compression="zstd")

    elapsed = time.time() - t0
    stats = {
        "canonical_records": n_canonical,
        "matched_records": n_matched,
        "match_rate": round(n_matched / n_canonical * 100, 2) if n_canonical else 0,
        "tier1_isbn_matches": n_t1,
        "tier2_title_matches": n_t2,
        "elapsed_sec": round(elapsed, 2),
    }
    print(f"Trove matching completed in {elapsed:.1f}s:")
    print(f"  Total canonical: {n_canonical:,}")
    print(f"  Matched: {n_matched:,} ({stats['match_rate']}%)")
    print(f"  - Tier 1 (ISBN): {n_t1:,}")
    print(f"  - Tier 2 (Title Candidate): {n_t2:,}")
    return stats


def main():
    match_canonical_to_trove()


if __name__ == "__main__":
    main()
