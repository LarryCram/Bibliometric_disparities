"""Resolve a real PDF URL out of an OA repository landing page.

Many OpenAlex-advertised "OA copies" are actually landing pages (an
abstract/cover page with a download link/button), not the PDF binary
itself - confirmed in download_oa_pdfs_direct.py's validation pass, where ~36% of
best_oa_pdf_url responses turn out to be HTML. These pages are legitimately
advertised as open-access article versions (not paywalled content being
scraped around), so resolving the actual PDF link off them is fair game -
this just finds the link the page itself is pointing at.

citation_pdf_url is a near-universal convention among institutional
repositories and publishers (it's what Google Scholar indexes off), and
covers about half of the genuine (non-bot-walled) landing pages sampled
from this project's data. There's no broader fallback here by design - a
generic "grab any <a href=*.pdf>" heuristic risks picking up a wrong
PDF (e.g. a supplementary file, a different article's link on the same
page) rather than failing cleanly.
"""

import re
from urllib.parse import urljoin

CITATION_PDF_RE = re.compile(
    r'<meta[^>]+name=["\']citation_pdf_url["\'][^>]+content=["\']([^"\']+)["\']',
    re.IGNORECASE,
)
# Some pages put content before name in the tag - handle both attribute orders.
CITATION_PDF_RE_ALT = re.compile(
    r'<meta[^>]+content=["\']([^"\']+)["\'][^>]+name=["\']citation_pdf_url["\']',
    re.IGNORECASE,
)


def resolve_pdf_url(html_text, page_url):
    """Return an absolute PDF URL found in the landing page HTML, or None."""
    match = CITATION_PDF_RE.search(html_text) or CITATION_PDF_RE_ALT.search(html_text)
    if not match:
        return None
    return urljoin(page_url, match.group(1))
