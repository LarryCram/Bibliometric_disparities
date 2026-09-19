"""OpenAlex Preprocessing & Data Quality Analysis.

Profiles OpenAlex records in isolation and tracks publication lifecycle stages:
  1. Intra-OpenAlex Olensky inaccuracies (whitespace K, casing R, markup Q, cropped F, indels B).
  2. Multi-version lifecycle fragmentation (submittedVersion/preprint -> acceptedVersion -> publishedVersion).
  3. Divergence in DOIs, host/source platforms, and publication years across versions.
  4. Authorship drift across lifecycle stages (author count, reordering Code O, affiliation changes).
  5. Temporal provenance audit: retrospective ORCID back-propagation onto pre-October 16, 2012 works.
"""

import html
import re
from pathlib import Path
from typing import Any

import duckdb

from era.config import DATA_DIR
from era.core.string_processor import StringProcessor
from era.core.olensky import single_char_indel_code

_SUBTITLE_DELIMITERS = [": ", " - ", " -- ", " — ", " / "]
_TRUNCATION_ELLIPSIS_RE = re.compile(r"\s*\.\.+$")
_WHITESPACE_RUN_RE = re.compile(r"\s{2,}")


_HTML_ENTITY_RE = re.compile(r"&[a-zA-Z0-9#]+;")
_TEX_MATH_RE = re.compile(r"\$.*?\$|\\[a-zA-Z]+")


def classify_openalex_title(title: str | None) -> dict[str, Any]:
    """Classify and clean a raw OpenAlex title string using Olensky IAC categories."""
    if not title or not title.strip():
        return {
            "cleaned_title": None,
            "codes": ["E"],
            "has_html": False,
            "has_tex": False,
            "is_cropped": False,
        }

    raw = title.strip()
    codes: list[str] = []

    # 1. Check for HTML entities or tags and TeX formulas
    sp = StringProcessor().process(raw)
    has_html_tag = "has_html" in sp.codes
    has_html_entity = bool(_HTML_ENTITY_RE.search(raw))
    has_html = has_html_tag or has_html_entity

    has_tex = "has_tex" in sp.codes or bool(_TEX_MATH_RE.search(raw))

    cleaned = raw
    # Unescape HTML entities
    unescaped = html.unescape(cleaned)
    if unescaped != cleaned or has_html or has_tex:
        if "Q" not in codes:
            codes.append("Q")
        cleaned = unescaped

    # 2. Check for fixed-length truncation / ellipsis
    is_cropped = bool(_TRUNCATION_ELLIPSIS_RE.search(cleaned))
    if is_cropped:
        if "F" not in codes:
            codes.append("F")
        cleaned = _TRUNCATION_ELLIPSIS_RE.sub("", cleaned).strip()

    # 3. Check for whitespace runs
    if _WHITESPACE_RUN_RE.search(cleaned):
        if "K" not in codes:
            codes.append("K")
        cleaned = _WHITESPACE_RUN_RE.sub(" ", cleaned).strip()

    return {
        "cleaned_title": cleaned,
        "codes": codes,
        "has_html": has_html,
        "has_tex": has_tex,
        "is_cropped": is_cropped,
    }


def detect_subtitle_truncation(title1: str | None, title2: str | None) -> bool:
    """Return True if one title matches the other truncated at a subtitle delimiter.
    
    Example: "Machine Learning: Principles and Practice" vs "Machine Learning"
    """
    if not title1 or not title2:
        return False

    t1, t2 = title1.strip().lower(), title2.strip().lower()
    if t1 == t2:
        return False

    shorter, longer = (t1, t2) if len(t1) < len(t2) else (t2, t1)

    for delim in _SUBTITLE_DELIMITERS:
        if longer.startswith(shorter + delim.rstrip()):
            return True
        # Also check if split by delim yields the shorter form
        parts = longer.split(delim.strip())
        if parts and parts[0].strip() == shorter:
            return True

    return False


