"""Automated Download Folder Sweeper and Cleaner for ERA Full-Text PDFs.

Scans ~/Dropbox/DOWNLOADS and ~/Downloads for newly downloaded papers, matches them to
canonical OpenAlex IDs using the batch manifest, URL basenames, DOI slugs, and PDF text,
and organizes them into /home/lc/k/era_oa_pdfs/<oax_id>.pdf.

If a file is in scope and already present in the target repository, or once safely moved,
it removes the file from the watched directory to prevent clutter and duplication.
Out-of-scope files (e.g. personal documents, manuals, non-ERA PDFs) are left untouched.
"""

import argparse
import json
import os
import re
import shutil
import subprocess
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

_CANONICAL_CACHE = None


def normalize_str(s: str) -> str:
    """Normalize string for robust title comparison."""
    if not s:
        return ""
    s = s.lower()
    s = re.sub(r"[^\w\s]", " ", s)
    return " ".join(s.split())


def load_canonical_data():
    """Load canonical lookup tables from DuckDB."""
    global _CANONICAL_CACHE
    if _CANONICAL_CACHE is not None:
        return _CANONICAL_CACHE

    con = duckdb.connect()
    rows = con.execute("""
        SELECT 
            openalex_id, 
            lower(coalesce(era_doi, oax_doi)) as doi,
            lower(era_title) as era_title,
            lower(oax_title) as oax_title
        FROM 'data/era_openalex_canonical_matches.parquet'
        WHERE openalex_id IS NOT NULL
    """).fetchall()
    con.close()

    valid_oax_ids = set()
    doi_to_oax = {}
    slug_to_oax = {}
    seen_titles = set()
    title_list = []

    for r in rows:
        oax_id = int(r[0])
        valid_oax_ids.add(oax_id)
        raw_doi = r[1]
        era_title = r[2]
        oax_title = r[3]

        if raw_doi:
            clean_doi = re.sub(r"^https?://(dx\.)?doi\.org/", "", raw_doi.strip()).strip().lower()
            doi_to_oax[clean_doi] = oax_id
            slug = clean_doi.split("/")[-1]
            if slug and len(slug) >= 6:
                slug_to_oax[slug] = oax_id
                norm_slug = re.sub(r"[-_.]", "", slug)
                if len(norm_slug) >= 6:
                    slug_to_oax[norm_slug] = oax_id

        for t in [era_title, oax_title]:
            if t and len(t.strip()) >= 30:
                norm_t = normalize_str(t)
                if len(norm_t) >= 30 and norm_t not in seen_titles:
                    seen_titles.add(norm_t)
                    title_list.append((len(norm_t), norm_t, oax_id))

    title_list.sort(key=lambda x: x[0], reverse=True)
    _CANONICAL_CACHE = (valid_oax_ids, doi_to_oax, slug_to_oax, title_list)
    return _CANONICAL_CACHE


def build_manifest_lookup(manifest_path: Path | None = None) -> tuple[dict[str, int], dict[str, int]]:
    """Build URL-basename and DOI lookup dictionaries from manifest."""
    m_path = manifest_path or MANIFEST_PATH
    basename_map = {}
    doi_map = {}
    if m_path.exists():
        with open(m_path, "r", encoding="utf-8") as f:
            items = json.load(f)
        for it in items:
            oax_id = int(it["oax_id"])
            url = it["url"].split("?")[0].rstrip("/")
            bn = Path(url).name.lower()
            basename_map[bn if bn.endswith(".pdf") else f"{bn}.pdf"] = oax_id
            doi = it.get("doi", "").lower()
            if doi:
                clean_doi = re.sub(r"^https?://(dx\.)?doi\.org/", "", doi.strip()).strip()
                slug = clean_doi.split("/")[-1]
                if len(slug) >= 5:
                    doi_map[slug] = oax_id
                    norm_slug = re.sub(r"[-_.]", "", slug)
                    doi_map[norm_slug] = oax_id
    return basename_map, doi_map


