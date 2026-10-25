# Crawler

`SeleniumCrawler.py` performs one measurement per website: it loads a search
engine as the referrer, navigates to the target, waits for the URL to stop
changing, accepts a consent banner with BannerClick, scrolls, then presses Back
up to three times and records the state after each press.

For the internal page it examines the first 1,500 links on the homepage, keeps
only HTTP(S) links within the same registrable domain, excludes
homepage-equivalent URLs and login, registration, account, checkout, privacy
and contact pages, ranks the rest by path structure and anchor text, and
reaches the chosen page by clicking its anchor rather than navigating to its
URL, so that the site's own click handlers run.

## Classification

A page is **benign** when the first Back press reaches the referrer, and
**hijacking** when the referrer is reached only after further presses or not
within three. Before a verdict is assigned, the measurement is checked for
artifacts: connection failures, bot-verification pages, authentication
redirects, and incomplete client-side rendering. An internal-page measurement
is also excluded when the selected link opens a new window or tab while
backward navigation still works in the original tab. Measurements affected by
any of these are excluded rather than classified.

## Instrumentation

An observer is injected before page scripts run and records `pushState` and
`replaceState` calls, `popstate` events, window openings, and navigation
timing. Two details matter for reading the output:

The observer is initialised per document and its counters reset when a new
document loads, so a before-and-after comparison across a cross-document
navigation is meaningless. Each observer carries an identifier and deltas are
computed only when the identifier is unchanged; otherwise the comparison is
recorded as unavailable rather than as zero. Treating it as zero would turn
"unknown" into "no manipulation".

Methods such as `location.replace` cannot be intercepted by overriding the
`Location` prototype, because their properties are defined on the instance. The
crawler infers possible history-entry replacement from navigation timing and
history length rather than relying on interception alone.

## Validating it

Serve `../controls/` over HTTP and run the crawler against those pages. Each
has a known expected verdict, listed in the top-level README. The popup case
and the two `location.replace` cases are where a naive detector reports
hijacking, so run those first.
