"""Second-pass PDF downloader: for ERA-matched works that
download_oa_pdfs_direct.py couldn't get a real PDF for, try every OA
location OpenAlex knows about (not just best_oa_location), and resolve
landing pages to their actual PDF link via citation_pdf_url (see
oa_landing_page.py) rather than giving up after one URL.

For each work, candidates are tried in this order until one produces a
verified PDF:
  1. every repository-type location's pdf_url (source.type='repository' in
     oa_locations.parquet) - repository copies are rarely behind the
     Cloudflare/AWS WAF bot-walls that block scripted access to publisher
     pages, even when the copy is only an accepted-manuscript version
     rather than the publisher's typeset PDF. Prioritised first on
     purpose: this deliberately prefers a reliably-downloadable repository
     copy over OpenAlex's own "best" pick if that pick is a publisher page.
  2. every repository-type location's landing_page_url, resolved via
     citation_pdf_url
  3. best_oa_pdf_url (from era_openalex_matches_with_oa.parquet - what
     download_oa_pdfs_direct.py tried; OpenAlex's own "best" pick, often a
     publisher's bronze OA page - see module history)
  4. every remaining (non-repository, e.g. journal/publisher) location's
     pdf_url

A candidate that 200s with HTML is itself run through the landing-page
resolver before being given up on - OpenAlex's best_oa_location.pdf_url
sometimes turns out to actually be a landing page in practice (e.g. some
hdl.handle.net entries), not just landing_page_url, so every HTML response
gets one resolution attempt regardless of which field it came from.

Same validation as download_oa_pdfs_direct.py (magic bytes, min size,
%%EOF trailer) and same skip-bot-walled-hosts decision (Cloudflare/AWS WAF
challenges are left as failures, not chased with a headless browser).

Final stage: OpenAlex's own PDF archive. `locations[].pdf_url` always
points at the original publisher's (or repository's) URL - never at
OpenAlex's own re-hosted copy, which lives at
`https://content.openalex.org/works/{work_id}.pdf` (a UUID-backed file
addressed by work ID) and is only present for a subset of works, flagged
by `has_content.pdf` in the raw record. Requires authentication - a bare
request gets a 401; `api_key=<OPENALEX_API_KEY>` as a query parameter
works (confirmed manually 2026-08-29). This endpoint has a strict external
quota (100/day), so it is NOT part of the per-work candidate chain above:
it only runs once, after every other candidate for every work has already
failed, checks `has_content.pdf` first to avoid spending attempts on works
OpenAlex has no archived copy for, runs sequentially (not threaded, unlike
the rest of this script) so the hard cap is exact, and stops after
OAX_ARCHIVE_MAX_SUCCESSES successful retrievals - well under the daily
quota, since this script may be run more than once a day.
"""

import argparse
import random
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from urllib.parse import urlencode

import duckdb
import requests

from era.config import DATA_DIR, M_DRIVE, PDF_DIR
from era.sources.fulltext.landing_page import resolve_pdf_url

RAW_OPENALEX_WORKS = M_DRIVE / "openalex_oct26/parquet_converted/compact/works"
ENV_FILE = Path(__file__).parent.parent / ".env"

MATCHES_WITH_OA = DATA_DIR / "era_openalex_matches_with_oa.parquet"
OA_LOCATIONS = DATA_DIR / "oa_locations.parquet"

OUTPUT_DIR = PDF_DIR

OAX_ARCHIVE_BASE = "https://content.openalex.org/works"
OAX_ARCHIVE_MAX_SUCCESSES = 10  # hard cap - this endpoint's quota is 100/day; stay far under it per run

DEFAULT_CONCURRENCY = 12
TIMEOUT = (6, 20)
MIN_VALID_SIZE = 2048
HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36 "
        "(Academic Research; Bibliometric Disparities Project; mailto:larry.cram@anu.edu.au)"
    )
}


