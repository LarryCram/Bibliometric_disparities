"""Shared path configuration for the whole project, built from K_DRIVE and
M_DRIVE in .env.
"""

import os
from pathlib import Path


def _load_dotenv():
    env_path = Path(__file__).resolve().parent / ".env"
    env = {}
    if env_path.exists():
        for line in env_path.read_text().splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, _, value = line.partition("=")
            env[key.strip()] = value.strip()
    return env


def _drive_root(name, default):
    value = _load_dotenv().get(name) or os.environ.get(name) or default
    path = Path(value).expanduser()
    if not path.exists():
        raise FileNotFoundError(
            f"{name}={path} does not exist - is the drive mounted? "
            f"Set {name} in .env to the correct path for this machine."
        )
    return path


PROJECT_ROOT = Path(__file__).resolve().parent
DATA_DIR = PROJECT_ROOT / "data"
DATA_PERSISTED_DIR = PROJECT_ROOT / "data_persisted"

K_DRIVE = _drive_root("K_DRIVE", "~/k")
M_DRIVE = _drive_root("M_DRIVE", "~/m")

# Raw OpenAlex works snapshot (~676GB)
RAW_OPENALEX_WORKS = K_DRIVE / "openalex_jul26/parquet/works"

# Flattened OpenAlex works exports
OPENALEX_COMPACT_WORKS = M_DRIVE / "openalex_jul26/parquet_converted/compact/works"
OPENALEX_XPAC_WORKS = M_DRIVE / "openalex_jul26/parquet_converted/xpac/works"

# OpenAlex's Institutions entity table
OPENALEX_INSTITUTIONS = M_DRIVE / "openalex_jul26/parquet_converted/institutions.parquet"

# DuckDB scratch/spill directory
DUCKDB_TMP = M_DRIVE / "duckdb_tmp"

# Downloaded PDFs and GROBID TEI XML output
PDF_DIR = K_DRIVE / "era_oa_pdfs"
TEI_DIR = K_DRIVE / "era_oa_pdfs_tei"

# pybliometrics request cache
SCOPUS_DIR = K_DRIVE / "era" / ".scopus"

# diskcache.Cache directory for raw Trove API responses
TROVE_DIR = K_DRIVE / "era" / ".trove"
