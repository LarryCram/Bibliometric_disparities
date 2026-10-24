"""Generate publication-quality figures (300 DPI .png and vector .pdf) for the
bibliometric disparities manuscript based on the ERA dataset, Olensky (2015)
inaccuracy classification, and multi-index synthesis (OpenAlex, Scopus, Trove).

Figures:
  1. figure1_output_types_and_duplicate_gradient: Output type volume and duplicate rate gradient.
  2. figure2_hep_reporting_disparities: Profile of cross-institution (HEP-HEP) reporting discrepancies.
  3. figure3_olensky_iac_taxonomy_profile: Incidence of Olensky inaccuracy codes across Type 1, 2, and 3.
  4. figure4_institution_coauthorship_distribution: Distribution of co-reporting universities per output.
  5. figure5_multi_index_coverage_by_output_type: Coverage across OpenAlex, Scopus, Trove, and Union by output type.
  6. figure6_cross_index_intersections_upset: Disjoint intersection sets and publication type distribution.
  7. figure7_comparative_olensky_profiles: Distribution of Olensky IAC types across OpenAlex, Scopus, and Trove.
"""

from pathlib import Path
import duckdb
import matplotlib.pyplot as plt
import matplotlib.ticker as ticker
import numpy as np

from era.config import DATA_DIR, PROJECT_ROOT

OUTPUT_DIR = PROJECT_ROOT / "report" / "output" / "figures"
OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

CANONICAL_PARQUET = DATA_DIR / "era_research_outputs_canonical.parquet"
DUPLICATES_PARQUET = DATA_DIR / "era_cross_institution_duplicates.parquet"
SYNTHESIS_PARQUET = DATA_DIR / "era_multi_index_synthesis.parquet"
OA_DISPARITIES_PARQUET = DATA_DIR / "era_openalex_disparities.parquet"
SCOPUS_DISPARITIES_PARQUET = DATA_DIR / "era_scopus_disparities.parquet"
TROVE_DISPARITIES_PARQUET = DATA_DIR / "era_trove_disparities.parquet"

# Matplotlib global style setup for academic publication
plt.rcParams["font.family"] = "sans-serif"
plt.rcParams["font.sans-serif"] = ["DejaVu Sans", "Arial", "Helvetica", "Calibri"]
plt.rcParams["axes.edgecolor"] = "#94A3B8"
plt.rcParams["axes.linewidth"] = 0.8
plt.rcParams["grid.color"] = "#E2E8F0"
plt.rcParams["grid.linestyle"] = "--"
plt.rcParams["grid.alpha"] = 0.8
plt.rcParams["xtick.color"] = "#334155"
plt.rcParams["ytick.color"] = "#334155"
plt.rcParams["text.color"] = "#1E293B"


