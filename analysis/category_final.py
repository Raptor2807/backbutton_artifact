#!/usr/bin/env python3
"""
category_final.py -- the Section 4.3 numbers, built on hand-verified positives
and model categories.

WHAT CHANGED FROM THE EARLIER VERSION

    The numerator is no longer the detector's output. Every positive in it was
    confirmed by a human against the before/after screenshots, so a category
    rate here is not inflated by the detector's false positives. The
    denominator is still every testable domain, which is not verified: a
    domain the detector called benign was never looked at. So the rate is
    "confirmed hijackers per testable domain", and recall remains unmeasured.

THREE EXCLUSIONS, EACH STATED RATHER THAN SILENT

    unknown     the model declined to classify. Not a category. Excluded from
                both numerator and denominator, because leaving it in the
                denominator dilutes every rate by domains nobody classified.
    SKIP        the reviewer did not judge. Excluded from the numerator, kept
                in the denominator, since the domain was still measured.
    BENIGN      the reviewer overturned the detector. These are removed from
                the numerator; that is the entire point of the exercise.

USAGE
    python category_final.py --categories top10k_classified.csv \\
        --reviewed results/per_domain.csv --verdicts verdicts.csv \\
        --out results/category_final.csv
"""
from __future__ import annotations

import argparse
import csv
import math
import re
import sys
from collections import Counter, defaultdict

NON_ANSWER = {"unknown", "", "none", "n/a", "failed"}
SITE_UNTESTABLE = {"UNTESTABLE", "BOT_GATE", "AUTH_WALL", "CONNECTION_FAILED"}
OURS_UNTESTABLE = {"ERROR", "TIMEOUT_KILLED"}
NO_RENDER = {"NO_LINK"}
GENERIC = {"ac", "bank", "bet", "biz", "co", "com", "edu", "firm", "gen", "go",
           "gob", "gov", "ind", "info", "int", "jus", "k12", "mil", "ne",
           "net", "nic", "or", "org", "pages", "res", "sch", "tv", "vlog",
           "web"}


def brand(host):
    p = host.lower().strip().rstrip(".").split(".")
    if len(p) >= 3 and p[-2] in GENERIC and len(p[-1]) == 2 and p[-1].isalpha():
        p = p[:-2]
    elif len(p) > 1:
        p = p[:-1]
    while len(p) > 1 and p[-1] in GENERIC:
        p = p[:-1]
    lab = p[-1] if p else host
    return re.sub(r"\d+$", "", lab) or lab


def wilson(k, n, z=1.959963985):
    if n == 0:
        return (0.0, 0.0)
    p, z2 = k / n, z * z
    den = 1.0 + z2 / n
    cen = (p + z2 / (2 * n)) / den
    hw = z * math.sqrt(p * (1 - p) / n + z2 / (4 * n * n)) / den
    return (max(0.0, cen - hw), min(1.0, cen + hw))


def _g(ser, a, x):
    if x < a + 1.0:
        s = d = 1.0 / a
        ap = a
        for _ in range(500):
            ap += 1
            d *= x / ap
            s += d
            if abs(d) < abs(s) * 1e-14:
                break
        return 1.0 - s * math.exp(-x + a * math.log(x) - math.lgamma(a))
    b, c, d = x + 1.0 - a, 1e300, 1.0 / (x + 1.0 - a)
    h = d
    for i in range(1, 500):
        an = -i * (i - a)
        b += 2
        d = an * d + b
        d = d if abs(d) > 1e-300 else 1e-300
        c = b + an / c
        c = c if abs(c) > 1e-300 else 1e-300
        d = 1 / d
        de = d * c
        h *= de
        if abs(de - 1) < 1e-14:
            break
    return math.exp(-x + a * math.log(x) - math.lgamma(a)) * h


def chi2_sf(x, df):
    return 1.0 if x <= 0 or df <= 0 else _g(None, df / 2.0, x / 2.0)


