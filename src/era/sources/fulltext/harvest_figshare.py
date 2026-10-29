"""Harvest open-access full-text PDFs from Figshare repositories via public REST API.

Queries https://api.figshare.com/v2/articles/{id} for pending ERA works with Figshare
locations and streams full-text PDFs from ndownloader.figshare.com/files/{id}.
"""

from __future__ import annotations

import argparse
import concurrent.futures
from dataclasses import dataclass
import json
import os
from pathlib import Path
import re
import sys
import time
import urllib.error
import urllib.request

import duckdb

PDF_DIR = Path("/home/lc/k/era_oa_pdfs")
API_BASE = "https://api.figshare.com/v2/articles"


@dataclass
class FigshareCandidate:
    oax_id: int
    fig_id: str
    landing_url: str
    dest_path: Path


def extract_figshare_id(url: str) -> str | None:
    """Extract numeric Figshare article ID from URL."""
    if not url:
        return None
    m = re.search(r"/([0-9]+)/?$", url)
    if m:
        return m.group(1)
    return None


def fetch_figshare_candidates(
    locations_parquet: str | Path = "data/oa_locations.parquet",
    pdf_dir: Path = PDF_DIR,
) -> list[FigshareCandidate]:
    """Find works with Figshare landing URLs not yet on disk."""
    conn = duckdb.connect()
    pdf_dir.mkdir(parents=True, exist_ok=True)

    query = f"""
    CREATE TEMP TABLE existing AS 
    SELECT TRY_CAST(regexp_extract(file, '([0-9]+)\\\\.pdf$', 1) AS BIGINT) as oax_id
    FROM glob('{pdf_dir}/*.pdf')
    WHERE TRY_CAST(regexp_extract(file, '([0-9]+)\\\\.pdf$', 1) AS BIGINT) IS NOT NULL;

    SELECT DISTINCT l.oax_id, l.landing_page_url
    FROM read_parquet('{locations_parquet}') l
    LEFT JOIN existing e ON l.oax_id = e.oax_id
    WHERE e.oax_id IS NULL
      AND l.landing_page_url ILIKE '%figshare.com/articles/%'
    """
    rows = conn.execute(query).fetchall()

    candidates: list[FigshareCandidate] = []
    seen_oax = set()
    for oax_id, url in rows:
        if oax_id in seen_oax:
            continue
        fig_id = extract_figshare_id(url)
        if not fig_id:
            continue
        dest = pdf_dir / f"{oax_id}.pdf"
        if dest.exists() and dest.stat().st_size > 1024:
            continue
        seen_oax.add(oax_id)
        candidates.append(
            FigshareCandidate(
                oax_id=oax_id,
                fig_id=fig_id,
                landing_url=url,
                dest_path=dest,
            )
        )
    return candidates


def download_single_figshare(cand: FigshareCandidate, timeout: int = 15) -> tuple[bool, int, str]:
    """Query Figshare API and download PDF if available.
    
    Returns (success, bytes_downloaded, message).
    """
    if cand.dest_path.exists() and cand.dest_path.stat().st_size > 1024:
        return True, cand.dest_path.stat().st_size, "already_exists"

    api_url = f"{API_BASE}/{cand.fig_id}"
    req = urllib.request.Request(
        api_url,
        headers={"User-Agent": "ERA-Research/1.0 (bibliometrics research; open access)"},
    )

    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            data = json.loads(resp.read().decode())
    except urllib.error.HTTPError as e:
        if e.code == 404:
            return False, 0, "article_404"
        elif e.code == 429:
            time.sleep(1.0)
            return False, 0, "rate_limited_429"
        return False, 0, f"http_err_{e.code}"
    except Exception as e:
        return False, 0, str(e)

    files = data.get("files", [])
    if not files:
        return False, 0, "no_files_attached"

    # Find PDF file
    pdf_files = [
        f for f in files
        if (f.get("name", "").lower().endswith(".pdf") or "pdf" in f.get("mimetype", "").lower())
        and not f.get("is_link_only", False)
    ]
    if not pdf_files:
        # Fallback to first non-link file if size > 10kb
        pdf_files = [f for f in files if not f.get("is_link_only", False) and f.get("size", 0) > 10240]
        if not pdf_files:
            return False, 0, "no_valid_pdf_files"

    target_file = pdf_files[0]
    dl_url = target_file.get("download_url")
    if not dl_url:
        return False, 0, "missing_download_url"

    dl_req = urllib.request.Request(
        dl_url,
        headers={"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"},
    )
    try:
        with urllib.request.urlopen(dl_req, timeout=timeout + 15) as dl_resp:
            content = dl_resp.read()
            if content.startswith(b"%PDF") and len(content) >= 1024:
                tmp_path = cand.dest_path.with_suffix(".tmp")
                tmp_path.write_bytes(content)
                tmp_path.replace(cand.dest_path)
                return True, len(content), "downloaded_pdf"
            else:
                return False, 0, f"invalid_content_type_{len(content)}_bytes"
    except Exception as e:
        return False, 0, f"dl_error_{e}"


