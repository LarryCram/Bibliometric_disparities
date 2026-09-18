"""Warm pybliometrics' on-disk cache with full Scopus records for one ERA
reference_year, via the Scopus Search API (view=COMPLETE), packing up to 25
DOIs per query (`DOI(x) OR DOI(y) OR ...`).

This script ONLY fetches and caches - it does not build a parquet shard.
That's a deliberate two-phase split: fetching is network-bound and quota-
limited (often spanning several weekly quota resets for a big year), while
building the shard is a pure local read once the cache is populated. See
build_scopus_shard.py for phase two - run it after this script reports a
year fully cached (or partially, if you want to inspect what's fetched so
far without waiting).

Why Search instead of Abstract Retrieval: confirmed manually (see the
scopus-api-rate-limits memory) that Search's COMPLETE view returns an
identical author list to Abstract Retrieval's FULL view (same names, same
Scopus author IDs, same order), but Search's 20,000/week quota buys up to
25 documents per request versus Abstract Retrieval's 1-per-request, 10,000/
week - roughly a 50x throughput improvement. 25 is Search's own COMPLETE-
view page size cap (Elsevier's limit, not a tuning knob here) - packing
exactly 25 DOIs per query means the result always fits on the first page,
so one query is always exactly one request/quota unit, with no pagination.

Usage: python3 fetch_scopus_records.py <era_reference_year>

Resumability has two layers:
  1. The per-year DOI list is persisted to data/scopus_doi_lists/<year>.txt
     the first time it's built, sorted lexicographically, and read from
     that file (not recomputed from era_research_outputs.parquet) on every
     later run. This locks batch membership in place: pybliometrics caches
     each Scopus Search request by the md5 of its exact query string, so if
     the same 25 DOIs weren't grouped identically across runs, a changed
     grouping would silently miss the cache and re-spend quota re-fetching
     DOIs already fetched under the old grouping. Do not delete/regenerate
     a year's .txt file, or change BATCH_SIZE, once fetching has started
     for that year - either invalidates its cache alignment.
  2. Within a year, this script checks the cache file for each batch
     directly (see cache_path_for_query) and skips any batch already
     cached without even calling the API - a rerun only ever spends quota
     on batches missing from a previous run. It stops cleanly on
     Scopus429Error once the week's quota is exhausted.

DOI values are quoted in the query (`DOI("...")` not `DOI(...)`) - some
real DOIs contain literal parentheses (e.g. ASCE journal DOIs like
`10.1061/(asce)te.1943-5436.0000215`), which Scopus's query parser
otherwise reads as grouping syntax and rejects with a 400 "Error
translating query" once combined with `OR` across a whole batch (confirmed
2026-08-28 - unquoted fails, quoted succeeds, for an otherwise-identical
batch). A small number of DOIs are genuinely malformed in the ERA source
export itself (e.g. a stray trailing `)`, or literal `*` characters) and
fail even quoted - if a whole batch's query still 400s after quoting,
warm_year() falls back to querying that batch's DOIs one at a time so the
other ~24 good DOIs aren't blocked by the one bad apple, without touching
the persisted batch grouping (which build_scopus_shard.py also checks as a
fallback - see its module docstring).
"""

import sys
import configparser
from hashlib import md5
from pathlib import Path

import duckdb

sys.path.insert(0, str(Path(__file__).parent.parent))
from config import SCOPUS_DIR

DATA_DIR = Path(__file__).parent.parent / "data"
ERA_PARQUET = DATA_DIR / "era_research_outputs.parquet"
DOI_LIST_DIR = DATA_DIR / "scopus_doi_lists"
ENV_FILE = Path(__file__).parent.parent / ".env"

BATCH_SIZE = 25  # Search COMPLETE view's page size - see module docstring
VIEW = "COMPLETE"
PROGRESS_PRINT_EVERY_BATCHES = 20


def load_env_value(name):
    for line in ENV_FILE.read_text().splitlines():
        if line.strip().startswith(f"{name}="):
            return line.split("=", 1)[1].strip()
    return None


