"""Automated Download Folder Sweeper for ERA Full-Text PDFs.

Scans ~/Dropbox/DOWNLOADS and ~/Downloads for newly downloaded papers, matches them to
canonical OpenAlex IDs using the batch manifest, URL basenames, and DOI slugs,
and organizes them into /home/lc/k/era_oa_pdfs/<oax_id>.pdf.
"""

import argparse
import json
import os
import re
import shutil
import time
from pathlib import Path

import duckdb

from era.config import K_DRIVE, PROJECT_ROOT

SOURCE_DIRS = [
    Path.home() / "Dropbox" / "DOWNLOADS",
    Path.home() / "Downloads",
]
TARGET_DIR = K_DRIVE / "era_oa_pdfs"
MANIFEST_PATH = PROJECT_ROOT / "report" / "output" / "batch128_manifest.json"

_DOI_LOOKUP_CACHE: dict[str, int] | None = None


def get_doi_slug_lookup() -> dict[str, int]:
    """Build global DOI slug lookup from era_openalex_canonical_matches."""
    global _DOI_LOOKUP_CACHE
    if _DOI_LOOKUP_CACHE is not None:
        return _DOI_LOOKUP_CACHE

    con = duckdb.connect()
    rows = con.execute("""
        SELECT openalex_id, lower(coalesce(era_doi, oax_doi)) as doi
        FROM 'data/era_openalex_canonical_matches.parquet'
        WHERE openalex_id IS NOT NULL 
          AND coalesce(era_doi, oax_doi) IS NOT NULL
    """).fetchall()
    con.close()

    lookup: dict[str, int] = {}
    for r in rows:
        oax_id = int(r[0])
        raw_doi = r[1].strip()
        clean = re.sub(r"^https?://(dx\.)?doi\.org/", "", raw_doi).strip()
        slug = clean.split("/")[-1].lower()
        if slug and len(slug) >= 4:
            lookup[slug] = oax_id
            # Also normalize hyphens vs underscores vs dots
            lookup[slug.replace("-", "").replace("_", "")] = oax_id
            lookup[slug.replace("-", "")] = oax_id
            lookup[slug.replace("_", "")] = oax_id

    _DOI_LOOKUP_CACHE = lookup
    return lookup


def build_manifest_lookup(manifest_path: Path) -> tuple[dict[str, int], dict[str, int]]:
    """Build URL-basename and DOI lookup dictionaries from manifest."""
    basename_map: dict[str, int] = {}
    doi_map: dict[str, int] = {}

    if not manifest_path.exists():
        return basename_map, doi_map

    with open(manifest_path, "r", encoding="utf-8") as f:
        items = json.load(f)

    for it in items:
        oax_id = int(it["oax_id"])
        url = it["url"].split("?")[0].rstrip("/")
        basename = Path(url).name.lower()
        if basename.endswith(".pdf"):
            basename_map[basename] = oax_id
        else:
            basename_map[f"{basename}.pdf"] = oax_id

        doi = it.get("doi", "").lower()
        if doi:
            slug = doi.split("/")[-1]
            doi_map[slug] = oax_id
            doi_map[slug.replace("-", "").replace("_", "")] = oax_id

    return basename_map, doi_map


