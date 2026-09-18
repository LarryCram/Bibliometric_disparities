"""Parse GROBID's TEI XML output into structured tables for comparison
against OpenAlex/WOS/Scopus metadata (see the [[project-era-bibliometric-
discrepancies]] framing - this is the "extracted straight from the PDF"
perspective).

The PDF filename stem is the OpenAlex work_idx (download_oa_pdfs_direct.py names
files that way), so oax_id is the join key back to
pipeline_provenance.parquet (built by build_provenance.py), which chains
oax_id back to era_id - one oax_id can map to multiple era_id (the
multi-institution ERA duplicates found elsewhere in this project), so that
mapping lives in pipeline_provenance.parquet rather than being folded in here.

Produces three parquet tables:
  - tei_papers.parquet: one row per paper (title, authors, abstract, counts)
  - tei_references.parquet: one row per bibliography entry (the reference list)
  - tei_mentions.parquet: one row per in-text citation mention, linked to its
    bibl_id where GROBID resolved it (some mentions have no target - GROBID
    recognized a citation-shaped mention but couldn't match it to a specific
    bibliography entry; these are kept with bibl_id = NULL rather than
    dropped, since "GROBID found a mention but couldn't resolve it" is
    itself a useful discrepancy signal).
"""

import json
import sys
from pathlib import Path

import pyarrow as pa
import pyarrow.parquet as pq
from lxml import etree

sys.path.insert(0, str(Path(__file__).parent.parent))
from config import TEI_DIR

DATA_DIR = Path(__file__).parent.parent / "data"

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


def parse_bibl(bibl_el):
    bibl_id = bibl_el.get("{http://www.w3.org/XML/1998/namespace}id")
    # lxml elements are falsy when childless, so `A or B` silently mis-fires
    # for a title with only text content (no child elements) - must check
    # `is not None` explicitly rather than relying on truthiness.
    title_el = bibl_el.find(".//tei:title[@level='a']", NS)
    if title_el is None:
        title_el = bibl_el.find(".//tei:title", NS)
    authors = [parse_person(p) for p in bibl_el.findall(".//tei:author/tei:persName", NS)]
    authors = [a for a in authors if a]
    date_el = bibl_el.find(".//tei:date[@when]", NS)
    doi_el = bibl_el.find(".//tei:idno[@type='DOI']", NS)
    return {
        "bibl_id": bibl_id,
        "ref_title": text_of(title_el),
        "ref_authors": json.dumps(authors),
        "ref_year": (date_el.get("when")[:4] if date_el is not None and date_el.get("when") else None),
        "ref_doi": text_of(doi_el),
    }


def parse_one(tei_path):
    oax_id = int(tei_path.stem.replace(".tei", ""))
    tree = etree.parse(str(tei_path))

    title = text_of(tree.find(".//tei:titleStmt/tei:title", NS))
    authors = [parse_person(p) for p in tree.findall(".//tei:sourceDesc//tei:author/tei:persName", NS)]
    authors = [a for a in authors if a]
    abstract = text_of(tree.find(".//tei:profileDesc/tei:abstract", NS))

    bibls = tree.findall(".//tei:back//tei:biblStruct", NS)
    references = [dict(parse_bibl(b), oax_id=oax_id) for b in bibls]

    mentions = []
    for div in tree.findall(".//tei:text/tei:body//tei:div", NS):
        head_el = div.find("tei:head", NS)
        section = text_of(head_el)
        for ref in div.findall(".//tei:ref[@type='bibr']", NS):
            target = ref.get("target")
            bibl_id = target.lstrip("#") if target else None
            mentions.append({
                "oax_id": oax_id,
                "section": section,
                "bibl_id": bibl_id,
                "mention_text": text_of(ref),
            })

    paper = {
        "oax_id": oax_id,
        "title": title,
        "authors": json.dumps(authors),
        "n_authors": len(authors),
        "abstract": abstract,
        "n_references": len(bibls),
        "n_intext_mentions": len(mentions),
        "n_unresolved_mentions": sum(1 for m in mentions if m["bibl_id"] is None),
    }
    return paper, references, mentions


def main():
    tei_files = sorted(TEI_DIR.glob("*.tei.xml"))
    print(f"Parsing {len(tei_files)} TEI files...")

    papers, all_refs, all_mentions = [], [], []
    n_errors = 0
    for tei_path in tei_files:
        try:
            paper, refs, mentions = parse_one(tei_path)
            papers.append(paper)
            all_refs.extend(refs)
            all_mentions.extend(mentions)
        except Exception as e:
            print(f"  error parsing {tei_path.name}: {type(e).__name__}: {e}")
            n_errors += 1

    pq.write_table(pa.Table.from_pylist(papers), DATA_DIR / "tei_papers.parquet", compression="snappy")
    pq.write_table(pa.Table.from_pylist(all_refs), DATA_DIR / "tei_references.parquet", compression="snappy")
    pq.write_table(pa.Table.from_pylist(all_mentions), DATA_DIR / "tei_mentions.parquet", compression="snappy")

    print(f"\n{len(papers)} papers parsed ({n_errors} errors), "
          f"{len(all_refs)} references, {len(all_mentions)} in-text mentions "
          f"({sum(1 for m in all_mentions if m['bibl_id'] is None)} unresolved)")
    print(f"Written to {DATA_DIR}/tei_papers.parquet, tei_references.parquet, tei_mentions.parquet")


if __name__ == "__main__":
    main()
