"""Unit tests for harvest_figshare.py."""

from pathlib import Path
from unittest.mock import MagicMock, patch

from era.sources.fulltext.harvest_figshare import (
    FigshareCandidate,
    download_single_figshare,
    extract_figshare_id,
)


def test_extract_figshare_id():
    assert extract_figshare_id("https://figshare.com/articles/journal_contribution/title/22892486") == "22892486"
    assert extract_figshare_id("https://figshare.com/articles/dataset/test/1234567/") == "1234567"
    assert extract_figshare_id("https://example.com/other") is None
    assert extract_figshare_id("") is None


def test_download_single_figshare_already_exists(tmp_path: Path):
    target = tmp_path / "1234.pdf"
    target.write_bytes(b"%PDF-1.4 sample content" * 200)

    cand = FigshareCandidate(
        oax_id=1234,
        fig_id="99999",
        landing_url="https://figshare.com/articles/99999",
        dest_path=target,
    )
    ok, size, msg = download_single_figshare(cand)
    assert ok is True
    assert size == target.stat().st_size
    assert msg == "already_exists"