def sweep_once() -> int:
    """Run a single sweep over all download directories."""
    TARGET_DIR.mkdir(parents=True, exist_ok=True)
    basename_map, doi_map = build_manifest_lookup(MANIFEST_PATH)
    doi_slug_cache = get_doi_slug_lookup()

    moved_count = 0

    for sdir in SOURCE_DIRS:
        if not sdir.exists():
            continue

        pdf_files = list(sdir.glob("*.pdf"))
        for p in pdf_files:
            fn = p.name
            fn_lower = fn.lower()
            stem = p.stem.lower()

            target_oax_id: int | None = None

            # Case 1: Already named <oax_id>.pdf
            if stem.isdigit():
                target_oax_id = int(stem)

            # Case 2: Matches manifest basename (e.g. "186-3-1315.pdf")
            if target_oax_id is None:
                clean_name = re.sub(r"\s*\(\d+\)\.pdf$", ".pdf", fn_lower)
                if clean_name in basename_map:
                    target_oax_id = basename_map[clean_name]

            # Case 3: Matches manifest DOI slug
            if target_oax_id is None:
                for slug, oax_id in doi_map.items():
                    if slug in fn_lower:
                        target_oax_id = oax_id
                        break

            # Case 4: Matches global DOI slug lookup
            if target_oax_id is None:
                clean_stem = re.sub(r"\s*\(\d+\)$", "", stem)
                if clean_stem in doi_slug_cache:
                    target_oax_id = doi_slug_cache[clean_stem]
                else:
                    norm = clean_stem.replace("-", "").replace("_", "")
                    if norm in doi_slug_cache:
                        target_oax_id = doi_slug_cache[norm]

            # Case 5: Exact DOI substring check (catches Springer e.g. s00737-014-0447-2)
            if target_oax_id is None:
                clean_stem = re.sub(r"\s*\(\d+\)$", "", stem)
                con = duckdb.connect()
                pattern = f"%{clean_stem}%"
                res = con.execute("""
                    SELECT openalex_id 
                    FROM 'data/era_openalex_canonical_matches.parquet'
                    WHERE openalex_id IS NOT NULL 
                      AND lower(coalesce(era_doi, oax_doi)) LIKE ?
                    LIMIT 1
                """, [pattern]).fetchall()
                if res:
                    target_oax_id = int(res[0][0])
                else:
                    # Check MDPI pattern: e.g. resources-03-00362 or symmetry-07-00146
                    m = re.match(r"([a-zA-Z]+)-(\d+)-(\d+)", clean_stem)
                    if m:
                        jname, vol, art = m.group(1), int(m.group(2)), int(m.group(3))
                        mdpi_pattern = f"%{jname[:3]}%{vol}%{art}%"
                        res_mdpi = con.execute("""
                            SELECT openalex_id 
                            FROM 'data/era_openalex_canonical_matches.parquet'
                            WHERE openalex_id IS NOT NULL 
                              AND lower(coalesce(era_doi, oax_doi)) LIKE ?
                            LIMIT 1
                        """, [mdpi_pattern]).fetchall()
                        if res_mdpi:
                            target_oax_id = int(res_mdpi[0][0])
                con.close()

            # Case 6: Fallback to reading first-page text from PDF for embedded DOI or title
            if target_oax_id is None:
                import subprocess
                try:
                    proc = subprocess.run(
                        ["pdftotext", "-f", "1", "-l", "1", str(p), "-"],
                        capture_output=True,
                        text=True,
                        errors="ignore",
                        timeout=5
                    )
                    txt = proc.stdout
                    doi_matches = re.findall(r"10\.\d{4,9}/[-._;()/:A-Za-z0-9]+", txt)
                    con = duckdb.connect()
                    for raw_d in doi_matches:
                        clean_d = raw_d.rstrip(".,;()").lower()
                        res_d = con.execute("""
                            SELECT openalex_id 
                            FROM 'data/era_openalex_canonical_matches.parquet'
                            WHERE openalex_id IS NOT NULL 
                              AND lower(coalesce(era_doi, oax_doi)) LIKE ?
                            LIMIT 1
                        """, [f"%{clean_d}%"]).fetchall()
                        if res_d:
                            target_oax_id = int(res_d[0][0])
                            break
                    
                    # If still not found, check title snippet in first 500 chars
                    if target_oax_id is None and len(txt) > 20:
                        lines = [line.strip().lower() for line in txt.split("\n") if len(line.strip()) > 15]
                        for line in lines[:5]:
                            res_t = con.execute("""
                                SELECT openalex_id 
                                FROM 'data/era_openalex_canonical_matches.parquet'
                                WHERE openalex_id IS NOT NULL 
                                  AND lower(era_title) LIKE ?
                                LIMIT 1
                            """, [f"%{line[:30]}%"]).fetchall()
                            if res_t:
                                target_oax_id = int(res_t[0][0])
                                break
                    con.close()
                except Exception:
                    pass

            # If matched, copy/move to target directory
            if target_oax_id is not None:
                dest = TARGET_DIR / f"{target_oax_id}.pdf"
                if not dest.exists() or dest.stat().st_size < p.stat().st_size:
                    shutil.copy2(str(p), str(dest))
                    print(f"✓ Swept from {sdir.name}: {fn} -> {dest.name}")
                    moved_count += 1

    return moved_count


def watch_mode(interval: float = 3.0):
    """Continuously monitor download folders and sweep newly added files."""
    watched_str = ", ".join(str(d) for d in SOURCE_DIRS if d.exists())
    print(f"Watching [{watched_str}] every {interval}s... (Press Ctrl+C to stop)")
    try:
        while True:
            moved = sweep_once()
            time.sleep(interval)
    except KeyboardInterrupt:
        print("\nWatcher stopped.")


def main():
    parser = argparse.ArgumentParser(description="Sweep downloads into /home/lc/k/era_oa_pdfs/<oax_id>.pdf")
    parser.add_argument("--watch", action="store_true", help="Run continuously in watcher mode")
    parser.add_argument("--interval", type=float, default=3.0, help="Watcher check interval in seconds (default: 3.0)")
    args = parser.parse_args()

    if args.watch:
        watch_mode(args.interval)
    else:
        moved = sweep_once()
        print(f"Sweep complete: {moved} file(s) organized to {TARGET_DIR}.")


if __name__ == "__main__":
    main()
