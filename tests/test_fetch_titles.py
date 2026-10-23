"""Unit tests for fetch_titles.py."""

from unittest.mock import MagicMock, patch
import pyarrow as pa
import pyarrow.parquet as pq

from era.sources.scopus.fetch_titles import (
    clean_title_for_scopus_query,
    build_scopus_title_query,
    execute_title_search_with_backoff,
    get_non_doi_canonical_titles,
)


class TestTitleQueryHygiene:
    def test_clean_title_removes_internal_quotes_and_braces(self):
        raw = 'A study of "quantum dots" and {superconductors} in \\Australia/'
        cleaned = clean_title_for_scopus_query(raw)
        assert '"' not in cleaned
        assert "{" not in cleaned
        assert "}" not in cleaned
        assert "\\" not in cleaned
        assert "A study of quantum dots and superconductors in Australia/" == cleaned

    def test_clean_title_unescapes_html(self):
        raw = "Structure &amp; function of protein X &gt; Y"
        cleaned = clean_title_for_scopus_query(raw)
        assert cleaned == "Structure & function of protein X > Y"

    def test_build_scopus_title_query_wraps_in_quotes(self):
        q = build_scopus_title_query("Quantum Computing Architecture")
        assert q == 'TITLE("Quantum Computing Architecture")'


class TestExecuteTitleSearchWithBackoff:
    def test_cached_fast_path(self):
        mock_scopus = MagicMock()
        mock_search = MagicMock()
        mock_search.get_results_size.return_value = 1
        mock_scopus.ScopusSearch.return_value = mock_search

        with patch("era.sources.scopus.fetch_titles.cache_path_for_query") as mock_cache:
            mock_cache.return_value.exists.return_value = True
            s, status = execute_title_search_with_backoff(mock_scopus, 'TITLE("Cached Title")')
            assert status == "cached_hit"
            assert s == mock_search

    def test_api_success_first_attempt(self):
        mock_scopus = MagicMock()
        mock_search = MagicMock()
        mock_search.get_results_size.return_value = 1
        mock_scopus.ScopusSearch.return_value = mock_search

        with patch("era.sources.scopus.fetch_titles.cache_path_for_query") as mock_cache:
            mock_cache.return_value.exists.return_value = False
            s, status = execute_title_search_with_backoff(mock_scopus, 'TITLE("New Title")', polite_delay=0.0)
            assert status == "api_hit"
            assert s == mock_search
            assert mock_scopus.ScopusSearch.call_count == 1

    def test_retry_on_429_then_succeeds(self):
        from pybliometrics.exception import Scopus429Error
        mock_scopus = MagicMock()
        mock_search = MagicMock()
        mock_search.get_results_size.return_value = 1

        # Fails once with 429, then succeeds
        mock_scopus.ScopusSearch.side_effect = [Scopus429Error("429 Too Many Requests"), mock_search]

        with patch("era.sources.scopus.fetch_titles.cache_path_for_query") as mock_cache:
            mock_cache.return_value.exists.return_value = False
            with patch("time.sleep") as mock_sleep:
                s, status = execute_title_search_with_backoff(
                    mock_scopus, 'TITLE("Retry Title")', max_retries=3, base_backoff=0.01, polite_delay=0.0
                )
                assert status == "api_hit"
                assert s == mock_search
                assert mock_scopus.ScopusSearch.call_count == 2
                assert mock_sleep.call_count >= 1

    def test_syntax_error_not_retried(self):
        from pybliometrics.exception import ScopusQueryError
        mock_scopus = MagicMock()
        mock_scopus.ScopusSearch.side_effect = ScopusQueryError("Error translating query")

        with patch("era.sources.scopus.fetch_titles.cache_path_for_query") as mock_cache:
            mock_cache.return_value.exists.return_value = False
            s, status = execute_title_search_with_backoff(
                mock_scopus, 'TITLE("Syntax Error Title")', max_retries=4
            )
            assert s is None
            assert status.startswith("syntax_error")
            assert mock_scopus.ScopusSearch.call_count == 1  # Not retried


class TestGetNonDOICanonicalTitles:
    def test_filtering_and_loading(self, tmp_path):
        records = [
            # Should be included: Journal Article without DOI, length >= 15
            {
                "canonical_id": 1,
                "research_output_type": "Journal Article",
                "title": "Quantum Physics Analysis in Sydney",
                "outlet": "Nature Physics",
                "doi": None,
                "doi_wellformed": None,
            },
            # Excluded: has valid DOI
            {
                "canonical_id": 2,
                "research_output_type": "Journal Article",
                "title": "Quantum Physics with Valid DOI",
                "outlet": "Nature Physics",
                "doi": "10.1038/xyz",
                "doi_wellformed": True,
            },
            # Excluded: Book Chapter (different output type)
            {
                "canonical_id": 3,
                "research_output_type": "Book Chapter",
                "title": "A Long Book Chapter Title Without DOI",
                "outlet": "Book Title",
                "doi": None,
                "doi_wellformed": None,
            },
            # Excluded: Title too short (< 15 chars)
            {
                "canonical_id": 4,
                "research_output_type": "Journal Article",
                "title": "Short title",
                "outlet": "Journal",
                "doi": None,
                "doi_wellformed": None,
            },
        ]
        test_pq = tmp_path / "canonical_test.parquet"
        pq.write_table(pa.Table.from_pylist(records), test_pq)

        titles = get_non_doi_canonical_titles(
            canonical_pq=test_pq,
            output_type="Journal Article",
            min_length=15,
        )
        assert len(titles) == 1
        assert titles[0][0] == 1
        assert titles[0][1] == "Quantum Physics Analysis in Sydney"
