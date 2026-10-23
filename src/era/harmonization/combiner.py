"""Stage-C Combiner: Reconciles and consolidates multi-HEP ERA submissions
into canonical ERAResearchRecord outputs.

Methodology:
  1. Duplicate Clustering (Connected Components):
     - Join Key 1: Valid wellformed DOI (doi_wellformed == True).
     - Join Key 2: Valid wellformed Identifier (identifier_wellformed == True).
     - Join Key 3: (research_output_type, norm_title) for distinct titles (word_count >= 2)
       where DOIs do not conflict (safeguarding against generic titles like 'conclusions'
       or 'introduction' across unrelated journals/books).

  2. Field Reconciliation & Olensky Provenance Tracking:
     - Title: consensus mode title chosen; cross-HEP title differences classified
       with Olensky codes (K, R, A, B, etc.) and recorded in title_provenance.
     - DOI: consensus DOI chosen; DOI omissions by co-submitting HEPs tagged with
       Olensky code 'E'; conflicting DOIs tagged with Olensky code 'D'.
     - Year: consensus reference_year chosen; cross-HEP year discrepancies tagged
       with Olensky code 'T'.
     - Outlet/Publisher/etc.: consensus values selected, preserving cleaned strings.
     - Co-submitting institutions tracked in `institutions: list[str]`.
     - Raw submission IDs tracked in `source_ids: list[int]`.

Every raw submission row maps to exactly one canonical record. Nothing is silently dropped.
"""

from collections import Counter
from dataclasses import dataclass, field
import html
from pathlib import Path
import re
from typing import Any

import duckdb
import pyarrow as pa
import pyarrow.parquet as pq

from era.config import DATA_DIR
from era.core.olensky import single_char_indel_code

_TRUNCATION_RE = re.compile(r"\s*\.\.+$")
_PUNCT_RE = re.compile(r"[^a-zA-Z0-9\s]")
_SPACE_RE = re.compile(r"\s+")


class DisjointSetUnion:
    """Disjoint Set Union (Union-Find) with path compression and union by rank."""

    def __init__(self, n: int):
        self.parent = list(range(n))
        self.rank = [0] * n

    def find(self, i: int) -> int:
        path = []
        while self.parent[i] != i:
            path.append(i)
            i = self.parent[i]
        for node in path:
            self.parent[node] = i
        return i

    def union(self, i: int, j: int) -> bool:
        root_i = self.find(i)
        root_j = self.find(j)
        if root_i == root_j:
            return False
        if self.rank[root_i] < self.rank[root_j]:
            self.parent[root_i] = root_j
        elif self.rank[root_i] > self.rank[root_j]:
            self.parent[root_j] = root_i
        else:
            self.parent[root_j] = root_i
            self.rank[root_i] += 1
        return True


