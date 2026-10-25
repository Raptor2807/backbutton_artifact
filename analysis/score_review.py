#!/usr/bin/env python3
"""
score_review.py -- turn reviewed.csv into a precision figure, overall and per
evidential tier.

WHAT THIS MEASURES

    Precision on the positive set: of the domains the detector called
    hijacking, how many a human agrees with. It says nothing about recall,
    which cannot be seen from positives at all.

TWO THINGS THAT WOULD INFLATE IT IF LEFT UNSAID

    1. The vantage shown to the reviewer was the one with the STRONGEST
       mechanism evidence for that domain, so every case was seen at its best.
       Precision measured this way is an upper bound on precision over all
       vantages. Say so wherever the number appears.
    2. SKIP is not BENIGN. Dropping skipped rows silently moves them out of
       the denominator; they are reported separately so the reader can see how
       many questions went unanswered.

DUPLICATES

    review_app.py appends a row per click, so a domain reviewed twice appears
    twice. The last verdict for each _idx wins, matching what the app itself
    shows as the current state.

USAGE
    python3 score_review.py --reviewed /var/tmp/review417/reviewed.csv \\
        --input /var/tmp/review417/review_input.csv
"""
from __future__ import annotations

import argparse
import csv
import math
import sys
from collections import Counter, defaultdict

TIER_ORDER = ["MECH_ACTIVE", "MECH_PLANTED", "MECH_REDIRECT",
              "OUTCOME_ONLY", "UNOBSERVABLE", "VOID_INSTRUMENT"]


def wilson(k, n, z=1.959963985):
    if n == 0:
        return (0.0, 0.0)
    p, z2 = k / n, z * z
    den = 1.0 + z2 / n
    cen = (p + z2 / (2 * n)) / den
    hw = z * math.sqrt(p * (1 - p) / n + z2 / (4 * n * n)) / den
    return (max(0.0, cen - hw), min(1.0, cen + hw))


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--reviewed", required=True)
    ap.add_argument("--input", default=None,
                    help="review_input.csv, to report how many were never seen")
    ap.add_argument("--out", default=None, help="write per-domain CSV")
    a = ap.parse_args()

    with open(a.reviewed, newline="", encoding="utf-8-sig") as f:
        rows = list(csv.DictReader(f))
    if not rows:
        sys.exit("reviewed.csv is empty")

    # last verdict per row index wins
    last = {}
    for r in rows:
        key = r.get("_idx") or r.get("domain")
        last[key] = r
    recs = list(last.values())

    v = Counter(r["verdict"].strip().upper() for r in recs)
    print(f"\nrows in file {len(rows)}   unique reviewed {len(recs)}")
    print("verdicts: " + ", ".join(f"{k} {n}" for k, n in v.most_common()))

    if a.input:
        with open(a.input, newline="", encoding="utf-8-sig") as f:
            total = sum(1 for _ in csv.DictReader(f))
        unseen = total - len(recs)
        print(f"queued {total}, reviewed {len(recs)}, not reached {unseen}")
        if unseen > 0:
            print("  Domains never shown are not evidence either way and are")
            print("  excluded from every figure below.")

    scored = [r for r in recs if r["verdict"].strip().upper() in
              ("HIJACKING", "BENIGN")]
    skipped = len(recs) - len(scored)
    if not scored:
        sys.exit("no HIJACKING/BENIGN verdicts to score")

    tp = sum(1 for r in scored if r["verdict"].strip().upper() == "HIJACKING")
    n = len(scored)
    lo, hi = wilson(tp, n)
    print(f"\n{'='*58}\nPRECISION ON THE POSITIVE SET\n{'='*58}")
    print(f"confirmed {tp} of {n} judged  =  {100*tp/n:.1f}%"
          f"  (95% CI {100*lo:.1f}-{100*hi:.1f})")
    if skipped:
        print(f"{skipped} skipped, excluded from the denominator")
    print("\nUpper bound: each domain was shown at the vantage with its")
    print("strongest mechanism evidence, so this is the best case per domain.")

    # ---- per tier ----
    by = defaultdict(lambda: [0, 0])
    for r in scored:
        t = (r.get("tier") or "?").strip()
        by[t][1] += 1
        if r["verdict"].strip().upper() == "HIJACKING":
            by[t][0] += 1
    print(f"\n{'tier':<18}{'confirmed':>10}{'judged':>8}{'precision':>11}"
          f"   {'95% CI':>13}")
    print("-" * 62)
    for t in TIER_ORDER + sorted(set(by) - set(TIER_ORDER)):
        if t not in by:
            continue
        k, m = by[t]
        l, h = wilson(k, m)
        flag = "  <- thin" if m < 20 else ""
        print(f"{t:<18}{k:>10}{m:>8}{100*k/m:>10.1f}%"
              f"   {100*l:>4.1f}-{100*h:<6.1f}{flag}")
    print("-" * 62)
    print("A tier whose precision is far below the others is one the paper")
    print("should report separately rather than pool into a single figure.")

    # ---- disagreements, for inspection ----
    fps = [r for r in scored if r["verdict"].strip().upper() == "BENIGN"]
    if fps:
        print(f"\n{len(fps)} judged BENIGN (detector false positives):")
        for r in sorted(fps, key=lambda x: x.get("tier", "")):
            note = (r.get("notes") or "").strip()
            print(f"   {r['domain']:<38}{r.get('tier',''):<16}"
                  f"{r.get('mechanism',''):<14}{note[:40]}")

    if a.out:
        with open(a.out, "w", newline="", encoding="utf-8") as f:
            w = csv.writer(f)
            w.writerow(["domain", "vantage", "page_type", "tier", "mechanism",
                        "verdict", "notes"])
            for r in sorted(recs, key=lambda x: x["domain"]):
                w.writerow([r["domain"], r.get("vantage", ""),
                            r.get("page_type", ""), r.get("tier", ""),
                            r.get("mechanism", ""), r["verdict"],
                            (r.get("notes") or "").strip()])
        print(f"\nwrote {a.out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
