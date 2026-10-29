"""Comprehensive, high-throughput harvester for all non-paywalled open access repositories.

Extracts all candidate direct PDF URLs from data/oa_locations.parquet across
hundreds of global and Australian open institutional repositories, disciplinary archives,
and open-access society publishers.

Features:
- Excludes gated commercial publisher domains (Wiley, Springer, Elsevier, etc.)
- Domain-level circuit breaker: skips a domain after 3 consecutive failures to avoid stalling
- Strict %PDF- binary header validation and minimum size check (>2 KB)
- Concurrent multi-worker thread pool with polite timeouts
"""

import argparse
import glob
import os
import sys
import time
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from urllib.parse import urlparse

import duckdb
import pyarrow as pa
import requests

from era.config import DATA_DIR, PDF_DIR

PAYWALLED_DOMAINS = [
    'onlinelibrary.wiley.com',
    'academic.oup.com',
    'tandfonline.com',
    'journals.sagepub.com',
    'sciencedirect.com',
    'nature.com',
    'springer.com',
    'royalsocietypublishing.org',
    'ahajournals.org',
    'ashpublications.org',
    'jimmunol.org',
    'link.aps.org',
    'cambridge.org',
    'pnas.org',
    'pubs.rsc.org',
    'pubs.aip.org',
    'cell.com',
    'thelancet.com'
]

HEADERS = {
    'User-Agent': (
        'Mozilla/5.0 (Windows NT 10.0; Win64; x64) '
        'AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36 '
        '(Academic Research; Open Access Harvester; mailto:larry.cram@anu.edu.au)'
    ),
    'Accept': 'application/pdf,application/octet-stream,*/*;q=0.8',
}


def load_open_access_candidates(limit=0):
    """Load distinct missing works with direct PDF URLs from non-paywalled domains."""
    con = duckdb.connect()
    downloaded = [int(os.path.splitext(os.path.basename(p))[0]) for p in glob.glob(f"{PDF_DIR}/*.pdf")]
    arrow_table = pa.Table.from_arrays([pa.array(downloaded, type=pa.int64())], names=['oax_id'])
    con.register('downloaded', arrow_table)

    paywalled_sql = ' OR '.join([f"l.pdf_url LIKE '%{p}%'" for p in PAYWALLED_DOMAINS])

    query = f"""
        SELECT 
            l.oax_id,
            l.pdf_url,
            regexp_extract(l.pdf_url, 'https?://([^/]+)', 1) as domain
        FROM read_parquet("{DATA_DIR}/oa_locations.parquet") l
        LEFT JOIN downloaded d ON l.oax_id = d.oax_id
        WHERE d.oax_id IS NULL 
          AND l.is_oa = TRUE 
          AND l.pdf_url IS NOT NULL
          AND NOT ({paywalled_sql})
    """
    if limit > 0:
        query += f" LIMIT {limit}"

    rows = con.execute(query).fetchall()
    print(f"Loaded {len(rows)} candidate links across open access repositories.")
    return rows


def download_single(session, oax_id, pdf_url, domain, domain_failures, max_consecutive_fails=3):
    if domain_failures.get(domain, 0) >= max_consecutive_fails:
        return oax_id, "domain_skipped", 0, domain

    dest = PDF_DIR / f"{oax_id}.pdf"
    if dest.exists() and dest.stat().st_size > 2048:
        return oax_id, "already_exists", 0, domain

    try:
        r = session.get(pdf_url, headers=HEADERS, timeout=(5, 15), stream=True)
        if r.status_code != 200:
            domain_failures[domain] = domain_failures.get(domain, 0) + 1
            return oax_id, f"http_{r.status_code}", 0, domain

        chunk = r.raw.read(1024)
        if not chunk.startswith(b"%PDF-"):
            domain_failures[domain] = domain_failures.get(domain, 0) + 1
            return oax_id, "not_pdf_magic", 0, domain

        tmp = dest.with_suffix(".tmp")
        with open(tmp, "wb") as f:
            f.write(chunk)
            for part in r.iter_content(chunk_size=65536):
                if part:
                    f.write(part)

        size = tmp.stat().st_size
        if size < 2048:
            tmp.unlink(missing_ok=True)
            domain_failures[domain] = domain_failures.get(domain, 0) + 1
            return oax_id, "too_small", 0, domain

        tmp.rename(dest)
        # Reset domain failure counter on success
        domain_failures[domain] = 0
        return oax_id, "ok", size, domain
    except Exception as e:
        domain_failures[domain] = domain_failures.get(domain, 0) + 1
        return oax_id, f"err_{type(e).__name__}", 0, domain


def main():
    parser = argparse.ArgumentParser(description="Harvest all non-paywalled open access repositories")
    parser.add_argument("--limit", type=int, default=0, help="Max candidate links to evaluate (0 for all)")
    parser.add_argument("--workers", type=int, default=12, help="Worker threads (default: 12)")
    args = parser.parse_args()

    PDF_DIR.mkdir(parents=True, exist_ok=True)
    candidates = load_open_access_candidates(limit=args.limit)
    if not candidates:
        print("No pending open access candidates found.")
        return

    # Deduplicate candidates by oax_id, keeping the first candidate URL
    dedup = {}
    for oax_id, url, dom in candidates:
        if oax_id not in dedup:
            dedup[oax_id] = (url, dom)
    unique_candidates = [(oid, url, dom) for oid, (url, dom) in dedup.items()]
    print(f"Deduplicated to {len(unique_candidates)} distinct pending works.")

    session = requests.Session()
    domain_failures = defaultdict(int)

    success = 0
    skipped = 0
    failed = 0
    total_bytes = 0
    t0 = time.time()

    print(f"Launching harvest across {args.workers} workers...")

    with ThreadPoolExecutor(max_workers=args.workers) as pool:
        futures = {
            pool.submit(download_single, session, oid, url, dom, domain_failures): oid
            for oid, url, dom in unique_candidates
        }
        done = 0
        for fut in as_completed(futures):
            done += 1
            oid, status, size, dom = fut.result()
            if status == "ok":
                success += 1
                total_bytes += size
            elif status == "already_exists":
                skipped += 1
            else:
                failed += 1

            if done % 100 == 0 or done == len(unique_candidates):
                elapsed = time.time() - t0
                rate = done / elapsed if elapsed > 0 else 0
                mb = total_bytes / (1024 * 1024)
                print(f"[{done:>5}/{len(unique_candidates)}] {success} ok, {skipped} skipped, {failed} failed ({mb:.1f} MB, {rate:.1f} items/s)")

    elapsed = time.time() - t0
    print(f"\n============================================================")
    print(f"Harvest Complete in {elapsed:.1f}s")
    print(f"Total processed: {done}")
    print(f"Successfully downloaded: {success} PDFs ({total_bytes/(1024*1024):.2f} MB)")
    print(f"Skipped / Already downloaded: {skipped}")
    print(f"Failed / Gated: {failed}")
    print(f"Total PDFs in {PDF_DIR}: {len(glob.glob(f'{PDF_DIR}/*.pdf'))}")
    print(f"============================================================")


if __name__ == "__main__":
    main()