def harvest_figshare(
    limit: int = 0,
    max_workers: int = 8,
    pdf_dir: Path = PDF_DIR,
) -> tuple[int, int, int]:
    """Harvest available full-text PDFs from Figshare concurrently."""
    candidates = fetch_figshare_candidates(pdf_dir=pdf_dir)
    print(f"Found {len(candidates)} pending Figshare candidate works to scan.", flush=True)

    if limit > 0:
        candidates = candidates[:limit]
        print(f"Limiting to first {len(candidates)} candidates.", flush=True)

    success_count = 0
    no_file_count = 0
    total_bytes = 0
    start_time = time.time()

    with concurrent.futures.ThreadPoolExecutor(max_workers=max_workers) as executor:
        future_to_cand = {executor.submit(download_single_figshare, c): c for c in candidates}
        n_done = 0

        for f in concurrent.futures.as_completed(future_to_cand):
            cand = future_to_cand[f]
            n_done += 1
            try:
                ok, nbytes, msg = f.result()
                if ok:
                    success_count += 1
                    total_bytes += nbytes
                    mb = nbytes / (1024 * 1024)
                    print(
                        f"[{success_count:4d}] SUCCESS: OAX {cand.oax_id} (Figshare {cand.fig_id}) -> {mb:.2f} MB ({msg})",
                        flush=True,
                    )
                else:
                    no_file_count += 1
            except Exception:
                no_file_count += 1

            if n_done % 100 == 0 or n_done == len(candidates):
                elapsed = time.time() - start_time
                rate = n_done / elapsed if elapsed > 0 else 0
                print(
                    f"  Progress: {n_done}/{len(candidates)} scanned ({success_count} PDFs retrieved, {rate:.1f} items/s)",
                    flush=True,
                )

    elapsed = time.time() - start_time
    total_mb = total_bytes / (1024 * 1024)
    print("\n" + "=" * 60, flush=True)
    print(f"Figshare Harvest Completed in {elapsed:.2f}s", flush=True)
    print(f"Successfully downloaded: {success_count} PDFs ({total_mb:.2f} MB)", flush=True)
    print(f"No attached file / 404: {no_file_count}", flush=True)
    print(f"Total PDFs in {pdf_dir}: {len(list(pdf_dir.glob('*.pdf'))):,}", flush=True)
    print("=" * 60, flush=True)

    return success_count, no_file_count, total_bytes


def main():
    parser = argparse.ArgumentParser(description="Harvest open-access PDFs from Figshare.")
    parser.add_argument("--limit", type=int, default=0, help="Max candidates to scan (0 for all)")
    parser.add_argument("--workers", type=int, default=8, help="Concurrent worker threads (default: 8)")
    args = parser.parse_args()

    harvest_figshare(limit=args.limit, max_workers=args.workers)


if __name__ == "__main__":
    main()
