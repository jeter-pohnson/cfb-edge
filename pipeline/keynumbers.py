"""Measured key numbers.

The hardcoded list this replaces was NFL thinking. In college football 3 is
still the most common margin but carries roughly two thirds the weight it does
in the NFL, the top five numbers account for barely more than a quarter of all
margins against about 42 percent in the pros, and margins scatter with a
standard deviation around 16 points rather than 13.5. Large numbers matter far
more, because a board with 30-point spreads on it produces 24 and 28-point
margins regularly.

So nothing here is assumed. Every margin from several completed seasons is
counted, bucketed by how big the spread was, and the result is used directly:
moving a line from the posted number to the fair number is worth exactly the
probability mass sitting between them. That is the real quantity, and it makes
"crosses a key number" a measurement rather than a slogan.
"""

import json
import os
import time

from . import cfbd_client

CACHE_DIR = os.path.join(os.path.dirname(os.path.dirname(__file__)), "cache")
PATH = os.path.join(CACHE_DIR, "key_numbers.json")
MAX_AGE_DAYS = 60

SEASONS_BACK = 3
METHOD_VERSION = 2

# Margin behaviour differs enormously between a one-score conference game and a
# 35-point non-conference mismatch, so the distribution is conditioned on the
# size of the spread rather than pooled.
BANDS = [(0, 7), (7, 14), (14, 21), (21, 35), (35, 999)]

MIN_BAND_SAMPLE = 150


def band_for(spread):
    size = abs(float(spread))
    for low, high in BANDS:
        if low <= size < high:
            return "%d-%d" % (low, high) if high < 999 else "%d+" % low
    return "0-7"


def load_cached():
    try:
        if time.time() - os.path.getmtime(PATH) > MAX_AGE_DAYS * 86400:
            return None
        with open(PATH) as fh:
            return json.load(fh)
    except (OSError, ValueError):
        return None


def _save(payload):
    os.makedirs(CACHE_DIR, exist_ok=True)
    with open(PATH, "w") as fh:
        json.dump(payload, fh, separators=(",", ":"))


def build(season, force=False):
    """Count margins and totals by spread band across recent seasons."""
    if not force:
        cached = load_cached()
        if cached and cached.get("through_season") == season - 1 \
                and cached.get("method_version") == METHOD_VERSION:
            return cached

    margins = {}
    totals = {}
    games_used = 0
    seasons_used = []

    for year in range(season - SEASONS_BACK, season):
        try:
            games = cfbd_client.calibration_games(year)
            lines = _lines_by_matchup(year)
        except Exception:  # noqa: BLE001
            continue
        if not games:
            continue
        seasons_used.append(year)

        for game in games:
            home_points = game.get("homePoints", game.get("home_points"))
            away_points = game.get("awayPoints", game.get("away_points"))
            home = game.get("homeTeam") or game.get("home_team")
            away = game.get("awayTeam") or game.get("away_team")
            if None in (home_points, away_points, home, away):
                continue

            market = lines.get((home, away))
            if not market or market.get("spread") is None:
                continue

            band = band_for(market["spread"])
            margin = int(round(abs(float(home_points) - float(away_points))))
            margins.setdefault(band, {})
            margins[band][str(margin)] = margins[band].get(str(margin), 0) + 1

            total = int(round(float(home_points) + float(away_points)))
            totals.setdefault(band, {})
            totals[band][str(total)] = totals[band].get(str(total), 0) + 1
            games_used += 1

    if games_used < 400:
        return {"available": False,
                "note": "Only %d graded games found, not enough to measure "
                        "key numbers." % games_used}

    payload = {
        "available": True,
        "method_version": METHOD_VERSION,
        "through_season": season - 1,
        "seasons": seasons_used,
        "games": games_used,
        "margins": margins,
        "totals": totals,
        "band_counts": {band: sum(counts.values())
                        for band, counts in margins.items()},
        "top_margins": _top(margins),
        "note": "Measured from %d games across %s."
                % (games_used, ", ".join(str(y) for y in seasons_used)),
    }
    _save(payload)
    return payload


def _lines_by_matchup(year):
    rows = cfbd_client.historical_lines(year)
    out = {}
    preferred = ["DraftKings", "consensus", "ESPN Bet", "Bovada"]
    for row in rows or []:
        home = row.get("homeTeam") or row.get("home_team")
        away = row.get("awayTeam") or row.get("away_team")
        lines = row.get("lines") or []
        if not home or not away or not lines:
            continue
        chosen = None
        for provider in preferred:
            for line in lines:
                if (line.get("provider") or "").lower() == provider.lower():
                    chosen = line
                    break
            if chosen:
                break
        chosen = chosen or lines[0]
        try:
            spread = chosen.get("spread")
            out[(home, away)] = {
                "spread": float(spread) if spread is not None else None,
            }
        except (TypeError, ValueError):
            continue
    return out


def _top(margins, limit=6):
    """Most common margins per band, for the Method tab."""
    out = {}
    for band, counts in margins.items():
        total = sum(counts.values())
        if total < MIN_BAND_SAMPLE:
            continue
        rows = sorted(counts.items(), key=lambda pair: -pair[1])[:limit]
        out[band] = [{"margin": int(k), "share": round(100.0 * v / total, 2)}
                     for k, v in rows]
    return out


def mass_between(report, spread_or_total, fair, posted, market="spread"):
    """Probability mass captured by moving from the posted number to fair.

    This is what "crossing a key number" is actually worth. Moving a spread
    from 3.5 to 2.5 wins every game decided by exactly 3, so the value of that
    move is the measured frequency of 3-point margins in that band, not a flat
    bonus for touching a number someone wrote down.
    """
    if not report or not report.get("available"):
        return None
    if fair is None or posted is None:
        return None

    table = report.get("margins" if market == "spread" else "totals") or {}
    band = band_for(spread_or_total)
    counts = table.get(band)
    if not counts:
        return None
    total = sum(counts.values())
    if total < MIN_BAND_SAMPLE:
        return None

    low, high = sorted([abs(float(fair)), abs(float(posted))]) \
        if market == "spread" else sorted([float(fair), float(posted)])

    captured = 0
    numbers = []
    for key, count in counts.items():
        number = int(key)
        if low < number < high:
            captured += count
            numbers.append((number, count))

    numbers.sort(key=lambda pair: -pair[1])
    return {
        "band": band,
        "sample": total,
        "mass": round(captured / total, 4),
        "numbers": [{"number": n, "share": round(100.0 * c / total, 2)}
                    for n, c in numbers[:4]],
    }