def fetch_candidates(limit: int | None = None) -> list[tuple[int, list[tuple[str, str]]]]:
    """Fetch works still missing a downloaded PDF, with ordered fallback chain of repository and OA URLs."""
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    existing_ids = {int(p.stem) for p in OUTPUT_DIR.glob("*.pdf") if p.stem.isdigit()}

    con = duckdb.connect()
    query = f"""
        WITH targets AS (
            SELECT DISTINCT openalex_id AS oax_id, ANY_VALUE(best_oa_pdf_url) AS best_pdf_url
            FROM '{MATCHES_WITH_OA}'
            WHERE openalex_id IS NOT NULL
            GROUP BY openalex_id
        ),
        alt_pdfs AS (
            SELECT oax_id,
                   list(DISTINCT pdf_url) FILTER (WHERE pdf_url IS NOT NULL AND source_type = 'repository') AS repo_pdf_urls,
                   list(DISTINCT landing_page_url) FILTER (WHERE landing_page_url IS NOT NULL AND source_type = 'repository') AS repo_landing_urls,
                   list(DISTINCT pdf_url) FILTER (WHERE pdf_url IS NOT NULL AND (source_type IS NULL OR source_type != 'repository')) AS other_pdf_urls,
                   list(DISTINCT landing_page_url) FILTER (WHERE landing_page_url IS NOT NULL AND (source_type IS NULL OR source_type != 'repository')) AS other_landing_urls
            FROM '{OA_LOCATIONS}'
            GROUP BY oax_id
        )
        SELECT t.oax_id, t.best_pdf_url, a.repo_pdf_urls, a.repo_landing_urls, a.other_pdf_urls, a.other_landing_urls
        FROM targets t
        LEFT JOIN alt_pdfs a USING (oax_id)
        WHERE (t.best_pdf_url IS NOT NULL OR a.repo_pdf_urls IS NOT NULL OR a.repo_landing_urls IS NOT NULL OR a.other_pdf_urls IS NOT NULL)
    """
    rows = con.execute(query).fetchall()
    con.close()

    targets = []
    for oax_id, best_pdf_url, repo_pdf_urls, repo_landing_urls, other_pdf_urls, other_landing_urls in rows:
        oid = int(oax_id)
        if oid in existing_ids:
            continue

        ordered = (
            [(u, "repo_pdf") for u in (repo_pdf_urls or [])]
            + [(u, "repo_landing") for u in (repo_landing_urls or [])]
            + ([(best_pdf_url, "best_oa")] if best_pdf_url else [])
            + [(u, "other_pdf") for u in (other_pdf_urls or [])]
            + [(u, "other_landing") for u in (other_landing_urls or [])]
        )
        candidates = []
        seen = set()
        for url, kind in ordered:
            if url and url not in seen:
                seen.add(url)
                candidates.append((url, kind))
        if candidates:
            targets.append((oid, candidates))

    random.seed(42)
    random.shuffle(targets)

    if limit is not None:
        return targets[:limit]
    return targets


def try_download(session, url, dest_tmp):
    """Fetch url; if it's a valid PDF, write it and return (True, False).
    If it's HTML, try resolving a citation_pdf_url and recurse once on
    that - second element of the return tuple flags whether the landing-
    page resolver is what actually produced the successful download."""
    resp = session.get(url, headers=HEADERS, timeout=TIMEOUT, stream=True)
    resp.raise_for_status()
    n_bytes = 0
    with open(dest_tmp, "wb") as f:
        for chunk in resp.iter_content(chunk_size=1 << 16):
            f.write(chunk)
            n_bytes += len(chunk)

    with open(dest_tmp, "rb") as f:
        magic = f.read(5)
        f.seek(-min(1024, n_bytes), 2)
        tail = f.read()
    if magic == b"%PDF-" and n_bytes >= MIN_VALID_SIZE and b"%%EOF" in tail:
        return True, False

    # Not a real PDF - if it looks like HTML, try resolving citation_pdf_url once.
    if n_bytes and magic[:1] in (b"<", b"\n", b"\r"):
        with open(dest_tmp, "rb") as f:
            html_text = f.read(200_000).decode("utf-8", errors="ignore")
        resolved = resolve_pdf_url(html_text, resp.url)
        if resolved and resolved != url:
            resp2 = session.get(resolved, headers=HEADERS, timeout=TIMEOUT, stream=True)
            resp2.raise_for_status()
            n2 = 0
            with open(dest_tmp, "wb") as f:
                for chunk in resp2.iter_content(chunk_size=1 << 16):
                    f.write(chunk)
                    n2 += len(chunk)
            with open(dest_tmp, "rb") as f:
                magic2 = f.read(5)
                f.seek(-min(1024, n2), 2)
                tail2 = f.read()
            if magic2 == b"%PDF-" and n2 >= MIN_VALID_SIZE and b"%%EOF" in tail2:
                return True, True
    return False, False


