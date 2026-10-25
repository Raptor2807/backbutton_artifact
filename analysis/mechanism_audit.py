#!/usr/bin/env python3
"""
mechanism_audit.py -- rebuild Table 5 from the per-press record, and measure
the ambient signals against their benign base rate.

TWO THINGS THIS FIXES

 1. "No mechanism identified" is a residual, and a residual is only as good as
    the list of mechanisms it is left over from. A cross-document back press
    that lands the visitor on a page they never visited is Google's first
    listed symptom, but it is invisible to a detector token keyed on
    popstate-then-redirect. We count it explicitly, so the residual shrinks to
    cases where nothing was observed rather than cases nothing looked for.

 2. "Ambient" is an assertion unless the benign rate is shown. A signal
    present on 51.7% of hijacking pages means nothing on its own; it means
    something only beside the share of BENIGN pages carrying it. We report
    both and the ratio, so the reader can see which signals discriminate and
    which merely co-occur. A ratio near 1.0 is the definition of ambient.

COMPARABILITY

    Mechanism signals are computed over ALL recorded presses for both groups,
    not over "decisive" presses only. A benign page has no decisive press by
    construction, so restricting to those would compare a measured quantity
    against an empty set and manufacture a difference.

NULL IS NOT ZERO

    `*_delta_this_press` is null whenever `instr_same_document` is false,
    because the per-document counters reset. Nulls are counted as UNKNOWN per
    page and never as absence; the denominator for each signal is the number
    of pages where that signal was readable, and it is printed.

USAGE
    python3 -u mechanism_audit.py \\
        --glob '/opt/bbh/runs/RUN_NAME_*/chunk_*.jsonl' \\
        --out mechanism_audit.csv --exclude nigeria
"""
from __future__ import annotations

import argparse
import csv
import glob as globmod
import json
import os
import sys
from collections import Counter

PAGES = ("main", "article")


def vantage_of(run):
    for tag in ("usa_california", "usa_newyork", "southkorea", "singapore",
                "thailand", "vietnam", "nigeria", "germany", "sweden",
                "japan", "brazil", "india"):
        if tag in run:
            return tag
    return run


def verdict_of(ev, page):
    """-> (verdict, p) with p the press that reached the referrer, or None."""
    steps = ev.get(f"back_steps_debug_{page}") or []
    if not steps:
        return None, None
    if ev.get(f"{page}_back_failed"):
        return "HIJACKING", None
    req = ev.get(f"{page}_back_steps_required")
    if isinstance(req, int) and req >= 1:
        return ("BENIGN", 1) if req == 1 else ("HIJACKING", req)
    return None, None


