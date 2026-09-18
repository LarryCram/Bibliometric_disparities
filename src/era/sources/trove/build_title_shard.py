"""Build the parquet shard for one (ERA type, reference_year) pair from
Trove title-search responses already cached by fetch_trove_titles.py.

Phase two of the fetch/build split (mirrors build_scopus_shard.py and
build_trove_shard.py): no network requests, only reads the diskcache.Cache
at config.TROVE_DIR and re-parses whatever's already there.

Unlike the ISBN pipeline, title search has no identifier to confirm a
specific edition against - there's nothing equivalent to "does this ISBN
appear in this edition's identifier list". So this shard is deliberately
long-format: one row per (ERA record, category, candidate work) rather
than one row per ERA record. Every candidate Trove returned (up to 5 per
category, per fetch_trove_titles.py) is kept, ranked by Trove's own
relevance order within its category - deciding which category/rank to
trust for a given ERA type is exactly the analysis this shard is meant to
support, not a decision baked in here. A record with zero candidates
across every requested category gets a single "not_found" row.

For each candidate, both the work-level fields (aggregate across all of a
title's editions) and the first returned edition's Dublin Core fields are
kept side by side - the latter has the fuller author list (with ORCIDs,
where the source repository provides them) but is NOT verified as "the"
correct edition the way the ISBN pipeline's dc_for_isbn() match is; treat
edition_* fields as informative, not confirmed.

Usage: python3 build_trove_title_shard.py <era_type_slug> <era_reference_year>
Output: data/trove_title_records/<era_type_slug>/<year>.parquet
"""

import sys
from pathlib import Path

import diskcache
import pyarrow as pa
import pyarrow.parquet as pq

sys.path.insert(0, str(Path(__file__).parent))
from fetch_trove_records import TROVE_CACHE_SIZE_LIMIT
from fetch_trove_titles import TYPES, targets_for, cache_key

sys.path.insert(0, str(Path(__file__).parent.parent))
from config import TROVE_DIR

DATA_DIR = Path(__file__).parent.parent / "data"
SHARD_DIR = DATA_DIR / "trove_title_records"


def first_edition_dc(work):
    versions = work.get("version") or []
    if not versions:
        return {}, None
    rec = (versions[0].get("record") or [{}])[0]
    dc = (rec.get("metadata") or {}).get("dc") or {}
    source = (rec.get("metadataSource") or {}).get("value")
    return dc, source


def rows_for_work(era_id, era_title, era_type, year, category, rank, work):
    dc, metadata_source = first_edition_dc(work)
    creators = [c.get("name") for c in (dc.get("creator") or [])]
    return {
        "era_id": era_id,
        "era_title": era_title,
        "era_type": era_type,
        "era_year": year,
        "status": "ok",
        "category": category,
        "rank": rank,
        "trove_work_id": work.get("id"),
        "trove_url": work.get("troveUrl"),
        "work_title": work.get("title"),
        "work_contributor": work.get("contributor") or [],
        "work_issued": work.get("issued"),
        "work_type": work.get("type") or [],
        "edition_creators": creators,
        "edition_issued": [i.get("value") for i in (dc.get("issued") or [])],
        "edition_subject": dc.get("subject") or [],
        "edition_publisher": dc.get("publisher") or [],
        "metadata_source": metadata_source,
    }


def build(era_type, year):
    targets = targets_for(era_type, year)

    rows = []
    n_uncached = 0
    with diskcache.Cache(str(TROVE_DIR), size_limit=TROVE_CACHE_SIZE_LIMIT) as cache:
        for era_id, title in targets:
            data = cache.get(cache_key(title, year))
            if data is None:
                n_uncached += 1
                continue

            any_hit = False
            for cat in data.get("category", []):
                works = cat["records"].get("work", [])
                for rank, work in enumerate(works):
                    rows.append(rows_for_work(era_id, title, era_type, year, cat["code"], rank, work))
                    any_hit = True
            if not any_hit:
                rows.append({"era_id": era_id, "era_title": title, "era_type": era_type,
                             "era_year": year, "status": "not_found"})

    return rows, n_uncached, len(targets)


def main():
    if len(sys.argv) != 3 or sys.argv[1] not in TYPES:
        print(f"Usage: build_trove_title_shard.py <era_type_slug> <era_reference_year>")
        print(f"  era_type_slug one of: {', '.join(TYPES)}")
        sys.exit(1)
    era_type = sys.argv[1]
    year = int(sys.argv[2])

    rows, n_uncached, n_targets = build(era_type, year)

    if n_uncached:
        print(f"{n_uncached}/{n_targets} records for {era_type} {year} aren't cached yet - "
              f"run fetch_trove_titles.py {era_type} {year} first. Building a partial shard "
              f"from the {n_targets - n_uncached} that are cached.")

    out_dir = SHARD_DIR / era_type
    out_dir.mkdir(parents=True, exist_ok=True)
    output_file = out_dir / f"{year}.parquet"
    table = pa.Table.from_pylist(rows)
    pq.write_table(table, output_file, compression="snappy")

    n_records_with_hits = len({r["era_id"] for r in rows if r["status"] == "ok"})
    n_not_found = sum(1 for r in rows if r["status"] == "not_found")
    print(f"{era_type} {year}: {len(rows)} candidate rows across {n_records_with_hits} "
          f"records with at least one hit, {n_not_found} records with no hit at all "
          f"({n_targets - n_uncached}/{n_targets} records included)")
    print(f"Written to {output_file}")


if __name__ == "__main__":
    main()