def download_one(session, oax_id, candidates):
    dest = OUTPUT_DIR / f"{oax_id}.pdf"
    if dest.exists() and dest.stat().st_size > 0:
        return oax_id, "skipped (exists)", 0, 0.0, None, False, None
    t0 = time.time()
    tmp = dest.with_suffix(".part")
    for i, (url, kind) in enumerate(candidates):
        try:
            ok, via_landing_page = try_download(session, url, tmp)
            if ok:
                tmp.rename(dest)
                return (oax_id, "ok", tmp.stat().st_size if tmp.exists() else dest.stat().st_size,
                        time.time() - t0, i, via_landing_page, kind)
        except Exception:
            continue
        finally:
            tmp.unlink(missing_ok=True)
    return oax_id, "all_candidates_failed", 0, time.time() - t0, None, False, None


def load_env_value(name):
    for line in ENV_FILE.read_text().splitlines():
        if line.strip().startswith(f"{name}="):
            return line.split("=", 1)[1].strip()
    return None


def fetch_has_content_pdf(oax_ids):
    """Bulk-check has_content.pdf (from the raw OpenAlex snapshot) for a
    set of works - used to gate attempts against content.openalex.org to
    only works OpenAlex actually has an archived copy for, since that
    endpoint has a strict external daily quota."""
    if not oax_ids:
        return {}
    con = duckdb.connect()
    ids_str = ",".join(str(i) for i in oax_ids)
    rows = con.execute(f"""
        SELECT CAST(regexp_replace(id, '[^0-9]', '', 'g') AS BIGINT) AS work_idx,
               has_content.pdf AS has_pdf
        FROM read_parquet('{RAW_OPENALEX_WORKS}/*/*.parquet', hive_partitioning=1)
        WHERE CAST(regexp_replace(id, '[^0-9]', '', 'g') AS BIGINT) IN ({ids_str})
    """).fetchall()
    return {wid: bool(has_pdf) for wid, has_pdf in rows}


def try_oax_archive_stage(session, still_missing_oax_ids):
    """Final fallback, run once after every other candidate has failed for
    every work - see module docstring for why this is separate from the
    per-work candidate chain. Sequential, not threaded, so the
    OAX_ARCHIVE_MAX_SUCCESSES cap is exact."""
    if not still_missing_oax_ids:
        return []
    api_key = load_env_value("OPENALEX_API_KEY")
    if not api_key:
        print("\nOPENALEX_API_KEY not found in .env - skipping the OpenAlex-archive final stage.")
        return []
    print(f"\nFinal stage: checking OpenAlex's own PDF archive for "
          f"{len(still_missing_oax_ids)} still-missing works "
          f"(capped at {OAX_ARCHIVE_MAX_SUCCESSES} successes)...")
    has_pdf = fetch_has_content_pdf(still_missing_oax_ids)
    candidates = [oid for oid in still_missing_oax_ids if has_pdf.get(oid)]
    print(f"  {len(candidates)} of those have has_content.pdf=true in OpenAlex's own metadata")

    results = []
    n_ok = 0
    for oid in candidates:
        if n_ok >= OAX_ARCHIVE_MAX_SUCCESSES:
            print(f"  Reached the {OAX_ARCHIVE_MAX_SUCCESSES}-success cap - stopping "
                  f"({len(candidates) - len(results)} candidates left untried).")
            break
        dest = OUTPUT_DIR / f"{oid}.pdf"
        tmp = dest.with_suffix(".part")
        url = f"{OAX_ARCHIVE_BASE}/W{oid}.pdf?{urlencode({'api_key': api_key})}"
        try:
            ok, _ = try_download(session, url, tmp)
            if ok:
                tmp.rename(dest)
                n_ok += 1
                results.append((oid, "ok"))
                print(f"  {oid}: ok ({n_ok}/{OAX_ARCHIVE_MAX_SUCCESSES})")
            else:
                results.append((oid, "not_a_pdf"))
        except Exception as e:
            results.append((oid, f"error: {type(e).__name__}: {e}"))
        finally:
            tmp.unlink(missing_ok=True)
    return results


