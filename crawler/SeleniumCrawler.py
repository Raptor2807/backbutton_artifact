#!/usr/bin/env python3
"""
Back-Button Hijacking Crawler
Detects whether websites trap/redirect users when pressing the browser Back button.

Usage:
    python crawler.py --input domains.csv --output results.json --headless
"""
###########################################################################
# VM specific changes to the original are Change 1 — add chromedriver_autoinstaller right after the existing imports at the top of the file,
# Change 2 — remove experimental_options in create_driver() because they cause Chrome to crash on Linux when combined with headless mode.
"""
The key additions for Linux VM are --disable-gpu and --remote-debugging-port=0.
The excludeSwitches and useAutomationExtension experimental options are removed because they are Windows-specific and break headless Chrome on Linux.
"""
##########################################################################
import chromedriver_autoinstaller
chromedriver_autoinstaller.install()
"""--------------------------------------------------------------------------"""
import argparse
import csv
import json
import os
import random
import re
import time
from datetime import datetime, timezone
from urllib.parse import urlparse, urlunparse, parse_qsl, urlencode, parse_qs
from concurrent.futures import ProcessPoolExecutor, as_completed
from screenshot_capture import save_backpress_screenshot, finalise_screenshots

from selenium import webdriver
from selenium.common.exceptions import (
    TimeoutException, WebDriverException, StaleElementReferenceException
)
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

DEFAULT_MAX_BACK_STEPS = 3
DEFAULT_CONTROLLED_REFERRER_URL = "https://www.google.com/"
DEFAULT_PAGE_LOAD_TIMEOUT = 30

DOMAIN_COLUMN_CANDIDATES = [
    "domain", "domains", "host", "hostname", "url",
    "origin", "site", "website", "target_url",
]

# ---------------------------------------------------------------------------
# Cookie banner config
# ---------------------------------------------------------------------------

COOKIE_ACCEPT_TEXTS = {
    "accept", "accept all", "accept cookies", "allow all", "agree",
    "i agree", "ok", "got it", "continue", "tout accepter", "j'accepte",
    "alle akzeptieren", "akzeptieren", "aceptar", "aceitar",
    "zustimmen", "동의", "모두 동의", "확인",
}

COOKIE_REJECT_TEXTS = {
    "book flight", "login", "register", "cart", "view all", "read more",
    "more information", "facebook", "instagram", "whatsapp", "subscribe",
    "bespoke", "history", "products", "shop",
}

COOKIE_CONTEXT_TOKENS = {
    "cookie", "consent", "privacy", "gdpr", "cmp",
    "onetrust", "didomi", "quantcast", "accept", "agree",
}

# ---------------------------------------------------------------------------
# Article link scoring
# ---------------------------------------------------------------------------

ARTICLE_POSITIVE_PATTERNS = [
    "/article/", "/articles/", "/news/", "/blog/",
    "/story/", "/post/", "/product/", ".html",
]

ARTICLE_NEGATIVE_PATTERNS = [
    "/cart", "/checkout", "/login", "/register", "/account",
    "/search", "/collections", "/category", "/tag",
    "/privacy", "/terms", "/contact", "/about",
]

ARTICLE_SKIP_TEXTS = {"view all", "more", "more information", "continue shopping"}

ARTICLE_BAD_LINK_PATTERNS = [
    "login", "signin", "sign-in", "register", "account", "cart",
    "checkout", "privacy", "terms", "contact", "about",
    "youtube.com", "youtu.be", "facebook.com", "instagram.com",
    "twitter.com", "x.com", "linkedin.com",
    ".pdf", ".jpg", ".jpeg", ".png", ".zip",
]

# ---------------------------------------------------------------------------
# Ad evidence constants
# ---------------------------------------------------------------------------

AD_KEYWORD_LIST = [
    "ad", "ads", "googleads", "googlesyndication", "doubleclick",
    "adservice", "adserver", "adform", "taboola", "outbrain",
    "mgid", "revcontent", "criteo", "pubmatic", "openx",
    "rubicon", "smartadserver", "yieldmo", "media.net",
    "popads", "propeller", "exoclick", "trafficjunky",
    "onclick", "vignette", "pagead", "adtech", "advertising",
    "banner", "sponsor", "interstitial", "popup", "pop-up",
]

AD_ANCHOR_TEXT_KEYWORDS = [
    "ad", "ads", "advertisement", "sponsored", "sponsor",
    "promoted", "recommended", "offer", "reward", "bonus",
    "win", "prize", "casino", "lottery", "install", "download",
    "extension", "click here", "continue", "open", "watch now", "play now",
]

AD_TEXT_ELEMENT_KEYWORDS = [
    "ad", "ads", "advertisement", "sponsored", "promoted",
    "sponsor", "reward", "offer", "pop", "popup", "banner",
    "vignette", "interstitial", "modal",
]

TRACKING_PARAM_NAMES = {
    "cmp", "campaign", "campaign_id", "traff", "traffic", "icid",
    "clickid", "subid", "sub_id", "aff", "affiliate", "pub", "publisher",
    "zone", "zoneid", "placement", "creative", "creative_id",
    "gclid", "fbclid", "msclkid",
    "utm_source", "utm_medium", "utm_campaign", "utm_content", "utm_term",
    "t1", "t2", "t3", "t4", "t14", "et2", "psid", "key", "token",
}

ADULT_TERMS = {"porn", "sex", "xxx", "hotindian", "adult", "nsfw", "nude", "hentai"}
POPUP_TERMS = {"method=pop", "pop", "popup", "onclick", "out.php"}
REWARD_TERMS = {"reward", "bonus", "sweep", "prize", "winner", "customer-reward"}
EXTENSION_TERMS = {"adblocker", "extension", "ultraplus", "install", "download"}
NEWS_TERMS = {"news", "post", "times", "daily", "nachrichten", "actualite", "noticia"}

# ---------------------------------------------------------------------------
# Instrumentation JS
# FIX: Single addEventListener wrap, internal listener excluded via __bbh_internal flag.
# Tracks website_popstate_listener_count (int) not a boolean.
# ---------------------------------------------------------------------------

INSTRUMENTATION_JS = r"""
(function () {
    if (window.__bbh_installed) return;
    window.__bbh_installed = true;

    const origAdd = EventTarget.prototype.addEventListener;
    const origPush = history.pushState;
    const origReplace = history.replaceState;
    const origAssign = Location.prototype.assign;
    const origLocReplace = Location.prototype.replace;

    window.__bbh = {
        events: [],
        pushState_count: 0,
        replaceState_count: 0,
        website_popstate_listener_count: 0,
        popstate_fired: false,
        popstate_fired_at: null,
        redirect_after_popstate: false,
        location_assign_count: 0,
        location_replace_count: 0,
        last_popstate_url: null,
        internal_listener_count: 0,
        new_window_observed: false,
        new_window_count: 0,
        window_open_count: 0
    };

    function safeStringify(v) {
        try { return JSON.stringify(v); } catch (e) { return String(v); }
    }

    function log(type, data) {
        try {
            window.__bbh.events.push(Object.assign(
                { type, ts: Date.now(), url: location.href, history_length: history.length },
                data
            ));
        } catch (e) {}
    }

    function isInternal(l) { return l && l.__bbh_internal === true; }

    // Single wrap — FIX: previously wrapped twice, causing double-counting
    EventTarget.prototype.addEventListener = function (type, listener, options) {
        if (type === "popstate") {
            if (isInternal(listener)) {
                window.__bbh.internal_listener_count += 1;
            } else {
                window.__bbh.website_popstate_listener_count += 1;
                log("website_popstate_listener_registered", {
                    listener_snippet: listener ? String(listener).slice(0, 500) : null
                });
            }
        }
        return origAdd.call(this, type, listener, options);
    };

    history.pushState = function (state, title, url) {
        const b = location.href, h = history.length;
        const r = origPush.apply(this, arguments);
        window.__bbh.pushState_count += 1;
        log("pushState", {
            before_url: b, after_url: location.href,
            history_length_before: h, history_length_after: history.length,
            state_argument: safeStringify(state), url_argument: safeStringify(url)
        });
        return r;
    };

    history.replaceState = function (state, title, url) {
        const b = location.href, h = history.length;
        const r = origReplace.apply(this, arguments);
        window.__bbh.replaceState_count += 1;
        log("replaceState", {
            before_url: b, after_url: location.href,
            history_length_before: h, history_length_after: history.length,
            state_argument: safeStringify(state), url_argument: safeStringify(url)
        });
        return r;
    };

    // Internal popstate listener — flagged so wrapper above ignores it
    const internalPop = function (event) {
        window.__bbh.popstate_fired = true;
        window.__bbh.popstate_fired_at = Date.now();
        window.__bbh.last_popstate_url = location.href;
        log("popstate_fired", {
            event_state: safeStringify(event.state),
            url_at_event: location.href
        });
    };
    internalPop.__bbh_internal = true;
    origAdd.call(window, "popstate", internalPop, true);

    Location.prototype.assign = function (url) {
        window.__bbh.location_assign_count += 1;
        if (window.__bbh.popstate_fired) window.__bbh.redirect_after_popstate = true;
        log("location_assign", { target_url: String(url) });
        return origAssign.call(this, url);
    };

    Location.prototype.replace = function (url) {
        window.__bbh.location_replace_count += 1;
        if (window.__bbh.popstate_fired) window.__bbh.redirect_after_popstate = true;
        log("location_replace", { target_url: String(url) });
        return origLocReplace.call(this, url);
    };

    const origWindowOpen = window.open;
    window.open = function () {
        window.__bbh.window_open_count += 1;
        window.__bbh.new_window_observed = true;
        log("window_open", { target_url: arguments[0] ? String(arguments[0]) : null });
        return origWindowOpen.apply(window, arguments);
    };

    window.addEventListener("beforeunload", function () {
        if (window.__bbh.popstate_fired) {
            window.__bbh.redirect_after_popstate = true;
            log("redirect_after_popstate_beforeunload", {});
        }
    });
})();
"""

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def utc_now_iso():
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()

def sleep_ms(ms):
    time.sleep(ms / 1000)