def is_pre_orcid_publication(year: int | None) -> bool:
    """Return True if the publication year predates ORCID's official launch (Oct 16, 2012).
    
    Works with publication_year <= 2011 were published before ORCID existed;
    any ORCID present on such a work represents retrospective back-propagation.
    """
    if year is None:
        return False
    return year <= 2011


def evaluate_lifecycle_version_drift(locations: list[dict[str, Any]]) -> dict[str, Any]:
    """Evaluate version and host/source platform divergence across locations of a work."""
    if not locations:
        return {
            "has_multiple_versions": False,
            "has_host_divergence": False,
            "versions": [],
            "distinct_sources": [],
            "has_submitted": False,
            "has_published": False,
            "has_accepted": False,
        }

    versions = sorted(list({loc.get("version") for loc in locations if loc.get("version")}))
    sources = sorted(list({loc.get("source_name") for loc in locations if loc.get("source_name")}))

    has_submitted = "submittedVersion" in versions
    has_published = "publishedVersion" in versions
    has_accepted = "acceptedVersion" in versions

    return {
        "has_multiple_versions": len(versions) > 1,
        "has_host_divergence": len(sources) > 1,
        "versions": versions,
        "distinct_sources": sources,
        "has_submitted": has_submitted,
        "has_published": has_published,
        "has_accepted": has_accepted,
    }


def evaluate_authorship_drift(
    auth1: list[dict[str, Any]], auth2: list[dict[str, Any]]
) -> dict[str, Any]:
    """Compare two authorship lists (e.g. across lifecycle stages or versions).
    
    Detects author count divergence, author reordering (Olensky Code O),
    and affiliation drift.
    """
    count_diff = abs(len(auth1) - len(auth2))

    # Shared authors by ID
    ids1 = [a.get("author_id") for a in auth1 if a.get("author_id")]
    ids2 = [a.get("author_id") for a in auth2 if a.get("author_id")]

    common_ids = set(ids1).intersection(set(ids2))
    has_reordering = False
    if len(common_ids) >= 2:
        # Sequence of shared authors in list 1 vs list 2
        seq1 = [i for i in ids1 if i in common_ids]
        seq2 = [i for i in ids2 if i in common_ids]
        if seq1 != seq2:
            has_reordering = True

    # Check affiliation drift
    affils1 = {
        aff for a in auth1 for aff in (a.get("raw_affiliation_strings") or [])
    }
    affils2 = {
        aff for a in auth2 for aff in (a.get("raw_affiliation_strings") or [])
    }
    has_affiliation_drift = bool(affils1 and affils2 and affils1 != affils2)

    return {
        "author_count_diff": count_diff,
        "has_reordering": has_reordering,
        "has_affiliation_drift": has_affiliation_drift,
    }


# ==============================================================================
# Pipeline & Dataset Audit Functions
# ==============================================================================
def audit_orcid_backpropagation(con: duckdb.DuckDBPyConnection) -> list[tuple]:
    """Audit ORCID iD incidence by publication year across the OpenAlex authorships."""
    authorships_pq = DATA_DIR / "oa_authorships.parquet"
    work_stats_pq = DATA_DIR / "oa_work_stats.parquet"

    q = f"""
    SELECT 
        s.publication_year,
        count(*) as total_authorships,
        count(a.author_orcid) as authorships_with_orcid,
        round(100.0 * count(a.author_orcid) / count(*), 1) as orcid_pct,
        count(DISTINCT s.oax_id) as total_works,
        count(DISTINCT CASE WHEN a.author_orcid IS NOT NULL THEN s.oax_id END) as works_with_orcid,
        round(100.0 * count(DISTINCT CASE WHEN a.author_orcid IS NOT NULL THEN s.oax_id END) / count(DISTINCT s.oax_id), 1) as work_orcid_pct
    FROM read_parquet('{authorships_pq}') a
    JOIN read_parquet('{work_stats_pq}') s USING (oax_id)
    WHERE s.publication_year BETWEEN 2009 AND 2016
    GROUP BY 1
    ORDER BY 1
    """
    return con.execute(q).fetchall()


