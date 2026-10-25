#!/usr/bin/env python3
"""
adjudicate_positives.py -- re-derive every hijacking verdict from the recorded
per-press evidence, and sort the positives into evidential tiers.

WHAT THIS IS AND IS NOT

    It is NOT a precision measurement. The per-press record is the detector's
    own input, so agreement with it is guaranteed by construction and proves
    nothing about whether a site really hijacks. Only human review of the
    sites (or their screenshots) measures precision.

    What it does establish is which positives rest on a DIRECTLY OBSERVED act
    of history manipulation, and which rest only on the outcome. "The site
    called replaceState during the back press" is a different quality of
    evidence from "Back did not return to the referrer", and the paper should
    be able to say how many of each it has. This is also the check that
    catches verdicts contradicting their own recorded chain.

THE DECISIVE PRESSES

    Only the presses BEFORE the referrer was reached can carry the mechanism:
    a press that lands on the referrer is the chain working. For a page where
    the referrer was never reached, every press is decisive.

NULL IS NOT ZERO

    `*_delta_this_press` is null whenever `instr_same_document` is false,
    because the observer is per-document and its counters reset with the new
    document (see the instrumentation section of the paper). Treating that
    null as 0 would silently convert "unknown" into "no manipulation" -- the
    exact error the paper warns about. Nulls are carried through as
    UNAVAILABLE and never counted as absence.

TIERS, in priority order
    VOID_INSTRUMENT   a decisive press failed inside our apparatus
                      (back_press_succeeded false, or back_press_error set).
                      The measurement is ours, not the site's.
    CONTRADICTION     labelled hijacking yet the referrer was reached on press
                      1. Should be zero; a non-zero count is a bug, not a
                      finding.
    MECH_ACTIVE       pushState/replaceState/window.open observed DURING a
                      decisive press. The manipulation was caught happening.
    MECH_PLANTED      no write during the press, but an entry already existed
                      (pushState_count > 0) and press 1 did not move the
                      document. An entry was planted at load and absorbed the
                      press.
    MECH_REDIRECT     the press traversed to a DIFFERENT document at a
                      different URL which, being a decisive press, is not the
                      referrer: the visitor was sent somewhere they had never
                      been. The per-document counters reset on that traversal,
                      so the deltas read null and the specific API call is
                      unattributable -- but the movement itself is directly
                      observed, and must not be filed as "no evidence".
    OUTCOME_ONLY      Back did not return, and the mechanism signals were
                      readable but showed nothing. True under the definition,
                      but uncorroborated.
    UNOBSERVABLE      every mechanism signal on every decisive press was
                      unavailable, so we could not look. Kept apart from
                      OUTCOME_ONLY because "could not look" is not "looked and
                      saw nothing"; merging them would turn unknown into
                      evidence of absence.

USAGE
    python3 -u adjudicate_positives.py \\
        --glob '/opt/bbh/runs/bbh_compare/RUN_NAME_*/chunk_*.jsonl' \\
        --out positives_adjudicated.csv --exclude nigeria
"""
from __future__ import annotations

import argparse
import csv
import glob as globmod
import json
import os
import sys
from collections import Counter, defaultdict

PAGES = ("main", "article")

TIER_ORDER = ["VOID_INSTRUMENT", "CONTRADICTION", "MECH_ACTIVE",
              "MECH_PLANTED", "MECH_REDIRECT", "OUTCOME_ONLY", "UNOBSERVABLE"]
# strength for rolling a domain up from its pages: a domain with any
# mechanism-confirmed page is mechanism-confirmed
STRENGTH = {"MECH_ACTIVE": 6, "MECH_PLANTED": 5, "MECH_REDIRECT": 4,
            "OUTCOME_ONLY": 3, "UNOBSERVABLE": 2, "CONTRADICTION": 1,
            "VOID_INSTRUMENT": 0}
MECH_TIERS = ("MECH_ACTIVE", "MECH_PLANTED", "MECH_REDIRECT")


