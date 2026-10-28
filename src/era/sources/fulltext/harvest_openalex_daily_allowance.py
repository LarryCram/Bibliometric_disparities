"""Harvest free daily allowance of full-text PDFs from OpenAlex Content API.

Targets Gold OA journal works behind Cloudflare/gated platforms (Wiley, OUP, T&F,
SAGE, ScienceDirect, etc.) that are not yet downloaded on disk.
Leverages the $1.00 USD/day free allowance provided by OpenAlex (100 free PDFs/day).
Stops gracefully when the free daily allowance is exhausted (remaining USD reaches 0.00).
"""

import argparse
import json
import os
import sys
import time
import urllib.request
from pathlib import Path
from typing import Any

import duckdb

from era.config import DATA_DIR, K_DRIVE
from era.sources.fulltext.playwright_harvester import is_valid_pdf_file

TARGET_DIR = K_DRIVE / "era_oa_pdfs"

GATED_PATTERNS = [
    "onlinelibrary.wiley.com",
    "academic.oup.com",
    "tandfonline.com",
    "journals.sagepub.com",
    "sciencedirect.com",
    "hindawi.com",
    "nature.com",
    "springer.com",
    "royalsocietypublishing.org",
    "ahajournals.org",
    "ashpublications.org",
    "jimmunol.org",
    "link.aps.org",
    "cambridge.org",
    "pnas.org",
]


def load_env_credentials() -> tuple[str, str]:
    """Load OpenAlex API key and email from .env."""
    env = {}
    env_file = Path(".env")
    if env_file.exists():
        for line in env_file.read_text().splitlines():
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, v = line.split("=", 1)
                env[k.strip()] = v.strip().strip("\"'")
    
    api_key = env.get("OPENALEX_API_KEY", os.environ.get("OPENALEX_API_KEY", ""))
    email = env.get("OPENALEX_EMAIL", os.environ.get("OPENALEX_EMAIL", "test@example.com"))
    return api_key, email


def fetch_pending_gated_works(limit: int = 2000) -> list[dict[str, Any]]:
    """Find Gold OA works behind gated commercial platforms not yet on disk."""
    TARGET_DIR.mkdir(parents=True, exist_ok=True)
    existing_ids = {int(p.stem) for p in TARGET_DIR.glob("*.pdf") if p.stem.isdigit()}

    con = duckdb.connect()
    clauses = [f"l.pdf_url ILIKE '%{p}%'" for p in GATED_PATTERNS]
    where_gated = " OR ".join(clauses)

    rows = con.execute(f"""
        SELECT DISTINCT 
            l.oax_id, 
            l.source_name as journal, 
            m.era_title as title
        FROM 'data/oa_locations.parquet' l
        JOIN 'data/era_openalex_canonical_matches.parquet' m ON l.oax_id = m.openalex_id
        WHERE l.source_type = 'journal' AND l.is_oa = true AND l.pdf_url IS NOT NULL
          AND ({where_gated})
        ORDER BY l.oax_id DESC
    """).fetchall()

    pending = []
    for r in rows:
        oid = r[0]
        if oid not in existing_ids:
            pending.append({
                "oax_id": oid,
                "journal": r[1] or "Unknown",
                "title": r[2] or "Untitled",
            })
            if len(pending) >= limit:
                break
    return pending


def check_content_availability_batch(
    candidates: list[dict[str, Any]],
    api_key: str,
    email: str,
    batch_size: int = 50,
) -> list[dict[str, Any]]:
    """Batch-check OpenAlex Works API to find candidate works with cached PDF content."""
    cached_candidates = []
    headers = {"User-Agent": f"mailto:{email}"}
    
    total_batches = (len(candidates) + batch_size - 1) // batch_size
    print(f"Checking OpenAlex content cache across {len(candidates):,} candidates ({total_batches} batches)...")

    for i in range(0, len(candidates), batch_size):
        batch = candidates[i : i + batch_size]
        id_map = {f"W{c['oax_id']}": c for c in batch}
        pipe_ids = "|".join(id_map.keys())

        url = f"https://api.openalex.org/works?filter=openalex_id:{pipe_ids}&select=id,has_content&per_page={batch_size}&api_key={api_key}"
        req = urllib.request.Request(url, headers=headers)

        try:
            with urllib.request.urlopen(req, timeout=15) as resp:
                data = json.loads(resp.read().decode())
                for res in data.get("results", []):
                    full_id = res.get("id", "")
                    short_id = full_id.split("/")[-1]
                    has_content = res.get("has_content", {})
                    if has_content.get("pdf", False):
                        if short_id in id_map:
                            cached_candidates.append(id_map[short_id])
        except Exception as e:
            print(f"  Warning: batch {i//batch_size + 1} query error: {e}")
        
        # We need enough candidates to fill the daily 100 free limit
        if len(cached_candidates) >= 150:
            break
        time.sleep(0.1)

    print(f"Found {len(cached_candidates)} verified works with cached PDF in OpenAlex Content repository.")
    return cached_candidates


