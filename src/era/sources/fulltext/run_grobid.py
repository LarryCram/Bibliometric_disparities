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

sys.path.insert(0, str(Path(__file__).parent.parent))
from config import PDF_DIR, TEI_DIR

GROBID_URL = "http://localhost:8070/api/processFulltextDocument"
CONCURRENCY = 8


def process_one(session, pdf_path):
    dest = TEI_DIR / f"{pdf_path.stem}.tei.xml"
    if dest.exists() and dest.stat().st_size > 0:
        return pdf_path.stem, "skipped (exists)", 0.0
    t0 = time.time()
    try:
        with open(pdf_path, "rb") as f:
            resp = session.post(
                GROBID_URL,
                files={"input": (pdf_path.name, f, "application/pdf")},
                timeout=120,
            )
        resp.raise_for_status()
        dest.write_text(resp.text, encoding="utf-8")
        return pdf_path.stem, "ok", time.time() - t0
    except Exception as e:
        return pdf_path.stem, f"error: {type(e).__name__}: {e}", time.time() - t0


def main():
    TEI_DIR.mkdir(parents=True, exist_ok=True)
    pdfs = sorted(PDF_DIR.glob("*.pdf"))
    print(f"Processing {len(pdfs)} PDFs through GROBID with {CONCURRENCY} workers...")

    results = []
    t_start = time.time()
    with requests.Session() as session, ThreadPoolExecutor(max_workers=CONCURRENCY) as pool:
        futures = {pool.submit(process_one, session, p): p for p in pdfs}
        n_done = 0
        for fut in as_completed(futures):
            result = fut.result()
            results.append(result)
            n_done += 1
            if n_done % 200 == 0:
                elapsed = time.time() - t_start
                print(f"  {n_done}/{len(pdfs)} done ({elapsed:.0f}s elapsed, "
                      f"{n_done / elapsed:.2f} papers/sec)")

    elapsed = time.time() - t_start
    ok = [r for r in results if r[1] == "ok"]
    skipped = [r for r in results if r[1].startswith("skipped")]
    failed = [r for r in results if r[1].startswith("error")]
    print(f"\nDone in {elapsed:.0f}s: {len(ok)} ok, {len(skipped)} skipped, {len(failed)} failed")
    if failed:
        print("Sample failures:")
        for name, status, _ in failed[:10]:
            print(f"  {name}: {status}")
    if ok:
        avg_time = sum(r[2] for r in ok) / len(ok)
        print(f"Average processing time: {avg_time:.1f}s/paper (wall-clock throughput: "
              f"{len(results) / elapsed:.2f} papers/sec with {CONCURRENCY} workers)")
    print(f"TEI XML written to {TEI_DIR}")


if __name__ == "__main__":
    main()
