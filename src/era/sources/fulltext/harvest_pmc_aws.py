"""Harvest open-access full-text PDFs from the PMC Open Access Subset on AWS Open Data.

AWS S3 Open Data hosts millions of biomedical and life sciences papers freely under
https://pmc-oa-opendata.s3.amazonaws.com/PMC{id}.1/PMC{id}.1.pdf at strictly $0.00 cost,
no credentials, and unmetered throughput.
"""

from __future__ import annotations

import argparse
import concurrent.futures
from dataclasses import dataclass
import os
from pathlib import Path
import re
import sys
import time
import urllib.error
import urllib.request

import duckdb

PDF_DIR = Path("/home/lc/k/era_oa_pdfs")
S3_BASE_URL = "https://pmc-oa-opendata.s3.amazonaws.com"


@dataclass
class PmcCandidate:
    oax_id: int
    pmc_num: str
    landing_url: str
    dest_path: Path


def extract_pmc_num(url: str) -> str | None:
    """Extract numeric PMC ID from NCBI PMC or Europe PMC URL."""
    if not url:
        return None
    # match patterns like articles/PMC12345 or articles/12345 or articles/PMC12345.pdf
    m = re.search(r"articles/(?:PMC)?([0-9]+)", url, re.IGNORECASE)
    if m:
        return m.group(1)
    m = re.search(r"PMC([0-9]+)", url, re.IGNORECASE)
    if m:
        return m.group(1)
    return None


def fetch_pmc_candidates(
    locations_parquet: str | Path = "data/oa_locations.parquet",
    pdf_dir: Path = PDF_DIR,
    limit: int = 128,
) -> list[PmcCandidate]:
    """Find works with PMC locations that are not yet downloaded on disk."""
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
      AND (
        l.landing_page_url ILIKE '%ncbi.nlm.nih.gov/pmc/articles/%'
        OR l.landing_page_url ILIKE '%europepmc.org/articles/%'
        OR l.pdf_url ILIKE '%ncbi.nlm.nih.gov/pmc/articles/%'
      )
    """
    rows = conn.execute(query).fetchall()

    candidates: list[PmcCandidate] = []
    seen_oax = set()
    for oax_id, url in rows:
        if oax_id in seen_oax:
            continue
        pmc_num = extract_pmc_num(url)
        if not pmc_num:
            continue
        dest = pdf_dir / f"{oax_id}.pdf"
        if dest.exists() and dest.stat().st_size > 1024:
            continue
        seen_oax.add(oax_id)
        candidates.append(
            PmcCandidate(
                oax_id=oax_id,
                pmc_num=pmc_num,
                landing_url=url,
                dest_path=dest,
            )
        )
    return candidates


def download_single_pmc(cand: PmcCandidate, timeout: int = 15) -> tuple[bool, int, str]:
    """Attempt download of PMC PDF from AWS Open Data (.1 then .2).
    
    Returns (success, bytes_downloaded, message).
    """
    if cand.dest_path.exists() and cand.dest_path.stat().st_size > 1024:
        return True, cand.dest_path.stat().st_size, "already_exists"

    versions = ["1", "2"]
    for v in versions:
        url = f"{S3_BASE_URL}/PMC{cand.pmc_num}.{v}/PMC{cand.pmc_num}.{v}.pdf"
        req = urllib.request.Request(
            url,
            headers={
                "User-Agent": "ERA-Research/1.0 (https://github.com/LarryCram/Bibliometric_disparities; open access bibliometrics)"
            },
        )
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                if resp.status == 200:
                    content = resp.read()
                    if content.startswith(b"%PDF") and len(content) >= 1024:
                        tmp_path = cand.dest_path.with_suffix(".tmp")
                        tmp_path.write_bytes(content)
                        tmp_path.replace(cand.dest_path)
                        return True, len(content), f"v{v}_success"
        except urllib.error.HTTPError as e:
            if e.code == 404:
                continue
            return False, 0, f"http_err_{e.code}"
        except Exception as e:
            return False, 0, str(e)

    return False, 0, "not_in_oa_subset_404"


def harvest_pmc_aws(
    target_count: int = 128,
    max_workers: int = 8,
    pdf_dir: Path = PDF_DIR,
) -> tuple[int, int, int]:
    """Harvest up to target_count PDFs from PMC AWS Open Data concurrently.
    
    Returns (success_count, not_found_count, total_bytes).
    """
    candidates = fetch_pmc_candidates(pdf_dir=pdf_dir)
    print(f"Found {len(candidates)} pending PMC works to scan for AWS Open Data full texts.")

    success_count = 0
    not_found_count = 0
    total_bytes = 0
    start_time = time.time()

    with concurrent.futures.ThreadPoolExecutor(max_workers=max_workers) as executor:
        cand_iter = iter(candidates)
        future_to_cand = {}

        # Prime initial batch of tasks
        batch_size = max_workers * 4
        for _ in range(batch_size):
            try:
                c = next(cand_iter)
                future_to_cand[executor.submit(download_single_pmc, c)] = c
            except StopIteration:
                break

        while future_to_cand and success_count < target_count:
            done, _ = concurrent.futures.wait(
                future_to_cand.keys(),
                return_when=concurrent.futures.FIRST_COMPLETED,
            )
            for f in done:
                cand = future_to_cand.pop(f)
                try:
                    ok, nbytes, msg = f.result()
                    if ok:
                        success_count += 1
                        total_bytes += nbytes
                        mb = nbytes / (1024 * 1024)
                        print(
                            f"[{success_count:3d}/{target_count}] SUCCESS: OAX {cand.oax_id} "
                            f"(PMC{cand.pmc_num}) -> {mb:.2f} MB ({msg})"
                        )
                    else:
                        not_found_count += 1
                except Exception as e:
                    not_found_count += 1

                # If we still need more successes, enqueue the next candidate
                if success_count < target_count:
                    try:
                        next_c = next(cand_iter)
                        future_to_cand[executor.submit(download_single_pmc, next_c)] = next_c
                    except StopIteration:
                        pass

    elapsed = time.time() - start_time
    total_mb = total_bytes / (1024 * 1024)
    print("\n" + "=" * 60)
    print(f"PMC AWS Harvest Completed in {elapsed:.2f}s")
    print(f"Successfully downloaded: {success_count} PDFs ({total_mb:.2f} MB)")
    print(f"Not in OA subset / 404: {not_found_count}")
    if (success_count + not_found_count) > 0:
        hit_rate = (success_count / (success_count + not_found_count)) * 100
        print(f"OA Subset Hit Rate: {hit_rate:.1f}%")
    print(f"Total PDFs in {pdf_dir}: {len(list(pdf_dir.glob('*.pdf'))):,}")
    print("=" * 60)

    return success_count, not_found_count, total_bytes


def main():
    parser = argparse.ArgumentParser(description="Harvest open-access PDFs from PMC AWS Open Data.")
    parser.add_argument("--count", type=int, default=128, help="Number of PDFs to retrieve (default: 128)")
    parser.add_argument("--workers", type=int, default=8, help="Concurrent worker threads (default: 8)")
    args = parser.parse_args()

    harvest_pmc_aws(target_count=args.count, max_workers=args.workers)


if __name__ == "__main__":
    main()
