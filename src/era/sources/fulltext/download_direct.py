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

import sys
import time
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor, as_completed

import duckdb
import requests

sys.path.insert(0, str(Path(__file__).parent.parent))
from config import PDF_DIR

DATA_DIR = Path(__file__).parent.parent / "data"
MATCHES_WITH_OA = DATA_DIR / "era_openalex_matches_with_oa.parquet"

OUTPUT_DIR = PDF_DIR

SAMPLE_SIZE = None  # None = download everything
CONCURRENCY = 8
TIMEOUT = (10, 30)  # (connect, read) seconds
MIN_VALID_SIZE = 2048  # bytes - below this, treat as a stub/error response rather than a real PDF
HEADERS = {
    "User-Agent": "ERA-OpenAlex-matching research script (mailto:lawrence.cram@gmail.com)"
}


def fetch_targets(limit):
    con = duckdb.connect()
    query = f"""
        SELECT openalex_id, ANY_VALUE(best_oa_pdf_url) AS pdf_url
        FROM read_parquet('{MATCHES_WITH_OA}')
        WHERE best_oa_pdf_url IS NOT NULL
        GROUP BY openalex_id
        ORDER BY openalex_id
    """
    if limit is not None:
        query += f" LIMIT {limit}"
    return con.execute(query).fetchall()


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
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

    targets = fetch_targets(SAMPLE_SIZE)
    total_distinct = fetch_targets(None)
    n_total_distinct = len(total_distinct)
    print(f"Downloading {len(targets)} PDFs (of {n_total_distinct} total distinct OA-PDF matches) "
          f"to {OUTPUT_DIR} with {CONCURRENCY} workers...")

    results = []
    t_start = time.time()
    with requests.Session() as session, ThreadPoolExecutor(max_workers=CONCURRENCY) as pool:
        futures = {pool.submit(download_one, session, oid, url): oid for oid, url in targets}
        for fut in as_completed(futures):
            results.append(fut.result())
    elapsed = time.time() - t_start

    ok = [r for r in results if r[1] == "ok"]
    not_pdf = [r for r in results if r[1] == "not_pdf"]
    skipped = [r for r in results if r[1].startswith("skipped")]
    failed = [r for r in results if r[1].startswith("error")]
    total_bytes = sum(r[2] for r in ok)

    print(f"\nDone in {elapsed:.1f}s: {len(ok)} real PDFs, {len(not_pdf)} not-actually-PDF "
          f"(200 OK but not %PDF- magic bytes, deleted), {len(skipped)} skipped, {len(failed)} failed")
    if ok:
        print(f"  Total downloaded: {total_bytes / 1e6:.1f} MB, avg {total_bytes / len(ok) / 1e6:.2f} MB/file")
    if failed:
        print("  Sample failures:")
        for oid, status, _, _ in failed[:10]:
            print(f"    {oid}: {status}")

    rate = len(targets) / elapsed if elapsed > 0 else 0
    pdf_yield = len(ok) / len(targets) if targets else 0
    print(f"\nThroughput: {rate:.2f} URLs/sec ({CONCURRENCY} concurrent workers), "
          f"real-PDF yield: {pdf_yield * 100:.0f}%")
    if rate > 0:
        est_seconds = n_total_distinct / rate
        print(f"Estimated time to attempt all {n_total_distinct} distinct OA-PDF matches: "
              f"{est_seconds / 3600:.1f} hours ({est_seconds / 60:.0f} min)")
        print(f"Estimated real PDFs obtained at this yield: {n_total_distinct * pdf_yield:.0f}")


if __name__ == "__main__":
    main()
