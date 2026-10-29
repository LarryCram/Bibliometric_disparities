"""Rigorous Audit and Deduplication of Watched Download Folders.

Watched folders:
- ~/Dropbox/DOWNLOADS
- ~/Downloads

Target repository:
- /home/lc/k/era_oa_pdfs/<oax_id>.pdf
"""

import argparse
import json
import os
import re
import shutil
import subprocess
from pathlib import Path

import duckdb

from era.config import K_DRIVE, PROJECT_ROOT

SOURCE_DIRS = [
    Path.home() / "Dropbox" / "DOWNLOADS",
    Path.home() / "Downloads",
]
TARGET_DIR = K_DRIVE / "era_oa_pdfs"
MANIFEST_PATH = PROJECT_ROOT / "report" / "output" / "batch128_manifest.json"


def normalize_str(s: str) -> str:
    """Normalize string for robust title comparison."""
    if not s:
        return ""
    s = s.lower()
    s = re.sub(r"[^\w\s]", " ", s)
    return " ".join(s.split())


def load_canonical_data():
    """Load canonical lookup tables from DuckDB."""
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

    # Sort titles descending by length so the most specific/longest title matches first
    title_list.sort(key=lambda x: x[0], reverse=True)

    return valid_oax_ids, doi_to_oax, slug_to_oax, title_list


def load_manifest():
    basename_map = {}
    doi_map = {}
    if MANIFEST_PATH.exists():
        with open(MANIFEST_PATH, "r", encoding="utf-8") as f:
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


def is_valid_pdf(path: Path) -> bool:
    """Verify file exists, is >2048 bytes, and begins with %PDF-."""
    if not path.exists():
        return False
    try:
        if path.stat().st_size < 2048:
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
        return int(stem), "filename_oax_id", "Numeric OpenAlex ID in filename"

    # 2. Manifest basename
    clean_name = re.sub(r"\s*\(\d+\)\.pdf$", ".pdf", fn_lower)
    if clean_name in basename_map:
        return basename_map[clean_name], "manifest_basename", f"Manifest URL basename: {clean_name}"

    # 3. Manifest DOI slug
    for slug, oax_id in doi_manifest_map.items():
        if slug in fn_lower and len(slug) >= 6:
            return oax_id, "manifest_doi_slug", f"Manifest DOI slug: {slug}"

    # 4. Exact DOI slug in filename stem
    clean_stem = re.sub(r"\s*\(\d+\)$", "", stem)
    if clean_stem in slug_to_oax:
        return slug_to_oax[clean_stem], "exact_doi_slug", f"Filename stem matches canonical DOI slug: {clean_stem}"
    norm_stem = re.sub(r"[-_.]", "", clean_stem)
    if norm_stem in slug_to_oax:
        return slug_to_oax[norm_stem], "norm_doi_slug", f"Normalized filename stem matches canonical DOI slug: {norm_stem}"

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
            return int(res[0][0]), "mdpi_slug_pattern", f"MDPI volume/article pattern matched: {res[0][1]}"

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
            return int(res[0][0]), "springer_stem", f"Springer DOI stem matched: {res[0][1]}"

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
            # Check explicit DOIs
            doi_matches = re.findall(r"10\.\d{4,9}/[-._;()/:A-Za-z0-9]+", txt)
            for raw_d in doi_matches:
                clean_d = raw_d.rstrip(".,;()").lower()
                clean_d = re.sub(r"^https?://(dx\.)?doi\.org/", "", clean_d)
                if clean_d in doi_to_oax:
                    return doi_to_oax[clean_d], "pdf_extracted_doi", f"Extracted canonical DOI from PDF text: {clean_d}"
                d_slug = clean_d.split("/")[-1]
                if len(d_slug) >= 6 and d_slug in slug_to_oax:
                    return slug_to_oax[d_slug], "pdf_extracted_doi_slug", f"Extracted canonical DOI slug from PDF text: {d_slug}"

            # Strict title matching: candidate titles checked longest-first (>= 30 chars)
            norm_txt = normalize_str(txt)
            for length, norm_t, oax_id in title_list:
                if length >= 30 and norm_t in norm_txt:
                    return oax_id, "pdf_full_title_match", f"Full canonical title match in PDF text: {norm_t[:50]}..."
    except Exception:
        pass

    return None, "unmatched", "No canonical match found"


