"""Identify ERA records that describe the same research output reported by
more than one institution - candidate cases for comparing how differently
indexers (OpenAlex, WOS, Scopus, Trove) describe a single underlying work,
since ERA itself gives no author field to cross-check directly.

Duplicate detection: identical normalised title (lower-cased, whitespace-
trimmed) within the same research_output_type - the same method already
used in docs/era_quality_review.md's duplicate-rate table.

First confirmed whether the same institution ever reports the identical
title twice: it does, 137 (research_output_type, normalised title) groups.
No row filtering is applied for either the same-institution or the
cross-institution case - every matching group is kept, with
title_word_count/group_n_institutions/group_n_records included as plain
diagnostic columns so any thresholding is a separate, explicit step
downstream rather than done here.

Output: data/era_cross_institution_duplicates.parquet - every ERA record
belonging to a group of 2+ records sharing a (research_output_type,
normalised title) with more than one distinct reporting institution.
"""

from pathlib import Path

import duckdb

DATA_DIR = Path(__file__).parent.parent / "data"
ERA_PARQUET = DATA_DIR / "era_research_outputs.parquet"
OUTPUT_FILE = DATA_DIR / "era_cross_institution_duplicates.parquet"


def check_same_institution_duplicates(con):
    rows = con.execute(f"""
        SELECT research_output_type, trim(lower(title)) AS norm_title, institution, count(*) c
        FROM read_parquet('{ERA_PARQUET}')
        WHERE title IS NOT NULL AND trim(title) != ''
        GROUP BY 1, 2, 3
        HAVING count(*) > 1
        ORDER BY c DESC
    """).fetchall()
    print(f"Same-institution duplicate (type, title) groups: {len(rows)}")
    if rows:
        print("  Top groups by count:")
        for r in rows[:5]:
            print(f"    {r}")
    return rows


def build_cross_institution_duplicates(con):
    con.execute(f"""
        COPY (
            WITH normalised AS (
                SELECT *, trim(lower(title)) AS norm_title
                FROM read_parquet('{ERA_PARQUET}')
                WHERE title IS NOT NULL AND trim(title) != ''
            ),
            groups AS (
                SELECT research_output_type, norm_title,
                       count(DISTINCT institution) AS group_n_institutions,
                       count(*) AS group_n_records
                FROM normalised
                GROUP BY 1, 2
                HAVING count(DISTINCT institution) > 1
            )
            SELECT n.* EXCLUDE (norm_title),
                   n.norm_title,
                   length(string_split(n.norm_title, ' ')) AS title_word_count,
                   g.group_n_institutions,
                   g.group_n_records
            FROM normalised n
            JOIN groups g USING (research_output_type, norm_title)
            ORDER BY g.group_n_institutions DESC, n.norm_title
        ) TO '{OUTPUT_FILE}' (FORMAT PARQUET, COMPRESSION SNAPPY)
    """)


def main():
    con = duckdb.connect()

    check_same_institution_duplicates(con)
    print()

    build_cross_institution_duplicates(con)
    n_records, n_groups = con.execute(f"""
        SELECT count(*), count(DISTINCT (research_output_type, norm_title))
        FROM read_parquet('{OUTPUT_FILE}')
    """).fetchone()
    print(f"Cross-institution duplicate groups: {n_groups}")
    print(f"ERA records belonging to those groups: {n_records}")

    by_type = con.execute(f"""
        SELECT research_output_type, count(DISTINCT (research_output_type, norm_title)) AS n_groups,
               count(*) AS n_records
        FROM read_parquet('{OUTPUT_FILE}')
        GROUP BY 1 ORDER BY n_records DESC
    """).fetchall()
    print("\nBy output type:")
    for t, n_g, n_r in by_type:
        print(f"  {t:<35} {n_g:>6} groups, {n_r:>7} records")

    print(f"\nWritten to {OUTPUT_FILE}")


if __name__ == "__main__":
    main()
