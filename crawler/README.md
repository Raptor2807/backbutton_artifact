# Crawler

**Not yet populated.** Add `SeleniumCrawler.py` and its helpers here, then run
`bash ../scrub_crawler.sh .` from this directory and read the residual report.
See `../ANONYMITY_CHECKLIST.md` item 2.

Once added, this file should cover:

- how to run one measurement against a single domain and a local referrer
- the fields in an output record, in particular `domain_classification`,
  `main_page_classification`, the per-press `back_steps_debug_*` arrays, and
  the instrumentation counters
- which conditions route a measurement to an artifact class rather than to a
  verdict
- how to point the crawler at `../controls/` to reproduce the instrumentation
  validation