def col(row, *names):
    for n in names:
        for k in row:
            if k.strip().lower() == n:
                return (row[k] or "").strip()
    return ""


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--categories", required=True)
    ap.add_argument("--reviewed", required=True)
    ap.add_argument("--verdicts", required=True)
    ap.add_argument("--exclude", default="nigeria")
    ap.add_argument("--min-n", type=int, default=30)
    ap.add_argument("--out", default=None)
    a = ap.parse_args()

    cat = {}
    with open(a.categories, newline="", encoding="utf-8-sig") as f:
        for r in csv.DictReader(f):
            d = col(r, "domain", "host").lower()
            if d:
                cat[d] = col(r, "category", "cf_mapped", "label").lower()

    verdict = {}
    with open(a.reviewed, newline="", encoding="utf-8-sig") as f:
        for r in csv.DictReader(f):
            d = col(r, "domain").lower()
            if d:
                verdict[d] = col(r, "verdict").upper()

    drop = {v.strip() for v in a.exclude.split(",") if v.strip()}
    testable = set()
    detected = set()
    with open(a.verdicts, newline="", encoding="utf-8-sig") as f:
        for r in csv.DictReader(f):
            if r["vantage"] in drop:
                continue
            d = r["domain"].strip().lower()
            dc, mc = r["domain_classification"], r.get("main_page_classification", "")
            if dc in OURS_UNTESTABLE or dc in SITE_UNTESTABLE or dc in NO_RENDER:
                continue
            testable.add(d)
            if dc == "HIJACKING" or mc == "HIJACKING":
                detected.add(d)

    confirmed = {d for d in detected if verdict.get(d) == "HIJACKING"}
    overturned = {d for d in detected if verdict.get(d) == "BENIGN"}
    unjudged = detected - confirmed - overturned

    print(f"testable domains          {len(testable)}")
    print(f"detector positives        {len(detected)}")
    print(f"  confirmed by review     {len(confirmed)}")
    print(f"  overturned (benign)     {len(overturned)}")
    print(f"  skipped / not judged    {len(unjudged)}")
    nocat = sum(1 for d in testable if cat.get(d, "") in NON_ANSWER)
    print(f"no model category         {nocat} of {len(testable)}"
          f"  ({100*nocat/len(testable):.1f}%), excluded both sides")

    pool = [d for d in testable if cat.get(d, "") not in NON_ANSWER]
    print(f"classified and testable   {len(pool)}"
          f"  ({100*len(pool)/len(testable):.1f}% coverage)")

    def table(unit_fn, title):
        tot, pos, members = Counter(), Counter(), defaultdict(list)
        for d in pool:
            members[(cat[d], unit_fn(d))].append(d)
        for (c, _u), ds in members.items():
            tot[c] += 1
            if sum(1 for x in ds if x in confirmed) * 2 >= len(ds):
                pos[c] += 1
        N, K = sum(tot.values()), sum(pos.values())
        print(f"\n{'='*64}\n{title}\n{'='*64}")
        print(f"{'category':<13}{'n':>7}{'confirmed':>11}{'rate':>8}"
              f"   {'95% CI':>13}")
        print("-" * 64)
        for c in sorted(tot, key=lambda k: -(pos[k] / max(tot[k], 1))):
            k, m = pos[c], tot[c]
            lo, hi = wilson(k, m)
            thin = "  <- thin" if m < a.min_n else ""
            print(f"{c:<13}{m:>7}{k:>11}{100*k/m:>7.2f}%"
                  f"   {100*lo:>5.2f}-{100*hi:<6.2f}{thin}")
        print("-" * 64)
        print(f"{'overall':<13}{N:>7}{K:>11}{100*K/N:>7.2f}%")
        use = [c for c in tot if tot[c] >= a.min_n]
        if len(use) > 1 and N:
            p0, chi, small = K / N, 0.0, 0
            for c in use:
                for o, e in ((pos[c], tot[c] * p0),
                             (tot[c] - pos[c], tot[c] * (1 - p0))):
                    if e < 5:
                        small += 1
                    if e > 0:
                        chi += (o - e) ** 2 / e
            df = len(use) - 1
            print(f"\nchi-square {chi:.2f}  df {df}  p {chi2_sf(chi, df):.4g}"
                  f"   ({len(use)} categories with n>={a.min_n})")
            if small:
                print(f"!! {small} expected counts < 5; collapse before testing")
        return tot, pos

    tot, pos = table(lambda d: d, "RATE BY CATEGORY, PER DOMAIN")
    table(brand, "RATE BY CATEGORY, PER OPERATOR")

    comp = Counter(cat[d] for d in confirmed if cat.get(d, "") not in NON_ANSWER)
    cn = sum(comp.values())
    print(f"\n{'='*64}\nCOMPOSITION OF THE {cn} CONFIRMED HIJACKERS\n{'='*64}")
    for c, k in comp.most_common():
        lo, hi = wilson(k, cn)
        print(f"{c:<13}{k:>6}{100*k/cn:>7.1f}%   "
              f"{100*lo:>5.1f}-{100*hi:<6.1f}")
    print("\nComposition and rate answer inverse questions. Only the rate")
    print("says whether a category hijacks more than others.")

    if a.out:
        with open(a.out, "w", newline="", encoding="utf-8") as f:
            w = csv.writer(f)
            w.writerow(["category", "testable", "confirmed", "rate_pct",
                        "ci_lo", "ci_hi", "share_of_confirmed_pct"])
            for c in sorted(tot, key=lambda k: -(pos[k] / max(tot[k], 1))):
                lo, hi = wilson(pos[c], tot[c])
                w.writerow([c, tot[c], pos[c], round(100*pos[c]/tot[c], 2),
                            round(100*lo, 2), round(100*hi, 2),
                            round(100*comp.get(c, 0)/cn, 2) if cn else 0])
        print(f"\nwrote {a.out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
