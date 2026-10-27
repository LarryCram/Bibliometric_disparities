"""Orchestrator to harvest Gold OA papers one publisher at a time.

Runs all pending works for test-proven high-yield publishers in sequential order:
1. BMJ Open (100% direct download)
2. PNAS (Proceedings of the National Academy of Sciences)
3. Journal of Biological Chemistry (JBC)
4. MDPI (Nutrients, Sensors, IJMS, etc.)
5. Royal Society (Proceedings of the Royal Society B)
6. Journal of Neuroscience
7. Australasian Journal of Paramedicine (ECU repo)
"""

import argparse
import sys
import time
from typing import Any

from era.sources.fulltext.playwright_harvester import (
    DEFAULT_PROFILE_DIR,
    fetch_gold_oa_candidates,
    harvest_candidates,
)

PROVEN_PUBLISHERS = [
    ("AISeL (Information Systems)", "aisel.aisnet.org"),
    ("Oncotarget", "oncotarget"),
    ("ASM (Microbiology)", ".asm.org"),
    ("IOP (Institute of Physics)", "iopscience.iop.org"),
    ("MDPI", "mdpi"),
    ("JBC", "Journal of Biological Chemistry"),
    ("Journal of Neuroscience", "Journal of Neuroscience"),
    ("BMJ Open", "BMJ Open"),
    ("Paramedicine (ECU)", "ro.ecu.edu.au"),
    ("Royal Society B", "Proceedings of the Royal Society B"),
    ("PNAS", "Proceedings of the National Academy of Sciences"),
]


def run_sequential_harvest(
    limit_per_pub: int | None = None,
    delay: float = 1.2,
    headless: bool = False,
    selected_pubs: list[str] | None = None,
) -> dict[str, tuple[int, int]]:
    """Run sequential harvest across proven publishers."""
    results = {}
    pub_list = PROVEN_PUBLISHERS
    if selected_pubs:
        selected_lower = [p.lower() for p in selected_pubs]
        pub_list = [p for p in PROVEN_PUBLISHERS if any(s in p[0].lower() or s in p[1].lower() for s in selected_lower)]

    print(f"Starting sequential harvest across {len(pub_list)} proven publishers...\n")

    for label, pattern in pub_list:
        print(f"\n{'='*70}")
        print(f"=== PUBLISHER: {label} (pattern: '{pattern}')")
        print(f"{'='*70}")

        candidates = fetch_gold_oa_candidates(limit=limit_per_pub, publisher=pattern)
        if not candidates:
            print(f"No pending candidates remaining for {label}.\n")
            results[label] = (0, 0)
            continue

        print(f"Pending candidates to harvest for {label}: {len(candidates)}")
        t0 = time.time()
        success, fail = harvest_candidates(
            candidates,
            headless=headless,
            delay=delay,
            profile_dir=DEFAULT_PROFILE_DIR,
        )
        elapsed = time.time() - t0
        pct = (success / len(candidates) * 100) if candidates else 0.0
        print(f"\n--- {label} Result: {success}/{len(candidates)} succeeded ({pct:.1f}%) in {elapsed/60:.1f}m ---")
        results[label] = (success, fail)

    print(f"\n{'='*70}")
    print("=== FINAL SEQUENTIAL HARVEST SUMMARY ===")
    print(f"{'='*70}")
    total_succ = 0
    total_attempt = 0
    for label, (s, f) in results.items():
        att = s + f
        total_succ += s
        total_attempt += att
        pct = (s / att * 100) if att > 0 else 0.0
        print(f"  {label:28s}: {s:4d} / {att:4d} succeeded ({pct:.1f}%)")
    overall_pct = (total_succ / total_attempt * 100) if total_attempt > 0 else 0.0
    print(f"\nOverall: {total_succ} / {total_attempt} succeeded ({overall_pct:.1f}%).\n")
    return results


def main():
    parser = argparse.ArgumentParser(description="Harvest proven Gold OA publishers sequentially")
    parser.add_argument("--publisher", "-p", action="append", help="Specific publisher(s) to run (can repeat)")
    parser.add_argument("--limit", "-l", type=int, default=None, help="Limit papers per publisher (default: all)")
    parser.add_argument("--delay", type=float, default=1.2, help="Delay between requests in seconds (default: 1.2)")
    parser.add_argument("--headless", action="store_true", default=False, help="Run headless Chromium (default: False)")
    args = parser.parse_args()

    run_sequential_harvest(
        limit_per_pub=args.limit,
        delay=args.delay,
        headless=args.headless,
        selected_pubs=args.publisher,
    )


if __name__ == "__main__":
    main()