class StageCCombiner:
    """Reconciles multi-HEP submissions into canonical research records."""

    @staticmethod
    def cluster_records(records: list[dict[str, Any]]) -> list[list[int]]:
        """Cluster records using DOI, Identifier, and safe Normalized Title matching.
        
        Returns a list of clusters, where each cluster is a list of record indices.
        """
        n = len(records)
        dsu = DisjointSetUnion(n)

        # 1. DOI clustering: group by valid wellformed DOI
        doi_map: dict[str, list[int]] = {}
        for i, r in enumerate(records):
            doi = r.get("doi")
            if r.get("doi_wellformed") and doi:
                d = str(doi).strip().lower()
                if d:
                    doi_map.setdefault(d, []).append(i)

        for idxs in doi_map.values():
            for j in range(1, len(idxs)):
                dsu.union(idxs[0], idxs[j])

        # 2. Identifier clustering: group by valid wellformed Identifier
        id_map: dict[str, list[int]] = {}
        for i, r in enumerate(records):
            ident = r.get("identifier")
            if r.get("identifier_wellformed") and ident:
                i_str = str(ident).strip().lower()
                if i_str:
                    id_map.setdefault(i_str, []).append(i)

        for idxs in id_map.values():
            for j in range(1, len(idxs)):
                dsu.union(idxs[0], idxs[j])

        # 3. Normalized Title clustering:
        # Group by (research_output_type, norm_title).
        # Only merge if titles have word_count >= 2 and DOIs do NOT conflict.
        title_map: dict[tuple[str, str], list[int]] = {}
        for i, r in enumerate(records):
            title = r.get("title")
            out_type = r.get("research_output_type") or ""
            if title:
                norm_title = str(title).strip().lower()
                words = norm_title.split()
                if len(words) >= 2:
                    title_map.setdefault((out_type, norm_title), []).append(i)

        for (out_type, norm_title), idxs in title_map.items():
            if len(idxs) > 1:
                # Check for DOI conflicts among records with valid DOIs
                valid_dois = {
                    str(records[idx]["doi"]).strip().lower()
                    for idx in idxs
                    if records[idx].get("doi_wellformed") and records[idx].get("doi")
                }
                # If 0 or 1 distinct valid DOI, it is safe to merge (no conflicting DOIs)
                if len(valid_dois) <= 1:
                    for j in range(1, len(idxs)):
                        dsu.union(idxs[0], idxs[j])

        # Collect clusters by root
        clusters_by_root: dict[int, list[int]] = {}
        for i in range(n):
            root = dsu.find(i)
            clusters_by_root.setdefault(root, []).append(i)

        return list(clusters_by_root.values())

    @classmethod
    def reconcile_cluster(cls, cluster_records: list[dict[str, Any]]) -> dict[str, Any]:
        """Reconcile a single cluster of 1+ submission records into one canonical record."""
        # 1. Submission metadata
        source_ids = [r["id"] for r in cluster_records]
        institutions = sorted(list({r["institution"] for r in cluster_records if r.get("institution")}))
        n_submissions = len(cluster_records)
        is_multi_hep = n_submissions > 1

        # Representative record: deterministic anchor (smallest submission id)
        cluster_sorted = sorted(cluster_records, key=lambda r: r["id"])
        anchor = cluster_sorted[0]
        canonical_id = anchor["id"]
        research_output_type = anchor.get("research_output_type")

        # 2. Reconcile Title
        titles = [r["title"] for r in cluster_records if r.get("title")]
        title_prov: set[str] = set()
        for r in cluster_records:
            for c in (r.get("title_provenance") or []):
                title_prov.add(c)

        if titles:
            # Mode title (most frequently reported string among HEPs; tie-break by longest)
            title_counts = Counter(titles)
            max_freq = max(title_counts.values())
            candidates = [t for t, freq in title_counts.items() if freq == max_freq]
            canonical_title = max(candidates, key=len)

            # Classify cross-HEP title disparities against the canonical title
            distinct_titles = set(titles)
            if len(distinct_titles) > 1:
                for alt_title in distinct_titles:
                    if alt_title != canonical_title:
                        # Check single char indel first
                        indel_code = single_char_indel_code(alt_title, canonical_title)
                        if indel_code:
                            title_prov.add(indel_code)
                        else:
                            u_alt = html.unescape(alt_title)
                            u_can = html.unescape(canonical_title)
                            c_alt = _SPACE_RE.sub(" ", _PUNCT_RE.sub("", u_alt)).strip().lower()
                            c_can = _SPACE_RE.sub(" ", _PUNCT_RE.sub("", u_can)).strip().lower()
                            if c_alt == c_can:
                                title_prov.add("R")
                            else:
                                title_prov.add("A")
        else:
            canonical_title = None

        # Structural flags for title
        title_wellformed = all(r.get("title_wellformed", True) for r in cluster_records)
        title_has_html = any(r.get("title_has_html", False) for r in cluster_records)
        title_has_tex = any(r.get("title_has_tex", False) for r in cluster_records)
        title_has_non_latin = any(r.get("title_has_non_latin", False) for r in cluster_records)
        title_has_mojibake = any(r.get("title_has_mojibake", False) for r in cluster_records)
        title_has_daterange = any(r.get("title_has_daterange", False) for r in cluster_records)
        title_daterange_status = anchor.get("title_daterange_status")
        title_has_en_us = any(r.get("title_has_en_us", False) for r in cluster_records)
        title_has_en_gb = any(r.get("title_has_en_gb", False) for r in cluster_records)
        title_has_en_au = any(r.get("title_has_en_au", False) for r in cluster_records)

        # 3. Reconcile DOI
        dois_valid = [r["doi"] for r in cluster_records if r.get("doi_wellformed") and r.get("doi")]
        doi_prov: set[str] = set()
        for r in cluster_records:
            for c in (r.get("doi_provenance") or []):
                doi_prov.add(c)

        if dois_valid:
            # Mode DOI
            doi_counts = Counter(dois_valid)
            canonical_doi = doi_counts.most_common(1)[0][0]
            doi_wellformed = True

            # Disparity checking: Omission (Code E) and Conflict (Code D)
            has_missing = any(not r.get("doi") for r in cluster_records)
            if has_missing and is_multi_hep:
                doi_prov.add("E")  # Olensky Code E: Omitted DOI by at least one co-submitting HEP

            distinct_dois = set(dois_valid)
            if len(distinct_dois) > 1:
                doi_prov.add("D")  # Olensky Code D: Conflicting DOIs reported
        else:
            # Check if any raw DOI was present (even if not wellformed)
            raw_dois = [r["doi"] for r in cluster_records if r.get("doi")]
            if raw_dois:
                canonical_doi = Counter(raw_dois).most_common(1)[0][0]
                doi_wellformed = False
            else:
                canonical_doi = None
                doi_wellformed = None

        # 4. Reconcile Reference Year
        years = [r["reference_year"] for r in cluster_records if r.get("reference_year") is not None]
        year_prov: set[str] = set()
        if years:
            year_counts = Counter(years)
            canonical_year = year_counts.most_common(1)[0][0]
            if len(set(years)) > 1:
                year_prov.add("T")  # Olensky Code T: Year discrepancy (e.g. advance vs print)
        else:
            canonical_year = None

        # 5. Reconcile Generic Fields
        generic_cols = {}
        for f in [
            "outlet", "publisher", "place_of_publication", "category_type",
            "conference_name", "era_conference_id", "era_journal_id", "identifier",
            "isbn", "issn", "issue", "journal_issue", "journal_title", "journal_volume",
            "media", "notes", "outlet_editor", "outlet_isbn", "outlet_issue",
            "outlet_venue", "outlet_volume", "standard_number", "volume",
        ]:
            vals = [r[f] for r in cluster_records if r.get(f)]
            prov: set[str] = set()
            for r in cluster_records:
                for c in (r.get(f"{f}_provenance") or []):
                    prov.add(c)

            if vals:
                # Mode value; tie-break by longest
                val_counts = Counter(vals)
                max_cnt = max(val_counts.values())
                cand = [v for v, c in val_counts.items() if c == max_cnt]
                canon_val = max(cand, key=len)
                # If outlet or publisher differed across submissions, note F or R if appropriate
                if len(set(vals)) > 1 and f in ("outlet", "publisher"):
                    prov.add("R")
            else:
                canon_val = None

            generic_cols[f] = canon_val
            generic_cols[f"{f}_provenance"] = sorted(list(prov))

        # Disparity diagnostic flags
        has_title_disp = len(set(titles)) > 1 if titles else False
        has_doi_disp = "E" in doi_prov or "D" in doi_prov
        has_year_disp = "T" in year_prov
        has_outlet_disp = len(set(r.get("outlet") for r in cluster_records if r.get("outlet"))) > 1

        record = {
            "canonical_id": canonical_id,
            "source_ids": source_ids,
            "institutions": institutions,
            "n_submissions": n_submissions,
            "is_multi_hep": is_multi_hep,
            "research_output_type": research_output_type,
            "title": canonical_title,
            "title_wellformed": title_wellformed,
            "title_provenance": sorted(list(title_prov)),
            "title_has_html": title_has_html,
            "title_has_tex": title_has_tex,
            "title_has_non_latin": title_has_non_latin,
            "title_has_mojibake": title_has_mojibake,
            "title_has_daterange": title_has_daterange,
            "title_daterange_status": title_daterange_status,
            "title_has_en_us": title_has_en_us,
            "title_has_en_gb": title_has_en_gb,
            "title_has_en_au": title_has_en_au,
            "doi": canonical_doi,
            "doi_wellformed": doi_wellformed,
            "doi_provenance": sorted(list(doi_prov)),
            "reference_year": canonical_year,
            "reference_year_provenance": sorted(list(year_prov)),
            "has_title_disparity": has_title_disp,
            "has_doi_disparity": has_doi_disp,
            "has_year_disparity": has_year_disp,
            "has_outlet_disparity": has_outlet_disp,
            **generic_cols,
        }
        return record

    @classmethod
    def combine_records(
        cls,
        processed_pq: Path = DATA_DIR / "era_research_outputs_processed.parquet",
        raw_pq: Path = DATA_DIR / "era_research_outputs_raw.parquet",
        output_pq: Path = DATA_DIR / "era_research_outputs_canonical.parquet",
        batch_size: int = 50_000,
    ) -> int:
        """Execute Stage C: load processed + raw, cluster, reconcile, and persist."""
        con = duckdb.connect()
        print(f"Stage C: Loading records from {processed_pq} and {raw_pq}...")
        q = f"""
        SELECT 
            p.*,
            r.institution,
            r.research_output_type
        FROM read_parquet('{processed_pq}') p
        JOIN read_parquet('{raw_pq}') r USING (id)
        ORDER BY p.id
        """
        rows = con.execute(q).fetchall()
        cols = [d[0] for d in con.description]
        records = [dict(zip(cols, r)) for r in rows]
        total_raw = len(records)
        print(f"Loaded {total_raw:,} records. Clustering multi-HEP duplicate groups...")

        clusters = cls.cluster_records(records)
        print(f"Formed {len(clusters):,} canonical clusters from {total_raw:,} raw records.")

        canonical_records = []
        for c_idx, cluster_idxs in enumerate(clusters):
            cluster_rows = [records[i] for i in cluster_idxs]
            canonical_records.append(cls.reconcile_cluster(cluster_rows))

        # Write to parquet
        print(f"Writing {len(canonical_records):,} canonical records to {output_pq}...")
        table = pa.Table.from_pylist(canonical_records)
        pq.write_table(table, output_pq, compression="snappy")
        print(f"Successfully generated {output_pq} ({len(canonical_records):,} works, {total_raw:,} submissions mapped)!")
        return len(canonical_records)
