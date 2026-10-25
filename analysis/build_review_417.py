#!/usr/bin/env python3
"""
build_review_417.py -- prepare the hijacking positives for screenshot review.

WHAT IT MAKES

    review_input.csv   one row per domain, in the three columns review_app.py
                       needs (domain, page_type, step) plus context columns it
                       carries through to the output.
    screenshots/       a flat directory of symlinks to just the files those
                       rows need, because review_app.py takes a single
                       screenshots root and the real files live under eight
                       per-vantage directories.

ONE ROW PER DOMAIN, NOT PER PRESS

    A domain hijacking at six vantage points on both pages has twelve press
    records. Reviewing all of them would mean 1,950 rows to settle 417
    questions. We pick one vantage and one page per domain and show press 1,
    whose before/after pair is the decisive evidence: before is the target
    page, after is the referrer if the back button worked.

WHICH VANTAGE IS CHOSEN

    The one whose record carries the strongest mechanism evidence, so the
    reviewer sees the clearest instance of what the detector claims. That
    biases toward confirming, so the `tier` column is carried through: a
    verdict should be read against it, and OUTCOME_ONLY cases are the ones
    worth reading sceptically.

USAGE (on a VM)
    python3 build_review_417.py \\
        --adjudicated /opt/bbh/runs/categories/positives_adjudicated.csv \\
        --runroot     /opt/bbh/runs/bbh_compare \\
        --run         RUN_NAME \\
        --out         /var/tmp/review417
"""
from __future__ import annotations

import argparse
import csv
import os
import sys
from collections import Counter, defaultdict

STRENGTH = {"MECH_ACTIVE": 6, "MECH_PLANTED": 5, "MECH_REDIRECT": 4,
            "OUTCOME_ONLY": 3, "UNOBSERVABLE": 2, "CONTRADICTION": 1,
            "VOID_INSTRUMENT": 0}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--adjudicated", required=True)
    ap.add_argument("--runroot", required=True,
                    help="directory holding the per-vantage run directories")
    ap.add_argument("--run", default="RUN_NAME")
    ap.add_argument("--out", required=True)
    ap.add_argument("--copy", action="store_true",
                    help="copy files instead of symlinking")
    a = ap.parse_args()

    with open(a.adjudicated, newline="", encoding="utf-8-sig") as f:
        rows = list(csv.DictReader(f))
    if not rows:
        sys.exit("adjudicated file is empty")

    # strongest (vantage, page) per domain
    best = {}
    for r in rows:
        d = r["domain"]
        s = STRENGTH.get(r["tier"], 0)
        if d not in best or s > best[d][0]:
            best[d] = (s, r)

    shots = os.path.join(a.out, "screenshots")
    os.makedirs(shots, exist_ok=True)

    out_rows, linked, missing = [], 0, []
    for d, (_, r) in sorted(best.items()):
        van, page = r["vantage"], r["page"]
        src_dir = os.path.join(a.runroot, f"{a.run}_{van}",
                               "screenshots", "hijacking")
        safe = d.replace("/", "_")
        got = 0
        for step in (1, 2, 3):
            for side in ("before", "after"):
                fn = f"{safe}_{page}_backpress_{step}_{side}.png"
                src = os.path.join(src_dir, fn)
                if not os.path.exists(src):
                    continue
                dst = os.path.join(shots, fn)
                if not os.path.exists(dst):
                    try:
                        if a.copy:
                            import shutil
                            shutil.copy2(src, dst)
                        else:
                            os.symlink(src, dst)
                        linked += 1
                    except OSError as e:          # noqa: PERF203
                        print(f"  link failed {fn}: {e}")
                got += 1
        if got == 0:
            missing.append((d, van, page))
        out_rows.append({
            "domain": d, "page_type": page, "step": 1,
            "vantage": van, "tier": r["tier"],
            "mechanism": r.get("mechanism", ""),
            "press_reached": r.get("press_reached", ""),
            "n_vantages": sum(1 for x in rows if x["domain"] == d),
            "files_found": got,
            "evidence": (r.get("evidence", "") or "")[:120],
        })

    inp = os.path.join(a.out, "review_input.csv")
    with open(inp, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=list(out_rows[0]))
        w.writeheader()
        w.writerows(out_rows)

    print(f"domains to review   {len(out_rows)}")
    print(f"screenshot links    {linked}")
    print(f"no screenshots      {len(missing)}")
    if missing:
        print("  these have a verdict but no image, so they cannot be")
        print("  reviewed this way and must be counted separately:")
        for d, v, p in missing[:10]:
            print(f"    {d} ({v}, {p})")
        if len(missing) > 10:
            print(f"    ... and {len(missing)-10} more")
    t = Counter(r["tier"] for r in out_rows)
    print("\ntier of the record chosen for review:")
    for k, v in t.most_common():
        print(f"   {k:<18}{v:>5}")
    print(f"\nwrote {inp}")
    print(f"\nNow run:\n"
          f"  python3 review_app.py --disagreements {inp} \\\n"
          f"      --screenshots {shots} \\\n"
          f"      --out {os.path.join(a.out, 'reviewed.csv')} --port 5050\n"
          f"Then from Windows:\n"
          f"  ssh -L 5050:localhost:5050 -A <USER>@<JUMPHOST>\n"
          f"  ssh -L 5050:localhost:5050 root@<vm>\n"
          f"  open http://localhost:5050")
    return 0


if __name__ == "__main__":
    sys.exit(main())
