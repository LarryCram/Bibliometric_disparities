"""Download all ARC ERA Research Outputs records from the DataPortal API.

Paginates through https://dataportal.arc.gov.au/ERA/API/research-outputs
(JSON:API spec) at the maximum page size of 100 and writes every record as
one JSON line to data/era_research_outputs.jsonl.

Resumable: progress (last completed page) is checkpointed to
data/era_download_progress.json after every page, so re-running the script
picks up where it left off instead of starting over.
"""

import json
import time
from pathlib import Path

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

API_URL = "https://dataportal.arc.gov.au/ERA/API/research-outputs"
PAGE_SIZE = 100
REQUEST_DELAY_SECONDS = 0.1

DATA_DIR = Path(__file__).parent.parent / "data"
OUTPUT_FILE = DATA_DIR / "era_research_outputs.jsonl"
PROGRESS_FILE = DATA_DIR / "era_download_progress.json"


def build_session() -> requests.Session:
    session = requests.Session()
    retry = Retry(
        total=8,
        backoff_factor=2,
        status_forcelist=[429, 500, 502, 503, 504],
        allowed_methods=["GET"],
    )
    adapter = HTTPAdapter(max_retries=retry)
    session.mount("https://", adapter)
    session.mount("http://", adapter)
    session.headers.update({"Accept": "application/json"})
    return session


def load_progress() -> dict:
    if PROGRESS_FILE.exists():
        return json.loads(PROGRESS_FILE.read_text())
    return {"last_completed_page": 0}


def save_progress(progress: dict) -> None:
    PROGRESS_FILE.write_text(json.dumps(progress, indent=2))


def fetch_page(session: requests.Session, page_number: int) -> dict:
    response = session.get(
        API_URL,
        params={"page[size]": PAGE_SIZE, "page[number]": page_number},
        timeout=60,
    )
    response.raise_for_status()
    return response.json()


def main() -> None:
    DATA_DIR.mkdir(exist_ok=True)
    session = build_session()
    progress = load_progress()
    start_page = progress["last_completed_page"] + 1

    file_mode = "a" if start_page > 1 else "w"
    print(f"Starting from page {start_page} (mode={file_mode})")

    with OUTPUT_FILE.open(file_mode, encoding="utf-8") as f:
        page_number = start_page
        total_pages = None
        start_time = time.time()

        while total_pages is None or page_number <= total_pages:
            body = fetch_page(session, page_number)
            meta = body["meta"]
            total_pages = meta["total-pages"]

            for record in body["data"]:
                f.write(json.dumps(record) + "\n")
            f.flush()

            progress = {
                "last_completed_page": page_number,
                "total_pages": total_pages,
                "total_size": meta["total-size"],
            }
            save_progress(progress)

            if page_number % 50 == 0 or page_number == total_pages:
                elapsed = time.time() - start_time
                done = page_number - start_page + 1
                rate = done / elapsed if elapsed > 0 else 0
                remaining = (total_pages - page_number) / rate if rate > 0 else 0
                print(
                    f"Page {page_number}/{total_pages} "
                    f"({meta['total-size']} total records) - "
                    f"{rate:.2f} pages/s, ~{remaining / 60:.1f} min remaining"
                )

            page_number += 1
            time.sleep(REQUEST_DELAY_SECONDS)

    print(f"Done. Records written to {OUTPUT_FILE}")


if __name__ == "__main__":
    main()