def normalize_url(url):
    if not url:
        return ""
    parsed = urlparse(url)
    scheme = parsed.scheme.lower()
    host = parsed.netloc.lower()
    if host.startswith("www."):
        host = host[4:]
    path = parsed.path or "/"
    if path != "/" and path.endswith("/"):
        path = path[:-1]
    query = parse_qsl(parsed.query, keep_blank_values=True)
    ignored = ("utm_", "fbclid", "gclid", "msclkid", "yclid")
    filtered = [(k, v) for k, v in query
                if not any(k.lower().startswith(p) for p in ignored)]
    return urlunparse((scheme, host, path, "", urlencode(sorted(filtered)), ""))

def urls_match(a, b):
    return normalize_url(a) == normalize_url(b)

def same_domain(url1, url2):
    try:
        h1 = urlparse(url1).netloc.lower().replace("www.", "")
        h2 = urlparse(url2).netloc.lower().replace("www.", "")
        return h1 == h2
    except Exception:
        return False

def get_domain(url):
    try:
        return urlparse(url).netloc.lower().replace("www.", "")
    except Exception:
        return ""

def domain_to_url(domain):
    domain = str(domain).strip()
    if domain.startswith(("http://", "https://")):
        return domain
    return f"https://{domain}"

def normalize_text(text):
    return " ".join((text or "").lower().split()).strip()

# ---------------------------------------------------------------------------
# Ad evidence helpers
# ---------------------------------------------------------------------------

def safe_parse_url(url):
    """Parse a URL safely, returning a ParseResult or None on failure."""
    try:
        if not url or not isinstance(url, str):
            return None
        if not url.startswith(("http://", "https://", "//")):
            return None
        return urlparse(url)
    except Exception:
        return None

def get_registrable_domain(url):
    """
    Return the registrable domain (e.g. 'example.com' from 'sub.example.com').
    Simple implementation — splits on last two parts.
    """
    try:
        host = urlparse(url).netloc.lower().replace("www.", "")
        host = host.split(":")[0]
        parts = host.split(".")
        if len(parts) >= 2:
            return ".".join(parts[-2:])
        return host
    except Exception:
        return ""

def extract_query_params(url):
    """Return all query parameters as a dict. Safe against malformed URLs."""
    try:
        parsed = urlparse(url)
        if not parsed.query:
            return {}
        params = {}
        for k, v in parse_qsl(parsed.query, keep_blank_values=True):
            params[k] = v
        return params
    except Exception:
        return {}

def extract_tracking_params(url):
    """Return only known tracking parameters from a URL."""
    try:
        all_params = extract_query_params(url)
        tracking = {}
        for k, v in all_params.items():
            kl = k.lower()
            if kl in TRACKING_PARAM_NAMES or kl.startswith("utm_"):
                tracking[k] = v
        return tracking
    except Exception:
        return {}

def match_ad_keywords(value):
    """Return list of matched ad keywords in a string value."""
    if not value:
        return []
    val = str(value).lower()
    return [kw for kw in AD_KEYWORD_LIST if kw in val]

def classify_ad_url(url, page_title=None, original_domain=None, expected_referrer_domain=None):
    """
    Classify a URL into an ad/redirect category.

    Returns one of:
      google_vignette, popup_redirect, reward_sweepstakes,
      browser_extension_install, adult_ad_redirect, tracker_redirect,
      fake_news_advertorial, unknown_off_domain, same_domain,
      expected_referrer, benign
    """
    try:
        if not url:
            return "unknown"

        url_lower   = url.lower()
        parsed      = safe_parse_url(url)
        domain      = get_domain(url) if parsed else ""
        title_lower = (page_title or "").lower()
        q_params    = extract_query_params(url)
        q_lower     = " ".join(f"{k}={v}" for k, v in q_params.items()).lower()
        full_lower  = f"{url_lower} {q_lower} {domain} {title_lower}"

        # 1. Google vignette
        if "#google_vignette" in url_lower:
            return "google_vignette"

        # 2. Browser extension install
        if any(t in full_lower for t in EXTENSION_TERMS):
            return "browser_extension_install"

        # 3. Adult ad redirect
        if any(t in full_lower for t in ADULT_TERMS):
            return "adult_ad_redirect"

        # 4. Reward / sweepstakes
        if any(t in full_lower for t in REWARD_TERMS):
            return "reward_sweepstakes"

        # 5. Popup redirect
        if any(t in q_lower for t in POPUP_TERMS) or "method=pop" in q_lower:
            return "popup_redirect"

        # 6. Tracker redirect — has tracking params
        tracking = extract_tracking_params(url)
        if tracking:
            # 6a. Fake news advertorial — off-domain + news-like title/domain + tracking
            if original_domain and domain and domain != original_domain:
                if any(t in full_lower for t in NEWS_TERMS):
                    return "fake_news_advertorial"
            return "tracker_redirect"

        # 7. Same registrable domain as original
        if original_domain:
            orig_reg = get_registrable_domain(f"https://{original_domain}")
            url_reg  = get_registrable_domain(url)
            if orig_reg and url_reg and orig_reg == url_reg:
                return "same_domain"

        # 8. Expected referrer
        if expected_referrer_domain and domain:
            ref_reg = get_registrable_domain(f"https://{expected_referrer_domain}")
            url_reg = get_registrable_domain(url)
            if ref_reg and url_reg and ref_reg == url_reg:
                return "expected_referrer"

        # 9. Off-domain from original
        if original_domain and domain and domain != original_domain:
            if expected_referrer_domain and domain == expected_referrer_domain:
                return "expected_referrer"
            return "unknown_off_domain"

        return "benign"

    except Exception:
        return "unknown"

def collect_dom_ad_evidence(driver, original_url, expected_referrer_url):
    """
    Collect structured ad evidence from the current page DOM using Selenium.
    Returns an ad_evidence dict. Never raises — returns error field on failure.
    """
    original_domain           = get_domain(original_url)
    expected_referrer_domain  = get_domain(expected_referrer_url)

    try:
        current_url   = driver.current_url
        current_domain = get_domain(current_url)
        url_cls       = classify_ad_url(
            current_url,
            page_title=None,
            original_domain=original_domain,
            expected_referrer_domain=expected_referrer_domain,
        )
        q_params      = extract_query_params(current_url)
        tracking      = extract_tracking_params(current_url)
        signals       = []

        # ── Collect iframes ───────────────────────────────────────────────
        iframe_srcs = []
        try:
            raw = driver.execute_script("""
                return Array.from(document.querySelectorAll('iframe')).map(f => ({
                    src: f.getAttribute('src') || f.getAttribute('data-src') || '',
                    id:  f.getAttribute('id')  || '',
                    cls: f.getAttribute('class') || ''
                }));
            """) or []
            for item in raw[:50]:
                src = (item.get("src") or "").strip()
                if not src:
                    continue
                src_domain    = get_domain(src) if src.startswith("http") else ""
                matched       = match_ad_keywords(src + " " + src_domain)
                is_cross      = bool(src_domain and src_domain != current_domain)
                is_ad         = bool(matched)
                entry = {
                    "src":            src,
                    "domain":         src_domain,
                    "is_cross_origin": is_cross,
                    "is_ad_like":     is_ad,
                    "matched_keywords": matched,
                }
                iframe_srcs.append(entry)
                if is_ad:
                    signals.append(f"ad_iframe_detected:{src_domain or src[:60]}")
        except Exception:
            pass

        # ── Collect scripts ───────────────────────────────────────────────
        script_srcs = []
        try:
            raw = driver.execute_script("""
                return Array.from(document.querySelectorAll('script[src]')).map(s => ({
                    src: s.getAttribute('src') || ''
                }));
            """) or []
            for item in raw[:50]:
                src = (item.get("src") or "").strip()
                if not src:
                    continue
                src_domain  = get_domain(src) if src.startswith("http") else ""
                matched     = match_ad_keywords(src + " " + src_domain)
                is_cross    = bool(src_domain and src_domain != current_domain)
                is_ad       = bool(matched)
                entry = {
                    "src":            src,
                    "domain":         src_domain,
                    "is_cross_origin": is_cross,
                    "is_ad_like":     is_ad,
                    "matched_keywords": matched,
                }
                script_srcs.append(entry)
                if is_ad and src_domain:
                    signals.append(f"ad_script_detected:{src_domain}")
        except Exception:
            pass

        # ── Collect ad-like anchors ───────────────────────────────────────
        anchor_hrefs = []
        try:
            raw = driver.execute_script("""
                return Array.from(document.querySelectorAll('a[href]')).slice(0, 200).map(a => ({
                    href: a.getAttribute('href') || '',
                    text: (a.innerText || a.textContent || '').slice(0, 100).trim()
                }));
            """) or []
            for item in raw:
                href = (item.get("href") or "").strip()
                text = (item.get("text") or "").strip().lower()
                if not href:
                    continue
                href_domain = get_domain(href) if href.startswith("http") else ""
                url_matched  = match_ad_keywords(href + " " + href_domain)
                text_matched = [kw for kw in AD_ANCHOR_TEXT_KEYWORDS if kw in text]
                all_matched  = list(set(url_matched + text_matched))
                is_cross     = bool(href_domain and href_domain != current_domain)
                is_ad        = bool(all_matched)
                if is_ad or is_cross:
                    anchor_hrefs.append({
                        "href":            href,
                        "domain":          href_domain,
                        "text":            item.get("text", "")[:100],
                        "is_cross_origin": is_cross,
                        "is_ad_like":      is_ad,
                        "matched_keywords": all_matched,
                    })
                if len(anchor_hrefs) >= 50:
                    break
        except Exception:
            pass

        # ── Visible ad text candidates ────────────────────────────────────
        visible_ad_texts = []
        try:
            kw_js = json.dumps(AD_TEXT_ELEMENT_KEYWORDS)
            raw = driver.execute_script(f"""
                const keywords = {kw_js};
                const results = [];
                const all = document.querySelectorAll('*');
                for (let el of all) {{
                    if (results.length >= 30) break;
                    const id  = (el.getAttribute('id') || '').toLowerCase();
                    const cls = (el.getAttribute('class') || '').toLowerCase();
                    const aria = (el.getAttribute('aria-label') || '').toLowerCase();
                    const tag = el.tagName.toLowerCase();
                    const combined = id + ' ' + cls + ' ' + aria;
                    let reason = null;
                    for (let kw of keywords) {{
                        if (combined.includes(kw)) {{ reason = 'matched class/id keyword: ' + kw; break; }}
                    }}
                    if (!reason) continue;
                    const text = (el.innerText || el.textContent || '').trim().slice(0, 200);
                    if (!text) continue;
                    results.push({{ text, tag, id: el.getAttribute('id') || '', cls: el.getAttribute('class') || '', reason }});
                }}
                return results;
            """) or []
            visible_ad_texts = raw[:30]
        except Exception:
            pass

        # ── Page title for classification ─────────────────────────────────
        page_title = ""
        try:
            page_title = driver.title or ""
        except Exception:
            pass

        # Re-classify with title available
        url_cls = classify_ad_url(
            current_url,
            page_title=page_title,
            original_domain=original_domain,
            expected_referrer_domain=expected_referrer_domain,
        )

        # ── Collect ad network domains ────────────────────────────────────
        ad_network_domains = list({
            e["domain"] for e in iframe_srcs + script_srcs
            if e.get("is_ad_like") and e.get("domain")
        })[:50]

        # ── Derive signals ────────────────────────────────────────────────
        google_vignette = "#google_vignette" in current_url.lower()
        ad_like_url     = url_cls not in ("benign", "same_domain", "expected_referrer", "unknown")
        off_domain_orig = bool(
            original_domain and current_domain and
            get_registrable_domain(f"https://{current_domain}") !=
            get_registrable_domain(f"https://{original_domain}")
        )
        off_domain_ref  = bool(
            expected_referrer_domain and current_domain and
            get_registrable_domain(f"https://{current_domain}") !=
            get_registrable_domain(f"https://{expected_referrer_domain}")
        )

        if google_vignette:
            signals.append("google_vignette_detected")
        if ad_like_url:
            signals.append(f"ad_like_url:{url_cls}")
        if off_domain_orig and url_cls not in ("expected_referrer",):
            signals.append("off_domain_from_original")
        if tracking:
            signals.append("tracking_params_present")

        detected = bool(signals)

        return {
            "detected":                      detected,
            "signals":                       signals,
            "current_url":                   current_url,
            "url_domain":                    current_domain,
            "url_classification":            url_cls,
            "page_title":                    page_title,
            "document_referrer":             "",   # filled by snapshot
            "iframe_count":                  len(iframe_srcs),
            "script_count":                  len(script_srcs),
            "anchor_count":                  len(anchor_hrefs),
            "iframe_srcs":                   iframe_srcs,
            "script_srcs":                   script_srcs,
            "anchor_hrefs_sample":           anchor_hrefs,
            "visible_ad_text_candidates":    visible_ad_texts,
            "google_vignette_detected":      google_vignette,
            "ad_like_url_detected":          ad_like_url,
            "off_domain_from_original":      off_domain_orig,
            "off_domain_from_expected_referrer": off_domain_ref,
            "query_params":                  q_params,
            "tracking_params":               tracking,
            "possible_ad_network_domains":   ad_network_domains,
            "possible_landing_page_category": url_cls if ad_like_url else None,
        }

    except Exception as exc:
        return {
            "detected":             False,
            "signals":              [],
            "ad_evidence_error":    str(exc),
        }

