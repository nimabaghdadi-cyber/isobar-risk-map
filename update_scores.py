"""
ISOBAR live update script.

What this does, in plain terms:
  1. For each country on the watchlist below, it asks GDELT (a free,
     public database of global news coverage) how the news has been
     talking about that country over the last few days — how negative
     the tone is, and how much coverage there's been.
  2. It turns that into a "political" and "conflict" signal.
  3. It blends that fresh signal with the existing baseline score
     (50/50) rather than replacing it outright — this avoids wild
     swings from a single noisy news day, per the earlier discussion
     about not treating raw news tone as gospel.
  4. It saves the result to data/scores.json, which the website reads.

This only updates political + conflict. Economic and regulatory scores
stay as the manually-set baseline, because GDELT is a news feed, not an
economic database (see: connect IMF/World Bank data for that later).

The 64-country watchlist below is a deliberate strategic selection, not
an arbitrary sample: major economies, active conflict/crisis zones,
supply chain & manufacturing hubs, and a regional strategic watch list.
Add or remove countries here any time — it's just a dictionary below.

Run automatically every 6 hours by the GitHub Actions workflow next to
this file. Can also be run by hand: `python update_scores.py`
"""

import json
import re
import urllib.request
import urllib.parse
from pathlib import Path
from datetime import datetime, timezone
from concurrent.futures import ThreadPoolExecutor, as_completed

GDELT_DOC_API = "https://api.gdeltproject.org/api/v2/doc/doc"
DATA_FILE = Path(__file__).parent / "data" / "scores.json"
HISTORY_FILE = Path(__file__).parent / "data" / "history.json"
HEADLINES_DIR = Path(__file__).parent / "data" / "headlines"
MAX_HISTORY_POINTS = 120  # ~30 days at 4 runs/day
MAX_ALERTS = 30
MAX_WORKERS = 8  # concurrent countries in flight — cuts a ~25min serial run to a few minutes
MAX_ARCHIVE_ARTICLES = 100