def init_pybliometrics():
    import pybliometrics.scopus as scopus
    from pybliometrics.utils.constants import DEFAULT_PATHS, CACHE_PATH

    api_key = load_env_value("sco_apikey")
    insttoken = load_env_value("sco_insttoken")
    if not api_key:
        raise RuntimeError("sco_apikey not found in .env")

    SCOPUS_DIR.mkdir(parents=True, exist_ok=True)
    config_path = SCOPUS_DIR / "config.ini"

    # Rewritten from .env on every run (rather than left in place after
    # first creation) so a rotated key/token in .env takes effect
    # immediately without manually clearing the cache directory.
    config = configparser.ConfigParser()
    config.optionxform = str
    config.add_section("Directories")
    for api, path in DEFAULT_PATHS.items():
        rel = Path(path).relative_to(CACHE_PATH)
        config.set("Directories", api, str(SCOPUS_DIR / rel))
    config.add_section("Authentication")
    config.set("Authentication", "APIKey", api_key)
    if insttoken:
        config.set("Authentication", "InstToken", insttoken)
    config.add_section("Requests")
    config.set("Requests", "Timeout", "20")
    config.set("Requests", "Retries", "5")
    with open(config_path, "w") as f:
        config.write(f)

    scopus.init(config_path=config_path)
    return scopus


def doi_list_for_year(year):
    list_file = DOI_LIST_DIR / f"{year}.txt"
    if list_file.exists():
        return [line.strip() for line in list_file.read_text().splitlines() if line.strip()]

    # ~127 of 376,155 ERA doi_normalized values (across all years) are
    # malformed (contain a literal space, duplicated "10." prefix, etc. - a
    # pre-existing data quality issue in the ERA source export, not
    # introduced here) and would just burn quota on a certain not_found.
    # Filter to the basic DOI shape before querying.
    con = duckdb.connect()
    query = f"""
        SELECT DISTINCT doi_normalized
        FROM read_parquet('{ERA_PARQUET}')
        WHERE reference_year = {int(year)}
          AND doi_normalized IS NOT NULL
          AND regexp_matches(doi_normalized, '^10\\.[0-9]{{4,9}}/\\S+$')
        ORDER BY doi_normalized
    """
    dois = [row[0] for row in con.execute(query).fetchall()]
    DOI_LIST_DIR.mkdir(parents=True, exist_ok=True)
    list_file.write_text("\n".join(dois) + "\n")
    return dois


def batches(items, size=BATCH_SIZE):
    for i in range(0, len(items), size):
        yield items[i:i + size]


def batch_query(batch, quote=False):
    # quote=False (the default, used for every batch on the first attempt)
    # preserves the exact query string - and therefore cache key - every
    # batch has used since this script's cache was first populated. Do NOT
    # make quote=True the default: that would change every batch's md5 key
    # at once and silently orphan all previously-cached batches, forcing a
    # full re-fetch. quote=True is only ever used as a fallback, for the
    # rare batch whose unquoted query 400s - see warm_year().
    if quote:
        return " OR ".join(f'DOI("{doi}")' for doi in batch)
    return " OR ".join(f"DOI({doi})" for doi in batch)


def cache_path_for_query(query, view=VIEW):
    from pybliometrics.utils import get_config
    config = get_config()
    parent = Path(config.get("Directories", "ScopusSearch"))
    stem = md5(query.encode("utf8")).hexdigest()
    return parent / view / stem


def batch_resolved(batch):
    """True if the batch's data is available under any of the cache keys
    warm_year() may have used for it: the original unquoted batch query,
    the quoted-batch fallback, or (last resort) one quoted single-DOI query
    per DOI. A batch containing a permanently malformed DOI (whose query
    will never succeed even quoted and alone) will always report
    unresolved here - that's accurate, not a bug: it genuinely can't be
    queried."""
    if cache_path_for_query(batch_query(batch)).exists():
        return True
    if cache_path_for_query(batch_query(batch, quote=True)).exists():
        return True
    return all(cache_path_for_query(batch_query([doi], quote=True)).exists() for doi in batch)