def analyze_back_step_for_ads(step, original_url, expected_referrer_url):
    """
    Add ad evidence fields to an existing back_steps_debug entry.
    Returns a new dict with ad_evidence key added. Never mutates input.
    """
    try:
        original_domain          = get_domain(original_url)
        expected_referrer_domain = get_domain(expected_referrer_url)

        after_url       = step.get("after_back_url") or ""
        short_url       = step.get("url_after_short_wait") or ""
        long_url        = step.get("url_after_long_wait") or ""

        after_domain  = get_domain(after_url)
        short_domain  = get_domain(short_url)
        long_domain   = get_domain(long_url)

        after_cls     = classify_ad_url(after_url,  original_domain=original_domain, expected_referrer_domain=expected_referrer_domain)
        short_cls     = classify_ad_url(short_url,  original_domain=original_domain, expected_referrer_domain=expected_referrer_domain)

        tracking_after = extract_tracking_params(after_url)
        tracking_short = extract_tracking_params(short_url)
        tracking_long  = extract_tracking_params(long_url)
        all_tracking   = {**tracking_after, **tracking_short, **tracking_long}

        signals = []
        if step.get("ad_or_vignette_after_back"):
            signals.append("ad_or_vignette_after_back")
        if "#google_vignette" in after_url.lower() or "#google_vignette" in short_url.lower():
            signals.append("google_vignette")
        if after_cls not in ("benign", "same_domain", "expected_referrer", "unknown"):
            signals.append(f"ad_url_after_back:{after_cls}")
        if all_tracking:
            signals.append("tracking_params_present")
        if after_domain and after_domain != original_domain and after_cls not in ("expected_referrer",):
            signals.append("off_domain_after_back")

        landing_cat = after_cls if after_cls not in ("benign", "same_domain", "expected_referrer", "unknown") else None

        ad_evidence = {
            "detected":                   bool(signals),
            "url_classification":         after_cls,
            "signals":                    signals,
            "after_url_domain":           after_domain,
            "short_wait_url_domain":      short_domain,
            "long_wait_url_domain":       long_domain,
            "tracking_params":            all_tracking,
            "possible_landing_page_category": landing_cat,
        }

        result = dict(step)
        result["ad_evidence"] = ad_evidence
        return result

    except Exception as exc:
        result = dict(step)
        result["ad_evidence"] = {"detected": False, "ad_evidence_error": str(exc)}
        return result

def analyze_back_chain_for_ads(back_steps, back_chain, final_url, original_url, expected_referrer_url):
    """
    Build a back_ad_analysis summary from back steps and chain URLs.
    """
    try:
        original_domain          = get_domain(original_url)
        expected_referrer_domain = get_domain(expected_referrer_url)

        steps_with_signals   = []
        off_domain_steps     = []
        vignette_steps       = []
        tracker_steps        = []
        landing_categories   = []
        domains_seen         = []
        all_tracking         = {}
        detected             = False

        for s in back_steps:
            ad_ev = s.get("ad_evidence", {})
            step_n = s.get("step", "?")
            if ad_ev.get("detected"):
                detected = True
                steps_with_signals.append(step_n)
            if "off_domain_after_back" in ad_ev.get("signals", []):
                off_domain_steps.append(step_n)
            if "google_vignette" in ad_ev.get("signals", []):
                vignette_steps.append(step_n)
            if "tracking_params_present" in ad_ev.get("signals", []):
                tracker_steps.append(step_n)
            if ad_ev.get("possible_landing_page_category"):
                cat = ad_ev["possible_landing_page_category"]
                if cat not in landing_categories:
                    landing_categories.append(cat)
            t = ad_ev.get("tracking_params", {})
            all_tracking.update(t)
            d = ad_ev.get("after_url_domain")
            if d and d not in domains_seen:
                domains_seen.append(d)

        # Also scan back_chain URLs directly
        for url in (back_chain or []):
            cls = classify_ad_url(url, original_domain=original_domain, expected_referrer_domain=expected_referrer_domain)
            d   = get_domain(url)
            if d and d not in domains_seen:
                domains_seen.append(d)
            if cls not in ("benign", "same_domain", "expected_referrer", "unknown"):
                if cls not in landing_categories:
                    landing_categories.append(cls)
                detected = True
            t = extract_tracking_params(url)
            all_tracking.update(t)

        final_cls = classify_ad_url(final_url, original_domain=original_domain, expected_referrer_domain=expected_referrer_domain)

        return {
            "detected":                True if detected else False,
            "steps_with_ad_signals":   steps_with_signals,
            "off_domain_steps":        off_domain_steps,
            "google_vignette_steps":   vignette_steps,
            "tracker_redirect_steps":  tracker_steps,
            "landing_page_categories": landing_categories,
            "domains_seen":            domains_seen,
            "tracking_params_seen":    all_tracking,
            "final_url_classification": final_cls,
        }
    except Exception as exc:
        return {
            "detected":             False,
            "ad_evidence_error":    str(exc),
        }

