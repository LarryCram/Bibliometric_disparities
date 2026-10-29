"""Run GROBID full-text extraction over the downloaded ERA OA PDFs.

GROBID 0.9.0 is running as a Docker container on localhost:8070 (started
manually - the docker CLI isn't accessible from this environment, so this
script just talks to the REST API). Output is TEI XML per paper, written
next to the source PDFs on the k drive.

Resumable: skips any PDF that already has a non-empty .tei.xml, so it's
safe to re-run while download_oa_pdfs_direct.py is still adding new PDFs to
PDF_DIR - just run this again later to pick up whatever's new.

Uses a thread pool since GROBID's Java service processes requests
concurrently rather than one-at-a-time; CONCURRENCY should stay comfortably
below the machine's core count (checked at 24 here) so GROBID's own
internal parallelism isn't starved.
"""

import sys
import time
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor, as_completed

import requests

from era.config import PDF_DIR, TEI_DIR

GROBID_URL = "http://localhost:8070/api/processFulltextDocument"
CONCURRENCY = 10


def process_one(session, pdf_path):
    dest = TEI_DIR / f"{pdf_path.stem}.tei.xml"
    if dest.exists() and dest.stat().st_size > 0:
        return pdf_path.stem, "skipped (exists)", 0.0
    t0 = time.time()
    for attempt in range(3):
        try:
            with open(pdf_path, "rb") as f:
                resp = session.post(
                    GROBID_URL,
                    files={"input": (pdf_path.name, f, "application/pdf")},
                    timeout=180,
                )
            if resp.status_code in (500, 503) and attempt < 2:
                time.sleep(2.0 * (attempt + 1))
                continue
            resp.raise_for_status()
            dest.write_text(resp.text, encoding="utf-8")
            return pdf_path.stem, "ok", time.time() - t0
        except Exception as e:
            if attempt < 2:
                time.sleep(2.0 * (attempt + 1))
                continue
            return pdf_path.stem, f"error: {type(e).__name__}: {e}", time.time() - t0
    return pdf_path.stem, "error: max retries exceeded", time.time() - t0


def main():
    import argparse
    import os

    parser = argparse.ArgumentParser(description="Run GROBID on unprocessed PDFs.")
    parser.add_argument("--workers", type=int, default=12, help="Worker threads (default: 12)")
    parser.add_argument("--limit", type=int, default=0, help="Max PDFs to process (0 for all)")
    args = parser.parse_args()

    concurrency = args.workers
    TEI_DIR.mkdir(parents=True, exist_ok=True)
    
    print("Scanning directories via os.scandir...", flush=True)
    existing_tei = {
        f.name[:-8] for f in os.scandir(TEI_DIR)
        if f.name.endswith(".tei.xml") and f.stat().st_size > 0
    }
    
    unprocessed = [
        Path(f.path) for f in os.scandir(PDF_DIR)
        if f.name.endswith(".pdf") and f.name[:-4] not in existing_tei and f.stat().st_size > 1024
    ]

    total_pdfs = len(existing_tei) + len(unprocessed)
    print(f"Total PDFs in repository: {total_pdfs:,}", flush=True)
    print(f"Already processed to TEI: {len(existing_tei):,}", flush=True)
    print(f"Unprocessed PDFs to run: {len(unprocessed):,} (using {concurrency} workers)...", flush=True)

    if not unprocessed:
        print("All PDFs are already converted to TEI XML!", flush=True)
        return

    if args.limit > 0:
        unprocessed = unprocessed[:args.limit]
        print(f"Limiting to first {len(unprocessed):,} PDFs.", flush=True)

    results = []
    t_start = time.time()
    with requests.Session() as session, ThreadPoolExecutor(max_workers=concurrency) as pool:
        futures = {pool.submit(process_one, session, p): p for p in unprocessed}
        n_done = 0
        for fut in as_completed(futures):
            result = fut.result()
            results.append(result)
            n_done += 1
            if n_done % 25 == 0 or n_done == len(unprocessed):
                elapsed = time.time() - t_start
                rate = n_done / elapsed if elapsed > 0 else 0
                remaining = (len(unprocessed) - n_done) / rate if rate > 0 else 0
                print(f"  [{n_done:5d}/{len(unprocessed)}] done ({elapsed:.0f}s elapsed, "
                      f"{rate:.2f} papers/sec, ~{remaining:.0f}s remaining)", flush=True)

    elapsed = time.time() - t_start
    ok = [r for r in results if r[1] == "ok"]
    skipped = [r for r in results if r[1].startswith("skipped")]
    failed = [r for r in results if r[1].startswith("error")]
    print(f"\nDone in {elapsed:.0f}s: {len(ok)} ok, {len(skipped)} skipped, {len(failed)} failed", flush=True)
    if failed:
        print("Sample failures:")
        for name, status, _ in failed[:10]:
            print(f"  {name}: {status}")
    if ok:
        avg_time = sum(r[2] for r in ok) / len(ok)
        print(f"Average processing time: {avg_time:.1f}s/paper (wall-clock throughput: "
              f"{len(results) / elapsed:.2f} papers/sec with {concurrency} workers)", flush=True)
    print(f"TEI XML written to {TEI_DIR}", flush=True)


if __name__ == "__main__":
    main()