def signals(ev, page, p):
    """-> dict signal -> True / False / None (None = not readable)

    `p` is the press that reached the referrer, or None if never reached.
    Press-level signals are read only from the DECISIVE presses, those before
    the referrer was reached. Reading them from all presses is wrong in a way
    that is easy to miss: on a benign page press 1 is itself a cross-document
    move to a different URL, so a redirect signal computed over all presses
    fires on 100% of benign pages and the mechanism looks ambient when it is
    not. A benign page has no decisive press, which is the correct answer: it
    never moved the visitor anywhere they had not been.
    """
    allsteps = ev.get(f"back_steps_debug_{page}") or []
    steps = allsteps if p is None else allsteps[:max(0, p - 1)]
    out = {k: False for k in
           ("same_page_trap", "push_on_press", "replace_on_press",
            "window_open_on_press", "redirect_on_back", "popstate_fired",
            "history_api_used_at_load", "entries_planted_at_load")}
    readable = {k: False for k in
                ("push_on_press", "replace_on_press", "window_open_on_press")}

    # popstate is a page-level ambient signal: read it from every press
    for s in allsteps:
        if s.get("popstate_fired_after_press"):
            out["popstate_fired"] = True

    for s in steps:
        same = s.get("instr_same_document")
        before, after = s.get("before_back_url"), s.get("after_back_url")
        if same and before == after and before is not None:
            out["same_page_trap"] = True
        if same is False and before and after and before != after:
            out["redirect_on_back"] = True
        for key, name in (("pushState_delta_this_press", "push_on_press"),
                          ("replaceState_delta_this_press", "replace_on_press"),
                          ("window_open_delta_this_press",
                           "window_open_on_press")):
            v = s.get(key)
            if v is not None:
                readable[name] = True
                if v > 0:
                    out[name] = True

    for k, ok in readable.items():
        if not ok and not out[k]:
            out[k] = None          # never readable -> unknown, not absent

    # ---- ambient signals, read at LOAD, before any back press ----
    #
    # NOT from pushState_count_{page}: that counter is per-document and is
    # read after the back chain, so on a benign page -- whose press 1 is a
    # cross-document move to the referrer -- it has already reset and reads 0
    # every time. Measured on this corpus it was 0 for 382 of 382 benign
    # pages, which is an artifact of when it is read, not a property of the
    # web, and it manufactures a 20x difference out of nothing.
    #
    # The *_after_load snapshot is taken before any press, so it survives.
    # history.state is non-null exactly when the page has called pushState or
    # replaceState; history_length above its baseline means entries were
    # planted at load. Baseline is 3 for the main page (blank, referrer,
    # target) and 4 for an article (blank, referrer, main, article).
    snaps = ev.get(f"snapshots_{page}") or []
    load = next((s for s in snaps
                 if s.get("phase") == f"{page}_after_load"), None)
    if load is None:
        out["history_api_used_at_load"] = None
        out["entries_planted_at_load"] = None
    else:
        # history_state arrives as a STRING: the literal 'null' when the page
        # never set one. `is not None` is true for that string and reports
        # 100.0% of 100,580 benign pages as History API users, which is the
        # shape of an artifact rather than a finding. Measured on this corpus,
        # 304 of 382 benign pages carry 'null'.
        #
        # This remains a LOWER BOUND on History API use: pushState(null, ...)
        # leaves the state null, so a page can use the API and still read
        # 'null' here. Report it as such, never as "did not use pushState".
        hstate = load.get("history_state")
        if hstate is None:
            out["history_api_used_at_load"] = None
        else:
            s = str(hstate).strip()
            out["history_api_used_at_load"] = s not in ("null", "None", "", "{}")
        hl = load.get("history_length")
        base = 3 if page == "main" else 4
        out["entries_planted_at_load"] = (hl > base if isinstance(hl, int)
                                          else None)
    return out


# popstate_fired is here, not in the ambient block: popstate only fires on a
# SAME-DOCUMENT traversal, and a benign page's press 1 is cross-document, so a
# benign page cannot fire it in the document being observed. Its benign rate is
# a structural zero, not a behavioural one.
PRESS_LEVEL = {"same_page_trap", "push_on_press", "replace_on_press",
               "redirect_on_back", "window_open_on_press", "popstate_fired"}
SIGNAL_ORDER = ["same_page_trap", "push_on_press", "replace_on_press",
                "redirect_on_back", "window_open_on_press",
                "history_api_used_at_load", "entries_planted_at_load",
                "popstate_fired"]