def build_domain_ad_summary(evidence_log):
    """
    Build a top-level ad_summary from all evidence collected during a domain test.
    """
    try:
        google_vignette   = False
        off_domain_ad     = False
        popup_redirect    = False
        tracker_params    = False
        ad_signals        = False
        ad_net_domains    = set()
        landing_domains   = set()
        landing_cats      = []
        tracking_seen     = {}
        visible_ad_count  = 0
        notes             = []

        def process_back_analysis(ba):
            nonlocal google_vignette, off_domain_ad, popup_redirect, tracker_params, ad_signals
            if not ba:
                return
            if ba.get("detected"):
                ad_signals = True
            if ba.get("google_vignette_steps"):
                google_vignette = True
            if ba.get("off_domain_steps"):
                off_domain_ad = True
            if ba.get("tracker_redirect_steps"):
                tracker_params = True
            tracking_seen.update(ba.get("tracking_params_seen", {}))
            for cat in ba.get("landing_page_categories", []):
                if cat == "popup_redirect":
                    popup_redirect = True
                if cat not in landing_cats:
                    landing_cats.append(cat)
            for d in ba.get("domains_seen", []):
                if d:
                    landing_domains.add(d)

        def process_snapshots(snaps):
            nonlocal google_vignette, off_domain_ad, tracker_params, ad_signals, visible_ad_count
            for snap in (snaps or []):
                ae = snap.get("ad_evidence", {})
                if not ae:
                    continue
                if ae.get("detected"):
                    ad_signals = True
                if ae.get("google_vignette_detected"):
                    google_vignette = True
                if ae.get("off_domain_from_original"):
                    off_domain_ad = True
                if ae.get("tracking_params"):
                    tracker_params = True
                    tracking_seen.update(ae["tracking_params"])
                visible_ad_count += len(ae.get("visible_ad_text_candidates", []))
                for d in ae.get("possible_ad_network_domains", []):
                    if d:
                        ad_net_domains.add(d)
                cat = ae.get("possible_landing_page_category")
                if cat and cat not in landing_cats:
                    landing_cats.append(cat)

        def process_back_steps(steps):
            for s in (steps or []):
                ae = s.get("ad_evidence", {})
                if ae.get("detected"):
                    process_back_analysis({
                        "detected": True,
                        "google_vignette_steps": [s.get("step")] if "google_vignette" in ae.get("signals", []) else [],
                        "off_domain_steps":      [s.get("step")] if "off_domain_after_back" in ae.get("signals", []) else [],
                        "tracker_redirect_steps":[s.get("step")] if "tracking_params_present" in ae.get("signals", []) else [],
                        "landing_page_categories": [ae["possible_landing_page_category"]] if ae.get("possible_landing_page_category") else [],
                        "domains_seen":          [ae.get("after_url_domain")] if ae.get("after_url_domain") else [],
                        "tracking_params_seen":  ae.get("tracking_params", {}),
                    })

        # Process all sources
        process_snapshots(evidence_log.get("snapshots_main", []))
        process_snapshots(evidence_log.get("snapshots_article", []))
        process_back_analysis(evidence_log.get("main_back_ad_analysis"))
        process_back_analysis(evidence_log.get("article_back_ad_analysis"))
        process_back_steps(evidence_log.get("back_steps_debug_main", []))
        process_back_steps(evidence_log.get("back_steps_debug_article", []))

        # Scan back chain URLs
        for chain_key in ("main_back_chain", "article_back_chain"):
            for url in (evidence_log.get(chain_key) or []):
                d = get_domain(url)
                if d:
                    landing_domains.add(d)
                if "#google_vignette" in url.lower():
                    google_vignette = True
                    ad_signals = True

        # Check final back URLs
        for url_key in ("main_back_final_url", "article_back_final_url"):
            url = evidence_log.get(url_key) or ""
            if url:
                cls = classify_ad_url(
                    url,
                    original_domain=get_domain(evidence_log.get("main_target_url", "")),
                    expected_referrer_domain=get_domain(evidence_log.get("controlled_referrer_url", "")),
                )
                if cls not in ("benign", "same_domain", "expected_referrer", "unknown"):
                    ad_signals = True
                    if cls not in landing_cats:
                        landing_cats.append(cls)

        return {
            "ad_signals_detected":             ad_signals,
            "google_vignette_detected":        google_vignette,
            "off_domain_ad_redirect_detected": off_domain_ad,
            "popup_redirect_detected":         popup_redirect,
            "tracker_params_detected":         tracker_params,
            "possible_ad_network_domains":     list(ad_net_domains)[:50],
            "possible_landing_domains":        list(landing_domains)[:50],
            "landing_page_categories":         landing_cats,
            "tracking_params_seen":            tracking_seen,
            "visible_ad_text_candidates_count": visible_ad_count,
            "network_ad_like_request_count":   0,   # Selenium: no network interception
            "notes":                           notes,
        }
    except Exception as exc:
        return {
            "ad_signals_detected": False,
            "ad_summary_error":    str(exc),
        }

# ---------------------------------------------------------------------------
# Interaction logger
# ---------------------------------------------------------------------------

class InteractionLogger:
    def __init__(self):
        self.events = []

    def add(self, action, driver=None, **details):
        event = {
            "ts_iso": utc_now_iso(),
            "ts_epoch_ms": int(time.time() * 1000),
            "action": action,
            **details,
        }
        if driver is not None:
            try:
                event["current_url"] = driver.current_url
            except Exception as e:
                event["current_url_error"] = str(e)
        self.events.append(event)

    def get_events(self):
        return self.events

# ---------------------------------------------------------------------------
# CSV loading
# ---------------------------------------------------------------------------

def detect_csv_domain_column(fieldnames):
    if not fieldnames:
        return None
    normalized = {name.lower().strip(): name for name in fieldnames}
    for candidate in DOMAIN_COLUMN_CANDIDATES:
        if candidate in normalized:
            return normalized[candidate]
    return None

def load_urls_from_csv(csv_file, split_filter=None):
    rows = []
    with open(csv_file, "r", encoding="utf-8-sig", newline="") as f:
        sample = f.read(4096)
        f.seek(0)
        try:
            dialect = csv.Sniffer().sniff(sample)
        except csv.Error:
            dialect = csv.excel
        reader = csv.DictReader(f, dialect=dialect)
        if not reader.fieldnames:
            raise ValueError("Input CSV has no header row.")
        domain_column = detect_csv_domain_column(reader.fieldnames)
        if not domain_column:
            raise ValueError(
                f"Could not detect domain/URL column. Found: {reader.fieldnames}"
            )
        for row_index, row in enumerate(reader, start=1):
            if split_filter:
                if row.get("split_group", "").strip().lower() != split_filter.lower():
                    continue
            raw_domain = row.get(domain_column, "").strip()
            if not raw_domain:
                continue
            target_url = row.get("target_url", "").strip() or domain_to_url(raw_domain)
            domain = get_domain(target_url) or raw_domain.replace("www.", "").strip()
            rows.append({
                "row_index": row_index,
                "domain": domain,
                "target_url": target_url,
                "csv_metadata": dict(row),
            })
    return rows

# ---------------------------------------------------------------------------
# Driver
# ---------------------------------------------------------------------------

def create_driver(headless, page_load_timeout):
    opts = Options()
    if headless:
        opts.add_argument("--headless=new")
    opts.add_argument("--window-size=1366,768")
    opts.add_argument("--no-sandbox")
    opts.add_argument("--disable-dev-shm-usage")
    opts.add_argument("--disable-notifications")
    opts.add_argument("--disable-popup-blocking")
    opts.add_argument("--disable-blink-features=AutomationControlled")
    opts.add_argument("--allow-running-insecure-content")
    opts.add_argument("--disable-gpu")
    opts.add_argument("--remote-debugging-port=0")
    opts.add_argument(
        "user-agent=Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
    )
    driver = webdriver.Chrome(options=opts)
    driver.set_page_load_timeout(page_load_timeout)
    driver.execute_cdp_cmd(
        "Page.addScriptToEvaluateOnNewDocument",
        {"source": INSTRUMENTATION_JS},
    )
    return driver

# ---------------------------------------------------------------------------
# Wait helpers
# ---------------------------------------------------------------------------

def wait_for_readystate(driver, logger=None, phase=None, timeout=20):
    try:
        WebDriverWait(driver, timeout).until(
            lambda d: d.execute_script(
                "return document.readyState"
            ) in ["interactive", "complete"]
        )
        if logger:
            logger.add("wait_ready_success", driver, phase=phase)
    except TimeoutException:
        if logger:
            logger.add("wait_ready_timeout", driver, phase=phase)

def wait_until_stable(driver, timeout=2.0):
    deadline = time.time() + 10
    last_url = driver.current_url
    stable_since = time.time()
    while time.time() < deadline:
        time.sleep(0.3)
        try:
            current = driver.current_url
        except Exception:
            break
        if current != last_url:
            last_url = current
            stable_since = time.time()
        elif time.time() - stable_since >= timeout:
            return

# ---------------------------------------------------------------------------
# Page info / untestable detection
# ---------------------------------------------------------------------------

def get_page_text_safe(driver):
    try:
        return driver.execute_script("""
            return {
                title: document.title || "",
                body_text: document.body ? document.body.innerText.slice(0, 4000) : "",
                url: location.href
            };
        """)
    except Exception:
        try:
            return {
                "title": driver.title or "",
                "body_text": "",
                "url": driver.current_url,
            }
        except Exception:
            return {"title": "", "body_text": "", "url": ""}

def detect_untestable_page(driver):
    page = get_page_text_safe(driver)
    title = (page.get("title") or "").lower()
    body  = (page.get("body_text") or "").lower()
    url   = (page.get("url") or "").lower()

    patterns = {
        "CAPTCHA": [
            "captcha", "verify you are human", "are you a human",
            "checking your browser", "attention required", "cloudflare",
        ],
        "REGION_BLOCKED": [
            "not available in your region", "not available from your region",
            "unavailable in your country", "access denied", "forbidden",
        ],
        "HOST_ERROR": [
            "this site can't be reached", "server ip address could not be found",
            "err_name_not_resolved", "err_connection", "err_timed_out",
            "err_ssl", "404 not found", "503 service unavailable",
        ],
    }
    for status, terms in patterns.items():
        if any(t in title or t in body or t in url for t in terms):
            return {"untestable": True, "reason": status, "page_info": page}
    return {"untestable": False, "reason": None, "page_info": page}

def is_chrome_security_interstitial(driver):
    page  = get_page_text_safe(driver)
    url   = (page.get("url") or "").lower()
    title = (page.get("title") or "").lower()
    body  = (page.get("body_text") or "").lower()
    indicators = [
        "your connection is not private", "your connection is not secure",
        "privacy error", "deceptive site ahead", "dangerous site",
        "attackers might be trying to steal your information",
        "net::err_cert", "ssl error",
    ]
    if url.startswith("chrome-error://"):
        return True
    if "privacy error" in title:
        return True
    if any(i in body for i in indicators):
        return True
    return False

def click_first_available(driver, candidates):
    for c in candidates:
        try:
            elements = driver.find_elements(c["by"], c["value"])
            for el in elements:
                try:
                    if el.is_displayed() and el.is_enabled():
                        driver.execute_script("arguments[0].click();", el)
                        return {
                            "clicked": True,
                            "by": str(c["by"]),
                            "value": c["value"],
                            "text": el.text,
                        }
                except Exception:
                    continue
        except Exception:
            continue
    return {"clicked": False}

