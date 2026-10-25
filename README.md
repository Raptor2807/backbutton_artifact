# Detecting and Characterizing Back-Button Hijacking on the Web

Crawler, data, and analysis for the paper *Detecting and Characterizing
Back-Button Hijacking on the Web*.

A website exhibits back-button hijacking when pressing the browser's Back
button does not immediately return the visitor to the page they came from. We
define the behaviour by that outcome rather than by the History API calls a
page makes, press Back up to three times per page while recording what happens
after each press, and measure the CrUX top 10,000 from eight vantage points
(DE, SE, US-CA, US-NY, IN, JP, VN, KR) on both the homepage and an internal
page.

## Setup Guide

### Step 1: Install dependencies

```
./install.sh
```

Installs Chrome, chromedriver pinned to the matching version, and the Python
packages in `requirements.txt` into a virtual environment.

### Step 2: Install BannerClick

Consent banners block the interactions we need, so the crawler accepts them
with BannerClick. Clone it next to the crawler:

```
git clone https://github.com/bannerclick/bannerclick.git
```

See `crawler/VERSIONS.txt` for the revision we used.

### Step 3: Check the environment

```
google-chrome --version
chromedriver --version
```

The two must match on their major version. A mismatch produces a run where
every measurement fails, which is the single most common way this breaks.

## Run the Program

Steps 1-3 run at one vantage point. Repeat them for each vantage point.

### Step 1: Validate the instrumentation

Before measuring anything, confirm the observer reports what actually happened
rather than what the page claims. Serve the control pages and run the crawler
against them:

```
cd controls && python3 -m http.server 8080
```

| control | expected verdict |
|---|---|
| `a_clean.html`, `a_article.html` | benign, referrer reached on press 1 |
| `b_click_replace.html`, `b_article.html` | benign, `replaceState` adds no entry |
| `c_pushstate_trap.html` | hijacking, one entry planted at load |
| `d_target_replace.html` | hijacking via `location.replace` |
| `e_hook_liveness.html` | observer survives the document change |
| `f_popup_link.html` | benign: new tab opens, Back still works |
| `g_empty_shell.html` | untestable, no anchors |
| `h_lazy_render.html` | internal page found only after the render wait |
| `i_no_articles.html` | untestable, anchors present but none eligible |

The popup and `d_` cases are where a naive detector reports hijacking, so run
those first.

### Step 2: Measure a single domain

```
python3 crawler/SeleniumCrawler.py --domain example.com --referrer https://www.google.com
```

### Step 3: Measure the full sample

```
python3 crawler/SeleniumCrawler.py --input data/top10k_sample.csv --out results.jsonl
```

The sample is crawled in the order given by its `shuffle_position` column, the
same order at every vantage point.

Each output record carries, per page and per Back press: the URL before and
after the press, the press index at which the referrer was reached, the
history length before and after, whether `pushState` or `replaceState` was
called, whether `popstate` fired, whether a window opened, and the
classification with the reasons behind it. A measurement is *testable* when it
yields a hijacking or benign verdict rather than an artifact such as a bot
gate, an authentication redirect, or a rendering failure.

### Step 4: Verify the vantage point

A comparison between countries depends on each host being where you claim it
is. Read the live exit before the run and again at every chunk boundary, and
reject your own network as well as the wrong country. On our hosts the exit
address differed from the server named in the VPN configuration, so reading
the configuration establishes which server was dialled, not where traffic
emerges. `deploy/` holds the systemd units we used.

## Analysis

`analysis/` reproduces every aggregate in `data/aggregates/` from the crawl
output and the review output. Each script prints its working to stdout and
writes a CSV with `--out`.

### Step 1: Classify the flagged measurements by evidence

```
python3 analysis/adjudicate_positives.py \
  --glob '/opt/bbh/runs/RUN_*/chunk_*.jsonl' \
  --out positives_adjudicated.csv
```

Sorts each flagged page into an evidential tier, from a mechanism observed
during the press down to an outcome with no readable instrumentation. The tier
decides which vantage point a website is shown at during review, and it is
what lets the precision figure be reported per tier rather than pooled.

### Step 2: Build the review queue and review it

```
python3 analysis/build_review_417.py \
  --adjudicated positives_adjudicated.csv \
  --runroot /opt/bbh/runs --run RUN_NAME --out review/
```

One row per website, at the vantage point whose record carried the strongest
mechanism evidence, with symlinks to the before-and-after screenshots for
press 1. Reviewing at the strongest vantage biases toward confirming, which is
why the tier travels with the verdict and why the resulting precision is an
upper bound.

### Step 3: Score the review

