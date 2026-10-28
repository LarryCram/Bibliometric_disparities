"""Harvester for alternative repository PDF locations and non-gated small publishers.

Executes:
1. Alternative repository direct PDF locations (~1,432 candidates from university repositories,
   preprint archives, and institutional repositories for works whose journal was gated).
2. The ~650 non-gated small and independent publishers (<50 papers each).
   Each domain is processed sequentially. If a domain fails 2 consecutive times,
   the harvester immediately skips to the next publisher to maintain high throughput.
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

# Commercial platforms with aggressive bot/WAF gates to skip in tiny publishers sweep
GATED_PLATTERNS = [
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


def fetch_alternative_repository_candidates() -> list[dict[str, Any]]:
    """Fetch direct PDF candidate links from open repositories for unfetched works."""
    TARGET_DIR.mkdir(parents=True, exist_ok=True)
    existing_ids = {int(p.stem) for p in TARGET_DIR.glob("*.pdf") if p.stem.isdigit()}

    con = duckdb.connect()
    # Find works that were gated on journal sites and not yet downloaded
    clauses = [f"l.pdf_url ILIKE '%{p}%'" for p in GATED_PLATTERNS]
    where_gated = " OR ".join(clauses)

    con.execute(f"""
        CREATE TEMP TABLE gated_pending AS
        SELECT DISTINCT l.oax_id
        FROM 'data/oa_locations.parquet' l
        JOIN 'data/era_openalex_canonical_matches.parquet' m ON l.oax_id = m.openalex_id
        WHERE l.source_type = 'journal' AND l.is_oa = true AND l.pdf_url IS NOT NULL
          AND ({where_gated})
    """)

    all_gated = {r[0] for r in con.execute("SELECT oax_id FROM gated_pending").fetchall()}
    gated_unfetched = all_gated - existing_ids

    con.execute("CREATE TEMP TABLE unfetched (oax_id BIGINT)")
    con.executemany("INSERT INTO unfetched VALUES (?)", [(x,) for x in gated_unfetched])

    rows = con.execute("""
        SELECT 
            l.oax_id,
            m.canonical_id,
            m.era_title,
            m.era_year,
            l.source_name as repository_name,
            l.pdf_url
        FROM 'data/oa_locations.parquet' l
        JOIN unfetched u ON l.oax_id = u.oax_id
        JOIN 'data/era_openalex_canonical_matches.parquet' m ON l.oax_id = m.openalex_id
        WHERE l.source_type = 'repository' AND l.is_oa = true AND l.pdf_url IS NOT NULL
    """).fetchall()

    candidates = []
    seen = set()
    for r in rows:
        oid = r[0]
        if oid in existing_ids or oid in seen:
            continue
        seen.add(oid)
        candidates.append({
            "oax_id": oid,
            "canonical_id": r[1],
            "title": r[2],
            "year": r[3],
            "journal": r[4] or "Repository",
            "url": r[5],
            "dest_path": TARGET_DIR / f"{oid}.pdf",
        })
    return candidates


def fetch_tiny_publisher_candidates() -> dict[str, list[dict[str, Any]]]:
    """Fetch candidates from non-gated small publishers grouped by domain."""
    TARGET_DIR.mkdir(parents=True, exist_ok=True)
    existing_ids = {int(p.stem) for p in TARGET_DIR.glob("*.pdf") if p.stem.isdigit()}

    con = duckdb.connect()
    rows = con.execute("""
        SELECT 
            l.oax_id,
            m.canonical_id,
            m.era_title,
            m.era_year,
            l.source_name as journal,
            l.pdf_url
        FROM 'data/oa_locations.parquet' l
        JOIN 'data/era_openalex_canonical_matches.parquet' m ON l.oax_id = m.openalex_id
        WHERE l.source_type = 'journal' AND l.is_oa = true AND l.pdf_url IS NOT NULL
    """).fetchall()

    grouped = defaultdict(list)
    seen = set()
    for r in rows:
        oid = r[0]
        if oid in existing_ids or oid in seen:
            continue
        url = r[5]
        netloc = urlparse(url).netloc.lower()
        if any(p in netloc or p in url.lower() for p in GATED_PLATTERNS):
            continue
        seen.add(oid)
        grouped[netloc].append({
            "oax_id": oid,
            "canonical_id": r[1],
            "title": r[2],
            "year": r[3],
            "journal": r[4] or netloc,
            "url": url,
            "dest_path": TARGET_DIR / f"{oid}.pdf",
        })
    return grouped


def run_domain_batch(
    page: Any,
    candidates: list[dict[str, Any]],
    domain_name: str,
    max_consecutive_fails: int = 2,
    delay: float = 1.0,
    timeout: int = 12000,
) -> tuple[int, int]:
    """Harvest candidates for a single domain with fast domain-level skip on failure."""
    success_count = 0
    fail_count = 0
    consecutive_fails = 0

    for idx, item in enumerate(candidates, 1):
        oid = item["oax_id"]
        dest = item["dest_path"]
        url = item["url"]
        journal = item.get("journal", "")

        if dest.exists() and dest.stat().st_size > 4096:
            success_count += 1
            continue

        if delay > 0 and idx > 1:
            time.sleep(delay)

        t0 = time.time()
        saved = False

        try:
            # 1. Direct download attempt
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

            # 2. Meta URL resolution fallback
            if not saved and not page.is_closed():
                try:
                    meta = page.locator(
                        "meta[name='bepress_citation_pdf_url'], meta[name='citation_pdf_url']"
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

            # 3. Button / link fallback
            if not saved and not page.is_closed():
                try:
                    btn = page.locator(
                        "a#pdf, a[data-article-pdf='true'], a:has-text('Download PDF'), "
                        "a:has-text('View PDF'), a:has-text('Full Text (PDF)'), "
                        "a.article-pdf-download, a.al-link.pdf, a:has-text('HERE')"
                    ).first
                    if btn and btn.is_visible(timeout=2000):
                        with page.expect_download(timeout=min(timeout, 10000)) as dl_info2:
                            btn.click(force=True, timeout=2500)
                        dl2 = dl_info2.value
                        dl2.save_as(str(dest))
                        if is_valid_pdf_file(dest):
                            sz = dest.stat().st_size
                            print(f"  ✓ Button Download: {sz/1024:.1f} KB in {time.time()-t0:.1f}s")
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
                title = page.title() if not page.is_closed() else ""
                print(f"  ✗ Failed on W{oid} ({journal[:25]}): '{title[:40]}'")
                if "just a moment" in title.lower() or "blocked" in title.lower() or consecutive_fails >= max_consecutive_fails:
                    print(f"  [SKIP DOMAIN] Skipping remaining items for '{domain_name}' after {consecutive_fails} failure(s).")
                    break

        except Exception as e:
            fail_count += 1
            consecutive_fails += 1
            print(f"  ✗ Error on W{oid}: {type(e).__name__}")
            if consecutive_fails >= max_consecutive_fails:
                print(f"  [SKIP DOMAIN] Skipping remaining items for '{domain_name}'.")
                break

    return success_count, fail_count


def harvest_all_repositories_and_tiny_publishers(
    headless: bool = False,
    delay: float = 1.0,
    profile_dir: Path = DEFAULT_PROFILE_DIR,
):
    """Run sequential harvest across alternative repositories and all tiny publishers."""
    setup_chrome_profile(profile_dir)

    print("\n" + "=" * 75)
    print("=== HARVESTING ALTERNATIVE REPOSITORIES & ~650 TINY PUBLISHERS ===")
    print("=" * 75)

    # 1. Fetch Candidates
    repo_cands = fetch_alternative_repository_candidates()
    tiny_groups = fetch_tiny_publisher_candidates()

    print(f"Total Alternative Repository Candidates : {len(repo_cands):,}")
    print(f"Total Tiny Publisher Domains            : {len(tiny_groups):,} ({sum(len(v) for v in tiny_groups.values()):,} papers)\n")

    total_success = 0
    total_failures = 0
    t_start = time.time()

    with sync_playwright() as p:
        args = [
            "--no-sandbox",
            "--disable-blink-features=AutomationControlled",
            "--disable-infobars",
        ]
        context = p.chromium.launch_persistent_context(
            user_data_dir=str(profile_dir),
            headless=headless,
            channel="chrome",
            accept_downloads=True,
            args=args,
        )
        page = context.new_page()

        # Phase 1: Alternative Repository Direct PDFs (~1,432 items)
        if repo_cands:
            print("\n" + "-" * 75)
            print(f"--- PHASE 1: Alternative Repository Copies ({len(repo_cands)} candidates) ---")
            print("-" * 75)
            # Group by repository name for clean skipping per institution
            repo_by_name = defaultdict(list)
            for c in repo_cands:
                name = c.get("journal") or urlparse(c["url"]).netloc.lower()
                repo_by_name[name].append(c)

            sorted_repos = sorted(repo_by_name.items(), key=lambda x: len(x[1]), reverse=True)
            for r_idx, (repo_name, items) in enumerate(sorted_repos, 1):
                print(f"\n[Repo {r_idx}/{len(sorted_repos)}] {repo_name} ({len(items)} papers):")
                s, f = run_domain_batch(page, items, repo_name, max_consecutive_fails=2, delay=delay)
                total_success += s
                total_failures += f

        # Phase 2: The ~650 Tiny Publishers
        print("\n" + "-" * 75)
        print(f"--- PHASE 2: Non-Gated Tiny Publishers ({len(tiny_groups)} domains) ---")
        print("-" * 75)

        # Sort tiny publishers by size descending
        sorted_domains = sorted(tiny_groups.items(), key=lambda x: len(x[1]), reverse=True)
        items_processed = 0

        for d_idx, (dom, items) in enumerate(sorted_domains, 1):
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

            print(f"\n[Publisher {d_idx}/{len(sorted_domains)}] {dom} ({len(items)} papers):")
            s, f = run_domain_batch(page, items, dom, max_consecutive_fails=2, delay=delay)
            total_success += s
            total_failures += f
            items_processed += (s + f)

        try:
            context.close()
        except Exception:
            pass

    elapsed = time.time() - t_start
    print("\n" + "=" * 75)
    print("=== HARVEST COMPLETE ===")
    print(f"Total Succeeded: {total_success:,}")
    print(f"Total Failed   : {total_failures:,}")
    print(f"Elapsed Time   : {elapsed/60:.1f} minutes")
    print("=" * 75 + "\n")


def main():
    parser = argparse.ArgumentParser(description="Harvest alternative repositories and tiny publishers")
    parser.add_argument("--headless", action="store_true", help="Run browser in headless mode")
    parser.add_argument("--delay", type=float, default=1.0, help="Delay between downloads in seconds (default: 1.0)")
    args = parser.parse_args()

    harvest_all_repositories_and_tiny_publishers(headless=args.headless, delay=args.delay)


if __name__ == "__main__":
    main()