def load_manifest():
    return build_manifest_lookup(MANIFEST_PATH)



def is_valid_pdf(path: Path) -> bool:
    """Verify file exists, is >10 bytes, and begins with %PDF-."""
    if not path.exists():
        return False
    try:
        if path.stat().st_size < 10:
            return False
        with open(path, "rb") as f:
            header = f.read(5)
            return header.startswith(b"%PDF-")
    except Exception:
        return False



def match_pdf(p: Path, valid_oax_ids, basename_map, doi_manifest_map, doi_to_oax, slug_to_oax, title_list, con_duck):
    fn = p.name
    fn_lower = fn.lower()
    stem = p.stem.lower()

    # 1. Stem is digits (OpenAlex ID)
    if stem.isdigit() and int(stem) in valid_oax_ids:
        return int(stem), "filename_oax_id"

    # 2. Manifest basename
    clean_name = re.sub(r"\s*\(\d+\)\.pdf$", ".pdf", fn_lower)
    if clean_name in basename_map:
        return basename_map[clean_name], "manifest_basename"

    # 3. Manifest DOI slug
    for slug, oax_id in doi_manifest_map.items():
        if slug in fn_lower and len(slug) >= 6:
            return oax_id, "manifest_doi_slug"

    # 4. Exact DOI slug in filename stem
    clean_stem = re.sub(r"\s*\(\d+\)$", "", stem)
    if clean_stem in slug_to_oax:
        return slug_to_oax[clean_stem], "exact_doi_slug"
    norm_stem = re.sub(r"[-_.]", "", clean_stem)
    if norm_stem in slug_to_oax:
        return slug_to_oax[norm_stem], "norm_doi_slug"

    # 5. Check if filename matches MDPI pattern: e.g. energies-09-00690
    m_mdpi = re.match(r"^([a-z]+)-(\d+)-(\d+)$", clean_stem)
    if m_mdpi:
        jname, vol, art = m_mdpi.group(1), int(m_mdpi.group(2)), int(m_mdpi.group(3))
        res = con_duck.execute("""
            SELECT openalex_id, coalesce(era_doi, oax_doi)
            FROM 'data/era_openalex_canonical_matches.parquet'
            WHERE openalex_id IS NOT NULL 
              AND lower(coalesce(era_doi, oax_doi)) LIKE ?
            LIMIT 1
        """, [f"%{jname}%/{vol}/{art}%"]).fetchall()
        if not res:
            res = con_duck.execute("""
                SELECT openalex_id, coalesce(era_doi, oax_doi)
                FROM 'data/era_openalex_canonical_matches.parquet'
                WHERE openalex_id IS NOT NULL 
                  AND lower(coalesce(era_doi, oax_doi)) LIKE ?
                LIMIT 1
            """, [f"%{jname}%{vol}%{art}%"]).fetchall()
        if res:
            return int(res[0][0]), "mdpi_slug_pattern"

    # 6. Check Springer stem pattern: s00737-014-0447-2 or s41599-019-0233-x
    m_springer = re.match(r"^(s\d{5}-\d{3}-\d{4}-[0-9a-z]|s\d{5}-\d{3}-\d{4}-[0-9a-z])$", clean_stem)
    if m_springer:
        res = con_duck.execute("""
            SELECT openalex_id, coalesce(era_doi, oax_doi)
            FROM 'data/era_openalex_canonical_matches.parquet'
            WHERE openalex_id IS NOT NULL 
              AND lower(coalesce(era_doi, oax_doi)) LIKE ?
            LIMIT 1
        """, [f"%{clean_stem}%"]).fetchall()
        if res:
            return int(res[0][0]), "springer_stem"

    # 7. Extract DOI or title directly from PDF text (page 1)
    try:
        proc = subprocess.run(
            ["pdftotext", "-f", "1", "-l", "1", str(p), "-"],
            capture_output=True,
            text=True,
            errors="ignore",
            timeout=5
        )
        txt = proc.stdout
        if txt:
            doi_matches = re.findall(r"10\.\d{4,9}/[-._;()/:A-Za-z0-9]+", txt)
            for raw_d in doi_matches:
                clean_d = raw_d.rstrip(".,;()").lower()
                clean_d = re.sub(r"^https?://(dx\.)?doi\.org/", "", clean_d)
                if clean_d in doi_to_oax:
                    return doi_to_oax[clean_d], "pdf_extracted_doi"
                d_slug = clean_d.split("/")[-1]
                if len(d_slug) >= 6 and d_slug in slug_to_oax:
                    return slug_to_oax[d_slug], "pdf_extracted_doi_slug"

            norm_txt = normalize_str(txt)
            for length, norm_t, oax_id in title_list:
                if length >= 30 and norm_t in norm_txt:
                    return oax_id, "pdf_full_title_match"
    except Exception:
        pass

    return None, "unmatched"