def slugify(name: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")

# Baseline scores — mirrors the original 48 hand-considered countries in
# the app. (The other ~130 auto-generated countries are left static for
# now; add them here later if you want them live too.)
BASELINE = {
    "United States": {"region": "North America", "political": 32, "conflict": 12, "economic": 24, "regulatory": 28},
    "Canada": {"region": "North America", "political": 14, "conflict": 5, "economic": 18, "regulatory": 20},
    "Mexico": {"region": "North America", "political": 44, "conflict": 58, "economic": 40, "regulatory": 46},
    "Brazil": {"region": "Latin America", "political": 40, "conflict": 30, "economic": 42, "regulatory": 38},
    "Argentina": {"region": "Latin America", "political": 46, "conflict": 20, "economic": 68, "regulatory": 44},
    "United Kingdom": {"region": "Europe", "political": 26, "conflict": 10, "economic": 24, "regulatory": 22},
    "France": {"region": "Europe", "political": 34, "conflict": 16, "economic": 28, "regulatory": 24},
    "Germany": {"region": "Europe", "political": 18, "conflict": 8, "economic": 26, "regulatory": 16},
    "Italy": {"region": "Europe", "political": 18, "conflict": 11, "economic": 44, "regulatory": 17},
    "Russia": {"region": "Europe", "political": 58, "conflict": 74, "economic": 64, "regulatory": 76},
    "Turkey": {"region": "Europe", "political": 52, "conflict": 38, "economic": 60, "regulatory": 48},
    "Saudi Arabia": {"region": "MENA", "political": 34, "conflict": 26, "economic": 30, "regulatory": 36},
    "South Africa": {"region": "Sub-Saharan Africa", "political": 38, "conflict": 30, "economic": 46, "regulatory": 40},
    "India": {"region": "South Asia", "political": 30, "conflict": 26, "economic": 24, "regulatory": 32},
    "China": {"region": "East Asia", "political": 38, "conflict": 22, "economic": 36, "regulatory": 54},
    "Japan": {"region": "East Asia", "political": 16, "conflict": 10, "economic": 22, "regulatory": 14},
    "South Korea": {"region": "East Asia", "political": 30, "conflict": 24, "economic": 20, "regulatory": 18},
    "Indonesia": {"region": "Southeast Asia", "political": 28, "conflict": 20, "economic": 30, "regulatory": 32},
    "Australia": {"region": "Oceania", "political": 17, "conflict": 8, "economic": 13, "regulatory": 10},
    "Ukraine": {"region": "Europe", "political": 62, "conflict": 92, "economic": 70, "regulatory": 58},
    "Israel": {"region": "MENA", "political": 58, "conflict": 66, "economic": 34, "regulatory": 30},
    "Iran": {"region": "MENA", "political": 70, "conflict": 56, "economic": 74, "regulatory": 78},
    "Yemen": {"region": "MENA", "political": 84, "conflict": 88, "economic": 82, "regulatory": 74},
    "Syria": {"region": "MENA", "political": 65, "conflict": 86, "economic": 69, "regulatory": 67},
    "Sudan": {"region": "Sub-Saharan Africa", "political": 90, "conflict": 94, "economic": 86, "regulatory": 80},
    "Myanmar": {"region": "Southeast Asia", "political": 82, "conflict": 78, "economic": 72, "regulatory": 70},
    "Haiti": {"region": "Latin America", "political": 88, "conflict": 84, "economic": 78, "regulatory": 82},
    "Venezuela": {"region": "Latin America", "political": 82, "conflict": 48, "economic": 88, "regulatory": 80},
    "North Korea": {"region": "East Asia", "political": 86, "conflict": 60, "economic": 84, "regulatory": 92},
    "Taiwan": {"region": "East Asia", "political": 44, "conflict": 48, "economic": 26, "regulatory": 20},
    "Lebanon": {"region": "MENA", "political": 65, "conflict": 34, "economic": 64, "regulatory": 20},
    "Libya": {"region": "MENA", "political": 62, "conflict": 55, "economic": 71, "regulatory": 42},
    "Ethiopia": {"region": "Sub-Saharan Africa", "political": 60, "conflict": 62, "economic": 58, "regulatory": 52},
    "DR Congo": {"region": "Sub-Saharan Africa", "political": 72, "conflict": 80, "economic": 68, "regulatory": 64},
    "Mali": {"region": "Sub-Saharan Africa", "political": 60, "conflict": 85, "economic": 72, "regulatory": 45},
    "Somalia": {"region": "Sub-Saharan Africa", "political": 55, "conflict": 90, "economic": 67, "regulatory": 42},
    "Pakistan": {"region": "South Asia", "political": 58, "conflict": 54, "economic": 62, "regulatory": 50},
    "Afghanistan": {"region": "South Asia", "political": 78, "conflict": 70, "economic": 80, "regulatory": 84},
    "Vietnam": {"region": "Southeast Asia", "political": 22, "conflict": 12, "economic": 24, "regulatory": 30},
    "Thailand": {"region": "Southeast Asia", "political": 40, "conflict": 26, "economic": 32, "regulatory": 34},
    "Malaysia": {"region": "Southeast Asia", "political": 31, "conflict": 13, "economic": 14, "regulatory": 18},
    "Philippines": {"region": "Southeast Asia", "political": 34, "conflict": 30, "economic": 32, "regulatory": 30},
    "Bangladesh": {"region": "South Asia", "political": 48, "conflict": 34, "economic": 44, "regulatory": 42},
    "Poland": {"region": "Europe", "political": 24, "conflict": 20, "economic": 22, "regulatory": 20},
    "Egypt": {"region": "MENA", "political": 46, "conflict": 34, "economic": 56, "regulatory": 44},
    "UAE": {"region": "MENA", "political": 16, "conflict": 12, "economic": 16, "regulatory": 18},
    "Singapore": {"region": "Southeast Asia", "political": 8, "conflict": 4, "economic": 10, "regulatory": 8},
    "Nigeria": {"region": "Sub-Saharan Africa", "political": 52, "conflict": 60, "economic": 54, "regulatory": 50},
    "Kenya": {"region": "Sub-Saharan Africa", "political": 36, "conflict": 28, "economic": 38, "regulatory": 34},
    "Morocco": {"region": "MENA", "political": 32, "conflict": 20, "economic": 38, "regulatory": 18},
    "Chile": {"region": "Latin America", "political": 24, "conflict": 14, "economic": 26, "regulatory": 22},
    "Peru": {"region": "Latin America", "political": 37, "conflict": 38, "economic": 40, "regulatory": 19},
    "Colombia": {"region": "Latin America", "political": 48, "conflict": 56, "economic": 38, "regulatory": 40},
    "Kazakhstan": {"region": "Eurasia", "political": 34, "conflict": 20, "economic": 34, "regulatory": 36},
    "Georgia": {"region": "Eurasia", "political": 48, "conflict": 30, "economic": 36, "regulatory": 38},
    "Azerbaijan": {"region": "Eurasia", "political": 31, "conflict": 31, "economic": 39, "regulatory": 20},
    "Jordan": {"region": "MENA", "political": 36, "conflict": 11, "economic": 40, "regulatory": 20},
    "Qatar": {"region": "MENA", "political": 34, "conflict": 14, "economic": 22, "regulatory": 16},
    "Ecuador": {"region": "Latin America", "political": 38, "conflict": 57, "economic": 36, "regulatory": 13},
    "Cuba": {"region": "Latin America", "political": 58, "conflict": 14, "economic": 67, "regulatory": 73},
    "Zimbabwe": {"region": "Sub-Saharan Africa", "political": 61, "conflict": 15, "economic": 65, "regulatory": 64},
    "Belarus": {"region": "Europe", "political": 62, "conflict": 36, "economic": 67, "regulatory": 72},
    "Serbia": {"region": "Europe", "political": 39, "conflict": 34, "economic": 43, "regulatory": 40},
    "Sri Lanka": {"region": "South Asia", "political": 40, "conflict": 18, "economic": 52, "regulatory": 38},
}


def fetch_tone(country_name: str):
    """Query GDELT for recent news tone + volume about a country.
    Returns (avg_tone, article_count) or (None, 0) if the request fails
    or there's no coverage — callers should fall back to baseline in
    that case rather than erroring the whole run out.
    """
    params = {
        "query": f'"{country_name}" sourcelang:eng',
        "mode": "tonechart",
        "format": "json",
        "timespan": "3d",
    }
    url = GDELT_DOC_API + "?" + urllib.parse.urlencode(params)
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "isobar-risk-map/1.0"})
        with urllib.request.urlopen(req, timeout=20) as resp:
            payload = json.loads(resp.read().decode("utf-8"))
    except Exception as exc:
        print(f"  [warn] GDELT request failed for {country_name}: {exc}")
        return None, 0

    bins = payload.get("tonechart", [])
    total_count = sum(b.get("count", 0) for b in bins)
    if total_count == 0:
        return None, 0
    weighted = sum(b.get("bin", 0) * b.get("count", 0) for b in bins)
    avg_tone = weighted / total_count
    return avg_tone, total_count


