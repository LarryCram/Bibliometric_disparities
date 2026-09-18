"""Batch-fetch Web of Science records for ERA outputs, via the Clarivate
Web of Science Starter API (GET /documents/{uid}).

The WOS accession number isn't a top-level column in the ERA export - it's
buried in the other_details JSON blob (e.g. other_details.identifier =
"WOS:000285368400043"), and only journal articles have one. There are
296,164 distinct WOS UIDs across the ERA dataset (390,648 records, since
~24% are the same multi-institution duplicate submissions seen elsewhere in
this project).

This API key is rate-limited to 5 req/sec and 5,000/day - nowhere near
enough to fetch all 296,164 UIDs in one run (~60 days at full quota). This
script is designed to be resumable: run it, it fetches until the daily quota
is exhausted (detected from response headers, or a 429), then stops cleanly.
Re-run it (e.g. once a day, or via cron) and it picks up where it left off.

Set ONLY_UNMATCHED = True to restrict to ERA records where the OpenAlex
match (era_openalex_matches_with_oa.parquet) found nothing - i.e. the WOS
lookup would add information not otherwise available - rather than covering
every WOS-tagged record regardless of OpenAlex match status.
"""

import json
import os
import time
from pathlib import Path

import duckdb
import requests

DATA_DIR = Path(__file__).parent.parent / "data"
ERA_PARQUET = DATA_DIR / "era_research_outputs.parquet"
OA_MATCHES = DATA_DIR / "era_openalex_matches_with_oa.parquet"
OUTPUT_FILE = DATA_DIR / "wos_records.jsonl"

ONLY_UNMATCHED = False  # True = only ERA records OpenAlex couldn't match

WOS_API_BASE = "https://api.clarivate.com/apis/wos-starter/v1"
REQUEST_INTERVAL = 0.25  # seconds between requests (4/sec, under the 5/sec cap)


def load_api_key():
    env_path = Path(__file__).parent.parent / ".env"
    for line in env_path.read_text().splitlines():
        if line.startswith("WOS_API="):
            return line.split("=", 1)[1].strip()
    raise RuntimeError("WOS_API not found in .env")


def target_wos_uids():
    con = duckdb.connect()
    where_unmatched = ""
    if ONLY_UNMATCHED:
        where_unmatched = f"""
            AND id NOT IN (
                SELECT id FROM read_parquet('{OA_MATCHES}') WHERE match_type != 'none'
            )
        """
    query = f"""
        SELECT DISTINCT regexp_extract(other_details, 'WOS:[0-9A-Z]+', 0) AS wos_uid
        FROM read_parquet('{ERA_PARQUET}')
        WHERE other_details LIKE '%WOS:%'
        {where_unmatched}
        ORDER BY wos_uid
    """
    return [row[0] for row in con.execute(query).fetchall()]


def already_fetched():
    if not OUTPUT_FILE.exists():
        return set()
    done = set()
    with open(OUTPUT_FILE) as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                done.add(json.loads(line)["wos_uid"])
            except (json.JSONDecodeError, KeyError):
                continue
    return done


def main():
    api_key = load_api_key()
    headers = {"X-ApiKey": api_key, "Accept": "application/json"}

    all_uids = target_wos_uids()
    done = already_fetched()
    remaining_uids = [u for u in all_uids if u not in done]
    print(f"{len(all_uids)} target WOS UIDs, {len(done)} already fetched, "
          f"{len(remaining_uids)} remaining")

    if not remaining_uids:
        print("Nothing to do.")
        return

    n_ok, n_error = 0, 0
    with open(OUTPUT_FILE, "a") as out, requests.Session() as session:
        for i, uid in enumerate(remaining_uids):
            t0 = time.time()
            try:
                resp = session.get(f"{WOS_API_BASE}/documents/{uid}", headers=headers, timeout=30)
                if resp.status_code == 429:
                    print(f"Hit rate limit (429) at UID {i} of {len(remaining_uids)} this run. "
                          f"Stopping - re-run this script later (e.g. tomorrow) to resume.")
                    break
                if resp.status_code == 200:
                    out.write(json.dumps({"wos_uid": uid, "status": "ok", "response": resp.json()}) + "\n")
                    n_ok += 1
                else:
                    out.write(json.dumps({
                        "wos_uid": uid, "status": "error",
                        "error": f"HTTP {resp.status_code}: {resp.text[:500]}",
                    }) + "\n")
                    n_error += 1
                out.flush()

                remaining_day = resp.headers.get("x-ratelimit-remaining-day")
                if remaining_day is not None and int(remaining_day) <= 0:
                    print(f"Daily quota exhausted after {i + 1} requests this run. "
                          f"Re-run this script tomorrow to resume.")
                    break
            except requests.RequestException as e:
                out.write(json.dumps({"wos_uid": uid, "status": "error", "error": str(e)}) + "\n")
                out.flush()
                n_error += 1

            if (i + 1) % 100 == 0:
                print(f"  {i + 1}/{len(remaining_uids)} processed this run "
                      f"({n_ok} ok, {n_error} errors)")

            elapsed = time.time() - t0
            if elapsed < REQUEST_INTERVAL:
                time.sleep(REQUEST_INTERVAL - elapsed)

    print(f"\nThis run: {n_ok} ok, {n_error} errors. Total fetched so far: {len(already_fetched())}/{len(all_uids)}")
    print(f"Results in {OUTPUT_FILE} (JSON Lines, one record per WOS UID)")


if __name__ == "__main__":
    main()
