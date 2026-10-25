#!/usr/bin/env python3
"""
category_by_mechanism.py -- cross-tabulate confirmed hijackers by website
category and by the mechanism observed.

READ THE `mechanism` COLUMN WITH CARE

    In per_domain.csv `mechanism` is a 1:1 relabelling of `tier`, and three
    different tiers share the empty string:

        MECH_REDIRECT   -> 'redirect-on-back'
        MECH_PLANTED    -> 'planted@load'
        MECH_ACTIVE     -> 'pushState' | 'replaceState' | 'pushState+replaceState'
        OUTCOME_ONLY    -> ''
        UNOBSERVABLE    -> ''
        VOID_INSTRUMENT -> ''

    Grouping on `mechanism` therefore merges "the back press failed and we
    observed no mechanism" (OUTCOME_ONLY) with "we could not read the signal"
    (UNOBSERVABLE, VOID_INSTRUMENT). Those are a result and a missing value,
    and reporting them as one row overstates the first. We key off `tier` and
    keep them apart.

WHAT THE UNIT IS

    One row per domain, not per measurement. review_input.csv showed each
    domain at the vantage point whose record carried the STRONGEST mechanism
    evidence (build_review_417.py), so the mechanism here is the best case
    observed for that domain anywhere. A domain that redirects in one country
    and traps in another appears once, under whichever tier ranked higher.
    Shares are therefore not comparable with the per-page table in the paper.

THE TEST

    Five mechanism groups over five categories leaves 7 of 25 cells with an
    expected count below 5, so chi-square on that table is unreliable. We
    report the test on the four-group table, merging "not readable" into "no
    mechanism", where 3 cells fall below 5, and print both so the difference
    is visible.

USAGE
    python3 category_by_mechanism.py \
        --categories results/model_categories_v2.csv \
        --reviewed   results/per_domain.csv \
        --min-n 20 --out results/category_by_mechanism.csv
"""
from __future__ import annotations

import argparse
import collections
import csv
import math
import sys

GROUP = {
    "MECH_REDIRECT":   "Unexpected destination",
    "MECH_PLANTED":    "Entry planted at load",
    "MECH_ACTIVE":     "API call on back press",
    "OUTCOME_ONLY":    "No mechanism observed",
    "UNOBSERVABLE":    "Signal not readable",
    "VOID_INSTRUMENT": "Signal not readable",
}
ORDER = ["Unexpected destination", "Entry planted at load",
         "API call on back press", "No mechanism observed",
         "Signal not readable"]
COLLAPSE = {"Signal not readable": "No mechanism observed"}
# Confirmed websites the classifier declined. They are hijackers with mechanism
# evidence, so dropping them would make this table disagree with the figure in
# the paper, which totals the full confirmed set.
UNCLASSIFIED = "unclassified"
NON_ANSWER = {"unknown", "parked", "", "none", "n/a", "failed"}


def wilson(k, n, z=1.959963985):
    if n == 0:
        return (0.0, 0.0)
    p, z2 = k / n, z * z
    den = 1.0 + z2 / n
    cen = (p + z2 / (2 * n)) / den
    hw = z * math.sqrt(p * (1 - p) / n + z2 / (4 * n * n)) / den
    return (max(0.0, cen - hw), min(1.0, cen + hw))


def _g(a, x):
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
    return 1.0 if x <= 0 or df <= 0 else _g(df / 2.0, x / 2.0)


