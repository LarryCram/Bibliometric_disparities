"""Generate Interactive Browser Download Queue Workbench for ERA Full-Text PDFs.

Creates a fast, compact 1-line-per-URL interactive HTML workbench allowing rapid
browser-assisted downloading of Gold Open Access ERA journal outputs directly
into ~/Downloads (to be organized into /home/lc/k/era_oa_pdfs/<oax_id>.pdf).
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


def fetch_candidate_batch(batch_size: int = DEFAULT_BATCH_SIZE, gold_only: bool = True) -> list[dict[str, Any]]:
    """Fetch batch_size undownloaded ERA journal articles.
    
    If gold_only is True (default), queries verified Gold Open Access journal articles
    with direct PDF URLs, ensuring zero login prompts or paywalls.
    """
    TARGET_DIR.mkdir(parents=True, exist_ok=True)
    existing_ids = {int(p.stem) for p in TARGET_DIR.glob("*.pdf") if p.stem.isdigit()}

    con = duckdb.connect()
    if gold_only:
        # Query verified Gold Open Access journal articles with direct PDF URLs
        query = """
            SELECT 
                l.oax_id as openalex_id,
                m.canonical_id,
                m.era_title,
                m.era_year,
                COALESCE(m.era_doi, m.oax_doi) as era_doi,
                m.research_output_type,
                COALESCE(m.institutions[1], 'Australian Institution') as primary_institution,
                l.pdf_url,
                l.source_name
            FROM 'data/oa_locations.parquet' l
            JOIN 'data/era_openalex_canonical_matches.parquet' m ON l.oax_id = m.openalex_id
            WHERE l.source_type = 'journal' 
              AND l.is_oa = true 
              AND l.pdf_url IS NOT NULL
            ORDER BY hash(l.oax_id)
            LIMIT 5000
        """
        rows = con.execute(query).fetchall()
        con.close()

        items = []
        for r in rows:
            oax_id = int(r[0])
            if oax_id in existing_ids:
                continue

            pdf_url = r[7].strip()
            raw_doi = str(r[4]).strip() if r[4] else ""
            doi_clean = raw_doi.lower().replace("http://dx.doi.org/", "").replace("https://doi.org/", "").replace("doi:", "").strip()

            raw_title = r[2].strip() if r[2] else "Untitled Record"
            title = raw_title.capitalize()

            items.append({
                "oax_id": oax_id,
                "canonical_id": int(r[1]) if r[1] else None,
                "title": title,
                "year": int(r[3]) if r[3] else None,
                "doi": doi_clean,
                "url": pdf_url,
                "journal": r[8] if r[8] else "Open Access Journal",
                "institution": r[6],
                "filename": f"{oax_id}.pdf",
                "exists_on_disk": (TARGET_DIR / f"{oax_id}.pdf").exists(),
            })

            if len(items) >= batch_size:
                break

        return items

    # Fallback to arbitrary journal articles across Australian HEPs
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
    """Generate simple, ultra-fast 1-line-per-URL interactive download table."""
    output_path.parent.mkdir(parents=True, exist_ok=True)
    items_json = json.dumps(items, indent=2)

    html_content = f"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>ERA Gold OA Fast-Click Queue ({len(items)} Papers)</title>
  <link rel="preconnect" href="https://fonts.googleapis.com">
  <link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
  <link href="https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700&family=JetBrains+Mono:wght@400;500;600&display=swap" rel="stylesheet">
  <style>
    :root {{
      --bg: #090d16;
      --panel-bg: #111827;
      --row-hover: #1e293b;
      --row-clicked: #0f172a;
      --border: #1f2937;
      --text: #f3f4f6;
      --text-muted: #9ca3af;
      --primary: #38bdf8;
      --primary-hover: #0ea5e9;
      --accent: #818cf8;
      --success: #10b981;
      --success-dim: #064e3b;
    }}

    * {{ box-sizing: border-box; margin: 0; padding: 0; }}

    body {{
      font-family: 'Inter', -apple-system, sans-serif;
      background-color: var(--bg);
      color: var(--text);
      font-size: 13px;
      line-height: 1.4;
      padding-bottom: 60px;
    }}

    header {{
      position: sticky;
      top: 0;
      z-index: 100;
      background: rgba(17, 24, 39, 0.95);
      backdrop-filter: blur(8px);
      border-bottom: 1px solid var(--border);
      padding: 12px 24px;
      display: flex;
      flex-wrap: wrap;
      justify-content: space-between;
      align-items: center;
      gap: 16px;
    }}

    .header-left {{
      display: flex;
      align-items: center;
      gap: 16px;
    }}

    h1 {{
      font-size: 16px;
      font-weight: 700;
      color: #fff;
      display: flex;
      align-items: center;
      gap: 8px;
    }}

    .badge-gold {{
      background: rgba(245, 158, 11, 0.2);
      color: #fbbf24;
      border: 1px solid rgba(245, 158, 11, 0.4);
      padding: 2px 8px;
      border-radius: 9999px;
      font-size: 11px;
      font-weight: 600;
    }}

    .progress-bar-wrap {{
      width: 140px;
      height: 8px;
      background: #1f2937;
      border-radius: 4px;
      overflow: hidden;
    }}

    .progress-bar-fill {{
      height: 100%;
      width: 0%;
      background: var(--success);
      transition: width 0.2s ease;
    }}

    .header-actions {{
      display: flex;
      align-items: center;
      gap: 10px;
    }}

    button {{
      font-family: inherit;
      font-size: 12px;
      font-weight: 600;
      padding: 6px 14px;
      border-radius: 6px;
      border: 1px solid transparent;
      cursor: pointer;
      display: inline-flex;
      align-items: center;
      gap: 6px;
      transition: all 0.15s ease;
    }}

    .btn-batch {{
      background: var(--primary);
      color: #090d16;
    }}
    .btn-batch:hover {{
      background: var(--primary-hover);
    }}

    .btn-reset {{
      background: transparent;
      border-color: var(--border);
      color: var(--text-muted);
    }}
    .btn-reset:hover {{
      color: #fff;
      border-color: #4b5563;
    }}

    .container {{
      max-width: 1600px;
      margin: 16px auto;
      padding: 0 16px;
    }}

    .info-banner {{
      background: rgba(56, 189, 248, 0.08);
      border: 1px solid rgba(56, 189, 248, 0.2);
      border-radius: 6px;
      padding: 8px 16px;
      margin-bottom: 12px;
      color: #bae6fd;
      font-size: 12px;
      display: flex;
      justify-content: space-between;
      align-items: center;
    }}

    table {{
      width: 100%;
      border-collapse: collapse;
      background: var(--panel-bg);
      border: 1px solid var(--border);
      border-radius: 8px;
      overflow: hidden;
    }}

    th {{
      background: #1f2937;
      color: var(--text-muted);
      font-weight: 600;
      text-align: left;
      padding: 10px 14px;
      border-bottom: 1px solid var(--border);
      font-size: 11px;
      text-transform: uppercase;
      letter-spacing: 0.05em;
    }}

    td {{
      padding: 9px 14px;
      border-bottom: 1px solid var(--border);
      vertical-align: middle;
      white-space: nowrap;
    }}

    tr:hover td {{
      background: var(--row-hover);
    }}

    tr.clicked td {{
      background: var(--row-clicked);
      opacity: 0.6;
    }}

    tr.clicked .title-link {{
      color: #94a3b8;
      text-decoration: line-through;
    }}

    .col-idx {{
      width: 40px;
      color: var(--text-muted);
      font-family: 'JetBrains Mono', monospace;
      font-size: 12px;
    }}

    .col-check {{
      width: 40px;
      text-align: center;
    }}

    .checkbox-custom {{
      cursor: pointer;
      width: 16px;
      height: 16px;
      accent-color: var(--success);
    }}

    .col-journal {{
      width: 220px;
      max-width: 220px;
      overflow: hidden;
      text-overflow: ellipsis;
      color: #38bdf8;
      font-weight: 500;
    }}

    .col-title {{
      max-width: 700px;
      overflow: hidden;
      text-overflow: ellipsis;
    }}

    .title-link {{
      color: #f3f4f6;
      text-decoration: none;
      font-weight: 500;
      transition: color 0.15s ease;
    }}
    .title-link:hover {{
      color: #38bdf8;
      text-decoration: underline;
    }}

    .col-id {{
      width: 140px;
      font-family: 'JetBrains Mono', monospace;
      font-size: 11px;
      color: var(--text-muted);
    }}

    .col-action {{
      width: 100px;
      text-align: right;
    }}

    .btn-open {{
      background: rgba(56, 189, 248, 0.15);
      color: #38bdf8;
      border: 1px solid rgba(56, 189, 248, 0.3);
      padding: 4px 10px;
      font-size: 11px;
      border-radius: 4px;
      text-decoration: none;
      display: inline-flex;
      align-items: center;
      gap: 4px;
    }}
    .btn-open:hover {{
      background: #38bdf8;
      color: #090d16;
    }}
  </style>
</head>
<body>
  <header>
    <div class="header-left">
      <h1>⚡ ERA Gold OA Fast-Click Queue <span class="badge-gold">Pure Gold OA</span></h1>
      <div style="display: flex; align-items: center; gap: 8px;">
        <div class="progress-bar-wrap">
          <div id="progress-bar" class="progress-bar-fill"></div>
        </div>
        <span id="progress-text" style="font-size: 12px; color: var(--text-muted); font-weight: 600;">0 / {len(items)}</span>
      </div>
    </div>
    <div class="header-actions">
      <button class="btn-batch" onclick="openNextBatch(5)">
        <span>🚀</span> Open Next 5 Tabs
      </button>
      <button class="btn-batch" onclick="openNextBatch(10)">
        <span>⚡</span> Open Next 10 Tabs
      </button>
      <button class="btn-reset" onclick="resetProgress()">
        <span>↺</span> Reset
      </button>
    </div>
  </header>

  <div class="container">
    <div class="info-banner">
      <span>💡 <strong>1-Line Direct Clicks:</strong> Click any link or "Open PDF" button to open the direct Gold OA PDF in a new tab. Files download straight into your <code>~/Downloads</code> folder.</span>
      <span style="font-family: 'JetBrains Mono', monospace; font-size: 11px;">Batch Size: {len(items)}</span>
    </div>

    <table>
      <thead>
        <tr>
          <th class="col-idx">#</th>
          <th class="col-check">Done</th>
          <th class="col-journal">Journal</th>
          <th class="col-title">Article Title (Click to Open PDF)</th>
          <th class="col-id">Target File</th>
          <th class="col-action">Action</th>
        </tr>
      </thead>
      <tbody id="queue-body">
      </tbody>
    </table>
  </div>

  <script>
    const items = {items_json};
    const STORAGE_KEY = 'era_gold_oa_clicks_{len(items)}';
    let clickedSet = new Set(JSON.parse(localStorage.getItem(STORAGE_KEY) || '[]'));

    function updateProgress() {{
      const count = clickedSet.size;
      const total = items.length;
      const pct = Math.round((count / total) * 100);
      document.getElementById('progress-bar').style.width = pct + '%';
      document.getElementById('progress-text').innerText = `${{count}} / ${{total}} (${{pct}}%)`;
      localStorage.setItem(STORAGE_KEY, JSON.stringify(Array.from(clickedSet)));
    }}

    function markClicked(idx) {{
      clickedSet.add(idx);
      const row = document.getElementById(`row-${{idx}}`);
      const cb = document.getElementById(`cb-${{idx}}`);
      if (row) row.classList.add('clicked');
      if (cb) cb.checked = true;
      updateProgress();
    }}

    function toggleRow(idx) {{
      if (clickedSet.has(idx)) {{
        clickedSet.delete(idx);
        document.getElementById(`row-${{idx}}`).classList.remove('clicked');
        document.getElementById(`cb-${{idx}}`).checked = false;
      }} else {{
        clickedSet.add(idx);
        document.getElementById(`row-${{idx}}`).classList.add('clicked');
        document.getElementById(`cb-${{idx}}`).checked = true;
      }}
      updateProgress();
    }}

    function handleClickLink(idx, url, event) {{
      markClicked(idx);
    }}

    function openNextBatch(count) {{
      let opened = 0;
      for (let i = 0; i < items.length; i++) {{
        if (!clickedSet.has(i)) {{
          const item = items[i];
          setTimeout(() => {{
            window.open(item.url, '_blank');
            markClicked(i);
          }}, opened * 250);
          opened++;
          if (opened >= count) break;
        }}
      }}
      if (opened === 0) {{
        alert("All items in this batch have already been opened!");
      }}
    }}

    function resetProgress() {{
      if (confirm("Reset click progress for this batch?")) {{
        clickedSet.clear();
        localStorage.removeItem(STORAGE_KEY);
        items.forEach((_, idx) => {{
          const row = document.getElementById(`row-${{idx}}`);
          const cb = document.getElementById(`cb-${{idx}}`);
          if (row) row.classList.remove('clicked');
          if (cb) cb.checked = false;
        }});
        updateProgress();
      }}
    }}

    function renderTable() {{
      const tbody = document.getElementById('queue-body');
      tbody.innerHTML = '';

      items.forEach((item, idx) => {{
        const isClicked = clickedSet.has(idx);
        const tr = document.createElement('tr');
        tr.id = `row-${{idx}}`;
        if (isClicked) tr.classList.add('clicked');

        tr.innerHTML = `
          <td class="col-idx">${{idx + 1}}</td>
          <td class="col-check">
            <input type="checkbox" id="cb-${{idx}}" class="checkbox-custom" ${{isClicked ? 'checked' : ''}} onclick="toggleRow(${{idx}})">
          </td>
          <td class="col-journal" title="${{item.journal}}">${{item.journal}}</td>
          <td class="col-title">
            <a href="${{item.url}}" target="_blank" rel="noopener noreferrer" class="title-link" onclick="handleClickLink(${{idx}}, '${{item.url}}', event)" title="${{item.title}}">
              ${{item.title}}
            </a>
          </td>
          <td class="col-id">${{item.filename}}</td>
          <td class="col-action">
            <a href="${{item.url}}" target="_blank" rel="noopener noreferrer" class="btn-open" onclick="handleClickLink(${{idx}}, '${{item.url}}', event)">
              PDF ↗
            </a>
          </td>
        `;
        tbody.appendChild(tr);
      }});

      updateProgress();
    }}

    renderTable();
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
