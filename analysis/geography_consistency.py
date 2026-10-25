#!/usr/bin/env python3
"""
geography_consistency.py -- for each confirmed hijacking website, at how many
vantage points did it hijack?

WHY THE DENOMINATOR IS RESTRICTED

    A website that could not be measured somewhere is not a website that
    behaved benignly there. We therefore count only websites testable at ALL
    vantage points, so "hijacked at 3 of 8" means three positives and five
    negatives, not three positives and five unknowns. Relaxing this inflates
    the single-vantage bucket, which is the bucket the paper's claim rests on.

WHY THE CONFIRMED SET

    Section 4 uses screenshot-confirmed websites throughout. Using the
    detector's output here instead would mix a verified numerator into an
    unverified one and change the shape of the distribution.

USAGE
    python3 geography_consistency.py \
        --verdicts results/verdicts.csv \
        --reviewed results/per_domain.csv \
        --out data/aggregates/geography_consistency.csv
"""
from __future__ import annotations

import argparse
import collections
import csv
import sys

# A measurement is testable when it produced a verdict. Defining it as "not in
# a list of artifact labels" admits any label the list forgot -- CSR_PENDING in
# our data -- and silently enlarges the denominator. Select on the verdict.
TESTABLE = {"HIJACKING", "BENIGN"}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--verdicts", required=True)
    ap.add_argument("--reviewed", required=True,
                    help="per-domain review output; SKIP counts as confirmed")
    ap.add_argument("--exclude", default="",
                    help="comma-separated vantage points to drop")
    ap.add_argument("--out", default=None)
    a = ap.parse_args()

    drop = {v.strip() for v in a.exclude.split(",") if v.strip()}

    with open(a.reviewed, newline="", encoding="utf-8-sig") as f:
        confirmed = {r["domain"].strip().lower()
                     for r in csv.DictReader(f)
                     if r["verdict"].strip().upper() in ("HIJACKING", "SKIP")}

    testable = collections.defaultdict(set)
    hijacked = collections.defaultdict(set)
    vantages = set()
    with open(a.verdicts, newline="", encoding="utf-8-sig") as f:
        for r in csv.DictReader(f):
            v = r["vantage"].strip()
            if v in drop:
                continue
            vantages.add(v)
            d = r["domain"].strip().lower()
            if r["domain_classification"] not in TESTABLE:
                continue
            testable[d].add(v)
            if r["domain_classification"] == "HIJACKING":
                hijacked[d].add(v)

    V = sorted(vantages)
    everywhere = {d for d, s in testable.items() if len(s) == len(V)}
    pos = {d for d in everywhere if hijacked[d] and d in confirmed}
    if not pos:
        sys.exit("no confirmed positives measurable at every vantage point")

    dist = collections.Counter(len(hijacked[d]) for d in pos)
    n = len(pos)
    print(f"vantage points        {len(V)}  {V}")
    print(f"testable everywhere   {len(everywhere)}")
    print(f"confirmed hijacking   {n}\n")
    print(f"{'vantage points':>16}{'websites':>10}{'share':>9}")
    print("-" * 35)
    rows = []
    for k in range(1, len(V) + 1):
        print(f"{k:>16}{dist[k]:>10}{100*dist[k]/n:>8.2f}%")
        rows.append([k, dist[k], round(100 * dist[k] / n, 2)])
    print("-" * 35)
    print(f"{'total':>16}{n:>10}{100.0:>8.2f}%")
    print(f"\nall {len(V)}: {dist[len(V)]} ({100*dist[len(V)]/n:.1f}%)   "
          f"exactly one: {dist[1]} ({100*dist[1]/n:.1f}%)")

    one = [d for d in pos if len(hijacked[d]) == 1]
    byv = collections.Counter(next(iter(hijacked[d])) for d in one)
    print("\nsingle-vantage positives, by vantage point:")
    for v, c in byv.most_common():
        print(f"   {v:<24}{c:>4}")
    print("\nA vantage point over-represented here may be serving genuinely")
    print("location-specific behavior, or its exit address may be treated")
    print("differently. Report its untestable rate alongside this count.")

    if a.out:
        with open(a.out, "w", newline="", encoding="utf-8") as f:
            w = csv.writer(f)
            w.writerow(["n_vantage_points_hijacking", "confirmed_websites",
                        "pct_of_confirmed"])
            w.writerows(rows)
            w.writerow(["TOTAL", n, 100.0])
        print(f"\nwrote {a.out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
