"""Automated Playwright Gold Open Access PDF Harvester for ERA 2018.

Uses Playwright and desktop Google Chrome to autonomously navigate, clear Cloudflare/cookie banners,
and trigger PDF downloads across verified Gold Open Access journal articles directly into
~/Downloads (where sweep_downloads.py organizes them into /home/lc/k/era_oa_pdfs/<oax_id>.pdf).
"""

import argparse
import sys
import time
from pathlib import Path
from typing import Any

import duckdb
from playwright.sync_api import sync_playwright

from era.config import DATA_DIR, K_DRIVE, PROJECT_ROOT

TARGET_DIR = K_DRIVE / "era_oa_pdfs"
OA_LOCATIONS = DATA_DIR / "oa_locations.parquet"
CANONICAL_MATCHES = DATA_DIR / "era_openalex_canonical_matches.parquet"


def fetch_gold_oa_candidates(limit: int | None = 100) -> list[dict[str, Any]]:
    """Fetch verified undownloaded Gold OA journal candidates with direct PDF links."""
    TARGET_DIR.mkdir(parents=True, exist_ok=True)
    existing_ids = {int(p.stem) for p in TARGET_DIR.glob("*.pdf") if p.stem.isdigit()}

    con = duckdb.connect()
    query = """
        SELECT 
            l.oax_id as openalex_id,
            m.canonical_id,
            m.era_title,
            m.era_year,
            l.source_name as journal,
            l.pdf_url
        FROM 'data/oa_locations.parquet' l
        JOIN 'data/era_openalex_canonical_matches.parquet' m ON l.oax_id = m.openalex_id
        WHERE l.source_type = 'journal' 
          AND l.is_oa = true 
          AND l.pdf_url IS NOT NULL
        ORDER BY hash(l.oax_id)
    """
    rows = con.execute(query).fetchall()
    con.close()

    candidates = []
    for r in rows:
        oid = int(r[0])
        if oid in existing_ids:
            continue

        candidates.append({
            "oax_id": oid,
            "canonical_id": int(r[1]) if r[1] else None,
            "title": r[2] if r[2] else "Untitled",
            "year": int(r[3]) if r[3] else None,
            "journal": r[4] if r[4] else "Open Access Journal",
            "url": r[5].strip(),
            "dest_path": TARGET_DIR / f"{oid}.pdf",
        })

        if limit is not None and len(candidates) >= limit:
            break

    return candidates


def harvest_candidates(
    candidates: list[dict[str, Any]],
    headless: bool = True,
    delay: float = 2.0,
    timeout: int = 15000,
) -> tuple[int, int]:
    """Harvest candidates sequentially using Playwright Chrome."""
    if not candidates:
        print("No pending candidates to harvest.")
        return 0, 0

    print(f"Launching Playwright Chrome (headless={headless}) for {len(candidates)} candidates...")
    success_count = 0
    fail_count = 0

    with sync_playwright() as p:
        browser = p.chromium.launch(
            channel="chrome",
            headless=headless,
            args=[
                "--disable-blink-features=AutomationControlled",
                "--no-sandbox",
            ],
        )
        context = browser.new_context(
            accept_downloads=True,
            user_agent="Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
        )
        page = context.new_page()

        for idx, item in enumerate(candidates, start=1):
            oid = item["oax_id"]
            url = item["url"]
            journal = item["journal"]
            dest = item["dest_path"]

            if dest.exists() and dest.stat().st_size > 4096:
                print(f"[{idx}/{len(candidates)}] Already exists on disk: OAX {oid}")
                success_count += 1
                continue

            print(f"[{idx}/{len(candidates)}] Navigating: OAX {oid} ({journal[:25]})...")
            t0 = time.time()
            saved = False

            try:
                # 1. Listen for immediate direct download event
                try:
                    with page.expect_download(timeout=min(timeout, 8000)) as dl_info:
                        page.goto(url, wait_until="commit", timeout=timeout)
                    dl = dl_info.value
                    dl.save_as(str(dest))
                    sz = dest.stat().st_size
                    print(f"  ✓ Direct Download: {sz/1024:.1f} KB in {time.time()-t0:.1f}s")
                    saved = True
                except Exception:
                    pass

                # 2. If not downloaded yet, page might be an article HTML page with a Download button
                if not saved:
                    # Dismiss cookie banners if present
                    try:
                        cookie_btn = page.locator(
                            "button[data-cc-action='accept'], button:has-text('Accept all'), "
                            "button:has-text('Agree'), #onetrust-accept-btn-handler"
                        ).first
                        if cookie_btn.is_visible(timeout=1500):
                            cookie_btn.click(timeout=1000)
                            page.wait_for_timeout(300)
                    except Exception:
                        pass

                    # Look for PDF button / link
                    pdf_btn = page.locator(
                        "a[data-article-pdf='true'], a:has-text('Download PDF'), "
                        "a:has-text('PDF'), a[href$='.pdf']"
                    ).first

                    if pdf_btn and pdf_btn.is_visible(timeout=2500):
                        print("  Found Download button, clicking...")
                        with page.expect_download(timeout=min(timeout, 10000)) as dl_info2:
                            pdf_btn.click(force=True, timeout=3000)
                        dl2 = dl_info2.value
                        dl2.save_as(str(dest))
                        sz = dest.stat().st_size
                        print(f"  ✓ Button Download: {sz/1024:.1f} KB in {time.time()-t0:.1f}s")
                        saved = True

                if saved:
                    success_count += 1
                else:
                    fail_count += 1
                    print(f"  ✗ Could not trigger PDF download for OAX {oid}")

            except Exception as e:
                fail_count += 1
                print(f"  ✗ Error on OAX {oid}: {type(e).__name__} ({e})")

            time.sleep(delay)

        browser.close()

    print(f"\nHarvest Run Complete: {success_count} succeeded, {fail_count} failed out of {len(candidates)}.")
    return success_count, fail_count


def main():
    parser = argparse.ArgumentParser(description="Automated Playwright Gold OA PDF Harvester")
    parser.add_argument("--limit", type=int, default=50, help="Number of papers to attempt (default: 50)")
    parser.add_argument("--delay", type=float, default=2.0, help="Delay between paper requests in seconds (default: 2.0)")
    parser.add_argument("--headless", action="store_true", default=False, help="Run headless Chromium (default: False for headed)")
    parser.add_argument("--timeout", type=int, default=15000, help="Page navigation timeout in ms (default: 15000)")
    args = parser.parse_args()

    candidates = fetch_gold_oa_candidates(limit=args.limit)
    harvest_candidates(candidates, headless=args.headless, delay=args.delay, timeout=args.timeout)


if __name__ == "__main__":
    main()
