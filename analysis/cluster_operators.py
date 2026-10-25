#!/usr/bin/env python3
"""
cluster_operators.py -- group the §4.3 coding sheet by operator, so category
counts can be reported per domain AND per operator.

WHY

    The sheet is a list of 834 domains, and §4.3 treats each as one
    observation. But some rows are the same operator several times over, in two
    quite different ways:

      brand ccTLD families -- shopee.{co.id,com.br,com.my,co.th,ph,tw,vn},
        lidl.{cz,fr,ro,rs}, ebay.{de,es,fr,it}, daraz.{com.bd,com.np,lk,pk}.
        One company, localised. Their category is genuinely the same because it
        is genuinely the same business.

      operator sprays -- tvwiki19/22/23/24.net, sex-studentki.{live,sale,team},
        dropmms.{co,com,net}, 11xplay.{live,pink}, xhamster45.desi +
        id.xhamster.desi. Numbered or TLD-sprayed families. This is the pattern
        the project already catalogued as a genuine-hijacker signature:
        randomised domains sharing a prefix and title.

    Either way the rows are not independent observations of "a site in category
    X chose to hijack". A category cell built mostly from one operator's family
    is one decision counted many times, and any test over those cells overstates
    its own precision.

    Measured on the real 834-row sheet: 18 clustered operators, 33 redundant
    rows, so effective n is about 801 rather than 834 -- a 4% effect. Real, but
    small, and it will matter to the adult and entertainment cells rather than
    to the headline. Report it; do not inflate it.

WHAT TO DO WITH THIS

    Do NOT drop rows from the sheet and do NOT collapse before coding. The draw
    is pre-registered and rank-matched, and removing rows breaks both the
    blinding and the decile matching. Code all 834, then use the operator column
    emitted here to report a per-operator figure alongside the per-domain one.

BRAND EXTRACTION

    No public-suffix library on the fleet, so a known multi-part suffix list is
    stripped first, then a single-label suffix, and the brand is the label
    immediately left of the suffix with digits removed. This is approximate.
    It is right on everything in the current sheet, and it will be wrong on
    some suffix it has never seen -- check the output before trusting a group.

    An earlier awk version grouped on the first surviving label and produced
    ten false clusters (news, account, login, sport, healthy, direct, ...) by
    collapsing generic subdomain labels. Grouping must happen on the
    REGISTRABLE label, never on a subdomain.

USAGE
    python3 cluster_operators.py category_sheet.csv
    python3 cluster_operators.py category_sheet.csv --out operators.csv
"""
from __future__ import annotations

import argparse
import collections
import csv
import re
import sys

# Generic second-level labels that are part of the public suffix rather than a
# name anyone chose: gob.mx, gov.my, edu.co, go.id, com.ve, co.uk, jus.br,
# bank.in. A hardcoded list of full suffixes was tried first and had gaps, so
# eight unrelated Latin American government sites grouped as the operator
# "gob". The rule generalises: <generic>.<2-letter ccTLD> is a suffix.
GENERIC = {
    "ac", "bank", "bet", "biz", "co", "com", "edu", "firm", "gen", "go", "gob",
    "gov", "ind", "info", "int", "jus", "k12", "mil", "ne", "net", "nic", "or",
    "org", "pages", "res", "sch", "tv", "vlog", "web",
}
# "tv", "vlog", "pages" added 2026-10-17 from the 417-domain hand-coding pass:
# xvideos.tv.br grouped as operator "tv", xvideo.vlog.br as "vlog" and
# yonotv.pages.dev as "pages". .tv.br and .vlog.br are Brazilian second-level
# suffixes and pages.dev is Cloudflare Pages hosting -- none is a name anyone
# chose, and each glued unrelated sites into one operator.
# "bet" is here because bet.br and bet.ar are Brazil's and Argentina's
# regulated betting second-level domains: bet365.bet.br and cassino.bet.br are
# different licensed operators, and merging them would support the false claim
# that bet365 runs seven domains in this sample.

# Known limitation, not fixable from the hostname: mi.com (Xiaomi) and mi.tv
# (a Latin American TV listings site) are unrelated and still merge. Operator
# identity here is INFERRED, never verified -- say so wherever it is reported.


def brand(host: str) -> str:
    """The registrable label, trailing digits removed.

    Approximate -- there is no public-suffix library on the fleet. Digits are
    stripped only from the END of the label: tvwiki19 -> tvwiki and bet365 ->
    bet are what we want, but a leading-digit strip turned 1go.plus into the
    operator "go" and merged it with five unrelated go.id domains.
    """
    h = host.lower().strip().rstrip(".")
    parts = h.split(".")

    # 1. strip the public suffix: <generic>.<cc> if present, else one label
    if (len(parts) >= 3 and parts[-2] in GENERIC
            and len(parts[-1]) == 2 and parts[-1].isalpha()):
        parts = parts[:-2]
    elif len(parts) > 1:
        parts = parts[:-1]

    # 2. a generic label may remain (aprendes.edu.gob.sv -> aprendes.edu;
    #    disneyworld.disney.go.com -> disneyworld.disney.go)
    while len(parts) > 1 and parts[-1] in GENERIC:
        parts = parts[:-1]

    label = parts[-1] if parts else h
    return re.sub(r"\d+$", "", label) or label


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("sheet")
    ap.add_argument("--out", default=None,
                    help="write code_id,domain,operator for the analysis step")
    a = ap.parse_args()

    with open(a.sheet, newline="", encoding="utf-8-sig") as f:
        rows = list(csv.DictReader(f))
    if not rows or "domain" not in rows[0]:
        sys.exit(f"need a 'domain' column; found {list(rows[0]) if rows else 'nothing'}")

    groups = collections.defaultdict(list)
    for r in rows:
        groups[brand(r["domain"])].append(r["domain"])

    multi = {k: v for k, v in groups.items() if len(v) > 1}
    redundant = sum(len(v) - 1 for v in multi.values())

    print(f"rows                  {len(rows)}")
    print(f"distinct operators    {len(groups)}")
    print(f"clustered operators   {len(multi)}")
    print(f"redundant rows        {redundant}")
    print(f"effective n           {len(rows) - redundant}  "
          f"({100 * redundant / len(rows):.1f}% of the sheet is repeat operators)\n")
    for k, v in sorted(multi.items(), key=lambda kv: (-len(kv[1]), kv[0])):
        print(f"{k:<22}{len(v):>3}  {' '.join(sorted(v))}")

    if a.out:
        with open(a.out, "w", newline="", encoding="utf-8") as f:
            w = csv.writer(f)
            w.writerow(["code_id", "domain", "operator"])
            for r in rows:
                w.writerow([r.get("code_id", ""), r["domain"], brand(r["domain"])])
        print(f"\nwritten: {a.out}")
        print("Join on code_id at analysis time. Carries no group or rank, so it")
        print("does not un-blind the sheet and is safe to keep beside it.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
