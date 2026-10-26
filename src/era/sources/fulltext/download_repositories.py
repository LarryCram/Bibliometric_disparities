"""Automated Institutional Repository & Open Archive PDF Harvester.

Harvests un-downloaded Open Access ERA papers from candidate university repositories
and open preprint archives (QUT, JCU, Curtin, UNSW, ANU, UWA, arXiv, PMC, etc.)
directly into /home/lc/k/era_oa_pdfs/<oax_id>.pdf.

Key Features:
- Polite domain shuffling to disperse load across universities.
- Atomic file writing (<oax_id>.pdf.tmp -> <oax_id>.pdf).
- Verification of '%PDF-' byte magic and minimum size threshold.
- Progress checkpointing and resumption.
"""

import argparse
import json
import os
import random
import sys
import time
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

import duckdb

from era.config import DATA_DIR, K_DRIVE

MANIFEST_PARQUET = DATA_DIR / "candidate_repository_downloads.parquet"
TARGET_DIR = K_DRIVE / "era_oa_pdfs"
PROGRESS_FILE = DATA_DIR / "era_download_progress.json"

USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36 "
    "(Academic Research; Bibliometric Disparities Project; mailto:larry.cram@anu.edu.au)"
)


def download_single_pdf(record: dict) -> dict:
    """Download and validate a single PDF file atomically."""
    oax_id = record["oax_id"]
    pdf_url = record["pdf_url"]
    domain = record.get("domain", "")

    final_path = TARGET_DIR / f"{oax_id}.pdf"
    if final_path.exists() and final_path.stat().st_size > 4096:
        return {"oax_id": oax_id, "status": "skipped", "size": final_path.stat().st_size}

    tmp_path = TARGET_DIR / f"{oax_id}.pdf.tmp"
    headers = {
        "User-Agent": USER_AGENT,
        "Accept": "application/pdf,application/octet-stream,*/*;q=0.8",
    }

    req = urllib.request.Request(pdf_url, headers=headers)
    t0 = time.time()
    try:
        with urllib.request.urlopen(req, timeout=20) as resp:
            if resp.status != 200:
                return {"oax_id": oax_id, "status": "failed", "error": f"HTTP {resp.status}"}

            # Read content
            content = resp.read()
            if len(content) < 4096:
                return {"oax_id": oax_id, "status": "failed", "error": f"File too small ({len(content)} bytes)"}

            # Validate %PDF magic header
            if not content.startswith(b"%PDF"):
                return {"oax_id": oax_id, "status": "failed", "error": "Missing %PDF magic signature"}

            # Atomic write
            with open(tmp_path, "wb") as f:
                f.write(content)
            tmp_path.replace(final_path)

            return {
                "oax_id": oax_id,
                "status": "success",
                "size": len(content),
                "duration": round(time.time() - t0, 2),
                "domain": domain,
            }

    except urllib.error.HTTPError as e:
        if tmp_path.exists():
            tmp_path.unlink()
        return {"oax_id": oax_id, "status": "failed", "error": f"HTTP {e.code}"}
    except Exception as e:
        if tmp_path.exists():
            tmp_path.unlink()
        return {"oax_id": oax_id, "status": "failed", "error": str(e)}


def run_harvest(
    manifest_path: Path = MANIFEST_PARQUET,
    limit: int | None = None,
    workers: int = 12,
) -> None:
    """Execute the parallel repository harvester."""
    if not manifest_path.exists():
        print(f"Error: manifest file {manifest_path} does not exist.")
        sys.exit(1)

    TARGET_DIR.mkdir(parents=True, exist_ok=True)
    con = duckdb.connect()

    q = f"SELECT oax_id, pdf_url, domain, era_title FROM '{manifest_path}'"
    if limit:
        q += f" LIMIT {limit}"
    
    rows = con.execute(q).fetchall()
    con.close()

    records = [
        {"oax_id": r[0], "pdf_url": r[1], "domain": r[2], "title": r[3]}
        for r in rows
    ]

    # Filter out already existing on disk
    pending = [
        rec for rec in records 
        if not (TARGET_DIR / f"{rec['oax_id']}.pdf").exists()
    ]

    print(f"Loaded {len(records):,} records from manifest.")
    print(f"Already downloaded: {len(records) - len(pending):,}. Pending harvest: {len(pending):,}.")

    if not pending:
        print("All records already present on disk.")
        return

    # Shuffle to distribute domain load politely across university repositories
    random.seed(42)
    random.shuffle(pending)

    print(f"Starting harvest with {workers} workers across {len(pending):,} files...")
    t0 = time.time()
    n_success = 0
    n_failed = 0
    n_bytes = 0

    with ThreadPoolExecutor(max_workers=workers) as executor:
        futures = {executor.submit(download_single_pdf, rec): rec for rec in pending}
        
        for i, fut in enumerate(as_completed(futures), 1):
            res = fut.result()
            if res["status"] == "success":
                n_success += 1
                n_bytes += res["size"]
            else:
                n_failed += 1

            if i % 50 == 0 or i == len(pending):
                rate = i / (time.time() - t0)
                mb_downloaded = n_bytes / (1024 * 1024)
                pct_succ = (n_success / i) * 100.0
                print(
                    f"[{i:,}/{len(pending):,}] Success: {n_success:,} ({pct_succ:.1f}%), "
                    f"Failed: {n_failed:,}, Size: {mb_downloaded:.1f} MB, Rate: {rate:.1f} files/s"
                )

    print("\nHarvest finished.")
    print(f"Total Success: {n_success:,} | Total Failed: {n_failed:,} in {time.time()-t0:.1f}s")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Institutional Repository PDF Harvester")
    parser.add_argument("--limit", type=int, default=None, help="Limit number of files to download (for testing)")
    parser.add_argument("--workers", type=int, default=12, help="Number of concurrent workers (default 12)")
    args = parser.parse_args()

    run_harvest(limit=args.limit, workers=args.workers)