def main():
    parser = argparse.ArgumentParser(description="Audit and clean watched download directories.")
    parser.add_argument("--execute", action="store_true", help="Actually move missing and delete redundant duplicates")
    args = parser.parse_args()

    mode = "EXECUTE" if args.execute else "DRY-RUN"
    print(f"=== Watched Folders Auditor & Cleaner ({mode} MODE) ===")

    valid_oax_ids, doi_to_oax, slug_to_oax, title_list = load_canonical_data()
    basename_map, doi_manifest_map = load_manifest()
    con_duck = duckdb.connect()

    print(f"Canonical index: {len(valid_oax_ids):,} ERA works, {len(doi_to_oax):,} DOIs, {len(slug_to_oax):,} slugs, {len(title_list):,} titles.")
    print(f"Target repository: {TARGET_DIR} (Total existing PDFs: {len(list(TARGET_DIR.glob('*.pdf'))):,})")

    results = []

    for sdir in SOURCE_DIRS:
        if not sdir.exists():
            continue

        pdf_files = sorted(list(sdir.glob("*.pdf")) + list(sdir.glob("*.PDF")))
        print(f"\nScanning {sdir} ({len(pdf_files)} PDF files)...")

        for p in pdf_files:
            oax_id, match_type, reason = match_pdf(
                p, valid_oax_ids, basename_map, doi_manifest_map, 
                doi_to_oax, slug_to_oax, title_list, con_duck
            )

            res = {
                "path": p,
                "folder": sdir.name,
                "name": p.name,
                "size": p.stat().st_size,
                "oax_id": oax_id,
                "match_type": match_type,
                "reason": reason,
                "status": "out_of_scope",
                "action": "keep",
            }

            if oax_id is not None:
                target_dest = TARGET_DIR / f"{oax_id}.pdf"
                dest_exists_valid = is_valid_pdf(target_dest)

                if dest_exists_valid:
                    res["status"] = "in_scope_already_moved"
                    res["target_dest"] = target_dest
                    res["action"] = "rm_duplicate"
                else:
                    res["status"] = "in_scope_needs_move"
                    res["target_dest"] = target_dest
                    res["action"] = "move_to_target"

            results.append(res)

    con_duck.close()

    # Summaries
    in_scope_already_moved = [r for r in results if r["status"] == "in_scope_already_moved"]
    in_scope_needs_move = [r for r in results if r["status"] == "in_scope_needs_move"]
    out_of_scope = [r for r in results if r["status"] == "out_of_scope"]

    print(f"\n================ AUDIT SUMMARY ================")
    print(f"Total PDFs scanned across both folders: {len(results)}")
    print(f"  1. In-Scope & Already Moved:          {len(in_scope_already_moved)}")
    print(f"  2. In-Scope & Needs Move:             {len(in_scope_needs_move)}")
    print(f"  3. Out-of-Scope (Kept untouched):     {len(out_of_scope)}")
    print(f"================================================")

    print(f"\n--- [1] IN-SCOPE & ALREADY MOVED (Duplicates to remove) ({len(in_scope_already_moved)} files) ---")
    for r in in_scope_already_moved:
        print(f"  • [{r['folder']}] {r['name']}")
        print(f"      -> Target: {r['target_dest'].name} ({r['target_dest'].stat().st_size:,} bytes)")
        print(f"      -> Reason: {r['reason']} (type={r['match_type']})")

    print(f"\n--- [2] IN-SCOPE & NEEDS MOVE ({len(in_scope_needs_move)} files) ---")
    for r in in_scope_needs_move:
        print(f"  • [{r['folder']}] {r['name']}")
        print(f"      -> Target: {r['target_dest'].name}")
        print(f"      -> Reason: {r['reason']} (type={r['match_type']})")

    print(f"\n--- [3] OUT-OF-SCOPE FILES (Kept Intact) (Sample of {min(15, len(out_of_scope))} of {len(out_of_scope)}) ---")
    for r in out_of_scope[:15]:
        print(f"  • [{r['folder']}] {r['name']} ({r['size']:,} bytes)")

    if args.execute:
        print(f"\n=== APPLYING ACTIONS (--execute) ===")
        # Step A: Move missing files first
        moved_count = 0
        for r in in_scope_needs_move:
            p = r["path"]
            target = r["target_dest"]
            if is_valid_pdf(p):
                shutil.copy2(str(p), str(target))
                if is_valid_pdf(target):
                    p.unlink()
                    moved_count += 1
                    print(f"  ✓ Moved: {r['name']} -> {target.name}")
                else:
                    print(f"  ✗ Failed verification after copy: {r['name']}")
            else:
                print(f"  ✗ Source file invalid: {r['name']}")

        # Step B: Remove already moved duplicates
        deleted_count = 0
        for r in in_scope_already_moved:
            p = r["path"]
            try:
                p.unlink()
                deleted_count += 1
                print(f"  ✓ Removed duplicate: {r['folder']}/{r['name']}")
            except Exception as e:
                print(f"  ✗ Failed to remove {r['name']}: {e}")

        print(f"\nExecution Complete: {moved_count} moved, {deleted_count} duplicates removed.")
    else:
        print(f"\n(Dry-run only. No files deleted. Pass --execute to remove duplicates and move missing files.)")


if __name__ == "__main__":
    main()
