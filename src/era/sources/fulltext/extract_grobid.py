"""High-performance parallel extractor for GROBID TEI XML outputs.

Extracts:
1. data/grobid_references.parquet: Verbatim bibliography reference lists.
2. data/grobid_mentions.parquet: In-text citation mentions with section location and context.
3. data/grobid_papers.parquet: Paper-level metadata (title, authors, counts).
"""

import os
import sys
import time
from pathlib import Path
from concurrent.futures import ProcessPoolExecutor
import pyarrow as pa
import pyarrow.parquet as pq
from lxml import etree

from era.config import TEI_DIR, DATA_DIR

REFERENCES_PARQUET = DATA_DIR / "grobid_references.parquet"
MENTIONS_PARQUET = DATA_DIR / "grobid_mentions.parquet"
PAPERS_PARQUET = DATA_DIR / "grobid_papers.parquet"

NS = {"tei": "http://www.tei-c.org/ns/1.0"}


def text_of(el):
    return "".join(el.itertext()).strip() if el is not None else None


def parse_person(persname_el):
    if persname_el is None:
        return None
    forename = persname_el.find("tei:forename", NS)
    surname = persname_el.find("tei:surname", NS)
    parts = [text_of(forename), text_of(surname)]
    return " ".join(p for p in parts if p) or None


def parse_single_tei(path_str: str):
    """Parse a single TEI XML file into paper, references, and mentions records."""
    try:
        base = os.path.basename(path_str)
        stem = base.replace(".tei.xml", "").replace(".xml", "")
        oax_id = int(stem)
    except ValueError:
        return None, None, None

    try:
        tree = etree.parse(path_str)
    except Exception:
        return None, None, None

    # 1. Paper metadata
    t_stmt = tree.find(".//tei:titleStmt/tei:title", NS)
    paper_title = text_of(t_stmt)
    paper_authors = [parse_person(p) for p in tree.findall(".//tei:sourceDesc//tei:author/tei:persName", NS)]
    paper_authors = [a for a in paper_authors if a]

    # 2. Reference list
    refs = []
    for b in tree.findall(".//tei:back//tei:biblStruct", NS):
        bid = b.get("{http://www.w3.org/XML/1998/namespace}id") or b.get("xml:id")

        # Analytical title (article/chapter) vs monograph title
        t_el = b.find(".//tei:analytic/tei:title", NS)
        if t_el is None:
            t_el = b.find(".//tei:title[@level='a']", NS)
        if t_el is None:
            t_el = b.find(".//tei:title[@level='m']", NS)
        if t_el is None:
            t_el = b.find(".//tei:title", NS)

        j_el = b.find(".//tei:monogr/tei:title", NS)
        d_el = b.find(".//tei:date[@when]", NS)
        doi_el = b.find(".//tei:idno[@type='DOI']", NS)

        authors = [parse_person(p) for p in b.findall(".//tei:author/tei:persName", NS)]
        authors = [a for a in authors if a]

        year_val = d_el.get("when")[:4] if d_el is not None and d_el.get("when") else None

        refs.append({
            "citing_oax_id": oax_id,
            "bibl_id": bid,
            "ref_title": text_of(t_el),
            "ref_authors": "; ".join(authors) if authors else None,
            "ref_year": year_val,
            "ref_journal": text_of(j_el),
            "ref_doi": text_of(doi_el),
        })

    # 3. In-text mentions
    mentions = []
    for div in tree.findall(".//tei:text/tei:body//tei:div", NS):
        head = div.find("tei:head", NS)
        sec = text_of(head)
        for ref in div.findall(".//tei:ref[@type='bibr']", NS):
            target = ref.get("target")
            bid = target.lstrip("#") if target else None
            mentions.append({
                "citing_oax_id": oax_id,
                "bibl_id": bid,
                "section": sec,
                "mention_text": text_of(ref),
            })

    paper = {
        "oax_id": oax_id,
        "paper_title": paper_title,
        "authors": "; ".join(paper_authors) if paper_authors else None,
        "n_references": len(refs),
        "n_mentions": len(mentions),
    }

    return paper, refs, mentions