def handle_chrome_security_interstitial(
    driver, logger=None, phase=None, target_url=None, proceed=True
):
    sleep_ms(500)
    if not is_chrome_security_interstitial(driver):
        return {"detected": False}
    if logger:
        logger.add("chrome_security_interstitial_detected", driver, phase=phase)
    if not proceed:
        return {"detected": True, "proceed_clicked": False, "reason": "proceed_disabled"}

    details_candidates = [
        {"by": By.ID, "value": "details-button"},
        {"by": By.ID, "value": "advancedButton"},
        {
            "by": By.XPATH,
            "value": "//button[contains(translate(normalize-space(.),"
                     "'ABCDEFGHIJKLMNOPQRSTUVWXYZ','abcdefghijklmnopqrstuvwxyz'),'advanced')]",
        },
        {
            "by": By.XPATH,
            "value": "//button[contains(translate(normalize-space(.),"
                     "'ABCDEFGHIJKLMNOPQRSTUVWXYZ','abcdefghijklmnopqrstuvwxyz'),'details')]",
        },
    ]
    details_result = click_first_available(driver, details_candidates)
    sleep_ms(800)

    proceed_candidates = [
        {"by": By.ID, "value": "proceed-link"},
        {
            "by": By.XPATH,
            "value": "//a[contains(translate(normalize-space(.),"
                     "'ABCDEFGHIJKLMNOPQRSTUVWXYZ','abcdefghijklmnopqrstuvwxyz'),'proceed')]",
        },
        {
            "by": By.XPATH,
            "value": "//a[contains(translate(normalize-space(.),"
                     "'ABCDEFGHIJKLMNOPQRSTUVWXYZ','abcdefghijklmnopqrstuvwxyz'),'continue')]",
        },
        {
            "by": By.XPATH,
            "value": "//button[contains(translate(normalize-space(.),"
                     "'ABCDEFGHIJKLMNOPQRSTUVWXYZ','abcdefghijklmnopqrstuvwxyz'),'proceed')]",
        },
    ]
    proceed_result = click_first_available(driver, proceed_candidates)
    if proceed_result.get("clicked"):
        sleep_ms(2000)
        wait_for_readystate(
            driver, logger, phase=f"{phase}_after_proceed", timeout=15
        )
        return {
            "detected": True,
            "details_clicked": details_result.get("clicked"),
            "proceed_clicked": True,
        }
    return {
        "detected": True,
        "details_clicked": details_result.get("clicked"),
        "proceed_clicked": False,
        "reason": "proceed_link_not_found",
    }

# ---------------------------------------------------------------------------
# Cookie banner
# FIX: Added URL-change safety check — if cookie click navigates away, back out.
# ---------------------------------------------------------------------------

def handle_cookie_banner(driver, logger=None):
    before_url = driver.current_url
    try:
        candidates = driver.find_elements(
            By.CSS_SELECTOR,
            "button, [role='button'], input[type='button'], input[type='submit'], a",
        )
    except Exception:
        return {"clicked": False}

    for el in candidates:
        try:
            if not el.is_displayed() or not el.is_enabled():
                continue
            raw_text  = (el.text or "").strip()
            tag       = el.tag_name.lower()
            el_id     = el.get_attribute("id") or ""
            class_name = el.get_attribute("class") or ""
            aria_label = el.get_attribute("aria-label") or ""
            text     = normalize_text(raw_text)
            combined = normalize_text(" ".join([text, el_id, class_name, aria_label]))

            if any(b in combined for b in COOKIE_REJECT_TEXTS):
                continue

            looks_like_cookie = any(t in combined for t in COOKIE_CONTEXT_TOKENS)
            text_is_accept    = text in COOKIE_ACCEPT_TEXTS

            if not looks_like_cookie and not text_is_accept:
                continue

            if tag == "a" and not looks_like_cookie:
                continue

            el.click()
            sleep_ms(1000)

            after_url = driver.current_url

            if not urls_match(before_url, after_url):
                if logger:
                    logger.add(
                        "cookie_banner_invalid_click_navigated", driver,
                        before_url=before_url, after_url=after_url, clicked_text=text,
                    )
                driver.back()
                wait_until_stable(driver)
                return {
                    "clicked": False,
                    "invalid_cookie_click": True,
                    "before_url": before_url,
                    "after_url": after_url,
                }

            if logger:
                logger.add("cookie_banner_clicked", driver, text=text, tag=tag)
            return {"clicked": True, "text": text, "tag": tag, "url_unchanged": True}

        except StaleElementReferenceException:
            continue
        except Exception:
            continue

    if logger:
        logger.add("cookie_banner_not_found", driver)
    return {"clicked": False}

# ---------------------------------------------------------------------------
# Navigation
# ---------------------------------------------------------------------------

def safe_get(driver, url, logger, phase, allow_insecure_proceed=True):
    logger.add("navigation_start", driver, phase=phase, target_url=url)
    try:
        driver.get(url)
        wait_for_readystate(driver, logger, phase=f"{phase}_ready", timeout=20)
        sec = handle_chrome_security_interstitial(
            driver, logger, phase, url, allow_insecure_proceed
        )
        logger.add("navigation_complete", driver, phase=phase, target_url=url, security=sec)
        return True, None
    except TimeoutException as e:
        logger.add("navigation_timeout", driver, phase=phase, target_url=url, error=str(e))
        sec = handle_chrome_security_interstitial(
            driver, logger, f"{phase}_timeout_sec", url, allow_insecure_proceed
        )
        if sec.get("proceed_clicked"):
            return True, None
        return False, str(e)
    except Exception as e:
        logger.add("navigation_error", driver, phase=phase, target_url=url, error=str(e))
        return False, str(e)

def navigate_from_referrer(
    driver, referrer_url, target_url, logger, allow_insecure_proceed
):
    """Navigate from referrer using JS redirect to preserve document.referrer."""
    safe_get(
        driver, referrer_url, logger,
        phase="load_controlled_referrer",
        allow_insecure_proceed=allow_insecure_proceed,
    )
    wait_for_readystate(driver, logger, phase="referrer_ready", timeout=10)

    logger.add("js_redirect_to_target", driver,
               referrer_url=referrer_url, target_url=target_url)
    try:
        driver.execute_script("window.location.href = arguments[0];", target_url)
    except Exception as e:
        logger.add("js_redirect_error", driver, error=str(e))
        safe_get(driver, target_url, logger, "fallback_direct_load", allow_insecure_proceed)

    wait_for_readystate(driver, logger, phase="target_ready", timeout=30)
    wait_until_stable(driver)
    handle_chrome_security_interstitial(
        driver, logger, "target_security", target_url, allow_insecure_proceed
    )
    handle_cookie_banner(driver, logger)
    sleep_ms(800)
    logger.add("navigate_from_referrer_complete", driver, target_url=target_url)

# ---------------------------------------------------------------------------
# Snapshots & instrumentation
# ---------------------------------------------------------------------------

def capture_history_snapshot(driver, phase, logger=None,
                              original_url=None, expected_referrer_url=None):
    """
    Capture a forensic snapshot of browser state.
    If original_url and expected_referrer_url are provided, also collects
    structured ad_evidence from the current page DOM.
    """
    try:
        snapshot = driver.execute_script("""
            const p = arguments[0];
            const safeStr = (o) => {
                try { return JSON.stringify(o); } catch(e) { return String(o); }
            };
            return {
                phase: p,
                timestamp: Date.now(),
                current_url: location.href,
                document_referrer: document.referrer,
                title: document.title,
                history_length: history.length,
                history_state: safeStr(history.state),
                navigation_entries: performance.getEntriesByType("navigation").map(e => ({
                    type: e.type, name: e.name, redirectCount: e.redirectCount
                })),
                dom_evidence: {
                    script_count: document.scripts.length,
                    iframe_count: document.querySelectorAll("iframe").length,
                    anchor_count: document.querySelectorAll("a[href]").length
                }
            };
        """, phase)
        if logger:
            logger.add("snapshot_captured", driver, phase=phase)
        snapshot = snapshot or {"phase": phase, "snapshot_error": "null_result"}

        # Attach ad_evidence if context URLs provided
        if original_url is not None:
            ad_ev = collect_dom_ad_evidence(driver, original_url, expected_referrer_url or "")
            # Backfill document_referrer from snapshot
            ad_ev["document_referrer"] = snapshot.get("document_referrer", "")
            snapshot["ad_evidence"] = ad_ev

        return snapshot
    except Exception as e:
        fallback = {"phase": phase, "snapshot_error": str(e)}
        try:
            fallback["current_url_fallback"] = driver.current_url
        except Exception:
            pass
        return fallback

def get_instrumentation(driver, logger=None, phase=None):
    try:
        data = driver.execute_script("return window.__bbh || {};") or {}
        if logger:
            logger.add(
                "instrumentation_collected", driver, phase=phase,
                pushState_count=data.get("pushState_count", 0),
                replaceState_count=data.get("replaceState_count", 0),
                website_popstate_listener_count=data.get(
                    "website_popstate_listener_count", 0
                ),
                popstate_fired=data.get("popstate_fired", False),
                redirect_after_popstate=data.get("redirect_after_popstate", False),
            )
        return data
    except Exception as e:
        if logger:
            logger.add("instrumentation_error", driver, phase=phase, error=str(e))
        return {}

def extract_signals(prefix, data):
    return {
        f"pushState_count_{prefix}":
            data.get("pushState_count", 0),
        f"replaceState_count_{prefix}":
            data.get("replaceState_count", 0),
        f"website_popstate_listener_count_{prefix}":
            data.get("website_popstate_listener_count", 0),
        f"popstate_fired_{prefix}":
            data.get("popstate_fired", False),
        f"redirect_after_popstate_{prefix}":
            data.get("redirect_after_popstate", False),
        f"location_assign_count_{prefix}":
            data.get("location_assign_count", 0),
        f"location_replace_count_{prefix}":
            data.get("location_replace_count", 0),
        f"new_window_observed_{prefix}":
            data.get("new_window_observed", False),
        f"window_open_count_{prefix}":
            data.get("window_open_count", 0),
        f"events_{prefix}":
            data.get("events", []),
    }

def empty_signals(prefix):
    return {
        f"pushState_count_{prefix}": 0,
        f"replaceState_count_{prefix}": 0,
        f"website_popstate_listener_count_{prefix}": 0,
        f"popstate_fired_{prefix}": False,
        f"redirect_after_popstate_{prefix}": False,
        f"location_assign_count_{prefix}": 0,
        f"location_replace_count_{prefix}": 0,
        f"new_window_observed_{prefix}": False,
        f"window_open_count_{prefix}": 0,
        f"events_{prefix}": [],
    }

# ---------------------------------------------------------------------------
# Simulate passive interaction
# ---------------------------------------------------------------------------

