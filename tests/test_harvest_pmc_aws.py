"""Unit tests for harvest_pmc_aws.py."""

from pathlib import Path
from unittest.mock import MagicMock, patch

from era.sources.fulltext.harvest_pmc_aws import (
    PmcCandidate,
    download_single_pmc,
    extract_pmc_num,
    fetch_pmc_candidates,
)


def test_extract_pmc_num():
    assert extract_pmc_num("https://www.ncbi.nlm.nih.gov/pmc/articles/PMC4366328/") == "4366328"
    assert extract_pmc_num("https://www.ncbi.nlm.nih.gov/pmc/articles/4366328") == "4366328"
    assert extract_pmc_num("https://europepmc.org/articles/PMC1234567") == "1234567"
    assert extract_pmc_num("https://example.com/other") is None
    assert extract_pmc_num("") is None


def test_download_single_pmc_existing(tmp_path: Path):
    target = tmp_path / "12345.pdf"
    target.write_bytes(b"%PDF-1.4 sample content" * 200)

    cand = PmcCandidate(
        oax_id=12345,
        pmc_num="999999",
        landing_url="https://ncbi.nlm.nih.gov/pmc/articles/999999",
        dest_path=target,
    )
    ok, size, msg = download_single_pmc(cand)
    assert ok is True
    assert size == target.stat().st_size
    assert msg == "already_exists"


def test_download_single_pmc_mock_success(tmp_path: Path):
    target = tmp_path / "99999.pdf"
    cand = PmcCandidate(
        oax_id=99999,
        pmc_num="888888",
        landing_url="https://ncbi.nlm.nih.gov/pmc/articles/888888",
        dest_path=target,
    )

    fake_pdf = b"%PDF-1.5 " + b"0" * 2048

    mock_resp = MagicMock()
    mock_resp.status = 200
    mock_resp.read.return_value = fake_pdf
    mock_resp.__enter__.return_value = mock_resp

    with patch("urllib.request.urlopen", return_value=mock_resp):
        ok, size, msg = download_single_pmc(cand)
        assert ok is True
        assert size == len(fake_pdf)
        assert target.exists()
        assert target.read_bytes() == fake_pdf
