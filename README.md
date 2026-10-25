# Detecting and Characterizing Back-Button Hijacking on the Web — artifact

Supporting material for the submission of the same title. A website exhibits
back-button hijacking when pressing the browser's Back button does not
immediately return the visitor to the page they came from. This repository
holds the domain sample we measured, the aggregate results behind every table
and figure in the paper, the control pages we used to validate our browser
instrumentation, and the crawler.

## Contents

```

data/top10k_sample.csv          the 10,000-domain sample, in the order crawled
data/aggregates/                the numbers behind each table and figure
data/SHA256SUMS                 checksums for everything under data/
controls/                       13 HTML pages for instrumentation validation
crawler/                        the measurement crawler (see below)
deploy/                         systemd units for a crawler vantage point
```

## The domain sample

`data/top10k_sample.csv` is the input to the large-scale measurement: the top
10,000 origins from the Chrome User Experience Report snapshot dated
2026-04-14, normalised to registrable hostnames and shuffled once with a fixed
seed. Every vantage point crawled this list in this order.

| column | meaning |
|---|---|
| `shuffle_position` | 1–10,000, the order in which the domain was crawled |
| `domain` | hostname, lowercased, scheme and `www.` prefix and port and path removed |
| `crux_rank` | the domain's rank in the CrUX snapshot before shuffling |

Normalisation discarded invalid entries and de-duplicated, so the 10,000 rows
are 10,000 distinct hostnames. To check you have the same list:

```
sha256sum data/top10k_sample.csv
# or, order-independent, over the domain column alone:
tail -n +2 data/top10k_sample.csv | cut -d, -f2 | sort -u | sha256sum
# ce5f61fd706c10f062c60b19a178d66de9f885bee661697ffd4312ce71429aa4
```

Of the 10,000, 9,090 were testable from at least one vantage point, meaning
the measurement produced a hijacking or benign verdict rather than an artifact
such as a bot gate, an authentication redirect or a rendering failure.

## The aggregate results

Each file corresponds to a specific table or figure. Percentages are rounded
to two decimals; counts are exact.

| file | backs | unit |
|---|---|---|
| `mechanism_prevalence.csv` | Table 5 | pages, with the denominator per row |
| `category_rates.csv` | Section 4.3 | domains and operators, per category |
| `category_by_mechanism.csv` | Table 6 | websites, confirmed set |
| `geography_consistency.csv` | Figure 4 | websites measurable at all eight vantage points |

Three quantities recur and are easy to confuse, so they are named explicitly in
every file:

- **Flagged**: 417 websites our detector classified as hijacking.
- **Confirmed**: 364 of those 417, after one author reviewed every flagged
  website against the before-and-after screenshots recorded at each Back press.
  Every website-level result in Section 4 uses these 364.
- **Confirmed and classified**: 330 of the 364, the subset carrying a category
  from the classifier. Section 4.3's per-category rates necessarily use these,
  because a domain with no category cannot enter a per-category rate.

`mechanism_prevalence.csv` carries a `note` column because the denominators
differ by row and the rows are not all mutually comparable. Press-level signals
are read only from presses before the referrer was reached, so a benign page has
none by construction and those rows have no meaningful benign base rate. Only
the two load-time signals support a hijacking-versus-benign comparison.

## What is not included

Per-website verdicts. The paper commits to reporting aggregate findings rather
than publicly identifying individual websites, since naming a site as hijacking
carries reputational consequences and Google's spam policy now covers the
behaviour. Publishing the verdicts with hashed domains would not help: anyone
holding the input list above could reverse the hashes. We will share
per-website data with reviewers or other researchers on request through the
submission system.

Also absent: screenshots, which are the evidence behind the manual review and
likewise identify individual sites, and VPN credentials.

## Instrumentation controls

`controls/` holds 13 static pages with known behaviour, used to check that the
injected observer reports what actually happened rather than what the page
claims. Serve the directory over HTTP and point the crawler at `ref.html` as
the referrer.

| page | expected behaviour |
|---|---|
| `a_clean.html`, `a_article.html` | benign: one Back press returns to the referrer |
| `b_click_replace.html`, `b_article.html` | `replaceState` on click, no extra history entry |
| `c_pushstate_trap.html` | one extra entry pushed at load: the same-page trap |
| `d_target_replace.html`, `d_target_replace_landed.html` | `location.replace` on the target, which cannot be intercepted by overriding the prototype |
| `e_hook_liveness.html` | the observer survives a document change |
| `f_popup_link.html` | opens a new tab while Back still works in the original |
| `g_empty_shell.html` | no anchors at all |
| `h_lazy_render.html` | anchors appear only after a delay |
| `i_no_articles.html` | anchors present, none eligible as an internal page |

The popup and the two `d_` pages are the cases where a naive detector reports
hijacking, so they are the ones worth running first.

## The crawler

The crawler drives Chrome through Selenium: it loads a search engine, navigates
to the target, waits for the URL to settle, accepts a consent banner via
BannerClick, scrolls, then presses Back up to three times and records the
outcome after each press. For internal pages it examines the first 1,500 links
on the homepage, excludes login, registration, checkout, privacy and contact
pages, ranks the rest by path structure and anchor text, and reaches the chosen
page by clicking its anchor rather than navigating to its URL, so that
click handlers run.

See `crawler/README.md` for how to run a single measurement and what each field
in the output record means.

## Environment

Measurements ran on Ubuntu with Chrome and chromedriver pinned to one version
fleet-wide, four concurrent browser instances per vantage point, and roughly 27
hours per vantage point for the full 10,000 domains. Eight vantage points were
used, each reached through a commercial VPN provider, with the live exit address
and its network organisation verified before the run and again at every chunk
boundary. Exact versions are recorded in `crawler/VERSIONS.txt`.

## Licence

Data under `data/` is derived from the Chrome User Experience Report and is
released under CC BY 4.0. Code and control pages are released under the MIT
licence. See `LICENSE`.
