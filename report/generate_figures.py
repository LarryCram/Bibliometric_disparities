"""Generate publication-quality figures (300 DPI .png and vector .pdf) for the
bibliometric disparities manuscript based on the ERA dataset and Olensky (2015)
inaccuracy classification.

Figures:
  1. figure1_output_types_and_duplicate_gradient: Output type volume and duplicate rate gradient.
  2. figure2_hep_reporting_disparities: Profile of cross-institution (HEP-HEP) reporting discrepancies.
  3. figure3_olensky_iac_taxonomy_profile: Incidence of Olensky inaccuracy codes across Type 1, 2, and 3.
  4. figure4_institution_coauthorship_distribution: Distribution of co-reporting universities per output.
"""

from pathlib import Path
import duckdb
import matplotlib.pyplot as plt
import matplotlib.ticker as ticker
import numpy as np

from era.config import DATA_DIR, PROJECT_ROOT

OUTPUT_DIR = PROJECT_ROOT / "report" / "output" / "figures"
OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

ERA_PARQUET = DATA_DIR / "era_research_outputs.parquet"
DUPLICATES_PARQUET = DATA_DIR / "era_cross_institution_duplicates.parquet"
PROCESSED_PARQUET = DATA_DIR / "era_research_outputs_processed.parquet"

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
# Figure 1: Output Type Volume & Duplicate Rate Gradient in ERA
# ==============================================================================
def generate_figure1(con):
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
        sum(case when tc.c > 1 then tc.c - 1 else 0 end) as duplicate_records,
        round(100.0 * sum(case when tc.c > 1 then tc.c - 1 else 0 end) / sum(tc.c), 1) as dup_rate
    FROM title_counts tc
    LEFT JOIN cross_hep ch USING (research_output_type)
    GROUP BY tc.research_output_type
    ORDER BY total_records ASC
    """
    rows = con.execute(q).fetchall()

    categories = [r[0] for r in rows]
    total_recs = [r[1] for r in rows]
    dup_rates = [r[3] for r in rows]

    fig, (ax1, ax2) = plt.subplots(1, 2, figsize=(11.5, 5.5), sharey=True, gridspec_kw={"width_ratios": [1.4, 1.0]})

    y_pos = np.arange(len(categories))

    # Panel A: Total Submissions (Logarithmic scale)
    bars1 = ax1.barh(y_pos, total_recs, color="#1E40AF", alpha=0.88, edgecolor="#1E3A8A", height=0.65)
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
    ax2.set_xlim(0, 24)
    ax2.set_xlabel("Duplicate Rate (%)", fontsize=10, fontweight="bold", labelpad=8)
    ax2.set_title("B. Cross-Institutional Duplication Rate", fontsize=11, fontweight="bold", pad=10, loc="left")
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

    # Major categories with multi-HEP records
    categories = ["Journal Article\n(N=65,230)", "Conference Pub.\n(N=4,828)", "Book Chapter\n(N=2,341)", "Book\n(N=268)"]
    row_dict = {r[0]: r for r in rows}
    # Metrics to display
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
    # Data compiled from Table 3
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
    types = [c[1] for c in reversed(codes)]
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

    # Custom legend for Olensky Types
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
    # From Table 4
    categories = ["2 HEPs", "3 HEPs", "4 HEPs", "5 HEPs", "6–10 HEPs", "11+ HEPs"]
    works = [57082, 11703, 2661, 812, 480, 11]
    shares = [78.5, 16.1, 3.7, 1.1, 0.7, 0.02]
    cumulative = [78.5, 94.6, 98.2, 99.3, 100.0, 100.0]

    fig, ax1 = plt.subplots(figsize=(9.5, 5.0))

    x = np.arange(len(categories))
    bars = ax1.bar(x, works, color="#3B82F6", alpha=0.88, edgecolor="#1D4ED8", width=0.55, label="Distinct Works (N)")
    ax1.set_ylabel("Distinct Co-Submitted Works (N)", fontsize=10.5, fontweight="bold", color="#1E40AF", labelpad=8)
    ax1.set_xticks(x)
    ax1.set_xticklabels(categories, fontsize=9.5, fontweight="normal")
    ax1.set_ylim(0, 65000)
    ax1.grid(True, axis="y")
    ax1.spines["top"].set_visible(False)

    for bar, pct in zip(bars, shares):
        h = bar.get_height()
        ax1.text(bar.get_x() + bar.get_width() / 2, h + 1000, f"{h:,}\n({pct:.1f}%)",
                 ha="center", va="bottom", fontsize=8.5, fontweight="bold", color="#1E3A8A")

    # Secondary axis: Cumulative % line
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


def main():
    con = duckdb.connect()
    print("Generating publication figures...")
    generate_figure1(con)
    generate_figure2(con)
    generate_figure3(con)
    generate_figure4(con)
    print("\nAll publication figures generated successfully in report/output/figures/!")


if __name__ == "__main__":
    main()
