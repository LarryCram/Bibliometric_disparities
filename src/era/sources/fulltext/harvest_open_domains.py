"""Harvest genuine open access PDFs from verified non-blocking repositories and open publishers.

Targets domains discovered to stream raw %PDF- content without anti-bot blocks:
- QUT ePrints (eprints.qut.edu.au)
- Institute of Physics Open Access (iopscience.iop.org)
- Taylor & Francis Open API (api.taylorfrancis.com)
- Victoria University (vuir.vu.edu.au)
- Lancaster University (eprints.lancs.ac.uk)
- Australian Institute of Criminology (www.aic.gov.au)
- AgEcon Search (ageconsearch.umn.edu)
- Australian Robotics & Automation (www.araa.asn.au)
- ANU Open Research (openresearch-repository.anu.edu.au)
"""

import os
import sys
import time
import glob
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor, as_completed
import duckdb
import pyarrow as pa
import requests

from era.config import PDF_DIR, DATA_DIR

VERIFIED_DOMAINS = [
    'eprints.qut.edu.au',
    'iopscience.iop.org',
    'api.taylorfrancis.com',
    'vuir.vu.edu.au',
    'eprints.lancs.ac.uk',
    'www.aic.gov.au',
    'ageconsearch.umn.edu',
    'www.araa.asn.au',
    'openresearch-repository.anu.edu.au',
]

HEADERS = {
    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
    'Accept': 'application/pdf,application/octet-stream,*/*;q=0.8',
}


def load_candidates():
    """Identify missing works that have direct pdf_url on verified open domains."""
    con = duckdb.connect()
    downloaded = [int(os.path.splitext(os.path.basename(p))[0]) for p in glob.glob(f"{PDF_DIR}/*.pdf")]
    arrow_table = pa.Table.from_arrays([pa.array(downloaded, type=pa.int64())], names=['oax_id'])
    con.register('downloaded', arrow_table)

    domains_sql = ", ".join(f"'{d}'" for d in VERIFIED_DOMAINS)
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
          AND regexp_extract(l.pdf_url, 'https?://([^/]+)', 1) IN ({domains_sql})
    """
    rows = con.execute(query).fetchall()
    print(f"Loaded {len(rows)} candidates across verified open domains.")
    return rows


def download_one(session, oax_id, pdf_url, domain):
    dest = PDF_DIR / f"{oax_id}.pdf"
    if dest.exists() and dest.stat().st_size > 1024:
        return oax_id, "skipped", 0

    try:
        r = session.get(pdf_url, headers=HEADERS, timeout=20, stream=True)
        if r.status_code != 200:
            return oax_id, f"http_{r.status_code}", 0

        # Read first 1KB to verify magic bytes
        chunk = r.raw.read(1024)
        if not chunk.startswith(b"%PDF-"):
            return oax_id, "not_pdf", 0

        # Stream remainder to temp file
        tmp = dest.with_suffix(".tmp")
        with open(tmp, "wb") as f:
            f.write(chunk)
            for rest in r.iter_content(chunk_size=65536):
                if rest:
                    f.write(rest)

        size = tmp.stat().st_size
        if size < 1024:
            tmp.unlink(missing_ok=True)
            return oax_id, "too_small", 0

        tmp.rename(dest)
        return oax_id, "ok", size
    except Exception as e:
        return oax_id, f"error_{type(e).__name__}", 0


def main():
    PDF_DIR.mkdir(parents=True, exist_ok=True)
    candidates = load_candidates()
    if not candidates:
        print("No candidates found.")
        return

    session = requests.Session()
    success = 0
    skipped = 0
    failed = 0
    total_bytes = 0

    t0 = time.time()
    print(f"Starting harvest of {len(candidates)} works using 6 workers...")

    with ThreadPoolExecutor(max_workers=6) as pool:
        futures = {
            pool.submit(download_one, session, oax_id, url, dom): oax_id
            for oax_id, url, dom in candidates
        }
        done = 0
        for fut in as_completed(futures):
            done += 1
            oax_id, status, size = fut.result()
            if status == "ok":
                success += 1
                total_bytes += size
            elif status == "skipped":
                skipped += 1
            else:
                failed += 1

            if done % 50 == 0 or done == len(candidates):
                elapsed = time.time() - t0
                rate = done / elapsed if elapsed > 0 else 0
                mb = total_bytes / (1024 * 1024)
                print(f"[{done:>4}/{len(candidates)}] {success} ok, {failed} failed ({mb:.1f} MB, {rate:.1f} items/s)")

    print(f"\nFinished in {time.time()-t0:.1f}s: {success} downloaded, {skipped} skipped, {failed} failed.")
    print(f"Total volume: {total_bytes / (1024*1024):.2f} MB")


if __name__ == "__main__":
    main()
