"""Convert data/era_research_outputs.jsonl into a Parquet file with clean,
typed join keys for matching against a local OpenAlex snapshot.

Match priority (per user direction): DOI first, then ISBN/ISSN, then title.
Publication year is deliberately NOT promoted into a hard join key here -
ERA's reference-year can diverge from OpenAlex's publication_year, so it's
left as reference_year for soft/tie-breaking use at query time.
"""

import json
import re
import unicodedata
from pathlib import Path

import pyarrow as pa
import pyarrow.parquet as pq

DATA_DIR = Path(__file__).parent.parent / "data"
INPUT_FILE = DATA_DIR / "era_research_outputs.jsonl"
OUTPUT_FILE = DATA_DIR / "era_research_outputs.parquet"
PROGRESS_FILE = DATA_DIR / "era_download_progress.json"

BATCH_SIZE = 50_000

SCHEMA = pa.schema([
    ("id", pa.int64()),
    ("era_round", pa.string()),
    ("institution", pa.string()),
    ("research_output_type", pa.string()),
    ("title", pa.string()),
    ("title_normalized", pa.string()),
    ("outlet", pa.string()),
    ("reference_year", pa.int32()),
    ("doi", pa.string()),
    ("doi_normalized", pa.string()),
    ("isbn_normalized", pa.string()),
    ("issn_normalized", pa.string()),
    ("standard_number", pa.string()),
    ("place_of_publication", pa.string()),
    ("other_details", pa.string()),
])

_PUNCT_RE = re.compile(r"[^a-z0-9\s]")
_WHITESPACE_RE = re.compile(r"\s+")
_ISBN_ISSN_RE = re.compile(r"[^0-9Xx]")
_DOI_PREFIX_RE = re.compile(r"^(https?://(dx\.)?doi\.org/|doi:\s*)", re.IGNORECASE)


def normalize_title(title):
    if not title:
        return None
    text = unicodedata.normalize("NFKD", title)
    text = text.encode("ascii", "ignore").decode("ascii")
    text = text.lower()
    text = _PUNCT_RE.sub(" ", text)
    text = _WHITESPACE_RE.sub(" ", text).strip()
    return text or None


def normalize_doi(doi):
    if not doi:
        return None
    text = _DOI_PREFIX_RE.sub("", doi.strip()).lower().strip()
    return text or None


def normalize_isbn_issn(value):
    if not value:
        return None
    text = _ISBN_ISSN_RE.sub("", value).upper()
    return text or None


def to_row(record):
    attrs = record["attributes"]
    other = attrs.get("other-details") or {}

    isbn = other.get("isbn") or other.get("outlet-isbn")
    issn = other.get("issn")
    doi = attrs.get("doi")
    title = attrs.get("title")

    return {
        "id": int(record["id"]),
        "era_round": attrs.get("era-round"),
        "institution": attrs.get("institution"),
        "research_output_type": attrs.get("research-output-type"),
        "title": title,
        "title_normalized": normalize_title(title),
        "outlet": attrs.get("outlet"),
        "reference_year": attrs.get("reference-year"),
        "doi": doi,
        "doi_normalized": normalize_doi(doi),
        "isbn_normalized": normalize_isbn_issn(isbn),
        "issn_normalized": normalize_isbn_issn(issn),
        "standard_number": other.get("standard-number"),
        "place_of_publication": attrs.get("place-of-publication"),
        "other_details": json.dumps(other) if other else None,
    }


def check_download_complete():
    if not PROGRESS_FILE.exists():
        print(f"Warning: {PROGRESS_FILE} not found, cannot verify download is complete.")
        return
    progress = json.loads(PROGRESS_FILE.read_text())
    if progress.get("last_completed_page") != progress.get("total_pages"):
        print(
            f"Warning: download appears incomplete "
            f"(page {progress.get('last_completed_page')}/{progress.get('total_pages')}). "
            f"Converting available data anyway."
        )


def main():
    check_download_complete()

    writer = pq.ParquetWriter(OUTPUT_FILE, SCHEMA, compression="snappy")
    rows = []
    total_rows = 0

    with INPUT_FILE.open(encoding="utf-8") as f:
        for line in f:
            rows.append(to_row(json.loads(line)))
            if len(rows) >= BATCH_SIZE:
                writer.write_table(pa.Table.from_pylist(rows, schema=SCHEMA))
                total_rows += len(rows)
                print(f"Wrote {total_rows} rows...")
                rows = []

    if rows:
        writer.write_table(pa.Table.from_pylist(rows, schema=SCHEMA))
        total_rows += len(rows)

    writer.close()
    print(f"Done. {total_rows} rows written to {OUTPUT_FILE}")


if __name__ == "__main__":
    main()
