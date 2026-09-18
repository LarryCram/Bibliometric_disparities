"""Fetch Trove title-search results for each nested item inside ERA
Portfolio records (the portfolio-items JSON), not the portfolio's own
umbrella title. Confirmed directly 2026-09-04: the umbrella title (e.g.
"Auspices") is an ERA administrative label the artist chose to bundle
several separate works into one submission, not something independently
published or catalogued - Trove has no concept of ERA's portfolio->items
hierarchy at all. Each nested item is fetched and searched completely
independently; the parent portfolio id is tracked on our side only, never
passed to or recognised by Trove.

Live-tested against one real portfolio ("Auspices", 4 items) before
building this: the two long/distinctive item titles ("The botanist at
his mothers grave", "The dead are bored") each resolved to a single,
clean hit in Trove's "research" category, both confirming the same
author name and ORCID. The two short/generic titles ("Guitar",
"Auspices" itself - which happens to also be one item's own title)
flooded with thousands of irrelevant hits spread across every OTHER
category (book/music/image/newspaper/etc). category=research,image,...
(all 8, same as fetch_trove_titles.py) is still fetched here, matching
every other ERA type's fetch/build split - deciding which category to
trust is deferred to the not-yet-written build_trove_portfolio_item_
shard.py, same "fetch broad, decide trust later" principle as
fetch_trove_titles.py - but that decision should trust only the
"research" category's hits here, per the live test above.

Reuses fetch_trove_titles.py's fetch_raw()/cache_key() exactly (same
diskcache.Cache at config.TROVE_DIR, same cache_key(title, year) shape,
year=0 always - Portfolio's reference_year is NULL for all 690 records)
so any overlap with a top-level portfolio-title fetch (if one is ever
run) shares cache entries for free, and reruns of this script resume
from cache with no wasted requests.

Usage: python3 fetch_trove_portfolio_items.py
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
from fetch_trove_titles import fetch_raw

sys.path.insert(0, str(Path(__file__).parent.parent))
from config import TROVE_DIR

DATA_DIR = Path(__file__).parent.parent / "data"
RAW_PARQUET = DATA_DIR / "era_research_outputs_raw.parquet"
TARGET_LIST_FILE = DATA_DIR / "trove_title_targets" / "portfolio_items.txt"

REQUESTS_PER_MINUTE = 100
REQUEST_INTERVAL = 60.0 / REQUESTS_PER_MINUTE
PROGRESS_PRINT_EVERY = 50
MAX_ATTEMPTS = 3


def targets_for_portfolio_items():
    """(portfolio_era_id, item_index, item_title) triples, one per
    nested item across every ERA Portfolio record. Cached to a JSON
    Lines target-list file (same reasoning as fetch_trove_titles.py -
    JSON, not TSV, since item titles can contain embedded punctuation/
    newlines a plain-delimited file would mishandle).
    """
    if TARGET_LIST_FILE.exists():
        triples = []
        for line in TARGET_LIST_FILE.read_text().splitlines():
            if not line.strip():
                continue
            obj = json.loads(line)
            triples.append((obj["era_id"], obj["item_index"], obj["title"]))
        return triples

    con = duckdb.connect()
    rows = con.execute(f"""
        SELECT id, portfolio_items
        FROM read_parquet('{RAW_PARQUET}')
        WHERE research_output_type = 'Portfolio' AND portfolio_items IS NOT NULL
        ORDER BY id
    """).fetchall()

    triples = []
    for era_id, items_json in rows:
        items = json.loads(items_json)
        for i, item in enumerate(items):
            title = item.get("title")
            if title and title.strip():
                triples.append((era_id, i, title))

    TARGET_LIST_FILE.parent.mkdir(parents=True, exist_ok=True)
    TARGET_LIST_FILE.write_text("".join(
        json.dumps({"era_id": era_id, "item_index": i, "title": title}) + "\n"
        for era_id, i, title in triples
    ))
    return triples


def fetch_targets():
    api_key = load_env_value("trove_token")
    if not api_key:
        raise RuntimeError("trove_token not found in .env")
    headers = {"X-API-KEY": api_key}

    targets = targets_for_portfolio_items()
    print(f"{len(targets)} portfolio items across all ERA Portfolio records")

    n_cached = n_fetched = n_error = 0
    TROVE_DIR.mkdir(parents=True, exist_ok=True)
    with diskcache.Cache(str(TROVE_DIR), size_limit=TROVE_CACHE_SIZE_LIMIT) as cache, requests.Session() as session:
        for i, (era_id, item_index, title) in enumerate(targets):
            t0 = time.time()
            try:
                _, from_cache = fetch_raw(cache, session, headers, title, 0)
            except requests.exceptions.RequestException as e:
                print(f"  ERA {era_id} item {item_index}: giving up after {MAX_ATTEMPTS} attempts ({e})")
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
    n_total, n_cached, n_fetched, n_error = fetch_targets()
    print(f"\nportfolio items: {n_total} total ({n_cached} from cache, "
          f"{n_fetched} fetched this run, {n_error} error)")


if __name__ == "__main__":
    main()
