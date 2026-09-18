"""Normalize fetch_wos_records.py's raw WOS Starter API JSON (one object
per line in data/wos_records.jsonl) into a typed parquet table.

WOS Starter API response shape (verified manually against a real record):
{
  "uid": "WOS:...", "title": "...",
  "source": {"sourceTitle": "...", "publishYear": 2011, ...},
  "names": {"authors": [{"displayName": "...", "researcherId": "..."}, ...]},
  "citations": [{"db": "WOS", "count": 166}],
  "identifiers": {"doi": "...", "issn": "...", "eissn": "..."}
}
No affiliation/institution field is present anywhere in this response - the
Starter API tier doesn't expose it. metric_institution_country.py must
treat WOS as "no institution data available", not silently join nulls as
if that meant "no affiliation" in a meaningful sense.

Every line in the source jsonl becomes one output row regardless of status
(ok/error) - failed lookups stay visible as rows with null fields, not
silently dropped, since "WOS lookup failed" is itself a fact worth keeping.
"""

import json
from pathlib import Path

import pyarrow as pa
import pyarrow.parquet as pq

DATA_DIR = Path(__file__).parent.parent / "data"
INPUT_FILE = DATA_DIR / "wos_records.jsonl"
OUTPUT_FILE = DATA_DIR / "wos_records.parquet"


def normalize_doi(doi):
    if not doi:
        return None
    doi = doi.lower().strip()
    for prefix in ("https://doi.org/", "http://doi.org/", "https://dx.doi.org/", "doi:"):
        if doi.startswith(prefix):
            doi = doi[len(prefix):]
    return doi.strip() or None


def parse_record(rec):
    wos_uid = rec.get("wos_uid")
    status = rec.get("status")
    resp = rec.get("response") or {}

    title = resp.get("title")
    source = resp.get("source") or {}
    identifiers = resp.get("identifiers") or {}
    doi = identifiers.get("doi")

    authors_raw = (resp.get("names") or {}).get("authors") or []
    authors = [{"name": a.get("displayName"), "researcher_id": a.get("researcherId")} for a in authors_raw]

    citedby_count = None
    for c in resp.get("citations") or []:
        if c.get("db") == "WOS":
            citedby_count = c.get("count")
            break

    return {
        "wos_uid": wos_uid,
        "status": status,
        "title": title,
        "doi": doi,
        "doi_normalized": normalize_doi(doi),
        "pub_year": source.get("publishYear"),
        "source_title": source.get("sourceTitle"),
        "n_authors": len(authors),
        "authors": json.dumps(authors),
        "citedby_count": citedby_count,
    }


def main():
    if not INPUT_FILE.exists():
        print(f"{INPUT_FILE} doesn't exist yet - nothing to normalize.")
        return

    rows = []
    n_errors = 0
    with open(INPUT_FILE) as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                rows.append(parse_record(json.loads(line)))
            except (json.JSONDecodeError, AttributeError) as e:
                print(f"  skipping unparseable line: {e}")
                n_errors += 1

    table = pa.Table.from_pylist(rows)
    pq.write_table(table, OUTPUT_FILE, compression="snappy")

    n_ok = sum(1 for r in rows if r["status"] == "ok")
    print(f"{len(rows)} records ({n_ok} ok, {len(rows) - n_ok} error, {n_errors} unparseable lines skipped)")
    print(f"Written to {OUTPUT_FILE}")


if __name__ == "__main__":
    main()
