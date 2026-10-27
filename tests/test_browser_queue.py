"""Unit tests for interactive browser download queue generation."""

import json
from pathlib import Path
import pytest

from era.sources.fulltext.generate_browser_queue import generate_html_page


def test_generate_html_page(tmp_path: Path):
    items = [
        {
            "oax_id": 2057069908,
            "canonical_id": 5755971,
            "title": "X-ray free-electron lasers: illuminating a new path to single particle imaging",
            "year": 2013,
            "doi": "10.1080/08940886.2013.771069",
            "url": "https://doi.org/10.1080/08940886.2013.771069",
            "institution": "The University of Melbourne",
            "filename": "2057069908.pdf",
            "exists_on_disk": False,
        }
    ]

    out_file = tmp_path / "test_queue.html"
    generate_html_page(items, out_file)

    assert out_file.exists()
    content = out_file.read_text(encoding="utf-8")
    assert "2057069908.pdf" in content
    assert "X-ray free-electron lasers" in content
    assert "https://doi.org/10.1080/08940886.2013.771069" in content
    assert "The University of Melbourne" in content
