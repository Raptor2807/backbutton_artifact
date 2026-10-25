#!/usr/bin/env python3
"""
validate_classification.py -- score the model's categories against the 417
hand-coded domains, and emit a file the census table can read.

WHY THIS STEP EXISTS

    A categorisation with no accuracy figure is an assumption. We have 417
    domains coded by hand, so the model's output can be measured on the same
    corpus the paper describes rather than trusted on reputation. The result
    is reportable either way: a high figure justifies using the labels, a low
    one is cheaper to learn now than after the tables are built.

WHAT IS AND IS NOT AGREEMENT

    The hand-coding is treated as the reference, not as truth. Where the two
    disagree, either may be right; what the number measures is how far the
    model can stand in for the coder. Disagreements are printed by pair so the
    systematic ones are visible, because a model that always calls shopping
    "business" is a different problem from one that is randomly wrong.

UNKNOWN AND PARKED

    `unknown` is the model declining to answer and is NOT a category. It is
    excluded from the accuracy denominator and counted separately, in the same
    way a skipped review is. Counting a refusal as a miss understates the
    model; counting it as a hit is worse.

USAGE
    python validate_classification.py --model top10k_classified.csv \\
        --hand results/category_mapping_coded.csv --out model_categories.csv
"""
from __future__ import annotations

import argparse
import csv
import math
import sys
from collections import Counter, defaultdict

NON_ANSWERS = {"unknown", "", "none", "n/a", "failed"}


def wilson(k, n, z=1.959963985):
    if n == 0:
        return (0.0, 0.0)
    p, z2 = k / n, z * z
    den = 1.0 + z2 / n
    cen = (p + z2 / (2 * n)) / den
    hw = z * math.sqrt(p * (1 - p) / n + z2 / (4 * n * n)) / den
    return (max(0.0, cen - hw), min(1.0, cen + hw))


def pick(row, *names):
    for n in names:
        for k in row:
            if k.strip().lower() == n:
                return (row[k] or "").strip()
    return ""


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--model", required=True)
    ap.add_argument("--hand", required=True)
    ap.add_argument("--out", default=None,
                    help="write domain,cf_mapped for category_census.py")
    a = ap.parse_args()

    with open(a.model, newline="", encoding="utf-8-sig") as f:
        mrows = list(csv.DictReader(f))
    with open(a.hand, newline="", encoding="utf-8-sig") as f:
        hrows = list(csv.DictReader(f))
    if not mrows or not hrows:
        sys.exit("one of the input files is empty")
    print(f"model columns: {list(mrows[0])}")

    model, conf, src = {}, {}, {}
    for r in mrows:
        d = pick(r, "domain", "host", "hostname").lower()
        if not d:
            continue
        model[d] = pick(r, "category", "cf_mapped", "label").lower()
        conf[d] = pick(r, "confidence", "conf").lower()
        src[d] = pick(r, "classified_by", "labelled_by", "labeled_by", "source", "method")

    hand = {}
    for r in hrows:
        d = pick(r, "domain").lower()
        c = pick(r, "category").lower()
        if d and c:
            hand[d] = c

    print(f"model rows {len(model)}   hand-coded {len(hand)}")
    overlap = sorted(set(model) & set(hand))
    print(f"overlap    {len(overlap)}")
    if not overlap:
        sys.exit("no overlap -- check the domain columns match in form")

    answered = [d for d in overlap if model[d] not in NON_ANSWERS]
    declined = len(overlap) - len(answered)
    hits = sum(1 for d in answered if model[d] == hand[d])
    n = len(answered)
    lo, hi = wilson(hits, n)
    print(f"\n{'='*58}\nAGREEMENT WITH THE HAND-CODED SET\n{'='*58}")
    print(f"agreed {hits} of {n} answered = {100*hits/n:.1f}%"
          f"  (95% CI {100*lo:.1f}-{100*hi:.1f})")
    print(f"declined ('unknown' etc.): {declined} of {len(overlap)}"
          f"  ({100*declined/len(overlap):.1f}%), excluded from the above")

    # ---- per confidence band ----
    band = defaultdict(lambda: [0, 0])
    for d in answered:
        band[conf.get(d, "?")][1] += 1
        if model[d] == hand[d]:
            band[conf.get(d, "?")][0] += 1
    if len(band) > 1:
        print(f"\n{'confidence':<14}{'agreed':>8}{'n':>7}{'rate':>9}   95% CI")
        print("-" * 52)
        for b in ("high", "medium", "low"):
            if b not in band:
                continue
            k, m = band[b]
            l, h = wilson(k, m)
            print(f"{b:<14}{k:>8}{m:>7}{100*k/m:>8.1f}%   "
                  f"{100*l:.1f}-{100*h:.1f}")
        print("-" * 52)
        print("If low-confidence rows agree far less, the confidence field is")
        print("usable as a filter and should be reported as one.")

    # ---- per source ----
    bysrc = defaultdict(lambda: [0, 0])
    for d in answered:
        s = src.get(d, "?") or "?"
        bysrc[s][1] += 1
        if model[d] == hand[d]:
            bysrc[s][0] += 1
    if len(bysrc) > 1:
        print(f"\n{'labelled by':<26}{'agreed':>8}{'n':>7}{'rate':>9}")
        print("-" * 50)
        for s, (k, m) in sorted(bysrc.items(), key=lambda x: -x[1][1]):
            print(f"{s:<26}{k:>8}{m:>7}{100*k/m:>8.1f}%")
        print("-" * 50)
        print("Rows classified without page metadata rest on the hostname")
        print("alone. If they agree less, say so: sites that block a fetch are")
        print("not a random subset and hijackers are over-represented in them.")

    # ---- per category, as recalled by the hand coding ----
    per = defaultdict(lambda: [0, 0])
    for d in answered:
        per[hand[d]][1] += 1
        if model[d] == hand[d]:
            per[hand[d]][0] += 1
    print(f"\n{'hand category':<14}{'agreed':>8}{'n':>7}{'rate':>9}")
    print("-" * 42)
    for c, (k, m) in sorted(per.items(), key=lambda x: -x[1][1]):
        print(f"{c:<14}{k:>8}{m:>7}{100*k/m:>8.1f}%")
    print("-" * 42)

    # ---- systematic confusions ----
    pairs = Counter((hand[d], model[d]) for d in answered if model[d] != hand[d])
    if pairs:
        print(f"\ndisagreements, hand -> model (top 12 of {sum(pairs.values())}):")
        for (h, m), c in pairs.most_common(12):
            print(f"   {h:<14} -> {m:<14}{c:>4}")

    if a.out:
        with open(a.out, "w", newline="", encoding="utf-8") as f:
            w = csv.writer(f)
            w.writerow(["domain", "cf_mapped", "cf_raw", "confidence"])
            for d, c in sorted(model.items()):
                w.writerow([d, "" if c in NON_ANSWERS else c,
                            src.get(d, ""), conf.get(d, "")])
        print(f"\nwrote {a.out} for category_census.py --categories")
    return 0


if __name__ == "__main__":
    sys.exit(main())
