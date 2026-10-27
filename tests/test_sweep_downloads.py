"""Unit tests for sweep_downloads.py."""

import json
from pathlib import Path
from unittest.mock import patch

from era.sources.fulltext.sweep_downloads import build_manifest_lookup, sweep_once


def test_build_manifest_lookup(tmp_path: Path):
    manifest = tmp_path / "test_manifest.json"
    data = [
        {
            "oax_id": 123456,
            "filename": "123456.pdf",
            "url": "https://academic.oup.com/article-pdf/186/3/1315/test_paper.pdf",
            "doi": "10.1093/gji/ggv123",
        }
    ]
    manifest.write_text(json.dumps(data), encoding="utf-8")

    basename_map, doi_map = build_manifest_lookup(manifest)
    assert basename_map["test_paper.pdf"] == 123456
    assert doi_map["ggv123"] == 123456


def test_sweep_once_matching(tmp_path: Path):
    downloads_dir = tmp_path / "Downloads"
    target_dir = tmp_path / "era_oa_pdfs"
    downloads_dir.mkdir()
    target_dir.mkdir()

    manifest = tmp_path / "manifest.json"
    manifest.write_text(
        json.dumps([
            {
                "oax_id": 999888,
                "filename": "999888.pdf",
                "url": "https://pubs.acs.org/doi/pdf/10.1021/acs.energyfuels.5b01020",
                "doi": "10.1021/acs.energyfuels.5b01020",
            }
        ]),
        encoding="utf-8",
    )

    # Create dummy download file
    sample_file = downloads_dir / "acs.energyfuels.5b01020.pdf"
    sample_file.write_bytes(b"%PDF-1.4 test dummy")

    with patch("era.sources.fulltext.sweep_downloads.SOURCE_DIRS", [downloads_dir]), \
         patch("era.sources.fulltext.sweep_downloads.TARGET_DIR", target_dir), \
         patch("era.sources.fulltext.sweep_downloads.MANIFEST_PATH", manifest):
        moved = sweep_once()
        assert moved == 1
        assert (target_dir / "999888.pdf").exists()