def sweep_once(return_tuple: bool = False):
    """Run a single sweep over all download directories, moving new files and removing duplicates."""
    TARGET_DIR.mkdir(parents=True, exist_ok=True)
    valid_oax_ids, doi_to_oax, slug_to_oax, title_list = load_canonical_data()
    basename_map, doi_manifest_map = load_manifest()
    con_duck = duckdb.connect()

    moved_count = 0
    removed_count = 0

    for sdir in SOURCE_DIRS:
        if not sdir.exists():
            continue

        pdf_files = list(sdir.glob("*.pdf")) + list(sdir.glob("*.PDF"))
        for p in pdf_files:
            oax_id, match_type = match_pdf(
                p, valid_oax_ids, basename_map, doi_manifest_map, 
                doi_to_oax, slug_to_oax, title_list, con_duck
            )

            if oax_id is not None:
                target_dest = TARGET_DIR / f"{oax_id}.pdf"
                dest_exists_valid = is_valid_pdf(target_dest)

                if dest_exists_valid:
                    # In scope and already safely moved -> remove duplicate
                    try:
                        p.unlink()
                        removed_count += 1
                        print(f"✓ Removed already moved duplicate from {sdir.name}: {p.name} -> {target_dest.name}")
                    except Exception as e:
                        print(f"✗ Failed to remove {p.name}: {e}")
                else:
                    # In scope and needs move
                    if is_valid_pdf(p):
                        try:
                            shutil.copy2(str(p), str(target_dest))
                            if is_valid_pdf(target_dest):
                                p.unlink()
                                moved_count += 1
                                print(f"✓ Swept from {sdir.name}: {p.name} -> {target_dest.name}")
                        except Exception as e:
                            print(f"✗ Failed to move {p.name}: {e}")

    con_duck.close()
    if return_tuple:
        return moved_count, removed_count
    return moved_count


def watch_mode(interval: float = 3.0):
    """Continuously monitor download folders and sweep newly added files."""
    watched_str = ", ".join(str(d) for d in SOURCE_DIRS if d.exists())
    print(f"Watching [{watched_str}] every {interval}s... (Press Ctrl+C to stop)")
    try:
        while True:
            moved, removed = sweep_once(return_tuple=True)
            if moved > 0 or removed > 0:
                print(f"Cycle summary: {moved} moved, {removed} duplicates cleaned.")
            time.sleep(interval)
    except KeyboardInterrupt:
        print("\nWatcher stopped.")


def main():
    parser = argparse.ArgumentParser(description="Sweep and clean downloads into /home/lc/k/era_oa_pdfs/<oax_id>.pdf")
    parser.add_argument("--watch", action="store_true", help="Run continuously in watcher mode")
    parser.add_argument("--interval", type=float, default=3.0, help="Watcher check interval in seconds (default: 3.0)")
    args = parser.parse_args()

    if args.watch:
        watch_mode(args.interval)
    else:
        moved, removed = sweep_once(return_tuple=True)
        print(f"Sweep complete: {moved} file(s) moved to {TARGET_DIR}, {removed} duplicate(s) removed.")



if __name__ == "__main__":
    main()