def run_extraction(
    batch_size: int = 5000,
    max_workers: int = 16,
):
    print("=" * 80)
    print("STARTING GROBID TEI PARALLEL EXTRACTION")
    print("=" * 80)
    t0 = time.time()

    tei_files = sorted(str(p) for p in TEI_DIR.glob("*.tei.xml"))
    total_files = len(tei_files)
    print(f"Discovered {total_files:,} TEI XML files in {TEI_DIR}")

    if total_files == 0:
        print("No TEI XML files found. Exiting.")
        return

    # Arrow schemas
    paper_schema = pa.schema([
        ("oax_id", pa.int64()),
        ("paper_title", pa.string()),
        ("authors", pa.string()),
        ("n_references", pa.int32()),
        ("n_mentions", pa.int32()),
    ])

    ref_schema = pa.schema([
        ("citing_oax_id", pa.int64()),
        ("bibl_id", pa.string()),
        ("ref_title", pa.string()),
        ("ref_authors", pa.string()),
        ("ref_year", pa.string()),
        ("ref_journal", pa.string()),
        ("ref_doi", pa.string()),
    ])

    mention_schema = pa.schema([
        ("citing_oax_id", pa.int64()),
        ("bibl_id", pa.string()),
        ("section", pa.string()),
        ("mention_text", pa.string()),
    ])

    writer_papers = pq.ParquetWriter(PAPERS_PARQUET, paper_schema, compression="snappy")
    writer_refs = pq.ParquetWriter(REFERENCES_PARQUET, ref_schema, compression="snappy")
    writer_mentions = pq.ParquetWriter(MENTIONS_PARQUET, mention_schema, compression="snappy")

    tot_papers = 0
    tot_refs = 0
    tot_mentions = 0

    try:
        with ProcessPoolExecutor(max_workers=max_workers) as pool:
            for i in range(0, total_files, batch_size):
                batch_files = tei_files[i : i + batch_size]
                t_b0 = time.time()
                results = list(pool.map(parse_single_tei, batch_files))

                b_papers, b_refs, b_mentions = [], [], []
                for p, r, m in results:
                    if p:
                        b_papers.append(p)
                    if r:
                        b_refs.extend(r)
                    if m:
                        b_mentions.extend(m)

                # Write to parquet
                if b_papers:
                    writer_papers.write_table(pa.Table.from_pylist(b_papers, schema=paper_schema))
                if b_refs:
                    writer_refs.write_table(pa.Table.from_pylist(b_refs, schema=ref_schema))
                if b_mentions:
                    writer_mentions.write_table(pa.Table.from_pylist(b_mentions, schema=mention_schema))

                tot_papers += len(b_papers)
                tot_refs += len(b_refs)
                tot_mentions += len(b_mentions)

                processed = min(i + batch_size, total_files)
                pct = processed * 100.0 / total_files
                rate = len(batch_files) / (time.time() - t_b0)
                print(f"Progress: {processed:,}/{total_files:,} ({pct:.1f}%) | "
                      f"Papers: {tot_papers:,} | Refs: {tot_refs:,} | Mentions: {tot_mentions:,} | "
                      f"Batch rate: {rate:.1f} files/s")

    finally:
        writer_papers.close()
        writer_refs.close()
        writer_mentions.close()

    elapsed = time.time() - t0
    print("=" * 80)
    print(f"EXTRACTION COMPLETED IN {elapsed:.1f}s ({total_files/elapsed:.1f} files/s)")
    print(f"  Papers output:     {PAPERS_PARQUET} ({tot_papers:,} papers)")
    print(f"  References output: {REFERENCES_PARQUET} ({tot_refs:,} bibliography references)")
    print(f"  Mentions output:   {MENTIONS_PARQUET} ({tot_mentions:,} in-text mentions)")
    print("=" * 80)


if __name__ == "__main__":
    run_extraction()
