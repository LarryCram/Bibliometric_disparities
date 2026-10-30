#!/bin/bash
set -euo pipefail

echo "[$(date '+%H:%M:%S')] Monitoring harvest process (PID 228862)..."
while kill -0 228862 2>/dev/null; do
    sleep 20
done

echo "[$(date '+%H:%M:%S')] Harvest process finished! Verifying output Parquet files..."
COUNT=$(ls -1 data/trove_harvest/hep_*.parquet | wc -l)
echo "[$(date '+%H:%M:%S')] Found $COUNT harvested files in data/trove_harvest/"

echo "[$(date '+%H:%M:%S')] Executing full corpus synthesis across all 40 Australian universities..."
.venv/bin/python3 src/era/analysis/synthesize_trove_harvest.py

echo "[$(date '+%H:%M:%S')] Full Trove HEP Corpus pipeline execution complete!"
