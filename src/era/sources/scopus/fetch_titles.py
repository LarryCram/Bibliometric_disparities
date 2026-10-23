"""Fetch Scopus records by Title for canonical ERA outputs lacking DOIs.

Queries the Scopus Search API (view=COMPLETE) using exact title phrases (`TITLE("...")`)
to populate pybliometrics' native on-disk cache on K_DRIVE (K_DRIVE/era/.scopus/Scopus/scopus_search/COMPLETE/).

Features:
  1. Full Resumability: Checks pybliometrics' cache path before issuing requests.
     Cached items load in 0 ms with zero network overhead.
  2. Solid Adaptive Exponential Backoff:
     - Normal polite cruising speed: ~6-8 req/sec (0.12s sleep).
     - On Scopus429Error (burst limit), Scopus500Error, or network drops:
       exponential backoff (base 2.0s, doubles up to 60s max) with randomized jitter.
     - Retries up to 6 times per title before logging and continuing.
  3. Query Hygiene:
     - Escapes HTML entities, strips internal quotes/braces, ensures length >= 15 chars.

Usage:
  python3 fetch_titles.py [--limit N] [--min-length 15] [--output-type "Journal Article"]
"""

import argparse
import html
import random
import re
import sys
import time
from pathlib import Path
from typing import Any

import duckdb
from requests.exceptions import ConnectionError, RequestException, Timeout

from era.config import DATA_DIR
from era.sources.scopus.fetch import (
    VIEW,
    cache_path_for_query,
    init_pybliometrics,
)

CANONICAL_PQ = DATA_DIR / "era_research_outputs_canonical.parquet"

_CLEAN_PUNCT_RE = re.compile(r'[\"{}\\]')
_SPACE_RE = re.compile(r"\s+")


def clean_title_for_scopus_query(title: str | None) -> str:
    """Sanitize title for Scopus TITLE(...) query syntax."""
    if not title:
        return ""
    u = html.unescape(str(title))
    # Remove internal double quotes, curly braces, and backslashes that break query parser
    c = _CLEAN_PUNCT_RE.sub(" ", u)
    return _SPACE_RE.sub(" ", c).strip()


def build_scopus_title_query(title: str) -> str:
    """Format sanitized title as exact phrase query."""
    clean = clean_title_for_scopus_query(title)
    return f'TITLE("{clean}")'


def execute_title_search_with_backoff(
    scopus_module: Any,
    query: str,
    max_retries: int = 6,
    base_backoff: float = 2.0,
    max_backoff: float = 60.0,
    polite_delay: float = 0.12,
) -> tuple[Any | None, str]:
    """Execute ScopusSearch with caching check and robust exponential backoff.
    
    Returns:
        (search_object, status_str)
        status_str in: 'cached_hit', 'cached_miss', 'api_hit', 'api_miss', 'syntax_error', 'max_retries_exceeded'
    """
    from pybliometrics.exception import (
        Scopus429Error,
        ScopusServerError,
        ScopusHtmlError,
        ScopusQueryError,
    )

    # 1. Fast-path: Check pybliometrics cache first
    try:
        cache_file = cache_path_for_query(query, view=VIEW)
        if cache_file.exists():
            s = scopus_module.ScopusSearch(query, view=VIEW, refresh=False)
            count = s.get_results_size()
            return s, ("cached_hit" if count > 0 else "cached_miss")
    except Exception:
        # Fall back to standard execution if cache inspection encounters any error
        pass

    # 2. Network call with exponential backoff
    for attempt in range(max_retries):
        try:
            s = scopus_module.ScopusSearch(query, view=VIEW, refresh=False)
            count = s.get_results_size()
            time.sleep(polite_delay)
            return s, ("api_hit" if count > 0 else "api_miss")

        except ScopusQueryError as e:
            # Query translation syntax error (e.g. malformed title string)
            # Not retryable - log and return immediately
            return None, f"syntax_error: {e}"

        except (Scopus429Error, ScopusServerError, ScopusHtmlError, Timeout, ConnectionError, RequestException) as e:
            err_name = type(e).__name__
            if attempt < max_retries - 1:
                # Exponential backoff with full jitter
                jitter = random.uniform(0.5, 1.5)
                wait_time = min(max_backoff, (base_backoff * (2 ** attempt)) + jitter)
                print(
                    f"  [Scopus {err_name}] Attempt {attempt + 1}/{max_retries} failed. "
                    f"Backing off for {wait_time:.1f}s before retry..."
                )
                time.sleep(wait_time)
            else:
                print(f"  [Scopus {err_name}] Max retries ({max_retries}) exceeded for query: {query[:60]}...")
                return None, f"max_retries_exceeded: {err_name}"

    return None, "max_retries_exceeded"


