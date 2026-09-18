"""Fetch Trove title-search results for the ERA output types that have no
ISBN to join on (everything except Book, which fetch_trove_records.py
already handles) - Book Chapter, Curated Exhibition Event, Live
Performance, Original Creative Work, Portfolio, Recorded Rendered Work,
and Research Report for External Body. One (ERA type, reference_year) pair
per run.

Query shape, settled after a session of manual exploration (see the
nla-trove-api-key memory for the full reasoning):
  - category=research,image,book,list,music,diary,newspaper,magazine -
    every category that showed at least one genuine hit across a 72-record
    correlation sample, except "people" (zero hits) and "article" (not a
    real Trove category at all). Deciding WHICH of these categories to
    trust for a given ERA type is deferred to build_trove_title_shard.py -
    fetching all of them up front means that decision can be revisited
    later without re-fetching.
  - reclevel=full, include=workversions - needed for the full author list
    (the main reason to use Trove at all), at the cost of a heavier
    response per query than the ISBN-lookup script's single-edition case.
  - l-year=<ERA reference_year> - critical, not optional. Short/generic
    titles ("Spin", "No Exit", etc.) get flooded with thousands of
    irrelevant results without it; narrowing to the record's own
    reference_year turned "Spin" from 6,713+ hits down to 86, small enough
    to scan for an exact title match. Confirmed live 2026-08-29.
  - No l-year for Portfolio - its reference_year is entirely NULL for all
    690 records (a genuine ERA data gap, not a bug here), so its queries
    run without that filter and will be noisier as a result.

Caching: raw responses go into the same diskcache.Cache at config.TROVE_DIR
that fetch_trove_records.py uses for ISBN lookups, keyed by
f"title::{title}::{year}" (the "title::" prefix keeps these keys
distinguishable from bare ISBN keys, though collision was never a real risk).
Unlike the ISBN cache, this key is NOT parameterised by ERA type or
category - the same title+year query returns the same Trove response
regardless of which ERA record asked for it, so two ERA records sharing an
identical (title, reference_year) - a real, common case, see the
duplicate-rate analysis in docs/era_quality_review.md - only ever cost one
request. size_limit is set generously (see TROVE_CACHE_SIZE_LIMIT in
fetch_trove_records.py) since this cache stores much heavier
multi-category, multi-edition responses than the ISBN cache does.

Usage: python3 fetch_trove_titles.py <era_type_slug> <era_reference_year>
  era_type_slug is one of: book_chapter, curated_exhibition_event,
  live_performance, original_creative_work, portfolio,
  recorded_rendered_work, research_report_for_external_body
"""

import json
import sys
import time
from pathlib import Path

import diskcache
import duckdb
import requests

sys.path.insert(0, str(Path(__file__).parent))
from fetch_trove_records import load_env_value, TROVE_CACHE_SIZE_LIMIT

sys.path.insert(0, str(Path(__file__).parent.parent))
from config import TROVE_DIR

DATA_DIR = Path(__file__).parent.parent / "data"
ERA_PARQUET = DATA_DIR / "era_research_outputs.parquet"
TARGET_LIST_DIR = DATA_DIR / "trove_title_targets"

API_URL = "https://api.trove.nla.gov.au/v3/result"
CATEGORIES = "research,image,book,list,music,diary,newspaper,magazine"
REQUESTS_PER_MINUTE = 100
REQUEST_INTERVAL = 60.0 / REQUESTS_PER_MINUTE
PROGRESS_PRINT_EVERY = 50
MAX_ATTEMPTS = 3

# era_type_slug -> ERA's own research_output_type string. Book excluded -
# fetch_trove_records.py handles it via ISBN. Portfolio has no
# reference_year at all (see module docstring).
TYPES = {
    "book_chapter": "Book Chapter",
    "curated_exhibition_event": "Curated Exhibition Event",
    "live_performance": "Live Performance",
    "original_creative_work": "Original Creative Work",
    "portfolio": "Portfolio",
    "recorded_rendered_work": "Recorded Rendered Work",
    "research_report_for_external_body": "Research Report for External Body",
}