def parse_doc(doc):
    author_names = doc.author_names.split(";") if doc.author_names else []
    author_auids = doc.author_ids.split(";") if doc.author_ids else []
    return {
        "doi_normalized": doc.doi.lower() if doc.doi else None,
        "status": "ok",
        "title": doc.title,
        "author_names": author_names,
        "author_auids": author_auids,
        "n_authors": int(doc.author_count) if doc.author_count else len(author_names),
        "citedby_count": doc.citedby_count,
        "openaccess_flag": bool(int(doc.openaccess)) if doc.openaccess is not None else None,
        "eid": doc.eid,
        # affilname/affiliation_city/affiliation_country come back as
        # semicolon-joined strings when a work has multiple affiliations -
        # not necessarily one-to-one or positionally aligned with authors.
        "affiliation_name": doc.affilname,
        "affiliation_city": doc.affiliation_city,
        "affiliation_country": doc.affiliation_country,
    }


def warm_year(scopus, year):
    from pybliometrics.exception import Scopus429Error, ScopusHtmlError, ScopusQueryError

    dois = doi_list_for_year(year)
    batch_list = list(batches(dois))
    print(f"{len(dois)} distinct valid DOIs for reference_year={year} "
          f"({DOI_LIST_DIR / f'{year}.txt'}), {len(batch_list)} batches of up to {BATCH_SIZE}")

    n_already_cached = n_fetched = n_error = 0
    quota_hit = False
    for i, batch in enumerate(batch_list):
        query = batch_query(batch)
        if cache_path_for_query(query).exists():
            n_already_cached += 1
            continue
        try:
            scopus.ScopusSearch(query, view=VIEW)
            n_fetched += 1
        except Scopus429Error:
            print(f"Hit the Scopus Search weekly quota limit at batch {i} of {len(batch_list)}. "
                  f"Stopping - re-run this script after the quota resets to resume.")
            quota_hit = True
            break
        except (ScopusHtmlError, ScopusQueryError) as e:
            print(f"  batch {i}: {type(e).__name__}: {e} - retrying batch with quoted DOIs")
            n_error += 1
            quoted_query = batch_query(batch, quote=True)
            resolved_as_batch = False
            if cache_path_for_query(quoted_query).exists():
                resolved_as_batch = True
            else:
                try:
                    scopus.ScopusSearch(quoted_query, view=VIEW)
                    resolved_as_batch = True
                except Scopus429Error:
                    print(f"Hit the Scopus Search weekly quota limit mid-retry at batch {i}. "
                          f"Stopping - re-run this script after the quota resets to resume.")
                    quota_hit = True
                    break
                except (ScopusHtmlError, ScopusQueryError) as e2:
                    print(f"  batch {i}: still fails quoted ({type(e2).__name__}: {e2}) - "
                          f"falling back to per-DOI queries")
            if resolved_as_batch:
                continue

            for doi in batch:
                single_query = batch_query([doi], quote=True)
                if cache_path_for_query(single_query).exists():
                    continue
                try:
                    scopus.ScopusSearch(single_query, view=VIEW)
                except Scopus429Error:
                    print(f"Hit the Scopus Search weekly quota limit mid-fallback at batch {i}. "
                          f"Stopping - re-run this script after the quota resets to resume.")
                    quota_hit = True
                    break
                except (ScopusHtmlError, ScopusQueryError) as e2:
                    print(f"    {doi}: still fails individually ({type(e2).__name__}: {e2}) - "
                          f"permanently malformed, cannot be queried at all")
            if quota_hit:
                break

        if (i + 1) % PROGRESS_PRINT_EVERY_BATCHES == 0:
            print(f"  {i + 1}/{len(batch_list)} batches checked "
                  f"({n_already_cached} already cached, {n_fetched} fetched this run, {n_error} error)")

    n_cached_total = sum(1 for b in batch_list if batch_resolved(b))
    print(f"\n{year}: {n_cached_total}/{len(batch_list)} batches cached "
          f"({n_already_cached} already cached, {n_fetched} fetched this run, {n_error} error this run)")
    if n_cached_total == len(batch_list):
        print("Fully cached - run build_scopus_shard.py to construct the parquet shard.")
    elif quota_hit:
        print(f"{len(batch_list) - n_cached_total} batches still uncached - re-run after the quota resets.")


def main():
    if len(sys.argv) != 2:
        print("Usage: fetch_scopus_records.py <era_reference_year>")
        sys.exit(1)
    year = int(sys.argv[1])

    scopus = init_pybliometrics()
    warm_year(scopus, year)


if __name__ == "__main__":
    main()
