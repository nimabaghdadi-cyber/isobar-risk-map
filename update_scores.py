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

Run automatically every 6 hours by the GitHub Actions workflow next to
this file. Can also be run by hand: `python update_scores.py`
"""

import json
import time
import urllib.request
import urllib.parse
from pathlib import Path
from datetime import datetime, timezone

GDELT_DOC_API = "https://api.gdeltproject.org/api/v2/doc/doc"
DATA_FILE = Path(__file__).parent / "data" / "scores.json"

# Baseline scores — mirrors the original 48 hand-considered countries in
# the app. (The other ~130 auto-generated countries are left static for
# now; add them here later if you want them live too.)
BASELINE = {
    "United States": {"region": "North America", "political": 32, "conflict": 12, "economic": 24, "regulatory": 28},
    "Canada": {"region": "North America", "political": 14, "conflict": 5, "economic": 18, "regulatory": 20},
    "Mexico": {"region": "North America", "political": 44, "conflict": 58, "economic": 40, "regulatory": 46},
    "Brazil": {"region": "Latin America", "political": 40, "conflict": 30, "economic": 42, "regulatory": 38},
    "Argentina": {"region": "Latin America", "political": 46, "conflict": 20, "economic": 68, "regulatory": 44},
    "Venezuela": {"region": "Latin America", "political": 82, "conflict": 48, "economic": 88, "regulatory": 80},
    "Colombia": {"region": "Latin America", "political": 48, "conflict": 56, "economic": 38, "regulatory": 40},
    "Haiti": {"region": "Latin America", "political": 88, "conflict": 84, "economic": 78, "regulatory": 82},
    "Chile": {"region": "Latin America", "political": 24, "conflict": 14, "economic": 26, "regulatory": 22},
    "Germany": {"region": "Europe", "political": 18, "conflict": 8, "economic": 26, "regulatory": 16},
    "France": {"region": "Europe", "political": 34, "conflict": 16, "economic": 28, "regulatory": 24},
    "United Kingdom": {"region": "Europe", "political": 26, "conflict": 10, "economic": 24, "regulatory": 22},
    "Poland": {"region": "Europe", "political": 24, "conflict": 20, "economic": 22, "regulatory": 20},
    "Ukraine": {"region": "Europe", "political": 62, "conflict": 92, "economic": 70, "regulatory": 58},
    "Russia": {"region": "Europe", "political": 58, "conflict": 74, "economic": 64, "regulatory": 76},
    "Turkey": {"region": "Europe", "political": 52, "conflict": 38, "economic": 60, "regulatory": 48},
    "Switzerland": {"region": "Europe", "political": 8, "conflict": 4, "economic": 12, "regulatory": 10},
    "Iran": {"region": "MENA", "political": 70, "conflict": 56, "economic": 74, "regulatory": 78},
    "Israel": {"region": "MENA", "political": 58, "conflict": 66, "economic": 34, "regulatory": 30},
    "Saudi Arabia": {"region": "MENA", "political": 34, "conflict": 26, "economic": 30, "regulatory": 36},
    "Egypt": {"region": "MENA", "political": 46, "conflict": 34, "economic": 56, "regulatory": 44},
    "Yemen": {"region": "MENA", "political": 84, "conflict": 88, "economic": 82, "regulatory": 74},
    "UAE": {"region": "MENA", "political": 16, "conflict": 12, "economic": 16, "regulatory": 18},
    "Nigeria": {"region": "Sub-Saharan Africa", "political": 52, "conflict": 60, "economic": 54, "regulatory": 50},
    "South Africa": {"region": "Sub-Saharan Africa", "political": 38, "conflict": 30, "economic": 46, "regulatory": 40},
    "Ethiopia": {"region": "Sub-Saharan Africa", "political": 60, "conflict": 62, "economic": 58, "regulatory": 52},
    "Sudan": {"region": "Sub-Saharan Africa", "political": 90, "conflict": 94, "economic": 86, "regulatory": 80},
    "Kenya": {"region": "Sub-Saharan Africa", "political": 36, "conflict": 28, "economic": 38, "regulatory": 34},
    "DR Congo": {"region": "Sub-Saharan Africa", "political": 72, "conflict": 80, "economic": 68, "regulatory": 64},
    "India": {"region": "South Asia", "political": 30, "conflict": 26, "economic": 24, "regulatory": 32},
    "Pakistan": {"region": "South Asia", "political": 58, "conflict": 54, "economic": 62, "regulatory": 50},
    "Bangladesh": {"region": "South Asia", "political": 48, "conflict": 34, "economic": 44, "regulatory": 42},
    "Afghanistan": {"region": "South Asia", "political": 78, "conflict": 70, "economic": 80, "regulatory": 84},
    "Sri Lanka": {"region": "South Asia", "political": 40, "conflict": 18, "economic": 52, "regulatory": 38},
    "China": {"region": "East Asia", "political": 38, "conflict": 22, "economic": 36, "regulatory": 54},
    "Japan": {"region": "East Asia", "political": 16, "conflict": 10, "economic": 22, "regulatory": 14},
    "South Korea": {"region": "East Asia", "political": 30, "conflict": 24, "economic": 20, "regulatory": 18},
    "North Korea": {"region": "East Asia", "political": 86, "conflict": 60, "economic": 84, "regulatory": 92},
    "Taiwan": {"region": "East Asia", "political": 44, "conflict": 48, "economic": 26, "regulatory": 20},
    "Indonesia": {"region": "Southeast Asia", "political": 28, "conflict": 20, "economic": 30, "regulatory": 32},
    "Vietnam": {"region": "Southeast Asia", "political": 22, "conflict": 12, "economic": 24, "regulatory": 30},
    "Thailand": {"region": "Southeast Asia", "political": 40, "conflict": 26, "economic": 32, "regulatory": 34},
    "Myanmar": {"region": "Southeast Asia", "political": 82, "conflict": 78, "economic": 72, "regulatory": 70},
    "Philippines": {"region": "Southeast Asia", "political": 34, "conflict": 30, "economic": 32, "regulatory": 30},
    "Singapore": {"region": "Southeast Asia", "political": 8, "conflict": 4, "economic": 10, "regulatory": 8},
    "Kazakhstan": {"region": "Eurasia", "political": 34, "conflict": 20, "economic": 34, "regulatory": 36},
    "Georgia": {"region": "Eurasia", "political": 48, "conflict": 30, "economic": 36, "regulatory": 38},
    "Armenia": {"region": "Eurasia", "political": 50, "conflict": 42, "economic": 40, "regulatory": 40},
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


def tone_to_political_signal(avg_tone: float) -> float:
    # GDELT tone typically runs roughly -10 (very negative) to +10 (very positive).
    # Map that onto a 0-100 risk scale, negative tone -> higher score.
    return max(0, min(100, 50 - (avg_tone * 5)))


def volume_to_conflict_bump(article_count: int) -> float:
    # A spike in coverage volume is itself a weak signal of something
    # happening worth flagging — small, capped bump only.
    return min(15, article_count / 40)


def load_previous():
    if DATA_FILE.exists():
        try:
            return json.loads(DATA_FILE.read_text())
        except Exception:
            return None
    return None


def main():
    previous = load_previous()
    previous_by_name = {c["name"]: c for c in (previous or {}).get("countries", [])} if previous else {}

    results = []
    for name, base in BASELINE.items():
        print(f"Checking {name}...")
        avg_tone, count = fetch_tone(name)

        if avg_tone is None:
            # No usable data this run — keep the baseline untouched.
            political = base["political"]
            conflict = base["conflict"]
        else:
            political_signal = tone_to_political_signal(avg_tone)
            conflict_bump = volume_to_conflict_bump(count) if avg_tone < -3 else 0
            # Blend 50/50 with baseline so one noisy day can't swing scores wildly.
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

        results.append({
            "name": name,
            "region": base["region"],
            "political": political,
            "conflict": conflict,
            "economic": base["economic"],
            "regulatory": base["regulatory"],
            "trend": trend,
            "note": "Live-updated from recent news coverage." if avg_tone is not None else "No fresh coverage this cycle; showing baseline.",
        })

        time.sleep(1)  # be polite to the free API

    output = {
        "generatedAt": datetime.now(timezone.utc).isoformat(),
        "source": "GDELT (news tone/volume, 3-day window) blended 50/50 with baseline",
        "countries": results,
    }

    DATA_FILE.parent.mkdir(parents=True, exist_ok=True)
    DATA_FILE.write_text(json.dumps(output, indent=2))
    print(f"\nWrote {len(results)} countries to {DATA_FILE}")


if __name__ == "__main__":
    main()
