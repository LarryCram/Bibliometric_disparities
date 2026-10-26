"""Metadata-Only Backward Reference Resolution Disparity Diagnostics.

Identifies erroneous reference resolution targets published BEFORE the citing paper's
publication year (T_target <= T_source), where temporal anachronism filters cannot detect errors.

Implements four subtle, non-fulltext tests:
  1. Monotonic Alphabetical Inversion (MAI):
     Detects bibliography row-shifts, misalignments, and false author-matching
     via alphabetical sequence violations in author-date bibliographies.
  2. Cross-Index Consensus Discordance (CCD):
     Compares resolved reference sets between Scopus and OpenAlex for the same
     canonical paper to isolate conflicting historical targets.
  3. Obscure Historical Outliers (OHO):
     Identifies pre-modern citations (>= 20 years old) with negligible global
     citations (<= 5), which are statistical anomalies in contemporary STEM papers.
  4. Disciplinary / Topological Ghost Orphans (TGO):
     Detects historical references with zero subject/venue cohesion with the citing paper.
"""

from __future__ import annotations

import argparse
import re
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import duckdb

from era.config import DATA_DIR, M_DRIVE, SCOPUS_DIR
from era.sources.scopus.fetch import init_pybliometrics

SYNTHESIS_PARQUET = DATA_DIR / "era_multi_index_synthesis.parquet"
OPENALEX_COMPACT_WORKS = M_DRIVE / "openalex_oct26" / "parquet_converted" / "compact" / "works"


# ==============================================================================
# Data Structures
# ==============================================================================

@dataclass
class ReferenceItem:
    """Normalized reference item representation across indices."""
    position: int
    source_index: str  # 'scopus' or 'openalex'
    title: str | None
    authors: str | None
    first_surname: str | None
    year: int | None
    doi: str | None
    eid: str | None = None
    openalex_id: str | None = None
    cited_by_count: int | None = None
    venue: str | None = None
    raw_text: str | None = None


@dataclass
class InversionAnomaly:
    """Record of an alphabetical sequence inversion."""
    position: int
    prev_surname: str
    curr_surname: str
    prev_pos: int
    letter_distance: int
    is_major: bool
    title: str | None


@dataclass
class CrossIndexComparison:
    """Comparison results between Scopus and OpenAlex reference sets."""
    doi: str
    citing_year: int
    scopus_count: int
    openalex_count: int
    count_delta: int
    consensus_titles: list[str] = field(default_factory=list)
    scopus_only_targets: list[ReferenceItem] = field(default_factory=list)
    openalex_only_targets: list[ReferenceItem] = field(default_factory=list)
    jaccard_similarity: float = 0.0


@dataclass
class PaperDiagnostics:
    """Diagnostic audit for a single paper."""
    doi: str
    title: str
    citing_year: int
    scopus_ref_count: int
    openalex_ref_count: int
    is_alphabetical_style: bool
    inversions: list[InversionAnomaly] = field(default_factory=list)
    cross_index: CrossIndexComparison | None = None
    historical_outliers: list[ReferenceItem] = field(default_factory=list)
    topological_ghost_orphans: list[ReferenceItem] = field(default_factory=list)


# ==============================================================================
# Helper Normalizers
# ==============================================================================

def normalize_title(title: str | None) -> str:
    """Normalize title for fuzzy matching across indices."""
    if not title:
        return ""
    # Strip HTML tags, punctuation, and lowercase
    clean = re.sub(r"<[^>]+>", "", title)
    clean = re.sub(r"[^a-z0-9]", "", clean.lower())
    return clean


def extract_first_surname(authors_str: str | None) -> str | None:
    """Extract normalized first author surname from author string."""
    if not authors_str or not authors_str.strip():
        return None
    # Scopus format: 'Bell, S.; Quiggin, J.' or 'Bell S.'
    first_author = authors_str.split(";")[0].strip()
    if "," in first_author:
        surname = first_author.split(",")[0].strip()
    else:
        parts = first_author.split()
        if len(parts) >= 2 and len(parts[-1].rstrip(".")) <= 2 and len(parts[0]) > 2:
            surname = parts[0]
        else:
            surname = parts[-1] if parts else first_author
    clean = re.sub(r"[^a-zA-Z]", "", surname).upper()
    return clean or None


# ==============================================================================
# Detection Engine 1: Monotonic Alphabetical Inversion (MAI)
# ==============================================================================

