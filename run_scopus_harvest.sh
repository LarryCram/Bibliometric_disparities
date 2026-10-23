#!/usr/bin/env bash
set -e

PROJECT_DIR="/home/lc/Projects_Antigravity/Bibliometric_disparities"
cd "$PROJECT_DIR"

LOG_FILE="$PROJECT_DIR/scopus_title_harvest.log"
PID_FILE="$PROJECT_DIR/scopus_title_harvest.pid"

echo $$ > "$PID_FILE"
echo "=== [$(date)] Starting Scopus Title Harvest Pipeline (PID: $$) ===" >> "$LOG_FILE"

# Step 1: Fetch Scopus records by Title for all non-DOI canonical journal articles
echo "[$(date)] Phase 1: Fetching titles and warming pybliometrics cache..." >> "$LOG_FILE"
.venv/bin/python -u -m era.sources.scopus.fetch_titles --print-every 100 >> "$LOG_FILE" 2>&1

# Step 2: Build title parquet shard from pybliometrics cache
echo "[$(date)] Phase 2: Compiling data/scopus_records/titles.parquet..." >> "$LOG_FILE"
.venv/bin/python -u -m era.sources.scopus.build_title_shard >> "$LOG_FILE" 2>&1

# Step 3: Re-execute Scopus matching and disparity evaluation
echo "[$(date)] Phase 3: Updating Scopus matching and Olensky disparities..." >> "$LOG_FILE"
.venv/bin/python -u -m era.matching.match_scopus >> "$LOG_FILE" 2>&1

echo "=== [$(date)] Scopus Title Harvest Pipeline Completed Successfully! ===" >> "$LOG_FILE"
rm -f "$PID_FILE"
