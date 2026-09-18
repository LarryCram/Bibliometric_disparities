"""Build analysis/era_institution_ror_lookup.csv - reconciles ERA's short
institution names (e.g. "The Australian National University") against
OpenAlex's ROR-linked institution records, for the indexer-vs-reporter
institution comparison (metric_institution_country.py).

Matches on exact normalized name against OpenAlex's display_name and
display_name_alternatives, restricted to country_code='AU'. The population
of ERA-eligible institutions is small (40) and stable, so this is a
one-time/rarely-rerun script, not a pipeline stage - only re-run it if a
new institution starts reporting to ERA or the OpenAlex institutions
snapshot is refreshed.

Two institutions in the current 40 don't resolve via exact/alternative
name matching and need a manual override (both verified by hand against
OpenAlex's institutions table):
  - "Federation University Australia" -> OpenAlex's "Federation University"
  - "The University of Newcastle" -> OpenAlex's "University of Newcastle Australia"
Any newly-unresolved institution (e.g. if ERA gains a reporting
institution) will show up in the output CSV with match_method=UNRESOLVED
and empty ror_id/oax_institution_display_name - fill it in by hand and add
it to MANUAL_OVERRIDES below.
"""

import re
import sys
from pathlib import Path

import duckdb

sys.path.insert(0, str(Path(__file__).parent.parent))
from config import OPENALEX_INSTITUTIONS

DATA_DIR = Path(__file__).parent.parent / "data"
ERA_PARQUET = DATA_DIR / "era_research_outputs.parquet"
OUTPUT_FILE = Path(__file__).parent / "era_institution_ror_lookup.csv"

OPENALEX_INSTITUTIONS_FILE = OPENALEX_INSTITUTIONS

MANUAL_OVERRIDES = {
    "Federation University Australia": "https://ror.org/05qbzwv83",
    "The University of Newcastle": "https://ror.org/00eae9z71",
}


def norm(s):
    s = s.lower()
    s = re.sub(r"^the\s+", "", s)
    s = re.sub(r"[^a-z0-9 ]", "", s)
    s = re.sub(r"\s+", " ", s).strip()
    return s


def main():
    if not OPENALEX_INSTITUTIONS_FILE.exists():
        print(f"{OPENALEX_INSTITUTIONS_FILE} not found - is the m-drive mounted?")
        return

    con = duckdb.connect()
    era_institutions = [
        row[0] for row in con.execute(f"""
            SELECT DISTINCT institution FROM read_parquet('{ERA_PARQUET}') ORDER BY institution
        """).fetchall()
    ]

    au_rows = con.execute(f"""
        SELECT ror, display_name, display_name_alternatives
        FROM read_parquet('{OPENALEX_INSTITUTIONS_FILE}')
        WHERE country_code = 'AU'
    """).fetchall()

    name_to_row = {}
    ror_to_display_name = {}
    for ror, display_name, alternatives in au_rows:
        ror_to_display_name[ror] = display_name
        name_to_row.setdefault(norm(display_name), (ror, display_name))
        for alt in (alternatives or []):
            name_to_row.setdefault(norm(alt), (ror, display_name))

    rows_out = []
    for inst in era_institutions:
        match = name_to_row.get(norm(inst))
        if match is not None:
            rows_out.append((inst, match[0], match[1], "auto"))
        elif inst in MANUAL_OVERRIDES:
            ror_id = MANUAL_OVERRIDES[inst]
            rows_out.append((inst, ror_id, ror_to_display_name[ror_id], "manual"))
        else:
            rows_out.append((inst, "", "", "UNRESOLVED"))

    with open(OUTPUT_FILE, "w") as f:
        f.write("era_institution_name,ror_id,oax_institution_display_name,match_method\n")
        for era_name, ror_id, oax_name, method in rows_out:
            f.write(f'"{era_name}","{ror_id}","{oax_name}",{method}\n')

    n_auto = sum(1 for r in rows_out if r[3] == "auto")
    n_manual = sum(1 for r in rows_out if r[3] == "manual")
    n_unresolved = sum(1 for r in rows_out if r[3] == "UNRESOLVED")
    print(f"{len(rows_out)} institutions: {n_auto} auto-matched, {n_manual} manual override, "
          f"{n_unresolved} unresolved")
    if n_unresolved:
        print("Unresolved institutions need a manual entry in MANUAL_OVERRIDES - check the "
              "output CSV for which ones.")
    print(f"Written to {OUTPUT_FILE}")


if __name__ == "__main__":
    main()
