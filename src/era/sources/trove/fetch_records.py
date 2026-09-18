"""Fetch Trove (National Library of Australia) book records for ERA Book
outputs, matched by ISBN, for one ERA reference_year at a time. See
https://trove.nla.gov.au/about/create-something/using-api/v3/api-technical-guide

This is still a validation-stage script, not a finished pipeline stage -
see the module-level notes below and the [[nla-trove-api-key]] /
scopus-api-rate-limits-style memory this will get once the schema settles.

Notes from getting this working (not documented clearly on the page
above):
  - Auth is an `X-API-KEY` header, not a `key=` query parameter as the
    guide's example URLs suggest - confirmed by testing both live.
  - Rate limit is generous: confirmed live from `ratelimit-limit`/
    `ratelimit-remaining`/`ratelimit-reset` response headers - 200
    requests/minute (matches the "Level 1" tier shown on this key's
    registration page), resetting every ~60s. This script paces to
    REQUESTS_PER_MINUTE (currently 100, half the confirmed cap, on request)
    rather than maxing it out.
  - `/v3/result` with `q=isbn:XXXX` returns a 'work' record (Trove's
    FRBR-like aggregate of every edition of a title), not necessarily the
    exact edition matching that ISBN - `include=workversions` only returns
    a SUBSET of a work's editions, and testing on 4 real ERA ISBNs found
    one case where the queried ISBN's edition simply wasn't in that
    subset, even though the work itself matched. So every result needs to
    be classified into three states, not two:
      - "ok": the queried ISBN was found in one of the returned editions -
        creator/issued/publisher come from that specific edition's
        dc metadata, so are trustworthy for that record.
      - "edition_unresolved": the work matched but no returned edition
        actually listed our ISBN - only work-level fields (aggregate
        title, work id/url) are usable; creator/issued/publisher are
        NOT populated, rather than guessed from an arbitrary edition.
      - "not_found": no work matched the ISBN query at all.
  - Publisher came back empty in all 4 manually-tested editions - kept in
    the schema but expect it to be sparse.

Raw responses are cached via diskcache.Cache at config.TROVE_DIR (K_DRIVE/
era/.trove), keyed by cleaned ISBN - mirrors fetch_scopus_records.py's
choice to keep API caches on K_DRIVE rather than under the repo/home dir.
Unlike Scopus's batched Search queries, each Trove lookup is keyed by a
single stable ISBN with no batch-grouping alignment to worry about, so a
flat key->response cache is enough. Re-running this script only spends
quota on ISBNs not already cached.
"""

import json
import sys
import time
from pathlib import Path

import diskcache
import duckdb
import requests

sys.path.insert(0, str(Path(__file__).parent.parent))
from config import TROVE_DIR

DATA_DIR = Path(__file__).parent.parent / "data"
ERA_PARQUET = DATA_DIR / "era_research_outputs.parquet"
ENV_FILE = Path(__file__).parent.parent / ".env"

API_URL = "https://api.trove.nla.gov.au/v3/result"
REQUESTS_PER_MINUTE = 100
REQUEST_INTERVAL = 60.0 / REQUESTS_PER_MINUTE
PROGRESS_PRINT_EVERY = 50

# diskcache defaults to a 1GB size_limit with LRU eviction once full - the
# title-search cache (fetch_trove_titles.py) shares this same TROVE_DIR and
# stores much heavier multi-category, multi-edition responses than this
# script's single-edition ISBN lookups, so the default would start
# silently evicting entries well before either fetch is done. Set well
# above anything either script should realistically need, but still far
# under K_DRIVE's free space (890GB as of 2026-08-29).
TROVE_CACHE_SIZE_LIMIT = 100 * 2**30  # 100GB


def load_env_value(name):
    for line in ENV_FILE.read_text().splitlines():
        if line.strip().startswith(f"{name}="):
            return line.split("=", 1)[1].strip()
    return None


def isbns_for_year(year):
    con = duckdb.connect()
    query = f"""
        SELECT DISTINCT json_extract_string(other_details, '$.isbn') AS isbn, title
        FROM read_parquet('{ERA_PARQUET}')
        WHERE research_output_type = 'Book' AND reference_year = {int(year)}
          AND other_details IS NOT NULL
        ORDER BY isbn
    """
    return con.execute(query).fetchall()


MAX_ATTEMPTS = 3


def fetch_raw(cache, session, headers, isbn_clean):
    cached = cache.get(isbn_clean)
    if cached is not None:
        return cached, True

    params = {
        "category": "book",
        "q": f"isbn:{isbn_clean}",
        "n": 1,
        "encoding": "json",
        "reclevel": "full",
        "include": "workversions",
    }
    # Trove's Kong gateway occasionally returns a transient 400/5xx that
    # succeeds identically on immediate retry (confirmed manually - not a
    # real bad-request or a problem with any specific ISBN), so retry a
    # few times with backoff before giving up on this one ISBN.
    last_exc = None
    for attempt in range(MAX_ATTEMPTS):
        try:
            resp = session.get(API_URL, headers=headers, params=params, timeout=30)
            resp.raise_for_status()
            data = resp.json()
            cache[isbn_clean] = data
            return data, False
        except requests.exceptions.RequestException as e:
            last_exc = e
            if attempt < MAX_ATTEMPTS - 1:
                time.sleep(2 ** attempt)
    raise last_exc


