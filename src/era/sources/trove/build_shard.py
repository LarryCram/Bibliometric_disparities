"""Build the parquet shard for one ERA reference_year's Trove book matches
from responses already cached by fetch_trove_records.py.

Phase two of the fetch/build split (mirrors build_scopus_shard.py): this
script makes no network requests - it only reads the diskcache.Cache at
config.TROVE_DIR (keyed by cleaned ISBN) and re-parses whatever's already
there. ISBNs not yet cached are skipped and reported, not fetched - run
fetch_trove_records.py first (and again, if any are still missing).

Usage: python3 build_trove_shard.py <era_reference_year>
Output: data/trove_records/<year>.parquet
"""

import sys
from pathlib import Path

import diskcache
import pyarrow as pa
import pyarrow.parquet as pq

sys.path.insert(0, str(Path(__file__).parent))
from fetch_trove_records import isbns_for_year, parse_result, TROVE_CACHE_SIZE_LIMIT

sys.path.insert(0, str(Path(__file__).parent.parent))
from config import TROVE_DIR

DATA_DIR = Path(__file__).parent.parent / "data"
SHARD_DIR = DATA_DIR / "trove_records"


def build_year(year):
    targets = isbns_for_year(year)

    rows = []
    n_uncached = 0
    with diskcache.Cache(str(TROVE_DIR), size_limit=TROVE_CACHE_SIZE_LIMIT) as cache:
        for isbn, era_title in targets:
            isbn_clean = isbn.replace("-", "").strip()
            data = cache.get(isbn_clean)
            if data is None:
                n_uncached += 1
                continue
            rows.append(parse_result(data, isbn, isbn_clean, era_title))

    return rows, n_uncached, len(targets)


def main():
    if len(sys.argv) != 2:
        print("Usage: build_trove_shard.py <era_reference_year>")
        sys.exit(1)
    year = int(sys.argv[1])

    rows, n_uncached, n_targets = build_year(year)

    if n_uncached:
        print(f"{n_uncached}/{n_targets} ISBNs for {year} aren't cached yet - "
              f"run fetch_trove_records.py {year} first. Building a partial shard from "
              f"the {n_targets - n_uncached} that are cached.")

    SHARD_DIR.mkdir(parents=True, exist_ok=True)
    output_file = SHARD_DIR / f"{year}.parquet"
    table = pa.Table.from_pylist(rows)
    pq.write_table(table, output_file, compression="snappy")

    n_ok = sum(1 for r in rows if r["status"] == "ok")
    n_unresolved = sum(1 for r in rows if r["status"] == "edition_unresolved")
    n_not_found = sum(1 for r in rows if r["status"] == "not_found")
    print(f"{year}: {n_ok} ok, {n_unresolved} edition_unresolved, {n_not_found} not_found "
          f"({n_targets - n_uncached}/{n_targets} ISBNs included)")
    print(f"Written to {output_file}")


if __name__ == "__main__":
    main()