```
python3 analysis/score_review.py \
  --reviewed review/reviewed.csv --input review/review_input.csv \
  --out per_domain.csv
```

Prints precision overall and per tier. `SKIP` means no screenshot was
recorded, so the website was never inspected; it is reported separately rather
than silently dropped or counted against the detector.

### Step 4: Mechanism prevalence (Table 5)

```
python3 analysis/mechanism_audit.py \
  --glob '/opt/bbh/runs/RUN_*/chunk_*.jsonl' \
  --out mechanism_audit.csv --exclude nigeria
```

Reports each signal on hijacking and benign pages with the denominator it was
readable on. Press-level signals are read only from presses before the
referrer was reached, so a benign page has none by construction and the script
marks those rows rather than printing a ratio against zero.

### Step 5: Category agreement, then rates (Section 4.3)

```
python3 analysis/validate_classification.py \
  --model top10k_classified.csv --hand category_mapping_coded.csv \
  --out model_categories.csv

python3 analysis/category_final.py \
  --categories model_categories.csv --reviewed per_domain.csv \
  --verdicts verdicts.csv --out data/aggregates/category_rates.csv
```

The first scores the classifier against the hand-coded set and reports
agreement per confidence band. The second computes the rate within each
category twice, once per domain and once per operator, where operators are
formed by `cluster_operators.py` on the registrable hostname label. Reporting
both matters: a single operator running many domains would otherwise look like
many independent sites.

### Step 6: Mechanism by category (Table 6)

```
python3 analysis/category_by_mechanism.py \
  --categories model_categories.csv --reviewed per_domain.csv \
  --out data/aggregates/category_by_mechanism.csv
```

### Step 7: Geographic consistency (Figure 4)

```
python3 analysis/geography_consistency.py \
  --verdicts verdicts.csv --reviewed per_domain.csv \
  --exclude nigeria --out data/aggregates/geography_consistency.csv
```

Counts only websites testable at every vantage point, so "hijacked at three of
eight" means three positives and five negatives rather than three positives
and five unknowns.

### A note on inputs

Steps 1, 2 and 4 read the per-chunk crawl output, and steps 2 and 3 read the
screenshots. Neither is in this repository: the records and screenshots
identify individual websites as hijacking, which the paper commits not to
publish. The scripts are included so the derivation is auditable and so the
numbers can be reproduced from a fresh crawl. Run step 3 onward against
`per_domain.csv` to reproduce `data/aggregates/` exactly.

## Data

| file | backs | unit |
|---|---|---|
| `data/top10k_sample.csv` | Section 3.4 | the 10,000-domain input sample |
| `data/aggregates/category_rates.csv` | Section 4.3 | domains and operators, per category |
| `data/aggregates/category_by_mechanism.csv` | Table 6 | websites, confirmed set |
| `data/aggregates/mechanism_prevalence.csv` | Table 5 | pages, denominator per row |
| `data/aggregates/geography_consistency.csv` | Figure 4 | websites measurable at all eight vantage points |

`data/SHA256SUMS` covers all of them.

### The three counts, which are easy to confuse

- **Flagged**: 417 websites the detector classified as hijacking.
- **Confirmed**: 364 of those, after reviewing every flagged website against
  the before-and-after screenshots recorded at each Back press. Every
  website-level result in Section 4 uses these 364.
- **Confirmed and classified**: 330 of the 364, the subset carrying a category.
  Section 4.3's per-category rates use these, because a domain with no
  category cannot enter a per-category rate.

`mechanism_prevalence.csv` has a `note` column because the denominators differ
by row. Press-level signals are read only from presses before the referrer was
reached, so a benign page has none by construction and those rows carry no
meaningful benign base rate. Only the two load-time signals support a
hijacking-versus-benign comparison.

## What is not included

Per-website verdicts and the screenshots behind the manual review. The paper
commits to reporting aggregate findings rather than publicly identifying
individual websites, since naming a site as hijacking carries reputational
consequences and Google's spam policy now covers the behaviour. Publishing
hashed domains would not help, because anyone holding the sample above could
reverse them. Both are available to reviewers on request through the
submission system.

## Done

Full pipeline:

1. Set up the environment and BannerClick
2. Validate the instrumentation against `controls/`
3. Verify the vantage point exit
4. Crawl `data/top10k_sample.csv`
5. Classify each measurement on the outcome of backward navigation
6. Review the flagged websites against the recorded screenshots
7. Analyse:
   - prevalence, and the homepage versus internal-page split
   - popularity rank
   - website category
   - mechanism
   - geographic consistency

## Licence

Data under `data/` derives from the Chrome User Experience Report and is
released under CC BY 4.0. Code and control pages are released under MIT. See
`LICENSE`.