def dc_for_isbn(work, isbn_clean):
    for v in (work.get("version") or []):
        rec = (v.get("record") or [{}])[0]
        dc = (rec.get("metadata") or {}).get("dc") or {}
        for ident in (dc.get("identifier") or []):
            if ident.get("type") == "isbn" and isbn_clean in ident.get("value", "").replace("-", ""):
                return dc
    return None


def parse_result(data, isbn, isbn_clean, era_title):
    works = data["category"][0]["records"].get("work", [])
    if not works:
        return {"isbn_normalized": isbn, "era_title": era_title, "status": "not_found"}

    w = works[0]
    dc = dc_for_isbn(w, isbn_clean)
    if dc is None:
        return {
            "isbn_normalized": isbn,
            "era_title": era_title,
            "status": "edition_unresolved",
            "trove_work_id": w.get("id"),
            "trove_url": w.get("troveUrl"),
            "title": w.get("title"),
            "title_source": "work",
        }

    creators = [c.get("name") for c in (dc.get("creator") or [])]
    issued = [i.get("value") for i in (dc.get("issued") or [])]
    isbns_found = [i["value"] for i in (dc.get("identifier") or []) if i.get("type") == "isbn"]
    title = dc.get("title") or [w.get("title")]
    title = title[0] if isinstance(title, list) else title

    return {
        "isbn_normalized": isbn,
        "era_title": era_title,
        "status": "ok",
        "trove_work_id": w.get("id"),
        "trove_url": w.get("troveUrl"),
        "title": title,
        "title_source": "edition",
        "creators": creators,
        "issued_year": issued,
        "isbns_on_edition": isbns_found,
        "publisher": dc.get("publisher"),
        "subject": dc.get("subject"),
    }


def fetch_year(year):
    api_key = load_env_value("trove_token")
    if not api_key:
        raise RuntimeError("trove_token not found in .env")
    headers = {"X-API-KEY": api_key}

    targets = isbns_for_year(year)
    print(f"{len(targets)} distinct (isbn, title) pairs for ERA Book, reference_year={year}")

    rows = []
    n_cached = n_fetched = 0
    TROVE_DIR.mkdir(parents=True, exist_ok=True)
    with diskcache.Cache(str(TROVE_DIR), size_limit=TROVE_CACHE_SIZE_LIMIT) as cache, requests.Session() as session:
        for i, (isbn, era_title) in enumerate(targets):
            isbn_clean = isbn.replace("-", "").strip()
            t0 = time.time()
            try:
                data, from_cache = fetch_raw(cache, session, headers, isbn_clean)
            except requests.exceptions.RequestException as e:
                print(f"  {isbn}: giving up after {MAX_ATTEMPTS} attempts ({e}) - recording as fetch_error")
                rows.append({"isbn_normalized": isbn, "era_title": era_title, "status": "fetch_error"})
                continue
            rows.append(parse_result(data, isbn, isbn_clean, era_title))
            if from_cache:
                n_cached += 1
            else:
                n_fetched += 1
                elapsed = time.time() - t0
                if elapsed < REQUEST_INTERVAL:
                    time.sleep(REQUEST_INTERVAL - elapsed)

            if (i + 1) % PROGRESS_PRINT_EVERY == 0:
                print(f"  {i + 1}/{len(targets)} processed ({n_cached} from cache, {n_fetched} fetched this run)")

    return rows, n_cached, n_fetched


def main():
    year = int(sys.argv[1]) if len(sys.argv) > 1 else 2011
    rows, n_cached, n_fetched = fetch_year(year)

    n_ok = sum(1 for r in rows if r["status"] == "ok")
    n_unresolved = sum(1 for r in rows if r["status"] == "edition_unresolved")
    n_not_found = sum(1 for r in rows if r["status"] == "not_found")
    n_fetch_error = sum(1 for r in rows if r["status"] == "fetch_error")
    print(f"\n{year}: {len(rows)} total ({n_cached} from cache, {n_fetched} fetched this run)")
    print(f"  ok (edition confirmed): {n_ok} ({n_ok / len(rows) * 100:.1f}%)")
    print(f"  edition_unresolved (work matched, edition not in returned versions): "
          f"{n_unresolved} ({n_unresolved / len(rows) * 100:.1f}%)")
    print(f"  not_found: {n_not_found} ({n_not_found / len(rows) * 100:.1f}%)")
    if n_fetch_error:
        print(f"  fetch_error (gave up after {MAX_ATTEMPTS} attempts - re-run to retry): "
              f"{n_fetch_error} ({n_fetch_error / len(rows) * 100:.1f}%)")

    out_file = DATA_DIR / f"trove_test_{year}.jsonl"
    with open(out_file, "w") as f:
        for r in rows:
            f.write(json.dumps(r) + "\n")
    print(f"Written to {out_file}")


if __name__ == "__main__":
    main()
