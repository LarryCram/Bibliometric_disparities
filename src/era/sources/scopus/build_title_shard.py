"""Build the parquet shard for Scopus title searches cached by fetch_titles.py.

Reads pybliometrics' on-disk cache for all non-DOI canonical ERA Journal Articles,
extracts matched document metadata using parse_doc(), and outputs:
data/scopus_records/titles.parquet

Because match_scopus.py already reads data/scopus_records/*.parquet, any records
compiled here are automatically ingested into the matching pipeline.
"""

from pathlib import Path
import pyarrow as pa
import pyarrow.parquet as pq

from era.config import DATA_DIR
from era.sources.scopus.fetch import (
    VIEW,
    cache_path_for_query,
    init_pybliometrics,
    parse_doc,
)
from era.sources.scopus.fetch_titles import (
    build_scopus_title_query,
    clean_title_for_scopus_query,
    get_non_doi_canonical_titles,
)

OUTPUT_PARQUET = DATA_DIR / "scopus_records" / "titles.parquet"


def build_title_shard(output_pq: Path = OUTPUT_PARQUET, min_length: int = 15) -> int:
    """Scan cached title queries in pybliometrics and compile titles.parquet."""
    print("Initializing pybliometrics...")
    scopus = init_pybliometrics()

    print(f"Loading candidate non-DOI titles from canonical ERA baseline...")
    candidates = get_non_doi_canonical_titles(min_length=min_length)
    print(f"Loaded {len(candidates):,} candidate works.")

    rows = []
    n_cached_hits = 0
    n_cached_misses = 0
    n_uncached = 0

    seen_eids = set()

    for cid, title, outlet in candidates:
        clean_title = clean_title_for_scopus_query(title)
        if len(clean_title) < min_length:
            continue

        query = build_scopus_title_query(clean_title)
        cache_file = cache_path_for_query(query, view=VIEW)

        if not cache_file.exists():
            n_uncached += 1
            continue

        try:
            s = scopus.ScopusSearch(query, view=VIEW, refresh=False)
            results = s.results or []
            if results:
                n_cached_hits += 1
                for doc in results:
                    row = parse_doc(doc)
                    if row["eid"] not in seen_eids:
                        seen_eids.add(row["eid"])
                        rows.append(row)
            else:
                n_cached_misses += 1
        except Exception as e:
            print(f"  Error reading cached query for {clean_title[:40]}...: {e}")

    print(f"Cache status: {n_cached_hits:,} hits, {n_cached_misses:,} misses, {n_uncached:,} uncached/pending.")
    print(f"Unique Scopus documents extracted: {len(rows):,}")

    if rows:
        output_pq.parent.mkdir(parents=True, exist_ok=True)
        table = pa.Table.from_pylist(rows)
        pq.write_table(table, output_pq, compression="snappy")
        print(f"Successfully wrote {len(rows):,} documents to {output_pq}!")
    else:
        print("No cached rows found to write yet.")

    return len(rows)


def main():
    build_title_shard()


if __name__ == "__main__":
    main()
