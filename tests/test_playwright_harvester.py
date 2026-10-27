"""Unit tests for playwright_harvester.py."""

from pathlib import Path
from unittest.mock import MagicMock, patch

from era.sources.fulltext.playwright_harvester import fetch_gold_oa_candidates, harvest_candidates


def test_fetch_gold_oa_candidates_limit():
    candidates = fetch_gold_oa_candidates(limit=5)
    assert len(candidates) <= 5
    for c in candidates:
        assert "oax_id" in c
        assert "url" in c
        assert "dest_path" in c
        assert c["url"].startswith("http")


def test_harvest_candidates_skip_existing(tmp_path: Path):
    existing_file = tmp_path / "123456.pdf"
    existing_file.write_bytes(b"%PDF-1.4 dummy content" * 500)

    candidates = [
        {
            "oax_id": 123456,
            "url": "http://example.com/test.pdf",
            "journal": "Test Journal",
            "dest_path": existing_file,
        }
    ]

    # harvest_candidates should detect dest_path.exists() and return success=1 without launching browser
    success, fail = harvest_candidates(candidates, headless=True)
    assert success == 1
    assert fail == 0


def test_fetch_gold_oa_candidates_publisher():
    candidates = fetch_gold_oa_candidates(limit=5, publisher="BMJ Open")
    for c in candidates:
        assert "bmj" in c["journal"].lower() or "bmj" in c["url"].lower()


def test_proven_publishers_list():
    from era.sources.fulltext.harvest_proven_publishers import PROVEN_PUBLISHERS

    assert len(PROVEN_PUBLISHERS) >= 5
    names = [p[0] for p in PROVEN_PUBLISHERS]
    assert "BMJ Open" in names
    assert "PNAS" in names
    assert "MDPI" in names
