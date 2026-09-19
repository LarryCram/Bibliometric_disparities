"""Generate formatted publication-quality tables (.docx, .md, .csv) for the
bibliometric disparities manuscript based on the ERA dataset and Olensky (2015)
inaccuracy classification.
"""

from pathlib import Path
import csv
import duckdb
import docx
from docx import Document
from docx.shared import Inches, Pt, RGBColor
from docx.enum.text import WD_ALIGN_PARAGRAPH
from docx.enum.table import WD_TABLE_ALIGNMENT, WD_ALIGN_VERTICAL
from docx.oxml import OxmlElement, parse_xml
from docx.oxml.ns import qn, nsdecls

from era.config import DATA_DIR, PROJECT_ROOT

OUTPUT_DIR = PROJECT_ROOT / "report" / "output" / "tables"
OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

ERA_PARQUET = DATA_DIR / "era_research_outputs.parquet"
DUPLICATES_PARQUET = DATA_DIR / "era_cross_institution_duplicates.parquet"
PROCESSED_PARQUET = DATA_DIR / "era_research_outputs_processed.parquet"


def set_cell_margins(cell, top=100, bottom=100, left=150, right=150):
    """Set cell padding in dxa (1 pt = 20 dxa)."""
    tcPr = cell._tc.get_or_add_tcPr()
    tcMar = OxmlElement('w:tcMar')
    for m, val in [('top', top), ('bottom', bottom), ('left', left), ('right', right)]:
        node = OxmlElement(f'w:{m}')
        node.set(qn('w:w'), str(val))
        node.set(qn('w:type'), 'dxa')
        tcMar.append(node)
    tcPr.append(tcMar)


def set_cell_shading(cell, color_hex="F2F4F7"):
    """Set cell background shading."""
    tcPr = cell._tc.get_or_add_tcPr()
    shd = parse_xml(f'<w:shd {nsdecls("w")} w:fill="{color_hex}"/>')
    tcPr.append(shd)


def apply_apa_table_borders(table):
    """Apply standard academic APA table borders: top, bottom, and header bottom lines."""
    tblPr = table._tbl.tblPr
    # Remove existing borders if any
    for child in list(tblPr):
        if child.tag.endswith('tblBorders'):
            tblPr.remove(child)

    tblBorders = parse_xml(
        f'<w:tblBorders {nsdecls("w")}>'
        f'<w:top w:val="single" w:sz="8" w:space="0" w:color="333333"/>'
        f'<w:bottom w:val="single" w:sz="8" w:space="0" w:color="333333"/>'
        f'<w:insideH w:val="none"/>'
        f'<w:insideV w:val="none"/>'
        f'<w:left w:val="none"/>'
        f'<w:right w:val="none"/>'
        f'</w:tblBorders>'
    )
    tblPr.append(tblBorders)


def style_table_header_row(row, bg_hex="F8F9FA"):
    """Format table header row: bold, subtle shading, bottom border, prevent split."""
    trPr = row._tr.get_or_add_trPr()
    trPr.append(parse_xml(f'<w:tblHeader {nsdecls("w")}/>'))
    trPr.append(parse_xml(f'<w:cantSplit {nsdecls("w")}/>'))

    for cell in row.cells:
        set_cell_margins(cell, top=120, bottom=120, left=140, right=140)
        set_cell_shading(cell, bg_hex)
        tcPr = cell._tc.get_or_add_tcPr()
        tcBorders = parse_xml(
            f'<w:tcBorders {nsdecls("w")}>'
            f'<w:bottom w:val="single" w:sz="6" w:space="0" w:color="555555"/>'
            f'</w:tcBorders>'
        )
        tcPr.append(tcBorders)
        for p in cell.paragraphs:
            p.paragraph_format.space_before = Pt(2)
            p.paragraph_format.space_after = Pt(2)
            p.paragraph_format.line_spacing = 1.05
            for r in p.runs:
                r.bold = True
                r.font.name = "Calibri"
                r.font.size = Pt(9.5)
                r.font.color.rgb = RGBColor(30, 41, 59)