def harvest_free_daily_allowance(max_downloads: int = 100) -> dict[str, Any]:
    """Download available PDFs until the daily free allowance is exhausted."""
    api_key, email = load_env_credentials()
    if not api_key:
        print("Error: OPENALEX_API_KEY not found in .env. Exiting.")
        return {"downloaded": 0, "status": "no_key"}

    pending = fetch_pending_gated_works(limit=500)
    print(f"Total pending gated works sampled: {len(pending):,}")
    if not pending:
        print("No pending gated works found.")
        return {"downloaded": 0, "status": "none_pending"}

    cached = check_content_availability_batch(pending, api_key, email)
    if not cached:
        print("No pending gated works currently have cached PDFs in OpenAlex.")
        return {"downloaded": 0, "status": "none_cached"}

    downloaded = 0
    failed = 0
    remaining_usd = None
    reset_secs = None

    headers = {"User-Agent": f"mailto:{email}"}

    print("\n" + "=" * 70)
    print("HARVESTING OPENALEX DAILY FREE ALLOWANCE ($1.00 USD / ~100 PDFs)")
    print("=" * 70)

    for idx, item in enumerate(cached, 1):
        if downloaded >= max_downloads:
            print(f"Reached targeted maximum downloads ({max_downloads}).")
            break

        oid = item["oax_id"]
        dest = TARGET_DIR / f"{oid}.pdf"
        if dest.exists() and dest.stat().st_size > 4096:
            continue

        pdf_url = f"https://content.openalex.org/works/W{oid}.pdf?api_key={api_key}"
        req = urllib.request.Request(pdf_url, headers=headers)

        t0 = time.time()
        try:
            with urllib.request.urlopen(req, timeout=30) as resp:
                status = resp.status
                rem_header = resp.headers.get("X-RateLimit-Remaining-USD")
                reset_header = resp.headers.get("X-RateLimit-Reset")
                if rem_header is not None:
                    try:
                        remaining_usd = float(rem_header)
                    except ValueError:
                        pass
                if reset_header is not None:
                    try:
                        reset_secs = int(reset_header)
                    except ValueError:
                        pass

                content = resp.read()
                if len(content) > 4096 and content[:4] == b"%PDF":
                    dest.write_bytes(content)
                    downloaded += 1
                    rem_str = f"${remaining_usd:.2f}" if remaining_usd is not None else "N/A"
                    print(f"  ✓ [{downloaded}] W{oid} ({item['journal'][:28]}): {len(content)/1024:.1f} KB in {time.time()-t0:.1f}s | Free Rem: {rem_str}")
                else:
                    failed += 1
                    print(f"  ✗ W{oid}: Invalid PDF payload ({len(content)} bytes)")
        except urllib.error.HTTPError as he:
            if he.code == 429:
                print(f"\n[DAILY ALLOWANCE EXHAUSTED] HTTP 429 Too Many Requests: Free daily limit reached.")
                break
            elif he.code == 404:
                print(f"  ✗ W{oid}: 404 Not Found in Content cache")
                failed += 1
            else:
                print(f"  ✗ W{oid}: HTTP {he.code}: {he.reason}")
                failed += 1
        except Exception as err:
            print(f"  ✗ W{oid}: Error: {err}")
            failed += 1

        # Check if daily allowance has reached zero
        if remaining_usd is not None and remaining_usd <= 0.00:
            print("\n[DAILY ALLOWANCE EXHAUSTED] Remaining free allowance reached $0.00.")
            break

        time.sleep(0.3)

    print("\n" + "=" * 70)
    print(f"DOWNLOAD SUMMARY: {downloaded} PDFs downloaded, {failed} failed.")
    if remaining_usd is not None:
        print(f"Remaining Free Allowance Today : ${remaining_usd:.2f} USD")
    if reset_secs is not None:
        print(f"Allowance Reset In            : {reset_secs/3600:.1f} hours ({reset_secs} seconds)")
    print("=" * 70)

    return {
        "downloaded": downloaded,
        "failed": failed,
        "remaining_usd": remaining_usd,
        "reset_seconds": reset_secs,
    }


def main():
    parser = argparse.ArgumentParser(description="Harvest free daily OpenAlex Content API PDFs.")
    parser.add_argument("--limit", type=int, default=100, help="Maximum number of PDFs to download (default 100)")
    args = parser.parse_args()

    harvest_free_daily_allowance(max_downloads=args.limit)


if __name__ == "__main__":
    main()