def simulate_user_interaction(driver, logger, phase_prefix, allow_insecure_proceed):
    logger.add("interaction_start", driver, phase=phase_prefix)
    try:
        driver.execute_script("window.scrollBy(0, 700);")
        sleep_ms(400)
        driver.execute_script("window.scrollBy(0, 800);")
        sleep_ms(600)
        driver.execute_script("window.scrollBy(0, -500);")
        sleep_ms(400)
        wait_for_readystate(
            driver, logger, f"{phase_prefix}_after_scroll", timeout=5
        )
        handle_chrome_security_interstitial(
            driver, logger, f"{phase_prefix}_after_scroll_sec",
            None, allow_insecure_proceed,
        )
    except Exception as e:
        logger.add("interaction_error", driver, phase=phase_prefix, error=str(e))
    logger.add("interaction_end", driver, phase=phase_prefix)

# ---------------------------------------------------------------------------
# Back-chain test
# ---------------------------------------------------------------------------

def is_ad_or_vignette_url(url):
    if not url:
        return False
    patterns = [
        "google_vignette", "googlesyndication", "doubleclick",
        "pagead", "adservice", "interstitial", "vignette",
    ]
    return any(p in url.lower() for p in patterns)

def run_back_chain(
    driver,
    domain,
    expected_url,
    max_steps,
    prefix,
    logger,
    allow_insecure_proceed,
    allow_domain_match=False,
    screenshot_dir="screenshots",
    original_url=None,
):
    """
    Execute up to max_steps back-button presses. Captures forensic snapshots
    with ad_evidence at each phase, and annotates each back_step_debug entry
    with structured ad evidence.
    """
    chain     = []
    debug     = []
    snapshots = []

    logger.add(
        "back_chain_start", driver, phase=prefix,
        expected_url=expected_url, max_steps=max_steps,
    )

    for step in range(1, max_steps + 1):

        # ── Pre-press snapshot ────────────────────────────────────────────
        before_url  = driver.current_url
        before_snap = capture_history_snapshot(
            driver, f"{prefix}_before_back_{step}", logger,
            original_url=original_url, expected_referrer_url=expected_url,
        )
        snapshots.append(before_snap)

        # ── Screenshot BEFORE back-press ──────────────────────────────────
        save_backpress_screenshot(
            driver,
            domain=domain,
            step=step,
            page_type=prefix,
            timing="before",
            screenshot_dir=screenshot_dir,
        )

        # ── Press back ────────────────────────────────────────────────────
        try:
            driver.back()
        except WebDriverException as e:
            logger.add("back_error", driver, phase=prefix, step=step, error=str(e))

        # ── Short wait ────────────────────────────────────────────────────
        sleep_ms(700)
        try:
            after_short = driver.current_url
        except Exception:
            after_short = ""

        # ── Stability wait ────────────────────────────────────────────────
        wait_until_stable(driver)
        sleep_ms(1500)
        try:
            after_long = driver.current_url
        except Exception:
            after_long = ""

        handle_chrome_security_interstitial(
            driver, logger, f"{prefix}_after_back_{step}_sec",
            None, allow_insecure_proceed,
        )

        try:
            final_url = driver.current_url
        except Exception:
            final_url = after_long

        # ── Post-press snapshot with ad_evidence ──────────────────────────
        after_snap = capture_history_snapshot(
            driver, f"{prefix}_after_back_{step}", logger,
            original_url=original_url, expected_referrer_url=expected_url,
        )
        snapshots.append(after_snap)

        # ── Screenshot AFTER back-press ───────────────────────────────────
        save_backpress_screenshot(
            driver,
            domain=domain,
            step=step,
            page_type=prefix,
            timing="after",
            screenshot_dir=screenshot_dir,
        )

        # ── Outcome evaluation ────────────────────────────────────────────
        same_page    = urls_match(before_url, final_url)
        auto_redirect = (
            normalize_url(after_short) != normalize_url(after_long)
            and not urls_match(after_short, expected_url)
            and not urls_match(after_long, expected_url)
        )
        success = (
            same_domain(final_url, expected_url)
            if allow_domain_match
            else urls_match(final_url, expected_url)
        )
        ad_vignette = (
            is_ad_or_vignette_url(final_url)
            or is_ad_or_vignette_url(after_short)
        )

        raw_step = {
            "step": step,
            "before_back_url": before_url,
            "after_back_url": final_url,
            "url_after_short_wait": after_short,
            "url_after_long_wait": after_long,
            "auto_redirect_after_back": auto_redirect,
            "same_page_after_back": same_page,
            "ad_or_vignette_after_back": ad_vignette,
            "expected_url": expected_url,
            "back_success": success,
            "history_length_before": before_snap.get("history_length"),
            "history_length_after": after_snap.get("history_length"),
        }

        # Annotate step with structured ad evidence
        annotated_step = analyze_back_step_for_ads(
            raw_step,
            original_url=original_url or before_url,
            expected_referrer_url=expected_url,
        )
        chain.append(final_url)
        debug.append(annotated_step)

        logger.add(
            "back_step_result", driver, phase=prefix, step=step,
            before_url=before_url, after_url=final_url,
            same_page=same_page, success=success, auto_redirect=auto_redirect,
        )

        if success:
            break

    final = chain[-1] if chain else driver.current_url
    back_failed = (
        not same_domain(final, expected_url)
        if allow_domain_match
        else not urls_match(final, expected_url)
    )

    logger.add(
        "back_chain_end", driver, phase=prefix,
        final_url=final, back_failed=back_failed, steps=len(chain),
    )

    # Build back chain ad analysis
    back_ad_analysis = analyze_back_chain_for_ads(
        debug, chain, final,
        original_url=original_url or "",
        expected_referrer_url=expected_url,
    )

    return {
        "back_failed": back_failed,
        "back_steps_required": len(chain),
        "back_final_url": final,
        "back_chain": chain,
        "back_steps_debug": debug,
        "snapshots": snapshots,
        "back_ad_analysis": back_ad_analysis,
    }

# ---------------------------------------------------------------------------
# Page classification
# FIX: replaceState, popstate_listener, and history_modified are supporting
#      evidence only. They NEVER classify a page as SUSPICIOUS or HIJACKING
#      when Back succeeded in one step.
# ---------------------------------------------------------------------------

def classify_page(prefix, back_result, signals, article_mode=False):
    reasons   = []
    back_failed = back_result["back_failed"]
    steps       = back_result["back_steps_required"]
    debug       = back_result.get("back_steps_debug", [])

    push_count             = signals.get(f"pushState_count_{prefix}", 0)
    popstate_fired         = signals.get(f"popstate_fired_{prefix}", False)
    redirect_after_popstate = signals.get(f"redirect_after_popstate_{prefix}", False)
    location_assign_count  = signals.get(f"location_assign_count_{prefix}", 0)
    location_replace_count = signals.get(f"location_replace_count_{prefix}", 0)
    new_window_observed    = signals.get(f"new_window_observed_{prefix}", False)
    window_open_count      = signals.get(f"window_open_count_{prefix}", 0)

    same_page_trap        = any(s.get("same_page_after_back") for s in debug)
    auto_redirect_after_back = any(s.get("auto_redirect_after_back") for s in debug)
    ad_vignette_after_back   = any(s.get("ad_or_vignette_after_back") for s in debug)

    location_redirect_after_back = (
        popstate_fired
        and (location_assign_count > 0 or location_replace_count > 0)
    )

    if same_page_trap:
        return "HIJACKING", ["back_trapped_user_on_same_page"]
    if redirect_after_popstate:
        return "HIJACKING", ["redirect_after_popstate"]
    if location_redirect_after_back:
        return "HIJACKING", ["location_redirect_after_back"]
    if ad_vignette_after_back:
        return "HIJACKING", ["back_opened_google_vignette_or_ad_interstitial"]
    if new_window_observed or window_open_count > 0:
        return "HIJACKING", ["click_opened_unexpected_new_window_or_tab"]
    if back_failed:
        reasons.append("final_back_did_not_return_to_expected_page")
        if push_count > 0:
            reasons.append("pushState_used")
        if popstate_fired:
            reasons.append("popstate_fired")
        return "HIJACKING", reasons

    if steps > 1:
        reasons.append("multiple_back_steps_required")
        if push_count > 0:
            reasons.append("pushState_used")
        if auto_redirect_after_back:
            reasons.append("auto_redirect_after_back")
        if popstate_fired:
            reasons.append("popstate_fired")
        return "SUSPICIOUS", reasons

    return "BENIGN", ["back_returned_expected_page_in_one_step"]

# ---------------------------------------------------------------------------
# Domain classification
# FIX: Returns UNTESTABLE (not BENIGN) when both pages have no valid result.
# ---------------------------------------------------------------------------

def classify_domain(main_cls, article_cls):
    if main_cls == "HIJACKING" or article_cls == "HIJACKING":
        return "HIJACKING"
    if main_cls == "SUSPICIOUS" or article_cls == "SUSPICIOUS":
        return "SUSPICIOUS"
    if main_cls == "BENIGN" or article_cls == "BENIGN":
        return "BENIGN"
    return "UNTESTABLE"

# ---------------------------------------------------------------------------
# Article link extraction
# FIX: Removed "view", "read", "content", "newsview" from positive hints
#      as they match generic navigation elements.
# ---------------------------------------------------------------------------

def score_candidate_link(href, text):
    score = 0
    try:
        path = urlparse(href).path.lower()
    except Exception:
        return -100
    text_l = normalize_text(text)

    if any(p in path for p in ARTICLE_NEGATIVE_PATTERNS):
        return -100
    if any(p in path for p in ARTICLE_POSITIVE_PATTERNS):
        score += 5
    if re.search(r"\d{3,}", path):
        score += 3
    parts = [p for p in path.strip("/").split("/") if p]
    if len(parts) >= 2:
        score += 2
    if len(text_l) >= 25:
        score += 2
    if text_l in ARTICLE_SKIP_TEXTS:
        score -= 5
    if len(text_l) < 5:
        score -= 2
    return score