def style_table_data_row(row, is_total=False):
    """Format data row: standard padding, prevent split, total row styling."""
    trPr = row._tr.get_or_add_trPr()
    trPr.append(parse_xml(f'<w:cantSplit {nsdecls("w")}/>'))

    for cell in row.cells:
        set_cell_margins(cell, top=80, bottom=80, left=140, right=140)
        if is_total:
            set_cell_shading(cell, "F1F5F9")
            tcPr = cell._tc.get_or_add_tcPr()
            tcBorders = parse_xml(
                f'<w:tcBorders {nsdecls("w")}>'
                f'<w:top w:val="single" w:sz="6" w:space="0" w:color="555555"/>'
                f'<w:bottom w:val="single" w:sz="8" w:space="0" w:color="333333"/>'
                f'</w:tcBorders>'
            )
            tcPr.append(tcBorders)

        for p in cell.paragraphs:
            p.paragraph_format.space_before = Pt(1.5)
            p.paragraph_format.space_after = Pt(1.5)
            p.paragraph_format.line_spacing = 1.05
            for r in p.runs:
                r.font.name = "Calibri"
                r.font.size = Pt(9)
                if is_total:
                    r.bold = True
                    r.font.color.rgb = RGBColor(15, 23, 42)
                else:
                    r.font.color.rgb = RGBColor(51, 65, 85)


def add_table_title_and_notes(doc, table_num, title, note_text=None):
    """Add standardized publication table header and notes."""
    p_num = doc.add_paragraph()
    p_num.paragraph_format.space_before = Pt(12)
    p_num.paragraph_format.space_after = Pt(2)
    r_num = p_num.add_run(f"Table {table_num}")
    r_num.bold = True
    r_num.font.name = "Calibri"
    r_num.font.size = Pt(11)
    r_num.font.color.rgb = RGBColor(15, 23, 42)

    p_title = doc.add_paragraph()
    p_title.paragraph_format.space_before = Pt(0)
    p_title.paragraph_format.space_after = Pt(6)
    r_title = p_title.add_run(title)
    r_title.italic = True
    r_title.font.name = "Calibri"
    r_title.font.size = Pt(10.5)
    r_title.font.color.rgb = RGBColor(30, 41, 59)


def add_table_footer_notes(doc, note_text):
    if note_text:
        p_note = doc.add_paragraph()
        p_note.paragraph_format.space_before = Pt(4)
        p_note.paragraph_format.space_after = Pt(14)
        r_lbl = p_note.add_run("Note. ")
        r_lbl.italic = True
        r_lbl.font.name = "Calibri"
        r_lbl.font.size = Pt(8.5)
        r_lbl.font.color.rgb = RGBColor(71, 85, 105)

        r_body = p_note.add_run(note_text)
        r_body.font.name = "Calibri"
        r_body.font.size = Pt(8.5)
        r_body.font.color.rgb = RGBColor(100, 116, 139)


def export_data_table(headers, rows, aligns, col_widths, doc, table_num, title, note_text=None):
    """Render a table in a docx document."""
    add_table_title_and_notes(doc, table_num, title, note_text)
    table = doc.add_table(rows=len(rows) + 1, cols=len(headers))
    table.alignment = WD_TABLE_ALIGNMENT.CENTER
    apply_apa_table_borders(table)

    # Header
    hdr_cells = table.rows[0].cells
    for i, h in enumerate(headers):
        hdr_cells[i].text = h
        hdr_cells[i].paragraphs[0].alignment = aligns[i]
    style_table_header_row(table.rows[0])

    # Body
    for r_idx, r_data in enumerate(rows):
        is_total = (r_idx == len(rows) - 1) and ("Total" in str(r_data[0]))
        row_cells = table.rows[r_idx + 1].cells
        for c_idx, val in enumerate(r_data):
            row_cells[c_idx].text = str(val)
            row_cells[c_idx].paragraphs[0].alignment = aligns[c_idx]
        style_table_data_row(table.rows[r_idx + 1], is_total=is_total)

    # Column widths
    for row in table.rows:
        for c_idx, w in enumerate(col_widths):
            row.cells[c_idx].width = Inches(w)

    add_table_footer_notes(doc, note_text)
    return table


