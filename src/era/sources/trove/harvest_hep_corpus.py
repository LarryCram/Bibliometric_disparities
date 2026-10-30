"""Harvest research outputs from Trove for Australian Higher Education Providers (HEPs)
across the ERA window (2011-2016) without format pre-filtering and without deduplication.

Saves each institution's complete, un-deduplicated record stream to:
    data/trove_harvest/hep_{code}.parquet
"""

import json
import logging
import os
import re
import sys
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

import diskcache
import duckdb
import requests

BASE_DIR = Path(__file__).resolve().parent.parent.parent.parent.parent
sys.path.insert(0, str(BASE_DIR))
from config import DATA_DIR, PROJECT_ROOT, TROVE_DIR

ENV_FILE = PROJECT_ROOT / ".env"

HARVEST_DIR = DATA_DIR / "trove_harvest"
HARVEST_DIR.mkdir(parents=True, exist_ok=True)

# Cache directory on K_DRIVE (persisted across runs, zero re-querying on re-runs)
CACHE_DIR = TROVE_DIR / "harvest_cache"
CACHE_DIR.mkdir(parents=True, exist_ok=True)
trove_cache = diskcache.Cache(str(CACHE_DIR))

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger("trove_harvester")

API_URL = "https://api.trove.nla.gov.au/v3/result"


def get_trove_api_key() -> str:
    """Retrieve Trove API key from environment or .env file."""
    api_key = os.getenv("TROVE_API_KEY") or os.getenv("NLA_TROVE_API_KEY")
    if not api_key and ENV_FILE.exists():
        with open(ENV_FILE, "r") as f:
            for line in f:
                if "trove" in line.lower():
                    m = re.search(r"=\s*([^\s#]+)", line)
                    if m:
                        api_key = m.group(1).strip("\"'")
                        break
    if not api_key:
        raise ValueError("No Trove API key found in environment or .env file!")
    return api_key


# Institution harvest targets: Name -> (institution_code, search_query)
# Cohort 1 (Done): DIV, ACU, CDU, LAT, USC, USQ, CQU, UNE, UND, NEW (8,417 records)
# Cohort 2 (Done): UTS, MUR, USA, BON, FED, SWI, SCU, ECU, SYD, UWA (57,576 records)
# Cohort 3 (Done): MON, WSU, VIC, CAN, CSU, UNSW, FLI, JCU, RMI, WOL (158,522 records)
# Cohort 4: Final 10 largest HEPs by Trove records (2011-2016)
TRIAL_INSTITUTIONS = [
    {
        "code": "MAC",
        "name": "Macquarie University",
        "query": '(nuc:"NMQU:IR" OR "mq.edu.au") date:[2011 TO 2016]',
    },
    {
        "code": "MEL",
        "name": "The University of Melbourne",
        "query": '(nuc:"VU:IR" OR "unimelb.edu.au") date:[2011 TO 2016]',
    },
    {
        "code": "TAS",
        "name": "University of Tasmania",
        "query": '(nuc:"TU:IR" OR "utas.edu.au") date:[2011 TO 2016]',
    },
    {
        "code": "GRI",
        "name": "Griffith University",
        "query": '(nuc:"QGU:IR" OR "griffith.edu.au") date:[2011 TO 2016]',
    },
    {
        "code": "CUR",
        "name": "Curtin University of Technology",
        "query": '("curtin.edu.au" OR nuc:"WCU:IR") date:[2011 TO 2016]',
    },
    {
        "code": "QUT",
        "name": "Queensland University of Technology",
        "query": '(nuc:"QUT:IR" OR "qut.edu.au") date:[2011 TO 2016]',
    },
    {
        "code": "DEA",
        "name": "Deakin University",
        "query": '(nuc:"VDU:IR" OR "deakin.edu.au") date:[2011 TO 2016]',
    },
    {
        "code": "ADE",
        "name": "The University of Adelaide",
        "query": '(nuc:"SAU:IR" OR "adelaide.edu.au") date:[2011 TO 2016]',
    },
    {
        "code": "ANU",
        "name": "The Australian National University",
        "query": '(nuc:"ANU:IR" OR "anu.edu.au") date:[2011 TO 2016]',
    },
    {
        "code": "UQ",
        "name": "The University of Queensland",
        "query": '(nuc:"QU:IR" OR "uq.edu.au") date:[2011 TO 2016]',
    },
]


def extract_work_record(work: Dict[str, Any], inst_code: str, inst_name: str) -> Dict[str, Any]:
    """Parse raw Trove work JSON item into structured row."""
    work_id = str(work.get("id", ""))
    title = work.get("title", "")
    trove_url = work.get("troveUrl", "")
    issued = work.get("issued", "")
    type_list = work.get("type", [])
    primary_type = type_list[0] if type_list else None
    all_types = "; ".join(type_list) if isinstance(type_list, list) else str(type_list)

    # Contributors / Authors
    contribs = work.get("contributor", [])
    if isinstance(contribs, list):
        creators_str = "; ".join(contribs)
    else:
        creators_str = str(contribs) if contribs else None

    # Identifiers
    identifiers = work.get("identifier", [])
    doi = None
    handle = None
    isbn = None
    issn = None
    all_ids = []

    if isinstance(identifiers, list):
        for item in identifiers:
            if isinstance(item, dict):
                val = str(item.get("value", "")).strip()
                itype = str(item.get("type", "")).lower()
                all_ids.append(f"{itype}:{val}")
                if "doi.org" in val.lower() or val.startswith("10."):
                    m = re.search(r"(10\.\d{4,9}/[-._;()/:A-Za-z0-9]+)", val)
                    if m:
                        doi = m.group(1).rstrip(".")
                if "hdl.handle.net" in val.lower() or "handle" in itype:
                    handle = val
                if itype == "isbn" and not isbn:
                    isbn = val
                if itype == "issn" and not issn:
                    issn = val

    return {
        "institution_code": inst_code,
        "institution_name": inst_name,
        "trove_work_id": work_id,
        "title": title,
        "issued": str(issued) if issued else None,
        "primary_type": primary_type,
        "all_types": all_types,
        "creators": creators_str,
        "doi": doi,
        "handle": handle,
        "isbn": isbn,
        "issn": issn,
        "all_identifiers": json.dumps(all_ids) if all_ids else None,
        "holdings_count": int(work.get("holdingsCount", 0)),
        "version_count": int(work.get("versionCount", 0)),
        "trove_url": trove_url,
    }