def get_non_doi_canonical_titles(
    canonical_pq: Path = CANONICAL_PQ,
    output_type: str = "Journal Article",
    min_length: int = 15,
    limit: int | None = None,
) -> list[tuple[int, str, str | None]]:
    """Load canonical works lacking DOIs for title searching.
    
    Returns list of (canonical_id, title, outlet).
    """
    con = duckdb.connect()
    q = f"""
        SELECT 
            canonical_id,
            title,
            outlet
        FROM read_parquet('{canonical_pq}')
        WHERE research_output_type = '{output_type}'
          AND (doi IS NULL OR doi_wellformed = false)
          AND title IS NOT NULL
          AND length(title) >= {min_length}
        ORDER BY canonical_id
    """
    if limit is not None:
        q += f" LIMIT {int(limit)}"
    return con.execute(q).fetchall()


def warm_scopus_title_cache(
    limit: int | None = None,
    output_type: str = "Journal Article",
    min_length: int = 15,
    print_every: int = 50,
) -> dict[str, int]:
    """Execute title cache warming across candidate canonical records."""
    t0 = time.time()
    print("Initializing pybliometrics...")
    scopus = init_pybliometrics()

    print(f"Loading non-DOI {output_type} titles (min_length={min_length}) from {CANONICAL_PQ}...")
    candidates = get_non_doi_canonical_titles(
        canonical_pq=CANONICAL_PQ,
        output_type=output_type,
        min_length=min_length,
        limit=limit,
    )
    total = len(candidates)
    print(f"Loaded {total:,} candidate works to process.")

    stats = {
        "total": total,
        "cached_hits": 0,
        "cached_misses": 0,
        "api_hits": 0,
        "api_misses": 0,
        "syntax_errors": 0,
        "network_errors": 0,
    }

    t_start_loop = time.time()
    for i, (cid, title, outlet) in enumerate(candidates, start=1):
        clean_title = clean_title_for_scopus_query(title)
        if len(clean_title) < min_length:
            continue

        query = build_scopus_title_query(clean_title)
        s, status = execute_title_search_with_backoff(scopus, query)

        if status == "cached_hit":
            stats["cached_hits"] += 1
        elif status == "cached_miss":
            stats["cached_misses"] += 1
        elif status == "api_hit":
            stats["api_hits"] += 1
        elif status == "api_miss":
            stats["api_misses"] += 1
        elif status.startswith("syntax_error"):
            stats["syntax_errors"] += 1
        else:
            stats["network_errors"] += 1

        if i % print_every == 0 or i == total:
            elapsed = time.time() - t_start_loop
            rate = i / elapsed if elapsed > 0 else 0
            hits = stats["cached_hits"] + stats["api_hits"]
            print(
                f"Progress: {i:,}/{total:,} ({100.0 * i / total:.1f}%) | "
                f"Hits: {hits:,} ({100.0 * hits / i:.1f}%) | "
                f"API calls: {stats['api_hits'] + stats['api_misses']:,} | "
                f"Rate: {rate:.1f} items/s"
            )

    t_total = time.time() - t0
    total_hits = stats["cached_hits"] + stats["api_hits"]
    print(f"\nCompleted in {t_total:.1f}s!")
    print(f"Summary: {total_hits:,} hits across {total:,} queried titles ({100.0 * total_hits / total if total > 0 else 0:.1f}%).")
    print(f"Breakdown: {stats}")
    return stats


def main():
    parser = argparse.ArgumentParser(description="Warm Scopus title cache for non-DOI ERA records.")
    parser.add_argument("--limit", type=int, default=None, help="Maximum number of titles to query (for testing).")
    parser.add_argument("--output-type", type=str, default="Journal Article", help="ERA output type.")
    parser.add_argument("--min-length", type=int, default=15, help="Minimum title character length.")
    parser.add_argument("--print-every", type=int, default=50, help="Print progress interval.")
    args = parser.parse_args()

    warm_scopus_title_cache(
        limit=args.limit,
        output_type=args.output_type,
        min_length=args.min_length,
        print_every=args.print_every,
    )


if __name__ == "__main__":
    main()