def detect_alphabetical_inversions(
    references: list[ReferenceItem],
    min_monotonic_ratio: float = 0.70,
) -> tuple[bool, list[InversionAnomaly]]:
    """Detect whether bibliography is alphabetical and identify sequence inversions.
    
    Returns:
        (is_alphabetical_style, list of InversionAnomaly)
    """
    valid_items = [r for r in references if r.first_surname]
    if len(valid_items) < 5:
        return False, []

    # Check overall monotonicity: how many adjacent transitions are non-decreasing
    non_decreasing_count = 0
    total_transitions = len(valid_items) - 1

    for i in range(total_transitions):
        prev = valid_items[i].first_surname
        curr = valid_items[i + 1].first_surname
        if curr >= prev:
            non_decreasing_count += 1

    monotonic_ratio = non_decreasing_count / total_transitions if total_transitions > 0 else 0.0
    is_alphabetical = monotonic_ratio >= min_monotonic_ratio

    if not is_alphabetical:
        return False, []

    # Find sequence inversions
    inversions: list[InversionAnomaly] = []
    max_surname_seen = valid_items[0].first_surname
    max_pos_seen = valid_items[0].position

    for i in range(1, len(valid_items)):
        curr_item = valid_items[i]
        curr_surname = curr_item.first_surname

        if curr_surname < max_surname_seen:
            # Ordinal letter difference
            first_curr = ord(curr_surname[0])
            first_max = ord(max_surname_seen[0])
            letter_dist = first_max - first_curr
            is_major = letter_dist >= 2  # e.g., 'A' or 'D' appearing after 'M'

            inversions.append(
                InversionAnomaly(
                    position=curr_item.position,
                    prev_surname=max_surname_seen,
                    curr_surname=curr_surname,
                    prev_pos=max_pos_seen,
                    letter_distance=letter_dist,
                    is_major=is_major,
                    title=curr_item.title,
                )
            )
        else:
            max_surname_seen = curr_surname
            max_pos_seen = curr_item.position

    return True, inversions


# ==============================================================================
# Detection Engine 2: Cross-Index Consensus Discordance (CCD)
# ==============================================================================

def compare_scopus_openalex_references(
    doi: str,
    citing_year: int,
    scopus_refs: list[ReferenceItem],
    openalex_refs: list[ReferenceItem],
) -> CrossIndexComparison:
    """Compare resolved reference sets between Scopus and OpenAlex for the same paper."""
    s_titles = {normalize_title(r.title): r for r in scopus_refs if r.title and len(normalize_title(r.title)) > 8}
    o_titles = {normalize_title(r.title): r for r in openalex_refs if r.title and len(normalize_title(r.title)) > 8}

    s_keys = set(s_titles.keys())
    o_keys = set(o_titles.keys())

    common_keys = s_keys & o_keys
    scopus_only_keys = s_keys - o_keys
    openalex_only_keys = o_keys - s_keys

    union_len = len(s_keys | o_keys)
    jaccard = (len(common_keys) / union_len) if union_len > 0 else 1.0

    scopus_only_items = [s_titles[k] for k in scopus_only_keys]
    openalex_only_items = [o_titles[k] for k in openalex_only_keys]

    return CrossIndexComparison(
        doi=doi,
        citing_year=citing_year,
        scopus_count=len(scopus_refs),
        openalex_count=len(openalex_refs),
        count_delta=len(scopus_refs) - len(openalex_refs),
        consensus_titles=[s_titles[k].title or "" for k in common_keys],
        scopus_only_targets=scopus_only_items,
        openalex_only_targets=openalex_only_items,
        jaccard_similarity=jaccard,
    )


# ==============================================================================
# Detection Engine 3: Obscure Historical Outliers (OHO)
# ==============================================================================

def detect_obscure_historical_outliers(
    references: list[ReferenceItem],
    citing_year: int,
    min_age_gap: int = 20,
    max_citations: int = 5,
) -> list[ReferenceItem]:
    """Identify historical citations that are not landmark foundations but have obscure impact."""
    outliers = []
    for r in references:
        if r.year and (citing_year - r.year) >= min_age_gap:
            # If citation count is available and very low, or target has no title
            if r.cited_by_count is not None and r.cited_by_count <= max_citations:
                outliers.append(r)
            elif r.title is None and r.year < 1980:
                # Truncated or empty historical reference
                outliers.append(r)
    return outliers