def targets_for(era_type, year):
    # JSON Lines, not TSV - a real ERA title turned up with a literal
    # embedded newline character ("...minimaxing strategy have\non the
    # benefits from external reviews"), which silently corrupted a plain
    # tab-separated file's line structure (confirmed 2026-08-31, broke
    # book_chapter 2012/2013's persisted lists). JSON escaping handles
    # embedded newlines/tabs/quotes in a title correctly; TSV does not.
    list_file = TARGET_LIST_DIR / era_type / f"{year}.txt"
    if list_file.exists():
        pairs = []
        for line in list_file.read_text().splitlines():
            if not line.strip():
                continue
            obj = json.loads(line)
            pairs.append((obj["id"], obj["title"]))
        return pairs

    con = duckdb.connect()
    era_output_type = TYPES[era_type]
    if year == 0:  # sentinel for Portfolio's NULL reference_year
        year_clause = "reference_year IS NULL"
    else:
        year_clause = f"reference_year = {int(year)}"
    query = f"""
        SELECT id, title
        FROM read_parquet('{ERA_PARQUET}')
        WHERE research_output_type = '{era_output_type}' AND {year_clause}
          AND title IS NOT NULL AND trim(title) != ''
        ORDER BY id
    """
    pairs = con.execute(query).fetchall()
    list_file.parent.mkdir(parents=True, exist_ok=True)
    list_file.write_text("".join(json.dumps({"id": id_, "title": title}) + "\n" for id_, title in pairs))
    return pairs


def cache_key(title, year):
    return f"title::{title.strip()}::{year}"


def fetch_raw(cache, session, headers, title, year):
    key = cache_key(title, year)
    cached = cache.get(key)
    if cached is not None:
        return cached, True

    params = {
        "category": CATEGORIES,
        "q": f'title:"{title.strip()}"',
        "n": 5,
        "encoding": "json",
        "reclevel": "full",
        "include": "workversions",
    }
    if year != 0:
        params["l-year"] = str(year)

    last_exc = None
    for attempt in range(MAX_ATTEMPTS):
        try:
            resp = session.get(API_URL, headers=headers, params=params, timeout=30)
            resp.raise_for_status()
            data = resp.json()
            cache[key] = data
            return data, False
        except requests.exceptions.RequestException as e:
            last_exc = e
            if attempt < MAX_ATTEMPTS - 1:
                time.sleep(2 ** attempt)
    raise last_exc


def fetch_targets(era_type, year):
    api_key = load_env_value("trove_token")
    if not api_key:
        raise RuntimeError("trove_token not found in .env")
    headers = {"X-API-KEY": api_key}

    targets = targets_for(era_type, year)
    year_label = "NULL" if year == 0 else year
    print(f"{len(targets)} ERA {TYPES[era_type]} records for reference_year={year_label}")

    n_cached = n_fetched = n_error = 0
    TROVE_DIR.mkdir(parents=True, exist_ok=True)
    with diskcache.Cache(str(TROVE_DIR), size_limit=TROVE_CACHE_SIZE_LIMIT) as cache, requests.Session() as session:
        for i, (era_id, title) in enumerate(targets):
            t0 = time.time()
            try:
                _, from_cache = fetch_raw(cache, session, headers, title, year)
            except requests.exceptions.RequestException as e:
                print(f"  ERA {era_id}: giving up after {MAX_ATTEMPTS} attempts ({e})")
                n_error += 1
                continue
            if from_cache:
                n_cached += 1
            else:
                n_fetched += 1
                elapsed = time.time() - t0
                if elapsed < REQUEST_INTERVAL:
                    time.sleep(REQUEST_INTERVAL - elapsed)

            if (i + 1) % PROGRESS_PRINT_EVERY == 0:
                print(f"  {i + 1}/{len(targets)} processed ({n_cached} from cache, "
                      f"{n_fetched} fetched this run, {n_error} error)")

    return len(targets), n_cached, n_fetched, n_error


def main():
    if len(sys.argv) != 3 or sys.argv[1] not in TYPES:
        print(f"Usage: fetch_trove_titles.py <era_type_slug> <era_reference_year>")
        print(f"  era_type_slug one of: {', '.join(TYPES)}")
        print(f"  (Portfolio has no reference_year - pass 0 for it)")
        sys.exit(1)
    era_type = sys.argv[1]
    year = int(sys.argv[2])

    n_total, n_cached, n_fetched, n_error = fetch_targets(era_type, year)
    print(f"\n{era_type} {year}: {n_total} total ({n_cached} from cache, "
          f"{n_fetched} fetched this run, {n_error} error)")


if __name__ == "__main__":
    main()
