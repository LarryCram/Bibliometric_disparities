"""Generate Interactive Browser Download Queue Workbench for ERA Full-Text PDFs.

Creates a 1-click interactive HTML workbench allowing rapid browser-assisted downloading
of paywalled or bot-walled ERA journal outputs directly into /home/lc/k/era_oa_pdfs/<oax_id>.pdf.
"""

import argparse
import html
import json
import time
from pathlib import Path
from typing import Any

import duckdb

from era.config import DATA_DIR, K_DRIVE, PROJECT_ROOT

TARGET_DIR = K_DRIVE / "era_oa_pdfs"
OUTPUT_DIR = PROJECT_ROOT / "report" / "output"
DEFAULT_BATCH_SIZE = 128


def fetch_candidate_batch(batch_size: int = DEFAULT_BATCH_SIZE) -> list[dict[str, Any]]:
    """Fetch batch_size undownloaded ERA journal articles with valid DOIs."""
    TARGET_DIR.mkdir(parents=True, exist_ok=True)
    existing_ids = {int(p.stem) for p in TARGET_DIR.glob("*.pdf") if p.stem.isdigit()}

    con = duckdb.connect()
    # Query diverse journal articles with valid DOIs across Australian HEPs
    query = """
        SELECT 
            s.openalex_id,
            s.canonical_id,
            s.era_title,
            s.era_year,
            s.era_doi,
            s.research_output_type,
            COALESCE(s.institutions[1], 'Australian Institution') as primary_institution
        FROM 'data/era_multi_index_synthesis.parquet' s
        WHERE s.openalex_id IS NOT NULL
          AND s.era_doi IS NOT NULL
          AND length(trim(s.era_doi)) > 5
          AND s.research_output_type = 'Journal Article'
        ORDER BY hash(s.canonical_id)
        LIMIT 2500
    """
    rows = con.execute(query).fetchall()
    con.close()

    items = []
    for r in rows:
        oax_id = int(r[0])
        if oax_id in existing_ids:
            continue

        raw_doi = r[4].strip()
        doi_clean = raw_doi.lower().replace("http://dx.doi.org/", "").replace("https://doi.org/", "").replace("doi:", "").strip()
        url = f"https://doi.org/{doi_clean}" if not raw_doi.startswith("http") else raw_doi

        raw_title = r[2].strip() if r[2] else "Untitled Record"
        # Prettify title casing
        title = raw_title.capitalize()

        items.append({
            "oax_id": oax_id,
            "canonical_id": int(r[1]),
            "title": title,
            "year": int(r[3]) if r[3] else None,
            "doi": doi_clean,
            "url": url,
            "institution": r[6],
            "filename": f"{oax_id}.pdf",
            "exists_on_disk": (TARGET_DIR / f"{oax_id}.pdf").exists(),
        })

        if len(items) >= batch_size:
            break

    return items