# ==============================================================================
# Detection Engine 4: Topological Ghost Orphans (TGO)
# ==============================================================================

STOPWORDS = {
    "the", "a", "an", "and", "or", "of", "in", "on", "at", "to", "for", "with",
    "by", "as", "is", "are", "was", "were", "from", "that", "this", "between",
    "into", "through", "after", "before", "using", "study", "analysis", "role",
}


def extract_content_tokens(text: str | None) -> set[str]:
    """Extract informative lowercase tokens of length >= 3."""
    if not text:
        return set()
    words = re.findall(r"[a-zA-Z]{3,}", text.lower())
    return set(words) - STOPWORDS


def detect_topological_ghost_orphans(
    references: list[ReferenceItem],
    citing_year: int,
    min_age_gap: int = 15,
) -> list[ReferenceItem]:
    """Identify historical references with zero lexical/disciplinary cohesion with the reference pool."""
    valid_refs = [r for r in references if r.title and len(r.title) > 5]
    if len(valid_refs) < 5:
        return []

    orphans = []
    for idx, target in enumerate(valid_refs):
        # Target must be historical
        if not target.year or (citing_year - target.year) < min_age_gap:
            continue

        target_tokens = extract_content_tokens(target.title)
        if not target_tokens:
            continue

        # Compute union of tokens across all other references
        corpus_tokens: set[str] = set()
        for j, other in enumerate(valid_refs):
            if j != idx and other.title:
                corpus_tokens.update(extract_content_tokens(other.title))

        overlap = target_tokens & corpus_tokens
        coherence = len(overlap) / len(target_tokens)

        # If zero token overlap with the entire paper's bibliography and historical
        if coherence == 0.0:
            orphans.append(target)

    return orphans


# ==============================================================================
# Paper Audit Pipeline
# ==============================================================================

def fetch_scopus_references(doi: str) -> list[ReferenceItem]:
    """Retrieve and normalize Scopus references for a paper via pybliometrics."""
    from pybliometrics.scopus import AbstractRetrieval

    try:
        ab = AbstractRetrieval(doi, identifier_type="doi", view="FULL")
        raw_refs = ab.references or []
    except Exception as e:
        return []

    items = []
    for idx, r in enumerate(raw_refs, start=1):
        pos = int(r.position) if (r.position and str(r.position).isdigit()) else idx
        year = int(r.publicationyear) if (r.publicationyear and str(r.publicationyear).isdigit()) else None
        surname = extract_first_surname(r.authors)
        items.append(
            ReferenceItem(
                position=pos,
                source_index="scopus",
                title=r.title,
                authors=r.authors,
                first_surname=surname,
                year=year,
                doi=r.doi,
                eid=str(r.id) if r.id else None,
                venue=r.sourcetitle,
                raw_text=r.fulltext,
            )
        )
    return items


def fetch_openalex_references(
    con: duckdb.DuckDBPyConnection,
    openalex_work_id: int,
) -> list[ReferenceItem]:
    """Retrieve and resolve OpenAlex references for a paper."""
    # Step 1: Get referenced_works array from full OpenAlex partition
    # OpenAlex compact works has referenced_works_count, full works has array
    # Query using work_idx
    query = f"""
        SELECT id, referenced_works
        FROM read_parquet('{M_DRIVE}/openalex_oct26/parquet/works/*/*.parquet')
        WHERE id = 'https://openalex.org/W{openalex_work_id}'
        LIMIT 1
    """
    try:
        row = con.execute(query).fetchall()
        if not row or not row[0][1]:
            return []
        ref_urls = row[0][1]
    except Exception:
        return []

    # Step 2: Extract numeric IDs and resolve from compact works
    numeric_ids = []
    for url in ref_urls:
        m = re.search(r"W(\d+)", url)
        if m:
            numeric_ids.append(m.group(1))

    if not numeric_ids:
        return []

    id_str = ",".join(numeric_ids[:100])  # limit batch size
    resolve_query = f"""
        SELECT work_idx, doi, title, publication_year, cited_by_count
        FROM read_parquet('{OPENALEX_COMPACT_WORKS}/*.parquet')
        WHERE work_idx IN ({id_str})
    """
    try:
        resolved = con.execute(resolve_query).fetchall()
    except Exception:
        return []

    items = []
    for idx, r in enumerate(resolved, start=1):
        items.append(
            ReferenceItem(
                position=idx,
                source_index="openalex",
                title=r[2],
                authors=None,
                first_surname=None,
                year=r[3],
                doi=r[1],
                openalex_id=f"W{r[0]}",
                cited_by_count=r[4],
            )
        )
    return items