def fetch_articles(country_name: str, max_articles: int = 4):
    """Fetch a handful of real, recent article titles + links about a
    country from GDELT. These are shown verbatim on the site — nothing
    here is AI-written, it's just real headlines with real links, so
    there's no fabrication risk. Returns [] on failure.

    Sorted by relevance (HybridRel), not pure recency (DateDesc) — when
    a country has sparse direct coverage, sorting by "most recent" alone
    causes GDELT to backfill with unrelated recent articles just to hit
    the record count, rather than returning fewer, more relevant ones.
    """
    params = {
        "query": f'"{country_name}" sourcelang:eng',
        "mode": "artlist",
        "format": "json",
        "timespan": "7d",
        "maxrecords": "10",
        "sort": "HybridRel",
    }
    url = GDELT_DOC_API + "?" + urllib.parse.urlencode(params)
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "isobar-risk-map/1.0"})
        with urllib.request.urlopen(req, timeout=20) as resp:
            payload = json.loads(resp.read().decode("utf-8"))
    except Exception as exc:
        print(f"  [warn] GDELT article fetch failed for {country_name}: {exc}")
        return []

    raw_articles = payload.get("articles", [])
    seen_titles = set()
    deduped = []
    for a in raw_articles:
        title = (a.get("title") or "").strip()
        url_ = a.get("url", "")
        if not title or not url_:
            continue
        key = title.lower()
        if key in seen_titles:
            continue  # skip syndicated duplicates of the same wire story
        seen_titles.add(key)
        deduped.append({"title": title, "url": url_, "domain": a.get("domain", "")})
        if len(deduped) >= max_articles:
            break
    return deduped