def _order(tab):
    """Categories by size, with the unclassified bucket pinned last.

    It is not a category and ranking it by size would put it mid-table, where
    a reader would read it as one.
    """
    named = sorted([k for k in tab if k != UNCLASSIFIED],
                   key=lambda k: -sum(tab[k].values()))
    return named + ([UNCLASSIFIED] if UNCLASSIFIED in tab else [])


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
    ap.add_argument("--min-n", type=int, default=20)
    ap.add_argument("--out", default=None)
    a = ap.parse_args()

    cat = {}
    with open(a.categories, newline="", encoding="utf-8-sig") as f:
        for r in csv.DictReader(f):
            c = col(r, "cf_mapped", "category", "label").lower()
            d = col(r, "domain", "host").lower()
            if d and c not in NON_ANSWER:
                cat[d] = c

    rows, no_cat, not_confirmed, unknown_mech = [], 0, 0, 0
    with open(a.reviewed, newline="", encoding="utf-8-sig") as f:
        for r in csv.DictReader(f):
            # SKIP means no screenshot was recorded, not that the reviewer
            # disagreed. The paper counts those with the confirmed set, so the
            # artifact must too or this table will not match it.
            if col(r, "verdict").upper() not in ("HIJACKING", "SKIP"):
                not_confirmed += 1
                continue
            d = col(r, "domain").lower()
            if d not in cat:
                no_cat += 1  # kept as its own row below, not dropped
            t = col(r, "tier")
            if t not in GROUP:
                unknown_mech += 1
                print(f"!! unrecognised tier {t!r} on {d}; add it to GROUP")
                continue
            rows.append((cat.get(d, UNCLASSIFIED), GROUP[t]))

    if not rows:
        sys.exit("nothing to tabulate -- check the domain columns match in form")
    print(f"confirmed hijackers tabulated {len(rows)}")
    print(f"  dropped, not confirmed      {not_confirmed}")
    print(f"  no model category, kept as 'unclassified'  {no_cat}")
    if unknown_mech:
        print(f"  dropped, unknown tier       {unknown_mech}  <- fix before using")

    tab = collections.defaultdict(collections.Counter)
    for c, m in rows:
        tab[c][m] += 1
    grand = collections.Counter(m for _, m in rows)
    N = len(rows)

    w = max(12, max(len(c) for c in tab) + 2)
    print(f"\n{'category':<{w}}" + "".join(f"{m.split()[0][:9]:>14}" for m in ORDER)
          + f"{'n':>6}")
    print("-" * (w + 14 * len(ORDER) + 6))
    for c in _order(tab):
        n = sum(tab[c].values())
        thin = "  <- thin" if n < a.min_n else ""
        print(f"{c:<{w}}" + "".join(f"{tab[c][m]:>7}{100*tab[c][m]/n:>6.1f}%"
                                    for m in ORDER) + f"{n:>6}{thin}")
    print("-" * (w + 14 * len(ORDER) + 6))
    print(f"{'all':<{w}}" + "".join(f"{grand[m]:>7}{100*grand[m]/N:>6.1f}%"
                                    for m in ORDER) + f"{N:>6}")

    # ---- independence, over the categories large enough to test ----
    keep = [c for c in tab
            if c != UNCLASSIFIED and sum(tab[c].values()) >= a.min_n]
    if len(keep) > 1:
        sub = sum(sum(tab[c].values()) for c in keep)
        g = collections.Counter()
        for c in keep:
            for m in ORDER:
                g[m] += tab[c][m]
        chi, small = 0.0, 0
        for c in keep:
            n = sum(tab[c].values())
            for m in ORDER:
                e = n * g[m] / sub
                if e < 5:
                    small += 1
                if e > 0:
                    chi += (tab[c][m] - e) ** 2 / e
        df = (len(keep) - 1) * (len(ORDER) - 1)
        print(f"\nchi-square {chi:.1f}  df {df}  p {chi2_sf(chi, df):.3g}"
              f"   ({len(keep)} categories with n>={a.min_n}, {sub} domains)")
        if small:
            print(f"!! {small} cells with expected count < 5 on the "
                  f"{len(ORDER)}-group table")
        cg = sorted({COLLAPSE.get(m, m) for m in ORDER},
                    key=lambda m: ORDER.index(m))
        cc_g = collections.Counter()
        for m in ORDER:
            cc_g[COLLAPSE.get(m, m)] += g[m]
        chi_c, small_c = 0.0, 0
        for c in keep:
            n = sum(tab[c].values())
            v = collections.Counter()
            for m in ORDER:
                v[COLLAPSE.get(m, m)] += tab[c][m]
            for m in cg:
                e = n * cc_g[m] / sub
                if e < 5:
                    small_c += 1
                chi_c += (v[m] - e) ** 2 / e
        df_c = (len(keep) - 1) * (len(cg) - 1)
        print(f"collapsed to {len(cg)} groups: chi-square {chi_c:.1f}  "
              f"df {df_c}  p {chi2_sf(chi_c, df_c):.3g}   "
              f"({small_c} cells with expected < 5)")
        print("Report the collapsed test; show the full split in the figure.")
        print("\nstandout cells vs the pooled share, Wilson 95%:")
        for c in keep:
            n = sum(tab[c].values())
            for m in ORDER:
                lo, hi = wilson(tab[c][m], n)
                glo, ghi = wilson(g[m], sub)
                if lo > ghi or hi < glo:
                    print(f"  {c:<11}{m:<26}{tab[c][m]}/{n} = {100*tab[c][m]/n:5.1f}%"
                          f"  [{100*lo:.1f}-{100*hi:.1f}]  vs {100*g[m]/sub:.1f}%"
                          f"  [{100*glo:.1f}-{100*ghi:.1f}]")

    # ---- pgfplots coordinates, in the figure's row order ----
    big = sorted(keep, key=lambda k: -sum(tab[k].values()))
    other = collections.Counter()
    for c in tab:
        if c not in keep:
            for m in ORDER:
                other[m] += tab[c][m]
    series = [(c.capitalize(), tab[c]) for c in big]
    if sum(other.values()):
        series.append(("Other", other))
    series.append(("All", grand))

    print("\n% ---- paste into the figure ----")
    print("symbolic y coords={"
          + ",".join(nm for nm, _ in reversed(series)) + "},")
    print("yticklabels={"
          + ",".join(f"{nm} ({sum(cc[m] for m in ORDER)})"
                     for nm, cc in reversed(series)) + "},")
    for m in ORDER:
        print(f"% {m}")
        pts = " ".join(f"({100*cc[m]/max(sum(cc[x] for x in ORDER),1):.1f},{nm})"
                       for nm, cc in series)
        print(f"\\addplot coordinates {{{pts}}};")

    if a.out:
        with open(a.out, "w", newline="", encoding="utf-8") as f:
            wr = csv.writer(f)
            wr.writerow(["category", "n"] + ORDER
                        + [m + " %" for m in ORDER])
            for c in _order(tab):
                n = sum(tab[c].values())
                wr.writerow([c, n] + [tab[c][m] for m in ORDER]
                            + [round(100 * tab[c][m] / n, 2) for m in ORDER])
            wr.writerow(["ALL", N] + [grand[m] for m in ORDER]
                        + [round(100 * grand[m] / N, 2) for m in ORDER])
        print(f"\nwrote {a.out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