def harvest_institution(
    inst: Dict[str, str],
    api_key: str,
    page_size: int = 100,
    delay_sec: float = 0.35,
) -> Path:
    """Harvest all records for an institution using cursor pagination without deduplication."""
    code = inst["code"]
    name = inst["name"]
    query = inst["query"]
    out_parquet = HARVEST_DIR / f"hep_{code.lower()}.parquet"

    logger.info("Starting harvest for [%s] %s | Query: %s", code, name, query)

    headers = {"X-API-KEY": api_key}
    cursor = "*"
    records: List[Dict[str, Any]] = []
    total_expected: Optional[int] = None
    page = 1

    while True:
        params: Dict[str, Any] = {
            "q": query,
            "category": "research",
            "encoding": "json",
            "n": page_size,
            "s": cursor,
        }

        cache_key = f"{code}_{query}_{cursor}"
        data = None

        if cache_key in trove_cache:
            data = trove_cache[cache_key]
        else:
            try:
                resp = requests.get(API_URL, headers=headers, params=params, timeout=30)
                if resp.status_code != 200:
                    logger.error("HTTP %s on page %d for %s: %s", resp.status_code, page, code, resp.text[:200])
                    time.sleep(2.0)
                    continue

                data = resp.json()
                trove_cache[cache_key] = data
                time.sleep(delay_sec)
            except Exception as e:
                logger.error("Exception during harvest of %s page %d: %s", code, page, e)
                time.sleep(2.0)
                continue

        cats = data.get("category", []) if isinstance(data, dict) else []
        if not cats:
            logger.warning("No category in response for %s page %d", code, page)
            break

        records_block = cats[0].get("records", {})
        if total_expected is None:
            total_expected = records_block.get("total", 0)
            logger.info("[%s] Target records: %d", code, total_expected)

        works = records_block.get("work", [])
        if not works:
            break

        for w in works:
            records.append(extract_work_record(w, code, name))

        next_cursor = records_block.get("nextStart")
        if not next_cursor or next_cursor == cursor or len(works) < page_size:
            break

        cursor = next_cursor
        page += 1

    # Save to Parquet via PyArrow
    logger.info("[%s] Harvested %d raw records. Writing to %s", code, len(records), out_parquet)
    if records:
        import pyarrow as pa
        import pyarrow.parquet as pq
        pa_table = pa.Table.from_pylist(records)
        pq.write_table(pa_table, str(out_parquet), compression="ZSTD")
    else:
        logger.warning("[%s] No records harvested to write.", code)

    logger.info("[%s] Successfully saved %s", code, out_parquet.name)
    return out_parquet


def run_trial_harvest():
    """Run trial extraction across the 10 smallest institutions."""
    api_key = get_trove_api_key()
    logger.info("Initializing trial harvest for %d institutions...", len(TRIAL_INSTITUTIONS))

    start_time = time.time()
    results = []

    for inst in TRIAL_INSTITUTIONS:
        t0 = time.time()
        p = harvest_institution(inst, api_key)
        elapsed = time.time() - t0

        # Quick summary stats
        con = duckdb.connect()
        cnt = con.execute(f"SELECT COUNT(*) FROM read_parquet('{p}')").fetchone()[0]
        has_doi = con.execute(f"SELECT COUNT(*) FROM read_parquet('{p}') WHERE doi IS NOT NULL").fetchone()[0]
        formats = con.execute(
            f"SELECT primary_type, COUNT(*) as n FROM read_parquet('{p}') GROUP BY primary_type ORDER BY n DESC LIMIT 3"
        ).fetchall()
        con.close()

        results.append({
            "code": inst["code"],
            "name": inst["name"],
            "count": cnt,
            "has_doi": has_doi,
            "top_formats": formats,
            "elapsed_s": round(elapsed, 1),
        })

    total_elapsed = round(time.time() - start_time, 1)
    logger.info("Completed trial harvest across %d institutions in %ss.", len(TRIAL_INSTITUTIONS), total_elapsed)

    print("\n" + "=" * 80)
    print("TRIAL HARVEST SUMMARY (10 SMALLEST HEPS - NO DEDUPLICATION)")
    print("=" * 80)
    for r in results:
        fmt_str = ", ".join([f"{f[0]}: {f[1]}" for f in r["top_formats"]])
        print(f"[{r['code']}] {r['name']:36} | {r['count']:,} records | {r['has_doi']:,} DOIs | {r['elapsed_s']}s")
        print(f"      Top Formats: {fmt_str}")
    print("=" * 80)


if __name__ == "__main__":
    run_trial_harvest()