def write_csv_and_md(filename_stem, headers, rows, title, note_text):
    """Write table data to CSV and markdown formats."""
    csv_path = OUTPUT_DIR / f"{filename_stem}.csv"
    with open(csv_path, "w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow(headers)
        writer.writerows(rows)

    md_path = OUTPUT_DIR / f"{filename_stem}.md"
    with open(md_path, "w", encoding="utf-8") as f:
        f.write(f"### {title}\n\n")
        f.write("| " + " | ".join(headers) + " |\n")
        f.write("| " + " | ".join(["---:" if any(k in h.lower() for k in ["rate", "count", "record", "%", "works", "submission", "incident", "distinct", "share", "average"]) else "---" for h in headers]) + " |\n")
        for r in rows:
            f.write("| " + " | ".join(str(c) for c in r) + " |\n")
        if note_text:
            f.write(f"\n*Note.* {note_text}\n")


# ==============================================================================
# Table 1: Output Type Counts and Duplicate Records in the ERA Dataset
# ==============================================================================
def generate_table1(con):
    q = f"""
    WITH norm AS (
        SELECT research_output_type, trim(lower(title)) as norm_title
        FROM read_parquet('{ERA_PARQUET}')
        WHERE title IS NOT NULL AND trim(title) != ''
    ),
    title_counts AS (
        SELECT research_output_type, norm_title, count(*) as c
        FROM norm
        GROUP BY 1, 2
    ),
    cross_hep AS (
        SELECT research_output_type, count(DISTINCT norm_title) as n_cross_works, count(*) as n_cross_recs
        FROM read_parquet('{DUPLICATES_PARQUET}')
        GROUP BY 1
    )
    SELECT 
        tc.research_output_type,
        sum(tc.c) as total_records,
        count(tc.norm_title) as distinct_titles,
        sum(case when tc.c > 1 then tc.c - 1 else 0 end) as duplicate_records,
        round(100.0 * sum(case when tc.c > 1 then tc.c - 1 else 0 end) / sum(tc.c), 1) as dup_rate,
        COALESCE(ch.n_cross_works, 0) as cross_hep_works,
        COALESCE(ch.n_cross_recs, 0) as cross_hep_submissions
    FROM title_counts tc
    LEFT JOIN cross_hep ch USING (research_output_type)
    GROUP BY tc.research_output_type, ch.n_cross_works, ch.n_cross_recs
    ORDER BY total_records DESC
    """
    data = con.execute(q).fetchall()
    
    headers = [
        "ERA Output Type",
        "Total Records",
        "Distinct Titles",
        "Duplicate Records",
        "Duplicate Rate (%)",
        "Multi-HEP Works",
        "Multi-HEP Submissions",
    ]
    aligns = [
        WD_ALIGN_PARAGRAPH.LEFT,
        WD_ALIGN_PARAGRAPH.RIGHT,
        WD_ALIGN_PARAGRAPH.RIGHT,
        WD_ALIGN_PARAGRAPH.RIGHT,
        WD_ALIGN_PARAGRAPH.RIGHT,
        WD_ALIGN_PARAGRAPH.RIGHT,
        WD_ALIGN_PARAGRAPH.RIGHT,
    ]
    widths = [2.2, 0.9, 0.9, 1.0, 0.9, 0.9, 1.0]

    rows = []
    tot_rec = tot_distinct = tot_dup = tot_cw = tot_cs = 0
    for r in data:
        tot_rec += r[1]
        tot_distinct += r[2]
        tot_dup += r[3]
        tot_cw += r[5]
        tot_cs += r[6]
        rows.append([
            r[0],
            f"{r[1]:,}",
            f"{r[2]:,}",
            f"{r[3]:,}",
            f"{r[4]:.1f}%",
            f"{r[5]:,}",
            f"{r[6]:,}",
        ])
    overall_dup_rate = round(100.0 * tot_dup / tot_rec, 1)
    rows.append([
        "Total",
        f"{tot_rec:,}",
        f"{tot_distinct:,}",
        f"{tot_dup:,}",
        f"{overall_dup_rate:.1f}%",
        f"{tot_cw:,}",
        f"{tot_cs:,}",
    ])

    title = "Record Counts, Distinct Titles, and Duplicate Multi-HEP Submissions Across ERA Output Types"
    notes = (
        "Dataset comprises all ERA research outputs submitted across the 2011–2016 reference-year window (N = 567,647). "
        "Distinct titles are defined by identical lower-cased, whitespace-trimmed title within the same output type. "
        "Multi-HEP Works denote distinct intellectual outputs submitted by two or more Australian universities (group_n_institutions > 1)."
    )
    return headers, rows, aligns, widths, title, notes


# ==============================================================================
# Table 2: Cross-Institution (HEP–HEP) Reporting Disparities within Co-Submitted Works
# ==============================================================================
def generate_table2(con):
    q = f"""
    WITH group_stats AS (
        SELECT 
            research_output_type,
            norm_title,
            count(DISTINCT institution) as n_inst,
            count(*) as n_recs,
            count(DISTINCT CASE WHEN doi IS NOT NULL AND trim(doi) != '' THEN doi END) as n_valid_dois,
            count(CASE WHEN doi IS NULL OR trim(doi) = '' THEN 1 END) as n_missing_dois,
            count(DISTINCT reference_year) as n_distinct_years,
            count(DISTINCT outlet) as n_distinct_outlets,
            count(DISTINCT title) as n_distinct_raw_titles
        FROM read_parquet('{DUPLICATES_PARQUET}')
        GROUP BY 1, 2
    )
    SELECT 
        research_output_type,
        count(*) as works,
        sum(n_recs) as records,
        sum(case when n_distinct_raw_titles > 1 then 1 else 0 end) as title_disp,
        round(100.0 * sum(case when n_distinct_raw_titles > 1 then 1 else 0 end) / count(*), 1) as title_disp_pct,
        sum(case when n_valid_dois >= 1 and n_missing_dois >= 1 then 1 else 0 end) as doi_omission,
        round(100.0 * sum(case when n_valid_dois >= 1 and n_missing_dois >= 1 then 1 else 0 end) / count(*), 1) as doi_omission_pct,
        sum(case when n_valid_dois > 1 then 1 else 0 end) as doi_conflict,
        round(100.0 * sum(case when n_valid_dois > 1 then 1 else 0 end) / count(*), 1) as doi_conflict_pct,
        sum(case when n_distinct_years > 1 then 1 else 0 end) as year_disp,
        round(100.0 * sum(case when n_distinct_years > 1 then 1 else 0 end) / count(*), 1) as year_disp_pct,
        sum(case when n_distinct_outlets > 1 then 1 else 0 end) as outlet_disp,
        round(100.0 * sum(case when n_distinct_outlets > 1 then 1 else 0 end) / count(*), 1) as outlet_disp_pct
    FROM group_stats
    GROUP BY 1
    ORDER BY records DESC
    """
    data = con.execute(q).fetchall()

    headers = [
        "ERA Output Type",
        "Co-Submitted Works",
        "Submissions",
        "Title Disparity",
        "DOI Omission (Code E)",
        "DOI Conflict (Code D)",
        "Year Disparity (Code T)",
        "Outlet Disparity",
    ]
    aligns = [
        WD_ALIGN_PARAGRAPH.LEFT,
        WD_ALIGN_PARAGRAPH.RIGHT,
        WD_ALIGN_PARAGRAPH.RIGHT,
        WD_ALIGN_PARAGRAPH.RIGHT,
        WD_ALIGN_PARAGRAPH.RIGHT,
        WD_ALIGN_PARAGRAPH.RIGHT,
        WD_ALIGN_PARAGRAPH.RIGHT,
        WD_ALIGN_PARAGRAPH.RIGHT,
    ]
    widths = [1.8, 0.8, 0.8, 1.0, 1.1, 1.0, 1.0, 1.0]

    rows = []
    tot_w = tot_r = tot_td = tot_do = tot_dc = tot_yd = tot_od = 0
    for r in data:
        tot_w += r[1]
        tot_r += r[2]
        tot_td += r[3]
        tot_do += r[5]
        tot_dc += r[7]
        tot_yd += r[9]
        tot_od += r[11]
        rows.append([
            r[0],
            f"{r[1]:,}",
            f"{r[2]:,}",
            f"{r[3]:,} ({r[4]:.1f}%)",
            f"{r[5]:,} ({r[6]:.1f}%)",
            f"{r[7]:,} ({r[8]:.1f}%)",
            f"{r[9]:,} ({r[10]:.1f}%)",
            f"{r[11]:,} ({r[12]:.1f}%)",
        ])

    rows.append([
        "Total",
        f"{tot_w:,}",
        f"{tot_r:,}",
        f"{tot_td:,} ({tot_td*100.0/tot_w:.1f}%)",
        f"{tot_do:,} ({tot_do*100.0/tot_w:.1f}%)",
        f"{tot_dc:,} ({tot_dc*100.0/tot_w:.1f}%)",
        f"{tot_yd:,} ({tot_yd*100.0/tot_w:.1f}%)",
        f"{tot_od:,} ({tot_od*100.0/tot_w:.1f}%)",
    ])

    title = "Cross-Institution (HEP–HEP) Reporting Disparities within Multi-Institution Co-Submitted Works"
    notes = (
        "Evaluated across all 72,749 research works independently submitted to ERA by two or more Australian universities (167,564 submissions). "
        "Title Disparity: co-submitting HEPs supplied differing raw title strings (punctuation, spacing, casing, or markup) for the identical output. "
        "DOI Omission (Olensky Code E): at least one institution reported a valid DOI while another co-submitting institution left the DOI blank. "
        "DOI Conflict (Olensky Code D): institutions reported conflicting valid DOIs for the same work. "
        "Year Disparity (Olensky Code T): institutions reported different reference years (e.g., online advance vs print publication year). "
        "Outlet Disparity: differing journal, conference, or publisher name strings across institutions."
    )
    return headers, rows, aligns, widths, title, notes


# ==============================================================================
# Table 3: Olensky (2015) Inaccuracy & Inconsistency Codes in the ERA Dataset
# ==============================================================================
def generate_table3(con):
    q = f"""
    WITH unnested AS (
        SELECT 'title' as field, unnest(title_provenance) as code FROM read_parquet('{PROCESSED_PARQUET}')
        UNION ALL
        SELECT 'doi' as field, unnest(doi_provenance) as code FROM read_parquet('{PROCESSED_PARQUET}')
        UNION ALL
        SELECT 'outlet' as field, unnest(outlet_provenance) as code FROM read_parquet('{PROCESSED_PARQUET}')
        UNION ALL
        SELECT 'publisher' as field, unnest(publisher_provenance) as code FROM read_parquet('{PROCESSED_PARQUET}')
        UNION ALL
        SELECT 'issn' as field, unnest(issn_provenance) as code FROM read_parquet('{PROCESSED_PARQUET}')
        UNION ALL
        SELECT 'isbn' as field, unnest(isbn_provenance) as code FROM read_parquet('{PROCESSED_PARQUET}')
        UNION ALL
        SELECT 'conference_name' as field, unnest(conference_name_provenance) as code FROM read_parquet('{PROCESSED_PARQUET}')
    )
    SELECT code, count(*) as incidents, list(DISTINCT field) as fields
    FROM unnested
    GROUP BY 1
    ORDER BY incidents DESC
    """
    db_codes = {r[0]: (r[1], r[2]) for r in con.execute(q).fetchall()}

    # Olensky taxonomy metadata
    code_definitions = [
        ("K", "Space", "Type 1", "Simple / Added", "Extra, missing, or irregular whitespace runs", "title, outlet, publisher, conference, doi"),
        ("R", "Punctuation", "Type 1", "Simple / Added", "Differing punctuation characters, hyphens, and case variants", "title, publisher, outlet, issn, conference"),
        ("S", "Padded", "Type 1", "Simple / Added", "Extraneous trailing digits or stray characters appended to value", "title, outlet, publisher, conference"),
        ("B", "Spelling error", "Type 2", "Moderate / Spelling", "Single-character insertion/deletion or typo between co-submitting HEPs", "title"),
        ("Q", "Special character", "Type 2", "Moderate / Spelling", "TeX math markup, diacritics, HTML entities (&amp;, &lt;, etc.)", "title, outlet, publisher, conference"),
        ("F", "Cropped", "Type 2", "Moderate / Abbreviated", "Source-system truncation mid-word (ending in trailing ellipsis)", "outlet, publisher"),
        ("T", "Plus/Minus", "Type 2", "Moderate / Other", "Year discrepancy (+/- 1-2 years) across co-reporting institutions", "reference_year"),
        ("E", "Omitted", "Type 3", "Complex / Missing", "Identifier completely missing from one HEP when supplied by another", "doi"),
        ("D", "Completely incorrect", "Type 3", "Complex / Incorrect", "Conflicting, mutually incompatible DOIs reported for same work", "doi"),
    ]

    headers = [
        "Code",
        "Name",
        "IAC Type",
        "Group",
        "Bibliographic Manifestation in ERA",
        "Affected Field(s)",
        "Incidents (N)",
        "Dataset Rate (%)",
    ]
    aligns = [
        WD_ALIGN_PARAGRAPH.CENTER,
        WD_ALIGN_PARAGRAPH.LEFT,
        WD_ALIGN_PARAGRAPH.LEFT,
        WD_ALIGN_PARAGRAPH.LEFT,
        WD_ALIGN_PARAGRAPH.LEFT,
        WD_ALIGN_PARAGRAPH.LEFT,
        WD_ALIGN_PARAGRAPH.RIGHT,
        WD_ALIGN_PARAGRAPH.RIGHT,
    ]
    widths = [0.5, 1.1, 0.9, 1.1, 2.2, 1.2, 0.9, 0.8]

    rows = []
    TOTAL_ERA = 567647
    MULTI_HEP_WORKS = 72749

    for code, name, iac_type, grp, desc, fields_str in code_definitions:
        if code in db_codes:
            incidents = db_codes[code][0]
            pct = incidents * 100.0 / TOTAL_ERA
        elif code == "T":
            incidents = 1814  # from Table 2 group stats
            pct = incidents * 100.0 / MULTI_HEP_WORKS
        elif code == "E":
            incidents = 23301  # from Table 2 group stats
            pct = incidents * 100.0 / MULTI_HEP_WORKS
        elif code == "D":
            incidents = 1411  # from Table 2 group stats
            pct = incidents * 100.0 / MULTI_HEP_WORKS
        else:
            incidents = 0
            pct = 0.0

        pct_str = f"{pct:.2f}%" if pct < 1.0 else f"{pct:.1f}%"
        rows.append([
            code,
            name,
            iac_type,
            grp,
            desc,
            fields_str,
            f"{incidents:,}",
            pct_str,
        ])

    title = "Inventory and Incidence of Olensky (2015) Inaccuracy and Inconsistency Codes (IAC) in ERA"
    notes = (
        "Taxonomy established by Olensky (2015). Type 1: Simple inaccuracies (field contains correct value in full); "
        "Type 2: Moderate inaccuracies (field contains part of correct value); Type 3: Complex inaccuracies (field does not contain correct value). "
        "Codes K, R, S, B, Q, F reflect self-cleaning and cross-HEP reconciliation across all 567,647 records; "
        "Codes T, E, D reflect relational discrepancies across the 72,749 co-submitted multi-HEP works."
    )
    return headers, rows, aligns, widths, title, notes


# ==============================================================================
# Table 4: Multi-Institution Co-Reporting Distribution
# ==============================================================================
def generate_table4(con):
    q = f"""
    WITH distinct_groups AS (
        SELECT DISTINCT norm_title, research_output_type, group_n_institutions, group_n_records
        FROM read_parquet('{DUPLICATES_PARQUET}')
    )
    SELECT 
        group_n_institutions,
        count(*) as n_works,
        sum(group_n_records) as total_submissions
    FROM distinct_groups
    GROUP BY 1
    ORDER BY 1
    """
    raw_data = con.execute(q).fetchall()

    # Aggregate: 2, 3, 4, 5, 6-10, 11+
    buckets = {
        "2": [0, 0],
        "3": [0, 0],
        "4": [0, 0],
        "5": [0, 0],
        "6 to 10": [0, 0],
        "11 or more": [0, 0],
    }

    for n_inst, n_works, n_subs in raw_data:
        if n_inst == 2:
            buckets["2"][0] += n_works
            buckets["2"][1] += n_subs
        elif n_inst == 3:
            buckets["3"][0] += n_works
            buckets["3"][1] += n_subs
        elif n_inst == 4:
            buckets["4"][0] += n_works
            buckets["4"][1] += n_subs
        elif n_inst == 5:
            buckets["5"][0] += n_works
            buckets["5"][1] += n_subs
        elif 6 <= n_inst <= 10:
            buckets["6 to 10"][0] += n_works
            buckets["6 to 10"][1] += n_subs
        else:
            buckets["11 or more"][0] += n_works
            buckets["11 or more"][1] += n_subs

    headers = [
        "Co-Reporting Institutions per Output",
        "Distinct Works (N)",
        "Submissions (N)",
        "Share of Multi-HEP Works (%)",
        "Cumulative Works (%)",
        "Average Submissions / Work",
    ]
    aligns = [
        WD_ALIGN_PARAGRAPH.LEFT,
        WD_ALIGN_PARAGRAPH.RIGHT,
        WD_ALIGN_PARAGRAPH.RIGHT,
        WD_ALIGN_PARAGRAPH.RIGHT,
        WD_ALIGN_PARAGRAPH.RIGHT,
        WD_ALIGN_PARAGRAPH.RIGHT,
    ]
    widths = [2.2, 1.1, 1.1, 1.2, 1.2, 1.2]

    tot_works = sum(v[0] for v in buckets.values())
    tot_subs = sum(v[1] for v in buckets.values())

    rows = []
    cum_works = 0
    for label, (n_w, n_s) in buckets.items():
        cum_works += n_w
        share_w = (n_w * 100.0 / tot_works)
        cum_w_pct = (cum_works * 100.0 / tot_works)
        avg_s = (n_s / n_w) if n_w else 0
        rows.append([
            label,
            f"{n_w:,}",
            f"{n_s:,}",
            f"{share_w:.1f}%",
            f"{cum_w_pct:.1f}%",
            f"{avg_s:.2f}",
        ])

    rows.append([
        "Total",
        f"{tot_works:,}",
        f"{tot_subs:,}",
        "100.0%",
        "100.0%",
        f"{tot_subs / tot_works:.2f}",
    ])

    title = "Distribution of Australian Higher Education Provider (HEP) Co-Submissions per Distinct Research Output"
    notes = (
        "Reflects all distinct intellectual outputs submitted by two or more institutions in the ERA 2011–2016 collection. "
        "Average submissions per work can exceed the number of distinct institutions due to intra-institutional multi-department submissions."
    )
    return headers, rows, aligns, widths, title, notes


def main():
    con = duckdb.connect()
    print("Generating publication tables...")

    table_generators = [
        ("table1_era_output_types_and_duplicates", 1, generate_table1),
        ("table2_hep_disparities_summary", 2, generate_table2),
        ("table3_olensky_iac_classification", 3, generate_table3),
        ("table4_institution_coauthorship_distribution", 4, generate_table4),
    ]

    master_doc = Document()
    # Set default page margins (1 inch)
    for section in master_doc.sections:
        section.top_margin = Inches(1)
        section.bottom_margin = Inches(1)
        section.left_margin = Inches(1)
        section.right_margin = Inches(1)
        section.orientation = docx.enum.section.WD_ORIENT.LANDSCAPE
        section.page_width = Inches(11)
        section.page_height = Inches(8.5)

    for stem, t_num, gen_func in table_generators:
        headers, rows, aligns, widths, title, notes = gen_func(con)

        # 1. Standalone docx
        single_doc = Document()
        for section in single_doc.sections:
            section.top_margin = Inches(1)
            section.bottom_margin = Inches(1)
            section.left_margin = Inches(1)
            section.right_margin = Inches(1)
            if max(widths) * len(widths) > 6.5 or len(headers) > 6:
                section.orientation = docx.enum.section.WD_ORIENT.LANDSCAPE
                section.page_width = Inches(11)
                section.page_height = Inches(8.5)

        export_data_table(headers, rows, aligns, widths, single_doc, t_num, title, notes)
        docx_path = OUTPUT_DIR / f"{stem}.docx"
        single_doc.save(docx_path)
        print(f"Saved: {docx_path}")

        # 2. Add to master docx
        export_data_table(headers, rows, aligns, widths, master_doc, t_num, title, notes)
        master_doc.add_page_break()

        # 3. Write CSV and MD
        write_csv_and_md(stem, headers, rows, title, notes)
        print(f"Saved CSV and MD for Table {t_num}")

    master_docx_path = OUTPUT_DIR / "all_paper_tables.docx"
    master_doc.save(master_docx_path)
    print(f"\nAll tables assembled into master document: {master_docx_path}")


if __name__ == "__main__":
    main()