def audit_paper_references(
    con: duckdb.DuckDBPyConnection,
    doi: str,
    title: str,
    citing_year: int,
    openalex_id: int | None,
    compare_openalex: bool = True,
) -> PaperDiagnostics:
    """Run all 4 backward reference resolution diagnostic tests on a paper."""
    clean_doi = re.sub(r"^(https?://(dx\.)?doi\.org/|doi:\s*)", "", doi, flags=re.I)
    scopus_refs = fetch_scopus_references(clean_doi)

    # Test 1: Monotonic Alphabetical Inversion
    is_alpha, inversions = detect_alphabetical_inversions(scopus_refs)

    # Test 2: Cross-Index Comparison (if OpenAlex ID provided)
    cross_comparison = None
    openalex_refs = []
    if compare_openalex and openalex_id:
        openalex_refs = fetch_openalex_references(con, openalex_id)
        if openalex_refs and scopus_refs:
            cross_comparison = compare_scopus_openalex_references(
                doi=clean_doi,
                citing_year=citing_year,
                scopus_refs=scopus_refs,
                openalex_refs=openalex_refs,
            )

    # Test 3: Obscure Historical Outliers
    all_refs = scopus_refs + openalex_refs
    historical_outliers = detect_obscure_historical_outliers(
        all_refs, citing_year=citing_year, min_age_gap=20, max_citations=5
    )

    # Test 4: Topological Ghost Orphans (Zero lexical/disciplinary cohesion)
    topological_orphans = detect_topological_ghost_orphans(
        all_refs, citing_year=citing_year, min_age_gap=15
    )

    return PaperDiagnostics(
        doi=clean_doi,
        title=title,
        citing_year=citing_year,
        scopus_ref_count=len(scopus_refs),
        openalex_ref_count=len(openalex_refs),
        is_alphabetical_style=is_alpha,
        inversions=inversions,
        cross_index=cross_comparison,
        historical_outliers=historical_outliers,
        topological_ghost_orphans=topological_orphans,
    )


# ==============================================================================
# Demo Runner
# ==============================================================================