MECHANISMS = {"same_page_trap", "push_on_press", "replace_on_press",
              "redirect_on_back", "window_open_on_press"}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--glob", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--exclude", default="")
    a = ap.parse_args()

    files = sorted(globmod.glob(a.glob))
    if not files:
        sys.exit(f"no files matched {a.glob!r}")
    if any(os.path.basename(f) == "results.jsonl" for f in files):
        sys.exit("refusing to run: glob matched results.jsonl")
    drop = {v.strip() for v in a.exclude.split(",") if v.strip()}

    yes = {g: Counter() for g in ("HIJACKING", "BENIGN")}
    known = {g: Counter() for g in ("HIJACKING", "BENIGN")}
    tot = Counter()
    nomech = Counter()
    nomech_strict = Counter()
    rows = []
    seen = set()

    for n, fp in enumerate(files, 1):
        van = vantage_of(os.path.basename(os.path.dirname(fp)))
        if van in drop:
            continue
        with open(fp, encoding="utf-8", errors="replace") as f:
            for line in f:
                if '"back_steps_debug' not in line:
                    continue
                try:
                    r = json.loads(line)
                except Exception:          # noqa: BLE001
                    continue
                ev = r.get("evidence_log") or {}
                dom = (ev.get("domain") or "").lower()
                if not dom or (van, dom) in seen:
                    continue
                seen.add((van, dom))
                for page in PAGES:
                    v, p_reached = verdict_of(ev, page)
                    if v not in ("HIJACKING", "BENIGN"):
                        continue
                    tot[v] += 1
                    sg = signals(ev, page, p_reached)
                    for k, val in sg.items():
                        if val is None:
                            continue
                        known[v][k] += 1
                        if val:
                            yes[v][k] += 1
                    if v == "HIJACKING":
                        # residual under two definitions
                        loose = any(sg.get(m) for m in MECHANISMS)
                        strict = any(sg.get(m) for m in MECHANISMS
                                     if m != "redirect_on_back")
                        if not loose:
                            nomech["none"] += 1
                        if not strict:
                            nomech_strict["none"] += 1
                        rows.append({
                            "vantage": van, "domain": dom, "page": page,
                            **{k: ("" if sg[k] is None else int(sg[k]))
                               for k in SIGNAL_ORDER},
                        })
        if n % 40 == 0:
            print(f"  [{n}/{len(files)}] pages scored: {sum(tot.values())}")

    if not tot:
        sys.exit("no scored pages")

    with open(a.out, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=list(rows[0]))
        w.writeheader()
        w.writerows(rows)

    H, B = tot["HIJACKING"], tot["BENIGN"]
    print(f"\nscored pages: HIJACKING {H}, BENIGN {B}")
    print("\nSignal prevalence, hijacking vs benign. `n known` is the number")
    print("of pages where the signal was readable; blanks were unreadable and")
    print("are excluded from that signal's denominator, never counted as 0.\n")
    print(f"{'signal':<26}{'hij %':>8}{'ben %':>8}{'ratio':>8}"
          f"{'n known H':>11}{'n known B':>11}")
    print("-" * 72)
    for k in SIGNAL_ORDER:
        kh, kb = known["HIJACKING"][k], known["BENIGN"][k]
        if not kh:
            continue
        ph = 100 * yes["HIJACKING"][k] / kh
        pb = 100 * yes["BENIGN"][k] / kb if kb else float("nan")
        ratio = (ph / pb) if kb and pb > 0 else float("inf")
        if k in PRESS_LEVEL:
            tag = "  <- hijacking only*"
        elif kb and 0.8 <= ratio <= 1.25:
            tag = "  <- ambient"
        else:
            tag = ""
        print(f"{k:<26}{ph:>7.1f}%{pb:>7.1f}%{ratio:>8.2f}"
              f"{kh:>11}{kb:>11}{tag}")
    print("-" * 72)
    print("* Press-level signals are read only from presses BEFORE the")
    print("  referrer was reached. A benign page has none by construction, so")
    print("  these cannot have a benign base rate and no ratio is meaningful.")
    print("  Only the two page-level signals below the line support the")
    print("  ambient comparison -- which is what Table 5's ambient block is.")
    print("A ratio near 1.0 means the signal is as common on benign pages as")
    print("on hijacking ones: it co-occurs with the behavior but does not")
    print("identify it, and belongs in an `ambient` block rather than a")
    print("mechanism table.")

    print(f"\nNO MECHANISM IDENTIFIED, as a residual:")
    print(f"  counting redirect-on-back as a mechanism : "
          f"{nomech['none']:>5} of {H}  ({100*nomech['none']/H:.1f}%)")
    print(f"  NOT counting it (the stricter list)      : "
          f"{nomech_strict['none']:>5} of {H}  "
          f"({100*nomech_strict['none']/H:.1f}%)")
    print("\nThe gap between these two lines is the share of positives whose")
    print("only observable mechanism is that the back press moved the visitor")
    print("to a document they had never visited. Whether that counts as a")
    print("mechanism is a definitional choice and should be stated, not left")
    print("to the residual.")
    print(f"\nwrote {a.out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