# ==============================================================================
# Figure 1: Output Type Volume & Duplicate Rate Gradient in ERA (Canonical)
# ==============================================================================
def generate_figure1(con):
    q = f"""
    SELECT 
        COALESCE(research_output_type, 'Unknown') as rtype,
        SUM(n_submissions) as total_submissions,
        COUNT(*) as canonical_works,
        SUM(n_submissions - 1) as duplicate_submissions,
        ROUND(SUM(n_submissions - 1) * 100.0 / SUM(n_submissions), 1) as dup_rate
    FROM read_parquet('{CANONICAL_PARQUET}')
    GROUP BY 1
    ORDER BY total_submissions ASC
    """
    rows = con.execute(q).fetchall()

    categories = [r[0] for r in rows]
    total_subs = [r[1] for r in rows]
    dup_rates = [r[4] for r in rows]

    fig, (ax1, ax2) = plt.subplots(1, 2, figsize=(11.5, 5.5), sharey=True, gridspec_kw={"width_ratios": [1.4, 1.0]})
    y_pos = np.arange(len(categories))

    # Panel A: Total Submissions (Logarithmic scale)
    bars1 = ax1.barh(y_pos, total_subs, color="#1E40AF", alpha=0.88, edgecolor="#1E3A8A", height=0.65)
    ax1.set_xscale("log")
    ax1.set_xlim(300, 1_000_000)
    ax1.set_yticks(y_pos)
    ax1.set_yticklabels(categories, fontsize=9.5, fontweight="normal")
    ax1.set_xlabel("Total Submissions (log scale)", fontsize=10, fontweight="bold", labelpad=8)
    ax1.set_title("A. Total ERA Submissions by Output Type", fontsize=11, fontweight="bold", pad=10, loc="left")
    ax1.grid(True, axis="x")
    ax1.spines["top"].set_visible(False)
    ax1.spines["right"].set_visible(False)

    for bar in bars1:
        w = bar.get_width()
        ax1.text(w * 1.15, bar.get_y() + bar.get_height() / 2, f"{int(w):,}",
                 va="center", ha="left", fontsize=8.5, color="#1E293B")

    # Panel B: Duplicate Rate (%)
    bars2 = ax2.barh(y_pos, dup_rates, color="#0D9488", alpha=0.88, edgecolor="#0F766E", height=0.65)
    ax2.set_xlim(0, 26)
    ax2.set_xlabel("Duplication Rate (%)", fontsize=10, fontweight="bold", labelpad=8)
    ax2.set_title("B. Cross-Institutional Duplication Gradient", fontsize=11, fontweight="bold", pad=10, loc="left")
    ax2.grid(True, axis="x")
    ax2.spines["top"].set_visible(False)
    ax2.spines["right"].set_visible(False)

    for bar in bars2:
        w = bar.get_width()
        ax2.text(w + 0.4, bar.get_y() + bar.get_height() / 2, f"{w:.1f}%",
                 va="center", ha="left", fontsize=8.5, color="#0F766E", fontweight="bold")

    plt.tight_layout()
    png_path = OUTPUT_DIR / "figure1_output_types_and_duplicate_gradient.png"
    pdf_path = OUTPUT_DIR / "figure1_output_types_and_duplicate_gradient.pdf"
    plt.savefig(png_path, dpi=300, bbox_inches="tight")
    plt.savefig(pdf_path, format="pdf", bbox_inches="tight")
    plt.close()
    print(f"Saved Figure 1: {png_path} and {pdf_path}")


