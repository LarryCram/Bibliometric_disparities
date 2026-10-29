"""Careful, respectful harvester using Semantic Scholar API.

Queries missing ERA canonical works by DOI to discover openAccessPdf links
from institutional repositories and preprints that OpenAlex/Unpaywall missed.

Respects Semantic Scholar's free tier rate limit:
  - 1 request per second cumulative threshold (uses 1.25s delay)
  - Automatic exponential backoff on HTTP 429
  - Strict validation of downloaded PDFs (%PDF- magic bytes, >1KB size)
"""

import argparse
import glob
import os
import sys
import time
from pathlib import Path

import duckdb
import pyarrow as pa
import requests

from era.config import DATA_DIR, PDF_DIR

S2_API_URL = "https://api.semanticscholar.org/graph/v1/paper"
S2_KEY = os.getenv("S2_API_KEY", "s2k-QpkUJmWBEX4HWz7ajKe24RGHSjJvVqP21RtO590x")

HEADERS = {
    "x-api-key": S2_KEY,
    "User-Agent": "ERA-Bibliometrics-Research/1.0 (academic research)",
}

DOWNLOAD_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Accept": "application/pdf,application/octet-stream,*/*;q=0.8",
}


def load_missing_doi_candidates(limit=500):
    con = duckdb.connect()
    downloaded = [int(os.path.splitext(os.path.basename(p))[0]) for p in glob.glob(f"{PDF_DIR}/*.pdf")]
    arrow_table = pa.Table.from_arrays([pa.array(downloaded, type=pa.int64())], names=["oax_id"])
    con.register("downloaded", arrow_table)

    query = f"""
        SELECT 
            s.openalex_id,
            coalesce(s.oax_doi, s.era_doi) as doi,
            s.era_title
        FROM read_parquet("{DATA_DIR}/era_multi_index_synthesis.parquet") s
        LEFT JOIN downloaded d ON s.openalex_id = d.oax_id
        WHERE d.oax_id IS NULL 
          AND coalesce(s.oax_doi, s.era_doi) IS NOT NULL
        ORDER BY s.oax_cited_by_count DESC NULLS LAST
        LIMIT {limit}
    """
    rows = con.execute(query).fetchall()
    print(f"Loaded {len(rows)} prioritized missing works with DOIs for S2 lookup.")
    return rows


def download_pdf(pdf_url, dest_path):
    try:
        r = requests.get(pdf_url, headers=DOWNLOAD_HEADERS, timeout=20, stream=True)
        if r.status_code != 200:
            return False, f"http_{r.status_code}", 0

        chunk = r.raw.read(1024)
        if not chunk.startswith(b"%PDF-"):
            return False, "not_pdf_magic", 0

        tmp = dest_path.with_suffix(".tmp")
        with open(tmp, "wb") as f:
            f.write(chunk)
            for part in r.iter_content(chunk_size=65536):
                if part:
                    f.write(part)

        size = tmp.stat().st_size
        if size < 1024:
            tmp.unlink(missing_ok=True)
            return False, "too_small", 0

        tmp.rename(dest_path)
        return True, "ok", size
    except Exception as e:
        return False, f"dl_error_{type(e).__name__}", 0


def main():
    parser = argparse.ArgumentParser(description="Harvest missing PDFs via Semantic Scholar API")
    parser.add_argument("--limit", type=int, default=300, help="Max DOIs to query (default: 300)")
    args = parser.parse_args()

    PDF_DIR.mkdir(parents=True, exist_ok=True)
    candidates = load_missing_doi_candidates(limit=args.limit)
    if not candidates:
        print("No candidates found.")
        return

    session = requests.Session()
    s2_hits = 0
    downloaded_count = 0
    total_bytes = 0
    t0 = time.time()

    print(f"Querying Semantic Scholar for {len(candidates)} works (polite 1.25s delay between requests)...")

    for i, (oax_id, doi, title) in enumerate(candidates, 1):
        dest = PDF_DIR / f"{oax_id}.pdf"
        if dest.exists() and dest.stat().st_size > 1024:
            continue

        # Respect S2 rate limit
        time.sleep(1.25)

        for attempt in range(3):
            try:
                url = f"{S2_API_URL}/DOI:{doi}?fields=isOpenAccess,openAccessPdf"
                r = session.get(url, headers=HEADERS, timeout=12)
                if r.status_code == 429:
                    time.sleep(3.0 * (attempt + 1))
                    continue
                if r.status_code == 200:
                    data = r.json()
                    oa_pdf = data.get("openAccessPdf")
                    if oa_pdf and oa_pdf.get("url"):
                        s2_hits += 1
                        pdf_url = oa_pdf["url"]
                        success, reason, size = download_pdf(pdf_url, dest)
                        if success:
                            downloaded_count += 1
                            total_bytes += size
                            mb = total_bytes / (1024 * 1024)
                            print(f"[{i:>3}/{len(candidates)}] ✓ S2 PDF SAVED: {oax_id} ({size/1024:.1f} KB, total: {mb:.1f} MB) -> {pdf_url[:70]}")
                        else:
                            # print(f"[{i:>3}/{len(candidates)}] S2 hit but download failed ({reason}): {pdf_url[:60]}")
                            pass
                break
            except Exception as e:
                if attempt < 2:
                    time.sleep(2.0)
                    continue

        if i % 25 == 0:
            mb = total_bytes / (1024 * 1024)
            print(f"--- Progress: {i}/{len(candidates)} queried | S2 OA hits: {s2_hits} | PDFs saved: {downloaded_count} ({mb:.1f} MB) ---")

    elapsed = time.time() - t0
    print(f"\n============================================================")
    print(f"Semantic Scholar Harvest Completed in {elapsed:.1f}s")
    print(f"Queried: {len(candidates)} DOIs")
    print(f"S2 OA URLs discovered: {s2_hits}")
    print(f"Successfully downloaded: {downloaded_count} PDFs ({total_bytes/(1024*1024):.2f} MB)")
    print(f"Total PDFs in {PDF_DIR}: {len(glob.glob(f'{PDF_DIR}/*.pdf'))}")
    print(f"============================================================")


if __name__ == "__main__":
    main()