def fetch_full_archive(country_name: str, max_articles: int = MAX_ARCHIVE_ARTICLES):
    """Fetch up to 100 dated headlines about a country, going back 30 days.
    Also relevance-sorted first (same reasoning as fetch_articles — avoids
    irrelevant backfill for sparsely-covered countries), then re-sorted by
    actual publish date, newest first, for display. A quiet country simply
    gets however many real articles exist — never padded to hit 100.
    """
    params = {
        "query": f'"{country_name}" sourcelang:eng',
        "mode": "artlist",
        "format": "json",
        "timespan": "30d",
        "maxrecords": "100",
        "sort": "HybridRel",
    }
    url = GDELT_DOC_API + "?" + urllib.parse.urlencode(params)
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "isobar-risk-map/1.0"})
        with urllib.request.urlopen(req, timeout=25) as resp:
            payload = json.loads(resp.read().decode("utf-8"))
    except Exception as exc:
        print(f"  [warn] GDELT archive fetch failed for {country_name}: {exc}")
        return []

    raw_articles = payload.get("articles", [])
    seen_titles = set()
    items = []
    for a in raw_articles:
        title = (a.get("title") or "").strip()
        url_ = a.get("url", "")
        if not title or not url_:
            continue
        key = title.lower()
        if key in seen_titles:
            continue
        seen_titles.add(key)

        seendate = a.get("seendate", "")
        try:
            dt = datetime.strptime(seendate, "%Y%m%dT%H%M%SZ")
            date_str = dt.strftime("%b %d, %Y")
        except Exception:
            dt = datetime.min
            date_str = ""

        items.append({"title": title, "url": url_, "domain": a.get("domain", ""), "date": date_str, "_dt": dt})

    items.sort(key=lambda x: x["_dt"], reverse=True)
    for it in items:
        it.pop("_dt", None)
    return items[:max_articles]


def tone_reading(avg_tone):
    """Plain-language description of recent coverage tone. Thresholds are
    rough judgment calls, not a precise scientific scale — GDELT tone
    typically runs roughly -10 (very negative) to +10 (very positive)."""
    if avg_tone is None:
        return "No recent coverage found."
    if avg_tone <= -5:
        return "Coverage over the last 3 days has been notably negative."
    if avg_tone <= -2:
        return "Coverage over the last 3 days has leaned negative."
    if avg_tone < 2:
        return "Coverage over the last 3 days has been mixed or neutral."
    if avg_tone < 5:
        return "Coverage over the last 3 days has leaned positive."
    return "Coverage over the last 3 days has been notably positive."


def volume_reading(article_count):
    """Plain-language description of coverage volume. Thresholds are a
    rough starting point (absolute, not yet compared to each country's
    own historical baseline) — reasonable to refine once there's a
    longer run history to compare against."""
    if article_count == 0:
        return None
    if article_count > 40:
        return "Coverage volume has been unusually high this week."
    if article_count < 8:
        return "Coverage volume has been light this week."
    return None  # typical volume isn't worth calling out


def tone_to_political_signal(avg_tone: float) -> float:
    # GDELT tone typically runs roughly -10 (very negative) to +10 (very positive).
    # Map that onto a 0-100 risk scale, negative tone -> higher score.
    return max(0, min(100, 50 - (avg_tone * 5)))


def volume_to_conflict_bump(article_count: int) -> float:
    # A spike in coverage volume is itself a weak signal of something
    # happening worth flagging — small, capped bump only.
    return min(15, article_count / 40)


def risk_band(score):
    # Mirrors the thresholds used on the website's riskLabel() function —
    # keep these two in sync if you ever change one.
    if score < 30:
        return "LOW"
    if score < 45:
        return "GUARDED"
    if score < 60:
        return "ELEVATED"
    if score < 75:
        return "HIGH"
    return "SEVERE"


def load_previous():
    if DATA_FILE.exists():
        try:
            return json.loads(DATA_FILE.read_text())
        except Exception:
            return None
    return None


def load_history():
    if HISTORY_FILE.exists():
        try:
            return json.loads(HISTORY_FILE.read_text())
        except Exception:
            return {}
    return {}


