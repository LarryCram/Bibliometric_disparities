"""High-throughput harvester for Australian and international DSpace/institutional repositories.

Targets ~12,487 direct PDF repository links from OpenAlex oa_locations across:
- Minerva Access (University of Melbourne: ~3,145 candidates)
- eSpace (Curtin University: ~2,189 candidates)
- Research Online (University of Wollongong: ~910 candidates)
- QUT ePrints (Queensland University of Technology: ~657 candidates)
- Murdoch Research Repository (~346 candidates)
- USQ ePrints (~289 candidates)
- NOVA (University of Newcastle: ~433 candidates)
- UWA Profiles and Research Repository (~214 candidates)
- Adelaide Research & Scholarship (~196 candidates)
- Charles Sturt University CRO (~186 candidates)
- ACU, CDU, UTAS, Flinders, Swinburne, Notre Dame, and international repositories.

Features:
- Playwright Chrome persistent profile with plugins.always_open_pdf_externally
- 3-tier resolution: Direct URL stream -> citation_pdf_url meta tag -> DSpace View/Open bitstream click
- Fast 2-strike circuit breaker per repository to prevent stalls
- Automatic context recycling every 250 items
"""

import argparse
import sys
import time
from collections import defaultdict
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

import duckdb
from playwright.sync_api import sync_playwright

from era.config import DATA_DIR, K_DRIVE
from era.sources.fulltext.playwright_harvester import (
    DEFAULT_PROFILE_DIR,
    is_valid_pdf_file,
    setup_chrome_profile,
)

TARGET_DIR = K_DRIVE / "era_oa_pdfs"


def fetch_all_dspace_repository_candidates() -> dict[str, list[dict[str, Any]]]:
    """Fetch direct PDF candidate links from open repositories for pending works."""
    TARGET_DIR.mkdir(parents=True, exist_ok=True)
    existing_ids = {int(p.stem) for p in TARGET_DIR.glob("*.pdf") if p.stem.isdigit()}

    con = duckdb.connect()
    con.execute("CREATE TEMP TABLE existing (oax_id BIGINT)")
    con.executemany("INSERT INTO existing VALUES (?)", [(x,) for x in existing_ids])

    rows = con.execute("""
        SELECT 
            l.oax_id,
            m.canonical_id,
            m.era_title,
            m.era_year,
            l.source_name as repository_name,
            l.pdf_url
        FROM 'data/oa_locations.parquet' l
        JOIN 'data/era_openalex_canonical_matches.parquet' m ON l.oax_id = m.openalex_id
        LEFT JOIN existing e ON l.oax_id = e.oax_id
        WHERE l.source_type = 'repository' AND l.is_oa = true AND l.pdf_url IS NOT NULL
          AND e.oax_id IS NULL
    """).fetchall()

    grouped = defaultdict(list)
    seen_oax = set()

    for r in rows:
        oid = r[0]
        if oid in seen_oax:
            continue
        seen_oax.add(oid)

        repo_name = r[4] or urlparse(r[5]).netloc.lower()
        grouped[repo_name].append({
            "oax_id": oid,
            "canonical_id": r[1],
            "title": r[2],
            "year": r[3],
            "repository": repo_name,
            "url": r[5],
            "dest_path": TARGET_DIR / f"{oid}.pdf",
        })

    return grouped