def generate_html_page(items: list[dict[str, Any]], output_path: Path) -> Path:
    """Generate high-aesthetic interactive HTML download queue."""
    output_path.parent.mkdir(parents=True, exist_ok=True)
    items_json = json.dumps(items, indent=2)

    html_content = f"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>ERA Full-Text Browser Harvest Workbench (Batch of {len(items)})</title>
  <link rel="preconnect" href="https://fonts.googleapis.com">
  <link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
  <link href="https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700&family=JetBrains+Mono:wght@400;500;600&display=swap" rel="stylesheet">
  <style>
    :root {{
      --bg: #0b0f19;
      --card-bg: #131b2e;
      --card-border: #1e293b;
      --card-hover: #1e293b;
      --text-main: #f1f5f9;
      --text-muted: #94a3b8;
      --primary: #38bdf8;
      --primary-hover: #0ea5e9;
      --primary-bg: rgba(56, 189, 248, 0.12);
      --success: #10b981;
      --success-bg: rgba(16, 185, 129, 0.15);
      --success-border: #059669;
      --accent: #818cf8;
      --accent-bg: rgba(129, 140, 248, 0.15);
      --warning: #f59e0b;
      --warning-bg: rgba(245, 158, 11, 0.15);
      --border-radius: 12px;
      --transition: all 0.2s cubic-bezier(0.4, 0, 0.2, 1);
    }}

    * {{
      box-sizing: border-box;
      margin: 0;
      padding: 0;
    }}

    body {{
      font-family: 'Inter', -apple-system, BlinkMacSystemFont, sans-serif;
      background-color: var(--bg);
      color: var(--text-main);
      line-height: 1.5;
      padding-bottom: 80px;
    }}

    header {{
      position: sticky;
      top: 0;
      z-index: 100;
      background: rgba(11, 15, 25, 0.92);
      backdrop-filter: blur(12px);
      border-bottom: 1px solid var(--card-border);
      padding: 16px 32px;
    }}

    .header-content {{
      max-width: 1400px;
      margin: 0 auto;
      display: flex;
      flex-wrap: wrap;
      justify-content: space-between;
      align-items: center;
      gap: 16px;
    }}

    .title-area h1 {{
      font-size: 1.35rem;
      font-weight: 700;
      display: flex;
      align-items: center;
      gap: 10px;
      color: #fff;
    }}

    .title-area p {{
      font-size: 0.85rem;
      color: var(--text-muted);
      margin-top: 2px;
    }}

    .stats-area {{
      display: flex;
      align-items: center;
      gap: 20px;
    }}

    .progress-box {{
      display: flex;
      flex-direction: column;
      gap: 4px;
      width: 220px;
    }}

    .progress-labels {{
      display: flex;
      justify-content: space-between;
      font-size: 0.8rem;
      font-weight: 600;
      color: var(--text-muted);
    }}

    .progress-bar-bg {{
      background: #1e293b;
      height: 8px;
      border-radius: 4px;
      overflow: hidden;
    }}

    .progress-bar-fill {{
      background: linear-gradient(90deg, #38bdf8, #10b981);
      height: 100%;
      width: 0%;
      border-radius: 4px;
      transition: width 0.3s ease;
    }}

    .instructions-card {{
      max-width: 1400px;
      margin: 24px auto 16px auto;
      padding: 16px 24px;
      background: linear-gradient(135deg, rgba(30, 41, 59, 0.7), rgba(15, 23, 42, 0.9));
      border: 1px solid rgba(56, 189, 248, 0.25);
      border-radius: var(--border-radius);
      display: flex;
      align-items: flex-start;
      gap: 16px;
    }}

    .instructions-icon {{
      font-size: 1.8rem;
      line-height: 1;
    }}

    .instructions-text h3 {{
      font-size: 0.95rem;
      font-weight: 600;
      color: var(--primary);
      margin-bottom: 4px;
    }}

    .instructions-text ol {{
      margin-left: 20px;
      font-size: 0.85rem;
      color: var(--text-muted);
    }}

    .instructions-text code {{
      font-family: 'JetBrains Mono', monospace;
      background: #0f172a;
      padding: 2px 6px;
      border-radius: 4px;
      color: #38bdf8;
      border: 1px solid #1e293b;
    }}

    .toolbar {{
      max-width: 1400px;
      margin: 16px auto 24px auto;
      padding: 0 16px;
      display: flex;
      flex-wrap: wrap;
      justify-content: space-between;
      align-items: center;
      gap: 12px;
    }}

    .filter-tabs {{
      display: flex;
      background: #0f172a;
      padding: 4px;
      border-radius: 8px;
      border: 1px solid var(--card-border);
      gap: 4px;
    }}

    .tab-btn {{
      background: transparent;
      border: none;
      color: var(--text-muted);
      padding: 6px 14px;
      font-size: 0.82rem;
      font-weight: 600;
      border-radius: 6px;
      cursor: pointer;
      transition: var(--transition);
    }}

    .tab-btn.active {{
      background: var(--card-bg);
      color: #fff;
      box-shadow: 0 2px 4px rgba(0,0,0,0.2);
    }}

    .search-box {{
      flex: 1;
      max-width: 360px;
      position: relative;
    }}

    .search-box input {{
      width: 100%;
      background: #0f172a;
      border: 1px solid var(--card-border);
      color: #fff;
      padding: 8px 14px;
      border-radius: 8px;
      font-size: 0.85rem;
      outline: none;
      transition: var(--transition);
    }}

    .search-box input:focus {{
      border-color: var(--primary);
    }}

    .cards-container {{
      max-width: 1400px;
      margin: 0 auto;
      padding: 0 16px;
      display: grid;
      grid-template-columns: repeat(auto-fill, minmax(420px, 1fr));
      gap: 16px;
    }}

    .card {{
      background: var(--card-bg);
      border: 1px solid var(--card-border);
      border-radius: var(--border-radius);
      padding: 18px 20px;
      display: flex;
      flex-direction: column;
      justify-content: space-between;
      gap: 14px;
      transition: var(--transition);
      position: relative;
    }}

    .card:hover {{
      border-color: rgba(56, 189, 248, 0.4);
      transform: translateY(-2px);
      box-shadow: 0 8px 20px rgba(0,0,0,0.3);
    }}

    .card.completed {{
      border-color: var(--success-border);
      background: linear-gradient(135deg, rgba(16, 185, 129, 0.05), rgba(19, 27, 46, 0.95));
      opacity: 0.85;
    }}

    .card-top {{
      display: flex;
      justify-content: space-between;
      align-items: center;
      font-size: 0.75rem;
    }}

    .index-badge {{
      background: #1e293b;
      color: #94a3b8;
      padding: 2px 8px;
      border-radius: 6px;
      font-weight: 700;
      font-family: 'JetBrains Mono', monospace;
    }}

    .meta-tags {{
      display: flex;
      gap: 6px;
    }}

    .tag {{
      padding: 2px 8px;
      border-radius: 4px;
      font-size: 0.72rem;
      font-weight: 600;
    }}

    .tag-inst {{
      background: var(--accent-bg);
      color: #a5b4fc;
      max-width: 180px;
      white-space: nowrap;
      overflow: hidden;
      text-overflow: ellipsis;
    }}

    .tag-year {{
      background: rgba(148, 163, 184, 0.15);
      color: #cbd5e1;
    }}

    .article-title {{
      font-size: 0.92rem;
      font-weight: 600;
      color: #f8fafc;
      line-height: 1.4;
      display: -webkit-box;
      -webkit-line-clamp: 3;
      -webkit-box-orient: vertical;
      overflow: hidden;
    }}

    .filename-box {{
      background: #0f172a;
      border: 1px solid #1e293b;
      border-radius: 8px;
      padding: 8px 12px;
      display: flex;
      align-items: center;
      justify-content: space-between;
      font-family: 'JetBrains Mono', monospace;
      font-size: 0.82rem;
    }}

    .filename-text {{
      color: #38bdf8;
      font-weight: 500;
      user-select: all;
    }}

    .btn-copy-name {{
      background: #1e293b;
      border: 1px solid #334155;
      color: #cbd5e1;
      padding: 4px 8px;
      font-size: 0.72rem;
      font-weight: 600;
      border-radius: 4px;
      cursor: pointer;
      transition: var(--transition);
    }}

    .btn-copy-name:hover {{
      background: #334155;
      color: #fff;
    }}

    .card-actions {{
      display: flex;
      gap: 8px;
      align-items: center;
    }}

    .btn-open-copy {{
      flex: 1;
      background: linear-gradient(135deg, #0284c7, #2563eb);
      color: #fff;
      border: none;
      padding: 9px 12px;
      font-size: 0.82rem;
      font-weight: 600;
      border-radius: 8px;
      cursor: pointer;
      display: flex;
      align-items: center;
      justify-content: center;
      gap: 6px;
      text-decoration: none;
      transition: var(--transition);
    }}

    .btn-open-copy:hover {{
      background: linear-gradient(135deg, #0369a1, #1d4ed8);
      box-shadow: 0 4px 12px rgba(37, 99, 235, 0.4);
    }}

    .btn-toggle-done {{
      background: #1e293b;
      border: 1px solid var(--card-border);
      color: var(--text-muted);
      padding: 9px 12px;
      font-size: 0.82rem;
      font-weight: 600;
      border-radius: 8px;
      cursor: pointer;
      transition: var(--transition);
      display: flex;
      align-items: center;
      gap: 6px;
    }}

    .btn-toggle-done:hover {{
      background: rgba(16, 185, 129, 0.2);
      border-color: var(--success);
      color: #10b981;
    }}

    .card.completed .btn-toggle-done {{
      background: var(--success-bg);
      border-color: var(--success);
      color: #10b981;
    }}

    .toast {{
      position: fixed;
      bottom: 24px;
      right: 24px;
      background: #10b981;
      color: #fff;
      font-weight: 600;
      font-size: 0.85rem;
      padding: 10px 18px;
      border-radius: 8px;
      box-shadow: 0 10px 25px rgba(0,0,0,0.4);
      z-index: 1000;
      opacity: 0;
      transform: translateY(20px);
      transition: all 0.25s ease;
      pointer-events: none;
      display: flex;
      align-items: center;
      gap: 8px;
    }}

    .toast.show {{
      opacity: 1;
      transform: translateY(0);
    }}
  </style>
</head>
<body>

  <header>
    <div class="header-content">
      <div class="title-area">
        <h1><span>⚡</span> ERA Full-Text Browser Harvest Queue</h1>
        <p>128 High-Priority Undownloaded Journal Articles with Direct DOI Links</p>
      </div>

      <div class="stats-area">
        <div class="progress-box">
          <div class="progress-labels">
            <span>Progress</span>
            <span id="progress-text">0 / {len(items)} (0%)</span>
          </div>
          <div class="progress-bar-bg">
            <div class="progress-bar-fill" id="progress-fill"></div>
          </div>
        </div>
      </div>
    </div>
  </header>

  <div class="instructions-card">
    <div class="instructions-icon">💡</div>
    <div class="instructions-text">
      <h3>Streamlined 3-Second Workflow:</h3>
      <ol>
        <li>Set browser default download directory to: <code>/home/lc/k/era_oa_pdfs/</code></li>
        <li>Click <b>"🚀 Open & Copy Name"</b> on any card (copies required filename and opens paper in a new tab).</li>
        <li>In the opened tab, press <code>Ctrl+S</code> (Save) &rarr; paste filename (<code>Ctrl+V</code>) &rarr; hit <code>Enter</code>!</li>
        <li>Click <b>"✓ Done"</b> to check off the paper.</li>
      </ol>
    </div>
  </div>

  <div class="toolbar">
    <div class="filter-tabs">
      <button class="tab-btn active" onclick="setFilter('all')">All ({len(items)})</button>
      <button class="tab-btn" onclick="setFilter('pending')" id="tab-pending">Pending</button>
      <button class="tab-btn" onclick="setFilter('completed')" id="tab-completed">Completed</button>
    </div>

    <div class="search-box">
      <input type="text" id="search-input" placeholder="Search title, DOI, institution..." oninput="handleSearch()">
    </div>
  </div>

  <main class="cards-container" id="cards-container">
    <!-- Cards rendered via JS -->
  </main>

  <div class="toast" id="toast">📋 Copied filename to clipboard!</div>

  <script>
    const items = {items_json};
    const STORAGE_KEY = 'era_harvest_batch_128';
    let completedSet = new Set(JSON.parse(localStorage.getItem(STORAGE_KEY) || '[]'));
    let currentFilter = 'all';
    let searchTerm = '';

    function saveState() {{
      localStorage.setItem(STORAGE_KEY, JSON.stringify(Array.from(completedSet)));
      updateProgress();
    }}

    function copyToClipboard(text, e) {{
      if (e) e.stopPropagation();
      navigator.clipboard.writeText(text).then(() => {{
        showToast(`📋 Copied: ${{text}}`);
      }});
    }}

    function handleOpenAndCopy(url, filename, index, e) {{
      navigator.clipboard.writeText(filename).then(() => {{
        showToast(`📋 Copied: ${{filename}} (Opening paper...)`);
        window.open(url, '_blank', 'noopener,noreferrer');
      }});
    }}

    function toggleDone(oaxId, cardId) {{
      const card = document.getElementById(cardId);
      if (completedSet.has(oaxId)) {{
        completedSet.delete(oaxId);
        card.classList.remove('completed');
      }} else {{
        completedSet.add(oaxId);
        card.classList.add('completed');
      }}
      saveState();
      filterCards();
    }}

    function showToast(msg) {{
      const toast = document.getElementById('toast');
      toast.textContent = msg;
      toast.classList.add('show');
      setTimeout(() => {{
        toast.classList.remove('show');
      }}, 2200);
    }}

    function updateProgress() {{
      const total = items.length;
      const done = completedSet.size;
      const pct = Math.round((done / total) * 100);
      document.getElementById('progress-text').textContent = `${{done}} / ${{total}} (${{pct}}%)`;
      document.getElementById('progress-fill').style.width = `${{pct}}%`;
      document.getElementById('tab-pending').textContent = `Pending (${{total - done}})`;
      document.getElementById('tab-completed').textContent = `Completed (${{done}})`;
    }}

    function setFilter(filter) {{
      currentFilter = filter;
      document.querySelectorAll('.tab-btn').forEach(btn => btn.classList.remove('active'));
      event.target.classList.add('active');
      filterCards();
    }}

    function handleSearch() {{
      searchTerm = document.getElementById('search-input').value.toLowerCase().trim();
      filterCards();
    }}

    function filterCards() {{
      items.forEach((item, idx) => {{
        const card = document.getElementById(`card-${{idx}}`);
        const isDone = completedSet.has(item.oax_id);
        const matchesSearch = !searchTerm || 
          item.title.toLowerCase().includes(searchTerm) || 
          item.doi.toLowerCase().includes(searchTerm) || 
          item.institution.toLowerCase().includes(searchTerm);

        let matchesFilter = true;
        if (currentFilter === 'pending' && isDone) matchesFilter = false;
        if (currentFilter === 'completed' && !isDone) matchesFilter = false;

        if (matchesSearch && matchesFilter) {{
          card.style.display = 'flex';
        }} else {{
          card.style.display = 'none';
        }}
      }});
    }}

    function renderCards() {{
      const container = document.getElementById('cards-container');
      container.innerHTML = '';

      items.forEach((item, idx) => {{
        const isDone = completedSet.has(item.oax_id);
        const card = document.createElement('div');
        card.className = `card ${{isDone ? 'completed' : ''}}`;
        card.id = `card-${{idx}}`;

        card.innerHTML = `
          <div>
            <div class="card-top">
              <span class="index-badge">#${{idx + 1}}</span>
              <div class="meta-tags">
                <span class="tag tag-inst" title="${{item.institution}}">${{item.institution}}</span>
                <span class="tag tag-year">${{item.year || '—'}}</span>
              </div>
            </div>

            <h2 class="article-title" style="margin-top: 10px;">${{item.title}}</h2>
          </div>

          <div style="display: flex; flex-direction: column; gap: 10px;">
            <div class="filename-box">
              <span class="filename-text">${{item.filename}}</span>
              <button class="btn-copy-name" onclick="copyToClipboard('${{item.filename}}', event)" title="Copy filename only">📋 Copy</button>
            </div>

            <div class="card-actions">
              <button class="btn-open-copy" onclick="handleOpenAndCopy('${{item.url}}', '${{item.filename}}', ${{idx}}, event)">
                <span>🚀</span> Open & Copy Name
              </button>
              <button class="btn-toggle-done" onclick="toggleDone(${{item.oax_id}}, 'card-${{idx}}')">
                <span>✓</span> Done
              </button>
            </div>
          </div>
        `;
        container.appendChild(card);
      }});

      updateProgress();
    }}

    renderCards();
  </script>
</body>
</html>
"""
    output_path.write_text(html_content, encoding="utf-8")
    return output_path


def main():
    parser = argparse.ArgumentParser(description="Generate interactive browser download queue for ERA full-text PDFs")
    parser.add_argument("--size", type=int, default=DEFAULT_BATCH_SIZE, help="Batch size (default: 128)")
    parser.add_argument("--check", action="store_true", help="Check current on-disk download status of the batch")
    args = parser.parse_args()

    manifest_path = OUTPUT_DIR / "batch128_manifest.json"
    html_path = OUTPUT_DIR / "batch128_browser_queue.html"

    if args.check and manifest_path.exists():
        with open(manifest_path, "r", encoding="utf-8") as f:
            items = json.load(f)
        downloaded = sum(1 for it in items if (TARGET_DIR / it["filename"]).exists() and (TARGET_DIR / it["filename"]).stat().st_size > 4096)
        print(f"Batch Status: {downloaded} / {len(items)} downloaded in {TARGET_DIR} ({downloaded/len(items)*100:.1f}%)")
        return

    print(f"Fetching {args.size} candidate journal articles...")
    items = fetch_candidate_batch(args.size)
    print(f"Loaded {len(items)} items across Australian HEPs.")

    # Save manifest JSON
    with open(manifest_path, "w", encoding="utf-8") as f:
        json.dump(items, f, indent=2)
    print(f"Saved manifest: {manifest_path}")

    # Generate HTML
    out_html = generate_html_page(items, html_path)
    print(f"Saved interactive HTML workbench: {out_html}")
    print("\nTo begin:")
    print(f"  Open in your browser: file://{out_html.resolve()}")


if __name__ == "__main__":
    main()