def process_country(name, base, previous_by_name):
    """Everything needed for one country — this runs concurrently across
    countries via the thread pool below, so it must not depend on shared
    mutable state."""
    avg_tone, count = fetch_tone(name)
    headlines = fetch_articles(name)
    archive = fetch_full_archive(name)

    if avg_tone is None:
        political = base["political"]
        conflict = base["conflict"]
    else:
        political_signal = tone_to_political_signal(avg_tone)
        conflict_bump = volume_to_conflict_bump(count) if avg_tone < -3 else 0
        political = round(base["political"] * 0.5 + political_signal * 0.5)
        conflict = round(min(100, base["conflict"] * 0.5 + (base["conflict"] + conflict_bump) * 0.5))

    prev_entry = previous_by_name.get(name)
    prev_score = None
    if prev_entry:
        prev_score = prev_entry.get("political", 0) * 0.5 + prev_entry.get("conflict", 0) * 0.5
    new_score = political * 0.5 + conflict * 0.5

    if prev_score is None:
        trend = "flat"
    elif new_score > prev_score + 2:
        trend = "up"
    elif new_score < prev_score - 2:
        trend = "down"
    else:
        trend = "flat"

    return {
        "name": name,
        "region": base["region"],
        "political": political,
        "conflict": conflict,
        "economic": base["economic"],
        "regulatory": base["regulatory"],
        "trend": trend,
        "note": "Live-updated from recent news coverage." if avg_tone is not None else "No fresh coverage this cycle; showing baseline.",
        "headlines": headlines,
        "toneReading": tone_reading(avg_tone),
        "volumeReading": volume_reading(count),
        "_trackScore": round(new_score),  # internal, used for history/alerts below
        "_prevScore": prev_score,
        "_archive": archive,
    }


def main():
    previous = load_previous()
    previous_by_name = {c["name"]: c for c in (previous or {}).get("countries", [])} if previous else {}
    history = load_history()
    prev_alerts = (previous or {}).get("alerts", []) if previous else []

    now_iso = datetime.now(timezone.utc).isoformat()
    results = []
    new_alerts = []

    print(f"Checking {len(BASELINE)} countries with up to {MAX_WORKERS} in parallel...")
    with ThreadPoolExecutor(max_workers=MAX_WORKERS) as executor:
        futures = {
            executor.submit(process_country, name, base, previous_by_name): name
            for name, base in BASELINE.items()
        }
        for future in as_completed(futures):
            name = futures[future]
            try:
                result = future.result()
            except Exception as exc:
                print(f"  [warn] {name} failed entirely this run: {exc}")
                continue
            print(f"  done: {name}")
            results.append(result)

            # Threshold-crossing alert: only fires when we actually have a
            # previous score to compare against (not on a country's first run).
            if result["_prevScore"] is not None:
                band_now = risk_band(result["_trackScore"])
                band_prev = risk_band(round(result["_prevScore"]))
                if band_now != band_prev:
                    new_alerts.append({
                        "name": result["name"],
                        "from": band_prev,
                        "to": band_now,
                        "at": now_iso,
                    })

            # History: append this run's point, capped to MAX_HISTORY_POINTS.
            hist = history.get(result["name"], [])
            hist.append({"t": now_iso, "score": result["_trackScore"]})
            history[result["name"]] = hist[-MAX_HISTORY_POINTS:]

    # Write each country's full headline archive to its own file — kept
    # separate from scores.json so every visitor isn't downloading up to
    # 100 headlines × 64 countries just to see the dashboard.
    HEADLINES_DIR.mkdir(parents=True, exist_ok=True)
    for r in results:
        archive_path = HEADLINES_DIR / f"{slugify(r['name'])}.json"
        archive_path.write_text(json.dumps({
            "country": r["name"],
            "generatedAt": now_iso,
            "headlines": r["_archive"],
        }, indent=2))

    # Drop the internal-only fields before writing the public output.
    for r in results:
        r.pop("_trackScore", None)
        r.pop("_prevScore", None)
        r.pop("_archive", None)

    results.sort(key=lambda r: r["name"])
    all_alerts = (new_alerts + prev_alerts)[:MAX_ALERTS]

    output = {
        "generatedAt": now_iso,
        "source": "GDELT (news tone/volume, 3-day window) blended 50/50 with baseline",
        "countries": results,
        "alerts": all_alerts,
    }

    DATA_FILE.parent.mkdir(parents=True, exist_ok=True)
    DATA_FILE.write_text(json.dumps(output, indent=2))
    HISTORY_FILE.write_text(json.dumps(history, indent=2))
    print(f"\nWrote {len(results)} countries to {DATA_FILE}")
    print(f"Wrote history for {len(history)} countries to {HISTORY_FILE}")
    if new_alerts:
        print(f"{len(new_alerts)} threshold-crossing alert(s) this run:")
        for a in new_alerts:
            print(f"  {a['name']}: {a['from']} -> {a['to']}")


if __name__ == "__main__":
    main()