def analyze_oa_locations_lifecycle(con: duckdb.DuckDBPyConnection) -> dict[str, Any]:
    """Analyze OpenAlex locations for multi-version lifecycle presence."""
    locations_pq = DATA_DIR / "oa_locations.parquet"

    # Distribution of versions
    v_rows = con.execute(f"""
        SELECT COALESCE(version, 'unspecified') as ver, count(*) as loc_count
        FROM read_parquet('{locations_pq}')
        GROUP BY 1
        ORDER BY loc_count DESC
    """).fetchall()

    # Works with multiple versions
    multi_v = con.execute(f"""
        WITH work_vers AS (
            SELECT oax_id,
                   count(DISTINCT COALESCE(version, 'unspecified')) as n_versions,
                   count(DISTINCT source_name) as n_sources,
                   count(CASE WHEN version = 'submittedVersion' THEN 1 END) as has_submitted,
                   count(CASE WHEN version = 'publishedVersion' THEN 1 END) as has_published,
                   count(CASE WHEN version = 'acceptedVersion' THEN 1 END) as has_accepted
            FROM read_parquet('{locations_pq}')
            GROUP BY 1
        )
        SELECT 
            count(*) as total_works,
            count(CASE WHEN n_versions > 1 THEN 1 END) as multi_version_works,
            count(CASE WHEN has_submitted > 0 AND has_published > 0 THEN 1 END) as preprint_and_published,
            count(CASE WHEN n_sources > 1 THEN 1 END) as multi_host_works
        FROM work_vers
    """).fetchone()

    return {
        "version_distribution": v_rows,
        "total_works": multi_v[0],
        "multi_version_works": multi_v[1],
        "preprint_and_published": multi_v[2],
        "multi_host_works": multi_v[3],
    }


def main():
    con = duckdb.connect()
    print("=== OpenAlex Preprocessing & Quality Audit ===")

    print("\n1. Auditing Temporal ORCID Provenance (Pre-October 16, 2012 launch):")
    orcid_stats = audit_orcid_backpropagation(con)
    print("   Year | Authorships | ORCIDs | % Auth ORCID | Works | Works w/ ORCID | % Work ORCID")
    print("   " + "-" * 75)
    for r in orcid_stats:
        pre_flag = " (PRE-LAUNCH!)" if r[0] <= 2011 else ""
        print(f"   {r[0]} | {r[1]:>11,} | {r[2]:>6,} | {r[3]:>11.1f}% | {r[4]:>5,} | {r[5]:>14,} | {r[6]:>11.1f}%{pre_flag}")

    print("\n2. Analyzing Work Lifecycle & Multi-Version Locations:")
    lifecycle_stats = analyze_oa_locations_lifecycle(con)
    for ver, cnt in lifecycle_stats["version_distribution"]:
        print(f"   - {ver:<18}: {cnt:>10,} locations")
    print(f"\n   Works evaluated            : {lifecycle_stats['total_works']:,}")
    print(f"   Works with multi-versions  : {lifecycle_stats['multi_version_works']:,} ({lifecycle_stats['multi_version_works']*100.0/lifecycle_stats['total_works']:.1f}%)")
    print(f"   Preprint + Published pairs : {lifecycle_stats['preprint_and_published']:,} ({lifecycle_stats['preprint_and_published']*100.0/lifecycle_stats['total_works']:.1f}%)")
    print(f"   Works with multiple hosts  : {lifecycle_stats['multi_host_works']:,} ({lifecycle_stats['multi_host_works']*100.0/lifecycle_stats['total_works']:.1f}%)")


if __name__ == "__main__":
    main()