def get_candidate_article_links(driver, base_url, logger):
    base_normalized = normalize_url(base_url)
    try:
        anchors = driver.find_elements(By.TAG_NAME, "a")
    except Exception:
        return []

    candidates = []
    for anchor in anchors:
        try:
            href = anchor.get_attribute("href") or ""
            text = anchor.text or ""
            if not href:
                continue
            if href.startswith(("javascript:", "mailto:", "tel:", "#")):
                continue
            parsed = urlparse(href)
            if parsed.scheme not in ("http", "https"):
                continue
            if parsed.fragment and not parsed.path:
                continue
            if not same_domain(href, base_url):
                continue
            if normalize_url(href) == base_normalized:
                continue
            score = score_candidate_link(href, text)
            if score > 0:
                candidates.append({"href": href, "text": text, "score": score})
        except StaleElementReferenceException:
            continue

    candidates.sort(key=lambda x: x["score"], reverse=True)
    logger.add(
        "candidate_article_links_found", driver,
        base_url=base_url, count=len(candidates),
    )
    return candidates[:25]

def select_article_candidate(candidates):
    if not candidates:
        return None
    for c in candidates[:10]:
        href = (c.get("href") or "").lower()
        if any(p in href for p in ARTICLE_BAD_LINK_PATTERNS):
            continue
        return c
    return candidates[0] if candidates else None

# ---------------------------------------------------------------------------
# Main page test
# ---------------------------------------------------------------------------

def test_main_page(
    driver, target_url, controlled_referrer_url,
    max_back_steps, logger, allow_insecure_proceed,
    screenshot_dir="screenshots",
):
    logger.add("main_page_test_start", driver, target_url=target_url)

    navigate_from_referrer(
        driver, controlled_referrer_url, target_url,
        logger, allow_insecure_proceed,
    )

    main_target_url = driver.current_url
    snapshots = [capture_history_snapshot(
        driver, "main_after_load", logger,
        original_url=target_url, expected_referrer_url=controlled_referrer_url,
    )]

    untestable = detect_untestable_page(driver)
    if untestable.get("untestable"):
        logger.add("main_page_untestable", driver, reason=untestable.get("reason"))
        return {
            "main_target_url": main_target_url,
            "main_back_final_url": None,
            "main_back_failed": False,
            "main_back_chain": [],
            "main_back_steps_required": 0,
            "main_page_classification": "UNTESTABLE",
            "main_classification_reasons": [untestable.get("reason")],
            "back_steps_debug_main": [],
            "snapshots_main": snapshots,
            "main_back_ad_analysis": {},
            **empty_signals("main"),
        }

    simulate_user_interaction(driver, logger, "main", allow_insecure_proceed)
    snapshots.append(capture_history_snapshot(
        driver, "main_before_back", logger,
        original_url=target_url, expected_referrer_url=controlled_referrer_url,
    ))

    instrumentation = get_instrumentation(driver, logger, "main_before_back")
    signals         = extract_signals("main", instrumentation)
    domain          = get_domain(target_url)

    back_result = run_back_chain(
        driver,
        domain=domain,
        expected_url=controlled_referrer_url,
        max_steps=max_back_steps,
        prefix="main",
        logger=logger,
        allow_insecure_proceed=allow_insecure_proceed,
        allow_domain_match=True,
        screenshot_dir=screenshot_dir,
        original_url=target_url,
    )
    snapshots.extend(back_result.pop("snapshots", []))
    main_back_ad_analysis = back_result.pop("back_ad_analysis", {})

    classification, reasons = classify_page("main", back_result, signals, article_mode=False)
    logger.add("main_page_test_end", driver, classification=classification, reasons=reasons)

    return {
        "main_target_url": main_target_url,
        "main_back_final_url": back_result["back_final_url"],
        "main_back_failed": back_result["back_failed"],
        "main_back_chain": back_result["back_chain"],
        "main_back_steps_required": back_result["back_steps_required"],
        "main_page_classification": classification,
        "main_classification_reasons": reasons,
        "back_steps_debug_main": back_result["back_steps_debug"],
        "snapshots_main": snapshots,
        "main_back_ad_analysis": main_back_ad_analysis,
        **signals,
    }

# ---------------------------------------------------------------------------
# Article page test
# ---------------------------------------------------------------------------

def test_article_page(
    driver, target_url, controlled_referrer_url,
    max_back_steps, logger, allow_insecure_proceed,
    screenshot_dir="screenshots",
):
    logger.add("article_page_test_start", driver, target_url=target_url)

    navigate_from_referrer(
        driver, controlled_referrer_url, target_url,
        logger, allow_insecure_proceed,
    )

    article_referrer_url = driver.current_url
    snapshots = [capture_history_snapshot(
        driver, "article_referrer_after_load", logger,
        original_url=target_url, expected_referrer_url=controlled_referrer_url,
    )]

    untestable = detect_untestable_page(driver)
    if untestable.get("untestable"):
        return _article_untestable_result(
            article_referrer_url, snapshots, untestable.get("reason"), None
        )

    candidates = get_candidate_article_links(driver, article_referrer_url, logger)
    if not candidates:
        logger.add("article_page_test_end", driver, status="NO_VALID_ARTICLE_LINK")
        return {
            "article_test_status": "NO_VALID_ARTICLE_LINK",
            "article_referrer_url": article_referrer_url,
            "selected_article_link": None,
            "article_target_url": None,
            "article_back_final_url": None,
            "article_back_failed": False,
            "article_back_chain": [],
            "article_back_steps_required": 0,
            "article_page_classification": "NO_LINK",
            "article_classification_reasons": ["no_valid_article_link"],
            "back_steps_debug_article": [],
            "snapshots_article": snapshots,
            "article_back_ad_analysis": {},
            **empty_signals("article"),
        }

    selected = select_article_candidate(candidates)

    try:
        driver.get(selected["href"])
    except TimeoutException:
        pass
    except Exception as e:
        logger.add("article_nav_error", driver, error=str(e))
        try:
            driver.execute_script(
                "window.location.href = arguments[0];", selected["href"]
            )
        except Exception:
            pass

    wait_for_readystate(driver, logger, "article_after_nav", timeout=15)
    wait_until_stable(driver)
    handle_chrome_security_interstitial(
        driver, logger, "article_security", selected["href"], allow_insecure_proceed
    )

    cookie_result = handle_cookie_banner(driver, logger)
    if cookie_result.get("invalid_cookie_click"):
        logger.add("article_navigation_contaminated_by_cookie_click", driver)
        return _article_untestable_result(
            article_referrer_url, snapshots,
            "ARTICLE_NAVIGATION_CONTAMINATED", selected,
        )

    article_target_url = driver.current_url

    if not same_domain(article_target_url, article_referrer_url):
        logger.add(
            "article_navigation_off_domain", driver,
            referrer=article_referrer_url, landed=article_target_url,
        )
        return _article_untestable_result(
            article_referrer_url, snapshots, "NAVIGATION_OFF_DOMAIN", selected
        )

    if urls_match(article_target_url, article_referrer_url):
        logger.add("article_navigation_failed_same_url", driver)
        return _article_untestable_result(
            article_referrer_url, snapshots, "ARTICLE_NAVIGATION_FAILED", selected
        )

    sleep_ms(2000)
    snapshots.append(capture_history_snapshot(
        driver, "article_after_load", logger,
        original_url=target_url, expected_referrer_url=article_referrer_url,
    ))

    untestable = detect_untestable_page(driver)
    if untestable.get("untestable"):
        return _article_untestable_result(
            article_referrer_url, snapshots, untestable.get("reason"), selected
        )

    simulate_user_interaction(driver, logger, "article", allow_insecure_proceed)
    snapshots.append(capture_history_snapshot(
        driver, "article_before_back", logger,
        original_url=target_url, expected_referrer_url=article_referrer_url,
    ))

    instrumentation = get_instrumentation(driver, logger, "article_before_back")
    signals         = extract_signals("article", instrumentation)
    domain          = get_domain(target_url)

    back_result = run_back_chain(
        driver,
        domain=domain,
        expected_url=article_referrer_url,
        max_steps=max_back_steps,
        prefix="article",
        logger=logger,
        allow_insecure_proceed=allow_insecure_proceed,
        allow_domain_match=False,
        screenshot_dir=screenshot_dir,
        original_url=target_url,
    )
    snapshots.extend(back_result.pop("snapshots", []))
    article_back_ad_analysis = back_result.pop("back_ad_analysis", {})

    classification, reasons = classify_page(
        "article", back_result, signals, article_mode=True
    )
    logger.add(
        "article_page_test_end", driver,
        status="TESTED", classification=classification, reasons=reasons,
    )

    return {
        "article_test_status": "TESTED",
        "article_referrer_url": article_referrer_url,
        "selected_article_link": selected,
        "article_target_url": article_target_url,
        "article_back_final_url": back_result["back_final_url"],
        "article_back_failed": back_result["back_failed"],
        "article_back_chain": back_result["back_chain"],
        "article_back_steps_required": back_result["back_steps_required"],
        "article_page_classification": classification,
        "article_classification_reasons": reasons,
        "back_steps_debug_article": back_result["back_steps_debug"],
        "snapshots_article": snapshots,
        "article_back_ad_analysis": article_back_ad_analysis,
        **signals,
    }

def _article_untestable_result(referrer_url, snapshots, reason, selected):
    return {
        "article_test_status": reason or "UNTESTABLE",
        "article_referrer_url": referrer_url,
        "selected_article_link": selected,
        "article_target_url": None,
        "article_back_final_url": None,
        "article_back_failed": False,
        "article_back_chain": [],
        "article_back_steps_required": 0,
        "article_page_classification": "UNTESTABLE",
        "article_classification_reasons": [reason] if reason else [],
        "back_steps_debug_article": [],
        "snapshots_article": snapshots,
        "article_back_ad_analysis": {},
        **empty_signals("article"),
    }

# ---------------------------------------------------------------------------
# Full domain test
# ---------------------------------------------------------------------------