# ==============================================================================
# Figure 2: HEP-HEP Reporting Disparities Profile
# ==============================================================================
def generate_figure2(con):
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
        round(100.0 * sum(case when n_distinct_raw_titles > 1 then 1 else 0 end) / count(*), 1) as title_disp_pct,
        round(100.0 * sum(case when n_valid_dois >= 1 and n_missing_dois >= 1 then 1 else 0 end) / count(*), 1) as doi_omission_pct,
        round(100.0 * sum(case when n_distinct_outlets > 1 then 1 else 0 end) / count(*), 1) as outlet_disp_pct,
        round(100.0 * sum(case when n_distinct_years > 1 then 1 else 0 end) / count(*), 1) as year_disp_pct,
        round(100.0 * sum(case when n_valid_dois > 1 then 1 else 0 end) / count(*), 1) as doi_conflict_pct
    FROM group_stats
    GROUP BY 1
    ORDER BY works DESC
    """
    rows = con.execute(q).fetchall()

    categories = ["Journal Article\n(N=65,230)", "Conference Pub.\n(N=4,828)", "Book Chapter\n(N=2,341)", "Book\n(N=268)"]
    row_dict = {r[0]: r for r in rows}
    metric_data = {
        "Outlet Disparity": [row_dict["Journal Article"][4], row_dict["Conference Publication"][4], row_dict["Book Chapter"][4], row_dict["Book"][4]],
        "Title Disparity": [row_dict["Journal Article"][2], row_dict["Conference Publication"][2], row_dict["Book Chapter"][2], row_dict["Book"][2]],
        "DOI Omission (Code E)": [row_dict["Journal Article"][3], row_dict["Conference Publication"][3], row_dict["Book Chapter"][3], row_dict["Book"][3]],
        "Year Disparity (Code T)": [row_dict["Journal Article"][5], row_dict["Conference Publication"][5], row_dict["Book Chapter"][5], row_dict["Book"][5]],
    }

    colors = ["#D97706", "#2563EB", "#E11D48", "#059669"]

    fig, ax = plt.subplots(figsize=(10.5, 5.5))
    x = np.arange(len(categories))
    width = 0.18

    for i, ((label, vals), col) in enumerate(zip(metric_data.items(), colors)):
        offset = (i - 1.5) * width
        rects = ax.bar(x + offset, vals, width, label=label, color=col, alpha=0.88, edgecolor=col)
        for rect in rects:
            h = rect.get_height()
            if h > 1.5:
                ax.text(rect.get_x() + rect.get_width() / 2, h + 1.2, f"{h:.1f}%",
                        ha="center", va="bottom", fontsize=8, fontweight="bold", color=col)

    ax.set_ylabel("Incidence in Co-Submitted Works (%)", fontsize=10.5, fontweight="bold", labelpad=8)
    ax.set_title("Cross-Institution (HEP–HEP) Reporting Disparities within Co-Submitted Outputs",
                 fontsize=11.5, fontweight="bold", pad=14, loc="left")
    ax.set_xticks(x)
    ax.set_xticklabels(categories, fontsize=9.5, fontweight="normal")
    ax.set_ylim(0, 108)
    ax.legend(frameon=True, facecolor="#F8FAFC", edgecolor="#CBD5E1", fontsize=9, loc="upper right")
    ax.grid(True, axis="y")
    ax.spines["top"].set_visible(False)
    ax.spines["right"].set_visible(False)

    plt.tight_layout()
    png_path = OUTPUT_DIR / "figure2_hep_reporting_disparities.png"
    pdf_path = OUTPUT_DIR / "figure2_hep_reporting_disparities.pdf"
    plt.savefig(png_path, dpi=300, bbox_inches="tight")
    plt.savefig(pdf_path, format="pdf", bbox_inches="tight")
    plt.close()
    print(f"Saved Figure 2: {png_path} and {pdf_path}")


# ==============================================================================
# Figure 3: Olensky (2015) Inaccuracy Distribution Profile
# ==============================================================================
def generate_figure3(con):
    codes = [
        ("Code K (Space)", "Type 1 (Simple)", 72982, "#1E40AF"),
        ("Code E (DOI Omission)", "Type 3 (Complex)", 23301, "#BE123C"),
        ("Code R (Punctuation/Case)", "Type 1 (Simple)", 19788, "#3B82F6"),
        ("Code T (Year Discrepancy)", "Type 2 (Moderate)", 1814, "#0D9488"),
        ("Code D (Conflicting DOI)", "Type 3 (Complex)", 1411, "#FB7185"),
        ("Code Q (Special/TeX/HTML)", "Type 2 (Moderate)", 1204, "#14B8A6"),
        ("Code B (1-char Indel Typo)", "Type 2 (Moderate)", 958, "#2DD4BF"),
        ("Code S (Padded Digits)", "Type 1 (Simple)", 21, "#93C5FD"),
        ("Code F (Cropped Truncation)", "Type 2 (Moderate)", 16, "#5EEAD4"),
    ]

    labels = [c[0] for c in reversed(codes)]
    counts = [c[2] for c in reversed(codes)]
    colors = [c[3] for c in reversed(codes)]

    fig, ax = plt.subplots(figsize=(10, 5.5))
    y_pos = np.arange(len(labels))

    bars = ax.barh(y_pos, counts, color=colors, alpha=0.9, height=0.62)
    ax.set_xscale("log")
    ax.set_xlim(5, 200_000)
    ax.set_yticks(y_pos)
    ax.set_yticklabels(labels, fontsize=9.5, fontweight="normal")
    ax.set_xlabel("Number of Disparity Incidents (log scale)", fontsize=10.5, fontweight="bold", labelpad=8)
    ax.set_title("Distribution of Olensky (2015) Bibliographic Inaccuracies in the ERA Dataset",
                 fontsize=11.5, fontweight="bold", pad=12, loc="left")
    ax.grid(True, axis="x")
    ax.spines["top"].set_visible(False)
    ax.spines["right"].set_visible(False)

    for bar in bars:
        w = bar.get_width()
        ax.text(w * 1.18, bar.get_y() + bar.get_height() / 2, f"{int(w):,}",
                 va="center", ha="left", fontsize=8.5, fontweight="bold", color="#1E293B")

    from matplotlib.patches import Patch
    legend_elements = [
        Patch(facecolor="#1E40AF", edgecolor="#1E3A8A", label="Type 1: Simple (Value Complete)"),
        Patch(facecolor="#0D9488", edgecolor="#0F766E", label="Type 2: Moderate (Value Partial / Indel)"),
        Patch(facecolor="#BE123C", edgecolor="#9F1239", label="Type 3: Complex (Value Missing / Conflict)"),
    ]
    ax.legend(handles=legend_elements, frameon=True, facecolor="#F8FAFC", edgecolor="#CBD5E1",
              fontsize=9, loc="lower right")

    plt.tight_layout()
    png_path = OUTPUT_DIR / "figure3_olensky_iac_taxonomy_profile.png"
    pdf_path = OUTPUT_DIR / "figure3_olensky_iac_taxonomy_profile.pdf"
    plt.savefig(png_path, dpi=300, bbox_inches="tight")
    plt.savefig(pdf_path, format="pdf", bbox_inches="tight")
    plt.close()
    print(f"Saved Figure 3: {png_path} and {pdf_path}")


# ==============================================================================
# Figure 4: Distribution of Co-Reporting HEPs per Output
# ==============================================================================
def generate_figure4(con):
    categories = ["2 HEPs", "3 HEPs", "4 HEPs", "5 HEPs", "6–10 HEPs", "11+ HEPs"]
    works = [64132, 14588, 3445, 1041, 660, 24]
    tot_works = sum(works)
    shares = [w * 100.0 / tot_works for w in works]
    cumulative = np.cumsum(shares)

    fig, ax1 = plt.subplots(figsize=(9.5, 5.0))
    x = np.arange(len(categories))
    bars = ax1.bar(x, works, color="#3B82F6", alpha=0.88, edgecolor="#1D4ED8", width=0.55, label="Distinct Works (N)")
    ax1.set_ylabel("Distinct Co-Submitted Works (N)", fontsize=10.5, fontweight="bold", color="#1E40AF", labelpad=8)
    ax1.set_xticks(x)
    ax1.set_xticklabels(categories, fontsize=9.5, fontweight="normal")
    ax1.set_ylim(0, 72000)
    ax1.grid(True, axis="y")
    ax1.spines["top"].set_visible(False)

    for bar, pct in zip(bars, shares):
        h = bar.get_height()
        ax1.text(bar.get_x() + bar.get_width() / 2, h + 1000, f"{h:,}\n({pct:.1f}%)",
                 ha="center", va="bottom", fontsize=8.5, fontweight="bold", color="#1E3A8A")

    ax2 = ax1.twinx()
    line = ax2.plot(x, cumulative, color="#DC2626", marker="o", linewidth=2.2, markersize=6,
                    label="Cumulative Share (%)")
    ax2.set_ylabel("Cumulative Share of Multi-HEP Works (%)", fontsize=10.5, fontweight="bold", color="#DC2626", labelpad=8)
    ax2.set_ylim(50, 105)
    ax2.spines["top"].set_visible(False)
    ax2.yaxis.set_major_formatter(ticker.PercentFormatter())

    for i, cum in enumerate(cumulative):
        ax2.annotate(f"{cum:.1f}%", (x[i], cum), textcoords="offset points", xytext=(0, 7),
                     ha="center", fontsize=8.5, fontweight="bold", color="#B91C1C")

    ax1.set_title("Distribution of Australian Higher Education Provider (HEP) Co-Submissions",
                  fontsize=11.5, fontweight="bold", pad=14, loc="left")

    plt.tight_layout()
    png_path = OUTPUT_DIR / "figure4_institution_coauthorship_distribution.png"
    pdf_path = OUTPUT_DIR / "figure4_institution_coauthorship_distribution.pdf"
    plt.savefig(png_path, dpi=300, bbox_inches="tight")
    plt.savefig(pdf_path, format="pdf", bbox_inches="tight")
    plt.close()
    print(f"Saved Figure 4: {png_path} and {pdf_path}")


# ==============================================================================
# Figure 5: Multi-Index Coverage by Output Type (Grouped Bar Chart)
# ==============================================================================
def generate_figure5(con):
    q = f"""
    SELECT 
        COALESCE(research_output_type, 'Unknown') as rtype,
        COUNT(*) as total_canonical,
        ROUND(SUM(in_openalex::INT) * 100.0 / COUNT(*), 1) as pct_oa,
        ROUND(SUM(in_scopus::INT) * 100.0 / COUNT(*), 1) as pct_sc,
        ROUND(SUM(in_trove::INT) * 100.0 / COUNT(*), 1) as pct_tr,
        ROUND(SUM(in_any_index::INT) * 100.0 / COUNT(*), 1) as pct_un
    FROM read_parquet('{SYNTHESIS_PARQUET}')
    GROUP BY 1
    ORDER BY total_canonical DESC
    """
    rows = con.execute(q).fetchall()

    # Filter out categories with tiny sample (<500 works) for chart readability
    filtered = [r for r in rows if r[1] >= 500]
    labels = [f"{r[0]}\n(N={r[1]:,})" for r in filtered]
    pct_oa = [r[2] for r in filtered]
    pct_sc = [r[3] for r in filtered]
    pct_tr = [r[4] for r in filtered]
    pct_un = [r[5] for r in filtered]

    fig, ax = plt.subplots(figsize=(12, 6.0))
    x = np.arange(len(labels))
    width = 0.20

    rects1 = ax.bar(x - 1.5 * width, pct_oa, width, label="OpenAlex (Oct26)", color="#2563EB", alpha=0.9)
    rects2 = ax.bar(x - 0.5 * width, pct_sc, width, label="Scopus (post-harvest)", color="#D97706", alpha=0.9)
    rects3 = ax.bar(x + 0.5 * width, pct_tr, width, label="Trove (National Library)", color="#059669", alpha=0.9)
    rects4 = ax.bar(x + 1.5 * width, pct_un, width, label="Multi-Index Union (Any)", color="#7C3AED", alpha=0.9)

    ax.set_ylabel("Coverage Rate (% of Canonical Works)", fontsize=11, fontweight="bold", labelpad=8)
    ax.set_title("Multi-Index Bibliometric Coverage by Research Output Type in Australia",
                 fontsize=12, fontweight="bold", pad=14, loc="left")
    ax.set_xticks(x)
    ax.set_xticklabels(labels, fontsize=9.5, fontweight="normal")
    ax.set_ylim(0, 112)
    ax.legend(frameon=True, facecolor="#F8FAFC", edgecolor="#CBD5E1", fontsize=9.5, loc="upper right")
    ax.grid(True, axis="y")
    ax.spines["top"].set_visible(False)
    ax.spines["right"].set_visible(False)

    # Annotate Union values
    for rect in rects4:
        h = rect.get_height()
        ax.text(rect.get_x() + rect.get_width() / 2, h + 1.5, f"{h:.1f}%",
                ha="center", va="bottom", fontsize=8, fontweight="bold", color="#7C3AED")

    plt.tight_layout()
    png_path = OUTPUT_DIR / "figure5_multi_index_coverage_by_output_type.png"
    pdf_path = OUTPUT_DIR / "figure5_multi_index_coverage_by_output_type.pdf"
    plt.savefig(png_path, dpi=300, bbox_inches="tight")
    plt.savefig(pdf_path, format="pdf", bbox_inches="tight")
    plt.close()
    print(f"Saved Figure 5: {png_path} and {pdf_path}")


# ==============================================================================
# Figure 6: Cross-Index UpSet / Overlap Intersections Breakdown
# ==============================================================================
def generate_figure6(con):
    q = f"""
    SELECT 
        coverage_category,
        COUNT(*) as total_works,
        SUM((research_output_type = 'Journal Article')::INT) as journals,
        SUM((research_output_type = 'Conference Publication')::INT) as conferences,
        SUM((research_output_type IN ('Book', 'Book Chapter'))::INT) as books,
        SUM((research_output_type NOT IN ('Journal Article', 'Conference Publication', 'Book', 'Book Chapter'))::INT) as ntro
    FROM read_parquet('{SYNTHESIS_PARQUET}')
    GROUP BY 1
    ORDER BY total_works ASC
    """
    rows = con.execute(q).fetchall()

    categories = [r[0] for r in rows]
    journals = [r[2] for r in rows]
    conferences = [r[3] for r in rows]
    books = [r[4] for r in rows]
    ntro = [r[5] for r in rows]
    totals = [r[1] for r in rows]

    fig, ax = plt.subplots(figsize=(11.5, 6.0))
    y_pos = np.arange(len(categories))

    p1 = ax.barh(y_pos, journals, color="#2563EB", alpha=0.88, height=0.6, label="Journal Articles")
    p2 = ax.barh(y_pos, conferences, left=journals, color="#0EA5E9", alpha=0.88, height=0.6, label="Conference Pubs")
    left_b = np.array(journals) + np.array(conferences)
    p3 = ax.barh(y_pos, books, left=left_b, color="#D97706", alpha=0.88, height=0.6, label="Books & Book Chapters")
    left_n = left_b + np.array(books)
    p4 = ax.barh(y_pos, ntro, left=left_n, color="#10B981", alpha=0.88, height=0.6, label="Non-Traditional (NTRO)")

    ax.set_xscale("log")
    ax.set_xlim(10, 800_000)
    ax.set_yticks(y_pos)
    ax.set_yticklabels(categories, fontsize=10, fontweight="normal")
    ax.set_xlabel("Number of Distinct Canonical Works (log scale)", fontsize=10.5, fontweight="bold", labelpad=8)
    ax.set_title("Cross-Index Intersection Sets and Composition Across Output Types",
                 fontsize=12, fontweight="bold", pad=14, loc="left")
    ax.grid(True, axis="x")
    ax.spines["top"].set_visible(False)
    ax.spines["right"].set_visible(False)
    ax.legend(frameon=True, facecolor="#F8FAFC", edgecolor="#CBD5E1", fontsize=9.5, loc="lower right")

    for i, tot in enumerate(totals):
        pct = tot * 100.0 / 455741
        ax.text(tot * 1.15, y_pos[i], f"{tot:,} ({pct:.1f}%)",
                va="center", ha="left", fontsize=8.5, fontweight="bold", color="#1E293B")

    plt.tight_layout()
    png_path = OUTPUT_DIR / "figure6_cross_index_intersections_upset.png"
    pdf_path = OUTPUT_DIR / "figure6_cross_index_intersections_upset.pdf"
    plt.savefig(png_path, dpi=300, bbox_inches="tight")
    plt.savefig(pdf_path, format="pdf", bbox_inches="tight")
    plt.close()
    print(f"Saved Figure 6: {png_path} and {pdf_path}")


# ==============================================================================
# Figure 7: Comparative External Olensky Inaccuracy Profiles
# ==============================================================================
def generate_figure7(con):
    sources = ["OpenAlex\n(N=536,707)", "Scopus\n(N=342,478)", "Trove\n(N=36,614)"]
    type1_pcts = [91.66, 94.84, 79.61]  # Surface / Punctuation / Case / Exact
    type2_pcts = [7.90, 4.94, 16.49]   # Textual / Typo / Indel / Cropped
    type3_pcts = [0.44, 0.22, 3.89]    # Severe / Discordant

    fig, ax = plt.subplots(figsize=(9.5, 4.8))
    y_pos = np.arange(len(sources))

    b1 = ax.barh(y_pos, type1_pcts, color="#2563EB", alpha=0.9, height=0.55,
                 label="Type 1: Surface Formatting (Exact, Punctuation, Case, TeX/HTML)")
    b2 = ax.barh(y_pos, type2_pcts, left=type1_pcts, color="#D97706", alpha=0.9, height=0.55,
                 label="Type 2: Textual Variation (Typo, Spelling Indel, Cropped Subtitle)")
    left3 = np.array(type1_pcts) + np.array(type2_pcts)
    b3 = ax.barh(y_pos, type3_pcts, left=left3, color="#E11D48", alpha=0.9, height=0.55,
                 label="Type 3: Severe Inaccuracy (Discordant Title, Omission)")

    ax.set_xlim(0, 102)
    ax.set_yticks(y_pos)
    ax.set_yticklabels(sources, fontsize=10, fontweight="bold")
    ax.set_xlabel("Share of Discrepancies (%)", fontsize=10.5, fontweight="bold", labelpad=8)
    ax.set_title("Comparative Olensky (2015) Inaccuracy Distribution Across External Indices",
                 fontsize=11.5, fontweight="bold", pad=14, loc="left")
    ax.grid(True, axis="x")
    ax.spines["top"].set_visible(False)
    ax.spines["right"].set_visible(False)
    ax.legend(frameon=True, facecolor="#F8FAFC", edgecolor="#CBD5E1", fontsize=9, loc="lower center",
              bbox_to_anchor=(0.5, -0.28), ncol=1)

    for i in range(len(sources)):
        # Annotate Type 1
        ax.text(type1_pcts[i] / 2, y_pos[i], f"{type1_pcts[i]:.1f}%",
                va="center", ha="center", fontsize=9, fontweight="bold", color="#FFFFFF")
        # Annotate Type 2
        ax.text(type1_pcts[i] + type2_pcts[i] / 2, y_pos[i], f"{type2_pcts[i]:.1f}%",
                va="center", ha="center", fontsize=8.5, fontweight="bold", color="#FFFFFF")
        # Annotate Type 3
        if type3_pcts[i] > 1.0:
            ax.text(left3[i] + type3_pcts[i] / 2, y_pos[i], f"{type3_pcts[i]:.1f}%",
                    va="center", ha="center", fontsize=8, fontweight="bold", color="#FFFFFF")

    plt.tight_layout()
    png_path = OUTPUT_DIR / "figure7_comparative_olensky_profiles.png"
    pdf_path = OUTPUT_DIR / "figure7_comparative_olensky_profiles.pdf"
    plt.savefig(png_path, dpi=300, bbox_inches="tight")
    plt.savefig(pdf_path, format="pdf", bbox_inches="tight")
    plt.close()
    print(f"Saved Figure 7: {png_path} and {pdf_path}")


def main():
    con = duckdb.connect()
    print("Generating complete publication figures suite (Stage 4)...")
    generate_figure1(con)
    generate_figure2(con)
    generate_figure3(con)
    generate_figure4(con)
    generate_figure5(con)
    generate_figure6(con)
    generate_figure7(con)
    print("\nAll 7 publication figures generated successfully in report/output/figures/!")


if __name__ == "__main__":
    main()
