"""Build the parquet shard for one ERA reference_year from Scopus Search
records already cached by fetch_scopus_records.py.

Phase two of the fetch/build split: this script makes no network requests
at all - it only reads pybliometrics' on-disk cache (via cache_path_for_query)
and, for cached queries, re-issues the same ScopusSearch call (which is a
free local cache read in that case). Mirrors fetch_scopus_records.py's
three-tier lookup per batch (see that module's docstring for why): the
original unquoted batch query, the quoted-batch retry, or one quoted
single-DOI query per DOI. A DOI found under none of those is either not
yet fetched, or permanently malformed (its query fails even quoted and
alone) - this script can't tell those apart (it never makes a request), so
it reports the DOI as "unresolved" rather than guessing.

Usage: python3 build_scopus_shard.py <era_reference_year>
Output: data/scopus_records/<year>.parquet
"""

import sys
from pathlib import Path

import pyarrow as pa
import pyarrow.parquet as pq

sys.path.insert(0, str(Path(__file__).parent))
from fetch_scopus_records import (
    init_pybliometrics, doi_list_for_year, batches, batch_query,
    cache_path_for_query, parse_doc, VIEW,
)

DATA_DIR = Path(__file__).parent.parent / "data"
SHARD_DIR = DATA_DIR / "scopus_records"


def rows_from_search(scopus, query, batch):
    """Read a cached ScopusSearch response and turn it into one row per
    DOI in `batch` (ok if found, not_found otherwise)."""
    res = scopus.ScopusSearch(query, view=VIEW)
    rows = []
    found = set()
    for doc in (res.results or []):
        row = parse_doc(doc)
        rows.append(row)
        if row["doi_normalized"]:
            found.add(row["doi_normalized"])
    for doi in batch:
        if doi.lower() not in found:
            rows.append({"doi_normalized": doi, "status": "not_found"})
    return rows


def build_year(scopus, year):
    dois = doi_list_for_year(year)
    batch_list = list(batches(dois))

    rows = []
    n_unresolved = 0
    for batch in batch_list:
        query = batch_query(batch)
        if cache_path_for_query(query).exists():
            rows.extend(rows_from_search(scopus, query, batch))
            continue

        quoted_query = batch_query(batch, quote=True)
        if cache_path_for_query(quoted_query).exists():
            rows.extend(rows_from_search(scopus, quoted_query, batch))
            continue

        # Batch-level query (either form) isn't cached - fall back to
        # per-DOI quoted queries, same as fetch_scopus_records.py's fallback.
        for doi in batch:
            single_query = batch_query([doi], quote=True)
            if not cache_path_for_query(single_query).exists():
                n_unresolved += 1
                continue
            rows.extend(rows_from_search(scopus, single_query, [doi]))

    return rows, n_unresolved, len(dois)


def main():
    if len(sys.argv) != 2:
        print("Usage: build_scopus_shard.py <era_reference_year>")
        sys.exit(1)
    year = int(sys.argv[1])

    scopus = init_pybliometrics()
    rows, n_unresolved, n_dois = build_year(scopus, year)

    if n_unresolved:
        print(f"{n_unresolved}/{n_dois} DOIs for {year} have no cache entry under any query "
              f"form yet (not yet fetched, or permanently malformed) - run "
              f"fetch_scopus_records.py {year} again if you expect more of these to resolve.")

    SHARD_DIR.mkdir(parents=True, exist_ok=True)
    output_file = SHARD_DIR / f"{year}.parquet"
    table = pa.Table.from_pylist(rows)
    pq.write_table(table, output_file, compression="snappy")

    n_ok = sum(1 for r in rows if r["status"] == "ok")
    n_not_found = sum(1 for r in rows if r["status"] == "not_found")
    print(f"{year}: {n_ok} ok, {n_not_found} not_found, {n_unresolved} unresolved "
          f"({n_dois - n_unresolved}/{n_dois} DOIs included)")
    print(f"Written to {output_file}")


if __name__ == "__main__":
    main()