def run_backward_disparities_demo(sample_size: int = 5, year: int = 2014) -> list[PaperDiagnostics]:
    """Run demonstration across a sample of Australian ERA papers."""
    print("=" * 80)
    print(f"BACKWARD REFERENCE RESOLUTION DISPARITY DEMO (Sample: {sample_size} papers, Year: {year})")
    print("=" * 80)

    init_pybliometrics()
    con = duckdb.connect()

    # Query sample papers from synthesis
    query = f"""
        SELECT canonical_id, era_title, oax_doi, era_year, scopus_eid, openalex_id
        FROM read_parquet('{SYNTHESIS_PARQUET}')
        WHERE in_openalex = true AND in_scopus = true AND oax_doi IS NOT NULL
          AND era_year = {year} AND oax_cited_by_count > 40
        ORDER BY canonical_id
        LIMIT {sample_size}
    """
    sample_papers = con.execute(query).fetchall()
    print(f"Loaded {len(sample_papers)} canonical ERA papers for evaluation.\n")

    results = []
    for idx, (cid, title, doi, yr, eid, oax_id) in enumerate(sample_papers, start=1):
        print(f"[{idx}/{len(sample_papers)}] Auditing: {title[:60]}... (DOI: {doi})")
        t0 = time.time()
        diag = audit_paper_references(
            con=con,
            doi=doi,
            title=title,
            citing_year=yr,
            openalex_id=oax_id,
            compare_openalex=True,
        )
        elapsed = time.time() - t0
        results.append(diag)

        # Print summary for this paper
        print(f"    Scopus refs: {diag.scopus_ref_count} | OpenAlex refs: {diag.openalex_ref_count} | Audit time: {elapsed:.2f}s")
        print(f"    Bibliography style: {'Alphabetical (Author-Date)' if diag.is_alphabetical_style else 'Numbered/Citation-Order'}")

        if diag.inversions:
            print(f"    [!] Mean 1 - Alphabetical Inversions: {len(diag.inversions)} detected")
            for inv in diag.inversions[:3]:
                sev = "MAJOR" if inv.is_major else "minor"
                print(f"        Pos {inv.position:2d}: '{inv.curr_surname}' follows '{inv.prev_surname}' (letter dist: {inv.letter_distance}) [{sev}]")
                if inv.title:
                    print(f"            Title: {inv.title[:60]}")

        if diag.cross_index:
            ci = diag.cross_index
            print(f"    [!] Mean 2 - Cross-Index Consensus: Jaccard = {ci.jaccard_similarity:.1%}, Common = {len(ci.consensus_titles)}, "
                  f"Scopus-only = {len(ci.scopus_only_targets)}, OpenAlex-only = {len(ci.openalex_only_targets)}")
            if ci.scopus_only_targets:
                s_sample = ci.scopus_only_targets[0]
                print(f"        Scopus unique target: [{s_sample.year or '?'}] {s_sample.title[:65] if s_sample.title else 'NO TITLE'}")
            if ci.openalex_only_targets:
                o_sample = ci.openalex_only_targets[0]
                print(f"        OpenAlex unique target: [{o_sample.year or '?'}] {o_sample.title[:65] if o_sample.title else 'NO TITLE'}")

        if diag.historical_outliers:
            print(f"    [!] Mean 3 - Obscure Historical Outliers: {len(diag.historical_outliers)} items (>= 20 yrs old with <= 5 cites)")
            for out in diag.historical_outliers[:2]:
                print(f"        Year {out.year}: {out.title[:55] if out.title else 'NO TITLE'} (Cites: {out.cited_by_count})")

        if diag.topological_ghost_orphans:
            print(f"    [!] Mean 4 - Topological Ghost Orphans: {len(diag.topological_ghost_orphans)} items (Zero lexical/field cohesion)")
            for tgo in diag.topological_ghost_orphans[:2]:
                print(f"        Year {tgo.year}: {tgo.title[:55] if tgo.title else 'NO TITLE'}")
        print()

    # Executive Summary
    print("=" * 80)
    print("DEMO AUDIT SUMMARY & DISPARITY TAXONOMY (ALL 4 MEANS)")
    print("=" * 80)
    total_papers = len(results)
    papers_with_delta = sum(1 for r in results if r.scopus_ref_count != r.openalex_ref_count)
    papers_with_inversion = sum(1 for r in results if r.inversions)
    papers_with_discordance = sum(1 for r in results if r.cross_index and (r.cross_index.scopus_only_targets or r.cross_index.openalex_only_targets))
    papers_with_outliers = sum(1 for r in results if r.historical_outliers)
    papers_with_orphans = sum(1 for r in results if r.topological_ghost_orphans)

    print(f"Total Papers Audited:                             {total_papers}")
    print(f"Reference Count Discrepancy (Scopus vs OpenAlex): {papers_with_delta}/{total_papers} ({papers_with_delta/total_papers*100:.1f}%)")
    print(f"Mean 1: Monotonic Alphabetical Inversions (MAI):  {papers_with_inversion}/{total_papers} ({papers_with_inversion/total_papers*100:.1f}%)")
    print(f"Mean 2: Cross-Index Target Conflicts (CCD):       {papers_with_discordance}/{total_papers} ({papers_with_discordance/total_papers*100:.1f}%)")
    print(f"Mean 3: Obscure Historical Outliers (OHO):        {papers_with_outliers}/{total_papers} ({papers_with_outliers/total_papers*100:.1f}%)")
    print(f"Mean 4: Topological Ghost Orphans (TGO):          {papers_with_orphans}/{total_papers} ({papers_with_orphans/total_papers*100:.1f}%)")
    print("=" * 80)

    return results


# ==============================================================================
# CLI Entrypoint
# ==============================================================================

def main():
    parser = argparse.ArgumentParser(description="Metadata-Only Backward Reference Resolution Diagnostics Demo")
    parser.add_argument("--sample-size", type=int, default=5, help="Number of papers to audit in demo (default: 5)")
    parser.add_argument("--year", type=int, default=2014, help="ERA reference year (default: 2014)")
    args = parser.parse_args()

    run_backward_disparities_demo(sample_size=args.sample_size, year=args.year)


if __name__ == "__main__":
    main()