def run_repo_batch(
    page: Any,
    candidates: list[dict[str, Any]],
    repo_name: str,
    max_consecutive_fails: int = 2,
    delay: float = 0.5,
    timeout: int = 12000,
) -> tuple[int, int]:
    """Harvest candidates for a single repository with fast 2-strike skip on failure."""
    success_count = 0
    fail_count = 0
    consecutive_fails = 0

    for idx, item in enumerate(candidates, 1):
        oid = item["oax_id"]
        dest = item["dest_path"]
        url = item["url"]
        title = item.get("title", "")

        if dest.exists() and dest.stat().st_size > 4096:
            success_count += 1
            continue

        if delay > 0 and idx > 1:
            time.sleep(delay)

        t0 = time.time()
        saved = False

        try:
            # Tier 1: Direct download attempt
            try:
                with page.expect_download(timeout=min(timeout, 10000)) as dl_info:
                    try:
                        page.goto(url, wait_until="commit", timeout=timeout)
                    except Exception as nav_err:
                        err_msg = str(nav_err)
                        if "Download is starting" not in err_msg and "net::ERR_ABORTED" not in err_msg:
                            raise
                dl = dl_info.value
                dl.save_as(str(dest))
                if is_valid_pdf_file(dest):
                    sz = dest.stat().st_size
                    print(f"  ✓ Direct Download: {sz/1024:.1f} KB in {time.time()-t0:.1f}s")
                    saved = True
                else:
                    dest.unlink(missing_ok=True)
            except Exception:
                pass

            # Tier 2: Meta URL resolution fallback (bepress, citation_pdf_url, dspace)
            if not saved and not page.is_closed():
                try:
                    meta = page.locator(
                        "meta[name='citation_pdf_url'], meta[name='bepress_citation_pdf_url']"
                    ).first
                    if meta.count() > 0:
                        meta_url = meta.get_attribute("content")
                        if meta_url and meta_url.startswith("http"):
                            with page.expect_download(timeout=min(timeout, 10000)) as dl_meta:
                                try:
                                    page.goto(meta_url, wait_until="commit", timeout=timeout)
                                except Exception as nav_e:
                                    if "Download is starting" not in str(nav_e) and "net::ERR_ABORTED" not in str(nav_e):
                                        raise
                            dl_m = dl_meta.value
                            dl_m.save_as(str(dest))
                            if is_valid_pdf_file(dest):
                                sz = dest.stat().st_size
                                print(f"  ✓ Meta URL Download: {sz/1024:.1f} KB in {time.time()-t0:.1f}s")
                                saved = True
                            else:
                                dest.unlink(missing_ok=True)
                except Exception:
                    pass

            # Tier 3: DSpace View/Open bitstream link fallback
            if not saved and not page.is_closed():
                try:
                    btn = page.locator(
                        "a:has-text('View/Open'), a[href*='bitstream'][href*='.pdf'], "
                        "a[href*='bitstream'], a#pdf, a:has-text('Download PDF'), "
                        "a:has-text('Download'), a:has-text('Open'), a.view-open"
                    ).first
                    if btn and btn.is_visible(timeout=2000):
                        with page.expect_download(timeout=min(timeout, 10000)) as dl_info2:
                            btn.click(force=True, timeout=2500)
                        dl2 = dl_info2.value
                        dl2.save_as(str(dest))
                        if is_valid_pdf_file(dest):
                            sz = dest.stat().st_size
                            print(f"  ✓ DSpace Bitstream: {sz/1024:.1f} KB in {time.time()-t0:.1f}s")
                            saved = True
                        else:
                            dest.unlink(missing_ok=True)
                except Exception:
                    pass

            if saved:
                success_count += 1
                consecutive_fails = 0
            else:
                fail_count += 1
                consecutive_fails += 1
                page_title = ""
                try:
                    page_title = page.title()[:40] if not page.is_closed() else "Closed"
                except Exception:
                    pass
                print(f"  ✗ Failed on W{oid} ({repo_name[:25]}): '{page_title}'")

                if consecutive_fails >= max_consecutive_fails:
                    remaining = len(candidates) - idx
                    if remaining > 0:
                        print(f"  [SKIP REPO] Skipping remaining {remaining} items for '{repo_name}' after {consecutive_fails} consecutive failure(s).")
                    break

        except Exception as e:
            fail_count += 1
            consecutive_fails += 1
            print(f"  ✗ Error on W{oid}: {type(e).__name__}: {e}")
            if consecutive_fails >= max_consecutive_fails:
                remaining = len(candidates) - idx
                if remaining > 0:
                    print(f"  [SKIP REPO] Skipping remaining {remaining} items for '{repo_name}' after {consecutive_fails} failure(s).")
                break

    return success_count, fail_count


def harvest_dspace_repositories(
    delay: float = 0.5,
    headless: bool = True,
    profile_dir: Path | None = None,
):
    """Run sequential harvesting across all DSpace and institutional repositories."""
    if profile_dir is None:
        profile_dir = DEFAULT_PROFILE_DIR

    repo_groups = fetch_all_dspace_repository_candidates()
    total_repos = len(repo_groups)
    total_candidates = sum(len(items) for items in repo_groups.values())

    print("\n" + "=" * 75)
    print("=== HARVESTING DSPACE & INSTITUTIONAL REPOSITORIES ===")
    print("=" * 75)
    print(f"Total Repositories       : {total_repos:,}")
    print(f"Total Candidate Papers   : {total_candidates:,}")
    print(f"Chrome Profile Directory : {profile_dir}")
    print("=" * 75)

    if total_candidates == 0:
        print("No pending repository candidates found.")
        return

    # Sort repositories by candidate count descending
    sorted_repos = sorted(repo_groups.items(), key=lambda x: len(x[1]), reverse=True)

    setup_chrome_profile(profile_dir)

    total_success = 0
    total_failures = 0
    items_processed = 0
    t0_all = time.time()

    with sync_playwright() as p:
        args = [
            "--no-first-run",
            "--no-default-browser-check",
            "--disable-blink-features=AutomationControlled",
        ]
        context = p.chromium.launch_persistent_context(
            user_data_dir=str(profile_dir),
            headless=headless,
            channel="chrome",
            accept_downloads=True,
            args=args,
        )
        page = context.new_page()

        for r_idx, (repo_name, items) in enumerate(sorted_repos, 1):
            # Recycle context periodically to free memory
            if items_processed > 0 and items_processed % 250 == 0:
                print("\nRecycling browser context...")
                try:
                    page.close()
                    context.close()
                except Exception:
                    pass
                time.sleep(1.0)
                context = p.chromium.launch_persistent_context(
                    user_data_dir=str(profile_dir),
                    headless=headless,
                    channel="chrome",
                    accept_downloads=True,
                    args=args,
                )
                page = context.new_page()

            print(f"\n[Repo {r_idx}/{total_repos}] {repo_name} ({len(items)} papers):")
            s, f = run_repo_batch(page, items, repo_name, max_consecutive_fails=2, delay=delay)
            total_success += s
            total_failures += f
            items_processed += (s + f)

        try:
            page.close()
            context.close()
        except Exception:
            pass

    elapsed = time.time() - t0_all
    print("\n" + "=" * 75)
    print("=== DSPACE REPOSITORY HARVEST COMPLETE ===")
    print(f"Total Succeeded: {total_success:,}")
    print(f"Total Failed   : {total_failures:,}")
    print(f"Elapsed Time   : {elapsed/60:.1f} minutes")
    print("=" * 75)


def main():
    parser = argparse.ArgumentParser(description="Harvest Australian and global DSpace/institutional repositories.")
    parser.add_argument("--delay", type=float, default=0.5, help="Polite delay between requests (default 0.5s)")
    parser.add_argument("--headed", action="store_true", help="Run headed browser")
    args = parser.parse_args()

    harvest_dspace_repositories(delay=args.delay, headless=not args.headed)


if __name__ == "__main__":
    main()