def main():
    parser = argparse.ArgumentParser(description="Multi-tier OA Landing Page PDF Downloader")
    parser.add_argument("--limit", type=int, default=None, help="Limit number of works to attempt (default: all)")
    parser.add_argument("--workers", type=int, default=DEFAULT_CONCURRENCY, help="Number of worker threads")
    args = parser.parse_args()

    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    print("Fetching pending candidates with repository and OA fallback chains...")
    targets = fetch_candidates(args.limit)

    print(f"Attempting {len(targets):,} works (each with a fallback chain of repository/OA candidate URLs) "
          f"with {args.workers} workers...")

    if not targets:
        print("No pending candidates found.")
        return

    results = []
    t_start = time.time()
    n_done = 0
    with requests.Session() as session, ThreadPoolExecutor(max_workers=args.workers) as pool:
        futures = {pool.submit(download_one, session, oid, cands): oid for oid, cands in targets}
        for fut in as_completed(futures):
            res = fut.result()
            results.append(res)
            n_done += 1
            if n_done % 20 == 0 or n_done == len(targets):
                elapsed = time.time() - t_start
                rate = n_done / elapsed if elapsed > 0 else 0
                n_ok = sum(1 for r in results if r[1] == "ok")
                print(f"  [{n_done:,}/{len(targets):,}] Ok: {n_ok:,} ({n_ok/n_done*100:.1f}%), Rate: {rate:.1f} works/s")
    elapsed = time.time() - t_start

    ok = [r for r in results if r[1] == "ok"]
    skipped = [r for r in results if r[1].startswith("skipped")]
    failed = [r for r in results if r[1] == "all_candidates_failed"]
    won_on_alt_location = [r for r in ok if r[4] is not None and r[4] > 0]
    won_on_landing_page = [r for r in ok if r[5]]
    won_via_repository = [r for r in ok if r[6] in ("repo_pdf", "repo_landing")]

    print(f"\nDone in {elapsed:.1f}s: {len(ok)} ok, {len(skipped)} skipped, "
          f"{len(failed)} total failures where candidate URLs were attempted")
    print(f"  {len(won_on_alt_location)} of {len(ok)} successes needed an alternate OA location "
          f"(primary best_oa_pdf_url alone would have failed these)")
    print(f"  {len(won_on_landing_page)} of {len(ok)} successes specifically needed citation_pdf_url "
          f"landing-page resolution (some URL in the chain 200'd with HTML, not a direct PDF)")
    print(f"  {len(won_via_repository)} of {len(ok)} successes came from a repository-type location "
          f"(prioritised ahead of best_oa_pdf_url/publisher locations - see module docstring)")
    if ok:
        total_bytes = sum(r[2] for r in ok)
        print(f"  Total downloaded: {total_bytes / 1e6:.1f} MB")
    rate = len(targets) / elapsed if elapsed > 0 else 0
    print(f"Throughput: {rate:.2f} works/sec, yield: {len(ok) / len(targets) * 100:.0f}%")

    with requests.Session() as session:
        archive_results = try_oax_archive_stage(session, [r[0] for r in failed])
    n_archive_ok = sum(1 for _, status in archive_results if status == "ok")
    if archive_results:
        print(f"OpenAlex-archive stage: {n_archive_ok} additional PDFs retrieved "
              f"({len(archive_results)} candidates attempted)")


if __name__ == "__main__":
    main()
