"""Download open-access PDFs for ERA-matched OpenAlex works, using the
best_oa_pdf_url column produced by add_oa_urls.py.

Downloads are deduplicated by openalex_id (several ERA records - e.g. the
same paper reported by multiple universities - can point at the same work,
and there's no reason to fetch the same PDF twice).

SAMPLE_SIZE caps this to a quick timed batch for estimating full-run time;
set it to None to download everything. Uses a thread pool since this is a
network-latency-bound workload spread across many different hosts (figshare,
institutional repositories, publisher mirrors, etc.) - a single host getting
hit hard is unlikely given how varied OA sources are.
"""

import argparse
import random
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from urllib.parse import urlparse

import duckdb
import requests

from era.config import DATA_DIR, PDF_DIR

MATCHES_WITH_OA = DATA_DIR / "era_openalex_matches_with_oa.parquet"
OUTPUT_DIR = PDF_DIR

DEFAULT_CONCURRENCY = 12
TIMEOUT = (5, 15)  # (connect, read) seconds
MIN_VALID_SIZE = 2048  # bytes - below this, treat as a stub/error response rather than a real PDF
HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36 "
        "(Academic Research; Bibliometric Disparities Project; mailto:larry.cram@anu.edu.au)"
    )
}


def fetch_pending_targets(limit: int | None = None) -> list[tuple[int, str]]:
    """Fetch un-downloaded targets with direct OA PDF URLs, prioritizing friction-free open domains."""
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    existing_ids = {int(p.stem) for p in OUTPUT_DIR.glob("*.pdf") if p.stem.isdigit()}

    con = duckdb.connect()
    query = f"""
        SELECT openalex_id, ANY_VALUE(best_oa_pdf_url) AS pdf_url
        FROM '{MATCHES_WITH_OA}'
        WHERE best_oa_pdf_url IS NOT NULL
        GROUP BY openalex_id
        ORDER BY openalex_id
    """
    all_targets = con.execute(query).fetchall()
    con.close()

    pending = [(int(r[0]), str(r[1])) for r in all_targets if int(r[0]) not in existing_ids]

    # Prioritize friction-free open domains (arXiv, PLOS, Frontiers, MDPI, BMC, University Repositories)
    def domain_priority(item: tuple[int, str]) -> tuple[int, float]:
        url = item[1].lower()
        # Tier 0: Pure Gold / Open Preprints
        if any(d in url for d in ("arxiv.org", "journals.plos.org", "frontiersin.org", "mdpi.com", "biomedcentral.com", "europepmc.org")):
            prio = 0
        # Tier 1: Australian University Repositories
        elif any(d in url for d in ("edu.au", "anu.edu", "usyd.edu", "unsw.edu", "uq.edu", "unimelb.edu", "curtin.edu", "qut.edu", "monash.edu", "adelaide.edu")):
            prio = 1
        # Tier 2: Other Open Repositories
        elif any(d in url for d in ("zenodo.org", "figshare.com", "osf.io", "biorxiv.org", "medrxiv.org", "researchgate.net")):
            prio = 2
        else:
            prio = 3
        # Jitter within tier to distribute load
        return (prio, random.random())

    random.seed(42)
    pending.sort(key=domain_priority)

    if limit is not None:
        return pending[:limit]
    return pending


def download_one(session, openalex_id, url):
    dest = OUTPUT_DIR / f"{openalex_id}.pdf"
    if dest.exists() and dest.stat().st_size > 0:
        return openalex_id, "skipped (exists)", 0, 0.0
    t0 = time.time()
    try:
        resp = session.get(url, headers=HEADERS, timeout=TIMEOUT, stream=True)
        resp.raise_for_status()
        tmp = dest.with_suffix(".part")
        n_bytes = 0
        with open(tmp, "wb") as f:
            for chunk in resp.iter_content(chunk_size=1 << 16):
                f.write(chunk)
                n_bytes += len(chunk)
        # OpenAlex's pdf_url is frequently a landing page in disguise - a 200
        # response with an HTML/other body, not an actual PDF - or a dropped
        # connection that leaves only a stub. Check the header magic bytes,
        # a minimum size, and a trailing %%EOF marker (a well-formed PDF's
        # cross-reference trailer) rather than trusting the URL alone.
        with open(tmp, "rb") as f:
            magic = f.read(5)
            f.seek(-min(1024, n_bytes), 2)
            tail = f.read()
        if magic != b"%PDF-" or n_bytes < MIN_VALID_SIZE or b"%%EOF" not in tail:
            tmp.unlink()
            return openalex_id, "not_pdf", n_bytes, time.time() - t0
        tmp.rename(dest)
        return openalex_id, "ok", n_bytes, time.time() - t0
    except Exception as e:
        return openalex_id, f"error: {type(e).__name__}: {e}", 0, time.time() - t0


def main():
    parser = argparse.ArgumentParser(description="Direct OA PDF Downloader")
    parser.add_argument("--limit", type=int, default=None, help="Limit number of PDFs to download")
    parser.add_argument("--workers", type=int, default=DEFAULT_CONCURRENCY, help="Concurrent workers")
    args = parser.parse_args()

    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

    print("Fetching pending direct OA targets...")
    targets = fetch_pending_targets(args.limit)
    print(f"Downloading {len(targets):,} pending PDFs to {OUTPUT_DIR} with {args.workers} workers...")

    if not targets:
        print("No pending targets to download.")
        return

    results = []
    t_start = time.time()
    n_done = 0
    with requests.Session() as session, ThreadPoolExecutor(max_workers=args.workers) as pool:
        futures = {pool.submit(download_one, session, oid, url): oid for oid, url in targets}
        for fut in as_completed(futures):
            results.append(fut.result())
            n_done += 1
            if n_done % 50 == 0 or n_done == len(targets):
                elapsed = time.time() - t_start
                rate = n_done / elapsed if elapsed > 0 else 0
                n_ok = sum(1 for r in results if r[1] == "ok")
                print(f"  [{n_done:,}/{len(targets):,}] Ok: {n_ok:,} ({n_ok/n_done*100:.1f}%), Rate: {rate:.1f} files/s")

    elapsed = time.time() - t_start
    ok = [r for r in results if r[1] == "ok"]
    not_pdf = [r for r in results if r[1] == "not_pdf"]
    skipped = [r for r in results if r[1].startswith("skipped")]
    failed = [r for r in results if r[1].startswith("error")]
    total_bytes = sum(r[2] for r in ok)

    print(f"\nDone in {elapsed:.1f}s: {len(ok):,} real PDFs, {len(not_pdf):,} not-actually-PDF, "
          f"{len(skipped):,} skipped, {len(failed):,} failed")
    if ok:
        print(f"  Total downloaded: {total_bytes / 1e6:.1f} MB, avg {total_bytes / len(ok) / 1e6:.2f} MB/file")
    if failed:
        print("  Sample failures:")
        for oid, status, _, _ in failed[:10]:
            print(f"    {oid}: {status}")


if __name__ == "__main__":
    main()