def vantage_of(run: str) -> str:
    for tag in ("usa_california", "usa_newyork", "southkorea", "singapore",
                "thailand", "vietnam", "nigeria", "germany", "sweden",
                "japan", "brazil", "india"):
        if tag in run:
            return tag
    return run


def page_verdict(ev, page):
    """(verdict, p) where p is the press that reached the referrer, or None.

    Uses the crawler's own derivation rather than re-deriving it, so that a
    disagreement between chain and verdict shows up as a CONTRADICTION rather
    than being silently papered over here.
    """
    failed = ev.get(f"{page}_back_failed")
    req = ev.get(f"{page}_back_steps_required")
    steps = ev.get(f"back_steps_debug_{page}") or []
    if not steps:
        return None, None                      # page never measured
    if failed:
        return "HIJACKING", None
    if isinstance(req, int) and req >= 1:
        return ("BENIGN", 1) if req == 1 else ("HIJACKING", req)
    return None, None


def adjudicate(ev, page, p):
    """-> (tier, mechanism, evidence string)"""
    steps = ev.get(f"back_steps_debug_{page}") or []
    decisive = steps if p is None else steps[:max(0, p - 1)]
    if not decisive:
        return "CONTRADICTION", "", "no decisive press before referrer"

    notes, mech = [], []
    observed = 0          # mechanism signals we could actually read
    redirect = None       # cross-document move to a non-referrer destination
    for s in decisive:
        i = s.get("step")
        if s.get("back_press_succeeded") is False or s.get("back_press_error"):
            return ("VOID_INSTRUMENT", "",
                    f"press {i}: {s.get('back_press_error') or 'press failed'}")
        same_doc = s.get("instr_same_document")
        for key, name in (("pushState_delta_this_press", "pushState"),
                          ("replaceState_delta_this_press", "replaceState"),
                          ("window_open_delta_this_press", "window.open")):
            v = s.get(key)
            if v is None:
                notes.append(f"press {i}: {name} UNAVAILABLE")
            else:
                observed += 1
                if v > 0:
                    mech.append(name)
                    notes.append(f"press {i}: {name} x{v} during press")
        before, after = s.get("before_back_url"), s.get("after_back_url")
        if same_doc and not mech:
            cnt = s.get("pushState_count_after_press")
            if before == after and isinstance(cnt, int) and cnt > 0:
                notes.append(f"press {i}: no movement, {cnt} entry(ies) "
                             f"already pushed")
                mech.append("planted@load")
        elif same_doc is False and before and after and before != after:
            # The press traversed to a DIFFERENT document at a different URL.
            # Because this is a decisive press, the referrer was not reached,
            # so the destination is somewhere the visitor never came from --
            # Google's first listed symptom. The per-document counters reset
            # on this traversal, which is why the deltas above are null: the
            # mechanism is unattributable to a specific API call, but the
            # movement itself is directly observed and is not "no evidence".
            if redirect is None:
                redirect = after
                notes.append(f"press {i}: cross-document move to {after[:80]}")

    if any(m != "planted@load" for m in mech):
        named = sorted({m for m in mech if m != "planted@load"})
        return "MECH_ACTIVE", "+".join(named), "; ".join(notes[:4])
    if "planted@load" in mech:
        return "MECH_PLANTED", "planted@load", "; ".join(notes[:4])
    if redirect is not None:
        return "MECH_REDIRECT", "redirect-on-back", "; ".join(notes[:4])
    if observed == 0:
        # every mechanism signal on every decisive press was unreadable.
        # "could not look" is not "looked and saw nothing": reporting this as
        # an absence of mechanism would turn unknown into evidence.
        return "UNOBSERVABLE", "", "; ".join(notes[:4])
    return "OUTCOME_ONLY", "", "; ".join(notes[:4]) or "no mechanism observed"


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--glob", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--exclude", default="",
                    help="comma list of vantages to drop, e.g. nigeria")
    a = ap.parse_args()

    files = sorted(globmod.glob(a.glob))
    if not files:
        sys.exit(f"no files matched {a.glob!r}")
    if any(os.path.basename(f) == "results.jsonl" for f in files):
        sys.exit("refusing to run: glob matched results.jsonl, which holds the "
                 "same records as chunk_*.jsonl and would double-count")
    drop = {v.strip() for v in a.exclude.split(",") if v.strip()}

    rows, seen = [], set()
    scanned = parsed = 0
    for n, fp in enumerate(files, 1):
        van = vantage_of(os.path.basename(os.path.dirname(fp)))
        if van in drop:
            continue
        with open(fp, encoding="utf-8", errors="replace") as f:
            for line in f:
                scanned += 1
                # pre-filter: records are ~194 KB, so only parse lines that
                # could possibly contain a positive
                if "HIJACKING" not in line:
                    continue
                try:
                    r = json.loads(line)
                except Exception:              # noqa: BLE001
                    continue
                parsed += 1
                ev = r.get("evidence_log") or {}
                dom = (ev.get("domain") or r.get("domain") or "").lower()
                if not dom or (van, dom) in seen:
                    continue
                seen.add((van, dom))
                for page in PAGES:
                    verdict, p = page_verdict(ev, page)
                    if verdict != "HIJACKING":
                        continue
                    tier, mech, why = adjudicate(ev, page, p)
                    rows.append({
                        "vantage": van, "domain": dom, "page": page,
                        "press_reached": "" if p is None else p,
                        "n_steps": len(ev.get(f"back_steps_debug_{page}") or []),
                        "tier": tier, "mechanism": mech, "evidence": why,
                    })
        if n % 20 == 0:
            print(f"  [{n}/{len(files)}] positives so far: {len(rows)}")

    if not rows:
        sys.exit("no hijacking pages found -- check the glob and --exclude")

    with open(a.out, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=list(rows[0]))
        w.writeheader()
        w.writerows(rows)

    # ---- report ----
    print(f"\nscanned {scanned} lines, parsed {parsed}")
    print(f"hijacking PAGE records: {len(rows)}")
    tiers = Counter(r["tier"] for r in rows)
    print(f"\n{'tier':<18}{'pages':>7}{'share':>8}")
    print("-" * 33)
    for t in TIER_ORDER:
        if tiers[t]:
            print(f"{t:<18}{tiers[t]:>7}{100*tiers[t]/len(rows):>7.1f}%")
    print("-" * 33)

    if tiers["CONTRADICTION"]:
        print(f"\n!! {tiers['CONTRADICTION']} verdicts contradict their own "
              f"recorded chain. This is a bug to fix, not a result to report.")
    if tiers["VOID_INSTRUMENT"]:
        print(f"!! {tiers['VOID_INSTRUMENT']} positives rest on a press that "
              f"failed inside our apparatus; these are ours, not the site's.")

    mechs = Counter(r["mechanism"] for r in rows if r["mechanism"])
    if mechs:
        print("\nmechanisms observed during a decisive press:")
        for m, c in mechs.most_common():
            print(f"   {m:<24}{c:>6}")

    pages = Counter(r["page"] for r in rows)
    print(f"\nby page: " + ", ".join(f"{k} {v}" for k, v in pages.items()))

    # ---- domain rollup: the 417 ----
    best = {}
    for r in rows:
        d = r["domain"]
        if d not in best or STRENGTH[r["tier"]] > STRENGTH[best[d]]:
            best[d] = r["tier"]
    dt = Counter(best.values())
    print(f"\ndistinct hijacking domains: {len(best)}")
    print(f"{'tier':<18}{'domains':>8}{'share':>8}")
    print("-" * 34)
    for t in TIER_ORDER:
        if dt[t]:
            print(f"{t:<18}{dt[t]:>8}{100*dt[t]/len(best):>7.1f}%")
    print("-" * 34)
    corrob = sum(dt[t] for t in MECH_TIERS)
    print(f"\nmechanism-corroborated: {corrob} of {len(best)} domains "
          f"({100*corrob/len(best):.1f}%)")
    print("\nNOTE: this is corroboration, not precision. The per-press record")
    print("is the detector's own input; only review of the sites or their")
    print("screenshots can establish whether these domains truly hijack.")
    print(f"\nwrote {a.out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