def detect_back_button_hijacking_combined(
    target_url, controlled_referrer_url, max_back_steps,
    headless, page_load_timeout, allow_insecure_proceed,
    screenshot_dir="screenshots",
):
    logger     = InteractionLogger()
    driver     = create_driver(headless=headless, page_load_timeout=page_load_timeout)
    started_at = utc_now_iso()
    domain     = get_domain(target_url)

    try:
        logger.add("domain_test_start", driver, target_url=target_url)

        main_result = test_main_page(
            driver, target_url, controlled_referrer_url,
            max_back_steps, logger, allow_insecure_proceed,
            screenshot_dir=screenshot_dir,
        )

        article_result = test_article_page(
            driver, target_url, controlled_referrer_url,
            max_back_steps, logger, allow_insecure_proceed,
            screenshot_dir=screenshot_dir,
        )

        domain_cls = classify_domain(
            main_result["main_page_classification"],
            article_result["article_page_classification"],
        )

        finalise_screenshots(
            domain=domain,
            classification=domain_cls,
            screenshot_dir=screenshot_dir,
        )

        evidence_log = {
            "domain": domain,
            "started_at": started_at,
            "finished_at": utc_now_iso(),
            "controlled_referrer_url": controlled_referrer_url,
            **main_result,
            **article_result,
            "domain_classification": domain_cls,
        }

        # Build and attach top-level ad summary
        evidence_log["ad_summary"] = build_domain_ad_summary(evidence_log)

        logger.add("domain_test_end", driver, domain_classification=domain_cls)
        evidence_log["interaction_log"] = logger.get_events()

        return {
            "domain_classification": domain_cls,
            "main_page_classification": main_result["main_page_classification"],
            "article_page_classification": article_result["article_page_classification"],
            "evidence_log": evidence_log,
        }

    finally:
        try:
            driver.quit()
        except Exception:
            pass

# ---------------------------------------------------------------------------
# Worker
# ---------------------------------------------------------------------------

def run_single_item_worker(worker_args):
    item, args_dict = worker_args
    url    = item["target_url"]
    domain = item["domain"]
    try:
        result = detect_back_button_hijacking_combined(
            target_url=url,
            controlled_referrer_url=args_dict["referrer"],
            max_back_steps=args_dict["max_back_steps"],
            headless=args_dict["headless"],
            page_load_timeout=args_dict["page_load_timeout"],
            allow_insecure_proceed=args_dict["allow_insecure_proceed"],
            screenshot_dir=args_dict["screenshot_dir"],
        )
        result["csv_metadata"] = item["csv_metadata"]
        result["row_index"]    = item["row_index"]
        result["domain"]       = domain
        result["target_url"]   = url
        result["finished_at"]  = utc_now_iso()
        return result
    except Exception as e:
        return build_error_result(item, e)

def build_error_result(item, error):
    return {
        "csv_metadata": item.get("csv_metadata", {}),
        "row_index": item.get("row_index"),
        "domain": item.get("domain"),
        "target_url": item.get("target_url"),
        "domain_classification": "ERROR",
        "main_page_classification": "ERROR",
        "article_page_classification": "ERROR",
        "error": str(error),
        "finished_at": utc_now_iso(),
    }

# ---------------------------------------------------------------------------
# Output helpers
# ---------------------------------------------------------------------------

def ensure_parent_dir(path):
    d = os.path.dirname(os.path.abspath(path))
    if d:
        os.makedirs(d, exist_ok=True)

def append_jsonl(output_jsonl, result):
    ensure_parent_dir(output_jsonl)
    with open(output_jsonl, "a", encoding="utf-8") as f:
        f.write(json.dumps(result, ensure_ascii=False) + "\n")

def write_json(output_json, results):
    ensure_parent_dir(output_json)
    temp = output_json + ".tmp"
    with open(temp, "w", encoding="utf-8") as f:
        json.dump(results, f, indent=2, ensure_ascii=False)
    os.replace(temp, output_json)

# ---------------------------------------------------------------------------
# Runner
# ---------------------------------------------------------------------------

def run_crawler(args):
    if args.seed is not None:
        random.seed(args.seed)

    test_items = load_urls_from_csv(args.input, split_filter=args.split_filter)

    if args.start_index > 0:
        test_items = test_items[args.start_index:]
    if args.limit is not None:
        test_items = test_items[:args.limit]

    print(f"Loaded {len(test_items)} rows")
    print(f"Input:          {args.input}")
    print(f"Output JSON:    {args.output}")
    print(f"Output JSONL:   {args.output_jsonl}")
    print(f"Workers:        {args.workers}")
    print(f"Headless:       {args.headless}")
    print(f"Screenshot dir: {args.screenshot_dir}")

    args_dict = {
        "referrer":               args.referrer,
        "max_back_steps":         args.max_back_steps,
        "headless":               args.headless,
        "page_load_timeout":      args.page_load_timeout,
        "allow_insecure_proceed": not args.no_insecure_proceed,
        "screenshot_dir":         args.screenshot_dir,
    }

    all_results = []

    if args.workers <= 1:
        for i, item in enumerate(test_items, 1):
            print(f"\n[{i}/{len(test_items)}] {item['domain']}")
            result = run_single_item_worker((item, args_dict))
            all_results.append(result)
            append_jsonl(args.output_jsonl, result)
            print(json.dumps({
                "domain":                      result.get("domain"),
                "domain_classification":       result.get("domain_classification"),
                "main_page_classification":    result.get("main_page_classification"),
                "article_page_classification": result.get("article_page_classification"),
            }, indent=2))
            if args.write_full_json_each_domain:
                write_json(args.output, all_results)
    else:
        work = [(item, args_dict) for item in test_items]
        with ProcessPoolExecutor(max_workers=args.workers) as executor:
            futures   = {
                executor.submit(run_single_item_worker, task): task[0]
                for task in work
            }
            completed = 0
            for future in as_completed(futures):
                item = futures[future]
                completed += 1
                try:
                    result = future.result()
                except Exception as e:
                    result = build_error_result(item, e)
                all_results.append(result)
                append_jsonl(args.output_jsonl, result)
                print(json.dumps({
                    "completed":             f"{completed}/{len(test_items)}",
                    "domain":                result.get("domain"),
                    "domain_classification": result.get("domain_classification"),
                    "main":                  result.get("main_page_classification"),
                    "article":               result.get("article_page_classification"),
                }, indent=2))
                if args.write_full_json_each_domain:
                    write_json(args.output, all_results)

    all_results.sort(key=lambda r: r.get("row_index") or 0)
    write_json(args.output, all_results)
    print(f"\nDone.")
    print(f"Saved JSON:  {args.output}")
    print(f"Saved JSONL: {args.output_jsonl}")
    print(f"Screenshots: {args.screenshot_dir}/")

# ---------------------------------------------------------------------------
# Argument parsing
# ---------------------------------------------------------------------------

def parse_args():
    parser = argparse.ArgumentParser(
        description="Back-button hijacking crawler with ad evidence extraction."
    )
    parser.add_argument("--input",       required=True,
                        help="Input CSV file (sampled_domains.csv).")
    parser.add_argument("--output",
                        default=f"bbh_results_{int(time.time())}.json")
    parser.add_argument("--output-jsonl",
                        default=f"bbh_results_{int(time.time())}.jsonl")
    parser.add_argument("--workers",     type=int, default=1,
                        help="Number of parallel Chrome processes.")
    parser.add_argument("--referrer",    default=DEFAULT_CONTROLLED_REFERRER_URL)
    parser.add_argument("--max-back-steps", type=int, default=DEFAULT_MAX_BACK_STEPS)
    parser.add_argument("--page-load-timeout", type=int, default=DEFAULT_PAGE_LOAD_TIMEOUT)
    parser.add_argument("--headless",    action="store_true",
                        help="Run Chrome in headless mode.")
    parser.add_argument("--limit",       type=int, default=None,
                        help="Only test the first N domains (for testing).")
    parser.add_argument("--start-index", type=int, default=0,
                        help="Skip the first N rows (for resuming a crashed run).")
    parser.add_argument("--seed",        type=int, default=None)
    parser.add_argument("--split-filter",
                        choices=["manual", "evaluation"], default=None,
                        help="Only run domains from this split group.")
    parser.add_argument("--write-full-json-each-domain", action="store_true",
                        help="Re-write the full JSON after every domain completes.")
    parser.add_argument("--no-insecure-proceed", action="store_true",
                        help="Do not click through Chrome SSL interstitials.")
    parser.add_argument("--screenshot-dir", default="screenshots",
                        help="Root folder for screenshots (default: screenshots/).")
    return parser.parse_args()


# ---------------------------------------------------------------------------
# URL classification validation
# ---------------------------------------------------------------------------
# Quick self-test — run: python SeleniumCrawler.py --validate-ad-urls
#
# Expected classifications:
#   https://example.com/#google_vignette
#     => google_vignette
#   https://nachrichtenpost.click/c/de/?method=pop&cmp=abc&traff=xyz
#     => popup_redirect  (method=pop takes priority over tracker_redirect)
#   https://sweepquest.info/bonus/all-cc/lp1/?t3=usei&t14=pop&et2=160x600
#     => reward_sweepstakes
#   https://adblocker-ultraplus.app/r.php?ea=123&dv=tr
#     => browser_extension_install
#   https://funkykeel.com/api/users?token=abc&psid=200
#     => tracker_redirect

def _validate_ad_url_classification():
    test_cases = [
        ("https://example.com/#google_vignette",                                          "google_vignette"),
        ("https://nachrichtenpost.click/c/de/52/?method=pop&cmp=abc&traff=xyz",           "popup_redirect"),
        ("https://sweepquest.info/bonus/all-cc/lp1/?t3=usei-hueads&t14=pop&et2=160x600", "reward_sweepstakes"),
        ("https://adblocker-ultraplus.app/r.php?ea=123&dv=tr",                            "browser_extension_install"),
        ("https://funkykeel.com/api/users?token=abc&psid=200",                            "tracker_redirect"),
    ]
    print("\nURL classification validation:")
    all_pass = True
    for url, expected in test_cases:
        got = classify_ad_url(url, original_domain="example.com",
                              expected_referrer_domain="google.com")
        ok  = (got == expected)
        if not ok:
            all_pass = False
        status = "PASS" if ok else "FAIL"
        print(f"  [{status}] {url[:70]}")
        print(f"         expected={expected}  got={got}")
    print(f"\nResult: {'ALL PASS' if all_pass else 'SOME FAILED'}")
    return all_pass


def main():
    import sys
    if "--validate-ad-urls" in sys.argv:
        _validate_ad_url_classification()
        return
    args = parse_args()
    run_crawler(args)


if __name__ == "__main__":
    main()
